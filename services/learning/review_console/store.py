"""库访问。两个连接池、两个身份（spec 设计要点 3）：
- kge 池：读地图版本、差异所需的行、校验结果、变更单、版本记录；改草稿节点。它不能签字、发布（库层授权保证）。
- review 池：签字、发布、撤下。每次在一个事务里 `set_config('app.account_id', staff, true)`，staff 由调用方
  （已登录的会话）给出，库里的函数再核对是否为 active staff。
只读写 kge.* 与这些函数，不碰任何个人层表（规则 13）。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from .errors import ReviewError
from .snapshot import Snapshot

# 审核台 v0 允许在草稿上直接改的节点字段（spec 冲刺 1 验收：改一个节点的“不包含”）；结构（新增、停用、父节点）走变更单
EDITABLE_NODE_FIELDS = ("name", "includes", "excludes", "examples", "cognitive_levels", "scope")

_TABLES = {
    "knowledge_node": "SELECT * FROM kge.knowledge_node WHERE map_version_id = $1",
    "knowledge_edge": "SELECT * FROM kge.knowledge_edge WHERE map_version_id = $1",
    "textbook_projection": "SELECT * FROM kge.textbook_projection WHERE map_version_id = $1",
    "region_projection": "SELECT * FROM kge.region_projection WHERE map_version_id = $1",
    "skill_link": "SELECT * FROM kge.skill_link WHERE map_version_id = $1",
    "id_migration": "SELECT * FROM kge.id_migration WHERE map_version_id = $1",
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
    # 规则类错误的消息是库里写好的中文说明（只含公共层对象），原样给审核台看；其余不回传库的原始消息
    detail = str(getattr(e, "message", "")) if status in (403, 409) and code != "CONFLICT" else None
    return ReviewError(status, code, detail)


async def check_roles(kge: asyncpg.Pool, review: asyncpg.Pool) -> None:
    """启动检查：两个登录角色分别只继承各自的组角色，且都不是超级用户（与迁移 0019 的角色断言一致）。"""
    q = """SELECT current_user::text AS u, r.rolsuper,
                  pg_has_role(current_user, 'starbuddy_kge', 'MEMBER') AS is_kge,
                  pg_has_role(current_user, 'starbuddy_review', 'MEMBER') AS is_review
             FROM pg_roles r WHERE r.rolname = current_user"""
    a, b = await kge.fetchrow(q), await review.fetchrow(q)
    if a["rolsuper"] or b["rolsuper"] or a["u"] == b["u"]:
        raise RuntimeError("审核台的两个数据库登录角色必须不同，且都不能是超级用户")
    if not (a["is_kge"] and not a["is_review"]):
        raise RuntimeError("kge 连接的登录角色必须只继承 starbuddy_kge")
    if not (b["is_review"] and not b["is_kge"]):
        raise RuntimeError("review 连接的登录角色必须只继承 starbuddy_review")


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
            "SELECT id, change_type, status, payload, evidence_pack, confidence, created_at FROM kge.map_change"
            " WHERE target_version = $1 ORDER BY created_at, id", version_id)
        return [{**dict(r), "payload": json.loads(r["payload"]), "evidence_pack": json.loads(r["evidence_pack"])} for r in rows]

    async def records(self, subject: str, version_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT map_version_id, version, action, decision, risk_level, basis_code, digest, decided_at, signer"
            " FROM kge.version_record($1::platform.subject) WHERE $2::uuid IS NULL OR map_version_id = $2", subject, version_id)
        return [dict(r) for r in rows]

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
    """签字身份。每个方法一个事务；staff 账号只经 set_config 传给库，库里的函数再核对。"""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

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

    async def sign_change(self, staff: uuid.UUID, change_id: uuid.UUID, decision: str) -> str:
        return await self._as_staff(staff, lambda c: c.fetchval("SELECT kge.sign_map_change($1, $2)", change_id, decision))

    async def publish(self, staff: uuid.UUID, version_id: uuid.UUID, expected_digest: str):
        async def run(c):
            await self._expect_digest(c, version_id, expected_digest)
            return await c.fetchval("SELECT kge.publish_map_version($1)", version_id)
        return await self._as_staff(staff, run)

    async def retire(self, staff: uuid.UUID, version_id: uuid.UUID, basis_code: str) -> None:
        await self._as_staff(staff, lambda c: c.execute("SELECT kge.retire_map_version($1, $2)", version_id, basis_code))
