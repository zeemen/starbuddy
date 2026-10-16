"""配置 config.json：加载、形状与取值校验、配置指纹。阈值集中在这一份，全部标“待教研确认”（spec 待人确认 1、2）。

“掌握”只有星亮一种口径（与学习服务 gates.py 的 star_lit 同口径，不另立）：星亮层次那一行的门槛属于 LIT 算掌握。
最小格人数不得低于 MIN_CELL_FLOOR（spec：同一格少于 20 人不出结论；调低是隐私口径变更，须由人决定后改代码）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "kge-calibrate-config/0.2"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
PENDING = "待教研确认"
MIN_CELL_FLOOR = 20
# 取值与 services/learning/learning/mastery/gates.py 的 GATES、LIT 一致（测试逐字核对）；只读口径，不改掌握计算
GATES = ("unseen", "understand", "execute", "stable", "transfer", "retain")
LIT = frozenset({"transfer", "retain"})
MASTERY_RULES = ("star_lit",)


class ConfigError(ValueError):
    """配置不合法：拒绝运行"""


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ConfigError(msg)


def _keys(doc: Any, required: set[str], where: str) -> None:
    _need(isinstance(doc, dict), f"{where} 必须是对象")
    extra = set(doc) - required - {"$comment"}
    missing = required - set(doc)
    _need(not extra and not missing, f"{where} 的配置位不对（多出 {sorted(extra)}，缺少 {sorted(missing)}）")


def _num(v: Any, where: str, lo: float, hi: float) -> float:
    _need(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi,
          f"{where} 必须是 [{lo}, {hi}] 内的数")
    return float(v)


def _int(v: Any, where: str, lo: int, hi: int) -> int:
    _need(isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi, f"{where} 必须是 [{lo}, {hi}] 内的整数")
    return int(v)


@dataclass(frozen=True)
class MasteryRule:
    rule: str = "star_lit"

    def mastered(self, gate: str) -> bool:
        """星亮：星亮层次那一行的门槛属于 LIT（gates.py 的 star_lit）"""
        return gate in LIT

    def describe(self) -> str:
        return ("星亮口径（与学习服务 star_lit 同口径）：星亮层次那一行的门槛为 transfer 或 retain 算掌握；"
                "掌握概率不参与判定；unseen、空门槛或无记录算未观测")


@dataclass(frozen=True)
class Config:
    config_version: str
    signoff: str
    mastery: MasteryRule
    min_cell: int
    confidence: float
    prereq_fdr_q: float
    min_effect: float
    hidden_fdr_q: float
    min_phi: float
    condition_on_shared: bool
    max_pairs_per_topic: int
    max_false_not_holds: int
    max_false_hidden: int
    fingerprint: str

    def provenance(self) -> dict[str, Any]:
        return {"config_version": self.config_version, "fingerprint": self.fingerprint, "signoff": self.signoff}


def parse_config(doc: Mapping[str, Any]) -> Config:
    _keys(doc, {"schema_version", "config_version", "signoff", "mastery", "min_cell", "prerequisite", "hidden_dependency",
                "synthetic_acceptance"}, "校准配置")
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and 0 < len(doc["config_version"]) <= 32
          and all(ch.isascii() and (ch.isalnum() or ch in "._-") for ch in doc["config_version"]), "config_version 格式不对")
    _need(isinstance(doc["signoff"], str) and doc["signoff"].strip() != "", f"signoff 必须写明（未确认写“{PENDING}”）")

    m = doc["mastery"]
    _keys(m, {"rule"}, "mastery")
    _need(m["rule"] in MASTERY_RULES, f"mastery.rule 只能是 {MASTERY_RULES}（与学习服务 star_lit 同口径）")

    min_cell = _int(doc["min_cell"], "min_cell", MIN_CELL_FLOOR, 100_000)

    p = doc["prerequisite"]
    _keys(p, {"confidence", "fdr_q", "min_effect"}, "prerequisite")
    h = doc["hidden_dependency"]
    _keys(h, {"fdr_q", "min_phi", "condition_on_shared_prerequisites", "max_pairs_per_topic"}, "hidden_dependency")
    _need(isinstance(h["condition_on_shared_prerequisites"], bool), "hidden_dependency.condition_on_shared_prerequisites 必须是布尔值")
    a = doc["synthetic_acceptance"]
    _keys(a, {"max_false_not_holds", "max_false_hidden"}, "synthetic_acceptance")

    fp = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return Config(doc["config_version"], doc["signoff"].strip(), MasteryRule(m["rule"]), min_cell,
                  _num(p["confidence"], "prerequisite.confidence", 0.8, 0.999),
                  _num(p["fdr_q"], "prerequisite.fdr_q", 0.001, 0.2),
                  _num(p["min_effect"], "prerequisite.min_effect", 0.0, 1.0),
                  _num(h["fdr_q"], "hidden_dependency.fdr_q", 0.001, 0.2),
                  _num(h["min_phi"], "hidden_dependency.min_phi", 0.05, 1.0),
                  h["condition_on_shared_prerequisites"],
                  _int(h["max_pairs_per_topic"], "hidden_dependency.max_pairs_per_topic", 1, 1_000_000),
                  _int(a["max_false_not_holds"], "synthetic_acceptance.max_false_not_holds", 0, 1000),
                  _int(a["max_false_hidden"], "synthetic_acceptance.max_false_hidden", 0, 1000), fp)


def load_config(path: str | Path = CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as fh:
        return parse_config(json.load(fh))
