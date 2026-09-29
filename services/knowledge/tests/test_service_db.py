"""库上测试（一次性 Postgres，bootstrap 角色 + 全部迁移 + 合成 kge 数据，见 tests/pgkge.py）：
- 以 starbuddy_kge_read 连接：草稿、退役、未核对 / 有争议的投影、待审 / 驳回的标签、已停用节点与边，经任何工具都不返回；
- 服务不能写，读不到个人层；启动时拒绝超级用户、BYPASSRLS、同时继承写入角色、不继承只读角色的连接；
- 第二道防线：本服务的 SQL 在不受 RLS 约束的连接上也只取已发布、已核对、审核通过、强前置的数据；
- 响应不合契约（库里有超长 includes）时回 500，不把数据交出去；
- 编译器的数据库数据源（DbAtomSource）只读已发布版本。
"""
from __future__ import annotations

import json

import asyncpg
import pytest

from knowledge.service.app import create_app
from knowledge.service.contract import contract
from knowledge.service.reader import DbAtomSource, KgeReader, ReaderRoleError, atom_key, create_reader_pool
from pgkge import (APP_ROLE, APPLY, BAD, BYPASS_ROLE, DRAFT_NAME, DRAFT_ONLY, FORBIDDEN_TEXT, LINEAR, OLD, OTHER_ROLE, QUOTE_A,
                   QUOTE_B, QUOTE_T, RETIRED_ONLY, TRANSPOSE, VP, WEAK, WRITER_ROLE)
from svc import call, ctx, running_app, settings

pytestmark = pytest.mark.anyio
C = contract()


# ---------- 角色 ----------
@pytest.mark.parametrize("role, message", [("postgres", "超级用户"), (BYPASS_ROLE, "BYPASSRLS"), (WRITER_ROLE, "应用角色")])
async def test_unsafe_roles_refused_at_startup(kgedb, role, message):
    with pytest.raises(ReaderRoleError, match=message):
        await create_reader_pool(**kgedb.connect_kwargs(role))


async def test_role_without_reader_membership_refused(kgedb):
    with pytest.raises((ReaderRoleError, asyncpg.InsufficientPrivilegeError)):
        await create_reader_pool(**kgedb.connect_kwargs(OTHER_ROLE))


async def test_injected_superuser_pool_refused_by_app(kgedb):
    raw = await asyncpg.create_pool(**kgedb.connect_kwargs("postgres"), min_size=1, max_size=2)
    try:
        app = create_app(settings(), pool=raw)
        with pytest.raises(ReaderRoleError, match="超级用户"):
            async with app.router.lifespan_context(app):
                pass
    finally:
        await raw.close()


