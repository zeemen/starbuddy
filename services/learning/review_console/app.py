"""审核台 v0 的 HTTP 接口（只供审核台前端，spec 冲刺 1 · PR 2）。

身份：每个请求由注入的 StaffAuthenticator 给出当前 staff 账号。staff 登录是 PR 4（[NEEDS-HUMAN]，spec 待人确认 #3），
在那之前默认的 DenyAll 拒绝一切请求；测试注入合成 staff。本模块不接受请求里自报的账号。
接口只返回公共层内容与 staff 账号 ID；日志只记方法、路径模板、状态码与错误码。
"""
from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal, Protocol

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import service
from .config import ReviewSettings
from .errors import ReviewError
from .store import KgeStore, ReviewStore, check_roles, db_error

log = logging.getLogger("starbuddy.review_console")
SUBJECTS = ("wenli", "aya", "zhixing", "echo")
DIGEST = r"^[0-9a-f]{64}$"
BASIS = r"^[a-z0-9_]{1,48}$"


class StaffAuthenticator(Protocol):
    async def __call__(self, request: Request) -> uuid.UUID | None: ...


async def deny_all(request: Request) -> uuid.UUID | None:
    """PR 4 之前的默认：没有 staff 登录，任何请求都拒绝。"""
    return None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignBody(_Strict):
    decision: Literal["approved", "rejected"]
    basis_code: str | None = Field(default=None, pattern=BASIS)
    expected_digest: str = Field(pattern=DIGEST)


class ChangeSignBody(_Strict):
    decision: Literal["approved", "rejected"]


class PublishBody(_Strict):
    expected_digest: str = Field(pattern=DIGEST)


class RetireBody(_Strict):
    basis_code: str = Field(pattern=BASIS)


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
        return d


NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")  # 与 kge.node_id 域一致


def create_app(settings: ReviewSettings | None = None, authenticate: StaffAuthenticator = deny_all,
               pools: tuple[asyncpg.Pool, asyncpg.Pool] | None = None) -> FastAPI:
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if pools is not None:
            kp, rp, own = pools[0], pools[1], False
        else:
            s = settings or ReviewSettings.from_env()
            kp = await asyncpg.create_pool(s.kge_database_url, min_size=1, max_size=5)
            rp = await asyncpg.create_pool(s.review_database_url, min_size=1, max_size=3)
            own = True
        await check_roles(kp, rp)
        state["kge"], state["review"] = KgeStore(kp), ReviewStore(rp)
        yield
        if own:
            await kp.close()
            await rp.close()

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        status, code = 500, "INTERNAL"
        try:
            staff = await authenticate(request)
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

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, e: RequestValidationError):
        # 不回显请求内容
        return JSONResponse(ReviewError(422, "INVALID_PARAMETER").problem(), status_code=422, media_type="application/problem+json")

    def _subject(subject: str) -> str:
        if subject not in SUBJECTS:
            raise ReviewError(422, "INVALID_PARAMETER", "未知学科")
        return subject

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
        return {"status": await state["review"].sign_change(request.state.staff, change_id, body.decision)}

    @app.post("/review/v0/map-versions/{version_id}/publish")
    async def publish(version_id: uuid.UUID, body: PublishBody, request: Request):
        at = await state["review"].publish(request.state.staff, version_id, body.expected_digest)
        return {"published_at": at.isoformat()}

    @app.post("/review/v0/map-versions/{version_id}/retire")
    async def retire(version_id: uuid.UUID, body: RetireBody, request: Request):
        await state["review"].retire(request.state.staff, version_id, body.basis_code)
        return {"status": "retired"}

    @app.get("/review/v0/records")
    async def records(subject: str):
        from .snapshot import norm
        return {"items": norm(await state["kge"].records(_subject(subject)))}

    return app
