"""性质测试（种子固定的随机游走，只用标准库 random，不引入新依赖）：
- 每一步都在 0010 白名单里，且 from 状态就是判断当时的真实状态；终态判断永不再被修改（生命周期不可逆）；
- 同样输入两次，操作序列逐字节相同；刚执行完再算一次，没有任何操作（幂等）；
- 未签字的策略下永远不会出现“成立”；每个单元至多一条活着的判断；每条新建或修改后的判断都带证据与依据。
"""
from __future__ import annotations

import json
import random
from datetime import timedelta

import pytest

from insight.claims import ALLOWED, LIVE, TERMINAL, apply_ops, plan_node_claims, unit_of
from support import NODE_A, NODE_B, NODE_C, T0, ids, make_policy, mrow, templates

POLICIES = {"unsigned": make_policy(), "signed": make_policy(signed=True)}
UNITS = [(NODE_A, "apply"), (NODE_B, "understand"), (NODE_C, "apply")]
GATES = ("unseen", "understand", "execute", "stable", "transfer", "retain")


def random_rows(rng: random.Random, step: int, now, prev: dict) -> list:
    rows = []
    for i, (node, level) in enumerate(UNITS):
        if rng.random() < 0.15:
            prev.pop(i, None)
            continue
        ev = set(prev.get(i, {rng.randint(1, 5)}))
        if rng.random() < 0.6:
            ev.add(100 * step + i + 10)           # 新证据
        if rng.random() < 0.1 and len(ev) > 1:
            ev.discard(min(ev))                   # 证据被更正（丢掉一条）
        prev[i] = ev
        retest = now + timedelta(days=rng.randint(-2, 20)) if rng.random() < 0.5 else None
        rows.append(mrow(basis=1000 * step + i + 1, node=node, level=level, gate=rng.choice(GATES), conf=round(rng.random(), 3),
                         evidence=tuple(sorted(ev)), at=now - timedelta(minutes=rng.randint(0, 600)), retest=retest))
    return rows


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("which", ["unsigned", "signed"])
def test_random_walk_invariants(seed: int, which: str):
    policy = POLICIES[which]
    tpl = templates(policy)
    rng = random.Random(seed * 7919 + (1 if which == "signed" else 0))
    claims: list = []
    now = T0
    prev: dict = {}
    for step in range(1, 16):
        now = now + timedelta(days=rng.choice([0, 1, 3, 10, 35]), minutes=rng.randint(1, 59))
        rows = random_rows(rng, step, now, prev)
        ops = plan_node_claims(claims, rows, policy, now, tpl)
        again = plan_node_claims(claims, rows, policy, now, tpl)
        assert json.dumps([o.as_dict() for o in ops]) == json.dumps([o.as_dict() for o in again])  # 逐字节相同
        status = {c.id: c.status for c in claims}
        for op in ops:
            assert (op.from_status, op.to_status, op.reason) in ALLOWED
            if op.claim_id is not None:
                assert status[op.claim_id] == op.from_status and status[op.claim_id] not in TERMINAL
                status[op.claim_id] = op.to_status
            else:
                assert op.fields["evidence_ids"] and op.fields["basis_id"] and op.fields["model_version"]
        before_terminal = {c.id: c for c in claims if c.status in TERMINAL}
        claims = apply_ops(claims, ops, now, ids())
        for cid, old in before_terminal.items():
            assert next(c for c in claims if c.id == cid) == old  # 终态逐字段不变
        if which == "unsigned":
            assert all(c.status != "established" for c in claims)
        live_units = [unit_of(c, policy) for c in claims if c.status in LIVE]
        assert len(live_units) == len(set(live_units))
        assert all(c.evidence_ids and c.basis_id and c.expires_at > c.created_at for c in claims)
        assert plan_node_claims(claims, rows, policy, now, tpl) == []  # 幂等


def test_walks_cover_every_transition():
    """随机游走确实走到了白名单里的每一种节点判断会用到的流转（避免性质测试空转）"""
    seen = set()
    for which, policy in POLICIES.items():
        tpl = templates(policy)
        for seed in range(60):
            rng = random.Random(seed * 7919 + (1 if which == "signed" else 0))
            claims, now, prev = [], T0, {}
            for step in range(1, 16):
                now = now + timedelta(days=rng.choice([0, 1, 3, 10, 35]), minutes=rng.randint(1, 59))
                rows = random_rows(rng, step, now, prev)
                ops = plan_node_claims(claims, rows, policy, now, tpl)
                seen |= {(o.from_status, o.to_status, o.reason) for o in ops}
                claims = apply_ops(claims, ops, now, ids())
    unused = {("hypothesis", "to_verify", "dispute"), ("established", "to_verify", "dispute"),
              ("hypothesis", "to_verify", "exam_deviation"), ("established", "to_verify", "exam_deviation")}  # 异议与考试对账不在本任务
    # 可见范围变化只在策略改动时出现（游走里策略不变），由 test_claims.py::test_visibility_first_launch_math_only 覆盖
    unused |= {(s, s, "visibility_changed") for s in ("hypothesis", "established", "to_verify")}
    assert set(ALLOWED) - unused <= seen, sorted(set(ALLOWED) - unused - seen, key=str)
