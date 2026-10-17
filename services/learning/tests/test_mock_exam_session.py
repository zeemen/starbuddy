"""PLT-18 模考第一步：限时状态机与会话票据（specs/mock-exam-plt18/spec.md 范围第 2 条）。纯函数，时间全部由测试注入；全部合成数据。

- 全部转移：created → in_progress → submitted | expired；非法转移的错误码；
- 截止时间只在开始时由服务端算（开始 + 限时 + 宽限）；截止前 1ms 收、截止时刻与之后拒；宽限内仍收；
- 到截止未交卷自动 expired（结束时刻 = 截止时刻），截止时刻交卷也是 expired；交卷后、过期后作答拒绝；
- 同题重复作答取最后一次；时钟回拨拒绝且会话不动；
- 票据：往返一致；篡改载荷、篡改签名、换密钥、未知 kid、换前缀、换卷（ID / 版本）、换学生一律 TICKET_INVALID；过期 TICKET_EXPIRED；
  轮换（当前 + 上一把，按 kid 选）；签发时间在未来、载荷形状或时序不一致（即使签名对）也无效；密钥不合规是 SigningKeyError；
- 重放的两种已知攻击用 xfail(strict=True) 记录：第一步纯函数不防，第二步会话表上线前必须解决。
"""
from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from learning.mock_exam import config as C
from learning.mock_exam import session as S
from test_mock_exam_manifest import CFG, load_daofa, load_math

T0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
MS = timedelta(milliseconds=1)
MIN = timedelta(minutes=1)
STUDENT = "5b8e1c52-0000-4000-8000-0000000000aa"
OTHER_STUDENT = "5b8e1c52-0000-4000-8000-0000000000bb"
SESSION_ID = "5b8e1c52-0000-4000-8000-00000000e501"
# 明显合成的签名密钥（只用于测试；部署时由调用方注入，模考单独一组，不与 BFF、再练复用）
TEST_SIGNING_KEY = b"synthetic-mock-exam-signing-key-0000000001"
OLD_SIGNING_KEY = b"synthetic-mock-exam-signing-key-0000000000"
OTHER_SIGNING_KEY = b"synthetic-mock-exam-signing-key-0000000002"
KEYS = S.SigningKeys("syn-k2", TEST_SIGNING_KEY, "syn-k1", OLD_SIGNING_KEY)
MATH = load_math()
DAOFA = load_daofa()
I1, I2 = MATH.items[0].item_id, MATH.items[1].item_id
DEADLINE = T0 + 120 * MIN + timedelta(seconds=30)


def started(paper=MATH, at=T0, cfg=CFG) -> S.Session:
    s = S.create(paper, session_id=SESSION_ID, student=STUDENT, now=at - MIN)
    return S.start(s, paper, cfg, now=at)


def err(fn) -> str:
    with pytest.raises(S.SessionError) as e:
        fn()
    return e.value.code


def terr(fn) -> str:
    with pytest.raises(S.TicketError) as e:
        fn()
    return e.value.code


# ---------- 转移 ----------
def test_create_and_start():
    s = S.create(MATH, session_id=SESSION_ID, student=STUDENT, now=T0 - MIN)
    assert (s.state, s.started_ms, s.deadline_ms, s.answers) == ("created", None, None, ())
    s2 = S.start(s, MATH, CFG, now=T0)
    assert s2.state == "in_progress"
    assert s2.started_ms == S.ms(T0)
    assert s2.deadline_ms == S.ms(DEADLINE)                  # 开始 + 限时 120 分钟 + 宽限 30 秒
    assert err(lambda: S.start(s2, MATH, CFG, now=T0 + MIN)) == "ALREADY_STARTED"


def test_grace_comes_from_config():
    no_grace = replace(CFG, grace_seconds=0)
    assert started(cfg=no_grace).deadline_ms == S.ms(T0 + 120 * MIN)
    s = started()
    s = S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=T0 + 120 * MIN + timedelta(seconds=10))   # 宽限内
    assert s.answer_of(I1).answer_ref == "att-1"
    assert err(lambda: S.answer(started(cfg=no_grace), MATH, item_id=I1, answer_ref="att-1",
                                now=T0 + 120 * MIN + timedelta(seconds=10))) == "DEADLINE_PASSED"


def test_deadline_uses_paper_duration():
    s = started(paper=DAOFA)
    assert s.deadline_ms == S.ms(T0 + 60 * MIN + timedelta(seconds=30))


