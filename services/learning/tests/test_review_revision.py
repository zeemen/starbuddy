"""KGE-06（specs/kge06-weekly-revision）：审核台的低置信打标候选工作单与修订发布预检。全部合成数据（规则 8）。

- 纯函数（不连库）：预检的版本号段与问题、改标核对、功能开关解析；
- 库上（一次性 Postgres，道法合成地图 MORAL.KR.*；登录角色与学科都与 test_review_console 分开，同一会话库里互不干扰）：
  开关关时接口不存在；工作单按置信度升序、游标翻页、带改标可选项；接受 / 驳回 / 改标后候选状态正确、处理人记进签字表；
  只有签字身份（starbuddy_review）能决定，知识服务身份调决定函数被库拒绝；非 staff 被拒；
  预检对结构性 / 非结构性样例的判断、版本号段与问题、签字进度。
"""
from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest

from review_console.app import create_app
from review_console.config import ReviewSettings, parse_features
from review_console.errors import ReviewError
from review_console.revision import preflight, structural_reasons
from review_console.service import candidate_view, check_relabel
from review_console.store import ReviewStore

S1 = uuid.UUID("6b060000-0000-4000-8000-0000000000a1")
S2 = uuid.UUID("6b060000-0000-4000-8000-0000000000a2")
GUARD = uuid.UUID("6b060000-0000-4000-8000-0000000000b1")
VP = uuid.UUID("6b060000-0000-4000-8000-0000000001c0")    # 1.0.0 已发布
VD = uuid.UUID("6b060000-0000-4000-8000-0000000001c1")    # 1.0.1 草稿：只改边界（非结构性）
VS = uuid.UUID("6b060000-0000-4000-8000-0000000001c2")    # 1.0.2 草稿：加一个节点（结构性，版本号段不够）
VM = uuid.UUID("6b060000-0000-4000-8000-0000000001c3")    # 1.1.0 草稿：加一个节点（结构性，升了次版本）
CV = uuid.UUID("6b060000-0000-4000-8000-0000000002c0")
ZERO = "0" * 64


# ---------- 纯函数 ----------
def pf(**kw):
    base = {"status": "draft", "draft_version": "1.0.1", "existing_versions": ["1.0.0"], "structural": False}
    return preflight(**{**base, **kw})


def codes(r, key="problems"):
    return [x["code"] for x in r[key]]


def test_preflight_non_structural_patch():
    r = pf()
    assert (r["bump"], r["suggested_version"], r["required_signers"], r["version_ok"]) == ("patch", "1.0.1", 1, True)
    assert r["latest_version"] == "1.0.0" and codes(r) == [] and codes(r, "advice") == [] and "只给建议" in r["note"]
    # 已撤下的版本也算（与发布函数同口径：已发布或已撤下的最大版本）
    r = pf(draft_version="1.0.3", existing_versions=["1.0.0", "1.0.2"])
    assert r["suggested_version"] == "1.0.3" and r["version_ok"]
    # 版本号要比最大的大；按数字比，不按字符串比
    assert codes(pf(draft_version="1.0.0")) == ["not_greater"]
    assert codes(pf(draft_version="1.0.9", existing_versions=["1.0.10"])) == ["not_greater"]
    assert pf(draft_version="1.0.11", existing_versions=["1.0.10"])["version_ok"]
    # 升了次版本：发布函数允许，但不是必须的（提示，不是问题）
    r = pf(draft_version="1.1.0")
    assert r["version_ok"] and codes(r, "advice") == ["minor_not_required"]
    assert codes(pf(draft_version="1.0.3"), "advice") == ["skips_versions"]
    assert codes(pf(draft_version="2.0.0"), "advice") == ["major_bump"]


def test_preflight_structural_minor_and_two_signers():
    r = pf(structural=True, draft_version="1.0.1")
    assert (r["bump"], r["suggested_version"], r["required_signers"]) == ("minor", "1.1.0", 2)
    assert codes(r) == ["structural_needs_minor"] and not r["version_ok"]
    r = pf(structural=True, draft_version="1.1.0", existing_versions=["1.0.0", "1.0.4"])
    assert r["version_ok"] and r["suggested_version"] == "1.1.0" and codes(r, "advice") == []
    assert codes(pf(structural=True, draft_version="1.2.0"), "advice") == ["skips_versions"]
    assert codes(pf(structural=True, draft_version="1.0.0")) == ["not_greater", "structural_needs_minor"]


