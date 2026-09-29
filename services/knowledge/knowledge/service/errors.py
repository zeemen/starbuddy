"""错误：对外一律转成契约的 Problem（plugin-api.v0.9.yaml#/components/schemas/Problem）。

detail 只写编码化说明，不带请求原文（node.resolve 的 text 可能来自学生）、数据库报错原文或堆栈。
无权访问与对象不存在返回同一个 404：title 与 code 相同，不带 detail（契约“错误与日志”）。
"""
from __future__ import annotations


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


def not_found(code: str = "CONTENT_VERSION_NOT_FOUND") -> ServiceError:
    return ServiceError(404, code, code)


def unauthenticated() -> ServiceError:
    # Problem 的 code 枚举没有鉴权专用值；与参考适配器一致用 INVALID_REQUEST（lessons 待人确认）
    return ServiceError(401, "INVALID_REQUEST", "missing or invalid service token")


def forbidden(title: str) -> ServiceError:
    return ServiceError(403, "INVALID_REQUEST", title)


def invalid(title: str, detail: str | None = None) -> ServiceError:
    return ServiceError(400, "INVALID_REQUEST", title, detail=detail)


def internal() -> ServiceError:
    return ServiceError(500, "INTERNAL", "internal error", retryable=True)
