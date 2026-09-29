"""审核台接口的错误：application/problem+json，只含错误码与（规则类）库里写好的中文说明。"""
from __future__ import annotations


class ReviewError(Exception):
    def __init__(self, status: int, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.status, self.code, self.detail = status, code, detail

    def problem(self) -> dict:
        d = {"type": f"urn:starbuddy:review:{self.code.lower()}", "status": self.status, "code": self.code}
        if self.detail:
            d["detail"] = self.detail
        return d
