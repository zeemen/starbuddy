"""月度计划的结构化生成（PLT-14 第 2 步，specs/family-goals-monthly-plan/step2-spec.md 默认设计 4–6）：纯函数，不连库、不调模型、不读系统时钟。

快照 = 结构化内容 + 模板键（文字全部在模板库 services/insight/templates/monthly_plan.v0.yaml，`{{TODO_COPY:monthly_plan.*}}` 占位，
人写人审后才能填；这里不渲染、不存整段文字）。三段：
- student_plan（学生与家长都看得到）：
  - 本月优先知识点：按 优先级 = 缺口 × 考情分值权重 × 频率 × 关注学科系数 排序，取前 N 个（配置，合成 8）。
    缺口按当前掌握门槛查配置（unseen … stable；进入“迁移”的不再是候选）；考情分值权重与频率没有已发布数据时一律为 1
    （快照标 policy_weights = default）；关注学科系数（合成 1.5）只用于当前有效绑定家长写的当前版诉求里的关注学科。
    每一项都必须引用一条“待点亮”的知识掌握判断（同节点同层次、成立或假设、未到期、对学生与家长都可见、不是家长观察来源、
    模板与策略一致、门槛与掌握行一致、能按已审核模板渲染）；没有这样的判断就不出这一项（规则 11）。
    学生段只有“做什么”：节点、层次、计划里的先后（position，模板不得渲染为名次）与所引判断，不含系数、优先级数值与任何来自家长诉求的字段；
  - 每周节奏：每周次数（配置）与每次时长（复用今日计划配置的 default_budget_minutes）；
  - 本月复测：开着的复测任务里到期不晚于月底的（逾期的按月初计）计数、日期与按学科计数；
  - 分科小复评：有掌握行的首发学科，每科一次（配置的日子起依次排开，不超过月底）。
- guardian_notes（只给家长）：模板库按顺序取满足条件的前 3 条；条件与变量只用计划事实（复测次数、每周次数、本月有没有考试等），
  模板只描述家长可做的安排，不评价孩子。
- review（只给家长）：
  - 上月回顾（有上月快照时）：上月计划节点里按 claim_history 在上个月首次进入“迁移”的（引用知识迁移判断，没有判断不出），
    另有上月计划节点数这一计划事实；不给任何不引用判断的关于学生的计数（规则 11）；
  - 分差：只存引用——成绩记录、诉求版本与所引考试判断（同一满分口径、两者都有、且有对家长可见的考试判断引用那条成绩时才给），
    不存成绩与目标分的值；家长视图读时现算，再核成绩记录与诉求仍是当前版，按 5 分一档给区间，只给本人诉求对应的项。
- plan_basis：时点审计记录，任何视图都不返回、任何计算都不读取。
- input_refs：输入引用（判断 ID 与 claim_history 版本、掌握行、成绩记录、考试安排、诉求版本、复测任务、上月快照）与配置指纹，
  重放时按它们找回同一组输入（store_monthly.replay_inputs）。
同样的输入（含 as_of）与同样的配置、模板，输出逐字节相同（to_json）；inputs_digest 是输入的规范化 JSON 的 SHA-256。
不读家长补充说明与关注学校；不出名次、概率、承诺（模板库加载时过禁用推断词表与“名次 / 概率 / 承诺”词表）。
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .claims import NODE_ID, MasteryRow
from .policy import CONFIG_DIR, CONFIG_VERSION, TEST_ENV, Policy, PolicyError
from .render import render_text
from .shared import ROOT, STAR_GATES, Forbidden, forbidden_matcher

FORMAT = "sie-monthly-plan/0.1"
ALGORITHM_VERSION = "sie-monthly@0.1.0"
SCHEMA_VERSION = "sie-monthly-plan-config/0.1"
TEMPLATE_FORMAT = "sie-monthly-plan-templates/0.1"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "monthly_plan.v0.yaml"
NO_PROMISE_PATH = ROOT / "docs" / "design" / "sie" / "no-promise.v0.draft.yaml"
GAP_GATES = ("unseen", "understand", "execute", "stable")   # 低于“迁移”的门槛（待点亮）
LIVE = ("hypothesis", "established")
SHANGHAI = timezone(timedelta(hours=8))
SUBJECTS = ("aya", "zhixing", "wenli")                       # 英语搁置，不出现
EXAM_TYPES = ("monthly", "midterm", "final", "mock1", "mock2", "other")
COPY = {  # 固定位置的模板键（与 0054 的形状校验一致）
    "priority_node": "monthly_plan.student.priority_node", "weekly_rhythm": "monthly_plan.student.weekly_rhythm",
    "retests": "monthly_plan.student.retests", "mini_review": "monthly_plan.student.mini_review",
    "last_month": "monthly_plan.review.last_month", "lit": "monthly_plan.review.lit", "score_gap": "monthly_plan.review.score_gap",
}
NOTE_FACTS = ("retest_count", "sessions_per_week", "minutes_per_session", "exam_in_month", "exam_count", "mini_review_count",
              "priority_count")
NOTE_CONDITIONS = {"retest_count_min": "retest_count", "mini_review_count_min": "mini_review_count",
                   "priority_count_min": "priority_count", "exam_in_month": "exam_in_month"}
PLACEHOLDER = re.compile(r"^\{\{TODO_COPY:(monthly_plan\.[a-z_]+\.[a-z_]+)\}\}$")
KEY = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise PolicyError(msg)


def _int(v: Any, where: str, lo: int, hi: int) -> int:
    _need(isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi, f"{where} 必须是 [{lo}, {hi}] 内的整数")
    return int(v)


def _num(v: Any, where: str, lo: float, hi: float) -> float:
    _need(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi,
          f"{where} 必须是 [{lo}, {hi}] 内的数")
    return float(v)


# ---------------------------------------------------------------- 配置

@dataclass(frozen=True)
class MonthlyConfig:
    """月度计划的配置位（全部是合成占位，待人签字）：优先知识点上限、关注学科系数、缺口表、每周次数、小复评日、注意事项与分差条数上限。
    每次时长复用今日计划配置（services/learning/learning/today/config.py 的 default_budget_minutes），不另设一份"""
    config_version: str
    synthetic: bool
    signed: bool
    max_priority_nodes: int
    focus_factor: float
    gate_gap: Mapping[str, float]
    sessions_per_week: int
    minutes_per_session: int
    mini_review_day: int
    max_guardian_notes: int
    max_score_gaps: int
    fingerprint: str        # 本配置文件内容的哈希
    today_fingerprint: str  # 今日计划预算配置（解析后的全部字段）的哈希


def _fp(doc: Any) -> str:
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _today(spec: str, env: str) -> tuple[int, str]:
    """今日计划配置的缺省预算（分钟）与它的指纹：沿用 shared 只读引用学习服务纯函数模块的方式"""
    from learning.today.config import TodayConfigError, load as load_today  # noqa: PLC0415 — 学习服务的纯配置模块
    try:
        t = load_today(spec, env=env)
    except TodayConfigError as e:
        raise PolicyError(f"今日计划配置不可用：{e}") from None
    return t.default_budget_minutes, _fp({"default_budget_minutes": t.default_budget_minutes, "source_priority": list(t.source_priority),
                                         "entry_minutes": dict(t.entry_minutes), "synthetic": t.synthetic, "signed_by": t.signed_by,
                                         "signed_at": t.signed_at})


def parse_monthly_config(doc: Mapping[str, Any], *, env: str) -> MonthlyConfig:
    keys = {"schema_version", "config_version", "synthetic", "signed_by", "signed_at", "max_priority_nodes", "focus_subject_factor",
            "gate_gap", "sessions_per_week", "today_config", "mini_review_day", "max_guardian_notes", "max_score_gaps"}
    _need(isinstance(doc, dict) and set(doc) - {"$comment"} == keys, f"月度计划配置的配置位不对（应为 {sorted(keys)}）")
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and bool(CONFIG_VERSION.fullmatch(doc["config_version"])), "config_version 格式不对")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    _need(isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str), "signed_by / signed_at 必须是字符串")
    if doc["synthetic"] and env != TEST_ENV:
        raise PolicyError("合成占位的月度计划配置只允许 INSIGHT_ENV=test 加载（数值待人签字）")
    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]
    gg = doc["gate_gap"]
    _need(isinstance(gg, dict) and set(gg) == set(GAP_GATES), f"gate_gap 必须恰好是 {list(GAP_GATES)}")
    gaps = {g: _num(gg[g], f"gate_gap.{g}", 0.0, 1.0) for g in GAP_GATES}
    _need(all(gaps[a] >= gaps[b] for a, b in zip(GAP_GATES, GAP_GATES[1:])) and gaps["stable"] > 0,
          "gate_gap 必须随门槛升高不增，且都大于 0（门槛越低缺口越大）")
    _need(isinstance(doc["today_config"], str) and 1 <= len(doc["today_config"]) <= 300, "today_config 必须是 synthetic 或配置文件路径")
    minutes, today_fp = _today(doc["today_config"], env)
    fp = _fp(doc)
    return MonthlyConfig(
        config_version=doc["config_version"], synthetic=doc["synthetic"], signed=signed,
        max_priority_nodes=_int(doc["max_priority_nodes"], "max_priority_nodes", 1, 20),
        focus_factor=_num(doc["focus_subject_factor"], "focus_subject_factor", 1.0, 3.0), gate_gap=gaps,
        sessions_per_week=_int(doc["sessions_per_week"], "sessions_per_week", 1, 7), minutes_per_session=minutes,
        mini_review_day=_int(doc["mini_review_day"], "mini_review_day", 1, 28),
        max_guardian_notes=_int(doc["max_guardian_notes"], "max_guardian_notes", 1, 3),
        max_score_gaps=_int(doc["max_score_gaps"], "max_score_gaps", 1, 16), fingerprint=fp, today_fingerprint=today_fp)


def load_monthly_config(path: str | Path, *, env: str) -> MonthlyConfig:
    with open(path, encoding="utf-8") as f:
        return parse_monthly_config(json.load(f), env=env)


def synthetic_monthly_config() -> MonthlyConfig:
    return load_monthly_config(CONFIG_DIR / "monthly-plan.synthetic.json", env=TEST_ENV)


# ---------------------------------------------------------------- “名次 / 概率 / 承诺”词表与模板库

_no_promise: Forbidden | None = None


def no_promise_matcher(path: Path = NO_PROMISE_PATH) -> Forbidden:
    """“名次 / 概率 / 承诺”最小词表（草稿）的匹配函数：与禁用推断词表同一个加载与匹配实现（sie_lexicon.load / find，同一套规范化）。
    找不到词表即报错：检查不能缺席"""
    global _no_promise
    if _no_promise is not None and path == NO_PROMISE_PATH:
        return _no_promise
    forbidden_matcher()  # 先确保 sie_lexicon 已按 consistency 的方式加载
    lexicon = sys.modules["sie_lexicon"].load(path)
    matcher: Forbidden = lambda text: [h.category for h in lexicon.find(text)]  # noqa: E731
    if path == NO_PROMISE_PATH:
        _no_promise = matcher
    return matcher


def no_promise_self_test(path: Path = NO_PROMISE_PATH) -> list[str]:
    """词表自检：rank / probability / promise 三类齐全；每类禁止示例都被本类命中；允许示例不被任何一类命中、也不命中禁用推断词表"""
    lexicon = (forbidden_matcher(), sys.modules["sie_lexicon"].load(path))[1]
    errors: list[str] = []
    ids = [c.id for c in lexicon.categories]
    for need in ("rank", "probability", "promise"):
        if need not in ids:
            errors.append(f"缺少类别 {need}")
    for c in lexicon.categories:
        if not c.forbidden_examples:
            errors.append(f"类别 {c.id} 没有禁止示例")
        for e in c.forbidden_examples:
            if not any(h.category == c.id for h in lexicon.find(e)):
                errors.append(f"类别 {c.id} 的禁止示例没有被本类命中：{e}")
    if len(lexicon.allowed_examples) < 8:
        errors.append("允许示例少于 8 条")
    fm = forbidden_matcher()
    for e in lexicon.allowed_examples:
        if lexicon.find(e):
            errors.append(f"允许示例被误伤：{e}")
        if fm(e):
            errors.append(f"允许示例命中禁用推断词表：{e}")
    return errors


@dataclass(frozen=True)
class NoteTemplate:
    key: str
    copy: str
    when: Mapping[str, Any]
    vars: tuple[str, ...]

    @property
    def copy_key(self) -> str:
        return f"monthly_plan.guardian.{self.key}"


@dataclass(frozen=True)
class TemplateLibrary:
    template_version: str
    synthetic: bool
    signed: bool
    fixed: Mapping[str, str]          # 模板键 → 文案（占位）
    notes: tuple[NoteTemplate, ...]   # 家长注意事项：按顺序选取
    fingerprint: str = ""             # 模板库原文字节的 SHA-256（与 BFF 同一算法，PLT-14 第 3 步审查）
    fixed_vars: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # 固定模板键 → 声明的变量


SLOT = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def template_fingerprint(raw: bytes) -> str:
    """模板库指纹：原文字节的 SHA-256（十六进制 64 位）。BFF（services/bff/src/family/monthly-plan.ts）同一算法"""
    return hashlib.sha256(raw).hexdigest()


def _copy_ok(text: Any, copy_key: str, signed: bool, variables: Sequence[str] = ()) -> str:
    """文案：未签字时必须是与模板键一致的 {{TODO_COPY:…}} 占位；签字后可以是人写的文字（1–120 字），
    其中的 {变量} 集合必须恰好等于声明的 vars，除此之外不含花括号"""
    _need(isinstance(text, str), f"{copy_key} 的文案必须是字符串")
    m = PLACEHOLDER.fullmatch(text)
    if m:
        _need(m.group(1) == copy_key, f"{copy_key} 的占位键不一致")
    else:
        _need(signed and 1 <= len(text.strip()) <= 120 and "{{" not in text, f"{copy_key}：未签字的模板库只收 {{{{TODO_COPY:…}}}} 占位")
        _need(set(SLOT.findall(text)) == set(variables) and not re.search(r"[{}]", SLOT.sub("", text)),
              f"{copy_key}：签字文案里的 {{变量}} 必须恰好是声明的 vars")
    return text


def parse_templates(doc: Mapping[str, Any], *, env: str, forbidden: Forbidden | None = None,
                    no_promise: Forbidden | None = None, raw: bytes | None = None) -> TemplateLibrary:
    """raw：模板库原文字节（指纹按它算）；只给解析后的文档时（测试）按规范化的 YAML 文本算"""
    keys = {"format", "template_version", "status", "synthetic", "signed_by", "signed_at", "student", "review", "guardian"}
    _need(isinstance(doc, dict) and set(doc) - {"$comment"} == keys, f"模板库的配置位不对（应为 {sorted(keys)}）")
    _need(doc["format"] == TEMPLATE_FORMAT, f"format 必须是 {TEMPLATE_FORMAT}")
    _need(isinstance(doc["template_version"], str) and bool(CONFIG_VERSION.fullmatch(doc["template_version"])), "template_version 格式不对")
    _need(doc["status"] in ("draft", "signed"), "status 只能是 draft / signed")
    _need(isinstance(doc["synthetic"], bool) and isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str),
          "synthetic / signed_by / signed_at 类型不对")
    signed = doc["status"] == "signed" and bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]
    if not signed and env != TEST_ENV:
        raise PolicyError("未签字（含合成占位）的月度计划模板库只允许 INSIGHT_ENV=test 加载（文案人写人审）")
    fixed: dict[str, str] = {}
    fixed_vars: dict[str, tuple[str, ...]] = {}
    sections = {"student": ("priority_node", "weekly_rhythm", "retests", "mini_review"), "review": ("last_month", "lit", "score_gap")}
    for sec, names in sections.items():
        s = doc[sec]
        _need(isinstance(s, dict) and set(s) == set(names), f"{sec} 必须恰好是 {list(names)}")
        for n in names:
            t = s[n]
            _need(isinstance(t, dict) and set(t) == {"copy", "vars"}, f"{sec}.{n} 必须恰好有 copy 与 vars")
            _need(isinstance(t["vars"], list) and all(isinstance(v, str) and KEY.fullmatch(v) for v in t["vars"]), f"{sec}.{n}.vars 必须是变量名")
            _need(len(t["vars"]) <= 8 and len(set(t["vars"])) == len(t["vars"]), f"{sec}.{n}.vars 不能重复、至多 8 个")
            ck = COPY[n]
            fixed[ck] = _copy_ok(t["copy"], ck, signed, t["vars"])
            fixed_vars[ck] = tuple(t["vars"])
    g = doc["guardian"]
    _need(isinstance(g, list) and 1 <= len(g) <= 12, "guardian 必须是 1–12 条注意事项模板")
    notes: list[NoteTemplate] = []
    for i, t in enumerate(g):
        _need(isinstance(t, dict) and set(t) == {"key", "copy", "when", "vars"}, f"guardian[{i}] 必须恰好有 key、copy、when、vars")
        _need(isinstance(t["key"], str) and bool(KEY.fullmatch(t["key"])), f"guardian[{i}].key 格式不对")
        _need(isinstance(t["when"], dict) and set(t["when"]) <= set(NOTE_CONDITIONS), f"guardian[{i}].when 只能用 {sorted(NOTE_CONDITIONS)}")
        for ck, v in t["when"].items():
            if ck == "exam_in_month":
                _need(isinstance(v, bool), f"guardian[{i}].when.exam_in_month 必须是布尔值")
            else:
                _int(v, f"guardian[{i}].when.{ck}", 0, 999)
        _need(isinstance(t["vars"], list) and len(t["vars"]) <= 8 and all(v in NOTE_FACTS for v in t["vars"])
              and len(set(t["vars"])) == len(t["vars"]), f"guardian[{i}].vars 只能是计划事实 {list(NOTE_FACTS)}")
        nt = NoteTemplate(t["key"], "", dict(t["when"]), tuple(t["vars"]))
        notes.append(NoteTemplate(nt.key, _copy_ok(t["copy"], nt.copy_key, signed, nt.vars), nt.when, nt.vars))
    _need(len({n.key for n in notes}) == len(notes), "注意事项模板的 key 不能重复")
    fm, npm = forbidden or forbidden_matcher(), no_promise or no_promise_matcher()
    for text in [*fixed.values(), *(n.copy for n in notes)]:
        _need(not fm(text), "模板库文案命中禁用推断词表")
        _need(not npm(text), "模板库文案命中“名次 / 概率 / 承诺”词表")
    if raw is None:
        raw = yaml.safe_dump(dict(doc), allow_unicode=True, sort_keys=True).encode("utf-8")
    return TemplateLibrary(doc["template_version"], doc["synthetic"], signed, fixed, tuple(notes), template_fingerprint(raw), fixed_vars)


def check_lock(lib: TemplateLibrary, lock_text: str) -> None:
    """钉死文件（与模板库同目录、同名 .lock）：恰好记 template_version 与原文指纹 sha256（可带 $comment）。
    任何一项与读到的模板库不一致即拒绝加载——内容改了而版本号没改、或钉死文件没跟着改，都加载不了（BFF 同一规则）"""
    try:
        lock = json.loads(lock_text)
    except ValueError:
        raise PolicyError("模板库钉死文件不是合法 JSON") from None
    _need(isinstance(lock, dict) and set(lock) - {"$comment"} == {"template_version", "sha256"}, "模板库钉死文件只能有 template_version 与 sha256")
    _need(lock["template_version"] == lib.template_version and lock["sha256"] == lib.fingerprint,
          "模板库与钉死文件不一致（内容改了要同时改版本号与钉死文件，并经人审）")


def lock_path(path: str | Path) -> Path:
    return Path(path).with_suffix(".lock")


def load_templates(path: str | Path = TEMPLATE_PATH, *, env: str) -> TemplateLibrary:
    raw = Path(path).read_bytes()
    lib = parse_templates(yaml.safe_load(raw.decode("utf-8")), env=env, raw=raw)
    lp = lock_path(path)
    _need(lp.is_file(), f"缺少模板库钉死文件 {lp.name}")
    check_lock(lib, lp.read_text(encoding="utf-8"))
    return lib


def export_library(lib: TemplateLibrary) -> dict[str, Any]:
    """跨语言一致性核对用（services/insight/tests/fixtures/monthly_plan_templates.expected.json；BFF 的单测比对同一份）：
    模板键 → 文案与变量、版本、是否签字、指纹"""
    templates = {k: {"copy": v, "vars": list(lib.fixed_vars.get(k, ()))} for k, v in lib.fixed.items()}
    templates.update({n.copy_key: {"copy": n.copy, "vars": list(n.vars)} for n in lib.notes})
    return {"template_version": lib.template_version, "signed": lib.signed, "fingerprint": lib.fingerprint,
            "templates": dict(sorted(templates.items()))}


# ---------------------------------------------------------------- 输入

@dataclass(frozen=True)
class PlanClaim:
    """生成时读到的判断（store 按受众可见范围读出；这里再按用途过滤）"""
    claim_id: str
    subject: str | None
    target_type: str
    target_id: str
    template_key: str
    template_version: int
    status: str
    value: Mapping[str, Any]
    visibility: tuple[str, ...]
    source_type: str
    expires_at: datetime
    created_at: datetime
    text_template: str | None
    history_id: str = ""                    # 生成时这条判断最新的 claim_history 行（版本），落库进 input_refs 供重放
    entered_star_at: datetime | None = None  # 按 claim_history：门槛首次进入“迁移”（成立或假设）的时刻；没有为 None


@dataclass(frozen=True)
class TargetScore:
    subject: str
    full_score: float
    target_score: float


@dataclass(frozen=True)
class Goal:
    """当前有效绑定家长写的当前版诉求里能用的部分（不含补充说明、学校、账号）"""
    goal_id: str
    focus_subjects: tuple[str, ...]
    target_scores: tuple[TargetScore, ...]


@dataclass(frozen=True)
class ExamScore:
    """每科最近一次录入的成绩（当前版，学校考试口径）"""
    record_id: str
    subject: str
    exam_type: str
    exam_date: date
    full_score: float
    raw_score: float


@dataclass(frozen=True)
class RetestDue:
    subject: str
    node_id: str
    day: date
    task_id: str = ""


FINGERPRINT_KEYS = ("claims_policy", "config", "templates", "today")


@dataclass(frozen=True)
class MonthlyInputs:
    student: str
    month: date
    as_of: datetime
    mastery: tuple[MasteryRow, ...] = ()
    claims: tuple[PlanClaim, ...] = ()
    goals: tuple[Goal, ...] = ()
    latest_scores: tuple[ExamScore, ...] = ()
    exam_plans: tuple[str, ...] = ()                 # 本月的考试安排（exam_record ID，还没有成绩）
    retests_due: tuple[RetestDue, ...] = ()
    prev_plan: Mapping[str, Any] | None = None       # 上月快照的 student_plan（没有上月快照为 None）
    prev_plan_id: str | None = None
    weights: Mapping[str, float] = field(default_factory=dict)      # "学科:节点" → 考情分值权重；空 = 没有已发布数据
    frequencies: Mapping[str, float] = field(default_factory=dict)  # "学科:节点" → 频率；空 = 1


def fingerprints(policy: Policy, cfg: MonthlyConfig, templates: TemplateLibrary) -> dict[str, str]:
    """配置指纹（按内容哈希）：判断策略、月度计划配置、模板库（含 when 条件）、今日计划预算配置"""
    return {"claims_policy": policy.fingerprint, "config": cfg.fingerprint, "templates": templates.fingerprint,
            "today": cfg.today_fingerprint}


def month_window(month: date) -> tuple[datetime, datetime]:
    """[本月 1 日 00:00, 下月 1 日 00:00)（上海时区）"""
    if month.day != 1:
        raise ValueError("month must be the first day of a month")
    start = datetime(month.year, month.month, 1, tzinfo=SHANGHAI)
    nxt = date(month.year + (month.month // 12), month.month % 12 + 1, 1)
    return start, datetime(nxt.year, nxt.month, 1, tzinfo=SHANGHAI)


def previous_month(month: date) -> date:
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


def inputs_document(inp: MonthlyInputs) -> dict[str, Any]:
    """规范化的输入（排好序、时间转 ISO），作为 inputs_digest 的依据与重放的记录"""
    return {
        "student": inp.student, "month": inp.month.isoformat(), "as_of": inp.as_of.isoformat(),
        "mastery": sorted(([r.basis_id, r.subject, r.node_id, r.cognitive_level, r.gate] for r in inp.mastery)),
        "claims": sorted(([c.claim_id, c.subject, c.target_type, c.target_id, c.template_key, c.template_version, c.status,
                           json.dumps(dict(c.value), sort_keys=True, ensure_ascii=False), sorted(c.visibility), c.source_type,
                           c.expires_at.isoformat(), c.created_at.isoformat(), c.text_template or "", c.history_id,
                           c.entered_star_at.isoformat() if c.entered_star_at else ""] for c in inp.claims)),
        "goals": sorted(([g.goal_id, sorted(g.focus_subjects), sorted([t.subject, t.full_score, t.target_score] for t in g.target_scores)]
                         for g in inp.goals)),
        "latest_scores": sorted(([s.record_id, s.subject, s.exam_type, s.exam_date.isoformat(), s.full_score, s.raw_score]
                                 for s in inp.latest_scores)),
        "exam_plans": sorted(inp.exam_plans),
        "retests_due": sorted(([r.task_id, r.subject, r.node_id, r.day.isoformat()] for r in inp.retests_due)),
        "prev_plan": inp.prev_plan, "prev_plan_id": inp.prev_plan_id,
        "weights": sorted(inp.weights.items()), "frequencies": sorted(inp.frequencies.items()),
    }


def input_refs(inp: MonthlyInputs, fps: Mapping[str, str]) -> dict[str, Any]:
    """落库的输入引用（0054 input_refs）：重放时按它们找回同一组输入；只供重放与审计"""
    return {"as_of": inp.as_of.isoformat(), "claims": sorted([c.claim_id, c.history_id] for c in inp.claims),
            "mastery": sorted({r.basis_id for r in inp.mastery}), "exam_records": sorted(s.record_id for s in inp.latest_scores),
            "exam_plans": sorted(inp.exam_plans), "goals": sorted(g.goal_id for g in inp.goals),
            "retest_tasks": sorted({r.task_id for r in inp.retests_due}), "prev_plan_id": inp.prev_plan_id,
            "fingerprints": {k: fps[k] for k in FINGERPRINT_KEYS}}


def to_json(doc: Any) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def inputs_digest(inp: MonthlyInputs) -> str:
    return hashlib.sha256(to_json(inputs_document(inp)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 生成

def _claim_live(c: PlanClaim, as_of: datetime) -> bool:
    return c.status in LIVE and c.expires_at > as_of


def _renders(c: PlanClaim, forbidden: Forbidden) -> bool:
    return render_text(c.text_template, c.target_id, c.value, forbidden) is not None


def _pending_claims(inp: MonthlyInputs, policy: Policy, forbidden: Forbidden) -> dict[tuple[str, str, str], PlanClaim]:
    """“待点亮”的知识掌握判断：（学科, 节点, 层次）→ 判断。学生段读得到，所以对学生与家长都可见、不是家长观察来源"""
    ref = policy.templates["node_mastery"]
    out: dict[tuple[str, str, str], PlanClaim] = {}
    for c in sorted(inp.claims, key=lambda x: (x.created_at, x.claim_id)):
        level, gate = c.value.get("level"), c.value.get("gate")
        if (c.target_type == "node" and c.template_key == ref.key and c.template_version == ref.version and _claim_live(c, inp.as_of)
                and "student" in c.visibility and "guardian" in c.visibility and c.source_type != "guardian"
                and c.subject in policy.visible_subjects and c.subject in SUBJECTS and NODE_ID.fullmatch(c.target_id)
                and isinstance(level, str) and gate in GAP_GATES and _renders(c, forbidden)):
            out.setdefault((c.subject, c.target_id, level), c)
    return out


def _focus_subjects(goals: Sequence[Goal]) -> frozenset[str]:
    return frozenset(s for g in goals for s in g.focus_subjects)


def _priority(inp: MonthlyInputs, policy: Policy, cfg: MonthlyConfig, forbidden: Forbidden) -> tuple[list[dict], list[dict]]:
    """返回（学生段优先知识点, 排序依据）"""
    claims = _pending_claims(inp, policy, forbidden)
    focus = _focus_subjects(inp.goals)
    best: dict[tuple[str, str], tuple[float, MasteryRow, PlanClaim, dict]] = {}
    for r in sorted(inp.mastery, key=lambda x: (x.subject, x.node_id, x.cognitive_level, x.basis_id)):
        if r.gate not in GAP_GATES or r.subject not in policy.visible_subjects or r.subject not in SUBJECTS:
            continue
        c = claims.get((r.subject, r.node_id, r.cognitive_level))
        if c is None or c.value.get("gate") != r.gate:
            continue  # 没有可引用的“待点亮”判断，或判断与当前掌握不一致：不出这一项
        k = f"{r.subject}:{r.node_id}"
        gap = cfg.gate_gap[r.gate]
        weight = float(inp.weights.get(k, 1.0))
        freq = float(inp.frequencies.get(k, 1.0))
        ff = cfg.focus_factor if r.subject in focus else 1.0
        pr = round(gap * weight * freq * ff, 6)
        basis = {"subject": r.subject, "node_id": r.node_id, "cognitive_level": r.cognitive_level, "gate": r.gate, "gap": gap,
                 "weight": weight, "frequency": freq, "focus_factor": ff, "priority": pr}
        prev = best.get((r.subject, r.node_id))
        if prev is None or pr > prev[0]:
            best[(r.subject, r.node_id)] = (pr, r, c, basis)
    ranked = sorted(best.values(), key=lambda t: (-t[0], t[1].subject, t[1].node_id, t[1].cognitive_level))
    nodes = []
    for i, (_, r, c, _) in enumerate(ranked[:cfg.max_priority_nodes], start=1):
        nodes.append({"position": i, "subject": r.subject, "node_id": r.node_id, "cognitive_level": r.cognitive_level, "claim_id": c.claim_id,
                      "template_key": c.template_key, "template_version": c.template_version, "copy_key": COPY["priority_node"]})
    basis_items = [t[3] for t in ranked]
    return nodes, basis_items


def _retests(inp: MonthlyInputs) -> dict[str, Any]:
    start, end = month_window(inp.month)
    days = [r for r in inp.retests_due if inp.month <= r.day < end.date()]
    by: dict[str, int] = {}
    for r in days:
        by[r.subject] = by.get(r.subject, 0) + 1
    return {"count": len(days), "dates": sorted({r.day.isoformat() for r in days}),
            "by_subject": [{"subject": s, "count": n} for s, n in sorted(by.items())], "copy_key": COPY["retests"]}


def _mini_reviews(inp: MonthlyInputs, policy: Policy, cfg: MonthlyConfig) -> list[dict]:
    subjects = sorted({r.subject for r in inp.mastery if r.subject in policy.visible_subjects and r.subject in SUBJECTS})
    last = calendar.monthrange(inp.month.year, inp.month.month)[1]
    return [{"subject": s, "date": inp.month.replace(day=min(cfg.mini_review_day + i, last)).isoformat(), "copy_key": COPY["mini_review"]}
            for i, s in enumerate(subjects[:8])]


def _notes(facts: Mapping[str, Any], templates: TemplateLibrary, cfg: MonthlyConfig) -> list[dict]:
    out = []
    for t in templates.notes:
        ok = True
        for cond, v in t.when.items():
            fact = facts[NOTE_CONDITIONS[cond]]
            ok = ok and (fact is v if cond == "exam_in_month" else fact >= v)
        if ok:
            out.append({"key": t.key, "copy_key": t.copy_key, "vars": {k: facts[k] for k in t.vars}})
        if len(out) >= cfg.max_guardian_notes:
            break
    return out


def _last_month(inp: MonthlyInputs, policy: Policy, forbidden: Forbidden) -> dict[str, Any] | None:
    if inp.prev_plan is None:
        return None
    prev = previous_month(inp.month)
    start, end = month_window(prev)
    planned = [(n.get("subject"), n.get("node_id")) for n in inp.prev_plan.get("priority_nodes") or [] if isinstance(n, Mapping)]
    wanted = set(planned)
    ref = policy.templates["node_transfer"]
    lit: dict[tuple[str, str], PlanClaim] = {}
    for c in sorted(inp.claims, key=lambda x: (x.created_at, x.claim_id)):
        if (c.target_type == "node" and c.template_key == ref.key and c.template_version == ref.version and _claim_live(c, inp.as_of)
                and "guardian" in c.visibility and (c.subject, c.target_id) in wanted and c.value.get("gate") in STAR_GATES
                and c.entered_star_at is not None and start <= c.entered_star_at < end and _renders(c, forbidden)):
            lit.setdefault((c.subject or "", c.target_id), c)
    items = [{"subject": c.subject, "node_id": c.target_id, "claim_id": c.claim_id, "template_key": c.template_key,
              "template_version": c.template_version, "copy_key": COPY["lit"]}
             for _, c in sorted(lit.items())]
    # 只有引用迁移判断的点亮项与“上月计划节点数”这一计划事实；不给任何不引用判断的关于学生的计数（规则 11）
    return {"month": prev.isoformat(), "planned_nodes": min(len(planned), 20), "lit": items[:20], "copy_key": COPY["last_month"]}


def _score_gaps(inp: MonthlyInputs, cfg: MonthlyConfig, forbidden: Forbidden) -> list[dict]:
    latest = {s.subject: s for s in inp.latest_scores}
    exam_claims: dict[str, PlanClaim] = {}
    for c in sorted(inp.claims, key=lambda x: (x.created_at, x.claim_id)):
        if c.target_type == "exam" and _claim_live(c, inp.as_of) and "guardian" in c.visibility and _renders(c, forbidden):
            exam_claims.setdefault(c.target_id, c)
    out = []
    for g in sorted(inp.goals, key=lambda x: x.goal_id):
        for t in sorted(g.target_scores, key=lambda x: x.subject):
            s = latest.get(t.subject)
            if s is None or s.exam_type not in EXAM_TYPES or round(s.full_score, 1) != round(t.full_score, 1):
                continue  # 没有成绩、或不是同一满分口径：不比
            c = exam_claims.get(s.record_id)
            if c is None:
                continue  # 没有对家长可见的考试判断引用这条成绩：不出（规则 11）
            # 只存引用（成绩记录、诉求版本、所引考试判断），不存成绩与目标分的值；家长视图读时现算、按 5 分一档给区间
            out.append({"subject": t.subject, "goal_id": g.goal_id, "exam_record_id": s.record_id, "claim_id": c.claim_id,
                        "template_key": c.template_key, "template_version": c.template_version, "copy_key": COPY["score_gap"]})
    return out[:cfg.max_score_gaps]


def build_monthly_plan(inp: MonthlyInputs, policy: Policy, cfg: MonthlyConfig, templates: TemplateLibrary,
                       forbidden: Forbidden) -> dict[str, Any]:
    """生成一份月度计划（落库用的结构，与 0054 insight.save_monthly_plan 的 p_plan 同形状）"""
    month_window(inp.month)  # 校验月份
    nodes, basis = _priority(inp, policy, cfg, forbidden)
    retests = _retests(inp)
    minis = _mini_reviews(inp, policy, cfg)
    student_plan = {
        "priority_nodes": nodes,
        "weekly_rhythm": {"sessions_per_week": cfg.sessions_per_week, "minutes_per_session": cfg.minutes_per_session,
                          "copy_key": COPY["weekly_rhythm"]},
        "retests": retests,
        "mini_reviews": minis,
    }
    facts = {"retest_count": retests["count"], "sessions_per_week": cfg.sessions_per_week, "minutes_per_session": cfg.minutes_per_session,
             "exam_in_month": len(inp.exam_plans) > 0, "exam_count": min(9999, len(inp.exam_plans)),
             "mini_review_count": len(minis), "priority_count": len(nodes)}
    return {
        "student_plan": student_plan,
        "guardian_notes": _notes(facts, templates, cfg),
        "review": {"last_month": _last_month(inp, policy, forbidden), "score_gaps": _score_gaps(inp, cfg, forbidden)},
        # 时点审计记录：只落库，任何视图都不返回，任何计算（含下月的生成）都不读取
        "plan_basis": {"items": basis[:200]},
        "input_refs": input_refs(inp, fingerprints(policy, cfg, templates)),
        "algorithm_version": ALGORITHM_VERSION,
        "config_version": f"{policy.config_version}_{cfg.config_version}",
        "template_version": templates.template_version,
        "policy_weights": "published" if (inp.weights or inp.frequencies) else "default",
        "inputs_digest": inputs_digest(inp),
    }


def claim_ids(plan: Mapping[str, Any]) -> list[str]:
    """快照里引用的全部判断 ID（按出现顺序去重）"""
    ids = [n["claim_id"] for n in plan["student_plan"]["priority_nodes"]]
    lm = plan["review"]["last_month"]
    if lm:
        ids += [x["claim_id"] for x in lm["lit"]]
    ids += [x["claim_id"] for x in plan["review"]["score_gaps"]]
    return list(dict.fromkeys(ids))


def student_section(plan: Mapping[str, Any]) -> dict[str, Any]:
    """学生能看到的部分（只有 student_plan）：与 0054 学生视图同一口径，测试用它断言学生段不含家长诉求相关的任何字段"""
    return {"student_plan": json.loads(to_json(plan["student_plan"]))}
