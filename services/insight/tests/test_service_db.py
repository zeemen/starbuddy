"""契约一致性（严格校验，库上）：insight.claims、insight.context、insight.explain 的响应逐条过契约 schema；
前端受众只看到可见范围内的判断，其他判断与“不存在”同一个 404（不暴露存在性）；中圈指标对前端不存在；首份报告（服务内接口）。"""
from __future__ import annotations

import copy
import dataclasses
import uuid

import pytest

from insight.jobs import first_report
from insight.policy import synthetic_check_config
from insight.service.contract import contract
from insight.shared import forbidden_matcher
from insight.store import InsightStore, create_pool
from pginsight import E, GA, GB, S1, S2, S3, SA1, SA3, TPL_TRANSFER
from support import NODE_A, NODE_C, StaticVerifier, agent_ctx, call, front_ctx, make_policy, running_app
from test_db import db_now, reset, row

pytestmark = pytest.mark.anyio
P = make_policy()
C = contract()
VERIFIER = StaticVerifier({(SA1, "student", S1), (GA, "guardian", S1), (SA3, "student", S3), (SA1, "student", S2),
                           ("00000000-0000-4000-8000-00000000a201", "guardian", "00000000-0000-4000-8000-00000000a001")})  # 最后一条：契约示例里的家长（导出与删除 501 用）
TAGS = {("aya", f"syn-item-{i:03d}"): ("MATH.SYN.EQ.LINEAR", "apply") for i in range(1, 27)}


async def seed(db) -> dict[str, str]:
    """S1：数学 NODE_A（执行）、NODE_C（迁移：掌握 + 迁移两条）、文砺 NODE_A（只给 agent）"""
    reset(db)
    pool = await create_pool(**db.connect_kwargs("insight_app"))
    try:
        now = await db_now(db)
        await InsightStore(pool).sync_mastery_claims(S1, [row(0, NODE_A, "execute", (0, 1), now), row(2, NODE_C, "transfer", (5, 6), now),
                                                          row(3, NODE_A, "stable", (7,), now, subject="wenli")], P)
        if not db.psql(f"SELECT 1 FROM insight.claim WHERE student_id = '{S3}' LIMIT 1").strip():
            await InsightStore(pool).recompute_metrics(S3, P, forbidden_matcher(), TAGS)
    finally:
        await pool.close()
    out = {}
    for line in db.psql(f"SELECT subject || '|' || target_id || '|' || template_key || '|' || id FROM insight.claim "
                        f"WHERE student_id = '{S1}'").split():
        subj, target, key, cid = line.split("|")
        out[f"{subj}:{target}:{key.rsplit('.', 1)[-1]}"] = cid
    out["metric"] = db.psql(f"SELECT id FROM insight.claim WHERE student_id = '{S3}' LIMIT 1").strip()
    return out


def explain_body(ctx, claim_id):
    return {"context": ctx, "claim_id": claim_id}


