"""断言（spec 设计要点 2：“同一学生模型”的可测定义）。

每条断言只读：在 READ ONLY 事务里查询，隔离断言另外 SET LOCAL ROLE starbuddy_bff 并设置 app.account_id（与 BFF 同一口径）。
同一套函数既用于合成联调（CI），也用于真实内测（RUNBOOK.md：只读连接、学生名单只有 ID、结果只入汇总）。
返回值只含 ID、计数与编码，不含作答、答案、维度分或模型原文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import asyncpg

CORE_SUBJECTS = ("aya", "wenli", "zhixing")
SUBJECT_TABLE_PREFIX = r"^(aya|wenli|zhixing|echo)_"
# 个人层：过渡期 platform.* 整体是个人层真相源（ADR 之前，CLAUDE.md 规则 2）；insight.* 只豁免白名单里的表
# （判断账本 0010/0032/0033/0034 与学生自述、家长观察等分表事实 0022/0025）。insight 里新增的表不在白名单时按“个人层以外”扫描。
PERSONAL_SCHEMA = "platform"
INSIGHT_PERSONAL_TABLES = (
    "claim", "claim_history", "dispute", "intervention_log", "exam_prediction", "exam_reconciliation",
    "self_report", "guardian_report", "exam_record", "onboarding_progress", "behavior_event", "confidence_probe",
)
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
# 标识学生、其作答或家庭的列名：出现在个人层以外的表上即为“第二份学生事实”的结构信号
STUDENT_KEY_COLUMNS = ("student_id", "student_ref", "attempt_id", "evidence_id", "grade_result_id", "household_id",
                       "guardian_account_id", "account_id", "user_id", "learner_id")
UUID_RE = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
REGISTRY = "platform.personal_table_registry"  # 个人表清单（迁移 0035，草稿）
LEARNING_TABLES = ("evidence_event", "attempt", "grade_result", "skill_observation", "error_attribution")
# 个人表清单不存在时（0035 未执行的库）隔离断言回退到这 6 张表
FALLBACK_ISOLATION_TABLES = (("platform", "student_profile", "id = $1"),) + tuple(
    ("platform", t, "student_id = $1") for t in LEARNING_TABLES)
GRADE_FIELDS = ("evidence_ids", "confidence", "source_version", "model_version", "human_review_required")
METERING_TABLES = ("usage_reservation", "usage_ledger", "practice_log")
PERSONAL_COUNTED = LEARNING_TABLES + ("ingest_request", "evidence_registry")


def qi(name: str) -> str:
    """SQL 标识符转义（表名、列名都来自系统目录，仍按规则转义）"""
    return '"' + name.replace('"', '""') + '"'


def is_personal(schema: str, table: str) -> bool:
    return schema == PERSONAL_SCHEMA or (schema == "insight" and table in INSIGHT_PERSONAL_TABLES)


@dataclass(frozen=True)
class Member:
    student_id: str
    account_id: str
    household_id: str
    guardian_ids: tuple[str, ...]


@dataclass
class Population:
    members: list[Member]
    unlinked_guardian_ids: list[str] = field(default_factory=list)
    # 家长 → 其全部生效绑定的孩子（含名单外的孩子）；不给时按名单推出
    guardian_children: dict[str, set[str]] = field(default_factory=dict)

    @property
    def student_ids(self) -> list[str]:
        return [m.student_id for m in self.members]

    def children_of(self, guardian_id: str) -> set[str]:
        if guardian_id in self.guardian_children:
            return set(self.guardian_children[guardian_id])
        return {m.student_id for m in self.members if guardian_id in m.guardian_ids}

    @property
    def guardians(self) -> list[str]:
        return sorted({g for m in self.members for g in m.guardian_ids})


@dataclass
class Check:
    name: str
    passed: bool
    detail: dict

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


async def registry_available(conn) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", REGISTRY))


async def load_population(conn, student_ids: list[str]) -> Population:
    """真实内测用：按学生 ID 名单从库里读账号、家庭与生效的家长绑定（只读）；家长的应见集合取其全部生效绑定。"""
    async with conn.transaction(readonly=True):
        rows = await conn.fetch(
            """SELECT s.id::text AS sid, s.account_id::text AS acct, s.household_id::text AS hh,
                      coalesce(array_agg(l.guardian_account_id::text ORDER BY l.guardian_account_id)
                               FILTER (WHERE l.status = 'active'), '{}') AS guardians
                 FROM platform.student_profile s LEFT JOIN platform.guardian_student_link l ON l.student_id = s.id
                WHERE s.id = ANY($1::uuid[]) GROUP BY s.id ORDER BY s.id""", student_ids)
        members = [Member(r["sid"], r["acct"], r["hh"], tuple(r["guardians"])) for r in rows]
        guardians = sorted({g for m in members for g in m.guardian_ids})
        links = await conn.fetch(
            """SELECT guardian_account_id::text AS g, student_id::text AS s FROM platform.guardian_student_link
                WHERE status = 'active' AND guardian_account_id = ANY($1::uuid[])""", guardians)
    children: dict[str, set[str]] = {g: set() for g in guardians}
    for r in links:
        children[r["g"]].add(r["s"])
    return Population(members, guardian_children=children)


# ---------------- 1. 同一 student_id 下三科事件并存、互不覆盖 ----------------
async def check_coexistence(conn, pop: Population, expected_evidence: dict | None = None,
                            required: tuple[str, ...] = CORE_SUBJECTS) -> Check:
    """expected_evidence：{(student_id, subject): {evidence_id…}}（合成联调由回放摘要给出）；真实内测传 None，只查并存。"""
    async with conn.transaction(readonly=True):
        rows = await conn.fetch(
            """SELECT student_id::text AS sid, subject::text AS subject, array_agg(DISTINCT evidence_id::text) AS ids
                 FROM platform.evidence_event WHERE student_id = ANY($1::uuid[]) GROUP BY 1, 2""", pop.student_ids)
    have: dict[tuple[str, str], set[str]] = {(r["sid"], r["subject"]): set(r["ids"]) for r in rows}
    missing_subjects = sorted(f"{m.student_id}:{s}" for m in pop.members for s in required if (m.student_id, s) not in have)
    unexpected_subjects = sorted(f"{sid}:{s}" for (sid, s) in have if s not in CORE_SUBJECTS)
    lost, foreign = [], []
    if expected_evidence is not None:
        for (sid, subj), ids in sorted(expected_evidence.items()):
            gone = ids - have.get((sid, subj), set())
            if gone:
                lost.append({"student_id": sid, "subject": subj, "missing": len(gone)})
        for (sid, subj), ids in sorted(have.items()):
            extra = ids - expected_evidence.get((sid, subj), set())
            if extra:
                foreign.append({"student_id": sid, "subject": subj, "unexpected": len(extra)})
    ok = bool(pop.members) and not missing_subjects and not unexpected_subjects and not lost and not foreign
    return Check("coexistence", ok, {
        "students": len(pop.members), "students_with_all_core_subjects":
            sum(1 for m in pop.members if all((m.student_id, s) in have for s in required)),
        "missing_subjects": missing_subjects, "unexpected_subjects": unexpected_subjects,
        "evidence_lost": lost, "evidence_unexpected": foreign})


# ---------------- 2. 各科私有表无学生事实副本（schema 扫描） ----------------
async def _needles(conn, pop: Population) -> list[str]:
    """查找值：本批学生、其账号、家庭、家长账号，以及这批学生的全部证据 ID 与作答 ID。"""
    ids = set(pop.student_ids) | {m.account_id for m in pop.members} | {m.household_id for m in pop.members} | set(pop.guardians)
    ids |= {r[0] for r in await conn.fetch(
        "SELECT DISTINCT evidence_id::text FROM platform.evidence_event WHERE student_id = ANY($1::uuid[])", pop.student_ids)}
    ids |= {r[0] for r in await conn.fetch(
        "SELECT DISTINCT attempt_id::text FROM platform.attempt WHERE student_id = ANY($1::uuid[])", pop.student_ids)}
    return sorted(i for i in ids if i)


async def check_no_private_copies(conn, pop: Population) -> Check:
    async with conn.transaction(readonly=True):
        tables = await conn.fetch(
            """SELECT n.nspname AS schema, c.relname AS name
                 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition
                  AND n.nspname <> ALL($1::text[]) AND n.nspname NOT LIKE 'pg_temp%' AND n.nspname NOT LIKE 'pg_toast%'
                ORDER BY 1, 2""", list(SYSTEM_SCHEMAS))
        cols = await conn.fetch(
            """SELECT table_schema AS schema, table_name AS name, column_name AS col
                 FROM information_schema.columns WHERE table_schema <> ALL($1::text[]) ORDER BY 1, 2, ordinal_position""",
            list(SYSTEM_SCHEMAS))
        schemas = sorted({t["schema"] for t in tables})
        outside = {(t["schema"], t["name"]) for t in tables if not is_personal(t["schema"], t["name"])}
        # (0) 个人层里以学科名开头的表（aya_ / wenli_ / zhixing_ / echo_）：各科私有表不得放进平台个人层
        subject_named = sorted(f"{t['schema']}.{t['name']}" for t in tables
                               if t["schema"] in (PERSONAL_SCHEMA, "insight") and re.match(SUBJECT_TABLE_PREFIX, t["name"]))
        # (a) 结构：个人层以外的表不得有学生键列
        keyed = sorted(f"{c['schema']}.{c['name']}.{c['col']}" for c in cols
                       if (c["schema"], c["name"]) in outside and c["col"] in STUDENT_KEY_COLUMNS)
        # (b) 取值：个人层以外的表的任何一列（转成文本）里出现的 UUID 不得是本批学生相关的 ID
        needles = await _needles(conn, pop)
        by_table: dict[tuple[str, str], list[str]] = {}
        for c in cols:
            if (c["schema"], c["name"]) in outside:
                by_table.setdefault((c["schema"], c["name"]), []).append(c["col"])
        hits = []
        if needles:
            for (schema, name), names in sorted(by_table.items()):
                row_text = "lower(concat_ws('|', " + ", ".join(f"{qi(n)}::text" for n in names) + "))"
                n = await conn.fetchval(
                    f"""SELECT count(*) FROM {qi(schema)}.{qi(name)} t
                         WHERE EXISTS (SELECT 1 FROM regexp_matches({row_text}, '{UUID_RE}', 'g') m WHERE m[1] = ANY($1::text[]))""",
                    needles)
                if n:
                    hits.append({"table": f"{schema}.{name}", "rows": n})
        # (c) 个人层内：带 student_id 的基表都登记在个人表清单（0035）里，没有未登记的副本；清单不存在时标“待 0035”并跳过
        if await registry_available(conn):
            registry_status = "checked"
            unregistered = [r["t"] for r in await conn.fetch(
                f"""SELECT DISTINCT c.table_schema || '.' || c.table_name AS t
                     FROM information_schema.columns c JOIN pg_class k ON k.relname = c.table_name
                     JOIN pg_namespace n ON n.oid = k.relnamespace AND n.nspname = c.table_schema
                    WHERE c.table_schema IN ('platform', 'insight') AND c.column_name = 'student_id'
                      AND k.relkind IN ('r', 'p') AND NOT k.relispartition
                      AND NOT EXISTS (SELECT 1 FROM {REGISTRY} r WHERE r.nspname = c.table_schema AND r.relname = c.table_name)
                    ORDER BY 1""")]
        else:
            registry_status, unregistered = "pending_0035（个人表清单未建，跳过）", []
    ok = bool(needles) and not subject_named and not keyed and not hits and not unregistered
    return Check("no_private_copies", ok, {
        "schemas": schemas, "tables_outside_personal_layer": len(outside), "needles": len(needles),
        "subject_named_tables_in_personal_layer": subject_named,
        "student_key_columns_outside_personal_layer": keyed, "student_values_outside_personal_layer": hits,
        "registry": registry_status, "personal_tables_not_in_registry": unregistered})


# ---------------- 2b. 家长绑定都有来历 ----------------
async def check_guardian_links_backed(conn, pop: Population) -> Check:
    """本批学生的生效家长绑定里，既不是“学生所在家庭的创建人”、也没有对应“已使用邀请”的条数（只输出条数）。
    局限：平台没有“家庭成员”表，成员身份只能由创建人或被邀请推出；邀请本身是否发给了对的人不在库里（见 RUNBOOK 第 3 节）。"""
    async with conn.transaction(readonly=True):
        n = await conn.fetchval(
            """SELECT count(*) FROM platform.guardian_student_link l
                 JOIN platform.student_profile s ON s.id = l.student_id
                 JOIN platform.household h ON h.id = s.household_id
                WHERE l.status = 'active' AND l.student_id = ANY($1::uuid[])
                  AND NOT (l.granted_via = 'household_creator' AND h.created_by_account = l.guardian_account_id)
                  AND NOT (l.granted_via = 'invite' AND EXISTS (
                        SELECT 1 FROM platform.guardian_invite i
                         WHERE i.student_id = l.student_id AND i.used_by_account = l.guardian_account_id))""", pop.student_ids)
    return Check("guardian_links_backed", n == 0, {"unbacked_active_links": n})


# ---------------- 3. 判分溯源：每条 grade 结果带五个字段 ----------------
def check_grade_envelopes(records: list[dict]) -> Check:
    """契约边界：适配器返回给学习服务的每条 grade 结果（RecordingTransport 留在内存里的响应体）。"""
    bad = []
    n = 0
    for r in records:
        if r["method"] != "grade" or r["status"] != 200:
            continue
        n += 1
        b = r["body"] or {}
        problems = [f for f in GRADE_FIELDS if f not in b]
        if "evidence_ids" in b and not (isinstance(b["evidence_ids"], list) and b["evidence_ids"]):
            problems.append("evidence_ids_empty")
        if "confidence" in b and not (isinstance(b["confidence"], (int, float)) and 0 <= b["confidence"] <= 1):
            problems.append("confidence_range")
        if "human_review_required" in b and not isinstance(b["human_review_required"], bool):
            problems.append("human_review_required_type")
        for f in ("source_version", "model_version"):
            if f in b and not (isinstance(b[f], str) and b[f]):
                problems.append(f"{f}_empty")
        if problems:
            bad.append({"index": n, "problems": problems})
    return Check("grade_envelope_provenance", n > 0 and not bad, {"grade_responses": n, "incomplete": bad})


async def check_grade_rows(conn, pop: Population) -> Check:
    """落库侧：本批学生的每条 grade_result 五个字段非空，evidence_ids 全部是同一学生、同一学科的证据。"""
    async with conn.transaction(readonly=True):
        rows = await conn.fetch(
            """SELECT g.id::text AS id, g.subject::text AS subject,
                      (g.confidence IS NOT NULL AND g.source_version IS NOT NULL AND g.model_version IS NOT NULL
                       AND g.human_review_required IS NOT NULL AND cardinality(g.evidence_ids) >= 1) AS complete,
                      (SELECT count(*) FROM unnest(g.evidence_ids) x
                        WHERE NOT EXISTS (SELECT 1 FROM platform.evidence_event e
                                           WHERE e.evidence_id = x AND e.student_id = g.student_id AND e.subject = g.subject)) AS dangling
                 FROM platform.grade_result g WHERE g.student_id = ANY($1::uuid[])""", pop.student_ids)
    incomplete = sum(1 for r in rows if not r["complete"])
    dangling = sum(1 for r in rows if r["dangling"])
    by_subject: dict[str, int] = {}
    for r in rows:
        by_subject[r["subject"]] = by_subject.get(r["subject"], 0) + 1
    return Check("grade_rows_provenance", bool(rows) and not incomplete and not dangling,
                 {"grade_results": len(rows), "by_subject": dict(sorted(by_subject.items())),
                  "incomplete": incomplete, "evidence_not_same_student_subject": dangling})


# ---------------- 4. 整轮重跑：事件与计量零新增 ----------------
async def snapshot_counts(conn, pop: Population) -> dict:
    async with conn.transaction(readonly=True):
        out = {}
        for t in PERSONAL_COUNTED:
            out[t] = await conn.fetchval(f"SELECT count(*) FROM platform.{t} WHERE student_id = ANY($1::uuid[])", pop.student_ids)
        out["grade_dimension"] = await conn.fetchval(
            """SELECT count(*) FROM platform.grade_dimension d JOIN platform.grade_result g ON g.id = d.grade_result_id
                WHERE g.student_id = ANY($1::uuid[])""", pop.student_ids)
        for t in METERING_TABLES:
            out[t] = await conn.fetchval(f"SELECT count(*) FROM platform.{t} WHERE student_id = ANY($1::uuid[])", pop.student_ids)
    return out


def check_rerun(before: dict, after: dict, *, replayed: int, total: int, adapter_calls: int, first_run_keys_ok: bool) -> Check:
    delta = {k: after[k] - before[k] for k in sorted(before) if after[k] != before[k]}
    ok = not delta and replayed == total and adapter_calls == 0 and first_run_keys_ok
    return Check("rerun_zero_new_rows", ok, {"counts": dict(sorted(before.items())), "delta": delta,
                                              "rerun_requests": total, "replayed": replayed,
                                              "adapter_calls_during_rerun": adapter_calls,
                                              "every_request_has_usage_id_and_idempotency_key": first_run_keys_ok})


# ---------------- 5. 跨学生、跨家庭隔离（RLS 角色查询） ----------------
async def isolation_tables(conn) -> tuple[list[tuple[str, str, str]], str]:
    """逐表比对的表：个人表清单（0035）里的全部表与其“按学生过滤”的谓词；清单不存在时回退 6 张并标注。"""
    if await registry_available(conn):
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(f"SELECT nspname::text AS s, relname::text AS t, student_predicate AS p FROM {REGISTRY} ORDER BY 1, 2")
        return [(r["s"], r["t"], r["p"]) for r in rows], "registry"
    return list(FALLBACK_ISOLATION_TABLES), "fallback_6_tables（个人表清单未建，待 0035）"


def _per_student_sql(schema: str, table: str, predicate: str) -> str:
    return (f"SELECT q.sid::text AS sid, (SELECT count(*) FROM {qi(schema)}.{qi(table)} WHERE {predicate.replace('$1', 'q.sid')}) AS n "
            "FROM unnest($1::uuid[]) AS q(sid)")


async def _counts(conn, sql: str, ids: list[str]) -> dict[str, int] | None:
    """在当前事务里按学生计数；没有权限（含只有部分列权限）返回 None，只回滚到保存点。"""
    try:
        async with conn.transaction():
            return {r["sid"]: r["n"] for r in await conn.fetch(sql, ids)}
    except asyncpg.InsufficientPrivilegeError:
        return None


async def _counts_as(conn, sqls: dict, ids: list[str], account_id: str | None, as_bff: bool) -> dict:
    """一个只读事务里跑完所有表：as_bff 时以 starbuddy_bff 身份、app.account_id = account_id（None = 不调用 set_config）。"""
    async with conn.transaction(readonly=True):
        if as_bff:
            await conn.execute("SET LOCAL ROLE starbuddy_bff")
            if account_id is not None:
                await conn.execute("SELECT set_config('app.account_id', $1, true)", account_id)
        return {key: await _counts(conn, sql, ids) for key, sql in sqls.items()}


async def _visible_students(conn, account_id: str | None) -> dict[str, set[str]]:
    """BFF 视角能看到的学生（不限名单）：档案与 5 张学习数据表，用来发现名单外学生的泄漏。"""
    async with conn.transaction(readonly=True):
        await conn.execute("SET LOCAL ROLE starbuddy_bff")
        if account_id is not None:
            await conn.execute("SELECT set_config('app.account_id', $1, true)", account_id)
        out = {"student_profile": {r[0] for r in await conn.fetch("SELECT id::text FROM platform.student_profile")}}
        for t in LEARNING_TABLES:
            out[t] = {r[0] for r in await conn.fetch(f"SELECT DISTINCT student_id::text FROM platform.{t}")}
    return out


async def check_isolation(conn, pop: Population) -> Check:
    """以 BFF 口径逐表比对：对名单里每名学生，查看者按该表的谓词能看到的行数；不该看到的学生行数 > 0 即违规。
    应见集合：学生本人 = 自己；家长 = 其全部生效绑定的孩子；按家庭关联的表（household 等）再放宽到同一家庭；
    未绑定家长、未登录（空账号与完全不设账号两种）= 空。另查名单外学生的泄漏（档案与 5 张学习数据表）。"""
    tables, source = await isolation_tables(conn)
    ids = pop.student_ids
    ours = set(ids)
    hh = {m.student_id: m.household_id for m in pop.members}
    sqls = {(s, t): (_per_student_sql(s, t, p), "household" in p) for s, t, p in tables}
    base = await _counts_as(conn, {k: v[0] for k, v in sqls.items()}, ids, None, as_bff=False)
    baseline = {k: v for k, v in base.items() if v is not None}
    skipped = sorted(".".join(k) for k, v in base.items() if v is None)
    bff_readable = {key for key in baseline
                    if await conn.fetchval("SELECT has_any_column_privilege('starbuddy_bff', $1, 'SELECT')", f"{qi(key[0])}.{qi(key[1])}")}

    viewers: list[tuple[str, str | None, set[str], set[str]]] = []  # (类别, 账号, 应见学生, 放宽到同家庭时的应见学生)
    for m in pop.members:
        same_hh = {s for s in ours if hh[s] == m.household_id}
        viewers.append(("student", m.account_id, {m.student_id}, same_hh))
    for g in pop.guardians:
        kids = pop.children_of(g)
        kid_hh = {hh[s] for s in kids & ours}
        viewers.append(("guardian", g, kids, kids | {s for s in ours if hh[s] in kid_hh}))
    for g in pop.unlinked_guardian_ids:
        viewers.append(("unlinked_guardian", g, set(), set()))
    viewers.append(("anonymous_empty", "", set(), set()))
    viewers.append(("anonymous_unset", None, set(), set()))

    violations, not_comparable = [], set()
    readable_sqls = {k: sqls[k][0] for k in sorted(bff_readable)}
    for kind, acct, allowed, allowed_hh in viewers:
        seen_counts = await _counts_as(conn, readable_sqls, ids, acct, as_bff=True)
        for key in sorted(bff_readable):
            by_household = sqls[key][1]
            got = seen_counts[key]
            if got is None:  # 只有部分列权限、谓词里的列读不到：记下，不算通过也不算违规
                not_comparable.add(".".join(key))
                continue
            ok_set = allowed_hh if by_household else allowed
            leaked = sorted(s for s, n in got.items() if n and s not in ok_set)
            if leaked:
                violations.append({"viewer": kind, "account_id": acct, "table": ".".join(key), "students_leaked": len(leaked)})
        seen = await _visible_students(conn, acct)
        for t, sids in seen.items():
            outsiders = sids - ours - allowed  # 名单外、也不是自己孩子的学生
            if outsiders:
                violations.append({"viewer": kind, "account_id": acct, "table": f"platform.{t}", "outsiders_visible": len(outsiders)})
    multi = sum(1 for g in pop.guardians if len(pop.children_of(g) & ours) > 1)
    ok = not violations and bool(bff_readable)
    return Check("isolation_rls", ok, {
        "table_source": source, "tables_compared": len(bff_readable), "tables_bff_cannot_read": len(baseline) - len(bff_readable),
        "tables_skipped_no_privilege": skipped,
        "tables_not_comparable_column_privilege": sorted(not_comparable), "students_checked": len(pop.members), "guardians_checked": len(pop.guardians),
        "guardians_with_multiple_children": multi, "unlinked_guardians_checked": len(pop.unlinked_guardian_ids),
        "anonymous_variants": ["empty_account", "unset_account"], "violations": violations})


# ---------------- 节点查询（证据聚合；KGE-04 第二步前没有掌握写库） ----------------
async def node_summary(conn, pop: Population) -> list[dict]:
    async with conn.transaction(readonly=True):
        rows = await conn.fetch(
            """WITH ev AS (
                 SELECT student_id, subject::text AS subject, coalesce(skill_local_id::text, '-') AS node,
                        count(DISTINCT evidence_id) AS evidence
                   FROM platform.evidence_event WHERE student_id = ANY($1::uuid[]) GROUP BY 1, 2, 3),
               ob AS (
                 SELECT student_id, subject::text AS subject, skill_local_id::text AS node,
                        count(*) FILTER (WHERE outcome = 'demonstrated') AS demonstrated,
                        count(*) FILTER (WHERE outcome = 'not_demonstrated') AS not_demonstrated,
                        count(*) FILTER (WHERE outcome NOT IN ('demonstrated', 'not_demonstrated')) AS other
                   FROM platform.skill_observation WHERE student_id = ANY($1::uuid[]) GROUP BY 1, 2, 3),
               me AS (
                 SELECT student_id, subject::text AS subject, skill_local_id::text AS node, state::text AS state
                   FROM platform.mastery_estimate WHERE student_id = ANY($1::uuid[]) AND is_current)
               SELECT coalesce(ev.student_id, ob.student_id)::text AS student_id, coalesce(ev.subject, ob.subject) AS subject,
                      coalesce(ev.node, ob.node) AS node, coalesce(ev.evidence, 0) AS evidence,
                      coalesce(ob.demonstrated, 0) AS demonstrated, coalesce(ob.not_demonstrated, 0) AS not_demonstrated,
                      coalesce(ob.other, 0) AS other, me.state
                 FROM ev FULL JOIN ob USING (student_id, subject, node)
                 LEFT JOIN me ON me.student_id = coalesce(ev.student_id, ob.student_id)
                             AND me.subject = coalesce(ev.subject, ob.subject) AND me.node = coalesce(ev.node, ob.node)
                ORDER BY 1, 2, 3""", pop.student_ids)
    return [{"student_id": r["student_id"], "subject": r["subject"], "node_id": r["node"], "evidence_count": r["evidence"],
             "observations_demonstrated": r["demonstrated"], "observations_not_demonstrated": r["not_demonstrated"],
             "observations_other": r["other"],
             "status": r["state"] or "evidence_only"} for r in rows]


async def event_counts(conn, pop: Population) -> list[dict]:
    """事件计数 × 科目 × 事件类型（来源方法 + 作答情境；复测 = grade + delayed_retest）。"""
    async with conn.transaction(readonly=True):
        rows = await conn.fetch(
            """SELECT student_id::text AS sid, subject::text AS subject, source_method AS method,
                      coalesce(evidence_kind, '-') AS evidence_kind, count(DISTINCT evidence_id) AS n
                 FROM platform.evidence_event WHERE student_id = ANY($1::uuid[]) GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4""",
            pop.student_ids)
    return [{"student_id": r["sid"], "subject": r["subject"], "event_type": f"{r['method']}/{r['evidence_kind']}", "count": r["n"]}
            for r in rows]