async def test_service_connection_cannot_write_or_read_personal_layer(kgedb):
    pool = await create_reader_pool(**kgedb.connect_kwargs(APP_ROLE))
    try:
        async with pool.acquire() as c:
            assert await c.fetchval("SELECT current_user") == "starbuddy_kge_read"
            assert await c.fetchval("SHOW default_transaction_read_only") == "on"
            for sql in (f"INSERT INTO kge.curriculum_item (subject, standard_year, code, domain, text, verb_level, source_ref) "
                        f"VALUES ('aya', 2022, 'X-1', 'd', 't', 'know', 's')",
                        f"UPDATE kge.knowledge_node SET name = 'x' WHERE id = '{LINEAR}'",
                        "DELETE FROM kge.item_tag"):
                with pytest.raises((asyncpg.ReadOnlySQLTransactionError, asyncpg.InsufficientPrivilegeError)):
                    await c.execute(sql)
            # 就算关掉只读事务，权限上也写不了
            await c.execute("BEGIN TRANSACTION READ WRITE")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(f"UPDATE kge.knowledge_node SET name = 'x' WHERE id = '{LINEAR}'")
            await c.execute("ROLLBACK")
            for table in ("platform.attempt", "platform.item", "platform.mastery_estimate", "platform.evidence_event", "platform.student_profile", "kge.content_review"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await c.fetchval(f"SELECT count(*) FROM {table}")
    finally:
        await pool.close()


# ---------- 经工具的可见性 ----------
async def _all_responses(client) -> list[tuple[str, int, dict]]:
    reqs = [
        ("node.get", {"context": ctx("bff"), "node_id": LINEAR}, "bff"),
        ("node.get", {"context": ctx(), "node_id": LINEAR, "edition": "syn-ed-a", "region": "syn-region"}, "orchestrator"),
        ("node.get", {"context": ctx(), "node_id": LINEAR, "edition": "syn-ed-c"}, "orchestrator"),
        ("node.get", {"context": ctx(), "node_id": DRAFT_ONLY}, "orchestrator"),
        ("node.get", {"context": ctx(), "node_id": RETIRED_ONLY}, "orchestrator"),
        ("node.get", {"context": ctx(), "node_id": OLD}, "orchestrator"),
        ("node.path", {"context": ctx(), "node_id": LINEAR, "direction": "up", "max_depth": 10}, "orchestrator"),
        ("node.path", {"context": ctx(), "node_id": TRANSPOSE, "direction": "down", "max_depth": 10}, "orchestrator"),
        ("node.resolve", {"context": ctx(data_class="L2"), "subject": "aya", "text": f"合成草稿节点 合成退役节点 {DRAFT_ONLY} {OLD}"},
         "orchestrator"),
        ("item.search", {"context": ctx(), "subject": "aya", "limit": 50}, "orchestrator"),
        ("item.search", {"context": ctx(), "subject": "aya", "node_id": LINEAR}, "orchestrator"),
        ("item.similar", {"context": ctx(), "item_ref": "aya:SYN-I1", "limit": 20}, "orchestrator"),
        ("item.similar", {"context": ctx(), "item_ref": "aya:SYN-I4"}, "orchestrator"),
        ("item.similar", {"context": ctx(), "item_ref": "aya:SYN-I6"}, "orchestrator"),
        ("rubric.get", {"context": ctx("learning"), "item_ref": "aya:SYN-I1", "rubric_version": "syn-rubric@0.1.0"}, "learning"),
        ("rubric.get", {"context": ctx("learning"), "item_ref": "aya:SYN-I1", "rubric_version": "syn-rubric@0.2.0"}, "learning"),
    ]
    out = []
    for tool, body, caller in reqs:
        r = await call(client, tool, body, caller)
        out.append((tool, r.status_code, r.json()))
    return out


async def test_no_draft_retired_unverified_or_pending_content_via_any_tool(kgedb):
    async with running_app(kgedb) as client:
        responses = await _all_responses(client)
    text = json.dumps(responses, ensure_ascii=False)
    for forbidden in FORBIDDEN_TEXT:
        assert forbidden not in text, forbidden
    by = [(t, s) for t, s, _ in responses]
    assert by.count(("node.get", 404)) == 3 and ("rubric.get", 404) in by and by.count(("item.similar", 404)) == 2
    for tool, status, body in responses:
        spec = C.tools[tool]
        assert (C.validate(spec.response_schema, body) if status == 200 else C.problem_errors(body)) == []
        if status == 404:
            assert body["title"] == body["code"] and "detail" not in body


async def test_node_get_published_view(kgedb):
    async with running_app(kgedb) as client:
        r = await call(client, "node.get", {"context": ctx("bff"), "node_id": LINEAR}, "bff")
        body = r.json()
        assert r.status_code == 200, body
        res = body["result"]
        assert res["node"]["name"] == "一元一次方程" and res["node"]["map_version"] == "0.9.0"
        assert [(p["edition"], p["quote_text"]) for p in res["textbook_projections"]] == [("syn-ed-a", QUOTE_A), ("syn-ed-b", QUOTE_B)]
        assert all(len(p["quote_hash"]) == 64 for p in res["textbook_projections"])
        assert sorted(w["region"] for w in res["region_weights"]) == ["syn-other", "syn-region"]
        assert body["source_version"] == "graph:math-map@0.9.0" and body["model_version"] == "algo:kge-node-get@0.1.0"
        assert body["evidence_ids"] == [] and body["confidence"] == 1 and body["human_review_required"] is False
        r = await call(client, "node.get", {"context": ctx(), "node_id": LINEAR, "edition": "syn-ed-b", "region": "syn-region"})
        res = r.json()["result"]
        assert [p["edition"] for p in res["textbook_projections"]] == ["syn-ed-b"]
        assert [w["region"] for w in res["region_weights"]] == ["syn-region"]
        for version, status in (("0.9.0", 200), ("0.10.0", 404), ("0.8.0", 404)):
            r = await call(client, "node.get", {"context": ctx(map_version=version), "node_id": LINEAR})
            assert r.status_code == status, version


async def test_node_path_only_strong_active_prerequisites(kgedb):
    async with running_app(kgedb) as client:
        up = (await call(client, "node.path", {"context": ctx(), "node_id": APPLY, "direction": "up", "max_depth": 10})).json()
        assert up["result"]["steps"] == [{"from_id": LINEAR, "to_id": APPLY, "depth": 1},
                                         {"from_id": TRANSPOSE, "to_id": LINEAR, "depth": 2}]
        down = (await call(client, "node.path", {"context": ctx(), "node_id": TRANSPOSE, "direction": "down"})).json()
        assert down["result"]["steps"] == [{"from_id": TRANSPOSE, "to_id": LINEAR, "depth": 1},
                                           {"from_id": LINEAR, "to_id": APPLY, "depth": 2}]
        one = (await call(client, "node.path", {"context": ctx(), "node_id": APPLY, "direction": "up", "max_depth": 1})).json()
        assert len(one["result"]["steps"]) == 1
        text = json.dumps([up, down])
        assert WEAK not in text and OLD not in text  # 弱前置边、指向已停用节点的边都不走


async def test_items_only_approved_tags(kgedb):
    async with running_app(kgedb) as client:
        r = (await call(client, "item.search", {"context": ctx(), "subject": "aya", "node_id": LINEAR})).json()
        assert [i["item_ref"] for i in r["result"]["items"]] == ["aya:SYN-I1", "aya:SYN-I2", "aya:SYN-I3"]
        r = (await call(client, "item.search", {"context": ctx(), "subject": "aya", "difficulty_min": 0.35, "difficulty_max": 0.5})).json()
        assert [i["item_ref"] for i in r["result"]["items"]] == ["aya:SYN-I1"]
        r = (await call(client, "item.search", {"context": ctx(), "subject": "aya", "family_code": "syn-fam-linear",
                                                "cognitive_level": "apply"})).json()
        assert [i["item_ref"] for i in r["result"]["items"]] == ["aya:SYN-I1", "aya:SYN-I2"]
        r = await call(client, "item.search", {"context": ctx(), "subject": "wenli", "node_id": LINEAR})
        assert r.status_code == 400 and r.json()["code"] == "SUBJECT_MISMATCH"
        r = await call(client, "item.search", {"context": ctx(), "subject": "aya", "difficulty_min": 0.6, "difficulty_max": 0.5})
        assert r.status_code == 400
        r = (await call(client, "item.similar", {"context": ctx(), "item_ref": "aya:SYN-I1"})).json()
        assert r["result"]["items"] == [{"item": {"item_ref": "aya:SYN-I2", "main_node_id": LINEAR, "license_state": "original",
                                                  "cognitive_level": "apply", "prior_difficulty": 0.6, "family_code": "syn-fam-linear"},
                                         "relation": "same_family"}]
        r = await call(client, "item.similar", {"context": ctx(), "item_ref": "aya:SYN-I4"})
        assert r.status_code == 404 and r.json()["code"] == "ITEM_NOT_FOUND"


async def test_rubric_points(kgedb):
    async with running_app(kgedb) as client:
        body = (await call(client, "rubric.get", {"context": ctx("learning"), "item_ref": "aya:SYN-I1",
                                                  "rubric_version": "syn-rubric@0.1.0"}, "learning")).json()
        assert body["result"]["points"] == [
            {"point_id": "solve", "score": 1.0, "node_ids": [LINEAR], "partial_credit": "stepwise"},
            {"point_id": "transpose", "score": 2.0, "node_ids": [TRANSPOSE], "partial_credit": "all_or_nothing",
             "subject_dimension": "computation", "keyword_family": ["移项", "变号"]}]
        assert body["result"]["rubric_version"] == "syn-rubric@0.1.0"
        assert body["source_version"] == "rubric:syn-rubric@0.1.0;graph:math-map@0.9.0"


async def test_node_resolve_rule_candidates(kgedb):
    async with running_app(kgedb) as client:
        r = await call(client, "node.resolve", {"context": ctx(data_class="L2"), "subject": "aya", "text": "一元一次方程怎么移项"})
        body = r.json()
        assert r.status_code == 200, body
        ids = [c["node_id"] for c in body["result"]["candidates"]]
        assert ids[:2] == [LINEAR, TRANSPOSE] and body["confidence"] == body["result"]["candidates"][0]["confidence"]
        assert body["human_review_required"] is False and body["model_version"] == "algo:kge-node-resolve@0.2.0"  # PR ③：规则 + 全文
        r = await call(client, "node.resolve", {"context": ctx(data_class="L2"), "subject": "aya", "text": "完全无关的合成文字"})
        body = r.json()
        assert body["result"]["candidates"] == [] and body["human_review_required"] is True
        assert body["review_reasons"] == ["low_confidence"] and body["confidence"] == 0


async def test_response_not_conforming_to_contract_is_500(kgedb):
    """库里的节点 includes 超过契约长度：不把不合契约的数据交出去"""
    async with running_app(kgedb) as client:
        r = await call(client, "node.get", {"context": ctx(), "node_id": BAD})
        assert r.status_code == 500 and r.json()["code"] == "INTERNAL" and "长" not in r.text


# ---------- 第二道防线：SQL 本身的过滤（在不受 RLS 约束的超级用户连接上核对） ----------
async def test_reader_sql_filters_without_rls(kgedb):
    pool = await asyncpg.create_pool(**kgedb.connect_kwargs("postgres"), min_size=1, max_size=2)
    try:
        r = KgeReader(pool)
        v = await r.published_version("aya")
        assert v is not None and v.version == "0.9.0" and str(v.id) == VP
        assert [p["edition"] for p in await r.textbook_projections(v.id, LINEAR)] == ["syn-ed-a", "syn-ed-b"]
        assert await r.textbook_projections(v.id, LINEAR, "syn-ed-c") == []
        assert {n["id"] for n in await r.nodes(v.id)} >= {LINEAR, TRANSPOSE} and OLD not in {n["id"] for n in await r.nodes(v.id)}
        assert await r.node(v.id, OLD) is None
        steps = await r.prerequisite_step(v.id, [LINEAR], "up")
        assert (WEAK, LINEAR) not in steps and (TRANSPOSE, LINEAR) in steps
        assert (TRANSPOSE, APPLY) not in await r.prerequisite_step(v.id, [TRANSPOSE], "down")  # 已停用的边、关联边
        items = await r.item_search(v.id, "aya", None, None, None, None, None, 50)
        assert sorted(i["item_id"] for i in items) == ["SYN-I1", "SYN-I2", "SYN-I3"]
        assert await r.item(v.id, "aya", "SYN-I4") is None and await r.item(v.id, "aya", "SYN-I5") is None
        sims = await r.item_similar(v.id, "aya", "SYN-I1", None, LINEAR, 20)
        assert sorted(i["item_id"] for i in sims) == ["SYN-I2"]
        doc = await r.snapshot_doc("aya")
        assert [a["source"] for a in doc["current_affairs"]] == ["合成来源：有效"]
        assert DRAFT_NAME not in json.dumps(doc, ensure_ascii=False)
    finally:
        await pool.close()


# ---------- 编译器的数据库数据源 ----------
def test_db_atom_source_reads_published_only(kgedb):
    snap = DbAtomSource(kgedb.dsn(APP_ROLE), "aya").load()
    assert snap.map_version == "0.9.0" and snap.status == "published"
    nodes = {a.id for a in snap.of_type("node")}
    assert {LINEAR, TRANSPOSE, APPLY, WEAK} <= nodes and not nodes & {OLD, DRAFT_ONLY, RETIRED_ONLY}
    assert sorted(q for _, q in snap.quote_texts()) == sorted([QUOTE_A, QUOTE_B, QUOTE_T])
    assert all(a.id.startswith("tp-") and len(a.id) == 19 for a in snap.of_type("projection"))
    assert "0" * 8 + "-" not in json.dumps([a.id for a in snap.atoms.values()])  # 不把 UUID 写进原子键
    assert len(snap.of_type("affair")) == 1
    edges = {a.id for a in snap.of_type("edge")}
    assert f"{TRANSPOSE}>prerequisite>{LINEAR}" in edges and all(OLD not in e for e in edges)
    with pytest.raises(ReaderRoleError):
        DbAtomSource(kgedb.dsn("postgres"), "aya").load()
    assert atom_key("tp", "x") == atom_key("tp", "x") != atom_key("rp", "x")
