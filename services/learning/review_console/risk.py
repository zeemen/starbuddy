"""风险分类：纯函数，输入一条差异，输出风险级别、理由编码、是否结构性（spec 设计要点 2，KGE V1.1 第四章分级发布闸门表）。

判不出的一律按高风险（spec：分级由规则判定，不由提交方自报）。理由编码只用小写英文，界面据此显示中文说明。
结构性变更（有效节点的增删、停用、恢复；有效前置边与从属边的增删、停用、恢复）须两名不同 staff 签字、至少升次版本号。
逐条标记与 kge.structural_diff 同口径（只看有效的一侧，契约审查 S1）；整个版本是否结构性、要几人签，以库的 kge.structural_diff
与未驳回的结构性变更单为准（service.diff_view），本模块的 summarize 只在没有库时用于单测。
阶段 0 没有语义体检，中风险按高风险审（spec 待人确认 #7 默认）：条目仍显示 medium，review_level 为 high。
冲刺 4 补全（第二部分 PR D）：编译页面（classify_page）、原子（classify_atom）、考情方案（classify_policy）、内容版本（classify_content）；
同样是纯函数，判不出的一律按高风险。
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
    if obj == "version":
        return Risk("high", "curriculum_standard")  # 课标年份
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


def _max(risks: list[Risk]) -> Risk:
    """取最高级；同级取第一条（输入顺序固定，结果确定）；空列表判不出，按高风险。"""
    if not risks:
        return UNKNOWN
    top = max(LEVELS.index(r.level) for r in risks)
    return next(r for r in risks if LEVELS.index(r.level) == top)


# ---------- 编译页面（冲刺 4 补全；KGE 第四章分级发布闸门表、spec 第 2 节、第 10 节） ----------
# 知识点页的段落与类型（与 services/knowledge/knowledge/pages/format.py 的 KP_SECTIONS 相同，学习服务测试核对）
PAGE_SECTIONS = {"definition": "locked", "curriculum": "locked", "relations": "render", "textbook": "render", "region": "render",
                 "key_points": "narrative", "pitfalls": "narrative", "frequent_questions": "narrative"}
PAGE_SOURCES = ("compiler", "human", "model_draft")
PAGE_REBUILDS = ("render", "full")


def classify_page(change: dict[str, Any]) -> Risk:
    """一次页面改动的风险。输入（全部由编译器或合并请求给出，不由提交方自报风险）：
      sections：变化的段落 ID 列表；prerequisite_changed：前置与后续段里“前置”部分是否变化（缺省视为变了）；
      projection_quote_changed：所引教材投影原句是否变化；atom_hash_only：只有所引原子的哈希更新；
      rebuild_kind：render | full；source：compiler | human | model_draft。
    规则：全量重编、锁定段（定义与课标要求）、前置关系表述、教材投影（原句与页码）高；叙述段（讲解要点、易错纠正、高频设问）改写中；
    地区权重（页面里的考情统计）与其他渲染段、只更新原子哈希低；模型草稿不能是低风险（spec 第 10 节），至少中；
    段落、来源、重建类型认不出，或什么都没变，一律高；来源与重建类型都没给（输入不是编译器给的完整描述）也按高（判不出从严）。"""
    if "source" not in change and "rebuild_kind" not in change:
        return UNKNOWN
    kind = change.get("rebuild_kind", "render")
    source = change.get("source", "compiler")
    sections = change.get("sections") or []
    if kind not in PAGE_REBUILDS or source not in PAGE_SOURCES or not isinstance(sections, list):
        return UNKNOWN
    if kind == "full":
        return Risk("high", "full_rebuild")
    risks: list[Risk] = []
    for s in sections:
        sk = PAGE_SECTIONS.get(s)
        if sk is None:
            return UNKNOWN
        if sk == "locked":
            risks.append(Risk("high", "definition_curriculum"))
        elif s == "relations":
            risks.append(Risk("high", "prerequisite_statement") if change.get("prerequisite_changed", True) is not False
                         else Risk("low", "render_segment"))
        elif s == "textbook":
            risks.append(Risk("high", "textbook_quote_page"))
        elif sk == "narrative":
            risks.append(Risk("medium", "narrative_rewrite"))
        elif s == "region":
            risks.append(Risk("low", "exam_stats_update"))
        else:
            risks.append(Risk("low", "render_segment"))
    if change.get("projection_quote_changed"):
        risks.append(Risk("high", "textbook_quote_page"))
    if not risks:
        if change.get("atom_hash_only") is True:
            risks.append(Risk("low", "atom_hash_only"))
        else:
            return UNKNOWN
    top = _max(risks)
    if source == "model_draft" and top.level == "low":
        return Risk("medium", "model_draft")
    return top


# ---------- 原子（KGE 第四章：采分点高、题目族引用低、时政中；题目标签不在表里，按高） ----------
ATOM_RISK = {"scoring_point": Risk("high", "scoring_basis"), "item_family": Risk("low", "item_family_ref"),
             "current_affair": Risk("medium", "current_affair"), "item_tag": Risk("high", "item_tag")}


def classify_atom(atom_type: str) -> Risk:
    """已发布地图版本上单独送审的原子：按类型定级；认不出的类型一律高。"""
    return ATOM_RISK.get(atom_type, UNKNOWN)


# ---------- 考情政策（spec 第 1、2、8 节：一律高风险、两名不同 staff；理由按改动类别给出） ----------
POLICY_REASONS = {
    ("scheme", "source_document_id"): "policy_source", ("scheme", "version"): "policy_version",
    ("subject", "scored"): "policy_scoring", ("subject", "full_score"): "policy_full_score",
    ("subject", "combined_group"): "policy_combined", ("subject", "exam_form"): "policy_exam_form",
    ("subject", "proposition"): "policy_exam_form", ("subject", "duration_minutes"): "policy_exam_form",
    ("subject", "taken_in_grade"): "policy_schedule", ("subject", "exam_month"): "policy_schedule",
    ("subject", "eligibility_effect"): "policy_scoring",
    ("batch", "batch_order"): "policy_batches", ("batch", "control_line_basis"): "policy_batches",
    ("transition", "replaces_scheme_id"): "policy_transition", ("total", "total"): "policy_full_score",
}
POLICY_EXISTENCE = {"subject": "policy_subjects", "batch": "policy_batches", "transition": "policy_transition"}
POLICY_SIGNERS = 2  # 与 policy.signers_required() 相等（库为准；学习服务测试核对迁移 0041 的常量）


def classify_policy(change: dict[str, Any]) -> Risk:
    """考情方案差异的一条：一律高风险（两人），理由按对象与字段；认不出的按 unclassified。"""
    obj, op, fld = change.get("object"), change.get("op"), change.get("field")
    if op in ("added", "removed") and obj in POLICY_EXISTENCE:
        return Risk("high", POLICY_EXISTENCE[obj])
    if op == "changed" and (obj, fld) in POLICY_REASONS:
        return Risk("high", POLICY_REASONS[(obj, fld)])
    return UNKNOWN


# ---------- 内容版本（spec 第 1、7 节：一律高风险、一名 staff；准入变化单独标出） ----------
CONTENT_VERSION_FIELDS = ("region", "exam_year", "grade", "textbook_version", "subject", "content_version")


def classify_content(change: dict[str, Any]) -> Risk:
    obj, op, fld = change.get("object"), change.get("op"), change.get("field")
    if obj == "version":
        if op == "added" or (op == "changed" and fld in CONTENT_VERSION_FIELDS):
            return Risk("high", "content_six_segment")
        if op == "changed" and fld == "map_version_id":
            return Risk("high", "content_map_version")
        return UNKNOWN
    if obj == "skill_node" and op in ("added", "removed", "changed"):
        return Risk("high", "skill_view_registry")
    if obj == "item":
        if op == "changed" and fld == "admission":
            return Risk("high", "admission_change")
        if op == "changed" and fld in ("source_ref", "source_type", "license_status"):
            return Risk("high", "item_source")
        if op in ("added", "removed") or (op == "changed" and fld == "skill_local_id"):
            return Risk("high", "item_registry")
    return UNKNOWN


STRUCTURAL_CHANGE_TYPES = ("node_add", "node_split", "node_merge", "node_retire", "edge_add", "edge_remove")


def classify_change_type(change_type: str) -> Risk:
    """变更单按类型分级：结构性的须两人（与 kge.map_change_required_signers 一致），其余单人，按高风险审。"""
    if change_type in STRUCTURAL_CHANGE_TYPES:
        return Risk("high", f"change_{change_type}", structural=True)
    return Risk("high", f"change_{change_type}" if change_type in ("node_revise", "projection_fix", "other") else "unclassified")
