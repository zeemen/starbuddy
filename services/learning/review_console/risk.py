"""风险分类：纯函数，输入一条差异，输出风险级别、理由编码、是否结构性（spec 设计要点 2，KGE V1.1 第四章分级发布闸门表）。

判不出的一律按高风险（spec：分级由规则判定，不由提交方自报）。理由编码只用小写英文，界面据此显示中文说明。
结构性变更（有效节点的增删、停用、恢复；有效前置边与从属边的增删、停用、恢复）须两名不同 staff 签字、至少升次版本号。
逐条标记与 kge.structural_diff 同口径（只看有效的一侧，契约审查 S1）；整个版本是否结构性、要几人签，以库的 kge.structural_diff
与未驳回的结构性变更单为准（service.diff_view），本模块的 summarize 只在没有库时用于单测。
阶段 0 没有语义体检，中风险按高风险审（spec 待人确认 #7 默认）：条目仍显示 medium，review_level 为 high。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LEVELS = ("low", "medium", "high")
STRUCTURAL_EDGE_TYPES = ("prerequisite", "part_of")
BOUNDARY_FIELDS = ("includes", "excludes", "examples")
EXISTENCE_OPS = ("added", "removed", "deprecated", "reactivated")


@dataclass(frozen=True)
class Risk:
    level: str
    reason: str
    structural: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "review_level": review_level(self.level), "reason": self.reason, "structural": self.structural}


UNKNOWN = Risk("high", "unclassified")


def _edge_type(key: str) -> str | None:
    # 键形如 `A -prerequisite-> B`
    parts = key.split(" ")
    if len(parts) == 3 and parts[1].startswith("-") and parts[1].endswith("->"):
        return parts[1][1:-2]
    return None


def _touches_active(change: dict[str, Any]) -> bool:
    """新增或删除的一侧是有效的（本来就停用的行被删、或新增一条停用行，不算结构性，与 kge.structural_diff 一致）。"""
    op = change.get("op")
    if op in ("deprecated", "reactivated"):
        return True
    side = change.get("new") if op == "added" else change.get("old")
    return isinstance(side, dict) and side.get("status") == "active"


def review_level(level: str) -> str:
    return "high" if level == "medium" else level


def classify(change: dict[str, Any]) -> Risk:
    obj, op, fld = change.get("object"), change.get("op"), change.get("field")
    if obj == "node":
        if op in EXISTENCE_OPS:
            return Risk("high", f"node_{op}", structural=_touches_active(change))
        if op == "changed" and fld in BOUNDARY_FIELDS:
            return Risk("medium", "boundary_supplement")
        if op == "changed" and fld == "cognitive_levels":
            return Risk("high", "curriculum_requirement")
        if op == "changed" and fld in ("name", "kind", "level", "parent_id", "scope", "error_type"):
            # 名称改动分不出是错别字还是定义变化，按定义处理
            return Risk("high", "definition")
        return UNKNOWN
    if obj == "edge":
        et = _edge_type(str(change.get("key", "")))
        if et is None:
            return UNKNOWN
        if et in STRUCTURAL_EDGE_TYPES and op in EXISTENCE_OPS:
            return Risk("high", "prerequisite_structure", structural=_touches_active(change))
        if et in STRUCTURAL_EDGE_TYPES and op == "changed" and fld in ("strength", "evidence_ref"):
            return Risk("high", "prerequisite_statement")
        if op in EXISTENCE_OPS or (op == "changed" and fld in ("strength", "evidence_ref")):
            return Risk("medium", "weak_edge")
        return UNKNOWN
    if obj == "textbook":
        # 教材投影中的原句与页码、核对状态都是高风险
        return Risk("high", "textbook_quote_page")
    if obj == "scoring_point":
        return Risk("high", "scoring_basis")
    if obj == "id_migration":
        if op == "added" and isinstance(change.get("new"), dict) and change["new"].get("rule") == "retire":
            return Risk("high", "id_retire_retest")  # 对应掌握度标为待复测（KGE 第五章）
        return Risk("high", "id_migration")
    if obj == "item_family":
        if op == "added":
            return Risk("low", "item_family_ref")
        return UNKNOWN
    if obj == "item_tag":
        if op == "changed" and fld == "family":
            return Risk("low", "item_family_ref")
        return UNKNOWN
    if obj == "current_affair":
        return Risk("medium", "current_affair")
    return UNKNOWN


def summarize(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """整个版本的风险：取最高级；有结构性变更时须两人签字。"""
    risks = [classify(c) for c in changes]
    level = max((r.level for r in risks), key=LEVELS.index, default="low")
    structural = any(r.structural for r in risks)
    return {"level": level, "review_level": review_level(level), "structural": structural, "required_signers": 2 if structural else 1,
            "counts": {lv: sum(1 for r in risks if r.level == lv) for lv in LEVELS}}


STRUCTURAL_CHANGE_TYPES = ("node_add", "node_split", "node_merge", "node_retire", "edge_add", "edge_remove")


def classify_change_type(change_type: str) -> Risk:
    """变更单按类型分级：结构性的须两人（与 kge.map_change_required_signers 一致），其余单人，按高风险审。"""
    if change_type in STRUCTURAL_CHANGE_TYPES:
        return Risk("high", f"change_{change_type}", structural=True)
    return Risk("high", f"change_{change_type}" if change_type in ("node_revise", "projection_fix", "other") else "unclassified")