def test_preflight_first_publish_and_format():
    r = pf(existing_versions=[], structural=True, draft_version="0.1.0")
    assert (r["bump"], r["suggested_version"], r["latest_version"], r["version_ok"]) == ("initial", None, None, True)
    assert codes(pf(draft_version="1.0")) == ["version_format"] and codes(pf(draft_version="v1.0.1")) == ["version_format"]
    assert codes(pf(status="published")) == ["not_draft"]


def test_structural_reasons_only_structural_and_unrejected():
    changes = [{"object": "node", "key": "X.A", "op": "added", "risk": {"structural": True, "reason": "node_added"}},
               {"object": "node", "key": "X.B", "op": "changed", "risk": {"structural": False, "reason": "boundary_supplement"}}]
    reqs = [{"id": "r1", "change_type": "node_split", "status": "proposed", "risk": {"structural": True, "reason": "node_split"}},
            {"id": "r2", "change_type": "node_split", "status": "rejected", "risk": {"structural": True, "reason": "node_split"}},
            {"id": "r3", "change_type": "boundary", "status": "proposed", "risk": {"structural": False, "reason": "boundary"}}]
    assert [(x["source"], x["key"]) for x in structural_reasons(changes, reqs)] == [("diff", "X.A"), ("change_request", "r1")]


def _cand(i, node, role="main", status="open", mv="m1", item="I1", **kw):
    return {"id": i, "node_id": node, "role": role, "status": status, "map_version_id": mv, "subject": "zhixing", "item_id": item,
            "node_name": node, "confidence": "0.5", "layer": "rule", **kw}


def test_check_relabel_and_alternatives():
    a, b = _cand("a", "N.A"), _cand("b", "N.B")
    check_relabel(a, b)
    with pytest.raises(ReviewError):  # 原候选已处理
        check_relabel(_cand("a", "N.A", status="accepted"), b)
    for bad in (_cand("b", "N.B", status="accepted"), _cand("b", "N.A"), _cand("b", "N.B", role="secondary"),
                _cand("b", "N.B", mv="m2"), _cand("b", "N.B", item="I2"), a):
        with pytest.raises(ReviewError) as e:
            check_relabel(a, bad)
        assert e.value.status == 409
    sib = [a, b, _cand("c", "N.A"), _cand("d", "N.C", role="secondary"), _cand("e", "N.D", mv="m2"), _cand("f", "N.E", item="I2")]
    assert [x["id"] for x in candidate_view(a, sib)["alternatives"]] == ["b"]


def test_feature_switch_parsing():
    assert parse_features("", "test") == frozenset()
    assert parse_features(" tag_worklist , revision_preflight ", "dev") == {"tag_worklist", "revision_preflight"}
    for raw, env in (("tag_worklist", "pilot"), ("tag_worklist", "production"), ("tag-worklist", "dev"), ("everything", "test")):
        with pytest.raises(RuntimeError):
            parse_features(raw, env)
    base = {"REVIEW_CONSOLE_ENV": "test", "REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://a", "REVIEW_CONSOLE_REVIEW_DATABASE_URL": "postgresql://b"}
    assert ReviewSettings.from_env(base).features == frozenset()
    assert ReviewSettings.from_env({**base, "REVIEW_CONSOLE_FEATURES": "tag_worklist"}).features == {"tag_worklist"}
    with pytest.raises(RuntimeError):
        create_app(features=frozenset({"unknown"}))


# ---------- 库上 ----------
SEED = f"""
CREATE ROLE k6_kge_app LOGIN IN ROLE starbuddy_kge;
CREATE ROLE k6_sign_app LOGIN IN ROLE starbuddy_review;
INSERT INTO platform.account (id, kind) VALUES ('{S1}', 'staff'), ('{S2}', 'staff'), ('{GUARD}', 'guardian');
SET ROLE starbuddy_learning;
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
  VALUES ('{CV}', 'synth-k6', 2031, 8, 'syn-k6', 'zhixing', '0.1.0');
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('zhixing', 'SYN-K6-01', '{CV}', 'syn-k6-01');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission) VALUES
  ('zhixing', 'SYN-K6-I1', '{CV}', 'SYN-K6-01', 'pending_review'), ('zhixing', 'SYN-K6-I2', '{CV}', 'SYN-K6-01', 'pending_review'),
  ('zhixing', 'SYN-K6-I3', '{CV}', 'SYN-K6-01', 'pending_review'), ('zhixing', 'SYN-K6-I4', '{CV}', 'SYN-K6-01', 'pending_review');
RESET ROLE;
"""


