"""审核台后端（PR 2）在一次性库上的测试：两个登录角色、合成 staff、文砺学科的合成地图（CHN.*）。

身份：多数用例注入一个只认合成 staff 的认证器；默认应用走 staff 登录会话（PR 4），不接受请求自报账号。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import logging
import re
import time
import uuid

import asyncpg
import httpx
import pytest

from review_console.__main__ import write_records
from review_console.app import create_app
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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            yield client, kp, rp
    await kp.close()
    await rp.close()


def H(account: uuid.UUID) -> dict:
    return {"x-test-account": str(account)}


SECRET = bytes(range(1, 21))  # 合成 TOTP 密钥（20 字节）
PASSWORD = "synthetic-password-rc1"


def totp(secret: bytes, step: int) -> str:
    """RFC 6238 的 6 位码，测试方独立实现（与库里的 platform.staff_totp 对照）。"""
    h = hmac.new(secret, step.to_bytes(8, "big"), hashlib.sha1).digest()
    o = h[19] & 15
    return f"{(int.from_bytes(h[o:o + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


async def test_session_login_flow(pg, rc, caplog):
    _, kp, rp = rc
    pg.psql(f"SELECT platform.staff_enroll('{SF1}', 'teacher.rc1', '{PASSWORD}', '\\x{SECRET.hex()}'::bytea);")
    step = int(time.time()) // 30
    app = create_app(pools=(kp, rp))  # 默认：staff 登录会话
    caplog.set_level(logging.DEBUG)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            r = await c.get("/review/v0/map-versions", params={"subject": "wenli"})
            assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
            # 自报账号头、伪造令牌都不被采信
            r = await c.post(f"/review/v0/map-versions/{V1}/publish", json={"expected_digest": ZERO}, headers=H(SF1))
            assert r.status_code == 401
            r = await c.get("/review/v0/auth/me", headers={"x-review-session": "a" * 64})
            assert r.status_code == 401
            # 登录失败：同一个 401，不回显任何输入
            for body in ({"login_name": "teacher.rc1", "password": PASSWORD, "code": totp(SECRET, step + 5)},
                         {"login_name": "teacher.rc1", "password": "wrong-password", "code": totp(SECRET, step)},
                         {"login_name": "nobody.here", "password": PASSWORD, "code": totp(SECRET, step)}):
                r = await c.post("/review/v0/auth/login", json=body)
                assert r.status_code == 401 and r.json() == {"type": "urn:starbuddy:review:login_failed", "status": 401, "code": "LOGIN_FAILED"}
            r = await c.post("/review/v0/auth/login", json={"login_name": "teacher.rc1", "password": PASSWORD, "code": "12ab56"})
            assert r.status_code == 422
            # 请求体超过 64 KB：先拒绝，不读完
            r = await c.post("/review/v0/auth/login", content=b"{" + b" " * (70 * 1024) + b"}", headers={"content-type": "application/json"})
            assert r.status_code == 413 and r.json()["code"] == "TOO_LARGE"
            # Host 不是本机（DNS 重绑定）：连登录接口都不给调
            r = await c.post("/review/v0/auth/login", headers={"host": "evil.example.test"},
                             json={"login_name": "teacher.rc1", "password": PASSWORD, "code": totp(SECRET, step)})
            assert r.status_code == 421 and r.json()["code"] == "MISDIRECTED"
            # 成功
            r = await c.post("/review/v0/auth/login", json={"login_name": "teacher.rc1", "password": PASSWORD, "code": totp(SECRET, step)})
            assert r.status_code == 200
            token = r.json()["token"]
            assert re.fullmatch(r"[0-9a-f]{64}", token)
            S = {"x-review-session": token}
            assert (await c.get("/review/v0/auth/me", headers=S)).json() == {"account_id": str(SF1), "login_name": "teacher.rc1"}
            assert (await c.get("/review/v0/map-versions", params={"subject": "wenli"}, headers=S)).status_code == 200
            # 同一个码不能再用
            r = await c.post("/review/v0/auth/login", json={"login_name": "teacher.rc1", "password": PASSWORD, "code": totp(SECRET, step)})
            assert r.status_code == 401
            # 退出后令牌失效
            assert (await c.post("/review/v0/auth/logout", headers=S)).status_code == 200
            assert (await c.get("/review/v0/auth/me", headers=S)).status_code == 401
    logs = caplog.text
    assert token not in logs and PASSWORD not in logs and "teacher.rc1" not in logs


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
    assert d["base_version"] is None and d["summary"]["approvals"] == 0
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
    for body in ({"status": "deprecated", "expected_digest": dg}, {"expected_digest": dg}, {"parent_id": "CHN.RC", "expected_digest": dg},
                 {"excludes": ["含\x00控制字符"], "expected_digest": dg}, {"name": "换行\n", "expected_digest": dg}):
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


CONTENT_LINES = ("content_version", "admission")


def no_content(rows: list[dict]) -> list[dict]:
    """去掉内容版本与准入行（冲刺 4 起版本记录含这两类行；会话共用的库里别的模块的端到端测试会经签字函数发布登记的内容版本）。"""
    return [r for r in rows if r["line"] not in CONTENT_LINES]


async def test_records_export(rc):
    c, kp, _ = rc
    items = no_content((await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"])
    assert [(i["version"], i["action"]) for i in items] == [
        ("1.0.0", "review"), ("1.0.0", "review"), ("1.0.0", "publish"),
        ("1.0.1", "review"), ("1.0.0", "retire"), ("1.0.1", "publish"), ("1.0.1", "retire")]
    assert items[-1]["basis_code"] == "wrong_version" and items[-1]["signer"] == str(SF1)
    from review_console.service import version_record
    from review_console.store import KgeStore
    rows = no_content(await version_record(KgeStore(kp), "wenli"))
    a, b = io.StringIO(), io.StringIO()
    write_records(rows, "csv", a)
    write_records(rows, "csv", b)
    assert a.getvalue() == b.getvalue()
    assert a.getvalue().splitlines()[0] == ("line,map_version_id,version,action,decision,risk_level,basis_code,digest,decided_at,signer,"
                                            "rubric_versions,validation_errors,validation_warnings,page_id,page_commit,"
                                            "policy_object,policy_object_id,city,cohort,policy_version,"
                                            "content_version_id,six_segment,content_map_version_id,item_id,from_admission,to_admission,"
                                            "admission_review_level")
    assert len(a.getvalue().splitlines()) == 8
    assert all(i["line"] == "map" and i["validation_errors"] == 0 and i["validation_warnings"] == 1 for i in items)


V3 = uuid.UUID("c0300000-0000-4000-8000-0000000000c3")
CH = uuid.UUID("c0400000-0000-4000-8000-0000000000c4")


async def test_structural_change_request_needs_two_and_digest(pg, rc):
    c, _, _ = rc
    pg.psql("SET ROLE starbuddy_kge;" + version_sql(V3, "1.1.0") + f"""
      INSERT INTO kge.map_change (id, subject, change_type, payload, target_version)
        VALUES ('{CH}', 'wenli', 'node_add', '{{"node": "CHN.RC.T.A"}}', '{V3}');""")
    d = (await c.get(f"/review/v0/map-versions/{V3}/diff", headers=H(SF1))).json()
    # 没有已发布版本（V2 已撤下）：库判为结构性；另有待审的结构性变更单
    assert d["summary"]["structural"] and d["summary"]["required_signers"] == 2
    req = d["change_requests"][0]
    assert req["risk"]["structural"] and req["signed_content"]["payload"] == {"node": "CHN.RC.T.A"} and len(req["digest"]) == 64
    # 页面显示的内容就是签字覆盖的内容：它的摘要等于变更单摘要（契约审查 PR 3 B1）
    assert set(req["signed_content"]) >= {"payload", "evidence_pack", "confidence", "subject", "target_version", "change_type", "id"}
    assert pg.psql(f"SELECT kge.row_digest(to_jsonb(m) - 'status' - 'reviewers' - 'created_at') = kge.map_change_digest_of(m.id)"
                   f" FROM kge.map_change m WHERE m.id = '{CH}'").strip() == "t"
    assert d["summary"]["approvals"] == 0 and d["summary"]["latest_decision"] is None
    r = await c.post(f"/review/v0/map-changes/{CH}/sign", json={"decision": "approved", "expected_digest": ZERO}, headers=H(SF1))
    assert r.status_code == 409 and "摘要不符" in r.json()["detail"]
    r = await c.post(f"/review/v0/map-changes/{CH}/sign", json={"decision": "approved"}, headers=H(SF1))
    assert r.status_code == 422
    r = await c.post(f"/review/v0/map-changes/{CH}/sign", json={"decision": "approved", "expected_digest": req["digest"]}, headers=H(SF1))
    assert r.json() == {"status": "proposed"}
    r = await c.post(f"/review/v0/map-changes/{CH}/sign", json={"decision": "approved", "expected_digest": req["digest"]}, headers=H(SF2))
    assert r.json() == {"status": "approved"}


async def test_role_check_rejects_extra_app_roles(pg, rc):
    from review_console.store import expect_role, role_facts
    _, kp, _ = rc
    pg.psql("CREATE ROLE rc_multi LOGIN IN ROLE starbuddy_kge, starbuddy_learning; CREATE ROLE rc_bypass LOGIN BYPASSRLS IN ROLE starbuddy_kge;")
    expect_role(await role_facts(kp), "starbuddy_kge")
    for user in ("rc_multi", "rc_bypass"):
        conn = await asyncpg.connect(**pg.dsn(user))
        try:
            with pytest.raises(RuntimeError):
                expect_role(await role_facts(conn), "starbuddy_kge")
        finally:
            await conn.close()


PAGES_FIX = __import__("pathlib").Path(__file__).parent / "fixtures" / "pages-reconcile"
T1 = "5f0e0000-0000-4000-8000-0000000000e1"
T2 = "5f0e0000-0000-4000-8000-0000000000e2"


async def test_pages_entry_and_page_records(pg, rc):
    """冲刺 3：库里的页面审核记录（0038，签字身份经 kge.sign_page_review）+ 合成合并记录 → 页面入口的对账状态与链接；版本记录有页面行。"""
    from review_console.config import PagesSettings
    c, kp, rp = rc
    pg.psql(f"INSERT INTO platform.account (id, kind) VALUES ('{T1}', 'staff'), ('{T2}', 'staff') ON CONFLICT DO NOTHING;")
    recs = __import__("json").loads((PAGES_FIX / "records.json").read_text(encoding="utf-8"))["records"]
    for r in recs:
        async with rp.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.account_id', $1, true)", r["signer"])
            await conn.fetchval("SELECT kge.sign_page_review($1, $2, $3, $4, 'mr_approved')",
                                r["page_id"], r["page_commit"], r["decision"], r["risk_level"])
    # 未配置时页面入口只说“未配置”
    assert (await c.get("/review/v0/pages", headers=H(SF1))).json() == {"configured": False}
    settings = PagesSettings(str(PAGES_FIX / "export.json"), str(PAGES_FIX / "staff-map.json"),
                             "https://git.example.cn/kge/pages/-/merge_requests/{mr_id}")
    app = create_app(authenticate=staff_auth(None), pools=(kp, rp), pages_settings=settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            assert (await client.get("/review/v0/pages")).status_code == 401
            v = (await client.get("/review/v0/pages", headers=H(SF1))).json()
            assert v["configured"] and v["ok"] and v["held_pages"] == []
            assert [(e["page_id"], e["status"]) for e in v["merged"]] == [
                ("MATH.SYN.T.A", "ok"), ("MATH.SYN.T.B", "ok"), ("MATH.SYN.T.C", "ok"), ("MATH.SYN.T.D", "ok")]
            [p] = v["pending"]
            assert p["mr_id"] == "104" and p["state"] == "open" and p["approvals_review"] == 1
            assert p["url"] == "https://git.example.cn/kge/pages/-/merge_requests/104"
            # 删不了审核记录（只追加），用“签一个合并记录里没有的提交”模拟一方有一方没有：
            # 低风险页 D 的最新头提交上没有签字，这条孤儿扣页
            async with rp.acquire() as conn, conn.transaction():
                await conn.execute("SELECT set_config('app.account_id', $1, true)", T2)
                await conn.fetchval("SELECT kge.sign_page_review('MATH.SYN.T.D', $1, 'approved', 'low', 'mr_approved')", "c3" * 20)
            v = (await client.get("/review/v0/pages", headers=H(SF1))).json()
            assert not v["ok"] and v["held_pages"] == ["MATH.SYN.T.D"]
            assert v["orphans"] == [{"page_id": "MATH.SYN.T.D", "page_commit": "c3" * 20, "records": 1, "problems": ["no_merge"],
                                     "blocking": True}]
    # 文件坏了：503，不回显内容
    bad = PagesSettings(str(PAGES_FIX / "missing.json"), str(PAGES_FIX / "staff-map.json"), settings.mr_url_template)
    app = create_app(authenticate=staff_auth(None), pools=(kp, rp), pages_settings=bad)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            r = await client.get("/review/v0/pages", headers=H(SF1))
            assert r.status_code == 503 and r.json()["code"] == "PAGES_EXPORT_UNAVAILABLE"
    # 版本记录（spec 第 11 节）：数学有页面行（页面 ID、提交号、签字人、时间），语文没有
    items = (await c.get("/review/v0/records", params={"subject": "aya"}, headers=H(SF1))).json()["items"]
    page_rows = [i for i in items if i["line"] == "page"]
    assert len(page_rows) == 8 and all(i["page_id"].startswith("MATH.") and i["decided_at"] for i in page_rows)
    assert {(i["page_id"], i["page_commit"], i["signer"]) for i in page_rows if i["page_id"] == "MATH.SYN.T.A"} == {
        ("MATH.SYN.T.A", "a1" * 20, T1), ("MATH.SYN.T.A", "a1" * 20, T2)}
    items = no_content((await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"])
    assert all(i["line"] == "map" for i in items)


async def test_reconcile_cli_reads_db(pg, rc, tmp_path):
    """命令行 --db：只用 kge 连接（先核对只继承 starbuddy_kge），读 kge.page_review_record 与文件对账。"""
    import os
    import subprocess
    import sys
    dsn = pg.dsn("review_kge_app")
    url = f"postgresql://{dsn['user']}@/{dsn['database']}?host={dsn['host']}"
    out = tmp_path / "report.json"
    p = subprocess.run([sys.executable, "-m", "review_console", "reconcile-pages", "--export", str(PAGES_FIX / "export.json"),
                        "--staff-map", str(PAGES_FIX / "staff-map.json"), "--db", "--out", str(out)],
                       cwd=__import__("pathlib").Path(__file__).resolve().parents[1], capture_output=True, text=True,
                       env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "REVIEW_CONSOLE_ENV": "test",
                            "REVIEW_CONSOLE_KGE_DATABASE_URL": url, "PYTHONDONTWRITEBYTECODE": "1"})
    report = __import__("json").loads(out.read_text(encoding="utf-8"))
    # 上一个用例多签了一个合并记录里没有的提交：对账失败，退出码 1
    assert p.returncode == 1, p.stderr
    assert report["held_pages"] == ["MATH.SYN.T.D"]


def test_db_error_detail_only_for_raised_rules():
    from review_console.store import db_error
    raised = asyncpg.exceptions.CheckViolationError("结构性变更需要两名不同 staff")
    assert db_error(raised).detail == "结构性变更需要两名不同 staff"
    native = asyncpg.exceptions.CheckViolationError('new row for relation "knowledge_node" violates check constraint "x"')
    native.constraint_name, native.table_name = "x", "knowledge_node"
    assert db_error(native).status == 409 and db_error(native).detail is None
    denied = asyncpg.exceptions.InsufficientPrivilegeError("permission denied for function publish_map_version")
    assert db_error(denied).status == 403 and db_error(denied).detail is None


# 考情政策线（冲刺 4，迁移 0041）：合成城市 synthcity 的 2030 届合成方案（非真实数据，docs/design/policy/data-entry-rules.md 第 6 节）
PDOC = uuid.UUID("c0500000-0000-4000-8000-0000000000d5")
PSCH = uuid.UUID("c0600000-0000-4000-8000-0000000000e6")


async def test_records_merge_policy_signoffs(pg, rc):
    """版本记录导出从两处合并（spec 第 8 节）：地图与页面行来自 kge，考情行来自 platform.policy_signoff_record（签字人表在 platform，
    复核人不在 kge.content_review 里另记）。两名 staff 经签字身份签来源文件（第二名批准时发布）与方案，再由签字身份发布方案。"""
    c, kp, rp = rc
    pg.psql(f"""
      INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.synthcity.example', '合成教育局', 'staff-001') ON CONFLICT DO NOTHING;
      SET ROLE starbuddy_kge;
      INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
        VALUES ('{PDOC}', 'synthcity', '合成教育局', '合成〔2029〕7号', '合成 2030 届方案（非真实数据）', '2029-03-01',
                'https://jyj.synthcity.example/doc/rc', 2030, '2029-09-01');
      INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES ('{PSCH}', 'synthcity', 2030, '1.0.0', '{PDOC}');
      INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
        VALUES ('{PSCH}', 'chinese', true, 100, 'city', 'closed_book', 9);
      INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) VALUES ('{PSCH}', 'batch_1', 1, 'unified');""")

    async def as_staff(staff: uuid.UUID, sql: str, *args):
        async with rp.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.account_id', $1, true)", str(staff))
            return await conn.fetchval(sql, *args)

    sign_doc = "SELECT policy.sign_record('source_document', $1, 'approved', policy.object_digest('source_document', $1))"
    sign_scheme = "SELECT policy.sign_scheme($1, 'approved', policy.object_digest('exam_scheme', $1))"
    assert await as_staff(SF1, sign_doc, PDOC) == "draft"
    assert await as_staff(SF2, sign_doc, PDOC) == "published"
    assert await as_staff(SF1, sign_scheme, PSCH) == 1
    assert await as_staff(SF2, sign_scheme, PSCH) == 2
    # 知识服务（kge 连接）不能发布方案，只有签字身份能
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await kp.fetchval("SELECT policy.publish_scheme($1)", PSCH)
    assert await as_staff(SF1, "SELECT policy.publish_scheme($1)", PSCH) is not None

    items = no_content((await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"])
    pol = [i for i in items if i["line"] == "policy"]
    assert [(i["policy_object"], i["action"], i["signer"]) for i in pol] == [
        ("source_document", "review", str(SF1)), ("source_document", "review", str(SF2)), ("source_document", "publish", str(SF2)),
        ("exam_scheme", "review", str(SF1)), ("exam_scheme", "review", str(SF2)), ("exam_scheme", "publish", str(SF1))]
    last = pol[-1]
    assert last["policy_object_id"] == str(PSCH) and last["city"] == "synthcity" and last["policy_version"] == "1.0.0" and last["cohort"] == 2030
    # 考情行的 version 一律为空（只给地图版本号），方案版本号在末尾的 policy_version；来源文件行没有方案版本
    assert all(i["version"] is None for i in pol) and pol[0]["policy_version"] is None
    assert all(i["policy_version"] is None for i in items if i["line"] != "policy")
    assert all(i["risk_level"] == "high" and i["decision"] == "approved" and len(i["digest"]) == 64 and i["map_version_id"] is None for i in pol)
    # 已有的地图行不变，考情行排在后面（按时间）；考情不按学科，每个学科的导出都带
    assert [i["line"] for i in items[:7]] == ["map"] * 7 and all(i["policy_object"] is None for i in items if i["line"] == "map")
    aya = (await c.get("/review/v0/records", params={"subject": "aya"}, headers=H(SF1))).json()["items"]
    assert [i for i in aya if i["line"] == "policy"] == pol
    # CSV：新列只追加在末尾，已有列顺序不变；两次导出逐字节相同
    from review_console.service import version_record
    from review_console.store import KgeStore
    rows = no_content(await version_record(KgeStore(kp), "wenli"))
    a, b = io.StringIO(), io.StringIO()
    write_records(rows, "csv", a)
    write_records(rows, "csv", b)
    assert a.getvalue() == b.getvalue()
    assert a.getvalue().splitlines()[0] == ("line,map_version_id,version,action,decision,risk_level,basis_code,digest,decided_at,signer,"
                                            "rubric_versions,validation_errors,validation_warnings,page_id,page_commit,"
                                            "policy_object,policy_object_id,city,cohort,policy_version,"
                                            "content_version_id,six_segment,content_map_version_id,item_id,from_admission,to_admission,"
                                            "admission_review_level")
    assert sum(1 for line in a.getvalue().splitlines() if line.startswith("policy,")) == 6


# ---------- 冲刺 4 · PR D：内容版本、准入、考情差异与签字、页面签字与事后抽检、时政下架、版本记录 ----------
# 合成语文内容版本（地区 synth-rc、教材 syn-rc；语文没有地图骨架，map_version_id 可空）与合成数学草稿（检查“缺地图”的提示与拒绝）
CV1 = uuid.UUID("c0700000-0000-4000-8000-0000000000f1")
CV2 = uuid.UUID("c0700000-0000-4000-8000-0000000000f2")
CVA = uuid.UUID("c0700000-0000-4000-8000-0000000000f3")


async def test_content_version_review_flow(pg, rc):
    c, kp, _ = rc
    pg.psql(f"""SET ROLE starbuddy_learning;
      INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
        VALUES ('{CV1}', 'synth-rc', 2031, 8, 'syn-rc', 'wenli', '1.0.0'), ('{CV2}', 'synth-rc', 2031, 8, 'syn-rc', 'wenli', '1.1.0'),
               ('{CVA}', 'synth-rc', 2031, 8, 'syn-rc', 'aya', '0.1.0');
      INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES
        ('wenli', 'SYN-RC-01', '{CV1}', 'syn-rc-01'), ('wenli', 'SYN-RC-11', '{CV2}', 'syn-rc-11');
      INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, source_ref, source_type) VALUES
        ('wenli', 'SYN-RC-I1', '{CV1}', 'SYN-RC-01', 'pending_review', 'SYN-SRC-RC-1', 'textbook'),
        ('wenli', 'SYN-RC-I11', '{CV2}', 'SYN-RC-11', 'source_unverified', NULL, 'unknown');""")
    lst = (await c.get("/review/v0/content-versions", params={"subject": "wenli"}, headers=H(SF1))).json()["items"]
    lst = [i for i in lst if i["region"] == "synth-rc"]  # 会话共用的库里还有端到端测试登记的语文内容版本
    assert [(i["content_version"], i["status"], i["six_segment"]) for i in lst] == [
        ("1.0.0", "draft", "synth-rc/2031/8/syn-rc/wenli/1.0.0"), ("1.1.0", "draft", "synth-rc/2031/8/syn-rc/wenli/1.1.0")]
    d = (await c.get(f"/review/v0/content-versions/{CV1}/diff", headers=H(SF1))).json()
    assert d["base_version"] is None and {ch["op"] for ch in d["changes"]} == {"added"}
    assert d["summary"]["required_signers"] == 1 and d["summary"]["latest_decision"] is None and not d["summary"]["ready"]
    # 政策库里没有 synth-rc 2031 届的已发布方案：只记警告（spec 第 7 节）
    assert [x["check_name"] for x in d["checks"]] == ["no_published_scheme"] and d["checks"][0]["severity"] == "warning"
    # 看到的就是签的：摘要不符被拒；正确摘要签字、发布
    r = await c.post(f"/review/v0/content-versions/{CV1}/sign", json={"decision": "approved", "expected_digest": ZERO}, headers=H(SF1))
    assert r.status_code == 409 and "摘要不符" in r.json()["detail"]
    r = await c.post(f"/review/v0/content-versions/{CV1}/publish", json={"expected_digest": d["digest"]}, headers=H(SF1))
    assert r.status_code == 409 and "没有批准" in r.json()["detail"]
    assert (await c.post(f"/review/v0/content-versions/{CV1}/sign", json={"decision": "approved", "expected_digest": d["digest"]},
                         headers=H(SF1))).status_code == 200
    d = (await c.get(f"/review/v0/content-versions/{CV1}/diff", headers=H(SF1))).json()
    assert d["summary"]["ready"] and d["summary"]["latest_decision"] == "approved"
    r = await c.post(f"/review/v0/content-versions/{CV1}/publish", json={"expected_digest": d["digest"]}, headers=H(PARENT))
    assert r.status_code == 403
    assert (await c.post(f"/review/v0/content-versions/{CV1}/publish", json={"expected_digest": d["digest"]}, headers=H(SF1))).status_code == 200
    # 准入：签字（看到的题目登记摘要）→ 待执行 → 执行，返回学习服务按题重算的起点
    item = next(i for i in d["items"] if i["item_id"] == "SYN-RC-I1")
    body = {"subject": "wenli", "item_id": "SYN-RC-I1", "to_admission": "admitted", "decision": "approved", "review_level": "Q3",
            "source_ref": "SYN-SRC-RC-1", "source_type": "textbook", "license_status": "cleared", "expected_digest": item["admission_digest"]}
    r = await c.post("/review/v0/admission/sign", json={**body, "review_level": "Q1"}, headers=H(SF2))
    assert r.status_code == 409 and "Q2" in r.json()["detail"]
    rid = (await c.post("/review/v0/admission/sign", json=body, headers=H(SF2))).json()["review_id"]
    d = (await c.get(f"/review/v0/content-versions/{CV1}/diff", headers=H(SF1))).json()
    assert [p["id"] for p in d["pending_admissions"]] == [rid]
    r = (await c.post("/review/v0/admission/apply", json={"review_ids": [rid]}, headers=H(SF1))).json()
    assert r["applied"] == 1 and r["recompute"].startswith("python -m learning.jobs admission-recompute --since ")
    log = pg.psql(f"SELECT count(*) FROM platform.admission_change_log('{r['recompute_since']}') WHERE item_id = 'SYN-RC-I1'").strip()
    assert log == "1"  # 学习服务从 recompute_since 起能读到这次执行
    assert (await c.post("/review/v0/admission/apply", json={"review_ids": [rid]}, headers=H(SF1))).status_code == 409  # 执行过了
    d = (await c.get(f"/review/v0/content-versions/{CV1}/diff", headers=H(SF1))).json()
    assert d["pending_admissions"] == [] and next(i for i in d["items"] if i["item_id"] == "SYN-RC-I1")["admission"] == "admitted"
    # 1.1.0：基准是 1.0.0（已发布）；能力点与题目主键全局唯一，新版本登记表现为新增、旧版本登记表现为删除
    d2 = (await c.get(f"/review/v0/content-versions/{CV2}/diff", headers=H(SF1))).json()
    assert d2["base_version"]["content_version"] == "1.0.0"
    got = {(ch["object"], ch["key"], ch["op"]) for ch in d2["changes"]}
    assert {("item", "SYN-RC-I1", "removed"), ("item", "SYN-RC-I11", "added"), ("skill_node", "SYN-RC-11", "added")} <= got
    assert all(ch["risk"]["level"] == "high" for ch in d2["changes"])
    await c.post(f"/review/v0/content-versions/{CV2}/sign", json={"decision": "approved", "expected_digest": d2["digest"]}, headers=H(SF2))
    assert (await c.post(f"/review/v0/content-versions/{CV2}/publish", json={"expected_digest": d2["digest"]}, headers=H(SF2))).status_code == 200
    r = await c.post(f"/review/v0/content-versions/{CV1}/retire", json={"basis_code": "withdrawn"}, headers=H(SF1))
    assert r.json() == {"status": "retired"}
    # 数学没有地图版本：差异视图提前提示，发布被库拒绝
    da = (await c.get(f"/review/v0/content-versions/{CVA}/diff", headers=H(SF1))).json()
    assert "map_version_missing" in [x["check_name"] for x in da["checks"]]
    await c.post(f"/review/v0/content-versions/{CVA}/sign", json={"decision": "approved", "expected_digest": da["digest"]}, headers=H(SF1))
    r = await c.post(f"/review/v0/content-versions/{CVA}/publish", json={"expected_digest": da["digest"]}, headers=H(SF1))
    assert r.status_code == 409 and "地图版本" in r.json()["detail"]
    # 版本记录：内容版本行与准入行（六段标识、签字人、摘要；执行行带审核等级与准入变化），已有的地图行不变
    items = (await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"]
    cv_rows = [i for i in items if i["line"] in CONTENT_LINES and i["six_segment"].startswith("synth-rc/")]
    assert [(i["line"], i["action"], i["six_segment"].rsplit("/", 1)[1]) for i in cv_rows] == [
        ("content_version", "review", "1.0.0"), ("content_version", "publish", "1.0.0"), ("admission", "review", "1.0.0"),
        ("admission", "apply", "1.0.0"), ("content_version", "review", "1.1.0"), ("content_version", "publish", "1.1.0"),
        ("content_version", "retire", "1.0.0")]
    apply_row = cv_rows[3]
    assert (apply_row["item_id"], apply_row["from_admission"], apply_row["to_admission"], apply_row["admission_review_level"], apply_row["signer"]) == \
        ("SYN-RC-I1", "pending_review", "admitted", "Q3", str(SF1))
    assert all(i["map_version_id"] is None and i["version"] is None and i["risk_level"] == "high" for i in cv_rows)
    assert cv_rows[1]["six_segment"] == "synth-rc/2031/8/syn-rc/wenli/1.0.0" and len(cv_rows[1]["digest"]) == 64
    assert all(i["line"] in ("map", "policy", "content_version", "admission") for i in items)


PSCH2 = uuid.UUID("c0600000-0000-4000-8000-0000000000e7")
PSCH3 = uuid.UUID("c0600000-0000-4000-8000-0000000000e8")
PDOC2 = uuid.UUID("c0500000-0000-4000-8000-0000000000d6")
PSCHOOL = uuid.UUID("c0800000-0000-4000-8000-0000000000a8")


async def test_policy_scheme_diff_sign_publish(pg, rc):
    """考情差异（spec 第 8 节）：与同城同届上一版本（1.0.0，前一个用例发布）、与上一届已发布方案比较；validate_scheme 逐条；两名 staff 签字后发布。"""
    c, _, _ = rc
    pg.psql(f"""SET ROLE starbuddy_kge;
      INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES
        ('{PSCH2}', 'synthcity', 2030, '1.1.0', '{PDOC}'), ('{PSCH3}', 'synthcity', 2031, '1.0.0', '{PDOC}');
      INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade) VALUES
        ('{PSCH2}', 'chinese', true, 120, 'city', 'closed_book', 9), ('{PSCH2}', 'math', true, 120, 'city', 'closed_book', 9),
        ('{PSCH3}', 'chinese', true, 120, 'city', 'closed_book', 9);
      INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) VALUES
        ('{PSCH2}', 'batch_1', 1, 'unified'), ('{PSCH2}', 'batch_2', 2, 'unified'), ('{PSCH3}', 'batch_1', 1, 'unified');""")
    lst = (await c.get("/review/v0/policy", params={"city": "synthcity"}, headers=H(SF1))).json()
    assert [(s["cohort"], s["version"], s["status"]) for s in lst["schemes"]] == [
        (2030, "1.0.0", "published"), (2030, "1.1.0", "draft"), (2031, "1.0.0", "draft")]
    assert (await c.get("/review/v0/policy", params={"city": "Bad City"}, headers=H(SF1))).status_code == 422
    v = (await c.get(f"/review/v0/policy/schemes/{PSCH2}", headers=H(SF1))).json()
    assert v["previous_version"]["version"] == "1.0.0" and v["previous_cohort"] is None
    got = {(x["object"], x["key"], x["field"], x["op"]) for x in v["diff_previous_version"]}
    assert {("subject", "chinese", "full_score", "changed"), ("subject", "math", None, "added"), ("batch", "batch_2|unified", None, "added"),
            ("total", "total", "total", "changed")} <= got
    assert all(x["risk"]["level"] == "high" for x in v["diff_previous_version"])
    assert v["summary"]["required_signers"] == 2 and v["summary"]["approvals"] == 0
    assert [x["check_name"] for x in v["validation"] if x["severity"] == "error"] == ["reviewers"]
    assert any(x["check_name"] == "missing_transition" for x in v["validation"]) is False  # 上一届（2029）没有已发布方案
    r = await c.post(f"/review/v0/policy/schemes/{PSCH2}/publish", json={"expected_digest": v["digest"]}, headers=H(SF1))
    assert r.status_code == 409  # 签字不足两人
    assert (await c.post(f"/review/v0/policy/schemes/{PSCH2}/sign", json={"decision": "approved", "expected_digest": v["digest"]},
                         headers=H(SF1))).json() == {"approvals": 1}
    r = await c.post(f"/review/v0/policy/schemes/{PSCH2}/sign", json={"decision": "approved", "expected_digest": v["digest"]}, headers=H(SF1))
    assert r.status_code == 409 and r.json()["code"] == "CONFLICT"  # 同一账号同一内容只能签一次
    assert (await c.post(f"/review/v0/policy/schemes/{PSCH2}/sign", json={"decision": "approved", "expected_digest": v["digest"]},
                         headers=H(SF2))).json() == {"approvals": 2}
    assert (await c.post(f"/review/v0/policy/schemes/{PSCH2}/publish", json={"expected_digest": v["digest"]}, headers=H(SF2))).status_code == 200
    # 2031 届：与上一届（2030）已发布方案（现在是 1.1.0）比较
    v3 = (await c.get(f"/review/v0/policy/schemes/{PSCH3}", headers=H(SF1))).json()
    assert v3["previous_version"] is None and v3["previous_cohort"]["version"] == "1.1.0"
    got3 = {(x["object"], x["key"], x["op"]) for x in v3["diff_previous_cohort"]}
    assert ("subject", "math", "removed") in got3 and ("batch", "batch_2|unified", "removed") in got3
    assert any(x["check_name"] == "missing_transition" for x in v3["validation"])  # 计分科目变了却没有过渡规则（警告）


async def test_policy_record_second_signer_publish_failure_is_explained(pg, rc):
    """来源文件、招录行、学校在第二名批准时发布；发布被表上的保护触发器拒绝时整笔回滚、签字不留痕——审核台给出明确提示（PR C 待处理）。"""
    c, _, _ = rc
    pg.psql(f"""SET ROLE starbuddy_kge;
      INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
        VALUES ('{PDOC2}', 'synthcity', '合成教育局', '合成〔2029〕8号', '合成学校名录（非真实数据）', '2029-03-01',
                'https://jyj.synthcity.example/doc/rc2', 2030, '2029-09-01');""")
    drafts = (await c.get("/review/v0/policy", params={"city": "synthcity"}, headers=H(SF1))).json()["drafts"]
    doc = next(x for x in drafts if x["id"] == str(PDOC2))
    for staff, status in ((SF1, "draft"), (SF2, "published")):
        r = await c.post(f"/review/v0/policy/records/{PDOC2}/sign",
                         json={"object_type": "source_document", "decision": "approved", "expected_digest": doc["digest"]}, headers=H(staff))
        assert r.json() == {"status": status}
    pg.psql(f"""SET ROLE starbuddy_kge;
      INSERT INTO policy.school (id, city, code, public_name, level_code, source_document_id)
        VALUES ('{PSCHOOL}', 'synthcity', 'synth-hs-rc', '合成高中（非真实）', 'level_a', '{PDOC2}');""")
    drafts = (await c.get("/review/v0/policy", params={"city": "synthcity"}, headers=H(SF1))).json()["drafts"]
    school = next(x for x in drafts if x["id"] == str(PSCHOOL))
    assert school["object_type"] == "school" and school["approvals"] == 0
    sign = {"object_type": "school", "decision": "approved", "expected_digest": school["digest"]}
    assert (await c.post(f"/review/v0/policy/records/{PSCHOOL}/sign", json=sign, headers=H(SF1))).json() == {"status": "draft"}
    # 来源文件被写入方撤下（撤下不收口，PR C 差异 #3）：第二名批准时发布被学校的保护触发器拒绝
    pg.psql(f"SET ROLE starbuddy_kge; UPDATE policy.source_document SET status = 'retired' WHERE id = '{PDOC2}';")
    r = await c.post(f"/review/v0/policy/records/{PSCHOOL}/sign", json=sign, headers=H(SF2))
    assert r.status_code == 409 and r.json()["code"] == "PUBLISH_FAILED_UNSIGNED"
    assert "签字没有保存" in r.json()["detail"] and "来源文件" in r.json()["detail"]
    # 确实没留痕：有效批准人数仍是 1，第二名的签字不在导出里
    drafts = (await c.get("/review/v0/policy", params={"city": "synthcity"}, headers=H(SF1))).json()["drafts"]
    assert next(x for x in drafts if x["id"] == str(PSCHOOL))["approvals"] == 1
    # 同一账号对同一内容再签（重复签字）照常报原错误，不套这个提示
    r = await c.post(f"/review/v0/policy/records/{PSCHOOL}/sign", json=sign, headers=H(SF1))
    assert r.status_code == 409 and r.json()["code"] == "CONFLICT"


async def test_page_sign_and_spot_check(pg, rc):
    """页面签字入口（PR A 待人确认 1 默认：审核人在审核台逐个签）与低风险事后抽检（固定种子，结果记审核记录，依据 spot_check）。"""
    import datetime as dt
    from review_console.config import PagesSettings
    from review_console.spot_check import SpotCheckSettings
    _, kp, rp = rc
    settings = PagesSettings(str(PAGES_FIX / "export.json"), str(PAGES_FIX / "staff-map.json"),
                             "https://git.example.cn/kge/pages/-/merge_requests/{mr_id}")
    app = create_app(authenticate=staff_auth(None), pools=(kp, rp), pages_settings=settings,
                     spot_settings=SpotCheckSettings(ratio=1.0, seed="syn-seed"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            # 页面签字：只能签导出里某个合并请求的头提交，风险取合并请求标注
            r = await client.post("/review/v0/pages/sign", json={"page_id": "MATH.SYN.T.E", "page_commit": "a4" * 20, "decision": "approved"},
                                  headers=H(SF1))
            assert r.status_code == 200 and r.json()["risk_level"] == "high"
            r = await client.post("/review/v0/pages/sign", json={"page_id": "MATH.SYN.T.E", "page_commit": "a4" * 20, "decision": "approved"},
                                  headers=H(SF1))
            assert r.status_code == 409  # 同一账号同一页面同一提交只签一次
            r = await client.post("/review/v0/pages/sign", json={"page_id": "MATH.SYN.T.E", "page_commit": "d0" * 20, "decision": "approved"},
                                  headers=H(SF1))
            assert r.status_code == 404
            v = (await client.get("/review/v0/pages", headers=H(SF1))).json()
            assert next(e for e in v["pending"] if e["page_id"] == "MATH.SYN.T.E")["approvals_review"] == 2
            # 抽检：2026-10-02（北京时间）低风险合并只有页面 D；比例 1.0 全抽
            s = (await client.get("/review/v0/spot-checks", params={"day": "2026-10-02"}, headers=H(SF1))).json()
            assert s["configured"] and s["candidates"] == 1 and [x["page_id"] for x in s["sampled"]] == ["MATH.SYN.T.D"]
            assert s["sampled"][0]["checks"] == [] and s["basis_code"] == "spot_check"
            future = (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date() + dt.timedelta(days=2)).isoformat()
            assert (await client.get("/review/v0/spot-checks", params={"day": future}, headers=H(SF1))).status_code == 422
            # 不能借抽检给高风险页面补签
            r = await client.post("/review/v0/spot-checks/sign", json={"page_id": "MATH.SYN.T.A", "page_commit": "a1" * 20,
                                                                      "decision": "approved"}, headers=H(SF2))
            assert r.status_code == 409
            r = await client.post("/review/v0/spot-checks/sign", json={"page_id": "MATH.SYN.T.D", "page_commit": "a3" * 20,
                                                                      "decision": "rejected"}, headers=H(SF2))
            assert r.status_code == 200
            s = (await client.get("/review/v0/spot-checks", params={"day": "2026-10-02"}, headers=H(SF1))).json()
            assert [(x["decision"], x["signer"]) for x in s["sampled"][0]["checks"]] == [("rejected", str(SF2))]
            # 抽检驳回 → 对账扣页（spot_check_rejected）
            v = (await client.get("/review/v0/pages", headers=H(SF1))).json()
            d = next(e for e in v["merged"] if e["page_id"] == "MATH.SYN.T.D")
            assert "spot_check_rejected" in d["problems"] and "MATH.SYN.T.D" in v["held_pages"]
    rows = await kp.fetch("SELECT basis_code, risk_level, decision FROM kge.page_review_record('aya') WHERE page_id = 'MATH.SYN.T.D'"
                          " AND page_commit = $1", "a3" * 20)
    assert [tuple(r) for r in rows] == [("spot_check", "low", "rejected")]


async def test_page_sign_rejects_author_and_unknown_author(pg, rc, tmp_path):
    """提交方不能自签（spec 设计要点 3）：合并请求作者经 staff 对应表映射，等于签字人 → 409 SELF_SIGN（页面签字与抽检都是）；
    导出里没有作者、或作者不在对应表 → 409 AUTHOR_UNKNOWN（判不出从严）。抽检种子没配置 → 503。全部被拒的请求都不留审核记录。"""
    import json
    from review_console.config import PagesSettings
    from review_console.spot_check import SpotCheckSettings
    _, kp, rp = rc
    staff = json.loads((PAGES_FIX / "staff-map.json").read_text(encoding="utf-8"))
    staff["users"]["syn-author-2"] = str(SF1)  # MR 103（页面 D，低风险已合并）与 104（页面 E，待审）的作者是 SF1
    (tmp_path / "staff-map.json").write_text(json.dumps(staff), encoding="utf-8")
    export = json.loads((PAGES_FIX / "export.json").read_text(encoding="utf-8"))
    for m in export["merge_requests"]:
        if m["mr_id"] == "102":  # MR 102（页面 B、C）没有作者
            del m["author"]
    (tmp_path / "export.json").write_text(json.dumps(export), encoding="utf-8")
    settings = PagesSettings(str(tmp_path / "export.json"), str(tmp_path / "staff-map.json"),
                             "https://git.example.cn/kge/pages/-/merge_requests/{mr_id}")
    before = await kp.fetchval("SELECT count(*) FROM kge.page_review_record(NULL)")

    async def post(app, path, page, commit, who):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            return await client.post(path, json={"page_id": page, "page_commit": commit, "decision": "approved"}, headers=H(who))

    app = create_app(authenticate=staff_auth(None), pools=(kp, rp), pages_settings=settings,
                     spot_settings=SpotCheckSettings(ratio=1.0, seed="syn-seed"))
    async with app.router.lifespan_context(app):
        r = await post(app, "/review/v0/pages/sign", "MATH.SYN.T.E", "a4" * 20, SF1)
        assert (r.status_code, r.json()["code"]) == (409, "SELF_SIGN")
        r = await post(app, "/review/v0/spot-checks/sign", "MATH.SYN.T.D", "a3" * 20, SF1)
        assert (r.status_code, r.json()["code"]) == (409, "SELF_SIGN")
        r = await post(app, "/review/v0/pages/sign", "MATH.SYN.T.B", "a2" * 20, SF2)
        assert (r.status_code, r.json()["code"]) == (409, "AUTHOR_UNKNOWN")
        staff["users"].pop("syn-author-1")  # MR 101 的作者不在对应表里
        (tmp_path / "staff-map.json").write_text(json.dumps(staff), encoding="utf-8")
        r = await post(app, "/review/v0/pages/sign", "MATH.SYN.T.A", "a1" * 20, SF2)
        assert (r.status_code, r.json()["code"]) == (409, "AUTHOR_UNKNOWN")
    assert await kp.fetchval("SELECT count(*) FROM kge.page_review_record(NULL)") == before
    # 抽检种子没配置：接口报错，不用任何缺省种子
    app = create_app(authenticate=staff_auth(None), pools=(kp, rp), pages_settings=settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            r = await client.get("/review/v0/spot-checks", params={"day": "2026-10-02"}, headers=H(SF1))
            assert (r.status_code, r.json()["code"]) == (503, "SPOT_CHECK_UNCONFIGURED")


CA_MAP = uuid.UUID("c0900000-0000-4000-8000-0000000000b1")
CA_OLD = uuid.UUID("c0900000-0000-4000-8000-0000000000b2")
CA_NEW = uuid.UUID("c0900000-0000-4000-8000-0000000000b3")


async def test_expire_affairs_cli(pg, rc):
    """时政过期下架（spec 第 9 节，迁移 0043）：命令行只用 kge 连接、只连本机；过期且已上线的改为 retired 并有一条系统动作审核记录。"""
    import os
    import subprocess
    import sys
    from review_console.__main__ import local_only
    pg.psql(version_sql(CA_MAP, "9.0.0").replace("'wenli', 2022", "'wenli', 2022") + f"""
      INSERT INTO kge.current_affair (id, map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES
        ('{CA_OLD}', '{CA_MAP}', '{{CHN.RC.T.A}}', '合成来源：已过期', '2019-01-01', '2019-03-01', 'published'),
        ('{CA_NEW}', '{CA_MAP}', '{{CHN.RC.T.A}}', '合成来源：仍有效', '2019-01-01', '2100-01-01', 'published');""")
    dsn = pg.dsn("review_kge_app")
    url = f"postgresql://{dsn['user']}@/{dsn['database']}?host={dsn['host']}"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "REVIEW_CONSOLE_ENV": "test", "REVIEW_CONSOLE_KGE_DATABASE_URL": url,
           "PYTHONDONTWRITEBYTECODE": "1"}
    cwd = __import__("pathlib").Path(__file__).resolve().parents[1]
    run = lambda *a: subprocess.run([sys.executable, "-m", "review_console", "expire-affairs", *a], cwd=cwd, capture_output=True, text=True,
                                    env=env)
    p = run("--today", "2019-06-01")
    assert p.returncode == 0, p.stderr
    assert __import__("json").loads(p.stdout) == {"job": "expire-affairs", "retired": 1, "today": "2019-06-01"}
    assert pg.psql(f"SELECT review_status FROM kge.current_affair WHERE id IN ('{CA_OLD}', '{CA_NEW}') ORDER BY id").split() == [
        "retired", "published"]
    assert pg.psql(f"SELECT basis_code || ',' || action || ',' || risk_level FROM kge.content_review WHERE object_id = '{CA_OLD}'").strip() == \
        "expired_auto,retire,low"
    assert run("--today", "2999-01-01").returncode == 2           # 不能提前下架（库函数拒绝）
    assert run("--today", "not-a-date").returncode == 2
    # 只连本机：连接串里出现的全部主机都要是本机（多主机写法、host 查询参数、能改道的参数都算）
    for bad in ("postgresql://u@db.example.cn/x", "postgresql://u@10.0.0.5/x", "mysql://u@localhost/x",
                "postgresql://u@/x?host=db.example.cn",
                "postgresql://u@127.0.0.1:5432,db.example.cn:5432/x",       # 多主机：第二个不是本机
                "postgresql://u@localhost,10.0.0.5/x",
                "postgresql://u@db.example.cn:5432,localhost:5432/x",       # 多主机：第一个不是本机
                "postgresql://u@localhost/x?host=db.example.cn",            # asyncpg 会忽略 host 参数，但照样拒
                "postgresql://u@/x?host=/tmp,db.example.cn",                # host 参数里的多主机
                "postgresql://u@/x?host=/tmp&host=db.example.cn",           # host 参数出现两次
                "postgresql://u@/x?host=%2Ftmp%2Cdb.example.cn",            # 编码过的逗号
                "postgresql://u@localhost/x?hostaddr=10.0.0.5",             # hostaddr 可以绕开主机名
                "postgresql://u@localhost/x?service=synthetic-remote",      # 服务文件里可以写别的主机
                "postgresql://u@[2001:db8::1]:5432/x",
                "postgresql://u@127.0.0.1.db.example.cn/x",
                "postgresql://u@localhost.db.example.cn/x"):
        with pytest.raises(SystemExit):
            local_only(bad)
    assert local_only(url) == url and local_only("postgresql://u@localhost:5432/x")
    assert local_only("postgresql://u@[::1]:5432/x") and local_only("postgresql://u@127.0.0.2/x")
    assert local_only("postgresql://u@localhost:5432,127.0.0.1:5433/x")
    # 连上之后再核对服务端地址：Unix socket（空）与回环地址放行，别的中止
    from review_console.__main__ import check_server_local

    class FakeConn:
        def __init__(self, addr):
            self.addr = addr

        async def fetchval(self, sql):
            assert "inet_server_addr()" in sql
            return self.addr
    await check_server_local(FakeConn(None))
    await check_server_local(FakeConn("127.0.0.1"))
    await check_server_local(FakeConn("::1"))
    for addr in ("10.0.0.5", "192.0.2.7", "2001:db8::1"):
        with pytest.raises(SystemExit):
            await check_server_local(FakeConn(addr))
    async with rc[1].acquire() as conn:  # 真实的本机测试库（Unix socket）：服务端地址为空，放行
        await check_server_local(conn)
    env2 = {**env, "REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://u@db.example.cn/x"}
    p = subprocess.run([sys.executable, "-m", "review_console", "expire-affairs"], cwd=cwd, capture_output=True, text=True, env=env2)
    assert p.returncode != 0 and "只连本机" in p.stderr


# ---------- 考情 v0.9 待定事项 PR2（specs/policy-decisions-v0.9.1）：控制线签字（迁移 0047）、官方来源清单（迁移 0048） ----------
# 合成城市 synth-cl 的 2032 届合成方案（非真实数据）：一条指标生招录规则设控制线（control_line_basis = total_score）
CL_DOC = uuid.UUID("c0a00000-0000-4000-8000-0000000000d1")
CL_SCH = uuid.UUID("c0a00000-0000-4000-8000-0000000000e1")
CL_LINE = uuid.UUID("c0a00000-0000-4000-8000-0000000000f1")


async def test_control_line_sign_publish_and_records(pg, rc):
    """控制线草稿出现在待签列表（带摘要与批准人数）；两名不同 staff 经审核台签字，第二名批准时发布；版本记录导出有控制线行。"""
    c, _, rp = rc
    pg.psql(f"""
      INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.synthcity.example', '合成教育局', 'staff-001') ON CONFLICT DO NOTHING;
      SET ROLE starbuddy_kge;
      INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
        VALUES ('{CL_DOC}', 'synth-cl', '合成教育局', '合成〔2031〕1号', '合成 2032 届方案（非真实数据）', '2031-03-01',
                'https://jyj.synthcity.example/doc/cl', 2032, '2031-09-01');
      INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES ('{CL_SCH}', 'synth-cl', 2032, '1.0.0', '{CL_DOC}');
      INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
        VALUES ('{CL_SCH}', 'chinese', true, 120, 'city', 'closed_book', 9);
      INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track, control_line_basis) VALUES
        ('{CL_SCH}', 'batch_1', 1, 'unified', 'none'), ('{CL_SCH}', 'batch_1', 1, 'quota_allocation', 'total_score');""")

    async def as_staff(staff: uuid.UUID, sql: str, *args):
        async with rp.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.account_id', $1, true)", str(staff))
            return await conn.fetchval(sql, *args)
    for s in (SF1, SF2):
        await as_staff(s, "SELECT policy.sign_record('source_document', $1, 'approved', policy.object_digest('source_document', $1))", CL_DOC)
        await as_staff(s, "SELECT policy.sign_scheme($1, 'approved', policy.object_digest('exam_scheme', $1))", CL_SCH)
    await as_staff(SF1, "SELECT policy.publish_scheme($1)", CL_SCH)
    pg.psql(f"""SET ROLE starbuddy_kge;
      INSERT INTO policy.control_line (id, city, year, batch_code, score, source_document_id) VALUES ('{CL_LINE}', 'synth-cl', 2032, 'batch_1', 96.5, '{CL_DOC}');""")
    drafts = (await c.get("/review/v0/policy", params={"city": "synth-cl"}, headers=H(SF1))).json()["drafts"]
    line = next(x for x in drafts if x["id"] == str(CL_LINE))
    assert (line["object_type"], line["cohort"], line["label"], line["approvals"]) == ("control_line", 2032, "batch_1 · quota_allocation · 96.5", 0)
    sign = {"object_type": "control_line", "decision": "approved", "expected_digest": line["digest"]}
    # 对象类型与 ID 对不上（按学校算不出这份摘要）、摘要不对：都是 409 DIGEST_CHANGED，不签
    for bad in ({**sign, "object_type": "school"}, {**sign, "expected_digest": ZERO}):
        r = await c.post(f"/review/v0/policy/records/{CL_LINE}/sign", json=bad, headers=H(SF1))
        assert r.status_code == 409 and r.json()["code"] == "DIGEST_CHANGED"
    # 非 staff（家长账号）不能签
    assert (await c.post(f"/review/v0/policy/records/{CL_LINE}/sign", json=sign, headers=H(PARENT))).status_code == 403
    assert (await c.post(f"/review/v0/policy/records/{CL_LINE}/sign", json=sign, headers=H(SF1))).json() == {"status": "draft"}
    r = await c.post(f"/review/v0/policy/records/{CL_LINE}/sign", json=sign, headers=H(SF1))
    assert r.status_code == 409 and r.json()["code"] == "CONFLICT"  # 同一账号同一内容只签一次
    assert (await c.post(f"/review/v0/policy/records/{CL_LINE}/sign", json=sign, headers=H(SF2))).json() == {"status": "published"}
    assert pg.psql(f"SELECT status FROM policy.control_line WHERE id = '{CL_LINE}'").strip() == "published"
    drafts = (await c.get("/review/v0/policy", params={"city": "synth-cl"}, headers=H(SF1))).json()["drafts"]
    assert all(x["id"] != str(CL_LINE) for x in drafts)
    # 已发布：不能再签
    r = await c.post(f"/review/v0/policy/records/{CL_LINE}/sign", json=sign, headers=H(SF1))
    assert r.status_code == 409
    # 版本记录：两次审核 + 一次发布，城市与年份来自控制线（platform.policy_signoff_record，0047）
    items = (await c.get("/review/v0/records", params={"subject": "aya"}, headers=H(SF1))).json()["items"]
    rows = [i for i in items if i["policy_object"] == "control_line"]
    assert [(i["action"], i["signer"], i["city"], i["cohort"], i["policy_object_id"]) for i in rows] == [
        ("review", str(SF1), "synth-cl", 2032, str(CL_LINE)), ("review", str(SF2), "synth-cl", 2032, str(CL_LINE)),
        ("publish", str(SF2), "synth-cl", 2032, str(CL_LINE))]
    assert all(i["line"] == "policy" and i["risk_level"] == "high" and i["policy_version"] is None for i in rows)


async def test_source_domain_admin(pg, rc):
    """官方来源清单（迁移 0048）：没有 policy_source_admin 的 staff（与非 staff）三个接口都是 403；有权限的能列表、新增（规范化）、
    下线、重新启用；主机名不合格、重复、下线两次、下线不存在的各有明确错误；每次成功都写操作记录（platform）。"""
    c, _, _ = rc
    pg.psql("INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.synthcity.example', '合成教育局', 'staff-001') ON CONFLICT DO NOTHING;")
    add = lambda who, host, note="合成教育局（非真实）": c.post("/review/v0/policy/sources", json={"host": host, "note": note}, headers=H(who))
    retire = lambda who, host: c.post("/review/v0/policy/sources/retire", json={"host": host}, headers=H(who))
    for who in (SF1, PARENT):
        for r in (await c.get("/review/v0/policy/sources", headers=H(who)), await add(who, "rc-new.synth.example"),
                  await retire(who, "jyj.synthcity.example")):
            assert r.status_code == 403 and r.json()["code"] == "SOURCE_ADMIN_REQUIRED", r.text
    assert pg.psql("SELECT count(*) FROM policy.source_domain WHERE host = 'rc-new.synth.example'").strip() == "0"
    pg.psql(f"INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES ('{SF1}', 'policy_source_admin', 'synthetic-dba');")
    lst = (await c.get("/review/v0/policy/sources", headers=H(SF1))).json()["items"]
    assert {"host": "jyj.synthcity.example", "issuer": "合成教育局", "status": "active"}.items() <= next(
        x for x in lst if x["host"] == "jyj.synthcity.example").items()
    assert all(set(x) == {"host", "issuer", "status", "added_at"} for x in lst)  # 不给登记人
    # 新增：去首尾空白、小写、去结尾的点
    r = await add(SF1, " RC-New.Synth.Example. ")
    assert r.status_code == 200 and r.json() == {"host": "rc-new.synth.example", "status": "active"}
    r = await add(SF1, "rc-new.synth.example")
    assert r.status_code == 409 and r.json()["code"] == "CONFLICT"
    # punycode（国际化域名）可以登记，界面另显示 Unicode 形式
    assert (await add(SF1, "xn--55qx5d.synth.example")).status_code == 200
    for bad in ("10.0.0.1", "*.synth.example", "gov.cn", "rc.synth.example:8443", "rc.synth.example/doc", "example", "rc_bad.example"):
        r = await add(SF1, bad)
        assert r.status_code == 422 and r.json()["code"] == "INVALID_PARAMETER" and "主机名不合格" in r.json()["detail"], bad
    for bad_note in ("合成\x00局", "   ", "长" * 65):
        assert (await add(SF1, "rc-note.synth.example", bad_note)).status_code == 422, bad_note
    # Unicode 类别 Cc（含 C1）、Cf（方向覆盖、零宽字符、BOM）、Zl、Zp：固定说明的 422，首尾的也算
    for bad_note in ("合成‮局", "合成​局", "合成‍局", "合成\u0085局", "合成\u009f局", "合成 局", "合成 局",
                     "﻿合成局", "合成局 ", "合成⁦局"):
        r = await add(SF1, "rc-note.synth.example", bad_note)
        assert r.status_code == 422 and "格式字符" in r.json()["detail"], repr(bad_note)
    assert pg.psql("SELECT count(*) FROM policy.source_domain WHERE host = 'rc-note.synth.example'").strip() == "0"
    assert (await c.post("/review/v0/policy/sources", json={"host": "rc-x.synth.example", "note": "x", "added_by": "x"},
                         headers=H(SF1))).status_code == 422  # 未知字段
    # 下线：只改状态不删行；下线两次 409（库里写好的说明）；不在清单 404；没有权限的 staff 仍 403
    assert (await retire(SF2, "rc-new.synth.example")).status_code == 403
    r = await retire(SF1, "RC-NEW.synth.example")
    assert r.status_code == 200 and r.json() == {"host": "rc-new.synth.example", "status": "retired"}
    r = await retire(SF1, "rc-new.synth.example")
    assert r.status_code == 409 and r.json()["code"] == "RULE_VIOLATION" and "已下线" in r.json()["detail"]
    assert (await retire(SF1, "rc-missing.synth.example")).status_code == 404
    assert (await retire(SF1, "gov.cn")).status_code == 422
    lst = (await c.get("/review/v0/policy/sources", headers=H(SF1))).json()["items"]
    assert next(x for x in lst if x["host"] == "rc-new.synth.example")["status"] == "retired"
    # 重新启用：说明更新为新的发布机关
    assert (await add(SF1, "rc-new.synth.example", "合成教育局（重新启用）")).status_code == 200
    assert pg.psql("SELECT status || ',' || issuer FROM policy.source_domain WHERE host = 'rc-new.synth.example'").strip() == "active,合成教育局（重新启用）"
    # 操作记录只在 platform（操作者是 staff 账号）；清单行的登记人固定 review-console
    assert pg.psql("SELECT string_agg(action || ':' || actor_account_id, ',' ORDER BY changed_at) FROM platform.policy_source_domain_change "
                   "WHERE host = 'rc-new.synth.example'").strip() == f"add:{SF1},retire:{SF1},reactivate:{SF1}"
    assert pg.psql("SELECT added_by FROM policy.source_domain WHERE host = 'rc-new.synth.example'").strip() == "review-console"


def test_db_error_lock_timeout_fixed_detail():
    """等锁超时（55P03）映射为 409 BUSY，说明是固定的“稍后重试”，不回显库里的消息（迁移 0052）。"""
    from review_console.store import BUSY_DETAIL, db_error
    err = db_error(asyncpg.exceptions.LockNotAvailableError("canceling statement due to lock timeout on relation source_domain"))
    assert (err.status, err.code, err.detail) == (409, "BUSY", BUSY_DETAIL) and "稍后重试" in BUSY_DETAIL


async def test_source_domain_lock_timeout(pg, rc):
    """官方来源清单的锁等待上限（迁移 0052，PR3 已知风险 1）：写入方（kge 登录角色）开着事务、经 policy.lock_active_source_host
    对清单行持有共享锁时，审核台下线约 5 秒后返回 409 BUSY（固定说明“稍后重试”）而不是挂住；清单不变；持锁事务的空闲上限被设为 60 秒；
    写入方结束事务后下线成功。"""
    c, kp, _ = rc
    host = "rc-lock.synth.example"
    pg.psql(f"INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES ('{SF1}', 'policy_source_admin', 'synthetic-dba') "
            "ON CONFLICT DO NOTHING;")
    r = await c.post("/review/v0/policy/sources", json={"host": host, "note": "合成锁等待教育局（非真实）"}, headers=H(SF1))
    assert r.status_code == 200, r.text
    conn = await kp.acquire()
    tr = conn.transaction()
    await tr.start()
    try:
        assert await conn.fetchval("SELECT policy.lock_active_source_host($1)", host) is True
        assert await conn.fetchval("SELECT current_setting('idle_in_transaction_session_timeout')") == "1min"
        t0 = time.monotonic()
        r = await c.post("/review/v0/policy/sources/retire", json={"host": host}, headers=H(SF1))
        elapsed = time.monotonic() - t0
        assert r.status_code == 409, r.text
        assert r.json() == {"type": "urn:starbuddy:review:busy", "status": 409, "code": "BUSY",
                            "detail": "官方来源清单的这一行正被占用（可能有来源文件正在登记），请稍后重试"}
        assert 4.5 <= elapsed < 10, elapsed
        assert pg.psql(f"SELECT status FROM policy.source_domain WHERE host = '{host}'").strip() == "active"
        assert pg.psql(f"SELECT count(*) FROM platform.policy_source_domain_change WHERE host = '{host}' AND action = 'retire'").strip() == "0"
    finally:
        await tr.rollback()
        await kp.release(conn)
    r = await c.post("/review/v0/policy/sources/retire", json={"host": host}, headers=H(SF1))
    assert r.status_code == 200 and r.json() == {"host": host, "status": "retired"}, r.text


async def test_source_domain_client_timeout(pg, rc, monkeypatch):
    """审核台的客户端超时（PR4 安全审查）：库里每次等锁最多 5 秒，但持续有登记时总等待可能更久；整个调用超过客户端超时
    （生产 10 秒，这里调成 1 秒以先于库里的 5 秒触发）同样回 409 BUSY 固定说明，查询被取消、事务回滚、清单不变，连接池仍可用。"""
    from review_console.store import ReviewStore
    c, kp, _ = rc
    assert ReviewStore.SOURCE_CHANGE_TIMEOUT_S == 10.0
    monkeypatch.setattr(ReviewStore, "SOURCE_CHANGE_TIMEOUT_S", 1.0)
    host = "rc-timeout.synth.example"
    pg.psql(f"INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES ('{SF1}', 'policy_source_admin', 'synthetic-dba') "
            "ON CONFLICT DO NOTHING;")
    r = await c.post("/review/v0/policy/sources", json={"host": host, "note": "合成超时教育局（非真实）"}, headers=H(SF1))
    assert r.status_code == 200, r.text
    conn = await kp.acquire()
    tr = conn.transaction()
    await tr.start()
    try:
        assert await conn.fetchval("SELECT policy.lock_active_source_host($1)", host) is True
        t0 = time.monotonic()
        r = await c.post("/review/v0/policy/sources/retire", json={"host": host}, headers=H(SF1))
        elapsed = time.monotonic() - t0
        assert r.status_code == 409, r.text
        assert r.json() == {"type": "urn:starbuddy:review:busy", "status": 409, "code": "BUSY",
                            "detail": "官方来源清单的这一行正被占用（可能有来源文件正在登记），请稍后重试"}
        assert elapsed < 4.5, elapsed  # 是客户端超时，不是库里的 5 秒锁等待上限
        assert pg.psql(f"SELECT status FROM policy.source_domain WHERE host = '{host}'").strip() == "active"
        assert pg.psql(f"SELECT count(*) FROM platform.policy_source_domain_change WHERE host = '{host}' AND action = 'retire'").strip() == "0"
    finally:
        await tr.rollback()
        await kp.release(conn)
    r = await c.post("/review/v0/policy/sources/retire", json={"host": host}, headers=H(SF1))
    assert r.status_code == 200 and r.json() == {"host": host, "status": "retired"}, r.text
