"""错题再练 PR 1 性质测试：随机合成作答序列（固定种子，不引入 hypothesis 依赖；同 test_mastery_properties 的做法）。

- 确定性：同一序列两次结果相同；输入顺序打乱结果不变；
- 闭环单调：在序列末尾追加任意“非错题”作答（含独立做对）永远不会把已闭环的条目重新打开，也不改动已闭环的条目；
  只有之后又出现新的错题才会另开条目（新锚点）；
- 状态与轮次一致：只有 waiting 有下一轮时间；exhausted 的轮数做满；做过的轮数不超过 max_rounds 之外的错题数；
- 选题：交给适配器的一档里没有原题、没有排除的题、没有其他单元的题；有不同族时不含同族题；
- 票据：随机票据往返一致；任意改一个字符都被拒（不会被当成另一张有效票据）。
"""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta

from learning.practice import queue as Q
from learning.practice import select as S
from learning.practice import ticket as T
from test_practice_pure import CFG, SECRET, STUDENT, T0, U1, U2, att, cand

SEEDS = range(300)
UNITS = [U1, U2]
H = timedelta(hours=1)


def random_attempts(rng: random.Random, n: int | None = None):
    n = n if n is not None else rng.randint(0, 30)
    t = T0
    out = []
    for _ in range(n):
        t = t + rng.choice([timedelta(minutes=1), H, 30 * H, 50 * H, 130 * H])
        out.append(att(t, f"I{rng.randrange(6)}", rng.choice(["correct", "correct", "incorrect", "partial", "needs_review"]),
                       unit=rng.choice(UNITS + [None]), hint=rng.choice([0, 0, 0, 1, 3, None]),
                       level=rng.choice(["A", "A", "B", "C", None]), channel=rng.choice(["keypad", "keypad", "canvas", "photo"]),
                       review=rng.random() < 0.1, self_report=rng.choice([None, None, "confident", "guessed"]),
                       diag=rng.random() < 0.2))
    return out


def snapshot(entries):
    return sorted((e.unit.node_id, e.unit.cognitive_level, e.status, e.round, e.rounds_done, e.anchor, e.source_item_id,
                   e.next_round_at, e.last_activity_at) for e in entries)


def test_deterministic_and_order_independent():
    for seed in SEEDS:
        rng = random.Random(seed)
        seq = random_attempts(rng)
        now = (seq[-1].recorded_at if seq else T0) + rng.choice([H, 49 * H, 200 * H])
        a = Q.build(seq, (), (), CFG, now)
        assert snapshot(a) == snapshot(Q.build(seq, (), (), CFG, now))
        shuffled = list(seq)
        rng.shuffle(shuffled)
        assert snapshot(a) == snapshot(Q.build(shuffled, (), (), CFG, now)), seed


def test_closure_is_monotone_under_non_wrong_evidence():
    for seed in SEEDS:
        rng = random.Random(seed)
        seq = random_attempts(rng)
        end = seq[-1].recorded_at if seq else T0
        now = end + 300 * H
        before = {e.unit: e for e in Q.build(seq, (), (), CFG, now, capped=False)}
        extra = []
        t = end
        for _ in range(rng.randint(1, 8)):
            t = t + H
            a = att(t, f"I{rng.randrange(8)}", "correct", unit=rng.choice(UNITS), hint=rng.choice([0, 1, None]),
                    level=rng.choice(["A", "B", "C"]), self_report=rng.choice([None, "guessed"]), review=rng.random() < 0.2)
            assert Q.wrong_reason(a, CFG, now) is not None
            extra.append(a)
        after = {e.unit: e for e in Q.build(seq + extra, (), (), CFG, now, capped=False)}
        for u, e in before.items():
            if e.status == "closed":
                assert after[u] == e, seed                     # 已闭环的条目不变，不会重新打开
            else:
                assert after[u].anchor == e.anchor             # 仍是同一条目（只可能闭环）
                assert after[u].status in (e.status, "closed")


def test_new_wrong_after_close_opens_a_new_entry():
    for seed in SEEDS:
        rng = random.Random(seed)
        seq = random_attempts(rng)
        end = seq[-1].recorded_at if seq else T0
        now = end + 2 * H
        closed = [e for e in Q.build(seq, (), (), CFG, now) if e.status == "closed"]
        for e in closed:
            w = att(end + H, "I9", unit=e.unit)
            n = next(x for x in Q.build(seq + [w], (), (), CFG, now) if x.unit == e.unit)
            assert n.status == "due" and n.round == 1 and n.anchor == w.attempt_id and n.source_item_id == "I9"


