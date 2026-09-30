"""判断生成策略配置：加载、结构与取值校验、签字状态、配置指纹（spec 设计要点 2、4、5；待人确认 #2、#3、#5、#6）。

本模块只校验“形状”与“方向”，不规定任何签字数值：
- 未签字（signed_by / signed_at 为空）的配置下判断最高只到“假设”（claims.py 检查 signed）；
- 标了 synthetic 的配置（合成占位）只允许 INSIGHT_ENV=test 加载；
- 报告的标题、顶部提示语、衔接短语在配置里只是占位，加载时过禁用推断词表检查（命中即拒绝加载）。
陈述—判断一致性检查的词表另放一个文件（learning.mastery.consistency 的格式），由同一个解析函数校验。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .shared import GATES, consistency, forbidden_matcher

SCHEMA_VERSION = "sie-claims-policy/0.1"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
TEST_ENV = "test"
CATEGORIES = ("node_mastery", "node_transfer", "metric")
NODE_CATEGORIES = ("node_mastery", "node_transfer")
SUBJECTS = ("aya", "zhixing", "wenli", "echo")
TEMPLATE_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$")  # 与 insight.claim_template.template_key 的约束一致
CONFIG_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")
METRIC_SAMPLE_KEYS = ("independent_solve_rate", "hint_dependency", "transfer_gap", "self_assessment_calibration",
                      "retention_rate", "plan_completion_rate_days", "reading_question_error_share_per_subject",
                      "reading_question_error_share_subjects", "persistence_weeks")
AUDIENCES = ("student", "guardian")


class PolicyError(ValueError):
    """配置不合法：拒绝加载（服务拒绝启动）"""


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise PolicyError(msg)


def _keys(doc: Any, required: set[str], where: str, optional: frozenset[str] = frozenset()) -> None:
    _need(isinstance(doc, dict), f"{where} 必须是对象")
    extra = set(doc) - required - optional
    missing = required - set(doc)
    _need(not extra and not missing, f"{where} 的配置位不对（多出 {sorted(extra)}，缺少 {sorted(missing)}）")


def _num(v: Any, where: str, lo: float = 0.0, hi: float = 1.0) -> float:
    _need(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi,
          f"{where} 必须是 [{lo}, {hi}] 内的数")
    return float(v)


def _int(v: Any, where: str, lo: int = 1, hi: int = 10_000) -> int:
    _need(isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi, f"{where} 必须是 [{lo}, {hi}] 内的整数")
    return int(v)


def _text(v: Any, where: str, max_len: int = 120) -> str:
    _need(isinstance(v, str) and 1 <= len(v.strip()) <= max_len, f"{where} 必须是 1–{max_len} 字的文本")
    return v


@dataclass(frozen=True)
class TemplateRef:
    key: str
    version: int


@dataclass(frozen=True)
class MetricPolicy:
    window_days: int
    min_samples: Mapping[str, int]
    hint_ladder_length: Mapping[str, int]
    probe_probability: Mapping[str, float]
    calibration_bias_epsilon: float
    break_days: int
    recover_days: int


@dataclass(frozen=True)
class ReportPolicy:
    notice: str
    titles: Mapping[str, str]
    connectives: Mapping[str, str]


@dataclass(frozen=True)
class Policy:
    config_version: str
    synthetic: bool
    signed: bool
    visible_subjects: tuple[str, ...]
    templates: Mapping[str, TemplateRef]
    mastery_gates: tuple[str, ...]
    establish_min_gate: str
    establish_confidence_min: float
    establish_evidence_min: int
    ttl_days: Mapping[str, int]
    band_moderate_min: float
    band_strong_min: float
    metrics: MetricPolicy
    report: ReportPolicy
    fingerprint: str

    def category_of(self, template_key: str) -> str | None:
        for cat, ref in self.templates.items():
            if ref.key == template_key:
                return cat
        return None

    def confidence_band(self, confidence: float) -> str:
        if confidence >= self.band_strong_min:
            return "strong"
        if confidence >= self.band_moderate_min:
            return "moderate"
        return "few_evidence"


def parse_policy(doc: Mapping[str, Any], *, env: str) -> Policy:
    top = {"schema_version", "config_version", "synthetic", "signed_by", "signed_at", "visible_subjects", "templates",
           "mastery_gates", "establish", "ttl_days", "confidence_bands", "metrics", "report"}
    _keys(doc, top, "判断策略配置", frozenset({"$comment"}))
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and bool(CONFIG_VERSION.fullmatch(doc["config_version"])), "config_version 格式不对")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    _need(isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str), "signed_by / signed_at 必须是字符串")
    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]
    if doc["synthetic"] and env != TEST_ENV:
        raise PolicyError("合成占位的判断策略配置只允许 INSIGHT_ENV=test 加载（待人确认 #2、#3）")

    vis = doc["visible_subjects"]
    _need(isinstance(vis, list) and set(vis) <= set(SUBJECTS) and len(set(vis)) == len(vis), "visible_subjects 只能是学科编码")

    _keys(doc["templates"], set(CATEGORIES), "templates")
    templates: dict[str, TemplateRef] = {}
    for cat in CATEGORIES:
        t = doc["templates"][cat]
        _keys(t, {"key", "version"}, f"templates.{cat}")
        _need(isinstance(t["key"], str) and bool(TEMPLATE_KEY.fullmatch(t["key"])), f"templates.{cat}.key 格式不对")
        templates[cat] = TemplateRef(t["key"], _int(t["version"], f"templates.{cat}.version"))
    _need(len({t.key for t in templates.values()}) == len(templates), "三类判断不能共用同一个模板键")

    gates = doc["mastery_gates"]
    _need(isinstance(gates, list) and gates and set(gates) <= set(GATES) - {"unseen"} and len(set(gates)) == len(gates),
          "mastery_gates 只能是 understand … retain 的子集（unseen 不生成判断）")

    est = doc["establish"]
    _keys(est, {"min_gate", "confidence_min", "evidence_min"}, "establish")
    _need(est["min_gate"] in GATES and est["min_gate"] != "unseen", "establish.min_gate 必须是门槛编码")

    ttl = doc["ttl_days"]
    _keys(ttl, set(CATEGORIES), "ttl_days")
    ttl_days = {c: _int(ttl[c], f"ttl_days.{c}", 1, 365) for c in CATEGORIES}  # 与模板 max_ttl 的范围一致

    bands = doc["confidence_bands"]
    _keys(bands, {"moderate_min", "strong_min"}, "confidence_bands")
    mod, strong = _num(bands["moderate_min"], "moderate_min"), _num(bands["strong_min"], "strong_min")
    _need(0 < mod < strong <= 1, "置信度分档必须 0 < moderate_min < strong_min ≤ 1")

    m = doc["metrics"]
    _keys(m, {"window_days", "min_samples", "hint_ladder_length", "probe_probability", "calibration_bias_epsilon",
              "persistence"}, "metrics")
    _keys(m["min_samples"], set(METRIC_SAMPLE_KEYS), "metrics.min_samples")
    samples = {k: _int(m["min_samples"][k], f"metrics.min_samples.{k}") for k in METRIC_SAMPLE_KEYS}
    _keys(m["hint_ladder_length"], set(SUBJECTS), "metrics.hint_ladder_length")
    ladders = {s: _int(m["hint_ladder_length"][s], f"hint_ladder_length.{s}", 1, 6) for s in SUBJECTS}  # attempt.hint_level_used 0–6
    _keys(m["probe_probability"], {"yes", "no", "unsure"}, "metrics.probe_probability")
    probs = {k: _num(m["probe_probability"][k], f"probe_probability.{k}") for k in ("yes", "no", "unsure")}
    _need(probs["no"] <= probs["unsure"] <= probs["yes"], "探针概率映射必须 no ≤ unsure ≤ yes")
    pers = m["persistence"]
    _keys(pers, {"break_days", "recover_days"}, "metrics.persistence")
    brk, rec = _int(pers["break_days"], "break_days", 1, 60), _int(pers["recover_days"], "recover_days", 1, 60)
    _need(brk <= rec, "persistence：恢复期限不能短于中断判定")
    metrics = MetricPolicy(_int(m["window_days"], "window_days", 1, 365), samples, ladders, probs,
                           _num(m["calibration_bias_epsilon"], "calibration_bias_epsilon", 0.0, 0.5), brk, rec)

    r = doc["report"]
    _keys(r, {"notice", "title", "connectives"}, "report")
    _keys(r["title"], set(AUDIENCES), "report.title")
    _keys(r["connectives"], set(NODE_CATEGORIES), "report.connectives")
    report = ReportPolicy(_text(r["notice"], "report.notice"),
                          {a: _text(r["title"][a], f"report.title.{a}") for a in AUDIENCES},
                          {c: _text(r["connectives"][c], f"report.connectives.{c}") for c in NODE_CATEGORIES})
    hits = forbidden_matcher()("\n".join([report.notice, *report.titles.values(), *report.connectives.values()]))
    _need(not hits, "报告占位文字命中禁用推断词表")

    fp = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return Policy(config_version=doc["config_version"], synthetic=doc["synthetic"], signed=signed, visible_subjects=tuple(vis),
                  templates=templates, mastery_gates=tuple(gates), establish_min_gate=est["min_gate"],
                  establish_confidence_min=_num(est["confidence_min"], "establish.confidence_min"),
                  establish_evidence_min=_int(est["evidence_min"], "establish.evidence_min"), ttl_days=ttl_days,
                  band_moderate_min=mod, band_strong_min=strong, metrics=metrics, report=report, fingerprint=fp)


def load_policy(path: str | Path, *, env: str) -> Policy:
    with open(path, encoding="utf-8") as f:
        return parse_policy(json.load(f), env=env)


def load_check_config(path: str | Path, *, env: str) -> "consistency.CheckConfig":
    """首份报告一致性检查的词表（与 KGE-04 档案页同一格式、同一校验；合成或未签字只允许 test 环境）"""
    try:
        return consistency.load_check_config(path, env=env)
    except consistency.ConfigError as e:
        raise PolicyError(str(e)) from None


def synthetic_policy() -> Policy:
    return load_policy(CONFIG_DIR / "claims.synthetic.json", env=TEST_ENV)


def synthetic_check_config() -> "consistency.CheckConfig":
    return load_check_config(CONFIG_DIR / "report-check.synthetic.json", env=TEST_ENV)
