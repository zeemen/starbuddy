"""KGE-05 复测与验证题交付：纯函数单测与性质测试（specs/retest-delivery-kge05/spec.md）。不连库、不发 HTTP；全部合成数据。

- 配置：合成占位只在 test / dev；8 周复测点默认关、天数为空；打开必须给天数；未签字在 pilot 拒绝；
- 任务 → 条目：只列本模块的原因、到期才出现、用途按原因由服务端定（签字前保守口径：只有衰减 retest，其余 practice，没有 transfer_check）、
  hint_followup 不列；自述验证题只统计用本任务票据（锚点）作答、有判分的题，做满 verify_items_per_task 道才算做完；参照题取最近一次作答；
- 8 周复测点：默认关时一条都没有；打开时只对有当前掌握记录、没有开着的任务、最近一次作答满天数的单元；
- 列表截断、条目 ID 不含可关联的学生信息、锚点用途解析；
- 票据：复测票据（rt1）与再练票据（pt1）互不通用；伪造、篡改、过期被拒；
- 性质（固定种子随机序列）：确定性与输入顺序无关；追加作答不会让已做完的自述验证重新打开；条目用途永远是 practice / retest。
"""
from __future__ import annotations

import random
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest

from learning.practice import ticket as T
from learning.retest import config as C
from learning.retest import tasks as K
from test_practice_pure import SECRET, STUDENT, T0, U1, U2, att, ok

H, D = timedelta(hours=1), timedelta(days=1)
CFG = C.parse(C.SYNTHETIC, env="test")


def cfg(**over) -> C.RetestConfig:
    return C.parse({**C.SYNTHETIC, **over}, env="test")


def task(reason: str, due, unit=U1, tid: str | None = None) -> K.TaskRow:
    return K.TaskRow(tid or str(uuid.uuid4()), unit, reason, due)


# ---------- 配置 ----------
def test_synthetic_config_defaults_and_envs():
    for env in ("test", "dev"):
        c = C.load("synthetic", env=env)
        assert c.synthetic and not c.signed
        # 8 周复测点：默认关、天数为空（数值等教研签字）
        assert c.followup_enabled is False and c.followup_after_days is None
        assert c.subjects_enabled == ("aya", "zhixing") and c.verify_items_per_task == 2
    for env in ("pilot", "prod", ""):
        with pytest.raises(C.RetestConfigUnsigned):
            C.load("synthetic", env=env)


def test_signed_config_loads_anywhere():
    doc = {**C.SYNTHETIC, "synthetic": False, "signed_by": "synthetic-signer", "signed_at": "2026-10-01T00:00:00+08:00"}
    assert C.parse(doc, env="pilot").signed
    with pytest.raises(C.RetestConfigUnsigned):
        C.parse({**doc, "signed_by": None, "signed_at": None}, env="pilot")


@pytest.mark.parametrize("over", [
    {"followup_enabled": True},                          # 打开必须给天数
    {"followup_after_days": 0}, {"followup_after_days": 366}, {"followup_after_days": 56.0}, {"followup_after_days": True},
    {"followup_enabled": "yes"}, {"ticket_ttl_minutes": 0}, {"queue_cap": 101}, {"verify_items_per_task": 0},
    {"verify_items_per_task": 6}, {"subjects_enabled": ["english"]}, {"subjects_enabled": ["aya", "aya"]},
    {"format": "starbuddy-retest-config/2"}, {"signed_by": "x"},       # 合成占位不能带签字
    {"signed_at": "2026-10-01"},
])
def test_config_rejects_bad_values(over):
    with pytest.raises(C.RetestConfigError):
        C.parse({**C.SYNTHETIC, **over}, env="test")


