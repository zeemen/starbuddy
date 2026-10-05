"""SIE-02b 第二步 PR 1（specs/sie-02b-claims-first-report/spec.md“第二步补充”默认方案第 1、2 条）：
- create_app 的缺省授权再核对 DbActorVerifier 调迁移 0044 的 insight.verify_actor：本人、有效绑定家长放行；另一户、没有绑定、
  撤销绑定、停用账号一律同一个 404；函数不可用（不存在、没有执行权限）时一律不通过（fail closed）；
- 契约工具 insight.report：只给 BFF；学生版 / 家长版按可见范围、每句带 claim_id、家长版带置信度说明；两道检查任一不过不出报告；
  无权、没有报告、检查不过都是同一个 404（不暴露存在性）。全部合成数据（规则 8）。"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest

from insight.service.contract import contract
from insight.service.tools import DbActorVerifier
from pginsight import GA, GB, LEARNING_ROLE, S1, S2, S3, SA1, SA2, SA3
from support import call, front_ctx, running_app
from test_db import reset
from test_service_db import seed

pytestmark = pytest.mark.anyio
C = contract()


def explain(ctx, claim_id):
    return {"context": ctx, "claim_id": claim_id}


def report(ctx):
    return {"context": ctx}


def problem_shape(r) -> tuple:
    """404 的对外形状（去掉每次不同的 request_id）：状态、code、title、有没有 detail"""
    b = r.json()
    return r.status_code, b.get("code"), b.get("title"), "detail" in b


async def test_default_verifier_uses_verify_actor(insightdb):
    ids = await seed(insightdb)
    cid = ids["aya:MATH.SYN.EQ.LINEAR:node_mastery"]
    link_gb = str(uuid.uuid4())
    try:
        async with running_app(insightdb) as client:  # 不注入 verifier：create_app 缺省 DbActorVerifier（0044）
            assert isinstance(client.app.state.insight["core"].verifier, DbActorVerifier)
            r = await call(client, "insight.explain", explain(front_ctx("student", SA1, S1), cid), "bff")
            assert r.status_code == 200, r.text
            r = await call(client, "insight.explain", explain(front_ctx("guardian", GA, S1, link_id=str(uuid.uuid4())), cid), "bff")
            assert r.status_code == 200, r.text
            missing = await call(client, "insight.explain", explain(front_ctx("student", SA1, S1), str(uuid.uuid4())), "bff")
            assert missing.status_code == 404
            denied = [
                await call(client, "insight.explain", explain(front_ctx("guardian", GB, S1, link_id=str(uuid.uuid4())), cid), "bff"),  # 另一户
                await call(client, "insight.explain", explain(front_ctx("student", SA3, S1), cid), "bff"),   # 另一个学生
                await call(client, "insight.explain", explain(front_ctx("student", SA2, S1), cid), "bff"),
                await call(client, "insight.explain", explain(front_ctx("student", GA, S1), cid), "bff"),    # 家长账号冒充学生
            ]
            assert {problem_shape(r) for r in denied} == {problem_shape(missing)}  # 无权与不存在同一个 404
            # 新绑定生效、撤销后立即失效（每次请求都在库里再核，不缓存）
            insightdb.psql(f"INSERT INTO platform.guardian_student_link (id, guardian_account_id, student_id, granted_via) "
                           f"VALUES ('{link_gb}', '{GB}', '{S1}', 'invite')")
            ctx_gb = front_ctx("guardian", GB, S1, link_id=link_gb)
            assert (await call(client, "insight.explain", explain(ctx_gb, cid), "bff")).status_code == 200
            insightdb.psql(f"UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE id = '{link_gb}'")
            r = await call(client, "insight.explain", explain(ctx_gb, cid), "bff")
            assert problem_shape(r) == problem_shape(missing)
            # 停用账号
            insightdb.psql(f"UPDATE platform.account SET status = 'disabled' WHERE id = '{SA1}'")
            r = await call(client, "insight.explain", explain(front_ctx("student", SA1, S1), cid), "bff")
            assert problem_shape(r) == problem_shape(missing)
    finally:
        insightdb.psql(f"UPDATE platform.account SET status = 'active' WHERE id = '{SA1}'")
        reset(insightdb)


class _Conn:
    def __init__(self, exc: BaseException | None, value=True):
        self.exc, self.value, self.calls = exc, value, 0

    async def fetchval(self, sql, *args):
        self.calls += 1
        assert "insight.verify_actor" in sql and len(args) == 3
        if self.exc is not None:
            raise self.exc
        return self.value


class _Pool:
    def __init__(self, conn: _Conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


STU = {"role": "student", "actor_ref": SA1}


async def test_db_verifier_fails_closed():
    # 迁移 0044 未执行或没有执行权限：不通过（不抛 500，也不放行）
    for exc in (asyncpg.UndefinedFunctionError("missing"), asyncpg.InsufficientPrivilegeError("denied")):
        assert await DbActorVerifier(_Pool(_Conn(exc))).verify(STU, "student", S1) is False
    # 其他数据库错误照常抛出（500，同样不放行）
    with pytest.raises(asyncpg.PostgresError):
        await DbActorVerifier(_Pool(_Conn(asyncpg.DeadlockDetectedError("x")))).verify(STU, "student", S1)
    # 函数返回值不是 True（False、NULL）一律不通过
    for v in (False, None, 1, "t"):
        assert await DbActorVerifier(_Pool(_Conn(None, v))).verify(STU, "student", S1) is False
    # 受众不对、身份与受众不一致、ID 不是 UUID：不查库直接不通过
    for actor, aud, stu in (({"role": "student", "actor_ref": SA1}, "agent", S1), ({"role": "guardian", "actor_ref": SA1}, "student", S1),
                            ({"role": "student", "actor_ref": "not-a-uuid"}, "student", S1), ({"role": "student"}, "student", S1),
                            ({"role": "student", "actor_ref": SA1}, "student", "x"), ({}, "student", S1)):
        conn = _Conn(None, True)
        assert await DbActorVerifier(_Pool(conn)).verify(actor, aud, stu) is False and conn.calls == 0
    assert await DbActorVerifier(_Pool(_Conn(None, True))).verify(STU, "student", S1) is True


async def test_db_verifier_without_grant_on_real_db(insightdb):
    """非洞察服务角色（learning）调不到 0044 的函数：缺省核对不通过，而不是报错放行"""
    pool = await asyncpg.create_pool(min_size=1, max_size=1, **insightdb.connect_kwargs(LEARNING_ROLE))
    try:
        assert await DbActorVerifier(pool).verify(STU, "student", S1) is False
    finally:
        await pool.close()


async def test_report_tool(insightdb):
    ids = await seed(insightdb)
    try:
        async with running_app(insightdb) as client:
            r = await call(client, "insight.report", report(front_ctx("student", SA1, S1)), "bff")
            assert r.status_code == 200, r.text
            body = r.json()
            assert C.validate("InsightReportResponse", body) == []
            res = body["result"]
            sentences = [s for sec in res["sections"] for s in sec["sentences"]]
            assert res["audience"] == "student" and res["claim_ids"] == [s["claim_id"] for s in sentences]
            assert len(sentences) == 3 and set(res["claim_ids"]) <= set(ids.values())
            assert all("confidence_band" not in s and s["label"] == "preliminary" for s in sentences)  # 未签字策略：都是假设
            assert ids["wenli:MATH.SYN.EQ.LINEAR:node_mastery"] not in res["claim_ids"]  # 只给 agent 的判断不进前端报告
            assert body["evidence_ids"] and body["model_version"].startswith("algo:sie-report@")
            core = client.app.state.insight["core"]
            assert res["policy_version"] == core.policy.config_version  # 固定文字按该版本登记文字核对
            assert res["notice"] == core.policy.report.notice and res["title"] == core.policy.report.titles["student"]

            g = await call(client, "insight.report", report(front_ctx("guardian", GA, S1, link_id=str(uuid.uuid4()))), "bff")
            assert g.status_code == 200 and C.validate("InsightReportResponse", g.json()) == []
            assert all(s["confidence_band"] in ("few_evidence", "moderate", "strong")
                       for sec in g.json()["result"]["sections"] for s in sec["sentences"])

            # 无权（另一户家长、另一个学生）、没有可出的报告（S3 只有中圈指标）、学生不存在：同一个 404
            nobody = await call(client, "insight.report", report(front_ctx("student", SA1, str(uuid.uuid4()))), "bff")
            assert nobody.status_code == 404 and C.problem_errors(nobody.json()) == []
            others = [await call(client, "insight.report", report(front_ctx("guardian", GB, S1, link_id=str(uuid.uuid4()))), "bff"),
                      await call(client, "insight.report", report(front_ctx("student", SA3, S1)), "bff"),
                      await call(client, "insight.report", report(front_ctx("student", SA3, S3)), "bff"),
                      await call(client, "insight.report", report(front_ctx("student", SA2, S2)), "bff")]
            assert {problem_shape(x) for x in others} == {problem_shape(nobody)}

            # 编排层不能调（准入 403）；请求不合契约（带筛选条件）400
            r = await call(client, "insight.report", {"context": {**front_ctx("student", SA1, S1), "caller": "orchestrator"}}, "orchestrator")
            assert r.status_code == 403
            r = await call(client, "insight.report", {"context": front_ctx("student", SA1, S1), "subject": "aya"}, "bff")
            assert r.status_code == 400

            # 两道检查任一不过不出报告：渲染文字命中禁用推断（模拟词表命中）→ 与“没有报告”同一个 404
            core = client.app.state.insight["core"]
            real = core.forbidden
            core.forbidden = lambda text: ["synthetic-hit"]
            try:
                r = await call(client, "insight.report", report(front_ctx("student", SA1, S1)), "bff")
                assert problem_shape(r) == problem_shape(nobody)
            finally:
                core.forbidden = real
            # 衔接短语不在一致性检查的白名单里（一致性检查不过）→ 同样 404
            import dataclasses
            real_cfg = core.check_cfg
            core.check_cfg = dataclasses.replace(real_cfg, connective_phrases=frozenset())
            try:
                r = await call(client, "insight.report", report(front_ctx("student", SA1, S1)), "bff")
                assert problem_shape(r) == problem_shape(nobody)
            finally:
                core.check_cfg = real_cfg
            # 报告超上限（读取多出一条）：不截断、不 500，与“没有报告”同一个 404
            from insight.report import MAX_REPORT_CLAIMS
            real_claims = await core.store.report_claims(S1, "student")
            many = [real_claims[0]] * (MAX_REPORT_CLAIMS + 1)

            async def over(student, audience):  # noqa: ARG001
                return many
            core.store.report_claims = over
            try:
                r = await call(client, "insight.report", report(front_ctx("student", SA1, S1)), "bff")
                assert problem_shape(r) == problem_shape(nobody)
            finally:
                del core.store.report_claims
            assert (await call(client, "insight.report", report(front_ctx("student", SA1, S1)), "bff")).status_code == 200
    finally:
        reset(insightdb)


async def test_report_tool_without_check_config_is_501():
    from insight.policy import synthetic_policy
    from insight.service.tools import ToolCore
    from insight.shared import forbidden_matcher
    from support import StaticVerifier

    class NoStore:
        async def visible_claims(self, *a, **k):  # pragma: no cover
            raise AssertionError("store must not be reached")

    core = ToolCore(NoStore(), C, synthetic_policy(), forbidden_matcher(), StaticVerifier({(SA1, "student", S1)}))
    status, out = await core.call("insight.report", report(front_ctx("student", SA1, S1)), "bff")
    assert status == 501 and out["code"] == "METHOD_NOT_IMPLEMENTED" and C.problem_errors(out) == []
