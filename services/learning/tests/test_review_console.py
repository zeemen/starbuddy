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


async def test_records_export(rc):
    c, kp, _ = rc
    items = (await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"]
    assert [(i["version"], i["action"]) for i in items] == [
        ("1.0.0", "review"), ("1.0.0", "review"), ("1.0.0", "publish"),
        ("1.0.1", "review"), ("1.0.0", "retire"), ("1.0.1", "publish"), ("1.0.1", "retire")]
    assert items[-1]["basis_code"] == "wrong_version" and items[-1]["signer"] == str(SF1)
    from review_console.service import version_record
    from review_console.store import KgeStore
    rows = await version_record(KgeStore(kp), "wenli")
    a, b = io.StringIO(), io.StringIO()
    write_records(rows, "csv", a)
    write_records(rows, "csv", b)
    assert a.getvalue() == b.getvalue()
    assert a.getvalue().splitlines()[0] == ("line,map_version_id,version,action,decision,risk_level,basis_code,digest,decided_at,signer,"
                                            "rubric_versions,validation_errors,validation_warnings,page_id,page_commit,"
                                            "policy_object,policy_object_id,city,cohort,policy_version")
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
    items = (await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"]
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

    items = (await c.get("/review/v0/records", params={"subject": "wenli"}, headers=H(SF1))).json()["items"]
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
    rows = await version_record(KgeStore(kp), "wenli")
    a, b = io.StringIO(), io.StringIO()
    write_records(rows, "csv", a)
    write_records(rows, "csv", b)
    assert a.getvalue() == b.getvalue()
    assert a.getvalue().splitlines()[0] == ("line,map_version_id,version,action,decision,risk_level,basis_code,digest,decided_at,signer,"
                                            "rubric_versions,validation_errors,validation_warnings,page_id,page_commit,"
                                            "policy_object,policy_object_id,city,cohort,policy_version")
    assert sum(1 for line in a.getvalue().splitlines() if line.startswith("policy,")) == 6
