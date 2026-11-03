"""走查样例的规范化（normalize.py）与准入安排（walk.admission_plan）。合成数据。"""
from __future__ import annotations

from release_walkthrough.normalize import Normalizer
from release_walkthrough.walk import FIXED_IDS, MAP_ID, T1, admission_plan

U1 = "c0de0000-0000-4000-8000-000000000001"
U2 = "c0de0000-0000-4000-8000-000000000002"
H1, H2 = "a" * 64, "b" * 64


def test_same_value_same_label_fixed_ids_kept():
    n = Normalizer(FIXED_IDS)
    v = n.value({"a": [U1, U2, U1, str(T1), str(MAP_ID)], "d": f"sha256:{H1}", "e": H2, "f": H1})
    assert v == {"a": ["<uuid-1>", "<uuid-2>", "<uuid-1>", str(T1), str(MAP_ID)], "d": "sha256:<sha256-1>", "e": "<sha256-2>",
                 "f": "<sha256-1>"}


def test_times_refs_and_pack_digest():
    n = Normalizer(set())
    assert n.text("2026-10-04T16:32:35Z 2026-10-04 16:32:35.123456+00:00") == "<time> <time>"
    assert n.text("AYA-ADM-20261004T163235Z") == "<export-ref-1>"
    assert n.text("0.3.0-0ed2fe4.d76489553") == "0.3.0-0ed2fe4.d<pack-digest-1>"
    assert n.text("Q-ALG-U01-D-001") == "Q-ALG-U01-D-001"  # 题号不动
    assert n.value({"k": 3, "b": None, "t": True}) == {"k": 3, "b": None, "t": True}


def test_admission_plan_is_deterministic_and_alternates_signers():
    ids = [f"Q-SYN-{n:03d}" for n in range(9, 0, -1)]
    p = admission_plan(ids)
    assert p == admission_plan(sorted(ids))
    assert {x["item_id"] for x in p} == {f"Q-SYN-{n:03d}" for n in range(1, 6)}
    assert all(x["signer"] != x["applier"] for x in p)  # 签字人与执行人不同
    assert all(x.get("source_ref") is None or x["source_ref"].startswith("SYN-SRC-") for x in p)  # 只用合成来源编号
    assert [x["to"] for x in p if x["item_id"] == "Q-SYN-005"] == ["pending_review", "admitted", "rejected"]
