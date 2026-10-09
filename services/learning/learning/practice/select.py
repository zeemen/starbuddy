"""再练选题（spec 第 2 节）：候选过滤、同族兜底、组装 generate_variation 请求、核对适配器的选题。纯函数，确定性。

1. 候选：读取层用原题调知识服务 item.similar（limit 20）；过滤后（第 2 步）同主节点、同层次、学生没做过的题目族少于
   variants_per_round 时再用 item.search(node_id, cognitive_level)（limit 50）补足。只留同主节点、同认知层次的题。
2. 排除：原题、学生在 exclude_window_days 内做过的题、版权受限（license_state = restricted）的题、平台表上未登记或未准入的题、
   不是验证器或确定性 Rubric 判分的题（按题面提供者登记的判分方式；首发只覆盖客观题）。
3. 偏好：不同题目族优先（“题目族”= 学生在这个单元做过的题所在的族，含原题；没有族的题按单题自成一族，同 KGE-04）；
   没有不同族的候选时才出同族题，条目标 same_family_only（做对能闭环，但 KGE-04 记 repeat_family 不计入掌握，待人确认 #5）。
4. 只把这一档候选交给适配器（item_family），适配器只能从里面选；返回的每道题都必须在这一档里、已登记、已准入，否则整轮不可用，不外露。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .queue import Unit

SIMILAR_LIMIT = 20                   # item.similar 的契约上限
SEARCH_LIMIT = 50                    # item.search 的契约上限（没有翻页）
FAMILY_LIMIT = 50                    # generate_variation.item_family 的契约上限
EXCLUDE_LIMIT = 200                  # exclude_item_refs 不设契约上限；这里封顶，防止请求无限长
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
CODE_RE = re.compile(r"^[a-z0-9_\-]{1,48}$")
# 不可用的原因：前三个由平台过滤得出，与契约 GenerateVariationResult.empty_reason 同名的照搬适配器的
NO_CANDIDATE = "no_candidate"                    # 同节点同层次没有任何候选
NO_ADMITTED = "no_admitted_candidate"            # 有候选，但都未准入或版权受限
ALL_EXCLUDED = "all_excluded"                    # 合格候选都做过（或只有原题）
ADAPTER_EMPTY = ("no_admitted_candidate", "all_excluded", "skill_node_unknown", "subject_not_supported")
ADAPTER_REJECTED = "adapter_result_rejected"     # 适配器选了候选集之外的题、未准入的题，或结果不合契约
ADAPTER_UNAVAILABLE = "adapter_unavailable"      # 适配器没有实现 generate_variation（501）或拒绝请求
NO_OBJECTIVE = "no_objective_candidate"          # 有已准入候选，但没有一道是验证器或确定性 Rubric 判分的客观题（审查 S7）
NO_SOURCE_ITEM = "no_source_item"                # hint_followup 找不到提示后做对的那道题：不调适配器（审查 S5）
NO_NEW_FAMILY = "no_new_family"                  # 复测与验证题（KGE-05）：合格候选都在学生做过的题目族里，不调适配器，不出同族题
EMPTY_REASONS = (NO_CANDIDATE, NO_ADMITTED, ALL_EXCLUDED, "skill_node_unknown", "subject_not_supported", ADAPTER_REJECTED,
                 ADAPTER_UNAVAILABLE, NO_OBJECTIVE, NO_SOURCE_ITEM, NO_NEW_FAMILY)
# 首发只覆盖客观题（spec 设计要点 0、待人确认 #7）：验证器判分或确定性 Rubric 判分；模型判分的主观题一律待复核，闭不了环
OBJECTIVE_GRADING = frozenset({"validator", "deterministic_rubric"})


@dataclass(frozen=True)
class Candidate:
    item_ref: str
    item_id: str
    main_node_id: str
    cognitive_level: str | None
    family_code: str | None
    license_state: str
    view: Mapping[str, Any]          # 知识服务原样的 ItemTagView（交给适配器的 item_family）

    @property
    def family_key(self) -> str:
        return family_key(self.item_id, self.family_code)


def family_key(item_id: str, family_code: str | None) -> str:
    return f"family:{family_code}" if family_code else f"item:{item_id}"


def from_view(subject: str, view: Mapping[str, Any]) -> Candidate | None:
    """知识服务的 ItemTagView → 候选；别的学科、引用格式不对的不要。"""
    ref = view.get("item_ref")
    if not isinstance(ref, str) or not ref.startswith(f"{subject}:"):
        return None
    local = ref.split(":", 1)[1]
    if not ITEM_ID_RE.fullmatch(local):
        return None
    return Candidate(ref, local, str(view.get("main_node_id")), view.get("cognitive_level"), view.get("family_code"),
                     str(view.get("license_state")), dict(view))


def merge(*groups: Iterable[Candidate]) -> list[Candidate]:
    """按出现先后合并去重（item.similar 在前，item.search 补在后）。"""
    seen: set[str] = set()
    out: list[Candidate] = []
    for g in groups:
        for c in g:
            if c.item_ref not in seen:
                seen.add(c.item_ref)
                out.append(c)
    return out


def in_unit(c: Candidate, unit: Unit) -> bool:
    return c.main_node_id == unit.node_id and c.cognitive_level == unit.cognitive_level


def needs_search(cands: Sequence[Candidate], unit: Unit, used_families: Iterable[str], want: int) -> bool:
    """（已过滤的）候选里同节点同层次、且学生没做过的题目族少于 want 个时，再用 item.search 补。"""
    used = set(used_families)
    fresh = {c.family_key for c in cands if in_unit(c, unit) and c.family_key not in used}
    return len(fresh) < want


@dataclass(frozen=True)
class Filtered:
    kept: tuple[Candidate, ...]
    empty_reason: str | None


def filter_candidates(cands: Sequence[Candidate], unit: Unit, *, source_item_id: str | None, exclude_item_ids: Iterable[str],
                      admission: Mapping[str, str], objective: Callable[[str], bool] | None = None) -> Filtered:
    """只留：同主节点同层次、不是原题、版权不受限、平台已登记且已准入、客观题、不在排除窗口里。
    admission：平台题目表的准入（item_id → 状态；不在里面 = 未登记）。
    objective：题目是否由验证器或确定性 Rubric 判分（按题面提供者登记的判分方式）；None = 不按判分方式过滤（列表页没有题面提供者时）。"""
    excluded = set(exclude_item_ids)
    same = [c for c in cands if in_unit(c, unit) and c.item_id != source_item_id]
    if not same:
        return Filtered((), NO_CANDIDATE if not any(in_unit(c, unit) for c in cands) else ALL_EXCLUDED)
    ok = [c for c in same if c.license_state != "restricted" and admission.get(c.item_id) == "admitted"]
    if not ok:
        return Filtered((), NO_ADMITTED)
    if objective is not None:
        ok = [c for c in ok if objective(c.item_id)]
        if not ok:
            return Filtered((), NO_OBJECTIVE)
    kept = [c for c in ok if c.item_id not in excluded]
    if not kept:
        return Filtered((), ALL_EXCLUDED)
    return Filtered(tuple(kept), None)


@dataclass(frozen=True)
class Tier:
    offered: tuple[Candidate, ...]       # 交给适配器的这一档（确定性顺序）
    same_family_only: bool


def tier(kept: Sequence[Candidate], used_families: Iterable[str], *, prefer_new_family: bool) -> Tier:
    """不同题目族优先：有就只给不同族的；没有才给同族的并标 same_family_only。prefer_new_family 关闭时全部给，按族的新旧排序。"""
    used = set(used_families)
    fresh = sorted((c for c in kept if c.family_key not in used), key=lambda c: c.item_id)
    stale = sorted((c for c in kept if c.family_key in used), key=lambda c: c.item_id)
    if prefer_new_family and fresh:
        return Tier(tuple(fresh[:FAMILY_LIMIT]), False)
    if not fresh:
        return Tier(tuple(stale[:FAMILY_LIMIT]), bool(stale))
    return Tier(tuple((fresh + stale)[:FAMILY_LIMIT]), False)


def variation_request(*, context: Mapping[str, Any], subject: str, source_item_id: str | None, unit: Unit, offered: Sequence[Candidate],
                      exclude_item_ids: Iterable[str], count: int, error_category: str | None) -> dict:
    """插件 API v1.0 GenerateVariationRequest（非消耗类：context 不带 usage_id）。
    没有原题（hint_followup 找不到提示后做对的那道题）时按 skill_node 请求：单元节点不是适配器的能力点，交给适配器按候选选。"""
    req: dict[str, Any] = {"context": dict(context), "count": count,
                           "item_family": [dict(c.view) for c in offered]}
    if source_item_id is not None:
        req["source_item_ref"] = f"{subject}:{source_item_id}"
    else:
        req["skill_node"] = f"{subject}:{unit.node_id}"
    # 原题固定放第一位再截断（审查 S9）：做过的题再多，原题也不会被截掉
    src = [f"{subject}:{source_item_id}"] if source_item_id else []
    refs = src + sorted({f"{subject}:{i}" for i in exclude_item_ids if ITEM_ID_RE.fullmatch(i)} - set(src))
    if refs:
        req["exclude_item_refs"] = refs[:EXCLUDE_LIMIT]
    if error_category is not None:
        req["subject_error_category"] = error_category
    return req


def check_picks(candidates: Sequence[Mapping[str, Any]], offered: Sequence[Candidate], admission: Mapping[str, str]) -> list[Candidate] | None:
    """适配器选出的题必须都在交给它的那一档里、已准入（结果自己说的与平台表都要是 admitted）、不重复。
    任一不满足返回 None（整轮不可用，不外露）。"""
    by_ref = {c.item_ref: c for c in offered}
    out: list[Candidate] = []
    for p in candidates:
        ref = p.get("item_ref") if isinstance(p, Mapping) else None
        c = by_ref.get(ref) if isinstance(ref, str) else None
        if c is None or p.get("admission") != "admitted" or admission.get(c.item_id) != "admitted" or c in out:
            return None
        out.append(c)
    return out


# ---------- 错因编码：只在存在、属于该科编码、且不属于敏感类时传给适配器 ----------
TAXONOMY = Path(__file__).resolve().parents[4] / "docs/mapping/error-taxonomy.v0.9.yaml"


@lru_cache(maxsize=1)
def taxonomy_codes() -> Mapping[str, Mapping[str, bool]]:
    """学科 → {编码: 是否敏感}（docs/mapping/error-taxonomy.v0.9.yaml，草稿待教研签字）。"""
    import yaml  # 只在选题时用到
    doc = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))
    out: dict[str, dict[str, bool]] = {}
    for subject, spec in (doc.get("subjects") or {}).items():
        codes: dict[str, bool] = {}
        for g in spec.get("groups") or []:
            for c in g.get("codes") or []:
                codes[str(c["code"])] = bool(c.get("sensitive", False))
        out[str(subject)] = codes
    return out


def error_category_for_request(subject: str, code: str | None, codes: Mapping[str, Mapping[str, bool]]) -> str | None:
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        return None
    sensitive = codes.get(subject, {}).get(code)
    return code if sensitive is False else None