def test_not_started_rejects_answer_and_submit():
    s = S.create(MATH, session_id=SESSION_ID, student=STUDENT, now=T0)
    assert err(lambda: S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=T0)) == "NOT_STARTED"
    assert err(lambda: S.submit(s, now=T0)) == "NOT_STARTED"
    assert S.advance(s, now=T0 + 500 * MIN).state == "created"      # 没开始就没有截止


def test_answer_deadline_boundaries():
    s = started()
    ok = S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=DEADLINE - MS)
    assert ok.answer_of(I1).at_ms == S.ms(DEADLINE) - 1
    assert err(lambda: S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=DEADLINE)) == "DEADLINE_PASSED"
    assert err(lambda: S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=DEADLINE + MS)) == "DEADLINE_PASSED"
    assert err(lambda: S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=DEADLINE + 60 * MIN)) == "DEADLINE_PASSED"


def test_advance_expires_at_deadline():
    s = S.answer(started(), MATH, item_id=I1, answer_ref="att-1", now=T0 + MIN)
    assert S.advance(s, now=DEADLINE - MS).state == "in_progress"
    e = S.advance(s, now=DEADLINE)
    assert (e.state, e.ended_ms, e.timed_out) == ("expired", S.ms(DEADLINE), True)
    assert e.answers == s.answers                                     # 已作答部分保留，照常计分
    late = S.advance(s, now=DEADLINE + 30 * MIN)
    assert (late.state, late.ended_ms) == ("expired", S.ms(DEADLINE))  # 结束时刻记截止时刻，不是发现时刻


def test_submit_before_deadline():
    s = started()
    d = S.submit(s, now=DEADLINE - MS)
    assert (d.state, d.ended_ms, d.timed_out) == ("submitted", S.ms(DEADLINE) - 1, False)
    assert err(lambda: S.submit(d, now=DEADLINE - MS)) == "ALREADY_SUBMITTED"
    assert err(lambda: S.answer(d, MATH, item_id=I1, answer_ref="att-1", now=DEADLINE - MS)) == "ALREADY_SUBMITTED"
    assert S.advance(d, now=DEADLINE + MIN).state == "submitted"      # 交卷后不会再变成过期


def test_submit_at_or_after_deadline_expires():
    s = started()
    for at in (DEADLINE, DEADLINE + MS, DEADLINE + 10 * MIN):
        e = S.submit(s, now=at)
        assert (e.state, e.ended_ms) == ("expired", S.ms(DEADLINE))


def test_expired_rejects_everything():
    e = S.advance(started(), now=DEADLINE)
    assert err(lambda: S.answer(e, MATH, item_id=I1, answer_ref="att-1", now=DEADLINE + MS)) == "EXPIRED"
    assert err(lambda: S.submit(e, now=DEADLINE + MS)) == "EXPIRED"
    assert err(lambda: S.start(e, MATH, CFG, now=DEADLINE + MS)) == "ALREADY_STARTED"


def test_repeated_answer_keeps_last():
    s = started()
    s = S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=T0 + MIN)
    s = S.answer(s, MATH, item_id=I2, answer_ref="att-2", now=T0 + 2 * MIN)
    s = S.answer(s, MATH, item_id=I1, answer_ref="att-3", now=T0 + 3 * MIN)
    assert len(s.answers) == 2
    a = s.answer_of(I1)
    assert (a.answer_ref, a.at_ms) == ("att-3", S.ms(T0 + 3 * MIN))
    assert s.answer_of(I2).answer_ref == "att-2"
    # 截止之后的“重答”不覆盖截止前的最后一次
    assert err(lambda: S.answer(s, MATH, item_id=I1, answer_ref="att-4", now=DEADLINE)) == "DEADLINE_PASSED"
    assert S.advance(s, now=DEADLINE).answer_of(I1).answer_ref == "att-3"


def test_clock_rollback_rejected():
    s = S.answer(started(), MATH, item_id=I1, answer_ref="att-1", now=T0 + 10 * MIN)
    assert err(lambda: S.answer(s, MATH, item_id=I2, answer_ref="att-2", now=T0 + 10 * MIN - MS)) == "CLOCK_ROLLBACK"
    assert err(lambda: S.submit(s, now=T0 + 5 * MIN)) == "CLOCK_ROLLBACK"
    assert err(lambda: S.advance(s, now=T0)) == "CLOCK_ROLLBACK"
    assert S.answer(s, MATH, item_id=I2, answer_ref="att-2", now=T0 + 10 * MIN).answer_of(I2) is not None   # 同一时刻可以
    # 过期之后把时钟拨回截止前也不能再作答
    e = S.advance(s, now=DEADLINE + MIN)
    assert err(lambda: S.answer(e, MATH, item_id=I2, answer_ref="att-2", now=DEADLINE - MIN)) == "CLOCK_ROLLBACK"
    c = S.create(MATH, session_id=SESSION_ID, student=STUDENT, now=T0)
    assert err(lambda: S.start(c, MATH, CFG, now=T0 - MS)) == "CLOCK_ROLLBACK"


