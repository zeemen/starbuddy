"""判断生成与生命周期（spec 设计要点 2；验收“状态按规则生成与流转，每次流转有原因与证据 ID；未签字配置下只能到假设”）。
纯函数，合成掌握行驱动；阈值是合成占位（非签字值）。"""
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

from insight.claims import ALLOWED, InputError, Op, apply_ops, check_ops, plan_node_claims
from support import NODE_A, NODE_B, STUDENT, T0, bid, eid, ids, make_policy, mrow, templates

UNSIGNED = make_policy()
SIGNED = make_policy(signed=True)
TPL = templates(UNSIGNED)
ROOT = Path(__file__).resolve().parents[3]


def run(existing, rows, policy=UNSIGNED, now=T0, tpl=None):
    ops = plan_node_claims(existing, rows, policy, now, tpl or templates(policy))
    return ops, apply_ops(existing, ops, now, ids())


def by_template(claims, key):
    return [c for c in claims if c.template_key == key]


def test_allowed_table_matches_migration_0010():
    """Python 白名单与 0010 insight.change_allowed 逐行一致（两处任何一处改了都会被发现）"""
    sql = (ROOT / "db/migrations/0010_insight__claim_ledger.up.sql").read_text(encoding="utf-8")
    body = sql.split("CREATE FUNCTION insight.change_allowed", 1)[1].split("WHERE t.f", 1)[0]
    rows = set()
    for f, t, r in re.findall(r"\((NULL(?:::insight\.claim_status)?|'[a-z_]+'),\s*'([a-z_]+)'(?:::insight\.claim_status)?,\s*'([a-z_]+)'\)", body):
        rows.add((None if f.startswith("NULL") else f.strip("'"), t, r))
    assert rows == set(ALLOWED) and len(rows) == 20


def test_unsigned_creates_hypothesis_with_reason_and_evidence():
    ops, claims = run([], [mrow(gate="stable", conf=0.95, evidence=(1, 2, 3, 4, 5, 6))])
    assert [(o.from_status, o.to_status, o.reason) for o in ops] == [(None, "hypothesis", "created")]
    c = claims[0]
    assert c.status == "hypothesis" and c.basis_kind == "mastery_estimate" and c.basis_id == bid(1)
    assert c.evidence_ids == tuple(eid(i) for i in range(1, 7)) and c.model_version == "algo:kge-mastery@0.1.0.syn"
    assert c.value == {"level": "apply", "gate": "stable"} and c.target_id == NODE_A and c.source_type == "inferred"
    assert c.expires_at == T0 + timedelta(days=30)


def test_signed_threshold_creates_established():
    row = mrow(gate="stable", conf=0.8, evidence=(1, 2, 3, 4, 5))
    ops, claims = run([], [row], SIGNED)
    assert ops[0].to_status == "established"
    # 任一条件不到：假设
    for weak in (mrow(gate="execute", conf=0.9, evidence=(1, 2, 3, 4, 5)), mrow(gate="stable", conf=0.6, evidence=(1, 2, 3, 4, 5)),
                 mrow(gate="stable", conf=0.9, evidence=(1, 2, 3, 4))):
        assert run([], [weak], SIGNED)[0][0].to_status == "hypothesis"


def test_hypothesis_to_established_by_evidence_threshold_only_when_signed():
    _, claims = run([], [mrow(gate="execute")], SIGNED)
    strong = mrow(basis=2, gate="stable", conf=0.8, evidence=(1, 2, 3, 4, 5), at=T0 + timedelta(days=1))
    ops, after = run(claims, [strong], SIGNED, T0 + timedelta(days=1))
    assert [(o.from_status, o.to_status, o.reason) for o in ops] == [("hypothesis", "established", "evidence_threshold")]
    assert after[0].basis_id == bid(2) and after[0].expires_at == claims[0].expires_at  # 不续期
    ops, _ = run(claims, [strong], UNSIGNED, T0 + timedelta(days=1))
    assert [o.reason for o in ops] == ["revised"] and ops[0].to_status == "hypothesis"


def test_transfer_claim_only_at_star_gates():
    ops, claims = run([], [mrow(gate="transfer")])
    keys = sorted(c.template_key for c in claims)
    assert keys == ["syn.math.node_mastery", "syn.math.node_transfer"]
    _, claims = run([], [mrow(gate="stable")])
    assert [c.template_key for c in claims] == ["syn.math.node_mastery"]


def test_expiry_moves_to_verify_and_retest_aligns_expiry():
    retest = T0 + timedelta(days=3)
    _, claims = run([], [mrow(retest=retest)])
    assert claims[0].expires_at == retest  # 复测到了判断就到期
    ops, after = run(claims, [mrow(retest=retest)], now=retest + timedelta(hours=1))
    assert [(o.from_status, o.to_status, o.reason) for o in ops] == [("hypothesis", "to_verify", "expired")]
    assert after[0].status == "to_verify"


