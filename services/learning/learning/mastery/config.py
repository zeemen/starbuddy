"""掌握 v0 的门槛配置：加载、结构与取值校验、方向与顺序校验、签字状态、配置指纹（specs/mastery-estimate-sprint4/spec.md 第 3、5、12 节）。

本模块只校验“形状”与“方向”，不规定任何数值：门槛、证据权重、BKT、衰减、复测间隔的数值由各科教研签字（待人确认 #1）。
- 未签字（signed_by / signed_at 为空）或标了 synthetic 的配置只允许 LEARNING_ENV=test 加载；
- 未签字的配置在状态机里最高只能到“稳定”（gates.py 检查 signed）；
- 校验固定方向、不固定幅度：独立 > 低级提示 > 高级提示 = 0；迁移、延迟复测 ≥ 独立；独立做错 < 0；
  独立做错 ≤ 提示后做错 ≤ 0；B 级权重 < 1（低于对应 A 级）；平台诊断 > 日常练习；门槛逐级不降；复测间隔递增。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

ALGORITHM = "kge-mastery"
ALGORITHM_VERSION = "0.1.0"
SCHEMA_VERSION = "kge-mastery-config/0.1"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "mastery"

SUBJECTS = ("aya", "zhixing", "wenli", "echo")
COGNITIVE_LEVELS = ("know", "understand", "apply", "synthesize")  # 迁移 0011，由低到高
CROSS_SUBJECT_ERRORS = ("reading_the_question", "locating_information", "thinking_method", "computation_or_grammar",
                        "expression_convention", "strategy_and_time", "knowledge")  # 迁移 0001 platform.cross_subject_error
WEIGHT_KEYS = ("independent", "hinted_low", "hinted_high", "transfer", "delayed_retest", "independent_wrong", "hinted_wrong")
SOURCE_KEYS = ("diagnose", "practice")  # KGE 第六章：正式考试 > 平台诊断 > 日常练习 > 对话表现；v0 平台内只有平台诊断（diagnose）与日常练习（grade）
STAR_MODES = ("star_level", "all_levels")  # 待人确认 #3：默认 star_level
HINT_LADDER_MAX = 6  # platform.attempt.hint_level_used 0–6
_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,15}$")
_VERSIONED_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")  # 迁移 0001 platform.versioned_name
TEST_ENV = "test"


class ConfigError(ValueError):
    """配置不合法：服务拒绝启动。"""


# 配置位清单（第 12 节）。叶子写类型；dict 表示嵌套；"map" 表示键由配置给出
_SCHEMA: dict[str, Any] = {
    "$comment": str,
    "schema_version": str,
    "subject": str,
    "config_version": str,
    "synthetic": bool,
    "signed_by": str,
    "signed_at": str,
    "calibrated": bool,
    "low_hint_max_level": int,
    "grade_confidence_min": float,
    "evidence_weights": {k: float for k in WEIGHT_KEYS},
    "level_weight_B": float,
    "source_weights": {k: float for k in SOURCE_KEYS},
    "error_penalty": "map:float",
    "delayed_retest_min_interval_seconds": int,
    "item_type_groups": "map:str",
    "gates": {
        "understand": {"correct_min": int},
        "execute": {"families_min": int, "p_floor": float},
        "stable": {"families_min": int, "item_type_groups_min": int, "p_floor": float},
        "transfer": {"transfer_evidence_min": int, "p_floor": float},
    },
    "retest": {"rung0_min_gap_seconds": int, "rung1_interval_seconds": int, "rung2_interval_seconds": int},
    "bkt": {"p_init": float, "p_learn": float, "p_slip": float, "p_guess": {"default": float, "by_item_type": "map:float"}},
    "confidence_formula": {"k": float},
    "confidence_decay": {"grace_seconds": int, "half_life_seconds": int},
    "review_confidence_floor": float,
    "write_threshold": {"p_mastery": float, "confidence": float},
    "star_level_rule": {"mode": str, "node_levels": "map:str"},
    "skill_view_aggregation": {"method": str},
    "sweep": {"lookback_seconds": int},
}
_OPTIONAL = {"$comment"}


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ConfigError(msg)


def _typed(value: Any, kind: Any, where: str, allow_blank: bool) -> None:
    if value is None:
        _need(allow_blank, f"{where}: 未填写（数值待教研签字）")
        return
    if kind is float:
        _need(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), f"{where}: 必须是数")
    elif kind is int:
        _need(isinstance(value, int) and not isinstance(value, bool), f"{where}: 必须是整数")
    else:
        _need(isinstance(value, kind), f"{where}: 类型不对")


def _check_shape(doc: Any, schema: dict[str, Any], where: str, allow_blank: bool) -> None:
    _need(isinstance(doc, dict), f"{where or '配置'}: 必须是对象")
    unknown = set(doc) - set(schema)
    _need(not unknown, f"{where or '配置'}: 不认识的配置位 {sorted(unknown)}")
    for key, kind in schema.items():
        w = f"{where}.{key}" if where else key
        if key not in doc:
            _need(key in _OPTIONAL, f"{w}: 缺少配置位")
            continue
        v = doc[key]
        if isinstance(kind, dict):
            _check_shape(v, kind, w, allow_blank)
        elif isinstance(kind, str) and kind.startswith("map:"):
            _need(isinstance(v, dict), f"{w}: 必须是对象")
            leaf = float if kind == "map:float" else str
            for k2, v2 in v.items():
                _need(isinstance(k2, str) and bool(k2), f"{w}: 键必须是非空字符串")
                _typed(v2, leaf, f"{w}.{k2}", allow_blank)
        else:
            _typed(v, kind, w, allow_blank)


def slot_paths(doc: Mapping[str, Any], prefix: str = "") -> set[str]:
    """配置位路径（不含 map 类配置位里的具体键），用于核对草案与合成配置的配置位一致。"""
    out: set[str] = set()
    for k, v in doc.items():
        p = f"{prefix}.{k}" if prefix else k
        sub = _SCHEMA
        for part in p.split("."):
            sub = sub.get(part) if isinstance(sub, dict) else None
        if isinstance(v, dict) and isinstance(sub, dict):
            out |= slot_paths(v, p)
        else:
            out.add(p)
    return out


def _prob(v: float, where: str, lo_open: bool = True, hi_open: bool = True) -> None:
    ok_lo = v > 0 if lo_open else v >= 0
    ok_hi = v < 1 if hi_open else v <= 1
    _need(ok_lo and ok_hi, f"{where}: 取值超出范围")


def _check_values(d: dict[str, Any]) -> None:
    _need(d["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(d["subject"] in SUBJECTS, "subject 不认识")
    _need(bool(_VERSION_RE.match(d["config_version"])), "config_version 只能是小写字母、数字、点与连字符，最长 16")
    by, at = d["signed_by"].strip(), d["signed_at"].strip()
    _need(bool(by) == bool(at), "signed_by 与 signed_at 必须同时为空或同时填写")
    if at:
        try:
            ts = datetime.fromisoformat(at)
        except ValueError as e:
            raise ConfigError("signed_at 必须是 ISO 8601 时间") from e
        _need(ts.tzinfo is not None, "signed_at 必须带时区")

    _need(0 <= d["low_hint_max_level"] < HINT_LADDER_MAX, "low_hint_max_level 必须在提示阶梯内且留出高级提示")
    _prob(d["grade_confidence_min"], "grade_confidence_min", hi_open=False)

    w = d["evidence_weights"]
    for k in WEIGHT_KEYS:
        _need(-1 <= w[k] <= 1, f"evidence_weights.{k}: 必须在 [-1, 1] 内（不封顶截断，保证大小顺序在更新里保留）")
    # 第 3 节：方向与大小顺序（SIE 第五章证据权重表）
    _need(w["hinted_high"] == 0, "方向：高级提示后做对不改掌握（hinted_high 必须为 0）")
    _need(w["independent"] > w["hinted_low"] > w["hinted_high"], "方向：独立做对 > 低级提示后做对 > 高级提示后做对")
    _need(w["transfer"] >= w["independent"], "方向：迁移做对 ≥ 独立做对")
    _need(w["delayed_retest"] >= w["independent"], "方向：延迟复测做对 ≥ 独立做对")
    _need(w["independent_wrong"] < 0, "方向：独立做错必须下调")
    _need(w["independent_wrong"] <= w["hinted_wrong"] <= 0, "方向：提示后做错不上调，且下调不超过独立做错")
    _need(0 < d["level_weight_B"] < 1, "level_weight_B 必须在 (0, 1)：B 级与 A 级同向、降权")
    s = d["source_weights"]
    _need(0 < s["practice"] < s["diagnose"] <= 1, "来源权重：平台诊断 > 日常练习 > 0，且不超过 1")
    pen = d["error_penalty"]
    _need("default" in pen, "error_penalty 必须有 default")
    _need(set(pen) <= set(CROSS_SUBJECT_ERRORS) | {"default"}, "error_penalty 只能按跨科错因大类配置")
    for k, v in pen.items():
        _need(0 < v <= 1, f"error_penalty.{k}: 必须在 (0, 1]")
    _need(d["delayed_retest_min_interval_seconds"] > 0, "delayed_retest_min_interval_seconds 必须为正")
    for k, v in d["item_type_groups"].items():
        _need(bool(v.strip()), f"item_type_groups.{k}: 组名不能为空")

    g = d["gates"]
    _need(g["understand"]["correct_min"] >= 1, "gates.understand.correct_min 至少为 1")
    # “不同题目族”是复数：一族题永远到不了执行（开发计划验收“刷同题无法点亮”）
    _need(g["execute"]["families_min"] >= 2, "gates.execute.families_min 至少为 2（不同题目族）")
    _need(g["stable"]["families_min"] >= g["execute"]["families_min"], "门槛逐级不降：stable.families_min ≥ execute.families_min")
    _need(g["stable"]["item_type_groups_min"] >= 2, "gates.stable.item_type_groups_min 至少为 2（只刷一种题型不能稳定）")
    _need(g["transfer"]["transfer_evidence_min"] >= 1, "gates.transfer.transfer_evidence_min 至少为 1")
    floors = [g["execute"]["p_floor"], g["stable"]["p_floor"], g["transfer"]["p_floor"]]
    for name, f in zip(("execute", "stable", "transfer"), floors):
        _need(0 <= f < 1, f"gates.{name}.p_floor 必须在 [0, 1)")
    _need(floors == sorted(floors), "门槛逐级不降：p_floor 按执行 ≤ 稳定 ≤ 迁移")

    r = d["retest"]
    _need(r["rung0_min_gap_seconds"] >= 0, "retest.rung0_min_gap_seconds 不能为负")
    _need(r["rung0_min_gap_seconds"] < r["rung1_interval_seconds"] < r["rung2_interval_seconds"],
          "复测间隔必须递增：rung0 < rung1 < rung2")

    b = d["bkt"]
    for k in ("p_init", "p_learn", "p_slip"):
        _prob(b[k], f"bkt.{k}")
    guesses = {"default": b["p_guess"]["default"], **b["p_guess"]["by_item_type"]}
    for k, gv in guesses.items():
        _prob(gv, f"bkt.p_guess.{k}")
        # 可辨识：做对必须比做错更像“会了”，否则做对会让掌握下调（方向校验）
        _need(b["p_slip"] + gv < 1, f"bkt.p_slip + p_guess.{k} 必须小于 1")

    _need(d["confidence_formula"]["k"] > 0, "confidence_formula.k 必须为正")
    _need(d["confidence_decay"]["grace_seconds"] >= 0, "confidence_decay.grace_seconds 不能为负")
    _need(d["confidence_decay"]["half_life_seconds"] > 0, "confidence_decay.half_life_seconds 必须为正")
    _prob(d["review_confidence_floor"], "review_confidence_floor")
    for k in ("p_mastery", "confidence"):
        _need(0 < d["write_threshold"][k] < 0.5, f"write_threshold.{k} 必须在 (0, 0.5)")
    sl = d["star_level_rule"]
    _need(sl["mode"] in STAR_MODES, "star_level_rule.mode 不认识")
    for k, v in sl["node_levels"].items():
        _need(v in COGNITIVE_LEVELS, f"star_level_rule.node_levels.{k}: 认知层次不认识")
    _need(d["skill_view_aggregation"]["method"] in ("weighted_mean",), "skill_view_aggregation.method 不认识")
    _need(d["sweep"]["lookback_seconds"] > 0, "sweep.lookback_seconds 必须为正")


def fingerprint(doc: Mapping[str, Any]) -> str:
    body = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MasteryConfig:
    raw: Mapping[str, Any]
    fingerprint: str

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    @property
    def subject(self) -> str:
        return self.raw["subject"]

    @property
    def signed(self) -> bool:
        """教研已签字：签字前状态机最高只到“稳定”（spec 第 4 节）。synthetic 配置即使填了签字也只在测试环境可加载。"""
        return bool(self.raw["signed_by"].strip() and self.raw["signed_at"].strip())

    @property
    def algorithm_version(self) -> str:
        """算法版本 + 配置版本 + 配置指纹（spec 第 9 节；符合 platform.versioned_name）。"""
        return f"{ALGORITHM}@{ALGORITHM_VERSION}.{self.raw['config_version']}.{self.fingerprint[:8]}"

    def seconds(self, v: int) -> timedelta:
        return timedelta(seconds=v)

    @property
    def weights(self) -> Mapping[str, float]:
        return self.raw["evidence_weights"]

    def p_guess(self, item_type: str | None) -> float:
        g = self.raw["bkt"]["p_guess"]
        return g["by_item_type"].get(item_type or "", g["default"])

    def type_group(self, item_type: str | None) -> str | None:
        # 题型来源未定（待人确认 #4）：没有题型或题型未映射的作答不计入任何题型组（保守）
        return self.raw["item_type_groups"].get(item_type) if item_type else None

    def penalty(self, category: str | None) -> float:
        pen = self.raw["error_penalty"]
        return pen.get(category or "default", pen["default"])


def validate_skeleton(doc: Mapping[str, Any]) -> None:
    """草案骨架：只校验配置位齐全、类型对，数值可以留空（待教研填写）。"""
    _check_shape(doc, _SCHEMA, "", allow_blank=True)


def parse_config(doc: Mapping[str, Any], *, env: str) -> MasteryConfig:
    """校验并返回配置。不合法、或未签字 / 合成配置出现在非 test 环境，一律拒绝。"""
    _check_shape(doc, _SCHEMA, "", allow_blank=False)
    d = json.loads(json.dumps(doc))  # 深拷贝，之后不可变
    _check_values(d)
    cfg = MasteryConfig(raw=d, fingerprint=fingerprint(d))
    if env != TEST_ENV:
        _need(not d["synthetic"], "合成占位配置只允许 LEARNING_ENV=test 加载")
        _need(cfg.signed, "未签字的掌握配置只允许 LEARNING_ENV=test 加载（待教研签字，待人确认 #1）")
    _need(bool(_VERSIONED_NAME.match(cfg.algorithm_version)), "algorithm_version 不符合 versioned_name")
    return cfg


def load_config(path: str | Path, *, env: str) -> MasteryConfig:
    with open(path, encoding="utf-8") as f:
        try:
            doc = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path}: 不是合法 JSON") from e
    return parse_config(doc, env=env)


def load_config_from_env(path: str | Path, environ: Mapping[str, str] | None = None) -> MasteryConfig:
    """部署入口：环境从 LEARNING_ENV 读，不由调用方传。"""
    env = (os.environ if environ is None else environ).get("LEARNING_ENV", "")
    _need(env in ("dev", TEST_ENV), "LEARNING_ENV 没有设置或不认识：掌握配置拒绝加载")
    return load_config(path, env=env)
