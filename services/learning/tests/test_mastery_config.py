"""掌握配置：结构、取值范围、方向与顺序、门槛单调、复测间隔递增、签字状态、环境限制、配置指纹（spec 第 3、5、12 节）。"""
from __future__ import annotations

import copy
import json
import re

import pytest

from learning.mastery import ConfigError, load_config, load_config_from_env, parse_config
from learning.mastery.config import CONFIG_DIR, slot_paths, validate_skeleton
from test_mastery_support import signed_doc, synthetic_doc

VERSIONED_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")


def mutate(path: str, value):
    d = synthetic_doc()
    cur = d
    parts = path.split(".")
    for p in parts[:-1]:
        cur = cur[p]
    cur[parts[-1]] = value
    return d


def test_synthetic_config_loads_in_test_env_and_is_unsigned():
    cfg = load_config(CONFIG_DIR / "synthetic-test.json", env="test")
    assert cfg.signed is False
    assert cfg["synthetic"] is True
    assert "合成占位" in cfg["$comment"] and "禁止用于任何非测试环境" in cfg["$comment"]


@pytest.mark.parametrize("env", ["dev", "pilot", "prod", ""])
def test_unsigned_or_synthetic_rejected_outside_test(env):
    with pytest.raises(ConfigError):
        parse_config(synthetic_doc(), env=env)
    # 即使填了签字，合成配置也不能离开测试环境
    with pytest.raises(ConfigError):
        parse_config(signed_doc(), env=env)
    # 非合成但未签字：同样只允许 test
    d = synthetic_doc()
    d["synthetic"] = False
    with pytest.raises(ConfigError):
        parse_config(d, env=env)
    parse_config(d, env="test")


def test_signed_non_synthetic_loads_in_dev():
    d = signed_doc()
    d["synthetic"] = False
    cfg = parse_config(d, env="dev")
    assert cfg.signed


def test_env_entry_reads_learning_env():
    with pytest.raises(ConfigError):
        load_config_from_env(CONFIG_DIR / "synthetic-test.json", {})
    with pytest.raises(ConfigError):
        load_config_from_env(CONFIG_DIR / "synthetic-test.json", {"LEARNING_ENV": "dev"})
    assert load_config_from_env(CONFIG_DIR / "synthetic-test.json", {"LEARNING_ENV": "test"}).signed is False


def test_signature_fields_must_come_together():
    for by, at in (("x", ""), ("", "2031-01-01T00:00:00+08:00")):
        d = synthetic_doc()
        d["signed_by"], d["signed_at"] = by, at
        with pytest.raises(ConfigError):
            parse_config(d, env="test")
    d = signed_doc()
    d["signed_at"] = "2031-01-01T00:00:00"  # 没有时区
    with pytest.raises(ConfigError):
        parse_config(d, env="test")


def test_algorithm_version_carries_config_version_and_fingerprint():
    a = parse_config(synthetic_doc(), env="test")
    b = parse_config(signed_doc(), env="test")
    assert VERSIONED_NAME.match(a.algorithm_version)
    assert a.algorithm_version.startswith("kge-mastery@0.1.0.syn-0.1.0.")
    assert a.algorithm_version != b.algorithm_version  # 任何改动（含签字）都换指纹
    assert parse_config(synthetic_doc(), env="test").algorithm_version == a.algorithm_version
    c = mutate("gates.execute.p_floor", 0.31)
    assert parse_config(c, env="test").algorithm_version != a.algorithm_version


