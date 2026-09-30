"""insight.export / insight.erase 的服务端编排（spec 设计要点 4；迁移 0035；契约 v0.9）。

0035 把导出与删除申请 / 确认 / 撤回的库函数（platform.export_student_data、request_erasure、confirm_erasure、cancel_erasure）
只授给 starbuddy_bff，并按 BFF 会话的当前账号（app.account_id）核对“有效绑定家长”；洞察服务以 starbuddy_insight 连接，
既没有这些函数的执行权限，也不能代表家长会话（不能继承其他应用角色，store.assert_insight_role）。所以本模块只做：
1. 接口 GuardianRequests：契约工具经它调用库函数；缺省实现 UnwiredGuardianRequests 一律不通（工具回 501，fail closed），
   接线方式（BFF 直接调库函数、或给洞察服务授权）由人决定（lessons 待人确认）；
2. 库函数返回形状 → 契约结果形状的映射（有库上测试：测试里用 BFF 角色调真实的 0035 函数，再经这里映射、过契约校验）：
   - 导出：库里同步生成、直接 ready，一次性返回 download_ref；导出内容（topics）不进契约响应，由实现方交给下载存储；
   - 删除：契约是一次调用 + 一次性确认码（8 位、10 分钟、错 5 次锁定）；库里是“发起 → 发起人 24 小时内确认 → 冷静期 7 天可撤回
     → 定时任务执行”。映射：确认码由实现方校验通过后依次 request + confirm，回执 ID = 删除请求 ID，status = scheduled；
     执行后 status = completed、scopes 取库里回执；执行前 scopes 取配置（与 0035 清单的 erase_scope 一致，有库上测试）。
     撤回（cancel_erasure）契约里没有工具，经 BFF（lessons 契约差异）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from .sie03_policy import ERASE_SCOPES

EXPORT_FORMAT = "starbuddy-export@1"


class NotWired(RuntimeError):
    """导出与删除的库函数没有接到洞察服务（缺省）"""


@dataclass(frozen=True)
class ExportTicket:
    job_id: str
    status: str                      # queued / running / ready / failed
    download_ref: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class EraseTicket:
    request_id: str
    status: str                      # scheduled / completed
    scopes: tuple[str, ...]


class GuardianRequests(Protocol):
    async def export(self, actor_ref: str, link_id: str, student: str) -> ExportTicket: ...

    async def erase(self, actor_ref: str, link_id: str, student: str, confirmation_code: str) -> EraseTicket: ...


class UnwiredGuardianRequests:
    async def export(self, actor_ref: str, link_id: str, student: str) -> ExportTicket:  # noqa: ARG002
        raise NotWired("export library function is granted to the BFF session only")

    async def erase(self, actor_ref: str, link_id: str, student: str, confirmation_code: str) -> EraseTicket:  # noqa: ARG002
        raise NotWired("erasure library functions are granted to the BFF session only")


def ticket_from_export(pkg: Mapping[str, Any], student: str) -> ExportTicket:
    """platform.export_student_data 的返回 → 导出任务（丢掉 topics：导出内容不进契约响应）"""
    if pkg.get("format") != EXPORT_FORMAT or str(pkg.get("student_id")) != student:
        raise ValueError("unexpected export package")
    expires = pkg.get("expires_at")
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires)
    return ExportTicket(str(pkg["export_job_id"]), str(pkg["status"]), pkg.get("download_ref"), expires)


def ticket_from_erasure(request: Mapping[str, Any], planned_scopes: Sequence[str]) -> EraseTicket:
    """platform.erasure_request 的一行 → 删除回执：已执行 → completed（scopes 取库里回执）；待执行 → scheduled（scopes 取配置）"""
    status = request["status"]
    if status == "executed":
        scopes = tuple(s for s in ERASE_SCOPES if s in (request.get("scopes") or ()))
        return EraseTicket(str(request["id"]), "completed", scopes)
    if status == "pending" and request.get("confirmed_at") is not None:
        return EraseTicket(str(request["id"]), "scheduled", tuple(s for s in ERASE_SCOPES if s in planned_scopes))
    raise ValueError("erasure request is not confirmed or was cancelled")


def export_result(t: ExportTicket) -> dict[str, Any]:
    out: dict[str, Any] = {"export_job_id": t.job_id, "status": t.status}
    if t.download_ref is not None:
        out["download_ref"] = t.download_ref
    if t.expires_at is not None:
        out["expires_at"] = t.expires_at.isoformat()
    return out


def erase_result(t: EraseTicket) -> dict[str, Any]:
    return {"erase_receipt_id": t.request_id, "status": t.status, "scopes": list(t.scopes)}
