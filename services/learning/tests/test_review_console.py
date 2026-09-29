"""审核台后端（PR 2）在一次性库上的测试：两个登录角色、合成 staff、文砺学科的合成地图（CHN.*）。

身份：测试注入一个只认合成 staff 的认证器；应用本身不接受请求自报账号（默认 deny_all）。
"""
from __future__ import annotations

import io
import uuid

import asyncpg
import httpx
import pytest

from review_console.__main__ import write_records
from review_console.app import create_app, deny_all
from review_console.store import check_roles

SF1 = uuid.UUID("5f100000-0000-4000-8000-00000000005f")
SF2 = uuid.UUID("5f200000-0000-4000-8000-00000000005f")
PARENT = uuid.UUID("5f300000-0000-4000-8000-00000000005f")
V1 = uuid.UUID("c0100000-0000-4000-8000-0000000000c1")
V2 = uuid.UUID("c0200000-0000-4000-8000-0000000000c2")
ZERO = "0" * 64

SEED = f"""
CREATE ROLE review_kge_app LOGIN IN ROLE starbuddy_kge;
CREATE ROLE review_sign_app LOGIN IN ROLE starbuddy_review;
INSERT INTO platform.account (id, kind) VALUES ('{SF1}', 'staff'), ('{SF2}', 'staff'), ('{PARENT}', 'guardian');
"""


def version_sql(vid: uuid.UUID, version: str, excludes: str = "{b}") -> str:
    return f"""
    INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('{vid}', 'wenli', 2022, '{version}');
    INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
      ('{vid}', 'CHN.RC', 'domain', NULL, NULL, '合成领域', '{{}}', '{{}}', '{{}}', '{{}}'),
      ('{vid}', 'CHN.RC.T', 'topic', 'CHN.RC', NULL, '合成主题', '{{}}', '{{}}', '{{}}', '{{}}'),
      ('{vid}', 'CHN.RC.T.A', 'node', 'CHN.RC.T', 'kp', '合成节点', '{{a}}', '{excludes}', '{{c}}', '{{understand}}');
    INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
      VALUES ('{vid}', 'CHN.RC.T.A', 'syn-2024', 2024, 'v', 'c', 1, 1);
    """


def staff_auth(request):
    # 只在测试里：按请求头选合成账号，模拟 PR 4 的登录会话
    async def _a(req):
        v = req.headers.get("x-test-account")
        return uuid.UUID(v) if v else None
    return _a


@pytest.fixture(scope="module")
async def rc(pg):
    pg.psql(SEED + "SET ROLE starbuddy_kge;" + version_sql(V1, "1.0.0"))
    kp = await asyncpg.create_pool(**pg.dsn("review_kge_app"), min_size=1, max_size=4)
    rp = await asyncpg.create_pool(**pg.dsn("review_sign_app"), min_size=1, max_size=2)
    app = create_app(authenticate=staff_auth(None), pools=(kp, rp))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rc") as client:
            yield client, kp, rp
    await kp.close()
    await rp.close()


def H(account: uuid.UUID) -> dict:
    return {"x-test-account": str(account)}


async def test_default_app_denies_everything(pg, rc):
    _, kp, rp = rc
    app = create_app(pools=(kp, rp))
    assert app is not None and deny_all is not None
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rc") as c:
            r = await c.get("/review/v0/map-versions", params={"subject": "wenli"})
            assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
            r = await c.post(f"/review/v0/map-versions/{V1}/publish", json={"expected_digest": ZERO},
                             headers=H(SF1))
            assert r.status_code == 401  # 自报账号头不被默认应用采信


async def test_role_check_rejects_shared_or_wrong_roles(pg, rc):
    _, kp, rp = rc
    with pytest.raises(RuntimeError):
        await check_roles(kp, kp)
    with pytest.raises(RuntimeError):
        await check_roles(rp, kp)
    admin = await asyncpg.create_pool(**pg.dsn("postgres"), min_size=1, max_size=1)
    try:
        with pytest.raises(RuntimeError):
            await check_roles(kp, admin)
    finally:
        await admin.close()


async def test_first_publish_flow(rc):
    c, _, _ = rc
    r = await c.get("/review/v0/map-versions", params={"subject": "wenli"}, headers=H(SF1))
    assert [v["id"] for v in r.json()["items"]] == [str(V1)]
    d = (await c.get(f"/review/v0/map-versions/{V1}/diff", headers=H(SF1))).json()
    assert d["base_version_id"] is None
    assert {x["op"] for x in d["changes"]} == {"added"}
    assert d["summary"]["structural"] and d["summary"]["required_signers"] == 2
    assert [(i["check_name"], i["severity"]) for i in d["validation"]] == [("few_items", "warning")] and d["signoffs"] == []
    # 校验问题挂到对应节点的差异条目上
    node_a = [x for x in d["changes"] if x["object"] == "node" and x["key"] == "CHN.RC.T.A"][0]
    assert [i["check_name"] for i in node_a["issues"]] == ["few_items"]
    dg = d["digest"]
    # 看到的就是签的：摘要不符拒签
    r = await c.post(f"/review/v0/map-versions/{V1}/sign", json={"decision": "approved", "expected_digest": ZERO}, headers=H(SF1))
    assert r.status_code == 409 and r.json()["code"] == "DIGEST_CHANGED"
    r = await c.post(f"/review/v0/map-versions/{V1}/sign", json={"decision": "approved", "expected_digest": dg}, headers=H(SF1))
    assert r.status_code == 200
    r = await c.post(f"/review/v0/map-versions/{V1}/publish", json={"expected_digest": dg}, headers=H(SF1))
    assert r.status_code == 409 and r.json()["code"] == "RULE_VIOLATION" and "两名不同 staff" in r.json()["detail"]
    r = await c.post(f"/review/v0/map-versions/{V1}/sign", json={"decision": "approved", "expected_digest": dg}, headers=H(SF2))
    assert r.status_code == 200
    r = await c.post(f"/review/v0/map-versions/{V1}/publish", json={"expected_digest": dg}, headers=H(SF2))
    assert r.status_code == 200 and r.json()["published_at"]
    d = (await c.get(f"/review/v0/map-versions/{V1}/diff", headers=H(SF1))).json()
    assert d["version"]["status"] == "published"
    assert sorted((s["action"], s["signer"]) for s in d["signoffs"]) == sorted(
        [("review", str(SF1)), ("review", str(SF2)), ("publish", str(SF2))])


