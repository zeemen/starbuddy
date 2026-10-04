"""库访问。两个连接池、两个身份（spec 设计要点 3）：
- kge 池：读地图版本、差异所需的行、校验结果、变更单、版本记录；改草稿节点。它不能签字、发布（库层授权保证）。
- review 池：签字、发布、撤下。每次在一个事务里 `set_config('app.account_id', staff, true)`，staff 由调用方
  （已登录的会话）给出，库里的函数再核对是否为 active staff。
只读写 kge.* 与这些函数，不直接读写任何个人层表（规则 13）。统一复核队列（冲刺 2-G，迁移 0023）也只经库函数：
队列与详情只给学生的不透明指针、结构化判分与判断元数据，不给 student_id 与作答原文；领取、放回、结案由库核对领取人与白名单。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from .errors import ReviewError
from .snapshot import Snapshot

# 审核台 v0 允许在草稿上直接改的节点字段（spec 冲刺 1 验收：改一个节点的“不包含”）；结构（新增、停用、父节点）走变更单
EDITABLE_NODE_FIELDS = ("name", "includes", "excludes", "examples", "cognitive_levels", "scope")

_TABLES = {
    "map_version": "SELECT * FROM kge.map_version WHERE id = $1",
    "knowledge_node": "SELECT * FROM kge.knowledge_node WHERE map_version_id = $1",
    "knowledge_edge": "SELECT * FROM kge.knowledge_edge WHERE map_version_id = $1",
    "textbook_projection": "SELECT * FROM kge.textbook_projection WHERE map_version_id = $1",
    "region_projection": "SELECT * FROM kge.region_projection WHERE map_version_id = $1",
    "skill_link": "SELECT * FROM kge.skill_link WHERE map_version_id = $1",
    "id_migration": "SELECT * FROM kge.id_migration WHERE map_version_id = $1",
    "item_family": "SELECT * FROM kge.item_family WHERE map_version_id = $1",
    "item_tag": """SELECT t.*, f.code AS family FROM kge.item_tag t
                    LEFT JOIN kge.item_family f ON f.map_version_id = t.map_version_id AND f.id = t.family_id
                   WHERE t.map_version_id = $1""",
    "scoring_point": "SELECT * FROM kge.scoring_point WHERE map_version_id = $1",
    "current_affair": "SELECT * FROM kge.current_affair WHERE map_version_id = $1",
}

# 库里的错误码 → HTTP：权限 403；业务规则（校验、摘要、人数、版本号）409；参数 422；不存在 404
_PG_MAP = {"42501": (403, "FORBIDDEN"), "23514": (409, "RULE_VIOLATION"), "22023": (422, "INVALID_PARAMETER"),
           "P0002": (404, "NOT_FOUND"), "23505": (409, "CONFLICT"), "23503": (422, "INVALID_REFERENCE"),
           "22P02": (422, "INVALID_PARAMETER"), "23502": (422, "INVALID_PARAMETER"), "22001": (422, "INVALID_PARAMETER")}


def db_error(e: asyncpg.PostgresError) -> ReviewError:
    status, code = _PG_MAP.get(getattr(e, "sqlstate", "") or "", (500, "INTERNAL"))
    # 只回传函数里 RAISE 写好的中文规则说明（409 且不是约束、不带表名）；权限错误与库的原生约束消息会带出表名、
    # 约束名，一律只给错误码（安全审查 B-5）
    native = getattr(e, "constraint_name", None) is not None or getattr(e, "table_name", None) is not None
    detail = (getattr(e, "message", None) or str(e)) if code == "RULE_VIOLATION" and not native else None
    return ReviewError(status, code, detail)


APP_ROLES = ("starbuddy_kge", "starbuddy_review", "starbuddy_learning", "starbuddy_bff", "starbuddy_insight",
             "starbuddy_authz", "starbuddy_scheduler")


async def check_roles(kge: asyncpg.Pool, review: asyncpg.Pool) -> None:
    """启动检查：两个登录角色分别只继承各自的组角色，且都不是超级用户（与迁移 0019 的角色断言一致）。"""
    a, b = await role_facts(kge), await role_facts(review)
    if a["u"] == b["u"]:
        raise RuntimeError("审核台的两个数据库登录角色必须不同")
    expect_role(a, "starbuddy_kge")
    expect_role(b, "starbuddy_review")


async def role_facts(conn) -> dict[str, Any]:
    """当前登录角色的危险属性，以及（经中间角色也算）继承了哪些应用角色。"""
    r = await conn.fetchrow(
        """SELECT current_user::text AS u, r.rolsuper, r.rolbypassrls,
                  ARRAY(SELECT a FROM unnest($1::text[]) a WHERE pg_has_role(current_user, a, 'MEMBER') ORDER BY a) AS app_roles
             FROM pg_roles r WHERE r.rolname = current_user""", list(APP_ROLES))
    return dict(r)


def expect_role(facts: dict[str, Any], role: str) -> None:
    """启动检查（安全审查 B-3）：不是超级用户、不绕过 RLS，恰好只继承一个应用角色。迁移里的断言只在执行那一刻有效。"""
    if facts["rolsuper"] or facts["rolbypassrls"] or list(facts["app_roles"]) != [role]:
        raise RuntimeError(f"登录角色必须只继承 {role}，且不能是超级用户或绕过 RLS")


class KgeStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def list_versions(self, subject: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT id, subject::text, version, standard_year, status, created_at, published_at FROM kge.map_version"
            " WHERE subject = $1::platform.subject ORDER BY created_at, id", subject)
        return [dict(r) for r in rows]

    async def version(self, version_id: uuid.UUID) -> dict[str, Any]:
        r = await self.pool.fetchrow(
            "SELECT id, subject::text, version, standard_year, status, created_at, published_at FROM kge.map_version WHERE id = $1",
            version_id)
        if r is None:
            raise ReviewError(404, "NOT_FOUND", "地图版本不存在")
        return dict(r)

    async def published_base(self, subject: str, exclude: uuid.UUID) -> uuid.UUID | None:
        return await self.pool.fetchval(
            "SELECT id FROM kge.map_version WHERE subject = $1::platform.subject AND status = 'published' AND id <> $2",
            subject, exclude)

    async def snapshot(self, version_id: uuid.UUID) -> Snapshot:
        async with self.pool.acquire() as c, c.transaction(isolation="repeatable_read", readonly=True):
            rows = {t: [dict(r) for r in await c.fetch(q, version_id)] for t, q in _TABLES.items()}
            digest = await c.fetchval("SELECT kge.map_version_digest($1)", version_id)
        s = Snapshot.from_rows(str(version_id), rows)
        s.digest = digest
        return s

    async def digest(self, version_id: uuid.UUID) -> str:
        return await self.pool.fetchval("SELECT kge.map_version_digest($1)", version_id)

    async def validate(self, version_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT check_name, severity, object_id, detail FROM kge.validate_map_version($1)"
            " ORDER BY severity, check_name, object_id, detail", version_id)
        return [dict(r) for r in rows]

    async def change_requests(self, version_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT m.id, m.change_type, m.status, m.created_at, kge.map_change_digest_of(m.id) AS digest,
                      -- 签字覆盖的内容：与 kge.map_change_digest 的输入一致（去掉状态、审核人、建立时间），页面原样显示（契约审查 PR 3 B1）
                      (to_jsonb(m) - 'status' - 'reviewers' - 'created_at')::text AS signed_content
                 FROM kge.map_change m WHERE m.target_version = $1 ORDER BY m.created_at, m.id""", version_id)
        return [{**dict(r), "signed_content": json.loads(r["signed_content"])} for r in rows]

    async def records(self, subject: str, version_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT map_version_id, version, action, decision, risk_level, basis_code, digest, decided_at, signer"
            " FROM kge.version_record($1::platform.subject) WHERE $2::uuid IS NULL OR map_version_id = $2", subject, version_id)
        return [dict(r) for r in rows]

    async def page_records(self, subject: str | None = None) -> list[dict[str, Any]]:
        """编译页面审核记录（迁移 0038 kge.page_review_record：页面 ID、提交号、签字序号、风险、结论、摘要、签字人）；
        subject 为空时全部，否则库里按页面 ID 的学科前缀过滤。"""
        rows = await self.pool.fetch(
            "SELECT page_id, page_commit, signer_no, risk_level, decision, basis_code, digest, decided_at, signer"
            " FROM kge.page_review_record($1::text::platform.subject)", subject)
        return [dict(r) for r in rows]

    async def structural(self, version_id: uuid.UUID) -> bool:
        """整个版本是否结构性，以库的判定为准（与发布时同一个函数）。"""
        return bool(await self.pool.fetchval("SELECT kge.structural_diff($1)", version_id))

    async def version_facts(self, version_id: uuid.UUID) -> dict[str, Any]:
        """版本记录附带的内容事实：涉及的评分版本、当前校验的错误与警告数（spec 设计要点 11）。"""
        r = await self.pool.fetchrow(
            """SELECT ARRAY(SELECT DISTINCT p.rubric_version::text FROM kge.scoring_point p WHERE p.map_version_id = $1 ORDER BY 1) AS rv,
                      (SELECT count(*) FROM kge.validate_map_version($1) x WHERE x.severity = 'error') AS errors,
                      (SELECT count(*) FROM kge.validate_map_version($1) x WHERE x.severity = 'warning') AS warnings""", version_id)
        return {"rubric_versions": list(r["rv"]), "validation_errors": int(r["errors"]), "validation_warnings": int(r["warnings"])}

    async def impact(self, version_id: uuid.UUID, node_ids: list[str]) -> dict[str, int]:
        """发布前影响分析（v0 只给公共层计数，spec 待人确认 #8）：挂在变化节点上的题目标签与采分点。"""
        r = await self.pool.fetchrow(
            """SELECT (SELECT count(*) FROM kge.item_tag t WHERE t.map_version_id = $1
                        AND (t.main_node_id::text = ANY ($2::text[]) OR t.node_ids::text[] && $2::text[])) AS item_tags,
                      (SELECT count(*) FROM kge.scoring_point p WHERE p.map_version_id = $1 AND p.node_ids::text[] && $2::text[]) AS scoring_points""",
            version_id, node_ids)
        return {"item_tags": int(r["item_tags"]), "scoring_points": int(r["scoring_points"])}

    async def edit_node(self, version_id: uuid.UUID, node_id: str, fields: dict[str, Any], expected_digest: str) -> str:
        """改草稿节点。带上编辑时看到的摘要，与当前不同就拒绝（别人先改过）。返回改后的摘要。"""
        unknown = set(fields) - set(EDITABLE_NODE_FIELDS)
        if not fields or unknown:
            raise ReviewError(422, "INVALID_PARAMETER", "只能改名称、包含、不包含、典型题例、认知层次、范围")
        cols = sorted(fields)
        sets = ", ".join(f"{c} = ${i + 3}" for i, c in enumerate(cols))
        try:
            async with self.pool.acquire() as c, c.transaction():
                st = await c.fetchval("SELECT status FROM kge.map_version WHERE id = $1 FOR UPDATE", version_id)
                if st is None:
                    raise ReviewError(404, "NOT_FOUND", "地图版本不存在")
                if st != "draft":
                    raise ReviewError(409, "RULE_VIOLATION", "只能改草稿版本")
                if await c.fetchval("SELECT kge.map_version_digest($1)", version_id) != expected_digest:
                    raise ReviewError(409, "DIGEST_CHANGED", "草稿已被别人改过，请刷新后再改")
                n = await c.execute(f"UPDATE kge.knowledge_node SET {sets} WHERE map_version_id = $1 AND id = $2",
                                    version_id, node_id, *[fields[k] for k in cols])
                if n != "UPDATE 1":
                    raise ReviewError(404, "NOT_FOUND", "节点不存在")
                return await c.fetchval("SELECT kge.map_version_digest($1)", version_id)
        except asyncpg.PostgresError as e:
            raise db_error(e) from None


