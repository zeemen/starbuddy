"""只读 kge.*（迁移 0026 的 starbuddy_kge_read）：知识服务读工具与编译器的数据库数据源。

两道防线：
1. 数据库：登录角色只继承 starbuddy_kge_read，连接建立后 SET ROLE starbuddy_kge_read，会话默认只读事务；
   RLS 只放出已发布地图版本里的有效节点与边、已核对的投影、审核通过的标签、已发布且在有效期内的时政。
   启动时断言登录角色不是超级用户、没有 BYPASSRLS、不继承 starbuddy_kge 或任何其他应用角色（assert_reader_role）。
2. 本模块的 SQL 自己也按同样的条件过滤（不依赖 RLS 才正确；有测试在不受 RLS 约束的连接上核对）。

不读写任何个人层表（规则 1、2、13）；不读投影的核对人工号与核对时间（0026 没有授予这两列）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

import asyncpg

from ..atoms import Snapshot, parse_snapshot
from ..nodeid import SUBJECT_PREFIX

READER_ROLE = "starbuddy_kge_read"
WRITER_ROLE = "starbuddy_kge"
# 读服务的登录角色不能同时继承这些角色（0026 的断言同一份清单）
OTHER_APP_ROLES = ("starbuddy_kge", "starbuddy_bff", "starbuddy_learning", "starbuddy_review", "starbuddy_scheduler",
                   "starbuddy_insight", "starbuddy_report", "starbuddy_authz")
SERVER_SETTINGS = {"default_transaction_read_only": "on", "application_name": "starbuddy-knowledge"}
PREFIX_SUBJECT = {v: k for k, v in SUBJECT_PREFIX.items()}


class ReaderRoleError(RuntimeError):
    """数据库连接的角色不安全：拒绝启动"""


def atom_key(prefix: str, row_id: Any) -> str:
    """投影、地区投影、时政在页面与快照里的键：不把数据库 UUID 写进页面（体检的个人信息检查会拦 UUID）"""
    return f"{prefix}-" + hashlib.sha256(str(row_id).encode("ascii")).hexdigest()[:16]


def subject_of(node_id: str) -> str | None:
    return PREFIX_SUBJECT.get(node_id.split(".", 1)[0])


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.execute(f"SET ROLE {READER_ROLE}")


async def assert_reader_role(conn: asyncpg.Connection) -> None:
    row = await conn.fetchrow(
        """SELECT current_user::text AS cur, r.rolsuper, r.rolbypassrls,
                  pg_has_role(session_user, $1, 'MEMBER') AS is_reader,
                  ARRAY(SELECT x FROM unnest($2::text[]) x WHERE pg_has_role(session_user, x, 'MEMBER')) AS others
             FROM pg_roles r WHERE r.rolname = session_user""", READER_ROLE, list(OTHER_APP_ROLES))
    if row is None:
        raise ReaderRoleError("无法确认登录角色")
    if row["rolsuper"]:
        raise ReaderRoleError("知识服务不能以超级用户连接数据库")
    if row["rolbypassrls"]:
        raise ReaderRoleError("知识服务的登录角色不能有 BYPASSRLS")
    if not row["is_reader"]:
        raise ReaderRoleError(f"登录角色必须继承 {READER_ROLE}")
    if row["others"]:
        raise ReaderRoleError(f"登录角色不能同时继承 {READER_ROLE} 以外的应用角色（含 {WRITER_ROLE}）")
    if row["cur"] != READER_ROLE:
        raise ReaderRoleError(f"当前角色必须是 {READER_ROLE}")
    cur = await conn.fetchrow("SELECT r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user")
    if cur is None or cur["rolsuper"] or cur["rolbypassrls"]:
        raise ReaderRoleError(f"{READER_ROLE} 的属性不安全")


async def create_reader_pool(dsn: str | None = None, **connect: Any) -> asyncpg.Pool:
    """建池并断言角色安全；不满足即关闭池并报错（fail closed）"""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_connection, server_settings=SERVER_SETTINGS, **connect)
    try:
        async with pool.acquire() as conn:
            await assert_reader_role(conn)
    except BaseException:
        await pool.close()
        raise
    return pool


@dataclass(frozen=True)
class MapVersionRow:
    id: Any
    subject: str
    version: str
    standard_year: int


# 第二道防线的过滤条件（与 0026 的 RLS 谓词对应）
_VERIFIED = "mapping_state = 'verified' AND quote_text IS NOT NULL"
_APPROVED = "t.tag_review_status = 'approved'"
_NODE_COLS = ("id, level, parent_id, kind, name, includes, excludes, examples, scope, cognitive_levels, "
              "error_type::text AS error_type, status")
_TP_COLS = ("id, node_id, edition, edition_year, printing, volume, chapter, page_from, page_to, quote_text, "
            "encode(quote_hash, 'hex') AS quote_hash, mapping_state")
_ITEM_COLS = ("t.subject::text AS subject, t.item_id::text AS item_id, t.main_node_id::text AS main_node_id, "
              "t.node_ids::text[] AS node_ids, t.cognitive_level, t.prior_difficulty, f.code AS family_code, t.license_state, "
              "t.family_id")


class KgeReader:
    """读 kge.* 的查询。每条查询都自带发布、有效、核对、审核通过的条件（第二道防线）"""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def published_version(self, subject: str) -> MapVersionRow | None:
        async with self.pool.acquire() as c:
            r = await c.fetchrow("""SELECT id, subject::text AS subject, version, standard_year FROM kge.map_version
                                     WHERE subject::text = $1 AND status = 'published'""", subject)
        return MapVersionRow(r["id"], r["subject"], r["version"], int(r["standard_year"])) if r else None

    async def node(self, version_id: Any, node_id: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as c:
            return await c.fetchrow(f"""SELECT {_NODE_COLS} FROM kge.knowledge_node
                                         WHERE map_version_id = $1 AND id = $2 AND status = 'active'""", version_id, node_id)

    async def nodes(self, version_id: Any) -> list[asyncpg.Record]:
        async with self.pool.acquire() as c:
            return await c.fetch(f"""SELECT {_NODE_COLS} FROM kge.knowledge_node
                                      WHERE map_version_id = $1 AND status = 'active' ORDER BY id""", version_id)

    async def textbook_projections(self, version_id: Any, node_id: str | None, edition: str | None = None) -> list[asyncpg.Record]:
        async with self.pool.acquire() as c:
            return await c.fetch(f"""SELECT {_TP_COLS} FROM kge.textbook_projection
                                      WHERE map_version_id = $1 AND ($2::text IS NULL OR node_id = $2)
                                        AND {_VERIFIED} AND ($3::text IS NULL OR edition = $3)
                                      ORDER BY edition, edition_year, volume, page_from, id""", version_id, node_id, edition)

    async def region_projections(self, version_id: Any, node_id: str | None, region: str | None = None) -> list[asyncpg.Record]:
        async with self.pool.acquire() as c:
            return await c.fetch("""SELECT id, node_id, region, exam_year, weight, typical_slots, style_tags, source_ref
                                      FROM kge.region_projection
                                     WHERE map_version_id = $1 AND ($2::text IS NULL OR node_id = $2) AND ($3::text IS NULL OR region = $3)
                                     ORDER BY region, exam_year DESC, id""", version_id, node_id, region)

    async def prerequisite_step(self, version_id: Any, frontier: Sequence[str], direction: str) -> list[tuple[str, str]]:
        """一层前置边（只走强前置边：type = prerequisite 且 strength = strong，两端同一地图版本）"""
        near = "to_id" if direction == "up" else "from_id"
        async with self.pool.acquire() as c:
            rows = await c.fetch(f"""SELECT from_id::text AS from_id, to_id::text AS to_id FROM kge.knowledge_edge
                                      WHERE map_version_id = $1 AND to_map_version_id = $1 AND status = 'active'
                                        AND type = 'prerequisite' AND strength = 'strong' AND {near} = ANY($2::text[])
                                      ORDER BY from_id, to_id""", version_id, list(frontier))
        return [(r["from_id"], r["to_id"]) for r in rows]

    async def item_search(self, version_id: Any, subject: str, node_id: str | None, cognitive_level: str | None,
                          dmin: float | None, dmax: float | None, family_code: str | None, limit: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as c:
            return await c.fetch(f"""SELECT {_ITEM_COLS} FROM kge.item_tag t
                                      LEFT JOIN kge.item_family f ON f.map_version_id = t.map_version_id AND f.id = t.family_id
                                      WHERE t.map_version_id = $1 AND t.subject::text = $2 AND {_APPROVED}
                                        AND ($3::text IS NULL OR t.main_node_id = $3 OR $3 = ANY(t.node_ids::text[]))
                                        AND ($4::text IS NULL OR t.cognitive_level = $4)
                                        AND ($5::numeric IS NULL OR t.prior_difficulty >= $5)
                                        AND ($6::numeric IS NULL OR t.prior_difficulty <= $6)
                                        AND ($7::text IS NULL OR f.code = $7)
                                      ORDER BY ($3::text IS NOT NULL AND t.main_node_id = $3) DESC, t.prior_difficulty NULLS LAST, t.item_id
                                      LIMIT $8""", version_id, subject, node_id, cognitive_level, dmin, dmax, family_code, limit)

    async def item(self, version_id: Any, subject: str, item_id: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as c:
            return await c.fetchrow(f"""SELECT {_ITEM_COLS} FROM kge.item_tag t
                                         LEFT JOIN kge.item_family f ON f.map_version_id = t.map_version_id AND f.id = t.family_id
                                         WHERE t.map_version_id = $1 AND t.subject::text = $2 AND t.item_id = $3
                                           AND {_APPROVED}""", version_id, subject, item_id)

    async def item_similar(self, version_id: Any, subject: str, item_id: str, family_id: Any, main_node_id: str,
                           limit: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as c:
            return await c.fetch(f"""SELECT {_ITEM_COLS}, (t.family_id IS NOT DISTINCT FROM $4 AND $4::uuid IS NOT NULL) AS same_family
                                      FROM kge.item_tag t
                                      LEFT JOIN kge.item_family f ON f.map_version_id = t.map_version_id AND f.id = t.family_id
                                      WHERE t.map_version_id = $1 AND t.subject::text = $2 AND t.item_id <> $3
                                        AND {_APPROVED}
                                        AND (($4::uuid IS NOT NULL AND t.family_id = $4) OR t.main_node_id = $5)
                                      ORDER BY same_family DESC, t.item_id LIMIT $6""",
                                   version_id, subject, item_id, family_id, main_node_id, limit)

    async def scoring_points(self, version_id: Any, subject: str, item_id: str, rubric_version: str) -> list[asyncpg.Record]:
        async with self.pool.acquire() as c:
            return await c.fetch("""SELECT point_id, score, node_ids::text[] AS node_ids, subject_dimension, keyword_family,
                                           partial_credit_rule
                                      FROM kge.scoring_point
                                     WHERE map_version_id = $1 AND subject::text = $2 AND item_id = $3 AND rubric_version = $4
                                     ORDER BY point_id""", version_id, subject, item_id, rubric_version)

    # ---------- 编译器数据源：已发布地图版本的公共层快照 ----------
    async def snapshot_doc(self, subject: str) -> dict[str, Any]:
        """与 knowledge.atoms 的快照格式相同的文档（只含白名单字段）；没有已发布版本时报错"""
        v = await self.published_version(subject)
        if v is None:
            raise LookupError(f"{subject} 没有已发布的地图版本")
        async with self.pool.acquire() as c, c.transaction(readonly=True, isolation="repeatable_read"):
            nodes = await c.fetch(f"SELECT {_NODE_COLS} FROM kge.knowledge_node WHERE map_version_id = $1 AND status = 'active' ORDER BY id", v.id)
            edges = await c.fetch("""SELECT from_id::text AS from_id, to_id::text AS to_id, type, strength, status FROM kge.knowledge_edge
                                      WHERE map_version_id = $1 AND to_map_version_id = $1 AND status = 'active'
                                      ORDER BY from_id, type, to_id""", v.id)
            items = await c.fetch("""SELECT standard_year, code, domain, text, verb_level, source_ref FROM kge.curriculum_item
                                      WHERE subject::text = $1 AND standard_year = $2 ORDER BY code""", subject, v.standard_year)
            tps = await c.fetch(f"""SELECT {_TP_COLS} FROM kge.textbook_projection
                                     WHERE map_version_id = $1 AND {_VERIFIED} ORDER BY id""", v.id)
            rps = await c.fetch("""SELECT id, node_id::text AS node_id, region, exam_year, weight, typical_slots, style_tags, source_ref
                                     FROM kge.region_projection WHERE map_version_id = $1 ORDER BY id""", v.id)
            cas = await c.fetch("""SELECT id, node_ids::text[] AS node_ids, source, valid_from, valid_until, review_status
                                     FROM kge.current_affair
                                    WHERE map_version_id = $1 AND review_status = 'published'
                                      AND current_date >= valid_from AND current_date < valid_until ORDER BY id""", v.id)
        visible = {r["id"] for r in nodes}
        return {
            "map_version": v.version, "subject": subject, "status": "published", "standard_year": v.standard_year,
            "nodes": [node_record(r) for r in nodes],
            "edges": [dict(r) for r in edges if r["from_id"] in visible and r["to_id"] in visible],
            "curriculum_items": [dict(r) for r in items],
            "textbook_projections": [projection_record(r) for r in tps if r["node_id"] in visible],
            "region_projections": [region_record(r) for r in rps if r["node_id"] in visible],
            "current_affairs": [{"id": atom_key("ca", r["id"]), "node_ids": list(r["node_ids"]), "source": r["source"],
                                 "valid_from": r["valid_from"].isoformat(), "valid_until": r["valid_until"].isoformat(),
                                 "review_status": r["review_status"]} for r in cas],
        }


def node_record(r: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": r["id"], "level": r["level"], "name": r["name"]}
    if r["parent_id"]:
        out["parent_id"] = r["parent_id"]
    if r["kind"]:
        out["kind"] = r["kind"]
    for k in ("includes", "excludes", "examples", "cognitive_levels"):
        if r[k]:
            out[k] = list(r[k])
    out["scope"] = r["scope"]
    return out


def projection_record(r: Any) -> dict[str, Any]:
    out = {"id": atom_key("tp", r["id"]), "node_id": r["node_id"], "edition": r["edition"], "edition_year": int(r["edition_year"]),
           "volume": r["volume"], "chapter": r["chapter"], "page_from": int(r["page_from"]), "page_to": int(r["page_to"]),
           "quote_text": r["quote_text"], "mapping_state": r["mapping_state"]}
    if r["printing"]:
        out["printing"] = r["printing"]
    return out


def region_record(r: Any) -> dict[str, Any]:
    out = {"id": atom_key("rp", r["id"]), "node_id": r["node_id"], "region": r["region"], "exam_year": int(r["exam_year"]),
           "weight": float(r["weight"]), "source_ref": r["source_ref"]}
    if r["typical_slots"]:
        out["typical_slots"] = list(r["typical_slots"])
    if r["style_tags"]:
        out["style_tags"] = list(r["style_tags"])
    return out


def jsonb(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class DbAtomSource:
    """PR ① 的 AtomSource 的数据库实现：只读 kge.* 已发布版本（编译器用；文件快照实现保留给测试与页面仓库 CI）"""
    dsn: str
    subject: str

    async def load_async(self) -> Snapshot:
        pool = await create_reader_pool(self.dsn)
        try:
            return parse_snapshot(await KgeReader(pool).snapshot_doc(self.subject))
        finally:
            await pool.close()

    def load(self) -> Snapshot:
        return asyncio.run(self.load_async())
