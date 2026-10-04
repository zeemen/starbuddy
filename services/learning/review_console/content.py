"""内容版本与考情方案的差异：纯函数，同一输入逐字节相同的输出（spec 第 4、7、8 节；第二部分 PR D）。

内容版本：输入是库函数 platform.content_version_snapshot 的快照（version、skill_nodes、items），基准是同前五段（地区、届别、年级、教材、学科）
上一个已发布过的版本；没有时全部按新增列出。能力点与题目的主键在平台上全局唯一（不带版本，spec“不做”：能力视图与题目主键加版本），
所以新版本的登记在差异里表现为新增，上一版本的登记表现为删除——这是如实列出，不是错配。

考情方案：输入是审核台后端从 policy 表读出的方案快照（scheme、subjects、batches、transitions、total），基准有两个：同城市同届别的
上一版本（已发布或已撤下），与同城市上一届的已发布方案（spec 第 8 节）。
每条差异附风险（risk.classify_content / classify_policy，判不出的一律按高风险）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from .diff import _keyed
from .risk import POLICY_SIGNERS, classify_content, classify_policy, review_level

Change = dict[str, Any]

CONTENT_ORDER = ("version", "skill_node", "item")
VERSION_FIELDS = ("region", "exam_year", "grade", "textbook_version", "subject", "content_version", "map_version_id")
SKILL_FIELDS = ("parent_local_id", "label_code")
ITEM_FIELDS = ("skill_local_id", "admission", "source_ref", "source_type", "license_status")


def _index(rows: list[dict[str, Any]], key: str, fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    return {str(r[key]): {f: r.get(f) for f in fields} for r in rows}


def _sorted(out: list[Change], order: tuple[str, ...]) -> list[Change]:
    rank = {o: i for i, o in enumerate(order)}
    out.sort(key=lambda c: (rank[c["object"]], c["key"], c["field"] or "", c["op"]))
    return out


def diff_content(base: dict[str, Any] | None, draft: dict[str, Any]) -> list[Change]:
    def meta(s: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if s is None:
            return {}
        return {"version": {f: s["version"].get(f) for f in VERSION_FIELDS}}

    out: list[Change] = []
    b = base or {"skill_nodes": [], "items": []}
    out += _keyed("version", meta(base), meta(draft), VERSION_FIELDS)
    out += _keyed("skill_node", _index(b["skill_nodes"], "local_id", SKILL_FIELDS), _index(draft["skill_nodes"], "local_id", SKILL_FIELDS),
                  SKILL_FIELDS)
    out += _keyed("item", _index(b["items"], "item_id", ITEM_FIELDS), _index(draft["items"], "item_id", ITEM_FIELDS), ITEM_FIELDS)
    _sorted(out, CONTENT_ORDER)
    for c in out:
        c["risk"] = classify_content(c).as_dict()
    return out


def content_summary(changes: list[Change]) -> dict[str, Any]:
    """内容版本一律高风险、一名 staff 签字（spec 第 1 节）；计数供界面显示。"""
    counts = {o: sum(1 for c in changes if c["object"] == o) for o in CONTENT_ORDER}
    return {"level": "high", "review_level": review_level("high"), "required_signers": 1, "counts": counts,
            "admission_changes": sum(1 for c in changes if c["object"] == "item" and c["field"] == "admission")}


POLICY_ORDER = ("scheme", "total", "subject", "batch", "transition")
SCHEME_FIELDS = ("version", "source_document_id")
SUBJECT_FIELDS = ("scored", "full_score", "proposition", "exam_form", "duration_minutes", "combined_group", "taken_in_grade", "exam_month",
                  "eligibility_effect")
BATCH_FIELDS = ("batch_order", "control_line_basis")
TRANSITION_FIELDS = ("replaces_scheme_id",)


def _num(v: Any) -> Any:
    """numeric 统一成字符串（Decimal 与 float 的写法不同会造成假差异），整数值去掉“.0”。"""
    if isinstance(v, (Decimal, float)) and not isinstance(v, bool):
        s = format(Decimal(str(v)) if isinstance(v, float) else v, "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    if isinstance(v, UUID):
        return str(v)
    return v


def diff_scheme(base: dict[str, Any] | None, draft: dict[str, Any]) -> list[Change]:
    """考情方案差异：科目（是否计分、满分、命题、形式、时长、合场、年级、月份、资格影响）、计分总分、批次、过渡规则、版本与来源。"""
    def parts(s: dict[str, Any] | None) -> dict[str, dict[str, dict[str, Any]]]:
        if s is None:
            return {"scheme": {}, "total": {}, "subject": {}, "batch": {}, "transition": {}}
        return {
            "scheme": {"scheme": {f: _num(s["scheme"].get(f)) for f in SCHEME_FIELDS}},
            "total": {"total": {"total": _num(s.get("total"))}},
            "subject": {str(r["subject"]): {f: _num(r.get(f)) for f in SUBJECT_FIELDS} for r in s["subjects"]},
            "batch": {f"{r['batch_code']}|{r['track']}": {f: _num(r.get(f)) for f in BATCH_FIELDS} for r in s["batches"]},
            "transition": {str(r["change_kind"]): {f: _num(r.get(f)) for f in TRANSITION_FIELDS} for r in s["transitions"]},
        }

    a, b = parts(base), parts(draft)
    out: list[Change] = []
    out += _keyed("scheme", a["scheme"], b["scheme"], SCHEME_FIELDS)
    out += _keyed("total", a["total"], b["total"], ("total",))
    out += _keyed("subject", a["subject"], b["subject"], SUBJECT_FIELDS)
    out += _keyed("batch", a["batch"], b["batch"], BATCH_FIELDS)
    out += _keyed("transition", a["transition"], b["transition"], TRANSITION_FIELDS)
    _sorted(out, POLICY_ORDER)
    for c in out:
        c["risk"] = classify_policy(c).as_dict()
    return out


def policy_summary(changes: list[Change]) -> dict[str, Any]:
    """考情一律高风险、两名不同 staff（spec 第 1、2、8 节）。"""
    return {"level": "high", "review_level": "high", "required_signers": POLICY_SIGNERS,
            "counts": {o: sum(1 for c in changes if c["object"] == o) for o in POLICY_ORDER}}
