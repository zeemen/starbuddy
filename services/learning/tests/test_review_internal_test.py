"""LT-06（specs/launch-lt06-internal-test-ops）：审核台的反馈只读列表、改状态与退出状态。全部合成数据（规则 8）。

- 纯函数：开关解析（缺省关；只认 internal_test；dev / test / staging 可开）；
- 库上（一次性 Postgres，登录角色与其他审核台测试分开）：开关关时三个接口不存在；列表新到旧、按状态筛、游标翻页、
  学生只给不透明指针（不回 student_id 与提交者账号）；改状态只收 new / triaged / closed、不存在的反馈 404、
  停用的 staff 被库拒绝；退出状态列表给出撤回的同意类型与是否仍有有效同意；参数校验。
"""
from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest

from review_console.app import create_app
from review_console.config import ReviewSettings, parse_internal_test

ST = uuid.UUID("17060000-0000-4000-8000-0000000000a1")
STX = uuid.UUID("17060000-0000-4000-8000-0000000000a2")   # 停用的 staff
GA = uuid.UUID("17060000-0000-4000-8000-0000000000b1")
AS = uuid.UUID("17060000-0000-4000-8000-0000000000b2")
AT = uuid.UUID("17060000-0000-4000-8000-0000000000b3")
HH = uuid.UUID("17060000-0000-4000-8000-0000000000c1")
S = uuid.UUID("17060000-0000-4000-8000-0000000000d1")
T = uuid.UUID("17060000-0000-4000-8000-0000000000d2")


def F(n: int) -> str:
    return f"17060000-0000-4000-8000-0000000001{n:02d}"


SEED = f"""
CREATE ROLE lt6_kge_app LOGIN IN ROLE starbuddy_kge;
CREATE ROLE lt6_sign_app LOGIN IN ROLE starbuddy_review;
INSERT INTO platform.account (id, kind, status) VALUES ('{ST}', 'staff', 'active'), ('{STX}', 'staff', 'disabled'),
  ('{GA}', 'guardian', 'active'), ('{AS}', 'student', 'active'), ('{AT}', 'student', 'active');
INSERT INTO platform.household (id, created_by_account) VALUES ('{HH}', '{GA}');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, birth_year_month) VALUES
  ('{S}', '{AS}', '{HH}', 8, 'nanjing', '2013-03-01'), ('{T}', '{AT}', '{HH}', 9, 'nanjing', '2012-03-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  ('{GA}', '{S}', 'household_creator'), ('{GA}', '{T}', 'household_creator');
-- 反馈（超级用户直接写，绕开 BFF 的插入策略；那一层由 db/tests/79zg 与 BFF 集成测试覆盖）：同一时刻两条，验证游标带 id
INSERT INTO platform.feedback (id, submitter_account_id, student_id, source, category, body, route_template, created_at) VALUES
  ('{F(1)}', '{AS}', '{S}', 'student_self_report', 'malfunction', '合成反馈一', '/s/[studentId]/today', '2031-03-01T08:00:00Z'),
  ('{F(2)}', '{GA}', '{S}', 'guardian_observation', 'wrong_content', '合成反馈二', NULL, '2031-03-01T09:00:00Z'),
  ('{F(3)}', '{GA}', '{T}', 'guardian_observation', 'slow', '合成反馈三', NULL, '2031-03-01T09:00:00Z');
-- 退出：S 已退出（隐私政策撤回、监护人同意仍有效）；T 未退出
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, decision, document_version, decided_at) VALUES
  ('{S}', '{GA}', 'privacy_policy', 'granted', 'privacy-policy@syn-lt6', '2031-02-01T00:00:00Z'),
  ('{S}', '{GA}', 'privacy_policy', 'revoked', 'privacy-policy@syn-lt6', '2031-03-02T00:00:00Z'),
  ('{S}', '{GA}', 'guardian_consent_under14', 'granted', 'guardian-consent@syn-lt6', '2031-02-01T00:00:00Z');
INSERT INTO platform.internal_test_exit (student_id, requested_by, exited_at, consents_revoked, sessions_revoked) VALUES
  ('{S}', '{GA}', '2031-03-02T00:00:00Z', ARRAY['privacy_policy'], 2);
"""


def H(account: uuid.UUID) -> dict:
    return {"x-test-account": str(account)}


def _auth():
    async def _a(req):
        v = req.headers.get("x-test-account")
        return uuid.UUID(v) if v else None
    return _a


# ---------- 纯函数 ----------
def test_internal_test_switch_parsing():
    assert parse_internal_test("", "staging") is False
    assert parse_internal_test(" internal_test ", "staging") is True
    assert parse_internal_test("internal_test", "dev") is True
    for raw, env in (("internal_test", "pilot"), ("internal_test", "production"), ("internal-test", "dev"), ("1", "test")):
        with pytest.raises(RuntimeError):
            parse_internal_test(raw, env)
    base = {"REVIEW_CONSOLE_ENV": "test", "REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://a",
            "REVIEW_CONSOLE_REVIEW_DATABASE_URL": "postgresql://b"}
    assert ReviewSettings.from_env(base).internal_test is False
    assert ReviewSettings.from_env({**base, "REVIEW_CONSOLE_INTERNAL_TEST": "internal_test"}).internal_test is True


