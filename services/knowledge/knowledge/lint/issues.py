"""体检结果（spec 设计要点 4 末条）：字段对应 KGE 第七章 lint_issue(id, page_id, page_version, layer, check_type, severity,
detail jsonb, status)。试点期只存于 CI 产物（ADR 12）。

severity：block = 阻断（不能合并），warn = 警告。layer 固定为 program（程序体检；语义体检不在本任务）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

REPORT_FORMAT = "kge-lint-report/1"
SEVERITIES = ("block", "warn")
CHECK_TYPES = (
    "format", "header", "node_id", "sections", "locked_baseline", "template", "link", "orphan", "citation", "atom_hash",
    "atom_expired", "latex", "sympy", "pii", "group_stats", "persona", "forbidden_phrase", "sentence_length", "textbook_quote",
    "snapshot",
)


@dataclass(frozen=True)
class LintIssue:
    check_type: str
    severity: str
    page_id: str
    page_version: str
    file: str
    message: str
    line: int | None = None
    section: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.check_type not in CHECK_TYPES or self.severity not in SEVERITIES:
            raise ValueError(f"未登记的检查类型或严重级别：{self.check_type}/{self.severity}")

    @property
    def location(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "section": self.section}

    def to_dict(self) -> dict[str, Any]:
        body = {"page_id": self.page_id, "page_version": self.page_version, "layer": "program", "check_type": self.check_type,
                "severity": self.severity, "detail": {"message": self.message, "location": self.location, **self.detail}}
        digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return {"id": f"li-{digest}", **body, "status": "open"}


@dataclass(frozen=True)
class LintReport:
    issues: tuple[LintIssue, ...]
    pages: int

    @property
    def blocks(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "block"]

    @property
    def blocked(self) -> bool:
        return bool(self.blocks)

    def for_page(self, page_id: str) -> list[LintIssue]:
        return [i for i in self.issues if i.page_id == page_id]

    def to_dict(self) -> dict[str, Any]:
        by: dict[str, int] = {}
        for i in self.issues:
            by[f"{i.check_type}/{i.severity}"] = by.get(f"{i.check_type}/{i.severity}", 0) + 1
        return {"format": REPORT_FORMAT, "summary": {"pages": self.pages, "blocks": len(self.blocks),
                                                     "warnings": len(self.issues) - len(self.blocks), "by_check": dict(sorted(by.items()))},
                "issues": [i.to_dict() for i in self.issues]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