class ReviewStore:
    """签字身份。每个方法一个事务；staff 账号只经 set_config 传给库，库里的函数再核对。
    登录走单独的小连接池（login_pool）：未登录请求能调登录，每次要做一次 bcrypt，不能占满签字用的连接（安全审查 PR 4 建议 1）。"""

    def __init__(self, pool: asyncpg.Pool, login_pool: asyncpg.Pool | None = None) -> None:
        self.pool = pool
        self.login_pool = login_pool or pool

    async def _as_staff(self, staff: uuid.UUID, fn):
        try:
            async with self.pool.acquire() as c, c.transaction():
                await c.execute("SELECT set_config('app.account_id', $1, true)", str(staff))
                return await fn(c)
        except asyncpg.PostgresError as e:
            raise db_error(e) from None

    @staticmethod
    async def _expect_digest(c, version_id: uuid.UUID, expected: str) -> None:
        if await c.fetchval("SELECT kge.map_version_digest($1)", version_id) != expected:
            raise ReviewError(409, "DIGEST_CHANGED", "你看到的内容与当前草稿不一致，请刷新后重新审核")

    async def sign_version(self, staff: uuid.UUID, version_id: uuid.UUID, decision: str, basis_code: str | None,
                           expected_digest: str) -> uuid.UUID:
        async def run(c):
            # 看到的就是签的（spec 设计要点 4）：签字前后各比一次摘要，任一不符整笔回滚
            await self._expect_digest(c, version_id, expected_digest)
            # 地图版本的审核记录按“高”风险记（迁移 0019 约束 content_review_version_risk）
            rid = await c.fetchval("SELECT kge.sign_review('map_version', $1, $2, 'high', $3)", str(version_id), decision, basis_code)
            await self._expect_digest(c, version_id, expected_digest)
            return rid
        return await self._as_staff(staff, run)

    async def sign_change(self, staff: uuid.UUID, change_id: uuid.UUID, decision: str, expected_digest: str) -> str:
        # 库里的签字函数在锁住变更单之后比对摘要（安全审查 A-1）
        return await self._as_staff(staff, lambda c: c.fetchval("SELECT kge.sign_map_change($1, $2, $3)", change_id, decision, expected_digest))

    async def publish(self, staff: uuid.UUID, version_id: uuid.UUID, expected_digest: str):
        async def run(c):
            await self._expect_digest(c, version_id, expected_digest)
            at = await c.fetchval("SELECT kge.publish_map_version($1)", version_id)
            # 发布函数已锁住版本行；再比一次，间隙里被改过就整笔回滚（安全审查 B-1）
            await self._expect_digest(c, version_id, expected_digest)
            return at
        return await self._as_staff(staff, run)

    # ---------- staff 登录与会话（迁移 0020；凭据核对全在库里，这里只转交） ----------
    async def login(self, login_name: str, password: str, code: str) -> str | None:
        """成功返回会话令牌；任何失败都是 None（库里不区分原因，连错 5 次锁 15 分钟）。"""
        try:
            return await self.login_pool.fetchval("SELECT platform.staff_login($1, $2, $3)", login_name, password, code, timeout=5)
        except asyncpg.PostgresError as e:
            raise db_error(e) from None

    async def resolve(self, token: str) -> tuple[uuid.UUID, str] | None:
        try:
            r = await self.pool.fetchrow("SELECT account_id, login_name FROM platform.staff_session_resolve($1)", token)
        except asyncpg.PostgresError as e:
            raise db_error(e) from None
        return (r["account_id"], r["login_name"]) if r else None

    async def logout(self, token: str) -> None:
        try:
            await self.pool.execute("SELECT platform.staff_logout($1)", token)
        except asyncpg.PostgresError as e:
            raise db_error(e) from None

    async def retire(self, staff: uuid.UUID, version_id: uuid.UUID, basis_code: str) -> None:
        await self._as_staff(staff, lambda c: c.execute("SELECT kge.retire_map_version($1, $2)", version_id, basis_code))

    # ---------- 统一复核队列（冲刺 2-G，迁移 0023；staff 与领取人都由库函数核对） ----------
    async def queue(self, staff: uuid.UUID, subject: str | None, status: str | None, reason: str | None, limit: int,
                    before: datetime | None, before_id: uuid.UUID | None) -> list[dict[str, Any]]:
        rows = await self._as_staff(staff, lambda c: c.fetch(
            "SELECT * FROM platform.review_queue($1::text::platform.subject, $2, $3, $4, $5, $6)",
            subject, status, reason, limit, before, before_id))
        return [_task_row(dict(r)) for r in rows]

    async def task_detail(self, staff: uuid.UUID, task_id: uuid.UUID) -> dict[str, Any]:
        raw = await self._as_staff(staff, lambda c: c.fetchval("SELECT platform.review_task_detail($1)::text", task_id))
        return json.loads(raw)

    async def resolution_codes(self, staff: uuid.UUID) -> list[dict[str, Any]]:
        rows = await self._as_staff(staff, lambda c: c.fetch(
            "SELECT code, label, applies_to FROM platform.review_resolution_code ORDER BY code"))
        return [{"code": r["code"], "label": r["label"], "applies_to": list(r["applies_to"])} for r in rows]

    async def take_task(self, staff: uuid.UUID, task_id: uuid.UUID) -> None:
        await self._as_staff(staff, lambda c: c.execute("SELECT platform.take_review_task($1)", task_id))

    async def release_task(self, staff: uuid.UUID, task_id: uuid.UUID) -> None:
        await self._as_staff(staff, lambda c: c.execute("SELECT platform.release_review_task($1)", task_id))

    async def resolve_task(self, staff: uuid.UUID, task_id: uuid.UUID, code: str) -> None:
        await self._as_staff(staff, lambda c: c.execute("SELECT platform.resolve_review_task($1, $2)", task_id, code))


def _task_row(r: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in r.items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, datetime):
            v = v.isoformat()
        elif k == "reasons":
            v = list(v)
        out[k] = v
    return out
