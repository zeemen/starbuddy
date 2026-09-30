"""首份画像报告的结构化生成（spec 设计要点 4）：纯函数，不连库、不调模型。

报告 = 顶部提示语占位 + 标题占位 + 按类别分节（固定衔接短语占位 + 判断渲染句）。每一句都带判断 ID；
标题、提示语、衔接短语是固定文字（人写人审，必停项），不是关于学生的陈述。
- 只收：对象类型 node、类别为“知识掌握 / 知识迁移”、状态为假设或成立（规则 11：待验证不作陈述）、可见范围含该受众、
  未到期、学科在策略 visible_subjects 里的判断；中圈指标（metric）永远不进报告；
- 学生版：假设显示为“初步判断”（label = preliminary），不带置信度；家长版：假设同样标注，并附置信度说明（confidence_band 编码）；
- 渲染后跑两道检查：陈述—判断一致性（复用 KGE-04 规则引擎 learning.mastery.consistency.check_page：衔接短语白名单、
  程度不升级、不新增结论、衔接语无数字、无人设语言、全文禁用推断）与禁用推断词表（提示语与标题另查一次）。任一不过不出报告，
  返回阻断编码（不降级为半份报告）。
- 同样的判断、同样的配置，输出逐字节相同（to_json）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .policy import NODE_CATEGORIES, Policy
from .render import ClaimForRender, render_text
from .shared import COGNITIVE_LEVELS, Forbidden, consistency

FORMAT = "sie-first-report/0.1"
AUDIENCES = ("student", "guardian")
REPORT_STATUSES = ("hypothesis", "established")


@dataclass(frozen=True)
class ReportClaim:
    """报告用的判断视图（store 按受众与可见范围读出；这里再过滤一次）"""
    claim: ClaimForRender
    subject: str | None
    target_type: str
    visibility: tuple[str, ...]
    expires_at: datetime
    template_approved: bool = True


@dataclass(frozen=True)
class ReportResult:
    report: dict[str, Any] | None
    blocked: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.report is not None


def _eligible(rc: ReportClaim, audience: str, policy: Policy, now: datetime) -> str | None:
    cat = policy.category_of(rc.claim.template_key)
    if (cat in NODE_CATEGORIES and rc.target_type == "node" and rc.claim.status in REPORT_STATUSES and audience in rc.visibility
            and rc.expires_at > now and rc.template_approved and rc.subject in policy.visible_subjects
            and policy.templates[cat].version == rc.claim.template_version):
        return cat
    return None


def _order(rc: ReportClaim) -> tuple:
    level = rc.claim.value.get("level")
    rank = COGNITIVE_LEVELS.index(level) if level in COGNITIVE_LEVELS else len(COGNITIVE_LEVELS)
    return (rc.subject or "", rc.claim.target_id, rank, rc.claim.claim_id)


def build_first_report(claims: Sequence[ReportClaim], audience: str, policy: Policy, check_cfg: "consistency.CheckConfig",
                       forbidden: Forbidden, now: datetime) -> ReportResult:
    if audience not in AUDIENCES:
        raise ValueError("audience must be student or guardian")
    groups: dict[str, list[ReportClaim]] = {c: [] for c in NODE_CATEGORIES}
    for rc in claims:
        cat = _eligible(rc, audience, policy, now)
        if cat is not None:
            groups[cat].append(rc)
    if not any(groups.values()):
        return ReportResult(None, ("no_visible_claims",))
    blocked: list[str] = []
    sections: list[dict[str, Any]] = []
    check_sections: list[consistency.Section] = []
    views: dict[str, consistency.ClaimView] = {}
    for cat in NODE_CATEGORIES:
        items = sorted(groups[cat], key=_order)
        if not items:
            continue
        sentences = []
        stmts = []
        for rc in items:
            c = rc.claim
            text = render_text(c.text_template, c.target_id, c.value, forbidden)
            if text is None:
                blocked.append("render_failed")
                continue
            s: dict[str, Any] = {"claim_id": c.claim_id, "template_key": c.template_key, "template_version": c.template_version,
                                 "status": c.status, "display": "solid", "text": text}
            if c.status == "hypothesis":
                s["label"] = "preliminary"
            if audience == "guardian":
                s["confidence_band"] = policy.confidence_band(c.confidence)
            sentences.append(s)
            stmts.append(consistency.Statement(c.claim_id, text))
            views[c.claim_id] = consistency.ClaimView(c.claim_id, c.status, c.template_key)
        connective = policy.report.connectives[cat]
        sections.append({"key": cat, "connective": connective, "sentences": sentences})
        check_sections.append(consistency.Section(cat, connective, tuple(stmts)))
    result = consistency.check_page(check_sections, views, check_cfg, forbidden)
    blocked.extend(v.code for v in result.violations)
    fixed = "\n".join([policy.report.notice, policy.report.titles[audience]])
    if forbidden(fixed):
        blocked.append("forbidden_inference")
    if blocked:
        return ReportResult(None, tuple(sorted(set(blocked))))
    report = {"format": FORMAT, "audience": audience, "notice": policy.report.notice, "title": policy.report.titles[audience],
              "sections": sections, "claim_ids": [s["claim_id"] for sec in sections for s in sec["sentences"]],
              "policy_version": policy.config_version, "check": "passed"}
    return ReportResult(report)


def to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def shadow_judgments(report: Mapping[str, Any], claims: Sequence[ReportClaim]) -> list[dict[str, str]]:
    """把首份报告里的判断导出成影子评审比对的输入（evals/sie-shadow，format sie-shadow-report/0.1 的 judgments）：
    只取报告里出现的“知识掌握”判断（节点 × 层次 → 门槛状态），同一单元的“知识迁移”判断不重复计"""
    in_report = set(report.get("claim_ids") or ())
    out: dict[tuple[str, str], str] = {}
    for rc in claims:
        c = rc.claim
        level, gate = c.value.get("level"), c.value.get("gate")
        if c.claim_id in in_report and c.template_key.endswith("node_mastery") and isinstance(level, str) and isinstance(gate, str):
            out[(c.target_id, level)] = gate
    return [{"node_id": n, "level": lv, "state": g} for (n, lv), g in sorted(out.items())]