def test_status_round_consistency():
    for seed in SEEDS:
        rng = random.Random(seed)
        seq = random_attempts(rng)
        now = (seq[-1].recorded_at if seq else T0) + rng.choice([H, 49 * H, 200 * H])
        for e in Q.build(seq, (), (), CFG, now):
            assert e.status in Q.STATUSES and e.source == "wrong"
            assert (e.next_round_at is not None) == (e.status == "waiting")
            if e.status == "waiting":
                assert e.next_round_at > now
            if e.status == "exhausted":
                assert e.rounds_done >= CFG.max_rounds and e.round == CFG.max_rounds
            if e.status in ("due", "waiting"):
                assert 1 <= e.round <= CFG.max_rounds and e.round == e.rounds_done + 1


def test_offered_tier_respects_filters():
    for seed in SEEDS:
        rng = random.Random(seed)
        cands = [cand(f"C{i}", rng.choice(["f1", "f2", "f3", None]), unit=rng.choice(UNITS),
                      license_state=rng.choice(["original", "original", "restricted"])) for i in range(rng.randint(0, 15))]
        adm = {c.item_id: rng.choice(["admitted", "admitted", "pending_review"]) for c in cands if rng.random() < 0.9}
        excl = {f"C{i}" for i in range(15) if rng.random() < 0.3}
        src = rng.choice([None, "C0", "C1"])
        used = {rng.choice(["family:f1", "family:f2", "item:C3"]) for _ in range(rng.randint(0, 2))}
        f = S.filter_candidates(cands, U1, source_item_id=src, exclude_item_ids=excl, admission=adm)
        assert (f.empty_reason is None) == bool(f.kept)
        t = S.tier(f.kept, used, prefer_new_family=True)
        for c in t.offered:
            assert S.in_unit(c, U1) and c.item_id != src and c.item_id not in excl
            assert c.license_state != "restricted" and adm.get(c.item_id) == "admitted"
        fresh = [c for c in f.kept if c.family_key not in used]
        if fresh:
            assert not t.same_family_only and all(c.family_key not in used for c in t.offered)
        else:
            assert t.same_family_only == bool(f.kept)


def test_ticket_roundtrip_and_single_char_mutations():
    rng = random.Random(7)
    for i in range(200):
        t = T.Ticket(student=STUDENT, subject=rng.choice(["aya", "zhixing"]), node_id=f"MATH.SYN.T1.N{i}",
                     cognitive_level=rng.choice(["know", "apply"]), anchor=f"anchor-{i}", item_id=f"C{i}",
                     round=rng.randint(1, 3), issued_at=T.issued(T0) + i)
        tok = T.issue(SECRET, t)
        assert T.verify(SECRET, tok, now=T0 + timedelta(minutes=5), ttl_minutes=60) == t
        pos = rng.randrange(len(tok))
        ch = rng.choice([c for c in "ABCabc019-_." if c != tok[pos]])
        bad = tok[:pos] + ch + tok[pos + 1:]
        try:
            got = T.verify(SECRET, bad, now=T0 + timedelta(minutes=5), ttl_minutes=60)
        except T.TicketError as e:
            assert e.code == "TICKET_INVALID"
        else:
            # base64 尾部填充位的等价写法：解出的载荷必须与原票据完全相同（不会变成另一张有效票据）
            assert got == t


def test_hint_entry_closes_only_on_other_item_independent_correct():
    for seed in SEEDS:
        rng = random.Random(seed)
        hinted = att(T0, "I1", "correct", hint=3)
        task = Q.HintTask("t", U1, T0)
        seq = [hinted] + [replace(a, recorded_at=a.recorded_at + H) for a in random_attempts(rng, 6) if a.unit == U1]
        e = next(x for x in Q.build(seq, [task], (), CFG, T0 + 1000 * H) if x.unit == U1)
        wrongs = [a for a in seq if Q.wrong_reason(a, CFG, T0 + 1000 * H) is None]
        if not wrongs:
            expect = any(Q.independent_correct(a) and a.item_id != "I1" for a in seq)
            assert e.source == "hint_followup" and (e.status == "closed") == expect, seed