# ---------- 库上 ----------
@pytest.fixture(scope="module")
async def lt6(pg):
    pg.psql(SEED)
    kp = await asyncpg.create_pool(**pg.dsn("lt6_kge_app"), min_size=1, max_size=2)
    rp = await asyncpg.create_pool(**pg.dsn("lt6_sign_app"), min_size=1, max_size=2)
    app = create_app(authenticate=_auth(), pools=(kp, rp), internal_test=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            yield client, kp, rp
    await kp.close()
    await rp.close()


async def test_switch_off_routes_absent(pg, lt6):
    _, kp, rp = lt6
    app = create_app(authenticate=_auth(), pools=(kp, rp))  # 缺省关
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            assert (await c.get("/review/v0/feedback", headers=H(ST))).status_code == 404
            assert (await c.post(f"/review/v0/feedback/{F(1)}/status", json={"status": "closed"}, headers=H(ST))).status_code == 404
            assert (await c.get("/review/v0/internal-test/exits", headers=H(ST))).status_code == 404


async def test_feedback_list_order_filter_paging(pg, lt6):
    c, _, _ = lt6
    r = await c.get("/review/v0/feedback", headers=H(ST))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [x["id"] for x in items] == [F(3), F(2), F(1)]  # 新到旧；同一时刻按 id 降序
    one = items[2]
    assert (one["source"], one["category"], one["body"], one["route_template"], one["status"]) == (
        "student_self_report", "malfunction", "合成反馈一", "/s/[studentId]/today", "new")
    assert set(one) == {"id", "source", "category", "body", "route_template", "status", "created_at", "status_changed_at", "student_ref"}
    assert str(S) not in r.text and str(AS) not in r.text and str(GA) not in r.text  # 只给不透明指针
    assert items[1]["student_ref"] == items[2]["student_ref"] != items[0]["student_ref"]
    # 游标翻页（每页 1 条；同一时刻的两条正好跨页）
    seen, params = [], {}
    for _ in range(6):  # 有上限：游标坏了（不带 id）时不会死循环
        page = (await c.get("/review/v0/feedback", params={"limit": 1, **params}, headers=H(ST))).json()
        seen += [x["id"] for x in page["items"]]
        if page["next"] is None:
            break
        params = page["next"]
    assert seen == [F(3), F(2), F(1)]
    assert (await c.get("/review/v0/feedback", params={"status": "closed"}, headers=H(ST))).json()["items"] == []
    for bad in ({"status": "done"}, {"limit": 0}, {"limit": 201}, {"before_id": F(1)}, {"before": "2031-03-01T09:00:00"}):
        assert (await c.get("/review/v0/feedback", params=bad, headers=H(ST))).status_code == 422, bad
    assert (await c.get("/review/v0/feedback")).status_code == 401
    assert (await c.get("/review/v0/feedback", headers=H(STX))).status_code == 403  # 停用的 staff：库里拒绝
    assert (await c.get("/review/v0/feedback", headers=H(GA))).status_code == 403   # 不是 staff


async def test_feedback_set_status(pg, lt6):
    c, _, _ = lt6
    r = await c.post(f"/review/v0/feedback/{F(2)}/status", json={"status": "triaged"}, headers=H(ST))
    assert r.status_code == 200 and r.json() == {"status": "triaged"}, r.text
    items = (await c.get("/review/v0/feedback", params={"status": "triaged"}, headers=H(ST))).json()["items"]
    assert [x["id"] for x in items] == [F(2)] and items[0]["status_changed_at"] is not None
    by = pg.psql(f"SELECT status_changed_by FROM platform.feedback WHERE id = '{F(2)}'")
    assert str(ST) in by
    # 可以重新打开；状态不变时照常回
    assert (await c.post(f"/review/v0/feedback/{F(2)}/status", json={"status": "new"}, headers=H(ST))).json() == {"status": "new"}
    assert (await c.post(f"/review/v0/feedback/{F(2)}/status", json={"status": "new"}, headers=H(ST))).status_code == 200
    for body in ({"status": "deleted"}, {}, {"status": "closed", "body": "x"}):
        assert (await c.post(f"/review/v0/feedback/{F(2)}/status", json=body, headers=H(ST))).status_code == 422, body
    assert (await c.post(f"/review/v0/feedback/{uuid.uuid4()}/status", json={"status": "closed"}, headers=H(ST))).status_code == 404
    assert (await c.post(f"/review/v0/feedback/{F(2)}/status", json={"status": "closed"}, headers=H(STX))).status_code == 403
    assert (await c.post(f"/review/v0/feedback/{F(2)}/status", json={"status": "closed"})).status_code == 401
    # 文本没被改动
    assert "合成反馈二" in pg.psql(f"SELECT body FROM platform.feedback WHERE id = '{F(2)}'")


async def test_internal_test_exits(pg, lt6):
    c, _, _ = lt6
    r = await c.get("/review/v0/internal-test/exits", headers=H(ST))
    assert r.status_code == 200, r.text
    ref = pg.psql(f"SELECT platform.internal_test_ref('{S}')").strip().splitlines()[-1].strip()
    assert ref.startswith("it-")
    mine = [x for x in r.json()["items"] if x["student_ref"] == ref]  # 同一会话库里别的测试也会写退出记录：只看本测试的学生
    assert len(mine) == 1
    x = mine[0]
    assert (x["consents_revoked"], x["consent_active"], x["sessions_revoked"], x["erasure_status"], x["erased"]) == (
        ["privacy_policy"], True, 2, None, False)
    assert x["exited_at"] is not None and x["student_ref"] and str(S) not in r.text
    for bad in ({"limit": 0}, {"limit": 501}):
        assert (await c.get("/review/v0/internal-test/exits", params=bad, headers=H(ST))).status_code == 422
    assert (await c.get("/review/v0/internal-test/exits", headers=H(STX))).status_code == 403
    assert (await c.get("/review/v0/internal-test/exits")).status_code == 401
