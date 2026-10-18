"""配置 config.json：加载、形状与取值校验、配置指纹。口径、参数界、先验与防泄露参数集中在这一份，全部“待教研确认”（spec 待人确认 1–3）。

形状校验的小函数与配置错误类型复用 kge-calibrate（同一口径）；人数门槛下限复用它的 MIN_ITEM_ATTEMPTS_FLOOR / MIN_CELL_FLOOR
（KGE“同一题少于 20 人不展示得分率”；调低是隐私口径变更，须由人决定后改代码）。

现行掌握估计基线的数值（BKT 参数、证据权重、来源权重、错因系数）不在本配置里抄写：从 baseline.source 所指的学习服务掌握配置读取
（单一真相源，只读）。证据口径固定为独立作答、A 级、日常练习来源、默认错因系数（与 KGE-09 作答导出口径一致）。
输入不是合成样例时，该掌握配置必须已签字且 synthetic=false（require_signed_baseline）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from kge_calibrate.config import MIN_CELL_FLOOR, MIN_ITEM_ATTEMPTS_FLOOR, PENDING, ConfigError, _int, _keys, _need, _num

from . import _REPO

SCHEMA_VERSION = "kge-irt-config/0.2"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
BLOCK_UNITS = ("node",)
EVIDENCE_KIND, EVIDENCE_LEVEL, SOURCE_METHOD = "independent", "A", "practice"
__all__ = ["CONFIG_PATH", "PENDING", "ConfigError", "Config", "BaselineRef", "load_config", "parse_config", "require_signed_baseline"]


@dataclass(frozen=True)
class FitConfig:
    quadrature_points: int
    max_iterations: int
    tolerance: float
    m_step_iterations: int
    b_lo: float
    b_hi: float
    a_lo: float
    a_hi: float
    b_prior_sd: float
    log_a_prior_mean: float
    log_a_prior_sd: float
    slope_prior_sd: float
    min_block_students: int


@dataclass(frozen=True)
class BaselineRef:
    """学习服务掌握配置里基线要用的数值（只读）。强度 = |证据权重| × 等级权重 × 来源权重 × 错因系数（gates.py 同一公式）"""
    source: str
    config_version: str
    synthetic: bool
    signed: bool
    p_init: float
    p_learn: float
    p_slip: float
    p_guess_default: float
    p_guess_by_type: Mapping[str, float] = field(default_factory=dict)
    w_correct: float = 0.0          # evidence_weights.independent
    w_wrong: float = 0.0            # evidence_weights.independent_wrong（负数）
    level_w: float = 1.0            # A 级 = 1（B 级才乘 level_weight_B）
    source_w: float = 1.0           # source_weights.practice
    penalty: float = 1.0            # error_penalty.default

    def p_guess(self, item_type: str | None) -> float:
        """与 learning.mastery.config.MasteryConfig.p_guess 同一取法"""
        return self.p_guess_by_type.get(item_type or "", self.p_guess_default)

    def strength(self, correct: bool) -> float:
        if correct:
            return abs(self.w_correct) * self.level_w * self.source_w
        return abs(self.w_wrong) * self.level_w * self.source_w * self.penalty


@dataclass(frozen=True)
class Config:
    config_version: str
    signoff: str
    fingerprint: str
    min_fit_fraction: float
    max_fit_fraction: float
    synthetic_fit_fraction: float
    fit: FitConfig
    bootstrap_draws: int
    bootstrap_seed: int
    bootstrap_confidence: float
    bootstrap_max_iterations: int
    min_respondents: int
    min_outcome_cell: int
    difficulty_step: float
    discrimination_step: float
    min_eval_students: int
    prob_clip: float
    bkt: BaselineRef
    compare_draws: int
    compare_seed: int
    min_logloss_gain: float
    min_items_for_rank: int
    prior_mismatch_logits: float
    near_bound_steps: int
    max_difficulty_mae: float
    max_discrimination_mae: float
    max_false_prior_mismatch: int

    @property
    def baseline_source(self) -> str:
        return self.bkt.source

    def provenance(self) -> dict[str, Any]:
        return {"config_version": self.config_version, "fingerprint": self.fingerprint, "signoff": self.signoff,
                "baseline_config": {"source": self.bkt.source, "config_version": self.bkt.config_version,
                                    "synthetic": self.bkt.synthetic, "signed": self.bkt.signed}}


def _bounds(v: Any, where: str, lo: float, hi: float) -> tuple[float, float]:
    _need(isinstance(v, list) and len(v) == 2, f"{where} 必须是 [下界, 上界]")
    a, b = _num(v[0], where, lo, hi), _num(v[1], where, lo, hi)
    _need(a < b, f"{where} 下界必须小于上界")
    return a, b


def _prob(v: Any, where: str, hi_closed: bool = False) -> float:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0.0 < v and (v <= 1.0 if hi_closed else v < 1.0)
    _need(ok, f"掌握配置 {where} 必须是 (0, 1{']' if hi_closed else ')'} 内的数（未填写的草案配置不能用作基线）")
    return float(v)


def parse_baseline_ref(source: str, doc: Any) -> BaselineRef:
    _need(isinstance(doc, dict), "掌握配置必须是对象")
    try:
        b, w, s, pen = doc["bkt"], doc["evidence_weights"], doc["source_weights"], doc["error_penalty"]
        g = b["p_guess"]
        by_type = g["by_item_type"]
        _need(isinstance(by_type, dict), "掌握配置 bkt.p_guess.by_item_type 必须是对象")
        wrong = w["independent_wrong"]
        _need(isinstance(wrong, (int, float)) and not isinstance(wrong, bool), "掌握配置 evidence_weights.independent_wrong 必须是数")
        ref = BaselineRef(source, str(doc["config_version"]), doc["synthetic"] is True,
                          bool(doc.get("signed_by")) and bool(doc.get("signed_at")),
                          _prob(b["p_init"], "bkt.p_init"), _prob(b["p_learn"], "bkt.p_learn"), _prob(b["p_slip"], "bkt.p_slip"),
                          _prob(g["default"], "bkt.p_guess.default"),
                          {str(k): _prob(v, f"bkt.p_guess.by_item_type.{k}") for k, v in sorted(by_type.items())},
                          _prob(w["independent"], "evidence_weights.independent", True),
                          -_prob(-wrong, "evidence_weights.independent_wrong（取负）", True),
                          1.0, _prob(s["practice"], "source_weights.practice", True), _prob(pen["default"], "error_penalty.default", True))
    except (KeyError, TypeError):
        raise ConfigError("掌握配置缺少 bkt / evidence_weights / source_weights / error_penalty 的必要字段") from None
    _need(all(ref.p_slip + v < 1 for v in (ref.p_guess_default, *ref.p_guess_by_type.values())), "掌握配置：p_slip + p_guess 必须小于 1")
    return ref


def load_baseline_ref(source: str, repo: Path = _REPO) -> BaselineRef:
    path = repo / source
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        raise ConfigError("baseline.source 所指的掌握配置读不了或不是合法 JSON") from None
    return parse_baseline_ref(source, doc)


def require_signed_baseline(cfg: Config, synthetic_inputs: bool) -> None:
    """非合成输入：基线所用掌握配置必须已签字且 synthetic=false（契约建议 10）"""
    if not synthetic_inputs:
        _need(cfg.bkt.signed and not cfg.bkt.synthetic,
              "输入不是合成样例：baseline.source 必须是已签字（signed_by、signed_at）且 synthetic=false 的掌握配置")


def parse_config(doc: Mapping[str, Any], repo: Path = _REPO) -> Config:
    _keys(doc, {"schema_version", "config_version", "signoff", "split", "blocking", "fit", "bootstrap", "disclosure", "baseline",
                "comparison", "synthetic_acceptance"}, "IRT 配置")
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and 0 < len(doc["config_version"]) <= 32
          and all(ch.isascii() and (ch.isalnum() or ch in "._-") for ch in doc["config_version"]), "config_version 格式不对")
    _need(isinstance(doc["signoff"], str) and doc["signoff"].strip() != "", f"signoff 必须写明（未确认写“{PENDING}”）")

    sp = doc["split"]
    _keys(sp, {"min_fit_fraction", "max_fit_fraction", "synthetic_fit_fraction"}, "split")
    lo_f = _num(sp["min_fit_fraction"], "split.min_fit_fraction", 0.1, 0.95)
    hi_f = _num(sp["max_fit_fraction"], "split.max_fit_fraction", 0.1, 0.95)
    syn_f = _num(sp["synthetic_fit_fraction"], "split.synthetic_fit_fraction", 0.1, 0.95)
    _need(lo_f < hi_f and lo_f <= syn_f <= hi_f, "split：须 min_fit_fraction < max_fit_fraction，合成比例在两者之间")

    bl = doc["blocking"]
    _keys(bl, {"unit", "share_family_parameters"}, "blocking")
    _need(bl["unit"] in BLOCK_UNITS, f"blocking.unit 本步只支持 {BLOCK_UNITS}（主题、学科分块需要地图快照，另起）")
    _need(bl["share_family_parameters"] is False, "blocking.share_family_parameters 本步只能是 false（题目族内不共享参数）")

    f = doc["fit"]
    _keys(f, {"quadrature_points", "max_iterations", "tolerance", "m_step_iterations", "difficulty_bounds", "discrimination_bounds",
              "difficulty_prior_sd", "log_discrimination_prior_mean", "log_discrimination_prior_sd", "prior_slope_sd",
              "min_block_students"}, "fit")
    b_lo, b_hi = _bounds(f["difficulty_bounds"], "fit.difficulty_bounds", -10.0, 10.0)
    a_lo, a_hi = _bounds(f["discrimination_bounds"], "fit.discrimination_bounds", 0.05, 10.0)
    fit = FitConfig(_int(f["quadrature_points"], "fit.quadrature_points", 5, 61),
                    _int(f["max_iterations"], "fit.max_iterations", 10, 5000),
                    _num(f["tolerance"], "fit.tolerance", 1e-8, 0.01),
                    _int(f["m_step_iterations"], "fit.m_step_iterations", 1, 50),
                    b_lo, b_hi, a_lo, a_hi,
                    _num(f["difficulty_prior_sd"], "fit.difficulty_prior_sd", 0.5, 10.0),
                    _num(f["log_discrimination_prior_mean"], "fit.log_discrimination_prior_mean", -1.0, 1.0),
                    _num(f["log_discrimination_prior_sd"], "fit.log_discrimination_prior_sd", 0.1, 2.0),
                    _num(f["prior_slope_sd"], "fit.prior_slope_sd", 0.5, 20.0),
                    _int(f["min_block_students"], "fit.min_block_students", MIN_CELL_FLOOR, 100_000))
    _need(b_lo < 0 < b_hi and a_lo < 1 < a_hi, "fit：参数界必须包含 b = 0 与 a = 1")

    bs = doc["bootstrap"]
    _keys(bs, {"draws", "seed", "confidence", "max_iterations"}, "bootstrap")
    di = doc["disclosure"]
    _keys(di, {"min_respondents", "min_outcome_cell", "difficulty_step", "discrimination_step", "min_eval_students", "prob_clip"},
          "disclosure")
    ba = doc["baseline"]
    _keys(ba, {"source"}, "baseline")
    _need(isinstance(ba["source"], str) and ba["source"].startswith("services/learning/config/mastery/") and ba["source"].endswith(".json")
          and ".." not in ba["source"], "baseline.source 必须指向学习服务的掌握配置文件")
    co = doc["comparison"]
    _keys(co, {"bootstrap_draws", "bootstrap_seed", "min_logloss_gain", "min_items_for_rank", "prior_mismatch_logits", "near_bound_steps"},
          "comparison")
    ac = doc["synthetic_acceptance"]
    _keys(ac, {"max_difficulty_mae", "max_discrimination_mae", "max_false_prior_mismatch"}, "synthetic_acceptance")

    fp = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return Config(doc["config_version"], doc["signoff"].strip(), fp, lo_f, hi_f, syn_f, fit,
                  _int(bs["draws"], "bootstrap.draws", 10, 2000), _int(bs["seed"], "bootstrap.seed", 0, 2**31 - 1),
                  _num(bs["confidence"], "bootstrap.confidence", 0.5, 0.99), _int(bs["max_iterations"], "bootstrap.max_iterations", 5, 1000),
                  _int(di["min_respondents"], "disclosure.min_respondents", MIN_ITEM_ATTEMPTS_FLOOR, 100_000),
                  _int(di["min_outcome_cell"], "disclosure.min_outcome_cell", MIN_CELL_FLOOR, 100_000),
                  _num(di["difficulty_step"], "disclosure.difficulty_step", 0.05, 1.0),
                  _num(di["discrimination_step"], "disclosure.discrimination_step", 0.05, 1.0),
                  _int(di["min_eval_students"], "disclosure.min_eval_students", MIN_CELL_FLOOR, 100_000),
                  _num(di["prob_clip"], "disclosure.prob_clip", 0.001, 0.1),
                  load_baseline_ref(ba["source"], repo),
                  _int(co["bootstrap_draws"], "comparison.bootstrap_draws", 50, 5000),
                  _int(co["bootstrap_seed"], "comparison.bootstrap_seed", 0, 2**31 - 1),
                  _num(co["min_logloss_gain"], "comparison.min_logloss_gain", 0.0, 1.0),
                  _int(co["min_items_for_rank"], "comparison.min_items_for_rank", 3, 1000),
                  _num(co["prior_mismatch_logits"], "comparison.prior_mismatch_logits", 0.2, 5.0),
                  _int(co["near_bound_steps"], "comparison.near_bound_steps", 0, 10),
                  _num(ac["max_difficulty_mae"], "synthetic_acceptance.max_difficulty_mae", 0.01, 2.0),
                  _num(ac["max_discrimination_mae"], "synthetic_acceptance.max_discrimination_mae", 0.01, 2.0),
                  _int(ac["max_false_prior_mismatch"], "synthetic_acceptance.max_false_prior_mismatch", 0, 1000))


def load_config(path: str | Path = CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as fh:
        return parse_config(json.load(fh))
