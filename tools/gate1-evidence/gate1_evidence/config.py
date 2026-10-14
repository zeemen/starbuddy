"""未签字配置 config.unsigned.json：加载、形状与取值校验、配置指纹。

本模块只校验形状与方向，不规定任何签字数值（每一节 signoff 必须是“待签字”或签字记录）；报表输出带配置指纹。
分组键只能是合成队列提供的非敏感键，且不得命中地区、学校、家庭等词（规则 12）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "gate1-evidence-config/0.1"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.unsigned.json"
SECTIONS = ("retest_g12", "exam_g13", "validity_g14", "fairness")
UNSIGNED = "待签字"
# 洞察服务 metrics.py 的全部 8 个静默指标（SIE-05：效度检验覆盖全部）；顺序即报表顺序
VALIDITY_METRICS = ("independent_solve_rate", "hint_dependency", "transfer_gap", "retention_rate", "self_assessment_calibration",
                    "plan_completion_rate", "reading_question_error_share", "persistence")
OUTCOMES = ("week8_retest_accuracy", "last_exam_fraction")
DIRECTIONS = ("positive", "negative")
# 合成队列提供的分组键（cohort.Student 的字段）；真实试点的分组键待人确认
SYNTHETIC_GROUP_KEYS = ("wave", "grade")
FORBIDDEN_GROUP_KEY = re.compile(
    r"region|district|city|province|area|school|class|family|household|guardian|parent|income|gender|sex|ethnic|address|phone|name"
    r"|地区|区域|学校|班级|家庭|家长|收入|性别|民族|住址|姓名",
    re.IGNORECASE)


class ConfigError(ValueError):
    """配置不合法：拒绝生成证据包"""


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ConfigError(msg)


def _keys(doc: Any, required: set[str], where: str) -> None:
    _need(isinstance(doc, dict), f"{where} 必须是对象")
    extra = set(doc) - required - {"$comment"}
    missing = required - set(doc)
    _need(not extra and not missing, f"{where} 的配置位不对（多出 {sorted(extra)}，缺少 {sorted(missing)}）")


def _num(v: Any, where: str, lo: float = 0.0, hi: float = 1.0) -> float:
    _need(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi,
          f"{where} 必须是 [{lo}, {hi}] 内的数")
    return float(v)


def _int(v: Any, where: str, lo: int = 1, hi: int = 10_000) -> int:
    _need(isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi, f"{where} 必须是 [{lo}, {hi}] 内的整数")
    return int(v)


@dataclass(frozen=True)
class RetestCfg:
    min_mean_delta: float
    min_share_improved: float
    min_items_per_round: int
    min_paired_students: int
    continuation_after_days: int
    continuation_window_days: int
    continuation_min_rate: float
    min_enrolled_students: int


@dataclass(frozen=True)
class ExamCfg:
    nominal: float
    coverage_low: float
    coverage_high: float
    min_records_per_round: int
    min_students_per_round: int
    min_rounds: int
    min_mae_relative_drop: float


@dataclass(frozen=True)
class ValidityCfg:
    measure_week: int
    min_students: int    # 按学生聚合后的学生数
    min_abs_r: float
    min_metrics_passing: int
    metrics: Mapping[str, tuple[str, str]]  # 指标 → (结局, 预期方向)
    counted: frozenset[str]                 # 计入 Gate 1“至少 N 个指标通过”的指标（counts_toward_gate = true）；其余观察用


@dataclass(frozen=True)
class FairnessCfg:
    group_keys: tuple[str, ...]
    min_records_per_group: int
    min_students_per_group: int
    max_mae_gap: float
    max_bias_gap: float


@dataclass(frozen=True)
class Config:
    config_version: str
    signed: bool
    retest: RetestCfg
    exam: ExamCfg
    validity: ValidityCfg
    fairness: FairnessCfg
    fingerprint: str

    def provenance(self) -> dict[str, Any]:
        return {"config_version": self.config_version, "fingerprint": self.fingerprint,
                "signoff": "已签字" if self.signed else UNSIGNED}


def check_group_key(key: str) -> None:
    _need(isinstance(key, str) and key in SYNTHETIC_GROUP_KEYS and not FORBIDDEN_GROUP_KEY.search(key),
          "分组键只能是合成队列的非敏感键（不用地区、学校、家庭，规则 12）")


def parse_config(doc: Mapping[str, Any]) -> Config:
    _keys(doc, {"schema_version", "config_version", "signed_by", "signed_at", *SECTIONS}, "Gate 1 配置")
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,31}", doc["config_version"])),
          "config_version 格式不对")
    _need(isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str), "signed_by / signed_at 必须是字符串")
    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip())
    for s in SECTIONS:
        _need(isinstance(doc[s], dict) and isinstance(doc[s].get("signoff"), str) and doc[s]["signoff"].strip() != "",
              f"{s}.signoff 必须写明（未签字写“{UNSIGNED}”）")
        _need(signed or doc[s]["signoff"] == UNSIGNED, f"未签字的配置里 {s}.signoff 只能是“{UNSIGNED}”")
        _need(not signed or doc[s]["signoff"] != UNSIGNED, f"填了签字人与签字时间的配置里 {s}.signoff 不能还是“{UNSIGNED}”")

    r = doc["retest_g12"]
    _keys(r, {"signoff", "improvement", "min_items_per_round", "min_paired_students", "continuation", "min_enrolled_students"},
          "retest_g12")
    imp = r["improvement"]
    _keys(imp, {"definition", "min_mean_delta", "min_share_improved"}, "retest_g12.improvement")
    _need(imp["definition"] == "paired_mean_delta", "retest_g12.improvement.definition 目前只支持 paired_mean_delta")
    cont = r["continuation"]
    _keys(cont, {"after_days", "window_days", "min_rate"}, "retest_g12.continuation")
    retest = RetestCfg(_num(imp["min_mean_delta"], "min_mean_delta", -1.0, 1.0),
                       _num(imp["min_share_improved"], "min_share_improved"),
                       _int(r["min_items_per_round"], "min_items_per_round", 1, 200),
                       _int(r["min_paired_students"], "min_paired_students", 2),
                       _int(cont["after_days"], "continuation.after_days", 1, 365),
                       _int(cont["window_days"], "continuation.window_days", 1, 90),
                       _num(cont["min_rate"], "continuation.min_rate"),
                       _int(r["min_enrolled_students"], "min_enrolled_students", 2))

    e = doc["exam_g13"]
    _keys(e, {"signoff", "nominal", "coverage_low", "coverage_high", "min_records_per_round", "min_students_per_round",
              "min_rounds", "mae_unit", "min_mae_relative_drop"}, "exam_g13")
    _need(e["nominal"] == 0.8, "exam_g13.nominal 固定 0.8（预测区间为 80% 区间）")
    lo, hi = _num(e["coverage_low"], "coverage_low"), _num(e["coverage_high"], "coverage_high")
    _need(lo < 0.8 < hi, "覆盖率区间必须包住名义值 0.8")
    _need(e["mae_unit"] == "fraction_of_full_score", "exam_g13.mae_unit 目前只支持 fraction_of_full_score")
    exam = ExamCfg(0.8, lo, hi, _int(e["min_records_per_round"], "min_records_per_round", 1),
                   _int(e["min_students_per_round"], "min_students_per_round", 2), _int(e["min_rounds"], "min_rounds", 2, 12), _num(e["min_mae_relative_drop"], "min_mae_relative_drop"))

    v = doc["validity_g14"]
    _keys(v, {"signoff", "method", "measure_week", "min_students", "min_abs_r", "min_metrics_passing", "metrics"}, "validity_g14")
    _need(v["method"] == "pearson", "validity_g14.method 目前只支持 pearson")
    _need(isinstance(v["metrics"], dict) and v["metrics"] and set(v["metrics"]) <= set(VALIDITY_METRICS),
          f"validity_g14.metrics 只能是 {VALIDITY_METRICS} 的子集")
    ms: dict[str, tuple[str, str]] = {}
    counted: set[str] = set()
    for k in VALIDITY_METRICS:
        if k not in v["metrics"]:
            continue
        m = v["metrics"][k]
        _keys(m, {"outcome", "direction", "counts_toward_gate"}, f"validity_g14.metrics.{k}")   # 缺 counts_toward_gate 即拒绝
        _need(m["outcome"] in OUTCOMES and m["direction"] in DIRECTIONS, f"validity_g14.metrics.{k} 的结局或方向不对")
        _need(isinstance(m["counts_toward_gate"], bool), f"validity_g14.metrics.{k}.counts_toward_gate 必须是布尔值")
        ms[k] = (m["outcome"], m["direction"])
        if m["counts_toward_gate"]:
            counted.add(k)
    validity = ValidityCfg(_int(v["measure_week"], "measure_week", 2, 11), _int(v["min_students"], "min_students", 3),
                           _num(v["min_abs_r"], "min_abs_r"), _int(v["min_metrics_passing"], "min_metrics_passing", 1, 8), ms,
                           frozenset(counted))

    f = doc["fairness"]
    _keys(f, {"signoff", "group_keys", "min_records_per_group", "min_students_per_group", "max_mae_gap", "max_bias_gap"},
          "fairness")
    _need(isinstance(f["group_keys"], list) and f["group_keys"] and len(set(f["group_keys"])) == len(f["group_keys"]),
          "fairness.group_keys 必须是不重复的非空列表")
    for k in f["group_keys"]:
        check_group_key(k)
    fairness = FairnessCfg(tuple(f["group_keys"]), _int(f["min_records_per_group"], "min_records_per_group", 2),
                           _int(f["min_students_per_group"], "min_students_per_group", 2),
                           _num(f["max_mae_gap"], "max_mae_gap"), _num(f["max_bias_gap"], "max_bias_gap"))

    fp = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return Config(doc["config_version"], signed, retest, exam, validity, fairness, fp)


def load_config(path: str | Path = CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as fh:
        return parse_config(json.load(fh))