def test_verification_supported_and_refuted():
    retest = T0 + timedelta(days=3)
    for policy, supported_status in ((SIGNED, "established"), (UNSIGNED, "to_verify")):
        _, claims = run([], [mrow(gate="stable", retest=retest)], policy)
        later = retest + timedelta(days=1)
        good = mrow(basis=2, gate="stable", conf=0.9, evidence=(1, 2, 3, 4, 5, 6), at=retest + timedelta(hours=2))
        ops, after = run(claims, [good], policy, later)
        assert ops[0].reason == "expired" and after[0].status == supported_status
        if policy is SIGNED:
            assert ops[1].reason == "verification_supported" and after[0].expires_at == later + timedelta(days=30)
    _, claims = run([], [mrow(gate="stable", retest=retest)])
    bad = mrow(basis=2, gate="execute", evidence=(1, 2, 7), at=retest + timedelta(hours=2))
    ops, after = run(claims, [bad], UNSIGNED, retest + timedelta(days=1))
    assert [o.reason for o in ops][:2] == ["expired", "verification_refuted"] and after[0].status == "refuted"
    # 推翻之后按新门槛重新立一条假设
    assert ops[2].reason == "created" and after[1].status == "hypothesis" and after[1].value["gate"] == "execute"


def test_withdrawn_when_basis_gone_or_evidence_removed():
    _, claims = run([], [mrow(), mrow(basis=2, node=NODE_B, evidence=(3, 4))])
    ops, after = run(claims, [mrow(basis=3, evidence=(1,))], now=T0 + timedelta(hours=1))
    reasons = {(o.unit[2], o.reason) for o in ops}
    assert (NODE_A, "fact_corrected") in reasons and (NODE_B, "fact_corrected") in reasons
    assert {c.status for c in after if c.basis_id in (bid(1), bid(2))} == {"withdrawn"}


def test_revised_on_new_basis_without_extending_expiry():
    _, claims = run([], [mrow()])
    newer = mrow(basis=2, gate="stable", conf=0.7, evidence=(1, 2, 3), at=T0 + timedelta(days=2))
    ops, after = run(claims, [newer], now=T0 + timedelta(days=2))
    assert len(ops) == 1 and ops[0].reason == "revised" and ops[0].to_status == "hypothesis"
    assert set(ops[0].fields) == {"basis_id", "confidence", "evidence_ids", "value"}
    assert after[0].expires_at == claims[0].expires_at


def test_gate_drop_before_due_withdraws_transfer_claim():
    _, claims = run([], [mrow(gate="transfer")])
    drop = mrow(basis=2, gate="stable", evidence=(1, 2, 3), at=T0 + timedelta(days=1))
    ops, after = run(claims, [drop], now=T0 + timedelta(days=1))
    t = [o for o in ops if o.unit[0] == "node_transfer"]
    assert [(o.to_status, o.reason) for o in t] == [("withdrawn", "fact_corrected")]


def test_visibility_first_launch_math_only():
    _, claims = run([], [mrow(), mrow(basis=2, node=NODE_B, subject="wenli")])
    vis = {c.subject: c.visibility for c in claims}
    assert vis == {"aya": ("agent", "student", "guardian"), "wenli": ("agent",)}
    # 策略把数学也收回：单独一次 visibility_changed
    hidden = make_policy(visible_subjects=[])
    ops, after = run(claims, [mrow(), mrow(basis=2, node=NODE_B, subject="wenli")], hidden)
    assert [(o.reason, o.fields) for o in ops] == [("visibility_changed", {"visibility": ("agent",)})]


def test_visibility_capped_by_template():
    tpl = templates(UNSIGNED)
    tpl["node_mastery"] = tpl["node_mastery"].__class__(tpl["node_mastery"].key, 1, ("agent", "student"), timedelta(days=60))
    _, claims = run([], [mrow()], tpl=tpl)
    assert claims[0].visibility == ("agent", "student")


def test_missing_template_skips_category():
    tpl = templates(UNSIGNED)
    del tpl["node_mastery"]
    ops, _ = run([], [mrow(gate="transfer")], tpl=tpl)
    assert {o.unit[0] for o in ops} == {"node_transfer"}


def test_ttl_capped_by_template_max_ttl():
    tpl = templates(UNSIGNED, days=7)
    _, claims = run([], [mrow()], tpl=tpl)
    assert claims[0].expires_at == T0 + timedelta(days=7)


def test_input_validation():
    with pytest.raises(InputError):
        plan_node_claims([], [mrow(node="bad node")], UNSIGNED, T0, TPL)
    with pytest.raises(InputError):
        plan_node_claims([], [mrow(), mrow(basis=2)], UNSIGNED, T0, TPL)  # 一个单元两条当前行
    with pytest.raises(InputError):
        plan_node_claims([], [mrow(), mrow(basis=2, node=NODE_B, student="c2000000-0000-4000-8000-0000000000c2")], UNSIGNED, T0, TPL)
    with pytest.raises(InputError):
        plan_node_claims([], [mrow()], UNSIGNED, T0.replace(tzinfo=None), TPL)
    with pytest.raises(InputError):
        check_ops([Op("x", ("u",), "hypothesis", "refuted", "verification_refuted")])  # 假设不能直接推翻


def test_terminal_claims_are_never_touched():
    _, claims = run([], [mrow()])
    _, gone = run(claims, [])
    assert gone[0].status == "withdrawn"
    ops, again = run(gone, [])
    assert ops == [] and again[0].status == "withdrawn"


def test_plan_is_idempotent_and_deterministic():
    rows = [mrow(), mrow(basis=2, node=NODE_B, gate="transfer", evidence=(3, 4)), mrow(basis=3, level="understand", gate="understand")]
    a, claims = run([], rows)
    b, _ = run([], rows)
    assert [o.as_dict() for o in a] == [o.as_dict() for o in b]
    again, _ = run(claims, rows, now=T0 + timedelta(minutes=5))
    assert again == []
    assert all(c.student_id == STUDENT for c in claims)