def test_input_checks():
    s = started()
    assert err(lambda: S.answer(s, MATH, item_id="SYN-MOCK-LAW-S01", answer_ref="att-1", now=T0)) == "ITEM_NOT_IN_PAPER"
    assert err(lambda: S.answer(s, DAOFA, item_id="SYN-MOCK-LAW-S01", answer_ref="att-1", now=T0)) == "PAPER_MISMATCH"
    assert err(lambda: S.answer(s, MATH, item_id=I1, answer_ref="has space", now=T0)) == "INVALID_INPUT"
    assert err(lambda: S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=T0.replace(tzinfo=None))) == "INVALID_TIME"
    assert err(lambda: S.create(MATH, session_id="not-a-uuid", student=STUDENT, now=T0)) == "INVALID_INPUT"
    assert err(lambda: S.create(MATH, session_id=SESSION_ID, student="not-a-uuid", now=T0)) == "INVALID_INPUT"
    c = S.create(MATH, session_id=SESSION_ID, student=STUDENT, now=T0)
    assert err(lambda: S.start(c, DAOFA, CFG, now=T0)) == "PAPER_MISMATCH"


def test_client_time_cannot_move_deadline():
    """截止只取开始时服务端算出的值：后续作答的时刻（无论多早）不改它。"""
    s = started()
    s2 = S.answer(s, MATH, item_id=I1, answer_ref="att-1", now=T0 + MIN)
    assert s2.deadline_ms == s.deadline_ms == S.ms(DEADLINE)


# ---------- 票据 ----------
def sealed(s: S.Session | None = None, at=T0 + MIN, keys=KEYS) -> str:
    s = s or S.answer(started(), MATH, item_id=I1, answer_ref="att-1", now=T0 + MIN)
    return S.seal(keys, s, now=at)


def opened(t: str, *, keys=KEYS, paper=MATH, student=STUDENT, now=T0 + 2 * MIN) -> S.Session:
    return S.unseal(keys, t, paper=paper, student=student, cfg=CFG, now=now)


def test_ticket_roundtrip():
    s = S.answer(started(), MATH, item_id=I1, answer_ref="att-1", now=T0 + MIN)
    s = S.answer(s, MATH, item_id=I2, answer_ref="att-2", now=T0 + 2 * MIN)
    t = S.seal(KEYS, s, now=T0 + 2 * MIN)
    assert t.startswith("me1.syn-k2.") and t.count(".") == 3
    assert opened(t, now=T0 + 3 * MIN) == s
    for st in (S.create(MATH, session_id=SESSION_ID, student=STUDENT, now=T0), S.submit(s, now=T0 + 3 * MIN),
               S.advance(s, now=DEADLINE)):
        assert opened(S.seal(KEYS, st, now=T0 + 5 * MIN), now=T0 + 5 * MIN) == st


def test_key_rotation_by_kid():
    old = S.SigningKeys("syn-k1", OLD_SIGNING_KEY)
    t_old = sealed(keys=old)
    assert opened(t_old).student == STUDENT                          # 上一把签的票据在轮换期仍认
    assert terr(lambda: opened(t_old, keys=S.SigningKeys("syn-k2", TEST_SIGNING_KEY))) == "TICKET_INVALID"   # 上一把下线后不认
    _, kid, payload, sig = t_old.split(".")
    assert terr(lambda: opened(f"me1.syn-k2.{payload}.{sig}")) == "TICKET_INVALID"   # 改 kid 让别的密钥验
    assert terr(lambda: opened(f"me1.syn-k9.{payload}.{sig}")) == "TICKET_INVALID"   # 未知 kid
    unknown_head = f"me1.syn-k9.{payload}"                           # 未知 kid、用已知的上一把密钥正确签名：也不认
    assert terr(lambda: opened(f"{unknown_head}.{S._sign(OLD_SIGNING_KEY, unknown_head)}")) == "TICKET_INVALID"
    assert sealed().split(".")[1] == "syn-k2"                        # 只用当前密钥签发