def test_draft_skeleton_has_every_slot_and_no_values():
    with open(CONFIG_DIR / "aya.draft.json", encoding="utf-8") as f:
        draft = json.load(f)
    validate_skeleton(draft)
    assert slot_paths(draft) == slot_paths(synthetic_doc())
    with pytest.raises(ConfigError, match="未填写"):
        parse_config(draft, env="test")

    def leaves(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from leaves(v)
        else:
            yield x
    numeric = [v for v in leaves(draft) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    assert numeric == []  # 草案里不得出现任何数值（待教研签字）


@pytest.mark.parametrize("path,value", [
    ("unknown_slot", 1),
    ("gates.execute.p_floor", "0.3"),
    ("gates.execute.families_min", 2.5),
    ("calibrated", 0),
    ("low_hint_max_level", True),
    ("bkt.p_init", float("nan")),
])
def test_shape_rejected(path, value):
    with pytest.raises(ConfigError):
        parse_config(mutate(path, value), env="test")


def test_missing_slot_rejected():
    d = synthetic_doc()
    del d["retest"]["rung2_interval_seconds"]
    with pytest.raises(ConfigError):
        parse_config(d, env="test")


# 方向与顺序（第 3 节）：任一方向或顺序写反，服务拒绝启动
@pytest.mark.parametrize("path,value", [
    ("evidence_weights.hinted_high", 0.1),        # 高级提示后做对必须不变
    ("evidence_weights.hinted_low", 0.8),         # 低级提示 = 独立
    ("evidence_weights.hinted_low", 0.9),         # 低级提示 > 独立
    ("evidence_weights.hinted_low", 0.0),         # 低级提示不上调
    ("evidence_weights.transfer", 0.7),           # 迁移 < 独立
    ("evidence_weights.delayed_retest", 0.5),     # 延迟复测 < 独立
    ("evidence_weights.independent_wrong", 0.0),  # 独立做错不下调
    ("evidence_weights.independent_wrong", 0.2),  # 独立做错上调
    ("evidence_weights.hinted_wrong", 0.1),       # 提示后做错上调
    ("evidence_weights.hinted_wrong", -0.8),      # 提示后做错比独立做错罚得还重
    ("evidence_weights.independent", 1.5),        # 超出 [-1, 1]
    ("level_weight_B", 1.0),                      # B 级不低于 A 级
    ("level_weight_B", 0.0),
    ("source_weights.practice", 1.0),             # 日常练习 ≥ 平台诊断
    ("error_penalty.default", 0.0),
    ("error_penalty.not_a_category", 0.5),
    ("low_hint_max_level", 6),                    # 没有留出高级提示
    ("grade_confidence_min", 0.0),
    ("bkt.p_slip", 0.85),                         # slip + guess ≥ 1：做对会让掌握下调
    ("bkt.p_guess.by_item_type", {"syn-choice": 0.95}),
    ("bkt.p_learn", 1.0),
])
def test_direction_and_range_rejected(path, value):
    with pytest.raises(ConfigError):
        parse_config(mutate(path, value), env="test")


# 门槛逐级不降、复测间隔递增
@pytest.mark.parametrize("path,value", [
    ("gates.execute.p_floor", 0.55),              # 执行 > 稳定
    ("gates.stable.p_floor", 0.65),               # 稳定 > 迁移
    ("gates.transfer.p_floor", 1.0),
    ("gates.execute.families_min", 1),            # 一族题就能到执行
    ("gates.stable.families_min", 1),             # 稳定要求的族数少于执行
    ("gates.stable.item_type_groups_min", 1),     # 只刷一种题型就能稳定
    ("gates.understand.correct_min", 0),
    ("gates.transfer.transfer_evidence_min", 0),
    ("retest.rung1_interval_seconds", 7200),      # rung1 = rung0
    ("retest.rung2_interval_seconds", 100000),    # rung2 < rung1
    ("retest.rung0_min_gap_seconds", -1),
    ("delayed_retest_min_interval_seconds", 0),
    ("confidence_decay.half_life_seconds", 0),
    ("write_threshold.p_mastery", 0.0),
    ("star_level_rule.mode", "any_level"),
    ("star_level_rule.node_levels", {"syn-node-001": "create"}),
    ("config_version", "Syn Version With Spaces"),
    ("schema_version", "other/0.1"),
    ("subject", "physics"),
])
def test_monotonic_and_misc_rejected(path, value):
    with pytest.raises(ConfigError):
        parse_config(mutate(path, value), env="test")


def test_parse_copies_input():
    d = synthetic_doc()
    cfg = parse_config(d, env="test")
    d["gates"]["execute"]["p_floor"] = 0.99
    assert cfg["gates"]["execute"]["p_floor"] != 0.99
    assert copy.deepcopy(cfg.raw) == cfg.raw
