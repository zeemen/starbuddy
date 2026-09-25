"""错误类型。所有错误对外都转成 Problem（见 packages/contracts/openapi/learning-internal.v0.yaml）。

detail 只写给开发者看的编码化说明，不得包含学生作答、题目答案或数据库报错原文
（数据库的约束报错会带出整行数据，见 store.py）。
"""
from __future__ import annotations


class IngestError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        *,
        detail: str | None = None,
        retryable: bool = False,
        upstream_code: str | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.retryable = retryable
        self.upstream_code = upstream_code

    def problem(self, request_id: str | None = None) -> dict:
        body: dict = {
            "type": f"/problems/{self.code.lower()}",
            "title": self.title,
            "status": self.status,
            "code": self.code,
            "retryable": self.retryable,
        }
        if self.detail:
            body["detail"] = self.detail[:500]
        if self.upstream_code:
            body["upstream_code"] = self.upstream_code
        if request_id:
            body["request_id"] = request_id
        return body


def rejected(reason: str) -> IngestError:
    """适配器结果没通过平台核对（spec 范围第 4 条）：整体拒绝，一行不写。"""
    return IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter result rejected by platform checks", detail=reason)
