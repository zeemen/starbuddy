"""SIE-03 配置（config/sie03.synthetic.json）：异议、考前预测与对账、触发规则 v1、导出与删除的参数与占位文案。

与判断策略（policy.py）同一套做法：只校验形状与方向，不规定签字数值。
- 标了 synthetic 的配置只允许 INSIGHT_ENV=test 加载；未签字（signed_by / signed_at 为空或 synthetic）时触发规则只生成建议、不下发；
- copy 下的全部文字（异议回应、偏差方向、平台调整、触发动作说明）都是占位（必停项：人写人审），加载时连同各编码一起
  过 SIE-00 禁用推断词表，命中即拒绝加载（阻断）；
- 编码集合与迁移 0032–0035、契约 v0.9 的枚举逐一对应（有测试核对）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .policy import CONFIG_DIR, CONFIG_VERSION, TEMPLATE_KEY, TEST_ENV, PolicyError, TemplateRef, _int, _keys, _need, _num, _text
from .shared import GATES, Forbidden, forbidden_matcher

SCHEMA_VERSION = "sie-03-policy/0.1"
AUDIENCE_ORDER = ("agent", "student", "guardian")
# 契约 InsightDisputeResponse.next_step
NEXT_STEPS = ("verification_scheduled", "observe_followups", "open_fact_for_correction")
# 契约 deviation_directions 与 0034 exam_reconciliation 的四方向
DIRECTIONS = ("item_type_transfer", "time_shortage", "calculation_slip", "diagnosis_optimistic")
# 契约 adjustments 与 0034 的编码
ADJUSTMENTS = ("claims_to_verify", "retest_scheduled", "interval_widened", "model_parameter_adjusted", "none")
# 0033 intervention_log.action
ACTIONS = ("plan_review_node", "plan_verification_item", "encourage_with_evidence", "shorten_task", "insert_familiar_item", "ask_reason")
# 0035 registry.erase_scope 与契约 InsightEraseResponse.scopes
ERASE_SCOPES = ("claim_ledger", "facts", "evidence", "inference", "reports", "uploads")
PARAMS_VERSION = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,31}@[0-9]+\.[0-9]+\.[0-9]+$")  # 0033 params_version
METRIC_KEYS = ("independent_solve_rate", "hint_dependency", "transfer_gap", "self_assessment_calibration", "retention_rate",
               "plan_completion_rate", "reading_question_error_share", "persistence")


@dataclass(frozen=True)
class DisputeParams:
    verification_items: int
    verify_within_hours: int
    one_off_slip_observe_days: int


@dataclass(frozen=True)
class IntervalParams:
    level: float
    z: float
    prior_p: float
    prior_strength: float
    base_sd: float
    gate_p: Mapping[str, float]


@dataclass(frozen=True)
class ExamParams:
    prediction_template: TemplateRef
    reconcile_template: TemplateRef
    prediction_visibility: tuple[str, ...]
    reconcile_visibility: tuple[str, ...]
    card_audience: tuple[str, ...]
    ttl_days_after_exam: int
    response_max_score: int
    scales: Mapping[int, str]
    interval: IntervalParams


@dataclass(frozen=True)
class TriggerParams:
    params_version: str
    dedup_days: int
    review_due_within_days: int
    encourage_lookback_days: int
    break_days: int
    exit_after_errors_window_days: int
    exit_after_errors_min: int
    reduce_load_metric_keys: tuple[str, ...]


@dataclass(frozen=True)
class Sie03Policy:
    config_version: str
    synthetic: bool
    signed: bool
    dispute: DisputeParams
    exam: ExamParams
    triggers: TriggerParams
    copy: Mapping[str, Mapping[str, str]]
    erase_scopes: tuple[str, ...]
    fingerprint: str


def _audiences(v: Any, where: str, allowed: tuple[str, ...] = AUDIENCE_ORDER) -> tuple[str, ...]:
    _need(isinstance(v, list) and v and set(v) <= set(allowed) and len(set(v)) == len(v), f"{where} 只能是 {allowed} 的非空子集")
    return tuple(a for a in allowed if a in v)


def _tpl(t: Any, where: str) -> TemplateRef:
    _keys(t, {"key", "version"}, where)
    _need(isinstance(t["key"], str) and bool(TEMPLATE_KEY.fullmatch(t["key"])), f"{where}.key 格式不对")
    return TemplateRef(t["key"], _int(t["version"], f"{where}.version"))


def _codes_section(doc: Any, codes: tuple[str, ...], where: str) -> dict[str, str]:
    _keys(doc, set(codes), where)
    return {c: _text(doc[c], f"{where}.{c}", 60) for c in codes}


def check_copy(policy: "Sie03Policy | Mapping[str, Mapping[str, str]]", forbidden: Forbidden) -> list[str]:
    """占位文案与编码逐条过禁用推断词表；返回命中的位置（空表示通过）。编码也查（英文词表覆盖 careless、lazy 等）"""
    copy = policy.copy if isinstance(policy, Sie03Policy) else policy
    hits = []
    for section, entries in sorted(copy.items()):
        for code, text in sorted(entries.items()):
            if forbidden(f"{code}\n{code.replace('_', ' ')}\n{text}"):
                hits.append(f"{section}.{code}")
    return hits


def parse_sie03(doc: Mapping[str, Any], *, env: str, forbidden: Forbidden | None = None) -> Sie03Policy:
    top = {"schema_version", "config_version", "synthetic", "signed_by", "signed_at", "dispute", "exam", "triggers", "copy",
           "erase_scopes"}
    _keys(doc, top, "SIE-03 配置", frozenset({"$comment"}))
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and bool(CONFIG_VERSION.fullmatch(doc["config_version"])), "config_version 格式不对")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    _need(isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str), "signed_by / signed_at 必须是字符串")
    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]
    if doc["synthetic"] and env != TEST_ENV:
        raise PolicyError("合成占位的 SIE-03 配置只允许 INSIGHT_ENV=test 加载（待人确认 #6、#7）")

    d = doc["dispute"]
    _keys(d, {"verification_items", "verify_within_hours", "one_off_slip_observe_days"}, "dispute")
    # 0032：验证任务 1–5 道；契约 verification_item_refs 最多 5；SIE 第八章默认 2 道（含 1 道变式）
    dispute = DisputeParams(_int(d["verification_items"], "dispute.verification_items", 1, 5),
                            _int(d["verify_within_hours"], "dispute.verify_within_hours", 1, 24),  # 指标：24 小时内推送
                            _int(d["one_off_slip_observe_days"], "dispute.one_off_slip_observe_days", 1, 60))

    e = doc["exam"]
    _keys(e, {"templates", "prediction_visibility", "reconcile_visibility", "card_audience", "ttl_days_after_exam",
              "response_max_score", "scales", "interval"}, "exam")
    _keys(e["templates"], {"prediction", "reconcile"}, "exam.templates")
    pred_t, rec_t = _tpl(e["templates"]["prediction"], "exam.templates.prediction"), _tpl(e["templates"]["reconcile"], "exam.templates.reconcile")
    _need(pred_t.key != rec_t.key, "预测与对账不能共用同一个判断模板")
    pred_vis = _audiences(e["prediction_visibility"], "exam.prediction_visibility")
    rec_vis = _audiences(e["reconcile_visibility"], "exam.reconcile_visibility")
    card_aud = _audiences(e["card_audience"], "exam.card_audience", ("student", "guardian"))  # 0034 audience 只收学生、家长
    _need(set(card_aud) <= set(rec_vis), "对账卡的读者必须在对账判断的可见范围内（规则 11）")
    scales_doc = e["scales"]
    _need(isinstance(scales_doc, dict) and scales_doc, "exam.scales 必须是非空对象")
    scales: dict[int, str] = {}
    for k, v in scales_doc.items():
        _need(isinstance(k, str) and k.isdigit() and 1 <= int(k) <= 300, "exam.scales 的键必须是 1–300 的满分")
        _need(isinstance(v, str) and bool(re.fullmatch(r"[a-z][a-z0-9_]{1,31}", v)), "exam.scales 的值必须是编码")
        scales[int(k)] = v
    iv = e["interval"]
    _keys(iv, {"level", "z", "prior_p", "prior_strength", "base_sd", "gate_p"}, "exam.interval")
    _need(iv["level"] == 0.8, "exam.interval.level 固定 0.8（0034 level、契约 interval_level）")
    _keys(iv["gate_p"], set(GATES), "exam.interval.gate_p")
    gate_p = {g: _num(iv["gate_p"][g], f"gate_p.{g}") for g in GATES}
    _need(all(gate_p[a] <= gate_p[b] for a, b in zip(GATES, GATES[1:])), "gate_p 必须随门槛单调不减")
    interval = IntervalParams(0.8, _num(iv["z"], "exam.interval.z", 0.5, 3.0), _num(iv["prior_p"], "exam.interval.prior_p"),
                              _num(iv["prior_strength"], "exam.interval.prior_strength", 0.5, 100.0),
                              _num(iv["base_sd"], "exam.interval.base_sd", 0.0, 0.5), gate_p)
    exam = ExamParams(pred_t, rec_t, pred_vis, rec_vis, card_aud, _int(e["ttl_days_after_exam"], "exam.ttl_days_after_exam", 1, 365),
                      _int(e["response_max_score"], "exam.response_max_score", 1, 300), scales, interval)

    t = doc["triggers"]
    _keys(t, {"params_version", "dedup_days", "review_due_within_days", "encourage_lookback_days", "break_days",
              "exit_after_errors_window_days", "exit_after_errors_min", "reduce_load_metric_keys"}, "triggers")
    _need(isinstance(t["params_version"], str) and bool(PARAMS_VERSION.fullmatch(t["params_version"])),
          "triggers.params_version 格式不对（0033 params_version）")
    keys = t["reduce_load_metric_keys"]
    _need(isinstance(keys, list) and keys and set(keys) <= set(METRIC_KEYS) and len(set(keys)) == len(keys),
          "triggers.reduce_load_metric_keys 只能是中圈指标键")
    triggers = TriggerParams(t["params_version"], _int(t["dedup_days"], "triggers.dedup_days", 1, 30),
                             _int(t["review_due_within_days"], "triggers.review_due_within_days", 1, 30),
                             _int(t["encourage_lookback_days"], "triggers.encourage_lookback_days", 1, 60),
                             _int(t["break_days"], "triggers.break_days", 1, 60),
                             _int(t["exit_after_errors_window_days"], "triggers.exit_after_errors_window_days", 1, 60),
                             _int(t["exit_after_errors_min"], "triggers.exit_after_errors_min", 1, 100), tuple(keys))

    c = doc["copy"]
    _keys(c, {"next_step", "direction", "adjustment", "action"}, "copy")
    copy = {"next_step": _codes_section(c["next_step"], NEXT_STEPS, "copy.next_step"),
            "direction": _codes_section(c["direction"], DIRECTIONS, "copy.direction"),
            "adjustment": _codes_section(c["adjustment"], ADJUSTMENTS, "copy.adjustment"),
            "action": _codes_section(c["action"], ACTIONS, "copy.action")}
    hits = check_copy(copy, forbidden or forbidden_matcher())
    _need(not hits, f"SIE-03 占位文案或编码命中禁用推断词表：{hits}")

    scopes = doc["erase_scopes"]
    _need(isinstance(scopes, list) and scopes and set(scopes) <= set(ERASE_SCOPES) and len(set(scopes)) == len(scopes),
          "erase_scopes 只能是契约 scopes 的非空子集")

    fp = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return Sie03Policy(doc["config_version"], doc["synthetic"], signed, dispute, exam, triggers, copy,
                       tuple(s for s in ERASE_SCOPES if s in scopes), fp)


def load_sie03(path: str | Path, *, env: str, forbidden: Forbidden | None = None) -> Sie03Policy:
    with open(path, encoding="utf-8") as f:
        return parse_sie03(json.load(f), env=env, forbidden=forbidden)


def synthetic_sie03() -> Sie03Policy:
    return load_sie03(CONFIG_DIR / "sie03.synthetic.json", env=TEST_ENV)
