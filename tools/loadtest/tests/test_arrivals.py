"""到达过程与请求序列：同种子两次逐项一致（spec 验收“本机压测可复现”），分阶段、泊松、配比正确。"""
from __future__ import annotations

import statistics

import pytest

from loadtest.arrivals import PROFILES, Profile, Stage, digest, parse_stages, plan, requests, slot_counts
from loadtest.scenarios import ENDPOINTS, JOURNEYS, keyed


def test_same_seed_same_request_sequence():
    p = Profile(seed=7, stages=(Stage(5, 10), Stage(10, 10)))
    a, b = requests(p), requests(Profile(seed=7, stages=(Stage(5, 10), Stage(10, 10))))
    assert a == b and digest(a) == digest(b)
    assert digest(requests(Profile(seed=8, stages=p.stages))) != digest(a)


def test_digest_ignores_completion_order_but_not_content():
    reqs = requests(Profile(seed=3))
    assert digest(list(reversed(reqs))) == digest(reqs), "实际执行时完成顺序不同，序列比较按（到达、步骤）排序"
    changed = list(reqs)
    changed[0] = type(changed[0])(changed[0].arrival, changed[0].seq, changed[0].step, "different-key")
    assert digest(changed) != digest(reqs)


def test_stages_are_sequential_and_arrivals_fall_inside():
    p = Profile(seed=11, stages=(Stage(4, 5), Stage(8, 5), Stage(16, 5)))
    arr = plan(p)
    assert [a.index for a in arr] == list(range(len(arr)))
    assert all(0 <= a.at_s < 15 for a in arr)
    for a in arr:
        assert 5 * a.stage <= a.at_s < 5 * (a.stage + 1)
    assert [a.at_s for a in arr] == sorted(a.at_s for a in arr)


def test_poisson_rate_matches_concurrency_over_session_time():
    p = Profile(seed=1, stages=(Stage(20, 600),), mean_session_s=4.0)
    arr = plan(p)
    rate = len(arr) / 600
    assert rate == pytest.approx(20 / 4.0, rel=0.05)
    gaps = [b.at_s - a.at_s for a, b in zip(arr, arr[1:])]
    # 指数分布：均值 ≈ 标准差
    assert statistics.mean(gaps) == pytest.approx(statistics.pstdev(gaps), rel=0.1)


def test_mix_and_slots():
    p = Profile(seed=5, stages=(Stage(20, 300),))
    arr = plan(p)
    counts = {k: sum(1 for a in arr if a.kind == k) for k in JOURNEYS}
    total = len(arr)
    assert counts["student"] / total == pytest.approx(0.6, abs=0.05)
    assert slot_counts(arr) == counts, "每类旅程的合成对象编号连续、不重用"
    for a in arr:
        assert len(a.think_s) == len(JOURNEYS[a.kind])


def test_keys_only_on_write_steps_and_all_distinct():
    reqs = requests(Profile(seed=9))
    keys = [r.key for r in reqs if r.key]
    assert len(keys) == len(set(keys))
    assert all((r.key is not None) == keyed(r.step) for r in reqs)
    assert all(r.key is None for r in reqs if r.step in ("today", "star_map", "login.otp_request"))


def test_every_step_has_a_report_label():
    assert {s for steps in JOURNEYS.values() for s in steps} <= set(ENDPOINTS)


def test_profiles():
    assert [s.concurrency for s in PROFILES["full"]] == [10, 50, 100, 200]
    assert max(s.concurrency for s in PROFILES["small"]) <= 20 and sum(s.duration_s for s in PROFILES["small"]) <= 30
    assert parse_stages("3:8,6:4") == (Stage(3, 8.0), Stage(6, 4.0))
    with pytest.raises(ValueError):
        Profile(stages=())
    with pytest.raises(ValueError):
        Profile(mix=(("nobody", 1.0),))
