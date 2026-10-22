"""配置：全部来自环境变量，不写进仓库。

只允许开发、测试与 staging 环境（CLAUDE.md 规则 9：不接触生产）。pilot 与生产的部署配置由人决定。
  LEARNING_ENV                   dev | test | staging（staging：LT-01 决定记录第 1 条，拒绝合成配置 synthetic、合成夹具与
                                 STARBUDDY_ALLOW_CANDIDATE_CONTRACT；未签字的配置文件照旧不启动对应模块）
  LEARNING_HOST                  监听地址，缺省 127.0.0.1；只有 staging 能设为内网 IPv4（RFC 1918），dev / test 设别的值拒绝启动
  服务间地址（适配器、知识服务）：https 或本机回环的 http；staging 另收内网 IPv4 字面量的 http（learning/netpolicy.py）
  LEARNING_DATABASE_URL          asyncpg 连接串；登录角色只继承 starbuddy_learning
  LEARNING_SERVICE_TOKEN         BFF → 学习服务的服务间令牌（≥ 32 字符）
  LEARNING_ADAPTER_<SUBJECT>_URL    例如 LEARNING_ADAPTER_AYA_URL=http://127.0.0.1:8787
  LEARNING_ADAPTER_<SUBJECT>_TOKEN  学习服务 → 该科适配器的服务间令牌
  LEARNING_ROUTE_CLIENT_<SUBJECT>_TOKEN  该科适配器 → 学习服务内部模型路由接口的令牌（≥ 32 字符，令牌绑定学科；不设即该科不能调模型）
  自适应诊断（specs/sie-02a-math-diagnosis）：
  LEARNING_DIAGNOSIS_PRESENTER   不设 = 诊断接口回 501；synthetic = 合成题面（只允许 LEARNING_ENV=test，契约 present_item 签字前不接真实题库）
  LEARNING_DIAGNOSIS_SOURCE      fixture（缺省，合成夹具，只允许 test）| knowledge（经知识服务 node.path / item.search）
  LEARNING_KNOWLEDGE_URL / LEARNING_KNOWLEDGE_TOKEN   知识服务地址（https 或本机回环）与 caller=learning 的令牌（≥ 32 字符）
  LEARNING_DIAGNOSIS_TARGETS     knowledge 模式下的目标节点：JSON {"年级": [节点 ID…]}；LEARNING_DIAGNOSIS_MAP_VERSION 地图版本号
  掌握重算（KGE-04 第二步，specs/kge-04-mastery-step2）：
  LEARNING_MASTERY_CONFIG_<SUBJECT>  该科掌握配置文件（services/learning/config/mastery/）。不设 = 该科不写掌握记录；
                                     未签字或合成配置只允许 LEARNING_ENV=test 加载，不合法拒绝启动
  LEARNING_MASTERY_SYNC_BUDGET_SECONDS  落库后同步重算的时间预算，默认 2（0 < 值 ≤ 10）；超时交给对账
  LEARNING_INSIGHT_CLIENT_TOKEN  洞察服务 → 学习服务验证任务接口（/internal/v1/verification 与 /verification/cancel）的令牌（≥ 32 字符，
                                 learning-internal 1.1.0 insightServiceToken）；只对三个接口有效：/verification、/verification/cancel 与
                                 /profile-page/ensure（只能 trigger = weekly，KGE-04 第二步 PR 4），与其他令牌各不相同。不设 = 这三个接口一律不认洞察服务
                                 按令牌区分调用方（编排层 / 洞察服务 / BFF）依赖下面“令牌互不相同”的启动校验
  LEARNING_ORCHESTRATOR_CLIENT_TOKEN  编排层 → 学习服务掌握读取（/internal/v1/mastery/get、/explain）的令牌（≥ 32 字符，
                                 orchestratorServiceToken）；只对这两个接口有效，与其他令牌各不相同。不设 = 掌握读取一律 401
                                 （KGE-04 第二步 PR 4 起也用于档案页 /internal/v1/profile-page/ensure 与 /read；洞察服务令牌另可调 ensure，只能是周报触发）
  LEARNING_PROFILE_PAGE_CONFIG   学情档案页配置文件（归节与固定衔接短语表，services/learning/config/mastery/profile-page*.json）。
                                 不设 = 档案页接口回 409 PROFILE_UNAVAILABLE；未签字或合成配置只允许 LEARNING_ENV=test 加载，不合法拒绝启动
  错题再练（specs/practice-loop-stage1）：
  LEARNING_PRACTICE_CONFIG       synthetic（合成占位，只允许 test / dev）或配置文件路径。不设 = 再练接口一律 501 PRACTICE_UNAVAILABLE；
                                 未签字的配置在 test / dev 以外不启动这一模块（接口 501），不合法拒绝启动
  LEARNING_PRACTICE_PRESENTER    不设 = next / answer 回 501；synthetic = 合成题面（只允许 LEARNING_ENV=test，present_item 签字前不接真实题库）
  LEARNING_PRACTICE_TICKET_SECRET  再练票据的 HMAC 密钥（≥ 32 字符，与所有令牌各不相同）；设了 LEARNING_PRACTICE_CONFIG 就必须设
  选题经知识服务（LEARNING_KNOWLEDGE_URL / LEARNING_KNOWLEDGE_TOKEN，同上）；不设 = next 回 501
  复测与验证题交付（KGE-05，specs/retest-delivery-kge05）：
  LEARNING_RETEST_CONFIG         synthetic（合成占位，只允许 test / dev）或配置文件路径。不设 = 复测接口一律 501 RETEST_UNAVAILABLE；
                                 复用再练的选题、题面与票据密钥，所以必须同时设 LEARNING_PRACTICE_CONFIG；未签字的配置在 test / dev 以外
                                 不启动这一模块（接口 501），不合法拒绝启动
  今日计划跨科编排（PLT-11，specs/today-plan-orchestration-plt11）：
  LEARNING_TODAY_CONFIG          synthetic（合成占位，只允许 test / dev）或配置文件路径。不设 = /internal/v1/today 一律 501 TODAY_UNAVAILABLE；
                                 未签字的配置在 test / dev 以外不启动这一模块（接口 501），不合法拒绝启动。只读汇总：再练、复测没启动时
                                 对应来源标 source_unavailable，不要求同时设它们
  LEARNING_TODAY_SNAPSHOT        今日计划快照（PLT-15，specs/today-plan-snapshot-plt15，迁移 0055）：不设或 0 = 关（缺省，
                                 /internal/v1/today 只读、响应与 PLT-11 逐字段一致）；1 = 当天首次读取冻结快照、每条带 task_id，
                                 同日再读返回同一批（写 platform.today_plan_item，启动时核对迁移 0055 已执行）。其他取值拒绝启动；
                                 设了 1 就必须设 LEARNING_TODAY_CONFIG
  题面来源（PLT-12，specs/present-item-contract）：
  LEARNING_PRESENT_ITEM_SOURCE   synthetic（缺省：诊断与再练用合成题面，行为与之前逐字节一致）| adapter（经适配器 present_item，
                                 插件契约 1.1.0 候选版；只在 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 时允许，否则拒绝启动）。只换题面来源：
                                 诊断与再练仍由 LEARNING_DIAGNOSIS_PRESENTER / LEARNING_PRACTICE_PRESENTER 打开，判分版本与判分方式仍取合成登记
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .netpolicy import STAGING, SERVICE_ENVS, check_image_env, listen_host, service_url_ok

SUBJECTS = ("wenli", "aya", "zhixing", "echo")
ALLOWED_ENVS = ("dev", "test")
# LT-01 决定记录第 1 条：服务本身收 SERVICE_ENVS（dev / test / staging；staging 下拒绝合成配置与开发开关，见 from_env）；
# 命令行任务（learning.jobs）同样收 staging（调度器在 staging 调 mastery-sweep），其余入口仍只收 ALLOWED_ENVS
SYNTHETIC_KEYWORD_VARS = ("LEARNING_PRACTICE_CONFIG", "LEARNING_RETEST_CONFIG", "LEARNING_TODAY_CONFIG")
PRESENT_ITEM_SOURCES = ("synthetic", "adapter")  # PLT-12：题面来源


def _budget(raw: str | None) -> float:
    if not raw:
        return 2.0
    try:
        v = float(raw)
    except ValueError:
        raise RuntimeError("LEARNING_MASTERY_SYNC_BUDGET_SECONDS 必须是数") from None
    if not 0 < v <= 10:
        raise RuntimeError("LEARNING_MASTERY_SYNC_BUDGET_SECONDS 必须在 (0, 10]")
    return v


def mastery_config_paths(e: dict[str, str]) -> dict[str, str]:
    """学科 → 掌握配置文件路径（LEARNING_MASTERY_CONFIG_<SUBJECT>）；没设的学科不在里面（不写掌握记录）。"""
    return {s: e[f"LEARNING_MASTERY_CONFIG_{s.upper()}"] for s in SUBJECTS if e.get(f"LEARNING_MASTERY_CONFIG_{s.upper()}")}


@dataclass(frozen=True)
class AdapterTarget:
    base_url: str
    credential: str = field(repr=False)   # 令牌不进 repr（错题再练审查 S3；只改 repr，不改行为）


# 令牌、密钥与可能带口令的连接串不进 repr（错题再练审查 S3）：日志或异常里打印 Settings 时不会带出；比较与哈希照旧
@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str = field(repr=False)
    service_credential: str = field(repr=False)
    adapters: dict[str, AdapterTarget] = field(default_factory=dict)
    # 学科 → 路由客户端令牌（specs/adapter-zhixing-sprint2 设计要点 5）
    route_clients: dict[str, str] = field(default_factory=dict, repr=False)
    # pending 租约（spec 待人确认 #5）：必须长于适配器调用的总预算（2 × 超时 + 1）再加核对与落库的余量
    # （安全审查 S6）。diagnose 总预算 61 秒，所以租约 90 秒。
    lease_seconds: float = 90.0
    grade_timeout: float = 10.0
    diagnose_timeout: float = 30.0
    # 今日计划（冲刺 4-H）：非消耗类，单科适配器调用的单次超时；超时的学科显示“今天暂无安排”，不拖住其他科
    plan_timeout: float = 2.0
    # 自适应诊断（SIE-02a）
    diagnosis_presenter: str | None = None
    diagnosis_source: str = "fixture"
    knowledge: AdapterTarget | None = None
    diagnosis_targets: dict[int, list[str]] = field(default_factory=dict)
    diagnosis_map_version: str | None = None
    # 掌握重算（KGE-04 第二步）：学科 → 配置文件路径；启动时加载并校验
    mastery_configs: dict[str, str] = field(default_factory=dict)
    # grade / diagnose 落库后同步重算的时间预算（秒）；超时记 failed、交给对账，接口照常返回
    mastery_sync_budget_seconds: float = 2.0
    # 洞察服务 → 验证任务接口的令牌（只对 /internal/v1/verification 有效）；None = 该接口不接受任何调用
    insight_client_credential: str | None = field(default=None, repr=False)
    # 编排层 → 掌握读取的令牌（只对 /internal/v1/mastery/*）；None = 掌握读取不接受任何调用
    orchestrator_client_credential: str | None = field(default=None, repr=False)
    # 学情档案页配置文件路径（KGE-04 第二步 PR 4）；None = 档案页接口不可用
    profile_page_config: str | None = None
    # 错题再练（specs/practice-loop-stage1）：配置（synthetic 或路径；None = 接口 501）、题面提供者、票据密钥
    practice_config: str | None = None
    practice_presenter: str | None = None
    practice_ticket_secret: str | None = field(default=None, repr=False)
    # 题面来源（PLT-12）：synthetic（缺省）| adapter（插件契约 1.1.0 present_item；候选开关打开时才允许）
    present_item_source: str = "synthetic"
    # 复测与验证题（KGE-05）：配置（synthetic 或路径；None = 接口 501）。依赖再练（选题、题面、票据密钥）
    retest_config: str | None = None
    # 今日计划跨科编排（PLT-11）：配置（synthetic 或路径；None = 接口 501）
    today_config: str | None = None
    # 今日计划快照（PLT-15）：缺省关；打开时 /internal/v1/today 冻结当天快照并返回 task_id
    today_snapshot: bool = False
    # 监听地址（LT-01）：缺省 127.0.0.1；只有 staging 能配置为内网 IPv4（LEARNING_HOST）
    host: str = "127.0.0.1"
    # staging 的回环路由监听端口（LT-02 决定记录（续）第 5 条）：127.0.0.1 上只服务 /internal/v1/model/route；
    # 只在 staging（缺省 8785，LEARNING_ROUTE_PORT 可改）；dev、test 为 None（不起，设了拒绝启动）
    route_port: int | None = None

    @staticmethod
    def from_env(environ: dict[str, str] | None = None) -> "Settings":
        e = dict(os.environ if environ is None else environ)
        env = e.get("LEARNING_ENV", "")
        if env not in SERVICE_ENVS:
            raise RuntimeError(f"LEARNING_ENV 必须是 {SERVICE_ENVS} 之一；本服务不连接 pilot 或生产环境")
        if env == STAGING:
            # staging 拒绝合成配置与开发开关（LT-01 决定记录第 1 条）；未签字的配置文件照旧只是不启动对应模块（接口 501）
            for name in SYNTHETIC_KEYWORD_VARS:
                if e.get(name) == "synthetic":
                    raise RuntimeError(f"{name}=synthetic 是合成占位：staging 拒绝启动（须配教研签字的配置文件）")
            if e.get("STARBUDDY_ALLOW_CANDIDATE_CONTRACT"):
                raise RuntimeError("STARBUDDY_ALLOW_CANDIDATE_CONTRACT 是开发开关：staging 拒绝启动")
            if e.get("LEARNING_DIAGNOSIS_SOURCE") == "fixture":
                raise RuntimeError("LEARNING_DIAGNOSIS_SOURCE=fixture 是合成夹具：staging 拒绝启动")
        check_image_env(env, e)
        host = listen_host(e.get("LEARNING_HOST"), env, "LEARNING_HOST")
        credential = e.get("LEARNING_SERVICE_TOKEN", "")
        if len(credential) < 32:
            raise RuntimeError("LEARNING_SERVICE_TOKEN 缺失或少于 32 个字符")
        database_url = e.get("LEARNING_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("缺少 LEARNING_DATABASE_URL")
        adapters = {}
        for subject in SUBJECTS:
            url = e.get(f"LEARNING_ADAPTER_{subject.upper()}_URL")
            if url:
                cred = e.get(f"LEARNING_ADAPTER_{subject.upper()}_TOKEN", "")
                if len(cred) < 32:
                    raise RuntimeError(f"LEARNING_ADAPTER_{subject.upper()}_TOKEN 缺失或少于 32 个字符")
                if not service_url_ok(url, env):
                    raise RuntimeError(f"LEARNING_ADAPTER_{subject.upper()}_URL 必须是 https，或本机回环地址（staging 另收内网 IPv4）")
                adapters[subject] = AdapterTarget(url.rstrip("/"), cred)
        route_clients = {}
        for subject in SUBJECTS:
            tok = e.get(f"LEARNING_ROUTE_CLIENT_{subject.upper()}_TOKEN")
            if tok is not None:
                if len(tok) < 32:
                    raise RuntimeError(f"LEARNING_ROUTE_CLIENT_{subject.upper()}_TOKEN 少于 32 个字符")
                route_clients[subject] = tok
        presenter = e.get("LEARNING_DIAGNOSIS_PRESENTER") or None
        if presenter not in (None, "synthetic"):
            raise RuntimeError("LEARNING_DIAGNOSIS_PRESENTER 只能不设或为 synthetic")
        if presenter == "synthetic" and env != "test":
            raise RuntimeError("合成题面只允许 LEARNING_ENV=test（契约 present_item 签字前不接真实题库）")
        # staging 缺省经知识服务（合成夹具只在 test；LT-01 安全审查 S10）
        source = e.get("LEARNING_DIAGNOSIS_SOURCE") or ("knowledge" if env == STAGING else "fixture")
        if source not in ("fixture", "knowledge"):
            raise RuntimeError("LEARNING_DIAGNOSIS_SOURCE 只能是 fixture 或 knowledge")
        if presenter and source == "fixture" and env != "test":
            raise RuntimeError("合成诊断夹具只允许 LEARNING_ENV=test")
        knowledge = None
        targets: dict[int, list[str]] = {}
        map_version = None
        practice_cfg = e.get("LEARNING_PRACTICE_CONFIG") or None
        practice_presenter = e.get("LEARNING_PRACTICE_PRESENTER") or None
        if practice_presenter not in (None, "synthetic"):
            raise RuntimeError("LEARNING_PRACTICE_PRESENTER 只能不设或为 synthetic")
        if practice_presenter == "synthetic" and env != "test":
            raise RuntimeError("再练合成题面只允许 LEARNING_ENV=test（契约 present_item 签字前不接真实题库）")
        present_source = e.get("LEARNING_PRESENT_ITEM_SOURCE") or "synthetic"
        if present_source not in PRESENT_ITEM_SOURCES:
            raise RuntimeError("LEARNING_PRESENT_ITEM_SOURCE 只能是 synthetic 或 adapter")
        if present_source == "adapter" and e.get("STARBUDDY_ALLOW_CANDIDATE_CONTRACT") != "1":
            raise RuntimeError("LEARNING_PRESENT_ITEM_SOURCE=adapter 走插件契约 1.1.0 候选版的 present_item：签字前只在测试里开"
                               "（需 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1）")
        practice_secret = e.get("LEARNING_PRACTICE_TICKET_SECRET") or None
        if practice_cfg and (practice_secret is None or len(practice_secret) < 32):
            raise RuntimeError("设了 LEARNING_PRACTICE_CONFIG 就必须设 LEARNING_PRACTICE_TICKET_SECRET（≥ 32 字符）")
        retest_cfg = e.get("LEARNING_RETEST_CONFIG") or None
        if retest_cfg and not practice_cfg:
            raise RuntimeError("LEARNING_RETEST_CONFIG 复用再练的选题、题面与票据密钥：必须同时设 LEARNING_PRACTICE_CONFIG")
        if (presenter and source == "knowledge") or (practice_cfg and e.get("LEARNING_KNOWLEDGE_URL")):
            url, tok = e.get("LEARNING_KNOWLEDGE_URL", ""), e.get("LEARNING_KNOWLEDGE_TOKEN", "")
            if len(tok) < 32:
                raise RuntimeError("LEARNING_KNOWLEDGE_TOKEN 缺失或少于 32 个字符")
            if not service_url_ok(url, env):
                raise RuntimeError("LEARNING_KNOWLEDGE_URL 必须是 https，或本机回环地址（staging 另收内网 IPv4）")
            knowledge = AdapterTarget(url.rstrip("/"), tok)
        if presenter and source == "knowledge":
            try:
                raw = json.loads(e.get("LEARNING_DIAGNOSIS_TARGETS", ""))
                targets = {int(g): [str(n) for n in v] for g, v in raw.items()}
            except (ValueError, AttributeError, TypeError):
                raise RuntimeError("LEARNING_DIAGNOSIS_TARGETS 必须是 JSON {\"年级\": [节点 ID…]}") from None
            map_version = e.get("LEARNING_DIAGNOSIS_MAP_VERSION", "")
            if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,4}", map_version):
                raise RuntimeError("LEARNING_DIAGNOSIS_MAP_VERSION 必须是 主.次.修订")
        insight_tok = e.get("LEARNING_INSIGHT_CLIENT_TOKEN") or None
        if insight_tok is not None and len(insight_tok) < 32:
            raise RuntimeError("LEARNING_INSIGHT_CLIENT_TOKEN 少于 32 个字符")
        orch_tok = e.get("LEARNING_ORCHESTRATOR_CLIENT_TOKEN") or None
        if orch_tok is not None and len(orch_tok) < 32:
            raise RuntimeError("LEARNING_ORCHESTRATOR_CLIENT_TOKEN 少于 32 个字符")
        tokens = [credential, *(a.credential for a in adapters.values()), *route_clients.values(),
                  *([knowledge.credential] if knowledge else []), *([insight_tok] if insight_tok else []),
                  *([orch_tok] if orch_tok else []), *([practice_secret] if practice_secret else [])]
        if len(set(tokens)) != len(tokens):
            raise RuntimeError("服务间令牌、适配器令牌、路由客户端令牌、知识服务令牌、洞察服务与编排层客户端令牌、再练票据密钥必须各不相同")
        snapshot_raw = e.get("LEARNING_TODAY_SNAPSHOT", "")
        if snapshot_raw not in ("", "0", "1"):
            raise RuntimeError("LEARNING_TODAY_SNAPSHOT 只能不设、0 或 1")
        today_cfg = e.get("LEARNING_TODAY_CONFIG") or None
        if snapshot_raw == "1" and not today_cfg:
            raise RuntimeError("LEARNING_TODAY_SNAPSHOT=1 冻结的是今日编排的结果：必须同时设 LEARNING_TODAY_CONFIG")
        route_port = _route_port(e.get("LEARNING_ROUTE_PORT"), e.get("PORT"), env)
        return Settings(env=env, database_url=database_url, service_credential=credential, adapters=adapters,
                        route_clients=route_clients, diagnosis_presenter=presenter, diagnosis_source=source, knowledge=knowledge,
                        diagnosis_targets=targets, diagnosis_map_version=map_version, mastery_configs=mastery_config_paths(e),
                        mastery_sync_budget_seconds=_budget(e.get("LEARNING_MASTERY_SYNC_BUDGET_SECONDS")),
                        insight_client_credential=insight_tok, orchestrator_client_credential=orch_tok,
                        profile_page_config=e.get("LEARNING_PROFILE_PAGE_CONFIG") or None, practice_config=practice_cfg,
                        practice_presenter=practice_presenter, practice_ticket_secret=practice_secret,
                        present_item_source=present_source, retest_config=retest_cfg,
                        today_config=today_cfg, today_snapshot=snapshot_raw == "1", host=host, route_port=route_port)


def _route_port(raw: str | None, main_raw: str | None, env: str) -> int | None:
    """staging 的回环路由监听端口（LT-02 决定记录（续）第 5 条）：只在 staging；1024–65535 的整数，不能与主监听端口相同"""
    from .route_listener import DEFAULT_ROUTE_PORT  # noqa: PLC0415  避免启动配置依赖顺序
    if env != STAGING:
        if raw:
            raise RuntimeError("LEARNING_ROUTE_PORT 只在 staging 生效（回环路由监听）：dev / test 设了拒绝启动")
        return None
    text = raw or str(DEFAULT_ROUTE_PORT)
    if not re.fullmatch(r"[1-9]\d{3,4}", text) or not 1024 <= int(text) <= 65535:
        raise RuntimeError("LEARNING_ROUTE_PORT 必须是 1024–65535 的整数")
    if text == (main_raw or "8788"):
        raise RuntimeError("LEARNING_ROUTE_PORT 不能与主监听端口 PORT 相同")
    return int(text)
