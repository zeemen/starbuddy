"""掌握 v0 测试的公用构件（不含测试）。全部合成数据（规则 8）：节点、题目、题目族、证据 ID 都是占位；
配置用 services/learning/config/mastery/synthetic-test.json（合成占位，非教研签字值），签字版本只在内存里构造。"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from learning.mastery import Evidence, Unit, parse_config
from learning.mastery.config import CONFIG_DIR

T0 = datetime(2031, 3, 3, 8, 0, tzinfo=timezone.utc)  # 合成时间
NODE = "syn-node-001"
LEVEL = "apply"
UNIT = Unit(NODE, LEVEL)
MIN, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)


def synthetic_doc() -> dict:
    with open(CONFIG_DIR / "synthetic-test.json", encoding="utf-8") as f:
        return json.load(f)


def signed_doc(doc: dict | None = None) -> dict:
    d = copy.deepcopy(doc or synthetic_doc())
    d["signed_by"] = "synthetic-test-signer"
    d["signed_at"] = "2031-01-01T00:00:00+08:00"
    return d


def unsigned_cfg():
    return parse_config(synthetic_doc(), env="test")


def signed_cfg():
    return parse_config(signed_doc(), env="test")


_n = [0]


def ev(t: datetime, family: str | None, *, item_type: str | None = "syn-solve", kind: str | None = "independent",
       level: str | None = "A", hint: int | None = 0, verdict: str = "correct", eid: str | None = None, **kw) -> Evidence:
    _n[0] += 1
    i = _n[0]
    base = dict(evidence_id=eid or f"syn-ev-{i:05d}", recorded_at=t, item_id=kw.pop("item_id", f"syn-item-{i:05d}"),
                attempt_id=kw.pop("attempt_id", f"syn-att-{i:05d}"), family_id=family, item_type=item_type,
                main_node_id=kw.pop("main_node_id", NODE), cognitive_level=kw.pop("cognitive_level", LEVEL),
                evidence_kind=kind, evidence_level=level, hint_level=hint, verdict=verdict)
    base.update(kw)
    return Evidence(**base)


def normal_path(t0: datetime = T0) -> list[Evidence]:
    """按合成配置：执行 → 当日复测 → 第二轮 → 第三轮，依次到达执行、稳定、迁移、保持。"""
    return [
        ev(t0, "fam-a", item_type="syn-solve"),
        ev(t0 + 5 * MIN, "fam-b", item_type="syn-choice"),        # 执行（两族），排 rung0：+2h
        ev(t0 + 3 * HOUR, "fam-c", item_type="syn-proof"),         # rung0 通过 → 稳定，排 rung1：+2d
        ev(t0 + 3 * DAY, "fam-d", kind="transfer"),                # rung1 通过 → 迁移，排 rung2：+9d
        ev(t0 + 13 * DAY, "fam-e", kind="delayed_retest"),         # rung2 通过 → 保持
    ]


def with_(e: Evidence, **kw) -> Evidence:
    return replace(e, **kw)