def test_signing_key_config_errors():
    for bad in (lambda: S.SigningKeys("syn-k1", b"short"), lambda: S.SigningKeys("Bad Kid", TEST_SIGNING_KEY),
                lambda: S.SigningKeys("syn-k1", TEST_SIGNING_KEY, "syn-k1", OLD_SIGNING_KEY),
                lambda: S.SigningKeys("syn-k1", TEST_SIGNING_KEY, "syn-k0", TEST_SIGNING_KEY),
                lambda: S.SigningKeys("syn-k1", TEST_SIGNING_KEY, "syn-k0", b"short"),
                lambda: S.SigningKeys("syn-k1", "a-string-key-is-not-bytes-0000000000")):
        with pytest.raises(S.SigningKeyError):
            bad()
    assert issubclass(S.SigningKeyError, C.MockExamConfigError) and not issubclass(S.SigningKeyError, S.TicketError)
    with pytest.raises(S.SigningKeyError):
        S.seal(TEST_SIGNING_KEY, started(), now=T0)                 # 裸密钥不收
    with pytest.raises(S.SigningKeyError):
        S.unseal(TEST_SIGNING_KEY, sealed(), paper=MATH, student=STUDENT, cfg=CFG, now=T0)


def _flip(c: str) -> str:
    return "A" if c != "A" else "B"


def test_ticket_tamper_rejected():
    t = sealed()
    head, kid, payload, sig = t.split(".")
    assert terr(lambda: opened(f"{head}.{kid}.{payload[:-1]}{_flip(payload[-1])}.{sig}")) == "TICKET_INVALID"
    assert terr(lambda: opened(f"{head}.{kid}.{payload}.{sig[:-1]}{_flip(sig[-1])}")) == "TICKET_INVALID"
    assert terr(lambda: opened(f"{head}.{kid}.{payload}.{_flip(sig[0])}{sig[1:]}")) == "TICKET_INVALID"
    assert terr(lambda: opened(f"{head}.{kid}.{payload}.{sig[:-4]}")) == "TICKET_INVALID"
    assert terr(lambda: opened(f"{head}.{kid}.{payload}.")) == "TICKET_INVALID"
    assert terr(lambda: opened(f"pt1.{kid}.{payload}.{sig}")) == "TICKET_INVALID"
    assert terr(lambda: opened(f"{head}.{payload}.{sig}")) == "TICKET_INVALID"
    assert terr(lambda: opened(t, keys=S.SigningKeys("syn-k2", OTHER_SIGNING_KEY))) == "TICKET_INVALID"
    # 改载荷（延后截止）再用别的密钥重签：不认
    d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    d["deadline_ms"] += 3_600_000
    forged_payload = base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    forged_head = f"me1.{kid}.{forged_payload}"
    assert terr(lambda: opened(f"{forged_head}.{S._sign(OTHER_SIGNING_KEY, forged_head)}")) == "TICKET_INVALID"
    assert terr(lambda: opened(f"{forged_head}.{sig}")) == "TICKET_INVALID"
    for bad in (None, 123, "", "me1", "a.b.c.d.e", "x" * (S.MAX_LEN + 1)):
        assert terr(lambda: opened(bad)) == "TICKET_INVALID"


def test_ticket_bound_to_paper_and_student():
    t = sealed()
    assert terr(lambda: opened(t, student=OTHER_STUDENT)) == "TICKET_INVALID"
    assert terr(lambda: opened(t, paper=DAOFA)) == "TICKET_INVALID"
    assert terr(lambda: opened(t, paper=replace(MATH, version="0.1.1"))) == "TICKET_INVALID"
    assert terr(lambda: opened(t, paper=replace(MATH, paper_id="SYN-MOCK-MATH-FUN-02"))) == "TICKET_INVALID"
    assert terr(lambda: opened(t, paper=replace(MATH, subject="zhixing"))) == "TICKET_INVALID"
    assert opened(t).student == STUDENT


def test_ticket_expiry():
    t = sealed(at=T0 + MIN)
    ttl = CFG.ticket_ttl_minutes * MIN
    assert opened(t, now=T0 + MIN + ttl).state == "in_progress"       # 恰好到期仍有效
    assert terr(lambda: opened(t, now=T0 + MIN + ttl + MS)) == "TICKET_EXPIRED"
    assert terr(lambda: opened(t, now=T0 + MIN - 6 * MIN)) == "TICKET_INVALID"   # 签发时间在未来（超过容差）
    assert opened(t, now=T0 + MIN - 4 * MIN).session_id == SESSION_ID          # 容差内