def version_sql(vid: uuid.UUID, version: str, excludes: str = "{b}", extra_node: bool = False) -> str:
    extra = (f",\n      ('{vid}', 'MORAL.KR.T.C', 'node', 'MORAL.KR.T', 'kp', '合成节点丙', '{{a}}', '{{b}}', '{{c}}', '{{understand}}')"
             if extra_node else "")
    return f"""
    INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('{vid}', 'zhixing', 2022, '{version}');
    INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
      ('{vid}', 'MORAL.KR', 'domain', NULL, NULL, '合成领域', '{{}}', '{{}}', '{{}}', '{{}}'),
      ('{vid}', 'MORAL.KR.T', 'topic', 'MORAL.KR', NULL, '合成主题', '{{}}', '{{}}', '{{}}', '{{}}'),
      ('{vid}', 'MORAL.KR.T.A', 'node', 'MORAL.KR.T', 'kp', '合成节点甲', '{{a}}', '{excludes}', '{{c}}', '{{understand}}'),
      ('{vid}', 'MORAL.KR.T.B', 'node', 'MORAL.KR.T', 'kp', '合成节点乙', '{{a}}', '{{b}}', '{{c}}', '{{understand}}'){extra};
    INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
      SELECT '{vid}', n.id, 'syn-2024', 2024, 'v', 'c', 1, 1 FROM kge.knowledge_node n WHERE n.map_version_id = '{vid}' AND n.level = 'node';
    """


def cand_sql(cid: str, vid: uuid.UUID, item: str, node: str, role: str, conf: str, layer: str = "rule") -> str:
    mv = "'algo:syn-tagger-model@0.1.0'" if layer == "model" else "NULL"
    return (f"INSERT INTO kge.tag_candidate (id, map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, "
            f"model_version, reason_code, grade, item_source_kind) VALUES ('{cid}', '{vid}', 'zhixing', '{item}', '{node}', '{role}', "
            f"'{layer}', {conf}, 'syn-tagger@1', {mv}, 'syn_low_conf', 'B', 'legacy');")


def C(n: int) -> str:
    return f"6b060000-0000-4000-8000-0000000003{n:02d}"


def H(account: uuid.UUID) -> dict:
    return {"x-test-account": str(account)}


def _auth():
    async def _a(req):
        v = req.headers.get("x-test-account")
        return uuid.UUID(v) if v else None
    return _a


@pytest.fixture(scope="module")
async def k6(pg):
    pg.psql(SEED + "SET ROLE starbuddy_kge;" + version_sql(VP, "1.0.0") + "RESET ROLE;")
    kp = await asyncpg.create_pool(**pg.dsn("k6_kge_app"), min_size=1, max_size=4)
    rp = await asyncpg.create_pool(**pg.dsn("k6_sign_app"), min_size=1, max_size=2)
    app = create_app(authenticate=_auth(), pools=(kp, rp), features=frozenset({"tag_worklist", "revision_preflight"}))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            # 1.0.0：两名 staff 签字后发布（首次发布整张图都是新增，结构性）
            d = (await client.get(f"/review/v0/map-versions/{VP}/diff", headers=H(S1))).json()
            for s in (S1, S2):
                r = await client.post(f"/review/v0/map-versions/{VP}/sign", json={"decision": "approved", "expected_digest": d["digest"]},
                                      headers=H(s))
                assert r.status_code == 200, r.text
            r = await client.post(f"/review/v0/map-versions/{VP}/publish", json={"expected_digest": d["digest"]}, headers=H(S1))
            assert r.status_code == 200, r.text
            yield client, kp, rp
    await kp.close()
    await rp.close()