def test_config_rejects_unknown_or_missing_keys(tmp_path):
    with pytest.raises(C.RetestConfigError):
        C.parse({**C.SYNTHETIC, "purpose": "retest"}, env="test")
    d = dict(C.SYNTHETIC)
    d.pop("followup_after_days")
    with pytest.raises(C.RetestConfigError):
        C.parse(d, env="test")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(C.RetestConfigError):
        C.load(str(bad), env="test")
    good = tmp_path / "good.json"
    good.write_text(__import__("json").dumps({**C.SYNTHETIC, "followup_enabled": True, "followup_after_days": 56}), encoding="utf-8")
    assert C.load(str(good), env="test").followup_after_days == 56


# ---------- 任务 → 条目 ----------
@pytest.mark.parametrize("reason,purpose,kind", [
    ("scheduled", "practice", "retest"), ("early", "practice", "retest"), ("decay", "retest", "retest"),
    ("verify_in_platform", "practice", "verify"), ("verify_self_report", "practice", "verify"),
])
def test_purpose_comes_from_task_reason(reason, purpose, kind):
    seq = [att(T0, "I1")]
    t = task(reason, T0 + H)
    e = K.task_entry(t, seq, {}, CFG, T0 + 2 * H)
    assert e is not None and (e.purpose, e.kind, e.status) == (purpose, kind, "due")
    assert e.anchor == f"{purpose}:{t.task_id}" and K.purpose_of_anchor(e.anchor) == purpose
    assert e.source_item_id == "I1"


def test_hint_followup_and_unknown_reasons_are_not_listed():
    for reason in ("hint_followup", "something_else"):
        assert K.task_entry(task(reason, T0), [], {}, CFG, T0 + H) is None
    assert K.build([task("hint_followup", T0)], [], {}, (), CFG, T0 + H) == []


def test_not_yet_due_is_not_listed():
    assert K.task_entry(task("scheduled", T0 + H), [], {}, CFG, T0) is None
    e = K.task_entry(task("scheduled", T0 + H), [], {}, CFG, T0 + H)
    assert e is not None and e.status == "due"                  # 到期那一刻就出现


def test_source_item_is_latest_attempt_in_unit_before_now():
    seq = [att(T0, "I1"), ok(T0 + H, "I2"), att(T0 + 2 * H, "I9", unit=U2), ok(T0 + 5 * H, "I3")]
    assert K.source_item(seq, U1, T0 + 3 * H) == "I2"
    assert K.source_item(seq, U1, T0 + 6 * H) == "I3"
    assert K.source_item(seq, U1, T0 - H) is None
    e = K.task_entry(task("decay", T0, unit=U2), seq, {}, CFG, T0 + 3 * H)
    assert e is not None and e.source_item_id == "I9"
    # 单元没有作答：参照题为空（service 标 unavailable no_source_item）
    assert K.task_entry(task("verify_self_report", T0, unit=U2), [], {}, CFG, T0 + H).source_item_id is None


def mine(a, key: str, anchor: str):
    """把一次合成作答标成“用这把键、这个锚点的票据作答”（作答 ID 按 attempt_id_for 派生）。"""
    return replace(a, attempt_id=K.attempt_id_for(key, anchor))


def test_attempt_id_for_is_deterministic_v4_and_bound_to_anchor():
    k, a = str(uuid.uuid4()), K.anchor_of("verify_self_report", str(uuid.uuid4()))
    x = K.attempt_id_for(k, a)
    assert x == K.attempt_id_for(k, a) and uuid.UUID(x).version == 4
    assert x != K.attempt_id_for(k, K.anchor_of("verify_self_report", str(uuid.uuid4())))
    assert x != K.attempt_id_for(str(uuid.uuid4()), a)


