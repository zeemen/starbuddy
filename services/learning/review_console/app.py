"""审核台 v0 的 HTTP 接口（只供审核台前端，spec 冲刺 1 · PR 2）。

身份：staff 登录（PR 4，specs/release-gate-sprint4/pr4-staff-login.md）。前端服务端在请求头 x-review-session 里转交会话令牌，
这里经库函数 platform.staff_session_resolve 解析出 staff 账号；除登录接口外，没有有效会话一律 401。
本模块不接受请求里自报的账号；测试可以注入别的认证器。
接口只返回公共层内容与 staff 账号 ID；日志只记方法、路径模板、状态码与错误码。
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, Protocol

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import service
from .config import PagesSettings, ReviewSettings
from .errors import ReviewError
from .pages import MR_REVIEW_BASIS
from .spot_check import SPOT_CHECK_BASIS, SpotCheckSettings
from .store import KgeStore, ReviewStore, check_roles, db_error

log = logging.getLogger("starbuddy.review_console")
SUBJECTS = ("wenli", "aya", "zhixing", "echo")
DIGEST = r"^[0-9a-f]{64}$"
BASIS = r"^[a-z0-9_]{1,48}$"


class StaffAuthenticator(Protocol):
    """约定（安全审查 B-7）：只能返回已登录会话里、核对过是 active staff 的账号 ID，否则返回 None。
    读接口与改草稿不经库里的 staff 核对，全靠这一步。CSRF 由前端负责（server action 同源校验、SameSite=Strict cookie）。"""

    async def __call__(self, request: Request) -> uuid.UUID | None: ...


SESSION_HEADER = "x-review-session"
MAX_BODY_BYTES = 64 * 1024  # 审核台请求体上限（契约审查 PR 4 S7）：最大的是节点编辑，远小于此
# 后端只给同机前端用：Host 必须是本机地址（挡 DNS 重绑定页面直接调登录接口去撞锁或爆破，安全审查 PR 4 建议 5）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def host_name(host: str) -> str:
    """Host 头去掉端口：`127.0.0.1:8790` → `127.0.0.1`，`[::1]:8790` → `[::1]`。"""
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host
TOKEN = re.compile(r"^[0-9a-f]{64}$")
# 不需要会话的接口：只有登录
PUBLIC_ROUTES = {("POST", "/review/v0/auth/login")}


def session_token(request: Request) -> str | None:
    t = request.headers.get(SESSION_HEADER, "")
    return t if TOKEN.match(t) else None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignBody(_Strict):
    decision: Literal["approved", "rejected"]
    basis_code: str | None = Field(default=None, pattern=BASIS)
    expected_digest: str = Field(pattern=DIGEST)


class ChangeSignBody(_Strict):
    decision: Literal["approved", "rejected"]
    expected_digest: str = Field(pattern=DIGEST)


class PublishBody(_Strict):
    expected_digest: str = Field(pattern=DIGEST)


class RetireBody(_Strict):
    basis_code: str = Field(pattern=BASIS)


class ResolveBody(_Strict):
    resolution_code: str = Field(pattern=BASIS)


ADMISSIONS = Literal["source_unverified", "pending_review", "admitted", "rejected"]
SOURCE_REF = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$"  # 与迁移 0039 platform.item.source_ref 一致
ITEM_ID = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$"  # 与 platform.skill_local_id 域一致
COMMIT = r"^[0-9a-f]{40}$"
CITY = r"^[a-z][a-z0-9\-]{1,31}$"


class AdmissionSignBody(_Strict):
    subject: Literal["wenli", "aya", "zhixing", "echo"]
    item_id: str = Field(pattern=ITEM_ID)
    to_admission: ADMISSIONS
    decision: Literal["approved", "rejected"]
    review_level: str | None = Field(default=None, pattern=r"^Q[0-5]$")
    source_ref: str | None = Field(default=None, pattern=SOURCE_REF)
    source_type: Literal["unknown", "self_authored", "adapted", "textbook", "exam_paper", "reference_book"] | None = None
    license_status: Literal["unverified", "cleared", "denied"] | None = None
    expected_digest: str = Field(pattern=DIGEST)
    basis_code: str | None = Field(default=None, pattern=BASIS)


class AdmissionApplyBody(_Strict):
    review_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class RecordSignBody(_Strict):
    object_type: Literal["source_document", "admission_line", "school"]
    decision: Literal["approved", "rejected"]
    expected_digest: str = Field(pattern=DIGEST)


class PageSignBody(_Strict):
    page_id: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")
    page_commit: str = Field(pattern=COMMIT)
    decision: Literal["approved", "rejected"]


QUEUE_STATUSES = ("open", "in_progress", "resolved")
QUEUE_REASONS = ("subjective_dimension", "low_confidence", "verifier_boundary_case", "content_not_admitted", "ocr_low_confidence",
                 "dimension_unavailable", "student_appeal", "spot_check", "double_grading_disagreement",
                 "full_score_second_review", "profile_dispute", "process_observation")


class LoginBody(_Strict):
    login_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,31}$")
    password: str = Field(min_length=1, max_length=72)  # 字节数另在库里核对（bcrypt 只看前 72 字节）
    code: str = Field(pattern=r"^[0-9]{6}$")


class NodeEdit(_Strict):
    expected_digest: str = Field(pattern=DIGEST)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    includes: list[str] | None = Field(default=None, max_length=32)
    excludes: list[str] | None = Field(default=None, max_length=32)
    examples: list[str] | None = Field(default=None, max_length=32)
    cognitive_levels: list[Literal["know", "understand", "apply", "synthesize"]] | None = Field(default=None, max_length=4)
    scope: Literal["required", "elective", "edition_ext", "exam_ext", "enrichment"] | None = None

    def fields(self) -> dict[str, Any]:
        d = self.model_dump(exclude_none=True, exclude={"expected_digest"})
        for k in ("includes", "excludes", "examples"):
            if k in d and any(not (1 <= len(x) <= 200) for x in d[k]):
                raise ReviewError(422, "INVALID_PARAMETER", "每条包含、不包含、题例 1–200 字")
        texts = [d.get("name") or ""] + [x for k in ("includes", "excludes", "examples") for x in d.get(k, [])]
        if any(CONTROL.search(t) for t in texts):  # NUL 等控制字符会让库报错并把入参写进日志（安全审查 B-6）
            raise ReviewError(422, "INVALID_PARAMETER", "不能含控制字符")
        return d


CONTROL = re.compile(r"[\x00-\x1f\x7f]")
NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")  # 与 kge.node_id 域一致


_UNSET: Any = object()


def create_app(settings: ReviewSettings | None = None, authenticate: StaffAuthenticator | None = None,
               pools: tuple[asyncpg.Pool, asyncpg.Pool] | None = None, pages_settings: PagesSettings | None = _UNSET,
               spot_settings: SpotCheckSettings | None = None) -> FastAPI:
    """pages_settings：编译页面入口的配置；缺省取 settings.pages（测试可直接注入）。spot_settings：事后抽检配置，缺省取 settings.spot_check；
    都没有时抽检接口回 503 SPOT_CHECK_UNCONFIGURED（种子必须由配置给出，仓库里没有缺省种子）。"""
    state: dict[str, Any] = {}

    async def session_auth(request: Request) -> uuid.UUID | None:
        token = session_token(request)
        if token is None:
            return None
        who = await state["review"].resolve(token)
        if who is None:
            return None
        request.state.login_name = who[1]
        return who[0]

    auth = authenticate or session_auth

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lp = None
        if pools is not None:
            kp, rp, own = pools[0], pools[1], False
        else:
            s = settings or ReviewSettings.from_env()
            kp = await asyncpg.create_pool(s.kge_database_url, min_size=1, max_size=5)
            rp = await asyncpg.create_pool(s.review_database_url, min_size=1, max_size=3)
            # 登录专用：同一个签字登录角色，单独两条连接，语句 5 秒超时
            lp = await asyncpg.create_pool(s.review_database_url, min_size=1, max_size=2,
                                           server_settings={"statement_timeout": "5000"})
            own = True
        await check_roles(kp, rp)
        state["kge"], state["review"] = KgeStore(kp), ReviewStore(rp, lp)
        state["pages"] = pages_settings if pages_settings is not _UNSET else (
            settings.pages if settings is not None else (ReviewSettings.from_env().pages if pools is None else None))
        state["spot"] = spot_settings or (settings.spot_check if settings is not None else (
            ReviewSettings.from_env().spot_check if pools is None else None))
        yield
        if own:
            await kp.close()
            await rp.close()
            if lp is not None:
                await lp.close()

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        status, code = 500, "INTERNAL"
        try:
            if host_name(request.headers.get("host", "")) not in LOOPBACK_HOSTS:
                raise ReviewError(421, "MISDIRECTED")
            declared = request.headers.get("content-length", "")
            if (declared and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES)) or \
                    "chunked" in request.headers.get("transfer-encoding", "").lower():
                raise ReviewError(413, "TOO_LARGE")
            if (request.method, request.url.path) not in PUBLIC_ROUTES:
                staff = await auth(request)
                if staff is None:
                    raise ReviewError(401, "UNAUTHENTICATED")
                request.state.staff = staff
            resp = await call_next(request)
            status, code = resp.status_code, "-"
            return resp
        except ReviewError as e:
            status, code = e.status, e.code
            return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json")
        finally:
            route = request.scope.get("route")
            log.info("review %s %s %s %s", request.method, getattr(route, "path", "-"), status, code)

    @app.exception_handler(ReviewError)
    async def _review_error(request: Request, e: ReviewError):
        return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json")

    @app.exception_handler(asyncpg.PostgresError)
    async def _pg_error(request: Request, e: asyncpg.PostgresError):
        err = db_error(e)
        return JSONResponse(err.problem(), status_code=err.status, media_type="application/problem+json")

    @app.exception_handler(asyncpg.InterfaceError)
    async def _pg_interface(request: Request, e: asyncpg.InterfaceError):
        return JSONResponse(ReviewError(500, "INTERNAL").problem(), status_code=500, media_type="application/problem+json")

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, e: RequestValidationError):
        # 不回显请求内容
        return JSONResponse(ReviewError(422, "INVALID_PARAMETER").problem(), status_code=422, media_type="application/problem+json")

    def _subject(subject: str) -> str:
        if subject not in SUBJECTS:
            raise ReviewError(422, "INVALID_PARAMETER", "未知学科")
        return subject

    @app.post("/review/v0/auth/login")
    async def login(body: LoginBody):
        # 失败一律同一个 401：不区分没有这个人、口令错、码错、已锁定、账号停用（库里就不区分）
        token = await state["review"].login(body.login_name, body.password, body.code)
        if token is None:
            raise ReviewError(401, "LOGIN_FAILED")
        return {"token": token}

    @app.post("/review/v0/auth/logout")
    async def logout(request: Request):
        token = session_token(request)
        if token is not None:
            await state["review"].logout(token)
        return {"status": "logged_out"}

    @app.get("/review/v0/auth/me")
    async def me(request: Request):
        return {"account_id": str(request.state.staff), "login_name": getattr(request.state, "login_name", None)}

    @app.get("/review/v0/map-versions")
    async def list_versions(subject: str):
        from .snapshot import norm
        return {"items": norm(await state["kge"].list_versions(_subject(subject)))}

    @app.get("/review/v0/map-versions/{version_id}/diff")
    async def diff(version_id: uuid.UUID):
        return await service.diff_view(state["kge"], version_id)

    @app.patch("/review/v0/map-versions/{version_id}/nodes/{node_id}")
    async def edit_node(version_id: uuid.UUID, node_id: str, body: NodeEdit):
        if not NODE_ID.match(node_id):
            raise ReviewError(422, "INVALID_PARAMETER", "节点 ID 格式不对")
        return {"digest": await state["kge"].edit_node(version_id, node_id, body.fields(), body.expected_digest)}

    @app.post("/review/v0/map-versions/{version_id}/sign")
    async def sign(version_id: uuid.UUID, body: SignBody, request: Request):
        rid = await state["review"].sign_version(request.state.staff, version_id, body.decision, body.basis_code, body.expected_digest)
        return {"review_id": str(rid)}

    @app.post("/review/v0/map-changes/{change_id}/sign")
    async def sign_change(change_id: uuid.UUID, body: ChangeSignBody, request: Request):
        return {"status": await state["review"].sign_change(request.state.staff, change_id, body.decision, body.expected_digest)}

    @app.post("/review/v0/map-versions/{version_id}/publish")
    async def publish(version_id: uuid.UUID, body: PublishBody, request: Request):
        at = await state["review"].publish(request.state.staff, version_id, body.expected_digest)
        return {"published_at": at.isoformat()}

    @app.post("/review/v0/map-versions/{version_id}/retire")
    async def retire(version_id: uuid.UUID, body: RetireBody, request: Request):
        await state["review"].retire(request.state.staff, version_id, body.basis_code)
        return {"status": "retired"}

    # ---------- 统一复核队列（冲刺 2-G） ----------
    @app.get("/review/v0/queue")
    async def queue(request: Request, subject: str | None = None, status: str | None = None, reason: str | None = None,
                    limit: int = 50, before: datetime | None = None, before_id: uuid.UUID | None = None):
        if subject is not None:
            _subject(subject)
        if (status is not None and status not in QUEUE_STATUSES) or (reason is not None and reason not in QUEUE_REASONS) \
                or not 1 <= limit <= 200 or (before_id is not None and before is None):
            raise ReviewError(422, "INVALID_PARAMETER")
        if before is not None and before.tzinfo is None:  # 不带时区的时间会被当成 UTC（安全审查 N3）
            raise ReviewError(422, "INVALID_PARAMETER", "before 必须带时区")
        items = await state["review"].queue(request.state.staff, subject, status, reason, limit, before, before_id)
        # 游标是 (创建时间, id)：同一刻批量进队的任务不会被跳过（安全审查 S2）
        nxt = {"before": items[-1]["created_at"], "before_id": items[-1]["id"]} if len(items) == limit else None
        return {"items": items, "next": nxt}

    @app.get("/review/v0/queue/resolution-codes")
    async def resolution_codes(request: Request):
        return {"items": await state["review"].resolution_codes(request.state.staff)}

    @app.get("/review/v0/queue/{task_id}")
    async def queue_task(task_id: uuid.UUID, request: Request):
        return await state["review"].task_detail(request.state.staff, task_id)

    @app.post("/review/v0/queue/{task_id}/take")
    async def take(task_id: uuid.UUID, request: Request):
        await state["review"].take_task(request.state.staff, task_id)
        return {"status": "in_progress"}

    @app.post("/review/v0/queue/{task_id}/release")
    async def release(task_id: uuid.UUID, request: Request):
        await state["review"].release_task(request.state.staff, task_id)
        return {"status": "open"}

    @app.post("/review/v0/queue/{task_id}/resolve")
    async def resolve_task(task_id: uuid.UUID, body: ResolveBody, request: Request):
        await state["review"].resolve_task(request.state.staff, task_id, body.resolution_code)
        return {"status": "resolved"}

    # ---------- 编译页面（冲刺 3）：只读，列待审合并请求与对账状态，链接到托管平台网页 ----------
    @app.get("/review/v0/pages")
    async def pages_list():
        return await service.pages_view(state["kge"], state["pages"])

    @app.post("/review/v0/pages/sign")
    async def sign_page(body: PageSignBody, request: Request):
        """页面签字入口（PR A 待人确认 1 的默认：审核人在审核台逐个签）：只能签托管平台导出里某个合并请求的头提交，
        风险取合并请求上的标注；同一账号对同一页面同一提交只能签一次（库函数核对）。"""
        mr = service.page_head(state["pages"], body.page_id, body.page_commit)
        service.check_not_author(state["pages"], mr, request.state.staff)  # 提交方不能自签；作者判不出时从严拒绝
        rid = await state["review"].sign_page(request.state.staff, body.page_id, body.page_commit, body.decision, mr.risk, MR_REVIEW_BASIS)
        return {"review_id": str(rid), "risk_level": mr.risk}

    # ---------- 低风险合并窗口的事后抽检（冲刺 4，spec 第 2 节）：固定种子抽样，结果记页面审核记录（依据 spot_check） ----------
    @app.get("/review/v0/spot-checks")
    async def spot_checks(day: dt.date | None = None):
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
        d = day or today
        if d > today:
            raise ReviewError(422, "INVALID_PARAMETER", "不能抽检将来的日期")
        return await service.spot_check_view(state["kge"], state["pages"], state["spot"], d)

    @app.post("/review/v0/spot-checks/sign")
    async def spot_check_sign(body: PageSignBody, request: Request):
        mr = service.spot_check_target(state["pages"], body.page_id, body.page_commit)
        service.check_not_author(state["pages"], mr, request.state.staff)  # 提交方不能抽检自己的合并
        rid = await state["review"].sign_page(request.state.staff, body.page_id, body.page_commit, body.decision, "low", SPOT_CHECK_BASIS)
        return {"review_id": str(rid)}

    # ---------- 内容版本与题目准入（冲刺 4，spec 第 7 节；迁移 0039、0042） ----------
    @app.get("/review/v0/content-versions")
    async def content_versions(subject: str):
        return await service.content_versions_view(state["kge"], _subject(subject))

    @app.get("/review/v0/content-versions/{cv}/diff")
    async def content_diff(cv: uuid.UUID):
        return await service.content_view(state["kge"], cv)

    @app.post("/review/v0/content-versions/{cv}/sign")
    async def content_sign(cv: uuid.UUID, body: SignBody, request: Request):
        rid = await state["review"].sign_content_version(request.state.staff, cv, body.decision, body.basis_code, body.expected_digest)
        return {"review_id": str(rid)}

    @app.post("/review/v0/content-versions/{cv}/publish")
    async def content_publish(cv: uuid.UUID, body: PublishBody, request: Request):
        at = await state["review"].publish_content_version(request.state.staff, cv, body.expected_digest)
        return {"published_at": at.isoformat()}

    @app.post("/review/v0/content-versions/{cv}/retire")
    async def content_retire(cv: uuid.UUID, body: RetireBody, request: Request):
        await state["review"].retire_content_version(request.state.staff, cv, body.basis_code)
        return {"status": "retired"}

    @app.post("/review/v0/admission/sign")
    async def admission_sign(body: AdmissionSignBody, request: Request):
        rid = await state["review"].sign_admission(request.state.staff, body.model_dump())
        return {"review_id": str(rid)}

    @app.post("/review/v0/admission/apply")
    async def admission_apply(body: AdmissionApplyBody, request: Request):
        """执行准入变化（platform.change_admission）。之后的掌握重算由学习服务按执行记录做（KGE-04 learning.mastery.recompute.recompute_item）：
        审核台的两个库身份都不能写个人层，所以这里不直接重算，只返回重算的起点；学习服务以
        `python -m learning.jobs admission-recompute --since <recompute_since>` 读 platform.admission_change_log 逐题重算。"""
        n, since = await state["review"].change_admission(request.state.staff, list(body.review_ids))
        return {"applied": n, "recompute_since": since.isoformat(),
                "recompute": "python -m learning.jobs admission-recompute --since " + since.isoformat()}

    # ---------- 考情政策线（冲刺 4，spec 第 8 节；迁移 0041） ----------
    @app.get("/review/v0/policy")
    async def policy_list(city: str | None = None):
        if city is not None and not re.fullmatch(CITY, city):
            raise ReviewError(422, "INVALID_PARAMETER", "城市编码格式不对")
        return await service.policy_list_view(state["kge"], city)

    @app.get("/review/v0/policy/schemes/{scheme}")
    async def policy_scheme(scheme: uuid.UUID):
        return await service.policy_view(state["kge"], scheme)

    @app.post("/review/v0/policy/schemes/{scheme}/sign")
    async def policy_scheme_sign(scheme: uuid.UUID, body: ChangeSignBody, request: Request):
        return {"approvals": await state["review"].sign_scheme(request.state.staff, scheme, body.decision, body.expected_digest)}

    @app.post("/review/v0/policy/schemes/{scheme}/publish")
    async def policy_scheme_publish(scheme: uuid.UUID, body: PublishBody, request: Request):
        at = await state["review"].publish_scheme(request.state.staff, scheme, body.expected_digest)
        return {"published_at": at.isoformat()}

    @app.post("/review/v0/policy/records/{object_id}/sign")
    async def policy_record_sign(object_id: uuid.UUID, body: RecordSignBody, request: Request):
        st = await state["review"].sign_record(request.state.staff, body.object_type, object_id, body.decision, body.expected_digest)
        return {"status": st}

    @app.get("/review/v0/records")
    async def records(subject: str):
        return {"items": await service.version_record(state["kge"], _subject(subject))}

    return app