async def test_features_off_routes_absent(pg, k6):
    _, kp, rp = k6
    app = create_app(authenticate=_auth(), pools=(kp, rp))  # 缺省全关
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            assert (await c.get("/review/v0/tag-candidates", params={"subject": "zhixing"}, headers=H(S1))).status_code == 404
            r = await c.post(f"/review/v0/tag-candidates/{C(1)}/decide", json={"decision": "accept"}, headers=H(S1))
            assert r.status_code == 404
            assert (await c.get(f"/review/v0/map-versions/{VP}/preflight", headers=H(S1))).status_code == 404
            assert (await c.get(f"/review/v0/map-versions/{VP}/diff", headers=H(S1))).status_code == 200  # 原有接口照常
    app = create_app(authenticate=_auth(), pools=(kp, rp), features=frozenset({"revision_preflight"}))  # 只开一项
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            assert (await c.get("/review/v0/tag-candidates", params={"subject": "zhixing"}, headers=H(S1))).status_code == 404
            assert (await c.get(f"/review/v0/map-versions/{VP}/preflight", headers=H(S1))).status_code == 200


async def test_preflight_structural_and_non_structural(pg, k6):
    c, _, _ = k6
    pg.psql("SET ROLE starbuddy_kge;" + version_sql(VD, "1.0.1", excludes="{b,新边界}") + version_sql(VS, "1.0.2", extra_node=True)
            + version_sql(VM, "1.1.0", extra_node=True) + "RESET ROLE;")
    # 非结构性：只改了“不包含”→ 修订号、一人签、版本号合规
    p = (await c.get(f"/review/v0/map-versions/{VD}/preflight", headers=H(S1))).json()
    assert (p["structural"], p["bump"], p["required_signers"], p["suggested_version"], p["version_ok"]) == (False, "patch", 1, "1.0.1", True)
    assert p["base_version"] == "1.0.0" and p["latest_version"] == "1.0.0" and p["structural_reasons"] == []
    assert p["problems"] == [] and p["signoff"] == {"approvals": 0, "required": 1, "latest_decision": None, "latest_matches": False,
                                                    "enough": False}
    assert p["impact"] == {"item_tags": 0, "scoring_points": 0} and "只给建议" in p["note"]
    # 结构性：加了节点 → 次版本、两人签；1.0.2 不够（发布函数会拒），建议 1.1.0
    p = (await c.get(f"/review/v0/map-versions/{VS}/preflight", headers=H(S1))).json()
    assert (p["structural"], p["bump"], p["required_signers"], p["suggested_version"]) == (True, "minor", 2, "1.1.0")
    assert [x["code"] for x in p["problems"]] == ["structural_needs_minor"] and not p["version_ok"]
    assert [(x["object"], x["key"], x["op"]) for x in p["structural_reasons"]] == [("node", "MORAL.KR.T.C", "added")]
    p = (await c.get(f"/review/v0/map-versions/{VM}/preflight", headers=H(S1))).json()
    assert p["structural"] and p["version_ok"] and p["problems"] == [] and p["advice"] == []
    # 签字进度与发布口径一致：一人批准不够，两人够
    for who, enough in ((S1, False), (S2, True)):
        r = await c.post(f"/review/v0/map-versions/{VM}/sign", json={"decision": "approved", "expected_digest": p["digest"]}, headers=H(who))
        assert r.status_code == 200
        q = (await c.get(f"/review/v0/map-versions/{VM}/preflight", headers=H(S1))).json()
        assert q["signoff"]["enough"] is enough
    # 已发布版本：不是草稿
    p = (await c.get(f"/review/v0/map-versions/{VP}/preflight", headers=H(S1))).json()
    assert [x["code"] for x in p["problems"]][:1] == ["not_draft"]
    assert (await c.get(f"/review/v0/map-versions/{uuid.uuid4()}/preflight", headers=H(S1))).status_code == 404
    assert (await c.get(f"/review/v0/map-versions/{VD}/preflight")).status_code == 401  # 没有会话


