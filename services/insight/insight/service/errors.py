"""错误：对外一律转成契约的 Problem（plugin-api.v0.9.yaml#/components/schemas/Problem）。

detail 只写编码化说明，不带请求原文、数据库报错原文或堆栈；永远不出现 student_ref、claim_id 等标识（契约“错误与日志”）。
无权访问与对象不存在返回同一个 404：title 与 code 相同，不带 detail。Problem 的 code 枚举没有“判断不存在”，
沿用知识服务的 CONTENT_VERSION_NOT_FOUND（lessons：契约缺口登记 v1.0 评审）。
"""
from __future__ import annotations

NOT_FOUND_CODE = "CONTENT_VERSION_NOT_FOUND"


class ServiceError(Exception):
    def __init__(self, status: int, code: str, title: str, *, detail: str | None = None, retryable: bool = False) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.retryable = retryable

    def problem(self, request_id: str | None = None) -> dict:
        body: dict = {"type": f"/problems/{self.code.lower().replace('_', '-')}", "title": self.title, "status": self.status,
                      "code": self.code, "retryable": self.retryable}
        if self.detail:
            body["detail"] = self.detail[:500]
        if request_id:
            body["request_id"] = request_id
        return body


def not_found() -> ServiceError:
    return ServiceError(404, NOT_FOUND_CODE, NOT_FOUND_CODE)


def unauthenticated() -> ServiceError:
    return ServiceError(401, "INVALID_REQUEST", "missing or invalid service token")


def forbidden(title: str) -> ServiceError:
    return ServiceError(403, "INVALID_REQUEST", title)


def invalid(title: str, detail: str | None = None) -> ServiceError:
    return ServiceError(400, "INVALID_REQUEST", title, detail=detail)


def not_implemented(title: str) -> ServiceError:
    return ServiceError(501, "METHOD_NOT_IMPLEMENTED", title)


def internal() -> ServiceError:
    return ServiceError(500, "INTERNAL", "internal error", retryable=True)