def _signed(payload: dict) -> str:
    head = "me1.syn-k2." + base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{head}.{S._sign(TEST_SIGNING_KEY, head)}"


GOOD_S = S.answer(started(), MATH, item_id=I1, answer_ref="att-1", now=T0 + MIN)
GOOD = S._payload(GOOD_S, S.ms(T0 + MIN))
START, DL = GOOD["started_ms"], GOOD["deadline_ms"]
SHAPE_BREAKS = {
    "卷里没有的题": {"answers": [["SYN-MOCK-LAW-S01", "att-1", START]]},
    "同题两条": {"answers": [[I1, "att-1", START], [I1, "att-2", START]]},
    "状态不认识": {"state": "paused"},
    "未开始却有开始时间": {"state": "created"},
    "进行中没有截止": {"deadline_ms": None},
    "已交卷没有结束时间": {"state": "submitted"},
    "进行中却有结束时间": {"ended_ms": START},
    "学生不是 UUID": {"student": "not-a-uuid"},
    "签发时间是布尔": {"issued_ms": True},
    "多余字段": {"extra": 1},
}
TIMELINE_BREAKS = {
    "开始早于建立": {"created_ms": START + 1},
    "截止早于开始": {"deadline_ms": START - 1, "seen_ms": START},
    "作答早于开始": {"answers": [[I1, "att-1", START - 1]]},
    "作答在截止时刻": {"answers": [[I1, "att-1", DL]], "seen_ms": DL},
    "seen 早于作答": {"seen_ms": START},
    "seen 早于开始": {"seen_ms": START - 1, "answers": []},
    "交卷在截止时刻": {"state": "submitted", "ended_ms": DL, "seen_ms": DL},
    "交卷早于开始": {"state": "submitted", "ended_ms": START - 1},
    "过期结束时刻不是截止": {"state": "expired", "ended_ms": DL + 1, "seen_ms": DL + 1},
    "seen 早于结束": {"state": "expired", "ended_ms": DL, "seen_ms": DL - 1},
}


def test_good_signed_payload_opens():
    assert opened(_signed(GOOD)) == GOOD_S
    assert opened(_signed({**GOOD, "state": "expired", "ended_ms": DL, "seen_ms": DL})).state == "expired"
    assert opened(_signed({**GOOD, "state": "submitted", "ended_ms": DL - 1, "seen_ms": DL - 1})).state == "submitted"


@pytest.mark.parametrize("name", list(SHAPE_BREAKS) + list(TIMELINE_BREAKS))
def test_signed_but_inconsistent_payload_rejected(name):
    b = SHAPE_BREAKS.get(name) or TIMELINE_BREAKS[name]
    assert terr(lambda: opened(_signed({**GOOD, **b}))) == "TICKET_INVALID"


def test_signed_payload_missing_field_rejected():
    missing = dict(GOOD)
    del missing["seen_ms"]
    assert terr(lambda: opened(_signed(missing))) == "TICKET_INVALID"


# ---------- 重放（第一步不防：第二步会话表唯一约束 + 状态版本号上线前必须解决） ----------
@pytest.mark.xfail(strict=True, reason="已知缺口（第二步前置条件）：created 票据可以重复开始、重置截止；需会话表唯一约束")
def test_replay_created_ticket_cannot_restart():
    created = S.create(MATH, session_id=SESSION_ID, student=STUDENT, now=T0)
    t = S.seal(KEYS, created, now=T0)
    first = S.start(opened(t, now=T0), MATH, CFG, now=T0)
    assert first.state == "in_progress"
    with pytest.raises(S.SessionError):
        S.start(opened(t, now=T0 + 60 * MIN), MATH, CFG, now=T0 + 60 * MIN)


@pytest.mark.xfail(strict=True, reason="已知缺口（第二步前置条件）：交卷后用旧 in_progress 票据可回滚重答、重复交卷；需状态版本号单调前进")
def test_replay_old_ticket_after_submit_rejected():
    s = S.answer(started(), MATH, item_id=I1, answer_ref="att-1", now=T0 + MIN)
    old = S.seal(KEYS, s, now=T0 + MIN)
    S.submit(s, now=T0 + 2 * MIN)
    with pytest.raises(S.SessionError):
        S.answer(opened(old, now=T0 + 3 * MIN), MATH, item_id=I1, answer_ref="att-2", now=T0 + 3 * MIN)