def test_self_report_verification_counts_only_answers_with_this_tasks_ticket():
    t = task("verify_self_report", T0 + H)
    anchor = K.anchor_of(t.reason, t.task_id)
    k1, k2, k3, k4, k5 = (str(uuid.uuid4()) for _ in range(5))
    a1, a2 = mine(ok(T0 + 2 * H, "I2"), k1, anchor), mine(att(T0 + 3 * H, "I3"), k2, anchor)   # 对错都算“做了一道”
    other_ticket = mine(ok(T0 + 4 * H, "I4"), k3, K.anchor_of("verify_self_report", str(uuid.uuid4())))   # 别的任务的票据
    plain = ok(T0 + 4 * H, "I7")                                                            # 不经复测票据的同单元作答
    ungraded = mine(att(T0 + 5 * H, "I5", graded=False), k4, anchor)                         # 没有判分
    elsewhere = mine(ok(T0 + 6 * H, "I6", unit=U2), k5, anchor)                              # 别的单元
    keys = [k1, k2, k3, k4, k5]
    now = T0 + 7 * H
    assert K.task_entry(t, [a1, other_ticket, plain, ungraded, elsewhere], keys, CFG, now).status == "due"
    assert K.task_entry(t, [a1, a2, other_ticket, plain, ungraded, elsewhere], keys, CFG, now).status == "done"
    assert K.task_entry(t, [a1, a2], [k1], CFG, now).status == "due"                        # 键不在已成功的判分里：不算
    assert K.task_entry(t, [a1, a2], keys, cfg(verify_items_per_task=3), now).status == "due"
    # 平台内验证（KGE-04 自己会撤销）不按道数判做完
    assert K.task_entry(task("verify_in_platform", T0 + H), [a1, a2], keys, CFG, now).status == "due"
    # 做完的不在列表里，但按 ID 还找得到（作答后回 done）
    assert K.build([t], [a1, a2], keys, (), CFG, now) == []
    assert [e.status for e in K.build([t], [a1, a2], keys, (), CFG, now, capped=False)] == ["done"]


def test_build_orders_by_due_and_caps():
    ts = [task("scheduled", T0 + i * H, unit=K.Unit(f"MATH.SYN.T1.N{i}", "apply")) for i in range(5)]
    out = K.build(list(reversed(ts)), [], {}, (), cfg(queue_cap=3), T0 + 10 * H)
    assert [e.due_at for e in out] == [T0, T0 + H, T0 + 2 * H]
    assert len(K.build(ts, [], {}, (), cfg(queue_cap=3), T0 + 10 * H, capped=False)) == 5


def test_entry_id_is_per_student_and_anchor():
    a = K.anchor_of("scheduled", str(uuid.uuid4()))
    other = "5b8e1c52-0000-4000-8000-0000000000bb"
    assert K.entry_id(STUDENT, "aya", a) == K.entry_id(STUDENT, "aya", a)
    assert K.entry_id(STUDENT, "aya", a) != K.entry_id(other, "aya", a)
    assert K.entry_id(STUDENT, "aya", a) != K.entry_id(STUDENT, "zhixing", a)
    assert STUDENT not in K.entry_id(STUDENT, "aya", a)
    e = K.task_entry(K.TaskRow(a.split(":")[1], U1, "scheduled", T0), [], {}, CFG, T0)
    assert K.find([e], STUDENT, "aya", K.entry_id(STUDENT, "aya", e.anchor)) is e
    assert K.find([e], other, "aya", K.entry_id(STUDENT, "aya", e.anchor)) is None


@pytest.mark.parametrize("anchor", ["transfer_check:5b8e1c52-0000-4000-8000-0000000000aa", "retest", "retest:x",
                                    "practice:5B8E1C52-0000-4000-8000-0000000000AA", "", "00000000-0000-4000-8000-000000000001"])
def test_purpose_of_anchor_rejects_others(anchor):
    assert K.purpose_of_anchor(anchor) is None


# ---------- 8 周复测点（默认关） ----------
def test_followup_off_by_default():
    seq = [ok(T0, "I1")]
    assert K.followup_entries(seq, {U1}, (), CFG, T0 + 400 * D) == []
    assert K.build([], seq, {}, {U1}, CFG, T0 + 400 * D) == []
    # 天数填了但开关关着：仍然一条都没有
    assert K.followup_entries(seq, {U1}, (), cfg(followup_after_days=56), T0 + 400 * D) == []


