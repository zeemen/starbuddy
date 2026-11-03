"""性质测试：随机合成证据序列（固定种子，不引入 hypothesis 依赖）。

- 重放确定性：同一序列两次结果逐字节相同；输入顺序打乱结果不变；
- 门槛单调：每次晋级只升一级；唯一的退级是复测失败从迁移 / 保持退回稳定；
- 刷同题永不点亮：只有一个题目族时最高“理解”；
- 未签字配置到不了迁移；B 级、未准入、非 formal、旧证据组成的序列签字后也到不了迁移；
- 已亮的记录只引用严格证据（写库前自检通过）；
- 衰减只降置信度：now 推后，p 与门槛不变，置信度单调不增。
"""
from __future__ import annotations

import json
import random

import pytest

from learning.mastery import EvidenceFacts, explain_gates, precommit_check, replay
from learning.mastery.gates import LIT, RANK
from test_mastery_support import DAY, MIN, T0, UNIT, ev, signed_cfg, unsigned_cfg

S = signed_cfg()
U = unsigned_cfg()
SEEDS = range(300)
TYPES = ["syn-solve", "syn-choice", "syn-fill", "syn-proof", None]
KINDS = ["independent", "independent", "transfer", "delayed_retest", "hinted", None]


def random_sequence(rng: random.Random, *, families: int | None = None, strict_ok: bool = True, n: int | None = None):
    n = n if n is not None else rng.randint(0, 40)
    t = T0
    out = []
    fam_pool = families or rng.randint(1, 15)
    for i in range(n):
        t = t + rng.choice([MIN, 30 * MIN, 3 * 60 * MIN, DAY, 3 * DAY, 10 * DAY])
        kind = rng.choice(KINDS)
        hint = 0 if kind in ("independent", "transfer", "delayed_retest") else rng.choice([None, 0, 1, 2, 3, 6])
        level = rng.choice(["A", "A", "A", "B", None] if strict_ok else ["B", None])
        out.append(ev(t, f"fam-{rng.randrange(fam_pool)}", item_type=rng.choice(TYPES), kind=kind, level=level, hint=hint,
                      verdict=rng.choice(["correct", "correct", "correct", "incorrect", "partial"]),
                      source=rng.choice(["grade", "grade", "diagnose"]),
                      admission=rng.choice(["admitted", "admitted", "admitted", "pending_review"]),
                      semantics=rng.choice(["formal", "formal", "practice"]),
                      human_review_required=rng.random() < 0.1,
                      grade_confidence=rng.choice([1.0, 0.9, 0.7, 0.3]),
                      self_report=rng.choice([None, None, "confident", "unsure", "guessed"]),
                      eid=f"syn-p-{i:04d}", attempt_id=f"syn-pa-{i:04d}", item_id=f"syn-pi-{i:04d}"))
    return out


def dump(rep) -> bytes:
    return json.dumps({"result": rep.result.to_dict(), "explain": explain_gates(rep)}, sort_keys=True,
                      ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("seed", SEEDS)
def test_replay_is_deterministic_and_order_independent(seed):
    rng = random.Random(seed)
    evs = random_sequence(rng)
    now = (evs[-1].recorded_at if evs else T0) + rng.choice([0, 1, 50]) * DAY
    a = dump(replay(evs, UNIT, S, now))
    assert dump(replay(evs, UNIT, S, now)) == a
    shuffled = evs[:]
    rng.shuffle(shuffled)
    assert dump(replay(shuffled, UNIT, S, now)) == a


@pytest.mark.parametrize("seed", SEEDS)
def test_gate_transitions_are_monotone(seed):
    evs = random_sequence(random.Random(seed))
    rep = replay(evs, UNIT, S, T0 + 400 * DAY)
    for t in rep.transitions:
        if t.cause == "promote":
            assert RANK[t.to_gate] == RANK[t.from_gate] + 1
        else:
            assert t.cause == "retest_failed" and t.from_gate in LIT and t.to_gate == "stable"
    if rep.transitions:
        assert rep.transitions[-1].to_gate == rep.result.state


@pytest.mark.parametrize("seed", SEEDS)
def test_one_family_never_passes_understand(seed):
    evs = random_sequence(random.Random(seed), families=1)
    rep = replay(evs, UNIT, S, T0 + 400 * DAY)
    assert rep.result.state in (None, "unseen", "understand")
    assert len(rep.result.evidence_ids) <= 1


@pytest.mark.parametrize("seed", SEEDS)
def test_unsigned_config_never_reaches_transfer(seed):
    evs = random_sequence(random.Random(seed), n=60)
    rep = replay(evs, UNIT, U, T0 + 400 * DAY)
    assert rep.result.state not in LIT
    assert all(t.to_gate not in LIT for t in rep.transitions)


@pytest.mark.parametrize("seed", SEEDS)
def test_non_strict_sequences_never_light(seed):
    evs = random_sequence(random.Random(seed), strict_ok=False, n=60)
    rep = replay(evs, UNIT, S, T0 + 400 * DAY)
    assert rep.result.state not in LIT


@pytest.mark.parametrize("seed", SEEDS)
def test_lit_records_pass_precommit(seed):
    evs = random_sequence(random.Random(seed), n=60)
    rep = replay(evs, UNIT, S, T0 + 400 * DAY)
    facts = {e.evidence_id: EvidenceFacts("A" if e.evidence_level == "A" and e.evidence_kind else None, e.admission,
                                          e.human_review_required, e.semantics) for e in evs}
    precommit_check(rep.result, facts)


@pytest.mark.parametrize("seed", range(60))
def test_decay_only_lowers_confidence(seed):
    evs = random_sequence(random.Random(seed), n=30)
    if not evs:
        return
    last = evs[-1].recorded_at
    base = replay(evs, UNIT, S, last).result
    prev = base.confidence
    for d in (1, 10, 30, 90, 365, 3650):
        r = replay(evs, UNIT, S, last + d * DAY).result
        assert (r.state, r.p_mastery, r.next_retest_at, r.evidence_ids) == (base.state, base.p_mastery, base.next_retest_at,
                                                                             base.evidence_ids)
        assert r.confidence <= prev
        prev = r.confidence


def test_generator_actually_reaches_high_gates():
    """防止性质测试空转：随机序列里确实出现过迁移与保持。"""
    seen = set()
    for seed in SEEDS:
        seen.add(replay(random_sequence(random.Random(seed), n=60), UNIT, S, T0 + 400 * DAY).result.state)
    assert {"execute", "stable"} <= seen and seen & LIT
