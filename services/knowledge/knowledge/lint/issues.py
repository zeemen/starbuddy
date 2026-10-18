"""体检结果（spec 设计要点 4 末条）：字段对应 KGE 第七章 lint_issue(id, page_id, page_version, layer, check_type, severity,
detail jsonb, status)。试点期只存于 CI 产物（ADR 12）。

severity：block = 阻断（不能合并），warn = 警告。layer：program（程序体检，KGE-03）或 semantic（规则式语义体检，KGE-11，
knowledge.semantic）；两层共用同一报告格式与检查类型枚举（代码内枚举，不是契约）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

REPORT_FORMAT = "kge-lint-report/1.1"
# 读取方（m1 等）接受的格式：/1 没有顶层 layer 等绑定字段（只有程序体检）
READ_FORMATS = ("kge-lint-report/1", "kge-lint-report/1.1")
SEVERITIES = ("block", "warn")
LAYERS = ("program", "semantic")
PROGRAM_CHECK_TYPES = (
    "format", "header", "node_id", "sections", "locked_baseline", "template", "link", "orphan", "citation", "atom_hash",
    "atom_expired", "latex", "sympy", "pii", "group_stats", "persona", "forbidden_phrase", "sentence_length", "textbook_quote",
    "snapshot",
)
# 语义层（KGE-11，knowledge.semantic）：与原子冲突、跨页前置方向、相邻页术语写法、版次专属表述、超纲、适龄文风、角色语言混入
SEMANTIC_CHECK_TYPES = (
    "atom_conflict", "prerequisite_direction", "term_consistency", "edition_scope", "out_of_scope", "age_style", "persona_mix",
    "affected_scope",  # 比对范围本身的问题（受影响页不在页面目录里）
)
CHECK_TYPES = PROGRAM_CHECK_TYPES + SEMANTIC_CHECK_TYPES
CHECK_TYPES_BY_LAYER = {"program": PROGRAM_CHECK_TYPES, "semantic": SEMANTIC_CHECK_TYPES}


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
    layer: str = "program"

    def __post_init__(self) -> None:
        if self.check_type not in CHECK_TYPES or self.severity not in SEVERITIES:
            raise ValueError(f"未登记的检查类型或严重级别：{self.check_type}/{self.severity}")
        if self.check_type not in CHECK_TYPES_BY_LAYER.get(self.layer, ()):
            raise ValueError(f"检查类型 {self.check_type} 不属于体检层 {self.layer}")

    @property
    def location(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "section": self.section}

    def to_dict(self) -> dict[str, Any]:
        body = {"page_id": self.page_id, "page_version": self.page_version, "layer": self.layer, "check_type": self.check_type,
                "severity": self.severity, "detail": {"message": self.message, "location": self.location, **self.detail}}
        digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return {"id": f"li-{digest}", **body, "status": "open"}


@dataclass(frozen=True)
class LintReport:
    """报告（kge-lint-report/1.1）：在 /1 的 summary、issues 之外，顶层加绑定字段——
    layer（program | semantic）、map_version（所依据的地图版本；没给快照为 null）、
    scope（检查的页面：page_id、page_version、页面原文 sha256；语义层另有 affected 与 pairs）、
    inputs（快照、词表等输入的摘要）、config（分级是否缺省及覆盖项、词表的 synthetic 标记、被跳过的检查）。
    条目的 id 与 /1 相同；summary 字段不变（页面仓库 CI 只读 summary）。"""
    issues: tuple[LintIssue, ...]
    pages: int
    layer: str = "program"
    map_version: str | None = None
    scope: Mapping[str, Any] = field(default_factory=dict)
    inputs: Mapping[str, Any] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ValueError(f"未登记的体检层：{self.layer}")
        if any(i.layer != self.layer for i in self.issues):
            raise ValueError("报告里有不属于本层的条目")

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
        return {"format": REPORT_FORMAT, "layer": self.layer, "map_version": self.map_version, "scope": dict(self.scope),
                "inputs": dict(self.inputs), "config": dict(self.config),
                "summary": {"pages": self.pages, "blocks": len(self.blocks), "warnings": len(self.issues) - len(self.blocks),
                            "by_check": dict(sorted(by.items()))},
                "issues": [i.to_dict() for i in self.issues]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