def test_followup_when_enabled():
    c = cfg(followup_enabled=True, followup_after_days=56)
    last = ok(T0 + D, "I2")
    seq = [ok(T0, "I1"), last, ok(T0, "I7", unit=U2)]
    assert K.followup_entries(seq, {U1}, (), c, T0 + D + 55 * D) == []                   # 不满天数
    out = K.followup_entries(seq, {U1}, (), c, T0 + D + 56 * D)
    assert len(out) == 1
    e = out[0]
    assert (e.unit, e.reason, e.purpose, e.kind, e.source_item_id, e.due_at) == (U1, K.FOLLOWUP, "practice", "retest", "I2", T0 + 57 * D)
    assert K.followup_entries(seq, {U1}, {U1}, c, T0 + 100 * D) == []                    # 有开着的本模块任务：不重复
    assert K.followup_entries(seq, set(), (), c, T0 + 100 * D) == []                     # 没有当前掌握记录：不出
    assert K.followup_entries([], {U1}, (), c, T0 + 100 * D) == []                       # 没有作答：不出
    # 新作答之后锚点变了（旧票据作废），而且不再满天数
    newer = ok(T0 + 60 * D, "I3")
    assert K.followup_entries(seq + [newer], {U1}, (), c, T0 + 61 * D) == []
    later = K.followup_entries(seq + [newer], {U1}, (), c, T0 + 200 * D)
    assert later and later[0].anchor != e.anchor
    # build：任务所在的单元不再算 8 周复测点（未到期的任务也算占用）
    assert [x.reason for x in K.build([task("scheduled", T0 + 300 * D)], seq, {}, {U1}, c, T0 + 100 * D)] == []
    assert [x.reason for x in K.build([], seq, {}, {U1}, c, T0 + 100 * D)] == [K.FOLLOWUP]


# ---------- 票据：复测与再练不能互换 ----------
def test_retest_and_practice_tickets_are_not_interchangeable():
    t = T.Ticket(student=STUDENT, subject="aya", node_id=U1.node_id, cognitive_level=U1.cognitive_level,
                 anchor=K.anchor_of("scheduled", str(uuid.uuid4())), item_id="I2", round=1, issued_at=T.issued(T0))
    rt = T.issue(SECRET, t, prefix="rt1")
    pt = T.issue(SECRET, t)
    assert rt.startswith("rt1.") and pt.startswith("pt1.")
    assert T.verify(SECRET, rt, now=T0, ttl_minutes=60, prefix="rt1") == t
    with pytest.raises(T.TicketError):
        T.verify(SECRET, rt, now=T0, ttl_minutes=60)                       # 复测票据拿去再练
    with pytest.raises(T.TicketError):
        T.verify(SECRET, pt, now=T0, ttl_minutes=60, prefix="rt1")          # 再练票据拿来复测
    relabeled = "rt1." + pt.split(".", 1)[1]                                # 只改前缀：签名覆盖前缀，验不过
    with pytest.raises(T.TicketError) as e:
        T.verify(SECRET, relabeled, now=T0, ttl_minutes=60, prefix="rt1")
    assert e.value.code == "TICKET_INVALID"
    with pytest.raises(T.TicketError) as e:
        T.verify(SECRET, rt, now=T0 + 61 * timedelta(minutes=1), ttl_minutes=60, prefix="rt1")
    assert e.value.code == "TICKET_EXPIRED"


# ---------- 性质（固定种子） ----------
SEEDS = range(200)
REASONS = list(K.RETEST_REASONS) + ["hint_followup"]