async def test_worklist_order_paging_and_decisions(pg, k6):
    c, kp, rp = k6
    pg.psql("SET ROLE starbuddy_kge;" + "".join([
        cand_sql(C(1), VD, "SYN-K6-I1", "MORAL.KR.T.A", "main", "0.40"),
        cand_sql(C(2), VD, "SYN-K6-I1", "MORAL.KR.T.B", "main", "0.55", "vector"),
        cand_sql(C(3), VD, "SYN-K6-I2", "MORAL.KR.T.A", "main", "0.30"),
        cand_sql(C(4), VD, "SYN-K6-I2", "MORAL.KR.T.B", "secondary", "0.62", "model"),
        cand_sql(C(5), VP, "SYN-K6-I3", "MORAL.KR.T.A", "main", "0.20"),
        cand_sql(C(6), VP, "SYN-K6-I3", "MORAL.KR.T.B", "main", "0.20"),
        cand_sql(C(7), VD, "SYN-K6-I4", "MORAL.KR.T.A", "secondary", "0.45"),
        cand_sql(C(8), VD, "SYN-K6-I4", "MORAL.KR.T.B", "secondary", "0.46"),
        cand_sql(C(9), VD, "SYN-K6-I1", "MORAL.KR.T.A", "secondary", "0.70"),
    ]) + "RESET ROLE;")
    # 置信度升序，同置信度按 id；游标翻页（每页 1 条：同置信度的两条正好跨页，游标必须带 id）
    seen, after = [], {}
    while True:
        r = await c.get("/review/v0/tag-candidates", params={"subject": "zhixing", "limit": 1, **after}, headers=H(S1))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["open_total"] == 9
        seen += [(x["id"], x["confidence"]) for x in body["items"]]
        if body["next"] is None or len(seen) > 20:
            break
        after = body["next"]
    assert [i for i, _ in seen] == [C(5), C(6), C(3), C(1), C(7), C(8), C(2), C(4), C(9)]
    assert [cf for _, cf in seen] == ["0.200", "0.200", "0.300", "0.400", "0.450", "0.460", "0.550", "0.620", "0.700"]
    one = (await c.get("/review/v0/tag-candidates", params={"subject": "zhixing", "map_version_id": str(VD), "limit": 1},
                       headers=H(S1))).json()
    first = one["items"][0]
    assert first["id"] == C(3) and first["version"] == "1.0.1" and first["map_status"] == "draft" and one["open_total"] == 7
    assert first["node_name"] == "合成节点甲" and first["alternatives"] == []  # I2 只有一条主考点候选（次要节点不是它的改标项）
    lst =(await c.get("/review/v0/tag-candidates", params={"subject": "zhixing", "limit": 100}, headers=H(S1))).json()["items"]
    by = {x["id"]: x for x in lst}
    assert [a["id"] for a in by[C(1)]["alternatives"]] == [C(2)] and [a["id"] for a in by[C(7)]["alternatives"]] == [C(8)]
    # 参数校验
    for params in ({"subject": "zhixing", "limit": 0}, {"subject": "zhixing", "limit": 101}, {"subject": "math"},
                   {"subject": "zhixing", "after_id": C(1)}, {"subject": "zhixing", "after_confidence": "0.5"},
                   {"subject": "zhixing", "after_confidence": "1.5", "after_id": C(1)},
                   {"subject": "zhixing", "after_confidence": "NaN", "after_id": C(1)}):
        assert (await c.get("/review/v0/tag-candidates", params=params, headers=H(S1))).status_code == 422, params

    async def decide(cid, who=S1, **body):
        return await c.post(f"/review/v0/tag-candidates/{cid}/decide", json=body, headers=H(who))

    def state(cid):
        return pg.psql(f"SELECT status || '|' || coalesce((SELECT account_id::text || ':' || decision FROM platform.tag_candidate_signoff"
                       f" WHERE tag_candidate_id = '{cid}'), '-') FROM kge.tag_candidate WHERE id = '{cid}'").strip()

    def tag(item):
        return pg.psql(f"SELECT main_node_id || '|' || array_to_string(node_ids, ',') || '|' || tag_source FROM kge.item_tag"
                       f" WHERE map_version_id = '{VD}' AND item_id = '{item}'").strip()

    # 非 staff（监护人账号）与没有会话：被拒；参数不对：422
    assert (await decide(C(3), GUARD, decision="accept")).status_code == 403
    assert state(C(3)) == "open|-"
    assert (await c.post(f"/review/v0/tag-candidates/{C(3)}/decide", json={"decision": "accept"})).status_code == 401
    assert (await decide(C(3), decision="approve")).status_code == 422
    assert (await decide(C(3), decision="accept", target_id=C(1))).status_code == 422
    assert (await decide(C(1), decision="relabel")).status_code == 422
    # 接受主考点（草稿）：写回 item_tag（人工、置信度 1），处理人记进签字表
    r = await decide(C(3), decision="accept")
    assert r.status_code == 200 and r.json() == {"status": "written", "decision": "accept", "decided_by": str(S1)}
    assert state(C(3)) == f"accepted|{S1}:accepted" and tag("SYN-K6-I2") == "MORAL.KR.T.A|MORAL.KR.T.A|human"
    # 再处理同一条：409
    r = await decide(C(3), decision="reject")
    assert r.status_code == 409 and r.json()["code"] == "RULE_VIOLATION"
    # 次要节点：接受后追加
    r = await decide(C(4), S2, decision="accept")
    assert r.json()["status"] == "written" and state(C(4)) == f"accepted|{S2}:accepted"
    assert tag("SYN-K6-I2") == "MORAL.KR.T.A|MORAL.KR.T.A,MORAL.KR.T.B|human"
    # 改标（主考点，草稿）：接受 B、原候选 A 被驳回（库函数随接受一并驳回），一个事务
    r = await decide(C(1), decision="relabel", target_id=C(2))
    assert r.status_code == 200 and r.json()["status"] == "written"
    assert state(C(2)) == f"accepted|{S1}:accepted" and state(C(1)) == f"rejected|{S1}:rejected"
    assert tag("SYN-K6-I1") == "MORAL.KR.T.B|MORAL.KR.T.B|human"
    # 改标（已发布版本只记结论）：原候选另行驳回
    r = await decide(C(5), decision="relabel", target_id=C(6))
    assert r.json()["status"] == "recorded"
    assert state(C(6)) == f"accepted|{S1}:accepted" and state(C(5)) == f"rejected|{S1}:rejected"
    # 改标（次要节点，草稿，但该题还没有主考点）：库函数拒绝接受 → 整笔回滚，原候选仍待处理
    r = await decide(C(7), decision="relabel", target_id=C(8))
    assert r.status_code == 409 and state(C(7)) == "open|-" and state(C(8)) == "open|-"
    # 改标的目标不合格：不同题（目标仍待处理，库函数本来接受得了）、已处理；都不动任何候选
    assert (await decide(C(7), decision="relabel", target_id=C(9))).status_code == 409
    assert state(C(7)) == "open|-" and state(C(9)) == "open|-"
    assert (await decide(C(7), decision="relabel", target_id=C(3))).status_code == 409
    assert (await decide(C(7), decision="relabel", target_id=str(uuid.uuid4()))).status_code == 404
    # 并发：原候选先被别人接受，再改标 → 409，目标候选仍待处理（已发布版本上接受不会连带驳回同题候选）
    pg.psql("SET ROLE starbuddy_kge;" + cand_sql(C(10), VP, "SYN-K6-I4", "MORAL.KR.T.A", "main", "0.80")
            + cand_sql(C(11), VP, "SYN-K6-I4", "MORAL.KR.T.B", "main", "0.81") + "RESET ROLE;")
    assert (await decide(C(10), S2, decision="accept")).json()["status"] == "recorded"
    r = await decide(C(10), decision="relabel", target_id=C(11))  # 接口先核对：原候选已处理
    assert r.status_code == 409 and state(C(11)) == "open|-"
    with pytest.raises(ReviewError) as e:  # 绕过接口核对，模拟核对之后才被别人处理：库里驳回失败，整笔回滚
        await ReviewStore(rp).relabel_tag(S1, uuid.UUID(C(10)), uuid.UUID(C(11)))
    assert e.value.status == 409 and e.value.code == "RULE_VIOLATION"
    assert state(C(10)) == f"accepted|{S2}:accepted" and state(C(11)) == "open|-"
    assert (await decide(C(11), S2, decision="reject")).status_code == 200
    # 驳回只记结论
    r = await decide(C(7), S2, decision="reject")
    assert r.json() == {"status": "recorded", "decision": "reject", "decided_by": str(S2)} and state(C(7)) == f"rejected|{S2}:rejected"
    left = (await c.get("/review/v0/tag-candidates", params={"subject": "zhixing"}, headers=H(S1))).json()
    assert [x["id"] for x in left["items"]] == [C(8), C(9)] and left["open_total"] == 2
    # 只有签字身份能决定：知识服务身份直接调决定函数被库拒绝（没有 EXECUTE 授权）
    async with kp.acquire() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("SELECT kge.decide_tag_candidate($1, 'accepted')", uuid.UUID(C(8)))
        # 知识服务身份也不能直接改候选结论
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE kge.tag_candidate SET status = 'accepted', decided_at = now() WHERE id = $1", uuid.UUID(C(8)))
    assert state(C(8)) == "open|-"
