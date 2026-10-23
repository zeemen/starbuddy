"""FastAPI 应用：内部接口，只供平台内部服务调用（spec 范围第 2 条；1.1.0 的掌握读取与验证任务见 mastery_routes.py）。

接口定义：packages/contracts/openapi/learning-internal.v0.yaml（插件契约 0.9.0）与 learning-internal.v1.yaml
（插件契约 1.0.0，2026-10-09 签字；另有只查询的重放接口；specs/plugin-api-v1.0-step2/spec.md）。
v0 写入（/internal/v0/grade、/internal/v0/diagnose）已下线，一律 410（LT-04 决定记录（再续）第 1 条）；v0 写下的结果仍可经
v1 重放接口与同键 v1 重试读回（只读保留）。
learning-internal v1 于 2026-10-20 签字为 1.1.0（specs/launch-lt04-learning-internal-v1）：开关 LEARNING_INTERNAL_V1 已删除；
/internal/v1 的 grade、diagnose 写入只认 BFF 服务令牌，写入前按计量账本核对 usage_id + 幂等键（迁移 0058，learning/meter.py），
用途与证据等级由学习服务推导（直接写入只能 practice、键盘作答）。
日志只记方法、状态码、错误码、request_id（必须是 UUID 形状）、耗时、是否重放；不记请求体与异常消息。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import health, mastery_routes, metrics, obs, store
from .adapter_client import strict_json
from .config import Settings
from .contract import UnsupportedContractVersion, internal_contract, plugin_contract
from .diagnosis import api as diagnosis_api
from .diagnosis.engine import Params as DiagnosisParams
from .errors import IngestError
from .mastery import load_config
from .mastery.profile import ProfilePages, load_profile_config
from .mastery.recompute import MasteryEngine
from .netpolicy import check_db_tls
from .model_router import ModelRouter
from .model_router.config import load_for_deployment
from .plan import plan_today
from .practice import api as practice_api
from .practice import config as practice_config
from .retest import api as retest_api
from .retest import config as retest_config
from .route_api import MAX_ROUTE_BODY, RouteApiError, parse_request
from .route_api import route as route_call
from .service import IngestService
from .today import api as today_api
from .today import config as today_config

log = logging.getLogger("starbuddy.learning")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
# 就绪探针（LT-03，契约审查建议 4）：0058 的计量核对函数仍在、本角色仍有执行权（传 oid：函数不存在时为假而不是报错）
READY_METER_SQL = ("SELECT CASE WHEN to_regprocedure($1) IS NULL THEN false"
                   " ELSE has_function_privilege(current_user, to_regprocedure($1), 'EXECUTE') END")
# 契约允许 diagnose 带 200 道作答、每道 20000 字（UTF-8 最多约 12 MB），再加 500 条既有证据
MAX_BODY_BYTES = 16 * 1024 * 1024
# v0 写入下线（LT-04 决定记录（再续）第 1 条）：410 的标题，不含任何请求内容
V0_RETIRED = "v0 write endpoints are retired; use /internal/v1"


async def _read_limited(request: Request) -> bytes:
    """边读边累计，超过上限立即中断（不信任 Content-Length，分块传输也一样）。"""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise IngestError(413, "INVALID_REQUEST", "request body too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise IngestError(413, "INVALID_REQUEST", "request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _bearer_ok(header: str | None, expected: str) -> bool:
    m = re.fullmatch(r"Bearer (.+)", header or "")
    if not m:
        return False
    a = hashlib.sha256(m.group(1).encode()).digest()
    b = hashlib.sha256(expected.encode()).digest()
    return hmac.compare_digest(a, b)


def _problem(e: IngestError, request_id: str | None) -> JSONResponse:
    headers = {"retry-after": "2"} if e.code == "BUSY" else None
    return JSONResponse(e.problem(request_id), status_code=e.status, media_type="application/problem+json", headers=headers)


def _diagnosis(settings: Settings, pool, client: httpx.AsyncClient, ingest: IngestService, params: DiagnosisParams | None,
               clock=None):
    """自适应诊断（SIE-02a）：合成题面只在 test 环境（配置层与构造函数各拦一道）；选题信息来自合成夹具或知识服务。"""
    from .diagnosis.presenter import SyntheticPresenter
    from .diagnosis.service import CONTENT_VERSION, DiagnosisService
    from .diagnosis.source import FixtureSource, KnowledgeServiceSource

    presenter = SyntheticPresenter(settings.env)
    if settings.present_item_source == "adapter":
        # PLT-12：题面改经适配器 present_item（插件契约 1.1.0 候选版）。内容版本与诊断判分请求用的同一组六段（CONTENT_VERSION）
        from .present_item import AdapterItemSource

        async def diagnosis_content_version(subject: str, item_id: str) -> dict | None:
            return dict(CONTENT_VERSION) if subject == CONTENT_VERSION["subject"] else None

        presenter = AdapterItemSource(client, settings.adapters, diagnosis_content_version)
    if settings.diagnosis_source == "knowledge":
        assert settings.knowledge is not None and settings.diagnosis_map_version is not None
        ks = KnowledgeServiceSource(client, settings.knowledge.base_url, settings.knowledge.credential, "aya",
                                    settings.diagnosis_targets, settings.diagnosis_map_version)

        async def factory(grade):
            await ks.load(grade)
            return ks
    else:
        fixture = FixtureSource(settings.env)

        async def factory(grade):
            return fixture
    svc = DiagnosisService(pool, ingest, factory, presenter, params or DiagnosisParams())
    if clock is not None:
        svc.clock = clock  # 测试注入：开始诊断时推算年级的时刻
    return svc


SYNTHETIC_DB = re.compile(r"^starbuddy_(dev|test)(_[a-z0-9_]+)?$")  # 与合成种子（db/seeds/dev/*.sql）的库名核对一致


async def _check_diagnosis_env(pool) -> None:
    """合成题面除 LEARNING_ENV=test 外再核对库名（安全审查 S6）：只能连一次性开发 / 测试库；
    诊断作答走 v1 写入路径（插件契约 1.0.0 与 learning-internal v1 都已签字）。"""
    db = await pool.fetchval("SELECT current_database()")
    if not SYNTHETIC_DB.fullmatch(db or ""):
        raise RuntimeError("合成诊断题面只能连开发库或测试库（库名 starbuddy_dev* 或 starbuddy_test*）")
    try:
        plugin_contract("1.0.0")
    except UnsupportedContractVersion:
        raise RuntimeError("诊断作答走 v1 写入路径：插件契约 1.0.0 在本服务不可用") from None


async def _check_practice_env(pool, synthetic_presenter: bool) -> None:
    """再练出题与作答走 generate_variation 与 v1 写入路径（插件契约 1.0.0 与 learning-internal v1 都已签字）；
    复测模块依赖再练，同样受这一检查约束；合成题面另核对库名（与诊断合成题面同一做法，安全审查 S6）。"""
    if synthetic_presenter:
        db = await pool.fetchval("SELECT current_database()")
        if not SYNTHETIC_DB.fullmatch(db or ""):
            raise RuntimeError("再练合成题面只能连开发库或测试库（库名 starbuddy_dev* 或 starbuddy_test*）")
    try:
        plugin_contract("1.0.0")
    except UnsupportedContractVersion:
        raise RuntimeError("错题再练走 generate_variation 与 v1 写入路径：插件契约 1.0.0 在本服务不可用") from None


PRACTICE_LOCK_POOL_SIZE = 8   # 再练作答串行锁的连接数上限：同时在判分的再练作答最多这么多，再多的取锁连接超时回 503 BUSY


def _practice(settings: Settings, cfg, pool, client: httpx.AsyncClient, ingest: IngestService, clock, lock_pool):
    from .practice import store as practice_store
    from .practice.presenter import AdapterPracticePresenter, SyntheticPracticePresenter
    from .practice.service import PracticeService, utcnow
    from .practice.sources import KnowledgeCandidates

    assert settings.practice_ticket_secret is not None
    presenter = SyntheticPracticePresenter(settings.env) if settings.practice_presenter == "synthetic" else None
    if presenter is not None and settings.present_item_source == "adapter":
        # PLT-12：题面改经适配器 present_item（插件契约 1.1.0 候选版）；内容版本取题目登记的六段（与再练判分请求同一来源）
        from .present_item import AdapterItemSource

        async def practice_content_version(subject: str, item_id: str) -> dict | None:
            async with practice_store.tx(pool) as conn:
                return await practice_store.content_version(conn, subject, item_id)

        presenter = AdapterPracticePresenter(AdapterItemSource(client, settings.adapters, practice_content_version), presenter)
    knowledge = (KnowledgeCandidates(client, settings.knowledge.base_url, settings.knowledge.credential)
                 if settings.knowledge is not None else None)
    return PracticeService(pool=pool, ingest=ingest, client=client, cfg=cfg, adapters=dict(settings.adapters),
                           ticket_secret=settings.practice_ticket_secret, presenter=presenter, knowledge=knowledge,
                           clock=clock or utcnow, lock_pool=lock_pool)


def create_app(
    settings: Settings,
    *,
    pool: asyncpg.Pool | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    model_router: ModelRouter | None = None,
    diagnosis_params: DiagnosisParams | None = None,
    mastery: MasteryEngine | None = None,
    profile: ProfilePages | None = None,
    practice_clock=None,
    practice_lock_pool: asyncpg.Pool | None = None,
    diagnosis_clock=None,
) -> FastAPI:
    """pool、transport、model_router、diagnosis_params、mastery、profile、practice_clock、practice_lock_pool、diagnosis_clock 供测试注入；
    不传时按配置创建。注入业务池又开了再练时必须一并注入再练锁池（锁连接不能取自业务池，审查 B2）。"""
    state: dict = {"router": model_router}
    if pool is None:
        # LT-03 决定记录（续）第 6 条：staging 连库必须 verify-full + 固定 CA（/run/rds-ca/ca.pem），在建任何连接之前拒绝
        check_db_tls(settings.database_url, settings.env, "LEARNING_DATABASE_URL")
    if settings.present_item_source == "adapter":
        # PLT-12：题面经适配器 present_item 只在候选契约 1.1.0 打开时可用（配置层已拦一道；这里在建任何连接之前再拦一道）
        from .present_item import require_candidate

        require_candidate()
    # 错题再练配置（specs/practice-loop-stage1 第 7 节）：不合法拒绝启动；未签字配置出现在 test / dev 以外只是这一模块不启动（接口 501）
    practice_cfg = None
    if settings.practice_config:
        try:
            practice_cfg = practice_config.load(settings.practice_config, env=settings.env)
        except practice_config.PracticeConfigUnsigned:
            log.warning(json.dumps({"event": "practice_disabled", "reason": "unsigned_config"}, ensure_ascii=False))
    # 复测与验证题配置（KGE-05）：不合法拒绝启动；未签字配置出现在 test / dev 以外只是这一模块不启动（接口 501）
    retest_cfg = None
    if settings.retest_config:
        try:
            retest_cfg = retest_config.load(settings.retest_config, env=settings.env)
        except retest_config.RetestConfigUnsigned:
            log.warning(json.dumps({"event": "retest_disabled", "reason": "unsigned_config"}, ensure_ascii=False))
    # 今日计划跨科编排配置（PLT-11）：不合法拒绝启动；未签字配置出现在 test / dev 以外只是这一模块不启动（接口 501）
    today_cfg = None
    if settings.today_config:
        try:
            today_cfg = today_config.load(settings.today_config, env=settings.env)
        except today_config.TodayConfigUnsigned:
            log.warning(json.dumps({"event": "today_disabled", "reason": "unsigned_config"}, ensure_ascii=False))
    # 学情档案页配置（KGE-04 第二步 PR 4）启动即加载与校验：未签字或合成配置出现在非 test 环境、短语不在衔接短语表里，一律拒绝启动
    profile_cfg = load_profile_config(settings.profile_page_config, env=settings.env) if settings.profile_page_config else None
    # 掌握配置启动即加载与校验（KGE-04 第二步）：不合法、学科对不上、未签字或合成配置出现在非 test 环境，一律拒绝启动
    configs = {}
    for subject, path in sorted(settings.mastery_configs.items()):
        cfg = load_config(path, env=settings.env)
        if cfg.subject != subject:
            raise RuntimeError(f"LEARNING_MASTERY_CONFIG_{subject.upper()} 指向的是另一科的配置")
        configs[subject] = cfg

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 启动时就加载契约：共享库或契约文件缺失时立即失败，而不是等到第一个请求（安全审查 S6）
        plugin_contract()
        internal_contract()
        internal_contract("1.0.0")  # v1 信封契约（安全审查建议 9：文件缺失时启动即失败）
        own_pool = pool is None
        p = pool or await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        # 迁移 0016 必须先于本版本执行：v0、v1 写入都要查封存表（安全审查必须改 1）
        if not await p.fetchval("SELECT to_regclass('platform.replay_seal') IS NOT NULL"):
            if own_pool:
                await p.close()
            raise RuntimeError("platform.replay_seal is missing: run migration 0016 before starting this version")
        # 迁移 0058 必须先于本版本执行：/internal/v1 写入前要经它核对计量（LT-04）；缺了所有 v1 写入都会失败，启动即拒绝。
        # 函数在但当前登录角色没有执行权（授权没跟上、连错角色）同样拒绝启动（决定记录（再续）第 4 条），不等到第一笔写入才 500
        if not await p.fetchval("SELECT to_regprocedure($1) IS NOT NULL", store.METER_CHECK_FUNCTION):
            if own_pool:
                await p.close()
            raise RuntimeError("platform.learning_meter_check is missing: run migration 0058 before starting this version")
        if not await p.fetchval("SELECT has_function_privilege(current_user, $1, 'EXECUTE')", store.METER_CHECK_FUNCTION):
            if own_pool:
                await p.close()
            raise RuntimeError("platform.learning_meter_check is not executable by this role: check the grants of migration 0058")
        # trust_env=False：不读 HTTP(S)_PROXY 等环境变量，含学生作答的请求体不会被悄悄送进代理（安全审查 S1）
        client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False)
        engine = mastery
        if engine is None and configs:
            # 掌握写入依赖迁移 0037（节点键、复测任务表）
            if not await p.fetchval("SELECT to_regclass('platform.retest_task') IS NOT NULL"):
                await client.aclose()
                if own_pool:
                    await p.close()
                raise RuntimeError("platform.retest_task is missing: run migration 0037 before enabling mastery")
            engine = MasteryEngine(p, configs)
        state["mastery"] = engine
        # 档案页：不调模型（第一步 #19）；只由会话开始（编排层）与周报（洞察服务）经 ensure 触发，作答与重算不触发
        state["profile"] = profile if profile is not None else (ProfilePages(p, profile_cfg) if profile_cfg is not None else None)
        state["mastery_configs"] = dict(engine.configs) if engine is not None else configs
        state["service"] = IngestService(settings, p, client, mastery=engine)
        state["plan"] = (p, client)
        if settings.diagnosis_presenter == "synthetic":
            try:
                await _check_diagnosis_env(p)
            except RuntimeError:
                await client.aclose()
                if own_pool:
                    await p.close()
                raise
            state["diagnosis"] = _diagnosis(settings, p, client, state["service"], diagnosis_params, diagnosis_clock)
        lock_pool, own_lock_pool = practice_lock_pool, False
        if practice_cfg is not None:
            try:
                await _check_practice_env(p, settings.practice_presenter == "synthetic")
                if lock_pool is None and not own_pool:
                    raise RuntimeError("注入业务池时需同时注入再练锁池（practice_lock_pool）")
            except RuntimeError:
                await client.aclose()
                if own_pool:
                    await p.close()
                raise
            if lock_pool is None:
                # 作答串行锁的独立小连接池（审查 B2）：会话级咨询锁要握到判分结束，放在业务池里会与 ingest 争连接而互相等死
                lock_pool = await asyncpg.create_pool(settings.database_url, min_size=0, max_size=PRACTICE_LOCK_POOL_SIZE)
                own_lock_pool = True
            state["practice"] = _practice(settings, practice_cfg, p, client, state["service"], practice_clock, lock_pool)
            if retest_cfg is not None:
                # 复测与验证题（KGE-05）：复用再练的选题、题面、票据密钥与单元锁；再练没启动时复测接口一律 501
                from .retest.service import RetestService
                state["retest"] = RetestService(practice=state["practice"], cfg=retest_cfg,
                                               clock=state["practice"].clock)
        if today_cfg is not None:
            # 今日计划快照（PLT-15）：开关打开时写 platform.today_plan_item，依赖迁移 0055
            if settings.today_snapshot and not await p.fetchval("SELECT to_regclass('platform.today_plan_item') IS NOT NULL"):
                await client.aclose()
                if own_lock_pool and lock_pool is not None:
                    await lock_pool.close()
                if own_pool:
                    await p.close()
                raise RuntimeError("platform.today_plan_item is missing: run migration 0055 before enabling LEARNING_TODAY_SNAPSHOT")
            # 今日计划跨科编排（PLT-11）：只读汇总 plan、再练与复测（运行时从 state 取；没启动的来源标 source_unavailable）
            from .today.service import TodayService
            state["today"] = TodayService(settings=settings, cfg=today_cfg, state=state)
        # 有路由客户端时才建模型路由：环境从 LEARNING_ENV 读，配置不合规（出境、未批准供应商等）即拒绝启动
        own_router = state["router"] is None and bool(settings.route_clients)
        if own_router:
            state["router"] = ModelRouter.from_config(load_for_deployment(), sink=metrics.router_sink())  # 调用记录：日志 + 指标
        # 就绪探针（LT-03）：上面的启动前置全部通过之后才放进连接池；/readyz 只看它在不在、能不能 SELECT 1
        state["ready_pool"] = p
        try:
            yield
        finally:
            state.pop("ready_pool", None)
            if own_router:
                await state["router"].aclose()
            await client.aclose()
            if own_lock_pool and lock_pool is not None:
                await lock_pool.close()
            if own_pool:
                await p.close()

    app = FastAPI(title="StarBuddy learning service (internal)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # 健康端点（LT-03；接口定义 services/learning/openapi/health.v0.yaml）：无需鉴权，响应只含状态
    async def ready_probe() -> bool:
        p = state.get("ready_pool")
        if not await health.pool_alive(p):
            return False
        # 契约审查建议 4：0058 被回滚或执行权被收回时不再就绪（传 oid，函数不存在时不报错、直接为假）
        return await p.fetchval(READY_METER_SQL, store.METER_CHECK_FUNCTION) is True

    cached = health.register(app, ready_probe)
    # 指标（LT-05，learning/obs.py、learning/metrics.py）：业务端口与回环路由监听上的 /metrics 一律 404；
    # GET /metrics 只在单独的指标端口（LEARNING_METRICS_PORT，route_listener.serve 另起监听，只服务这一条路径）
    obs.register(app, "learning", pools=lambda: [state.get("ready_pool")], ready=cached)
    metrics.install(lambda: state.get("router"), lambda: state.get("ready_pool"))

    async def handle(method: str, request: Request, version: str) -> JSONResponse:  # 只有 /internal/v1（1.0.0）调用
        started = time.monotonic()
        rid: str | None = None
        record: dict = {"method": method}
        try:
            if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            raw = await _read_limited(request)
            try:
                body = strict_json(raw)
            except ValueError:
                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
            inner = body.get("request", body) if isinstance(body, dict) and version != "0.9.0" else body  # v1 是信封
            candidate = inner.get("context", {}).get("request_id") if isinstance(inner, dict) and isinstance(inner.get("context"), dict) else None
            rid = candidate if isinstance(candidate, str) and UUID_RE.fullmatch(candidate) else None
            summary, replayed = await state["service"].ingest(method, body, version)
            record.update(status=200, replayed=replayed)
            return JSONResponse(summary, headers={"idempotent-replayed": "true" if replayed else "false"})
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return _problem(e, rid)
        except Exception as e:  # 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            # 5xx 一律是结果未知（评审 N18）：提交阶段出错时事务可能已提交，BFF 必须同 key 重试
            return _problem(IngestError(500, "INTERNAL", "internal error", retryable=True), rid)
        finally:
            if rid:
                record["request_id"] = rid
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    diagnosis_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)
    # 错题再练（specs/practice-loop-stage1 第 4 节）：只认 BFF 服务令牌；编排层、洞察服务、路由客户端令牌一律 401
    practice_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)
    # 复测与验证题（KGE-05）：同再练，只认 BFF 服务令牌
    retest_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)
    # 今日计划跨科编排（PLT-11）：同再练，只认 BFF 服务令牌
    today_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)
    # learning-internal 1.1.0（KGE-04 第二步 PR 3）：掌握读取只认编排层令牌；验证任务与撤销只认洞察服务令牌（未配置即一律 401）；
    # BFF 服务令牌对这四个接口一律 401；档案页 ensure 认编排层（会话开始）与洞察服务（周报），read 只认编排层（PR 4）；
    # 星图节点星态只认 BFF 服务令牌（H5 星图 v1 PR 1）
    insight_token = settings.insight_client_credential
    orchestrator_token = settings.orchestrator_client_credential
    mastery_routes.register(
        app, orchestrator_ok=lambda r: orchestrator_token is not None and _bearer_ok(r.headers.get("authorization"), orchestrator_token),
        insight_ok=lambda r: insight_token is not None and _bearer_ok(r.headers.get("authorization"), insight_token),
        bff_ok=lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state=state)

    def retired_v0(method: str) -> JSONResponse:
        """v0 写入已下线（LT-04 决定记录（再续）第 1 条）：一律 410，不读请求体、不验令牌、不核对、不调适配器、不落库。
        retryable = false：BFF 按“确定没有写入”释放预留；写入只经 /internal/v1（BFF 服务令牌 + 计量核对）。"""
        log.info(json.dumps({"method": method, "path": "v0", "status": 410, "code": "INVALID_REQUEST"}, ensure_ascii=False))
        return _problem(IngestError(410, "INVALID_REQUEST", V0_RETIRED), None)

    @app.post("/internal/v0/grade")
    async def grade(request: Request) -> JSONResponse:  # noqa: ARG001
        return retired_v0("grade")

    @app.post("/internal/v0/diagnose")
    async def diagnose(request: Request) -> JSONResponse:  # noqa: ARG001
        return retired_v0("diagnose")

    @app.post("/internal/v1/grade")
    async def grade_v1(request: Request) -> JSONResponse:
        return await handle("grade", request, "1.0.0")

    @app.post("/internal/v1/diagnose")
    async def diagnose_v1(request: Request) -> JSONResponse:
        return await handle("diagnose", request, "1.0.0")

    @app.post("/internal/v1/plan")
    async def plan(request: Request) -> JSONResponse:
        """今日计划（learning-internal.v1，冲刺 4-H；非消耗类，不写任何表）。日志只记学科数与各科状态，不记学生 ID 与任务。"""
        started = time.monotonic()
        rid: str | None = None
        record: dict = {"method": "plan"}
        try:
            if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            raw = await _read_limited(request)
            try:
                body = strict_json(raw)
            except ValueError:
                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
            candidate = body.get("request_id") if isinstance(body, dict) else None
            rid = candidate if isinstance(candidate, str) and UUID_RE.fullmatch(candidate) else None
            pool_, client_ = state["plan"]
            digest, fields = await plan_today(settings, pool_, client_, body)
            record.update(status=200, **fields)
            return JSONResponse(digest)
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return _problem(e, rid)
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return _problem(IngestError(500, "INTERNAL", "internal error", retryable=True), rid)
        finally:
            if rid:
                record["request_id"] = rid
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    @app.post("/internal/v1/model/route")
    async def model_route(request: Request) -> JSONResponse:
        """各科适配器调模型的唯一入口（learning-internal.v1）：令牌绑定学科；日志不记 messages 与 output。"""
        started = time.monotonic()
        record: dict = {"method": "model_route"}
        try:
            header = request.headers.get("authorization")
            # 全部比完再取结果：不按命中顺序提前返回（安全审查建议 1）
            hits = [s for s, tok in sorted(settings.route_clients.items()) if _bearer_ok(header, tok)]
            subject = hits[0] if len(hits) == 1 else None
            if subject is None:
                raise RouteApiError(401, "UNAUTHENTICATED", "missing or invalid route client token")
            record["subject"] = subject
            if state["router"] is None:
                raise RouteApiError(503, "ROUTE_UNAVAILABLE", "model router is not configured", retryable=True)
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_ROUTE_BODY:
                raise RouteApiError(413, "INVALID_REQUEST", "request body too large")
            raw = b""
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > MAX_ROUTE_BODY:
                    raise RouteApiError(413, "INVALID_REQUEST", "request body too large")
            try:
                body = strict_json(raw)
            except ValueError:
                raise RouteApiError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
            req = parse_request(body, subject)
            record.update(task=req.task.value, data_class=req.data_class.value)
            out = await route_call(state["router"], req)
            record["status"] = 200
            return JSONResponse(out)
        except RouteApiError as e:
            record.update(status=e.status, code=e.code)
            if e.detail is not None:
                record["detail"] = e.detail
            return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json", headers=e.headers or None)
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出请求内容
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return JSONResponse(RouteApiError(500, "INTERNAL", "internal error", retryable=True).problem(), status_code=500,
                                media_type="application/problem+json")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    @app.get("/internal/v1/replay/{method}/{key}")
    async def replay(method: str, key: str, request: Request) -> JSONResponse:
        """只查询、不执行（learning-internal.v1，评审 N17）。不存在、参数不匹配都返回同样的 404，不透露 key 是否存在。"""
        started = time.monotonic()
        record: dict = {"method": f"replay:{method}" if method in ("grade", "diagnose") else "replay:invalid"}
        try:
            if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            student = request.query_params.get("student_ref", "")
            usage = request.query_params.get("usage_id", "")
            if (method not in ("grade", "diagnose") or not UUID4_RE.fullmatch(key)
                    or not UUID_RE.fullmatch(student) or not UUID_RE.fullmatch(usage)):
                raise IngestError(400, "INVALID_REQUEST", "method, idempotency_key, student_ref and usage_id are required")
            r = await state["service"].replay(method, key, student.lower(), usage.lower())
            if r.kind == "found":
                record.update(status=200, replayed=True)
                return JSONResponse(r.summary, headers={"idempotent-replayed": "true"})
            if r.kind == "busy":
                raise IngestError(409, "BUSY", "another request with this key is still running", retryable=True)
            raise IngestError(404, "REPLAY_NOT_FOUND", "no recorded result for this key")
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return _problem(e, None)
        except Exception as e:  # noqa: BLE001
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return _problem(IngestError(500, "INTERNAL", "internal error", retryable=True), None)
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    return app
