"""跨科周报 v0 的结构化生成（冲刺 5-H spec 设计要点 4；SIE V1.1 第六章节奏、第八章信任界面）：纯函数，不连库、不调模型。

周报 = 固定结构（按受众的固定段落）+ 衔接短语占位 + 判断渲染句，外加一栏使用记录：使用时长与各科练习（来源 objective），
以及单独的“学生自己标记”栏（计划完成 / 跳过，来源 self，迁移 0057）。两栏都是记录，不是判断。
- 学生版“成长手记”：本周点亮了什么（lit）、正在攻克什么（working_on）、下一步（next）；
  家长版周报：本周进展（progress）、当前卡点（stuck）、一条具体配合建议（suggestion）、预测与对账摘要（prediction）。
- 只由判断渲染（规则 11）：首发只收数学“知识掌握 / 知识迁移”两类节点判断（待人确认 #2），与首份报告同一套筛选——
  状态为假设或成立、可见范围含该受众、未到期、模板版本与策略一致、学科在策略 visible_subjects 里；英语不出现。
  段落归属只看判断本身：知识迁移判断且本周新建 → 点亮 / 进展；知识掌握判断且门槛低于“迁移”→ 攻克 / 卡点。
  “下一步”“配合建议”“预测与对账摘要”没有对应的判断模板，v0 不出（模板人写人审后再接，lessons）。
- 学生版不含家长问卷与家长评价：来源为家长观察（guardian）的判断不进学生版；家长版不含逐句对话（判断渲染句之外没有任何文字来源）、
  同伴比较、排名与层级标签（配置的排除词表在全文另查一次）。
- 复用首份报告的两道检查：陈述—判断一致性（learning.mastery.consistency.check_page：衔接短语白名单、程度不升级、不新增结论、
  衔接语无数字、无人设语言、全文禁用推断）与禁用推断词表（标题另查）。任一不过 → 不出（withheld，只留阻断编码）。
- 每句记所引判断的修订标记（claim_revision = 判断的 updated_at；库里的守卫核对并补盖）：BFF 读取时判断被修订过的句子丢弃（安全审查 F1）。
- 快照口径以生成时刻为准：判断的筛选（状态、可见、到期）按生成时的数据库时间，句子文字是生成时的渲染结果。
- 同样的判断、同样的配置，输出逐字节相同（to_json）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .policy import CONFIG_DIR, CONFIG_VERSION, NODE_CATEGORIES, TEST_ENV, Policy, PolicyError, load_check_config
from .render import ClaimForRender, render_text
from .report import _order
from .shared import STAR_GATES, Forbidden, consistency, forbidden_matcher

# 0.2（PLT-15 PR3）：客观栏里的计划完成 / 跳过移到 self_marked 栏（来源 self）
FORMAT = "sie-weekly-report/0.2"
# 0.1.1（PLT-15 PR2）：客观栏的计划完成 / 跳过改为“同一任务以最后一次记录为准”（与计划完成率同一口径，plan_tasks.py）；
# 原来“只要有完成就算完成”。判断句、模板、可见范围不变
# 0.2.0（PLT-15 PR3，迁移 0057）：计划完成 / 跳过移到客观栏里单独的 self_marked 栏、标来源 self（学生自己标记的，规则 6）；
# 计数口径不变。旧形状只留给 0.1.0 / 0.1.1 的已有快照（0057 的表约束 weekly_report_objective_plan_source）
ALGORITHM_VERSION = "sie-weekly@0.2.0"
# 0057 里允许旧形状（顶层计划计数、标 objective）的算法版本：当前版本不得在其中（tests/test_weekly.py 锁住）
LEGACY_OBJECTIVE_VERSIONS = ("sie-weekly@0.1.0", "sie-weekly@0.1.1")
SCHEMA_VERSION = "sie-weekly-policy/0.1"
AUDIENCES = ("student", "guardian")
SECTIONS: Mapping[str, tuple[str, ...]] = {"student": ("lit", "working_on", "next"),
                                           "guardian": ("progress", "stuck", "suggestion", "prediction")}
ALL_SECTIONS = tuple(s for a in AUDIENCES for s in SECTIONS[a])
STATUSES = ("hypothesis", "established")
OBJECTIVE_SUBJECTS = ("aya", "zhixing", "wenli")  # 英语搁置，不出现
SHANGHAI = timezone(timedelta(hours=8))
MAX_MINUTES = 10080


@dataclass(frozen=True)
class WeeklyPolicy:
    """周报的固定结构配置（标题、段落衔接短语、每段上限、抽检比例、按受众的排除词表）；全部是占位，人写人审（必停）"""
    config_version: str
    synthetic: bool
    signed: bool
    titles: Mapping[str, str]
    connectives: Mapping[str, str]
    max_per_section: int
    sample_ratio: float
    sample_min: int
    excluded_terms: Mapping[str, tuple[str, ...]]
    fingerprint: str


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise PolicyError(msg)


def _text(v: Any, where: str, max_len: int = 120) -> str:
    _need(isinstance(v, str) and 1 <= len(v.strip()) <= max_len, f"{where} 必须是 1–{max_len} 字的文本")
    return v


def parse_weekly_policy(doc: Mapping[str, Any], *, env: str) -> WeeklyPolicy:
    keys = {"schema_version", "config_version", "synthetic", "signed_by", "signed_at", "titles", "connectives", "max_per_section",
            "sample", "excluded_terms"}
    _need(isinstance(doc, dict) and set(doc) - {"$comment"} == keys, f"周报配置的配置位不对（应为 {sorted(keys)}）")
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and bool(CONFIG_VERSION.fullmatch(doc["config_version"])), "config_version 格式不对")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    _need(isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str), "signed_by / signed_at 必须是字符串")
    if doc["synthetic"] and env != TEST_ENV:
        raise PolicyError("合成占位的周报配置只允许 INSIGHT_ENV=test 加载（标题、衔接短语待人写人审）")
    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]
    t = doc["titles"]
    _need(isinstance(t, dict) and set(t) == set(AUDIENCES), "titles 必须恰好是 student / guardian")
    titles = {a: _text(t[a], f"titles.{a}") for a in AUDIENCES}
    c = doc["connectives"]
    _need(isinstance(c, dict) and set(c) == set(ALL_SECTIONS), f"connectives 必须恰好是 {list(ALL_SECTIONS)}")
    connectives = {s: _text(c[s], f"connectives.{s}", 60) for s in ALL_SECTIONS}
    n = doc["max_per_section"]
    _need(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 10, "max_per_section 必须是 1–10 的整数")
    sm = doc["sample"]
    _need(isinstance(sm, dict) and set(sm) == {"ratio", "min"}, "sample 必须恰好是 ratio / min")
    ratio, smin = sm["ratio"], sm["min"]
    _need(isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and math.isfinite(ratio) and 0 < ratio <= 0.5,
          "sample.ratio 必须在 (0, 0.5]（与 platform.review_sample_weekly_reports 一致）")
    _need(isinstance(smin, int) and not isinstance(smin, bool) and 0 <= smin <= 100, "sample.min 必须是 0–100 的整数")
    ex = doc["excluded_terms"]
    _need(isinstance(ex, dict) and set(ex) == set(AUDIENCES), "excluded_terms 必须恰好是 student / guardian")
    excluded = {}
    for a in AUDIENCES:
        _need(isinstance(ex[a], list) and all(isinstance(w, str) and 1 <= len(w) <= 20 for w in ex[a]), f"excluded_terms.{a} 必须是短词列表")
        excluded[a] = tuple(ex[a])
    _need(len(excluded["guardian"]) >= 1, "家长版必须有排除词表（同伴比较、排名、层级标签）")
    fixed = "\n".join([*titles.values(), *connectives.values()])
    _need(not forbidden_matcher()(fixed), "周报的标题与衔接短语占位命中禁用推断词表")
    for a in AUDIENCES:
        _need(not any(w in fixed for w in excluded[a]), "周报的标题与衔接短语里出现了排除词")
    fp = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return WeeklyPolicy(doc["config_version"], doc["synthetic"], signed, titles, connectives, n, float(ratio), smin, excluded, fp)


def load_weekly_policy(path: str | Path, *, env: str) -> WeeklyPolicy:
    with open(path, encoding="utf-8") as f:
        return parse_weekly_policy(json.load(f), env=env)


def synthetic_weekly_policy() -> WeeklyPolicy:
    return load_weekly_policy(CONFIG_DIR / "weekly.synthetic.json", env=TEST_ENV)


def synthetic_weekly_check_config() -> "consistency.CheckConfig":
    """周报一致性检查的词表（与首份报告同一格式；衔接短语是周报的固定段落占位）"""
    return load_check_config(CONFIG_DIR / "weekly-check.synthetic.json", env=TEST_ENV)


# ---------------------------------------------------------------- 周与客观栏

def week_of(day: date) -> tuple[date, date]:
    """上海时区日历周：周一到周日"""
    start = day - timedelta(days=day.isoweekday() - 1)
    return start, start + timedelta(days=6)


def week_window(week_start: date) -> tuple[datetime, datetime]:
    """[周一 00:00, 下周一 00:00)（上海时区）"""
    if week_start.isoweekday() != 1:
        raise ValueError("week_start must be a Monday")
    start = datetime.combine(week_start, time(0), SHANGHAI)
    return start, start + timedelta(days=7)


@dataclass(frozen=True)
class ObjectiveFacts:
    """一周的使用记录（事实，不是判断）：会话结束事件的时长、计划完成与跳过、各科作答（用时与题数）。
    计划完成 / 跳过是学生自己点的记录（来源 self，0057；同一任务以最后一次为准，plan_tasks.py），不是核实过的完成，也不代表掌握"""
    session_seconds: int = 0
    plan_done: int = 0
    plan_skipped: int = 0
    practice: Mapping[str, tuple[int, int]] = None  # type: ignore[assignment]  学科 → (用时秒, 作答数)


def objective_column(facts: ObjectiveFacts) -> dict[str, Any]:
    """客观栏：使用时长与各科练习标来源 objective；计划完成 / 跳过放在单独的 self_marked 栏、标来源 self（学生自己标记的，0057）。
    分钟向下取整、封顶一周；只列首发学科（英语不出现），按学科编码排序"""
    def minutes(sec: int) -> int:
        return max(0, min(MAX_MINUTES, int(sec) // 60))
    practice = facts.practice or {}
    subjects = [{"subject": s, "practice_minutes": minutes(practice[s][0]), "attempts": max(0, min(99999, int(practice[s][1])))}
                for s in sorted(practice) if s in OBJECTIVE_SUBJECTS]
    return {"source_type": "objective", "usage_minutes": minutes(facts.session_seconds), "subjects": subjects,
            "self_marked": {"source_type": "self", "plan_done": max(0, min(9999, int(facts.plan_done))),
                            "plan_skipped": max(0, min(9999, int(facts.plan_skipped)))}}


# ---------------------------------------------------------------- 周报

@dataclass(frozen=True)
class WeeklyClaim:
    """周报用的判断视图（store 按受众与可见范围读出；这里再过滤一次）"""
    claim: ClaimForRender
    subject: str | None
    target_type: str
    visibility: tuple[str, ...]
    expires_at: datetime
    source_type: str
    created_at: datetime
    updated_at: datetime | None = None  # 判断的修订标记（insight.claim.updated_at）：落库后 BFF 用它丢掉生成后被修订过的句子


@dataclass(frozen=True)
class WeeklyResult:
    report: dict[str, Any] | None
    blocked: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.report is not None

    @property
    def empty(self) -> bool:
        """没有可说的（没有可见判断）：不写周报，也不算不出"""
        return self.blocked == ("no_visible_claims",)


def _eligible(wc: WeeklyClaim, audience: str, policy: Policy, now: datetime) -> str | None:
    cat = policy.category_of(wc.claim.template_key)
    if (cat in NODE_CATEGORIES and wc.target_type == "node" and wc.claim.status in STATUSES and audience in wc.visibility
            and wc.expires_at > now and wc.subject in policy.visible_subjects and wc.subject != "echo"
            and policy.templates[cat].version == wc.claim.template_version
            and not (audience == "student" and wc.source_type == "guardian")):
        return cat
    return None


def section_of(wc: WeeklyClaim, cat: str, audience: str, week: tuple[datetime, datetime]) -> str | None:
    gate = wc.claim.value.get("gate")
    if cat == "node_transfer" and week[0] <= wc.created_at < week[1]:
        return "lit" if audience == "student" else "progress"
    if cat == "node_mastery" and isinstance(gate, str) and gate not in STAR_GATES:
        return "working_on" if audience == "student" else "stuck"
    return None


def build_weekly(claims: Sequence[WeeklyClaim], audience: str, week_start: date, policy: Policy, weekly: WeeklyPolicy,
                 check_cfg: "consistency.CheckConfig", forbidden: Forbidden, now: datetime, facts: ObjectiveFacts) -> WeeklyResult:
    if audience not in AUDIENCES:
        raise ValueError("audience must be student or guardian")
    window = week_window(week_start)
    groups: dict[str, list[WeeklyClaim]] = {s: [] for s in SECTIONS[audience]}
    for wc in claims:
        cat = _eligible(wc, audience, policy, now)
        sec = section_of(wc, cat, audience, window) if cat else None
        if sec is not None:
            groups[sec].append(wc)
    if not any(groups.values()):
        return WeeklyResult(None, ("no_visible_claims",))
    blocked: list[str] = []
    sections: list[dict[str, Any]] = []
    check_sections: list[consistency.Section] = []
    views: dict[str, consistency.ClaimView] = {}
    for key in SECTIONS[audience]:
        items = sorted(groups[key], key=_order)[:weekly.max_per_section]  # type: ignore[arg-type]
        if not items:
            continue
        sentences, stmts = [], []
        for wc in items:
            c = wc.claim
            text = render_text(c.text_template, c.target_id, c.value, forbidden)
            if text is None:
                blocked.append("render_failed")
                continue
            s: dict[str, Any] = {"claim_id": c.claim_id, "template_key": c.template_key, "template_version": c.template_version,
                                 "section": key, "text": text}
            if wc.updated_at is not None:
                s["claim_revision"] = wc.updated_at.isoformat()
            if c.status == "hypothesis":
                s["label"] = "preliminary"
            if audience == "guardian":
                s["confidence_band"] = policy.confidence_band(c.confidence)
            sentences.append(s)
            stmts.append(consistency.Statement(c.claim_id, text))
            views[c.claim_id] = consistency.ClaimView(c.claim_id, c.status, c.template_key)
        sections.append({"key": key, "connective": weekly.connectives[key], "sentences": sentences})
        check_sections.append(consistency.Section(key, weekly.connectives[key], tuple(stmts)))
    result = consistency.check_page(check_sections, views, check_cfg, forbidden)
    blocked.extend(v.code for v in result.violations)
    title = weekly.titles[audience]
    if forbidden(title):
        blocked.append("forbidden_inference")
    full = "\n".join([title, *(sec["connective"] for sec in sections), *(s["text"] for sec in sections for s in sec["sentences"])])
    if any(w in full for w in weekly.excluded_terms[audience]):
        blocked.append("excluded_term")
    if blocked:
        return WeeklyResult(None, tuple(sorted(set(blocked))))
    report = {"format": FORMAT, "audience": audience, "week_start": week_start.isoformat(),
              "week_end": (week_start + timedelta(days=6)).isoformat(), "title": title, "sections": sections,
              "claim_ids": [s["claim_id"] for sec in sections for s in sec["sentences"]], "objective": objective_column(facts),
              "algorithm_version": ALGORITHM_VERSION, "policy_version": f"{policy.config_version}_{weekly.config_version}",
              "check": "passed"}
    return WeeklyResult(report)


def sentences_of(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """落库用的句子数组（insight.weekly_report.sentences）：每句带 claim_id 与段落"""
    return [dict(s) for sec in report["sections"] for s in sec["sentences"]]


def to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