async def test_edit_boundary_then_single_signer_publish(pg, rc):
    c, _, _ = rc
    pg.psql("SET ROLE starbuddy_kge;" + version_sql(V2, "1.0.1"))
    d = (await c.get(f"/review/v0/map-versions/{V2}/diff", headers=H(SF1))).json()
    assert d["base_version_id"] == str(V1) and d["changes"] == []
    # 改一个节点的“不包含”：带旧摘要被拒，带当前摘要成功
    r = await c.patch(f"/review/v0/map-versions/{V2}/nodes/CHN.RC.T.A", json={"excludes": ["b", "新不包含"], "expected_digest": ZERO},
                      headers=H(SF1))
    assert r.status_code == 409 and r.json()["code"] == "DIGEST_CHANGED"
    r = await c.patch(f"/review/v0/map-versions/{V2}/nodes/CHN.RC.T.A",
                      json={"excludes": ["b", "新不包含"], "expected_digest": d["digest"]}, headers=H(SF1))
    assert r.status_code == 200
    dg = r.json()["digest"]
    d = (await c.get(f"/review/v0/map-versions/{V2}/diff", headers=H(SF1))).json()
    assert d["digest"] == dg
    assert [(x["key"], x["field"], x["old"], x["new"], x["risk"]["level"]) for x in d["changes"]] == [
        ("CHN.RC.T.A", "excludes", ["b"], ["b", "新不包含"], "medium")]
    assert d["summary"]["required_signers"] == 1 and not d["summary"]["structural"]
    assert d["impact"] == {"item_tags": 0, "scoring_points": 0}
    # 不允许的字段、未知字段、结构字段
    for body in ({"status": "deprecated", "expected_digest": dg}, {"expected_digest": dg}, {"parent_id": "CHN.RC", "expected_digest": dg}):
        r = await c.patch(f"/review/v0/map-versions/{V2}/nodes/CHN.RC.T.A", json=body, headers=H(SF1))
        assert r.status_code == 422, body
    r = await c.patch(f"/review/v0/map-versions/{V2}/nodes/CHN.RC.T.Z", json={"excludes": ["x"], "expected_digest": dg}, headers=H(SF1))
    assert r.status_code == 404
    r = await c.post(f"/review/v0/map-versions/{V2}/sign", json={"decision": "approved", "expected_digest": dg}, headers=H(SF1))
    assert r.status_code == 200
    r = await c.post(f"/review/v0/map-versions/{V2}/publish", json={"expected_digest": dg}, headers=H(SF1))
    assert r.status_code == 200, r.json()
    vs = {v["id"]: v["status"] for v in (await c.get("/review/v0/map-versions", params={"subject": "wenli"}, headers=H(SF1))).json()["items"]}
    assert vs == {str(V1): "retired", str(V2): "published"}
    # 已发布版本不能再改
    r = await c.patch(f"/review/v0/map-versions/{V2}/nodes/CHN.RC.T.A", json={"excludes": ["x"], "expected_digest": dg}, headers=H(SF1))
    assert r.status_code == 409 and r.json()["code"] == "RULE_VIOLATION"


async def test_non_staff_and_retire(rc):
    c, _, _ = rc
    r = await c.post(f"/review/v0/map-versions/{V2}/retire", json={"basis_code": "wrong_version"}, headers=H(PARENT))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    r = await c.post(f"/review/v0/map-versions/{V2}/retire", json={}, headers=H(SF1))
    assert r.status_code == 422 and "detail" not in r.json()
    r = await c.post(f"/review/v0/map-versions/{V2}/retire", json={"basis_code": "Bad Code"}, headers=H(SF1))
    assert r.status_code == 422
    r = await c.post(f"/review/v0/map-versions/{V2}/retire", json={"basis_code": "wrong_version"}, headers=H(SF1))
    assert r.status_code == 200
    r = await c.get("/review/v0/map-versions", params={"subject": "english"}, headers=H(SF1))
    assert r.status_code == 422
    r = await c.post(f"/review/v0/map-versions/{V2}/sign", json={"decision": "maybe", "expected_digest": ZERO}, headers=H(SF1))
    assert r.status_code == 422


async def test_records_export(rc):
    c, kp, _ = rc
    items = (await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"]
    assert [(i["version"], i["action"]) for i in items] == [
        ("1.0.0", "review"), ("1.0.0", "review"), ("1.0.0", "publish"),
        ("1.0.1", "review"), ("1.0.0", "retire"), ("1.0.1", "publish"), ("1.0.1", "retire")]
    assert items[-1]["basis_code"] == "wrong_version" and items[-1]["signer"] == str(SF1)
    from review_console.store import KgeStore
    rows = await KgeStore(kp).records("wenli")
    a, b = io.StringIO(), io.StringIO()
    write_records(rows, "csv", a)
    write_records(rows, "csv", b)
    assert a.getvalue() == b.getvalue()
    assert a.getvalue().splitlines()[0] == "map_version_id,version,action,decision,risk_level,basis_code,digest,decided_at,signer"
    assert len(a.getvalue().splitlines()) == 8
