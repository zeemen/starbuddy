"""“刷同题”无法点亮（开发计划 v2.0 冲刺 4 KGE-04 验收；spec 第 6 节与验收第 1 条）。配置用已签字的合成配置。"""
from __future__ import annotations

from learning.mastery import replay, write_decision
from learning.mastery import evidence as E
from learning.mastery.gates import RANK
from test_mastery_support import DAY, HOUR, MIN, T0, UNIT, ev, normal_path, signed_cfg, with_

S = signed_cfg()


def test_same_item_every_day_with_new_keys_stays_at_understand():
    evs = [ev(T0 + i * DAY, None, item_id="syn-item-same", kind="independent") for i in range(30)]
    rep = replay(evs, UNIT, S, T0 + 31 * DAY)
    assert RANK[rep.result.state] <= RANK["understand"]
    assert len(rep.result.evidence_ids) == 1
    assert [r for _, r in rep.excluded] == [(E.REPEAT_FAMILY,)] * 29


def test_many_items_of_one_family_count_once():
    kinds = ["independent", "transfer", "delayed_retest"]
    types = ["syn-solve", "syn-choice", "syn-proof"]
    evs = [ev(T0 + i * DAY, "fam-one", kind=kinds[i % 3], item_type=types[i % 3]) for i in range(30)]
    rep = replay(evs, UNIT, S, T0 + 31 * DAY)
    assert RANK[rep.result.state] <= RANK["understand"]
    assert rep.result.p_mastery == replay(evs[:1], UNIT, S, T0 + 31 * DAY).result.p_mastery


def test_replayed_request_and_duplicates_change_nothing():
    evs = normal_path()
    dup_same_id = [with_(e) for e in evs]                                   # 同一证据重复提交
    dup_attempt = [with_(e, evidence_id=e.evidence_id + "-x") for e in evs]  # 同一作答另起证据 ID
    base = replay(evs, UNIT, S, T0 + 14 * DAY)
    for extra in (dup_same_id, dup_attempt):
        rep = replay(evs + extra, UNIT, S, T0 + 14 * DAY)
        assert rep.result == base.result
        assert all(r == (E.DUPLICATE,) for _, r in rep.excluded)
    # 掌握记录只写一次：第二次比较不写
    from test_mastery_gates import _stored
    assert write_decision(_stored(base.result), replay(evs + dup_same_id, UNIT, S, T0 + 14 * DAY).result, S)[0] is False


def test_many_families_one_item_type_group_stops_at_execute():
    evs = [ev(T0 + i * DAY, f"fam-{i}", item_type="syn-solve", kind="transfer" if i == 5 else "independent") for i in range(12)]
    rep = replay(evs, UNIT, S, T0 + 13 * DAY)
    assert rep.result.state == "execute"
    # 同组的两种题型（syn-choice 与 syn-fill 同属一组）也不算两组
    evs2 = [ev(T0 + i * DAY, f"fam-{i}", item_type="syn-choice" if i % 2 else "syn-fill") for i in range(12)]
    assert replay(evs2, UNIT, S, T0 + 13 * DAY).result.state == "execute"


def test_heavy_hints_never_move_mastery():
    evs = [ev(T0 + i * HOUR, f"fam-{i}", kind="hinted", hint=6, item_type=["syn-solve", "syn-proof"][i % 2]) for i in range(20)]
    rep = replay(evs, UNIT, S, T0 + 2 * DAY)
    assert rep.result.state is None and rep.result.p_mastery == S["bkt"]["p_init"]
    assert [t.reason for t in rep.tasks] == ["hint_followup"]


def test_redo_family_after_hint_or_guess_not_counted():
    evs = [ev(T0, "fam-a", kind="hinted", hint=5), ev(T0 + MIN, "fam-a"),
           ev(T0 + 2 * MIN, "fam-b", self_report="guessed"), ev(T0 + 3 * MIN, "fam-b")]
    rep = replay(evs, UNIT, S, T0 + HOUR)
    assert rep.result.state is None
    assert [r for _, r in rep.excluded] == [(E.HINT_HEAVY,), (E.REPEAT_FAMILY,), (E.GUESSED,), (E.REPEAT_FAMILY,)]


def test_spam_within_retest_window_is_not_a_retest():
    evs = normal_path()[:2]
    spam = [ev(evs[1].recorded_at + i * 10 * MIN, f"fam-s{i}", item_type="syn-proof") for i in range(1, 11)]
    rep = replay(evs + spam, UNIT, S, evs[1].recorded_at + 2 * HOUR + MIN)
    # 到期前的十道都不算复测；到期后第一道新族题才算（这里没有到期后的作答）
    early = [s for s in spam if s.recorded_at < evs[1].recorded_at + 2 * HOUR]
    assert all((s.evidence_id, E.RETEST_TOO_EARLY) in rep.annotations for s in early)
    assert rep.result.state == "execute" and rep.retests == []


def test_photo_spam_never_lights():
    evs = [with_(e, evidence_level="B") for e in normal_path()]
    more = [ev(T0 + (14 + i) * DAY, f"fam-p{i}", level="B", kind="transfer") for i in range(20)]
    assert replay(evs + more, UNIT, S, T0 + 40 * DAY).result.state == "stable"


def test_unadmitted_content_never_lights():
    evs = [with_(e, admission="source_unverified") for e in normal_path()]
    more = [ev(T0 + (14 + i) * DAY, f"fam-u{i}", admission="pending_review", kind="transfer") for i in range(20)]
    assert replay(evs + more, UNIT, S, T0 + 40 * DAY).result.state == "stable"