async def test_claims_and_context_strict(insightdb):
    ids = await seed(insightdb)
    async with running_app(insightdb, verifier=VERIFIER) as client:
        r = await call(client, "insight.claims", {"context": agent_ctx(S1)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert C.validate("InsightClaimsResponse", body) == []
        got = {c["claim_id"] for c in body["result"]["claims"]}
        assert got == {ids["aya:MATH.SYN.EQ.LINEAR:node_mastery"], ids["aya:MATH.SYN.EQ.TRANSPOSE:node_mastery"],
                       ids["aya:MATH.SYN.EQ.TRANSPOSE:node_transfer"], ids["wenli:MATH.SYN.EQ.LINEAR:node_mastery"]}
        assert all(c["status"] == "hypothesis" and c["model_version"] == "algo:kge-mastery@0.1.0.syn" for c in body["result"]["claims"])
        r = await call(client, "insight.claims", {"context": agent_ctx(S1), "subject": "wenli", "statuses": ["hypothesis"], "limit": 5})
        assert [c["subject"] for c in r.json()["result"]["claims"]] == ["wenli"]
        r = await call(client, "insight.claims", {"context": agent_ctx(S1), "statuses": ["established"]})
        assert r.json()["result"]["claims"] == []

        r = await call(client, "insight.context", {"context": agent_ctx(S1), "task": "plan"})
        assert r.status_code == 200 and C.validate("InsightContextResponse", r.json()) == []
        entries = r.json()["result"]["entries"]
        assert sorted(e["value"] for e in entries) == ["execute", "stable", "transfer", "transfer"]
        assert all(e["field_id"] == "inner.g3.node_mastery" and e["claim_id"] and e["source_type"] == "inferred" for e in entries)
        r = await call(client, "insight.context", {"context": agent_ctx(S3), "task": "teach", "token_budget": 200})
        body = r.json()
        assert r.status_code == 200 and C.validate("InsightContextResponse", body) == []
        assert len(body["result"]["entries"]) == 8  # 200 // 25
        r = await call(client, "insight.context", {"context": agent_ctx(S3), "task": "teach"})
        fields = {e["field_id"] for e in r.json()["result"]["entries"]}
        assert fields == {"middle.g7.independent_solve_rate", "middle.g7.hint_dependency", "middle.g7.transfer_gap",
                          "middle.g7.self_assessment_calibration", "middle.g7.retention_rate", "middle.g5.plan_completion_rate",
                          "middle.g7.reading_question_error_share", "middle.g5.persistence"}


async def test_explain_visibility_and_existence_hiding(insightdb):
    ids = await seed(insightdb)
    a_mastery = ids["aya:MATH.SYN.EQ.LINEAR:node_mastery"]
    async with running_app(insightdb, verifier=VERIFIER) as client:
        r = await call(client, "insight.explain", explain_body(front_ctx("student", SA1, S1), a_mastery), "bff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert C.validate("InsightExplainResponse", body) == []
        st = body["result"]["statement"]
        assert st["text"] == f"【占位模板·未审核】{NODE_A}｜apply｜execute" and st["display"] == "solid" and st["claim_id"] == a_mastery
        chain = body["result"]["evidence_chain"]
        assert [e["evidence_id"] for e in chain] == [E[0], E[1]]
        assert all(e["evidence_level"] == "A" and e["node_id"] == NODE_A and e["source_type"] == "objective" for e in chain)
        assert body["evidence_ids"] == [E[0], E[1]] and "algo:kge-mastery@0.1.0.syn" in body["source_version"]

        r = await call(client, "insight.explain", explain_body(front_ctx("guardian", GA, S1, link_id=str(uuid.uuid4())), a_mastery), "bff")
        assert r.status_code == 200 and C.validate("InsightExplainResponse", r.json()) == []

        missing = await call(client, "insight.explain", explain_body(front_ctx("student", SA1, S1), str(uuid.uuid4())), "bff")
        cases = [
            explain_body(front_ctx("student", SA1, S1), ids["wenli:MATH.SYN.EQ.LINEAR:node_mastery"]),  # 只给 agent
            explain_body(front_ctx("student", SA1, S2), a_mastery),                                     # 不属于 student_ref
            explain_body(front_ctx("guardian", GB, S1, link_id=str(uuid.uuid4())), a_mastery),         # 授权核对不通过
            explain_body(front_ctx("student", SA3, S3), ids["metric"]),                                 # 中圈指标对前端不存在
        ]
        for b in cases:
            r = await call(client, "insight.explain", b, "bff")
            assert r.status_code == 404
            got, want = r.json(), missing.json()
            got.pop("request_id", None), want.pop("request_id", None)
            assert got == want and "detail" not in got  # 与“不存在”完全相同

        # 只给学生看：家长 404
        insightdb.psql(f"SET ROLE starbuddy_insight; UPDATE insight.claim SET visibility = '{{agent,student}}', "
                       f"change_reason = 'visibility_changed' WHERE id = '{a_mastery}';")
        r = await call(client, "insight.explain", explain_body(front_ctx("guardian", GA, S1, link_id=str(uuid.uuid4())), a_mastery), "bff")
        assert r.status_code == 404
        r = await call(client, "insight.explain", explain_body(front_ctx("student", SA1, S1), a_mastery), "bff")
        assert r.status_code == 200

        # 编排层：agent 可见的都能解释；没有文本模板的中圈指标没有可渲染的结论 → 404
        r = await call(client, "insight.explain", explain_body(agent_ctx(S1), ids["wenli:MATH.SYN.EQ.LINEAR:node_mastery"]))
        assert r.status_code == 200 and C.validate("InsightExplainResponse", r.json()) == []
        r = await call(client, "insight.explain", explain_body(agent_ctx(S3), ids["metric"]))
        assert r.status_code == 404

        # 待验证：虚线
        insightdb.psql(f"SET ROLE starbuddy_insight; UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' "
                       f"WHERE id = '{ids['aya:MATH.SYN.EQ.TRANSPOSE:node_transfer']}';")
        r = await call(client, "insight.explain", explain_body(front_ctx("student", SA1, S1), ids["aya:MATH.SYN.EQ.TRANSPOSE:node_transfer"]), "bff")
        assert r.status_code == 200 and r.json()["result"]["statement"]["display"] == "dashed"
        assert C.validate("InsightExplainResponse", r.json()) == []


async def test_default_verifier_fails_closed(insightdb):
    ids = await seed(insightdb)
    async with running_app(insightdb) as client:
        r = await call(client, "insight.explain", explain_body(front_ctx("student", SA1, S1), ids["aya:MATH.SYN.EQ.LINEAR:node_mastery"]), "bff")
        assert r.status_code == 404


async def test_http_layer_guards(insightdb):
    await seed(insightdb)
    async with running_app(insightdb, verifier=VERIFIER) as client:
        r = await client.post("/v0/insight.claims", json={"context": agent_ctx(S1)})
        assert r.status_code == 401 and C.problem_errors(r.json()) == []
        r = await client.post("/v0/insight.claims", content=b'{"context": {}, "context": {}}',
                              headers={"authorization": "Bearer " + "synthetic-orchestrator-to-insight-cred-01", "content-type": "application/json"})
        assert r.status_code == 400
        r = await client.post("/v0/insight.claims", content=b"x" * (64 * 1024 + 1),
                              headers={"authorization": "Bearer " + "synthetic-orchestrator-to-insight-cred-01"})
        assert r.status_code == 413
        r = await client.post("/v0/insight.claims", json={"context": agent_ctx(S1)}, headers={"host": "evil.example",
                              "authorization": "Bearer synthetic-orchestrator-to-insight-cred-01"})
        assert r.status_code == 400
        # 导出与删除：0035 的库函数只授 BFF 会话，缺省不接线 → 501（fail closed）；编排层调不了（准入 403）
        for tool in ("insight.export", "insight.erase"):
            name = C.tools[tool].request_schema.removesuffix("Request")
            body = copy.deepcopy(C.doc["components"]["examples"][f"{name}Request"]["value"])
            r = await call(client, tool, body, "bff")
            assert r.status_code == 501 and r.json()["code"] == "METHOD_NOT_IMPLEMENTED" and C.problem_errors(r.json()) == []
            r = await call(client, tool, {"context": agent_ctx(S1)}, "orchestrator")
            assert r.status_code == 403


async def test_response_contract_violation_is_500(insightdb):
    await seed(insightdb)
    bad = dataclasses.replace(P, config_version="not a version!")  # source_version 不合契约 → 不把响应交出去
    async with running_app(insightdb, policy=bad, verifier=VERIFIER) as client:
        r = await call(client, "insight.claims", {"context": agent_ctx(S1)})
        assert r.status_code == 500 and r.json()["code"] == "INTERNAL"


async def test_first_report_service_interface(insightdb):
    await seed(insightdb)
    pool = await create_pool(**insightdb.connect_kwargs("insight_app"))
    store = InsightStore(pool)
    try:
        cfg = synthetic_check_config()
        stu = await first_report(store, S1, "student", P, cfg)
        gua = await first_report(store, S1, "guardian", P, cfg)
        assert stu.ok and gua.ok
        s_ids = [s["claim_id"] for sec in stu.report["sections"] for s in sec["sentences"]]
        assert len(s_ids) == 3 and stu.report["claim_ids"] == s_ids
        assert all(s.get("label") == "preliminary" and "confidence_band" not in s for sec in stu.report["sections"] for s in sec["sentences"])
        assert all("confidence_band" in s for sec in gua.report["sections"] for s in sec["sentences"])
        # 迁移判断转为待验证：不再作为陈述出现
        insightdb.psql(f"SET ROLE starbuddy_insight; UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' "
                       f"WHERE student_id = '{S1}' AND template_key = '{TPL_TRANSFER}';")
        again = await first_report(store, S1, "student", P, cfg)
        assert len(again.report["claim_ids"]) == 2
        # 全部撤下：不出报告
        await store.sync_mastery_claims(S1, [], P)
        assert (await first_report(store, S1, "student", P, cfg)).blocked == ("no_visible_claims",)
        assert (await first_report(store, S3, "student", P, cfg)).blocked == ("no_visible_claims",)  # 中圈指标不进报告
    finally:
        await pool.close()
        reset(insightdb)
