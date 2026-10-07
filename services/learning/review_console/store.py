"""库访问。两个连接池、两个身份（spec 设计要点 3）：
- kge 池：读地图版本、差异所需的行、校验结果、变更单、版本记录；改草稿节点。它不能签字、发布（库层授权保证）。
  冲刺 4 起另经只读函数读内容版本与发布记录（0042），直接读考情表（kge 是考情写入方），调时政过期下架（0043）。
- review 池：签字、发布、撤下（地图、内容版本与准入 0039、考情 0041、控制线 0047、编译页面 0038），官方来源清单的新增与下线（0048，
  另要 staff 权限 policy_source_admin）。每次在一个事务里 `set_config('app.account_id', staff, true)`，staff 由调用方
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

    async def policy_records(self) -> list[dict[str, Any]]:
        """考情政策线的签字与发布记录（迁移 0040 platform.policy_signoff_record：对象类型与 ID、城市、方案版本、届别、动作、结论、摘要、签字人）。
        考情的复核人留在签字人表 platform.policy_signoff，不在 kge.content_review 里另记（spec 第 8 节，规则 2）。"""
        rows = await self.pool.fetch(
            "SELECT object_type, object_id, city, version, cohort, action, decision, digest, signed_at, signer"
            " FROM platform.policy_signoff_record()")
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

    # ---------- 内容版本（冲刺 4，迁移 0039 / 0042：经只读函数读，表本身读不到） ----------
    async def content_versions(self, subject: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch("SELECT * FROM platform.content_version_list($1::platform.subject)", subject)
        return [{**dict(r), "subject": str(r["subject"])} for r in rows]

    async def content_snapshot(self, cv: uuid.UUID) -> dict[str, Any]:
        raw = await self.pool.fetchval("SELECT platform.content_version_snapshot($1)::text", cv)
        if raw is None:
            raise ReviewError(404, "NOT_FOUND", "内容版本不存在")
        return json.loads(raw)

    async def release_records(self, subject: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch("SELECT * FROM platform.release_record_export($1::platform.subject)", subject)
        return [{**dict(r), "from_admission": _s(r["from_admission"]), "to_admission": _s(r["to_admission"]),
                 "affected_rubric_versions": list(r["affected_rubric_versions"]) if r["affected_rubric_versions"] is not None else None}
                for r in rows]

    async def map_version_brief(self, version_id: uuid.UUID | None) -> dict[str, Any] | None:
        if version_id is None:
            return None
        r = await self.pool.fetchrow("SELECT id, subject::text, version, status FROM kge.map_version WHERE id = $1", version_id)
        return dict(r) if r else None

    async def has_published_scheme(self, city: str, cohort: int) -> bool:
        """内容版本的（地区, 届别）在政策库里有没有已发布方案（spec 第 7 节：没有就在报告里记警告，不阻断）。"""
        return bool(await self.pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM policy.exam_scheme WHERE city::text = $1 AND cohort = $2 AND status = 'published')", city, cohort))

    # ---------- 考情政策（冲刺 4，迁移 0013 / 0041：写入方 kge 能读 policy 全部表） ----------
    async def schemes(self, city: str | None = None) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT id, city::text, cohort::integer, version, status::text, source_document_id, published_at, created_at
                 FROM policy.exam_scheme WHERE $1::text IS NULL OR city::text = $1
                ORDER BY city, cohort, string_to_array(version, '.')::int[], id""", city)
        return [dict(r) for r in rows]

    async def scheme_snapshot(self, scheme_id: uuid.UUID) -> dict[str, Any]:
        async with self.pool.acquire() as c, c.transaction(isolation="repeatable_read", readonly=True):
            s = await c.fetchrow(
                """SELECT id, city::text, cohort::integer, version, status::text, source_document_id, published_at
                     FROM policy.exam_scheme WHERE id = $1""", scheme_id)
            if s is None:
                raise ReviewError(404, "NOT_FOUND", "方案不存在")
            subjects = await c.fetch("SELECT subject::text, scored, full_score, proposition, exam_form, duration_minutes, "
                                     "combined_group::text, taken_in_grade, exam_month, eligibility_effect "
                                     "FROM policy.subject_config WHERE scheme_id = $1 ORDER BY subject", scheme_id)
            batches = await c.fetch("SELECT batch_code::text, batch_order, track, control_line_basis FROM policy.admission_rule "
                                    "WHERE scheme_id = $1 ORDER BY batch_code, track", scheme_id)
            transitions = await c.fetch("SELECT change_kind, replaces_scheme_id FROM policy.transition_rule WHERE scheme_id = $1 "
                                        "ORDER BY change_kind", scheme_id)
            total = await c.fetchval("SELECT policy.scheme_total($1)", scheme_id)
            digest = await c.fetchval("SELECT policy.object_digest('exam_scheme', $1)", scheme_id)
            approvals = await c.fetchval("SELECT policy.approval_count('exam_scheme', $1)", scheme_id)
        return {"scheme": dict(s), "subjects": [dict(r) for r in subjects], "batches": [dict(r) for r in batches],
                "transitions": [dict(r) for r in transitions], "total": total, "digest": digest, "approvals": approvals}

    async def scheme_bases(self, s: dict[str, Any]) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        """（同城市同届别的上一版本：已发布或已撤下、版本号更小的最大者；同城市上一届的已发布方案）"""
        prev = await self.pool.fetchval(
            """SELECT id FROM policy.exam_scheme WHERE city::text = $1 AND cohort = $2 AND id <> $3 AND status <> 'draft'
                  AND string_to_array(version, '.')::int[] < string_to_array($4, '.')::int[]
                ORDER BY string_to_array(version, '.')::int[] DESC LIMIT 1""", s["city"], s["cohort"], s["id"], s["version"])
        last = await self.pool.fetchval(
            "SELECT id FROM policy.exam_scheme WHERE city::text = $1 AND cohort = $2 AND status = 'published'", s["city"], s["cohort"] - 1)
        return prev, last

    async def validate_scheme(self, scheme_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = await self.pool.fetch("SELECT check_name, severity, object_id, detail FROM policy.validate_scheme($1)"
                                     " ORDER BY severity, check_name, object_id, detail", scheme_id)
        return [dict(r) for r in rows]

    async def source_domains(self) -> list[dict[str, Any]]:
        """官方来源清单（policy.source_domain，公共层；迁移 0048 起只下线不删除）。不给登记人工号：审核台登记的一律是 review-console，
        操作者只记在 platform.policy_source_domain_change（账号引用不进 policy，规则 13）。"""
        rows = await self.pool.fetch("SELECT host, issuer, status, added_at FROM policy.source_domain ORDER BY status, host")
        return [dict(r) for r in rows]

    async def policy_drafts(self, city: str | None = None) -> list[dict[str, Any]]:
        """待签的来源文件、招录行、学校、控制线（草稿），带当前摘要与有效批准人数（第二名不同 staff 批准时由 policy.sign_record 发布）。"""
        rows = await self.pool.fetch(
            """SELECT 'source_document' AS object_type, d.id, d.city::text AS city, d.applies_from_cohort::integer AS cohort,
                      d.doc_number || ' · ' || d.title AS label
                 FROM policy.source_document d WHERE d.status = 'draft' AND ($1::text IS NULL OR d.city::text = $1)
               UNION ALL
               SELECT 'school', h.id, h.city::text, NULL, h.code::text || ' · ' || h.public_name
                 FROM policy.school h WHERE h.review_status = 'draft' AND ($1::text IS NULL OR h.city::text = $1)
               UNION ALL
               SELECT 'admission_line', l.id, h.city::text, l.year::integer,
                      h.code::text || ' · ' || l.batch_code::text || ' · ' || l.track
                 FROM policy.admission_line l JOIN policy.school h ON h.id = l.school_id
                WHERE l.status = 'draft' AND ($1::text IS NULL OR h.city::text = $1)
               UNION ALL
               -- 指标生控制线（迁移 0047）：城市 × 年份 × 批次 × 类别一条；0047 从招录行旧列回填的也是草稿，要两名 staff 重新签字发布
               SELECT 'control_line', k.id, k.city::text, k.year::integer,
                      k.batch_code::text || ' · ' || k.track || ' · ' || k.score::text
                 FROM policy.control_line k WHERE k.status = 'draft' AND ($1::text IS NULL OR k.city::text = $1)
               ORDER BY 1, 3, 5, 2""", city)
        out = []
        for r in rows:
            d = dict(r)
            d["digest"] = await self.pool.fetchval("SELECT policy.object_digest($1, $2)", d["object_type"], d["id"])
            d["approvals"] = await self.pool.fetchval("SELECT policy.approval_count($1, $2)", d["object_type"], d["id"])
            out.append(d)
        return out

    async def expire_current_affairs(self, today) -> int:
        """时政过期下架（迁移 0043）：知识服务身份调库函数，函数写系统审核记录。"""
        return int(await self.pool.fetchval("SELECT kge.expire_current_affairs($1)", today))

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

    # ---------- 内容版本与题目准入（迁移 0039：签字、发布、撤下、准入执行都在库函数里核对 active staff 与摘要） ----------
    @staticmethod
    async def _expect(c, sql: str, args: tuple, expected: str, what: str) -> None:
        if await c.fetchval(sql, *args) != expected:
            raise ReviewError(409, "DIGEST_CHANGED", f"你看到的{what}与当前不一致，请刷新后重新审核")

    async def sign_content_version(self, staff: uuid.UUID, cv: uuid.UUID, decision: str, basis_code: str | None, expected: str) -> uuid.UUID:
        # 库函数在锁住版本行之后比对摘要（看到的就是签的）
        return await self._as_staff(staff, lambda c: c.fetchval(
            "SELECT platform.sign_content_version($1, $2, $3, $4)", cv, decision, expected, basis_code))

    async def publish_content_version(self, staff: uuid.UUID, cv: uuid.UUID, expected: str):
        async def run(c):
            q = ("SELECT platform.content_version_digest($1)", (cv,))
            await self._expect(c, *q, expected, "内容版本")
            at = await c.fetchval("SELECT platform.publish_content_version($1)", cv)
            await self._expect(c, *q, expected, "内容版本")  # 发布函数已锁住版本行；间隙里被改过就整笔回滚
            return at
        return await self._as_staff(staff, run)

    async def retire_content_version(self, staff: uuid.UUID, cv: uuid.UUID, basis_code: str) -> None:
        await self._as_staff(staff, lambda c: c.execute("SELECT platform.retire_content_version($1, $2)", cv, basis_code))

    async def sign_admission(self, staff: uuid.UUID, b: dict[str, Any]) -> uuid.UUID:
        return await self._as_staff(staff, lambda c: c.fetchval(
            "SELECT platform.sign_admission($1::platform.subject, $2, $3::platform.admission, $4, $5, $6, $7, $8, $9, $10)",
            b["subject"], b["item_id"], b["to_admission"], b["decision"], b.get("review_level"), b.get("source_ref"), b.get("source_type"),
            b.get("license_status"), b["expected_digest"], b.get("basis_code")))

    async def change_admission(self, staff: uuid.UUID, review_ids: list[uuid.UUID]) -> tuple[int, Any]:
        """执行一批批准的准入审核（一条不合格整批回滚）；返回（条数，执行前的时刻）——执行记录的时间都不早于它，
        供学习服务按题重算的起点（python -m learning.jobs admission-recompute --since）。"""
        async def run(c):
            start = await c.fetchval("SELECT clock_timestamp()")
            n = await c.fetchval("SELECT platform.change_admission($1::uuid[])", review_ids)
            return int(n), start
        return await self._as_staff(staff, run)

    # ---------- 考情政策（迁移 0041：两名不同 staff 对当前内容批准；签字人取当前账号） ----------
    async def sign_scheme(self, staff: uuid.UUID, scheme: uuid.UUID, decision: str, expected: str) -> int:
        return int(await self._as_staff(staff, lambda c: c.fetchval("SELECT policy.sign_scheme($1, $2, $3)", scheme, decision, expected)))

    async def publish_scheme(self, staff: uuid.UUID, scheme: uuid.UUID, expected: str):
        async def run(c):
            q = ("SELECT policy.object_digest('exam_scheme', $1)", (scheme,))
            await self._expect(c, *q, expected, "方案")
            at = await c.fetchval("SELECT policy.publish_scheme($1)", scheme)
            await self._expect(c, *q, expected, "方案")
            return at
        return await self._as_staff(staff, run)

    async def sign_record(self, staff: uuid.UUID, object_type: str, object_id: uuid.UUID, decision: str, expected: str) -> str:
        """来源文件、招录行、学校：第二名不同 staff 对当前内容批准时，库函数在同一事务里发布。发布被表上的保护触发器拒绝时，
        整笔回滚，签字也不留痕（PR C“第二名批准时发布失败，签字一并回滚”）——这里识别这种情况，给明确提示（PUBLISH_FAILED_UNSIGNED）。"""
        async def run(c):
            q = ("SELECT policy.object_digest($1, $2)", (object_type, object_id))
            await self._expect(c, *q, expected, "内容")
            before = int(await c.fetchval("SELECT policy.approval_count($1, $2)", object_type, object_id))
            required = int(await c.fetchval("SELECT policy.signers_required()"))
            would_publish = decision == "approved" and before + 1 >= required
            try:
                async with c.transaction():  # 保存点：失败时能区分原因，外层事务照样整笔回滚
                    return await c.fetchval("SELECT policy.sign_record($1, $2, $3, $4)", object_type, object_id, decision, expected)
            except asyncpg.PostgresError as e:
                if would_publish and getattr(e, "sqlstate", "") != "23505":
                    err = db_error(e)
                    raise ReviewError(409, "PUBLISH_FAILED_UNSIGNED",
                                      "这是第二名批准，签字时发布被拒，整笔已回滚：你的签字没有保存。请先按下面的原因处理，再重新签字"
                                      + (f"（{err.detail}）" if err.detail else f"（{err.code}）")) from None
                raise
        return await self._as_staff(staff, run)

    # ---------- 官方来源清单（迁移 0048：权限 policy_source_admin 由 DBA 授予；函数再核对 active staff 与权限） ----------
    async def is_source_admin(self, staff: uuid.UUID) -> bool:
        """当前 staff 有没有 policy_source_admin。签字身份只读得到自己的权限行（RLS：staff_id = 当前账号），这里另按账号过滤一次。"""
        return bool(await self._as_staff(staff, lambda c: c.fetchval(
            "SELECT EXISTS (SELECT 1 FROM platform.staff_permission WHERE staff_id = $1 AND permission = 'policy_source_admin')", staff)))

    async def add_source_domain(self, staff: uuid.UUID, host: str, note: str) -> str:
        return await self._source_change(staff, "SELECT policy.add_source_domain($1, $2)", host, note)

    async def retire_source_domain(self, staff: uuid.UUID, host: str) -> str:
        return await self._source_change(staff, "SELECT policy.retire_source_domain($1)", host)

    async def _source_change(self, staff: uuid.UUID, sql: str, *args) -> str:
        """主机名不合格（22023）给固定说明（不回显库里的消息）；已在清单里（23505）409 CONFLICT；已下线（23514）带库里写好的说明。"""
        try:
            return await self._as_staff(staff, lambda c: c.fetchval(sql, *args))
        except ReviewError as e:
            if e.code == "INVALID_PARAMETER":
                raise ReviewError(422, "INVALID_PARAMETER", "主机名不合格：只收域名（至少两段），不收 IP、通配符、端口、路径或只有公共后缀的主机；"
                                                            "说明须为 1–64 个字") from None
            raise

    # ---------- 编译页面签字（迁移 0038；PR A 待人确认 1 的默认：审核人在审核台逐个签） ----------
    async def sign_page(self, staff: uuid.UUID, page_id: str, page_commit: str, decision: str, risk: str, basis_code: str) -> uuid.UUID:
        return await self._as_staff(staff, lambda c: c.fetchval(
            "SELECT kge.sign_page_review($1, $2, $3, $4, $5)", page_id, page_commit, decision, risk, basis_code))

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


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


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