def random_attempt(rng: random.Random, t, tasks, keys: list[str], units=(U1, U2, None)):
    """随机一次作答；有自述验证任务时，有时把它标成用其中一条任务的票据作答（并把键记进已成功的判分键，有时不记）。"""
    a = att(t, f"I{rng.randrange(6)}", rng.choice(["correct", "incorrect"]), unit=rng.choice(list(units)), graded=rng.random() > 0.1)
    verify = [x for x in tasks if x.reason == "verify_self_report"]
    if verify and rng.random() < 0.5:
        x = rng.choice(verify)
        k = str(uuid.UUID(int=rng.getrandbits(128)))
        a = replace(a, attempt_id=K.attempt_id_for(k, K.anchor_of(x.reason, x.task_id)), node_id=x.unit.node_id,
                    cognitive_level=x.unit.cognitive_level)
        if rng.random() < 0.9:
            keys.append(k)
    return a


def random_case(rng: random.Random):
    tasks = [task(rng.choice(REASONS), T0 + rng.choice([H, 10 * D, 90 * D]), unit=rng.choice([U1, U2]),
                  tid=str(uuid.UUID(int=rng.getrandbits(128)))) for _ in range(rng.randint(0, 4))]
    t = T0
    seq: list = []
    keys: list[str] = []
    for _ in range(rng.randint(0, 20)):
        t += rng.choice([timedelta(minutes=5), H, 30 * H, 20 * D])
        seq.append(random_attempt(rng, t, tasks, keys))
    est = {u for u in (U1, U2) if rng.random() < 0.5}
    c = cfg(followup_enabled=rng.random() < 0.5, followup_after_days=rng.choice([7, 56]), verify_items_per_task=rng.choice([1, 2]))
    now = t + rng.choice([H, 30 * D, 120 * D])
    return seq, keys, tasks, est, c, now


def snap(entries):
    return [(e.unit, e.reason, e.anchor, e.due_at, e.source_item_id, e.status) for e in entries]


def test_deterministic_order_independent_and_purpose_whitelist():
    for seed in SEEDS:
        rng = random.Random(seed)
        seq, keys, tasks, est, c, now = random_case(rng)
        a = K.build(tasks, seq, keys, est, c, now, capped=False)
        shuffled, ts, ks = list(seq), list(tasks), list(keys)
        rng.shuffle(shuffled)
        rng.shuffle(ts)
        rng.shuffle(ks)
        assert snap(a) == snap(K.build(ts, shuffled, ks, est, c, now, capped=False)), seed
        for e in a:
            # 签字前的保守口径：只有衰减写 retest，其余一律 practice；永远没有 transfer_check
            assert e.purpose == ("retest" if e.reason == "decay" else "practice") and e.reason != "hint_followup"
            assert e.due_at <= now and K.purpose_of_anchor(e.anchor) == e.purpose
        listed = K.build(tasks, seq, keys, est, c, now)
        assert all(e.status == "due" for e in listed) and len(listed) <= c.queue_cap


def test_done_self_report_stays_done_when_more_attempts_arrive():
    for seed in SEEDS:
        rng = random.Random(seed)
        seq, keys, tasks, est, c, now = random_case(rng)
        done = {e.anchor for e in K.build(tasks, seq, keys, est, c, now, capped=False)
                if e.reason == "verify_self_report" and e.status == "done"}
        more = list(seq)
        t = now
        for _ in range(rng.randint(1, 5)):
            t += H
            more.append(random_attempt(rng, t, tasks, keys, units=(U1, U2)))
        after = {e.anchor: e.status for e in K.build(tasks, more, keys, est, c, t, capped=False)}
        assert all(after.get(a) == "done" for a in done), seed
    # 性质确实覆盖到“做完”：随机序列里至少有一部分种子出现做完的自述验证
    assert sum(1 for seed in SEEDS if any(e.status == "done" for e in K.build(*_case_args(seed), capped=False))) > 5


def _case_args(seed: int):
    seq, keys, tasks, est, c, now = random_case(random.Random(seed))
    return tasks, seq, keys, est, c, now
