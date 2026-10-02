"""掌握 v0 的读取层（KGE-04 第二步 PR 2，specs/kge-04-mastery-step2/spec.md 第 2 节；设计正文 specs/mastery-estimate-sprint4/spec.md 第 1、3、11 节）。

从 platform.attempt / evidence_event / grade_result / error_attribution / platform.item 与 kge.item_tag 组装第一步的 Evidence：
- 计入口径（第一步 #23）：只读“作答 + 它的作答证据”，有没有当前判分（grade_result 未被取代的那条）由 has_grade 表达，
  过程原文证据、探针、行为事件不是作答证据，不进来；
- 准入取证据自身与题目当前登记里更严的那个；
- 打标按证据自身的地图版本（evidence_event.map_version_id）解析 kge.item_tag，再经 kge.id_migration 映射到当前已发布版本：
  rename / merge 延续，split 的新节点重新判定（旧证据不归属），retire 不再计算；证据没有地图版本时不猜，按未打标处理；
- 题型来源未定（第一步 #4）：读取层留注入口 item_type_of，生产默认没有题型（真实数据到不了“稳定”，与第一步偏差 8 一致）。
另有能力视图的读时汇总（skill_link 权重，不写库、不点亮）与只读查询。本模块不写任何表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from .evidence import Evidence, Unit
from .gates import GATES, RANK
from .write import EvidenceFacts, StoredEstimate

COMPUTABLE_KINDS = ("kp", "ability_kp", "viewpoint")       # 只有这三类节点带认知层次、参与掌握计算（迁移 0011）
ADMISSION_RANK = {"admitted": 0, "pending_review": 1, "source_unverified": 2, "rejected": 3}
ItemTypeOf = Callable[[str, str], "str | None"]


def no_item_type(subject: str, item_id: str) -> str | None:  # noqa: ARG001 — 题型来源待人确认 #4
    return None


def stricter(*admissions: str | None) -> str:
    """准入取更严的那个；题目未登记（不应发生，外键保证）按未核实。"""
    vals = [a or "source_unverified" for a in admissions]
    return max(vals, key=lambda a: ADMISSION_RANK.get(a, 3))


# ---------- 地图：当前已发布版本、版本链、ID 迁移 ----------
@dataclass(frozen=True)
class MapContext:
    subject: str
    current_id: str | None
    nodes: Mapping[str, tuple[str, ...]]                   # 当前版本里可计算的有效节点 → 认知层次
    chain: tuple[str, ...]                                # 发布过的版本（已发布、已撤下），按发布时间
    migrations: Mapping[str, Mapping[str, tuple[tuple[str | None, str], ...]]] = field(default_factory=dict)

    def unit_ok(self, node_id: str, level: str) -> bool:
        return level in self.nodes.get(node_id, ())


def resolve_node(ctx: MapContext, version: str | None, node_id: str) -> tuple[str | None, str]:
    """把某地图版本里的节点 ID 映射到当前版本。返回 (当前 ID 或 None, 状态)：
    ok（延续：同 ID、改名、合并）/ retired（撤销）/ split（拆分、人工迁移或一对多：新节点重新判定）/ no_version / unknown_version。
    只走证据版本之后、到当前版本为止的迁移记录（按发布时间）。"""
    if version is None:
        return None, "no_version"
    if version not in ctx.chain or ctx.current_id not in ctx.chain:
        return None, "unknown_version"
    start, end = ctx.chain.index(version), ctx.chain.index(ctx.current_id)
    if start > end:  # 证据版本比当前版本还新（当前版本被回退）：不猜
        return None, "unknown_version"
    cur = node_id
    for v in ctx.chain[start + 1:end + 1]:
        rows = ctx.migrations.get(v, {}).get(cur)
        if not rows:
            continue
        rules = {r for _, r in rows}
        news = {n for n, _ in rows if n}
        if "retire" in rules:
            return None, "retired"
        # manual（人工迁移）按拆分处理：新节点重新判定，不延续旧证据（审查意见 8，最保守）
        if "split" in rules or "manual" in rules or len(news) != 1:
            return None, "split"
        cur = news.pop()
    return cur, "ok"


async def load_map(conn, subject: str) -> MapContext:
    cur = await conn.fetchval(
        "SELECT id::text FROM kge.map_version WHERE subject = $1::platform.subject AND status = 'published'", subject)
    chain = [r[0] for r in await conn.fetch(
        """SELECT id::text FROM kge.map_version WHERE subject = $1::platform.subject AND published_at IS NOT NULL
            ORDER BY published_at, id""", subject)]
    nodes: dict[str, tuple[str, ...]] = {}
    if cur is not None:
        for r in await conn.fetch(
                """SELECT id::text, cognitive_levels FROM kge.knowledge_node
                    WHERE map_version_id = $1::uuid AND status = 'active' AND kind = ANY($2::text[])""",
                cur, list(COMPUTABLE_KINDS)):
            nodes[r[0]] = tuple(r[1] or ())
    mig: dict[str, dict[str, list[tuple[str | None, str]]]] = {}
    for r in await conn.fetch(
            "SELECT map_version_id::text, old_id, new_id::text, rule FROM kge.id_migration WHERE subject = $1::platform.subject",
            subject):
        mig.setdefault(r[0], {}).setdefault(r[1], []).append((r[2], r[3]))
    return MapContext(subject, cur, nodes, tuple(chain),
                      {v: {k: tuple(sorted(x, key=lambda t: (t[0] or "", t[1]))) for k, x in d.items()} for v, d in mig.items()})


# ---------- 证据 ----------
@dataclass(frozen=True)
class EvidenceRow:
    """一次作答与它的作答证据、当前判分、错因、题目准入与打标（只含 ID、编码、数字与时间，不含作答内容）。"""
    attempt_id: str
    evidence_id: str
    item_id: str
    occurred_at: datetime
    recorded_at: datetime
    hint_level: int | None
    self_report: str | None
    source_method: str
    evidence_kind: str | None
    evidence_level: str | None
    map_version_id: str | None
    admission: str                      # 已取更严者
    grade_id: str | None
    verdict: str | None
    grade_confidence: float | None
    human_review_required: bool
    review_reasons: tuple[str, ...]
    semantics: str | None
    in_diagnosis: bool
    error_category: str | None


@dataclass(frozen=True)
class Tag:
    main_node_id: str
    node_ids: tuple[str, ...]
    cognitive_level: str | None
    family_id: str | None
    review_status: str


_EVIDENCE_SQL = """
SELECT a.attempt_id::text, e.evidence_id::text, a.item_id::text, a.occurred_at, e.recorded_at, a.hint_level_used, a.self_report,
       e.source_method, e.evidence_kind, e.evidence_level, e.map_version_id::text,
       e.admission::text AS ev_adm, i.admission::text AS item_adm,
       g.id::text AS grade_id, g.verdict, g.confidence AS grade_conf, g.human_review_required, g.review_reasons::text[] AS review_reasons,
       g.semantics::text AS semantics,
       (g.id IS NOT NULL AND EXISTS (SELECT 1 FROM platform.diagnosis_step d WHERE d.grade_result_id = g.id)) AS in_diagnosis,
       (SELECT x.cross_subject_category::text FROM platform.error_attribution x
         WHERE x.student_id = a.student_id AND x.subject = a.subject AND x.superseded_by IS NULL AND e.evidence_id = ANY (x.evidence_ids)
         ORDER BY x.created_at DESC, x.id LIMIT 1) AS error_category
  FROM platform.attempt a
  JOIN platform.evidence_event e ON e.evidence_id = a.response_evidence_id AND e.student_id = a.student_id AND e.subject = a.subject
  LEFT JOIN platform.item i ON i.subject = a.subject AND i.item_id = a.item_id
  LEFT JOIN LATERAL (SELECT * FROM platform.grade_result g0
                      WHERE g0.attempt_id = a.attempt_id AND g0.attempt_occurred_at = a.occurred_at
                        AND g0.student_id = a.student_id AND g0.superseded_by IS NULL
                      ORDER BY g0.created_at DESC, g0.id LIMIT 1) g ON true
 WHERE a.student_id = $1::uuid AND a.subject = $2::platform.subject {extra}
 ORDER BY e.recorded_at, e.evidence_id
"""


def _row(r: Mapping[str, Any]) -> EvidenceRow:
    return EvidenceRow(
        attempt_id=r["attempt_id"], evidence_id=r["evidence_id"], item_id=r["item_id"], occurred_at=r["occurred_at"],
        recorded_at=r["recorded_at"], hint_level=r["hint_level_used"], self_report=r["self_report"],
        source_method=r["source_method"], evidence_kind=r["evidence_kind"], evidence_level=r["evidence_level"],
        map_version_id=r["map_version_id"], admission=stricter(r["ev_adm"], r["item_adm"]), grade_id=r["grade_id"],
        verdict=r["verdict"], grade_confidence=None if r["grade_conf"] is None else float(r["grade_conf"]),
        human_review_required=bool(r["human_review_required"]), review_reasons=tuple(r["review_reasons"] or ()),
        semantics=r["semantics"], in_diagnosis=bool(r["in_diagnosis"]), error_category=r["error_category"])


async def load_rows(conn, student: str, subject: str, *, attempt_ids: Sequence[str] | None = None) -> list[EvidenceRow]:
    if attempt_ids is None:
        rows = await conn.fetch(_EVIDENCE_SQL.format(extra=""), student, subject)
    else:
        rows = await conn.fetch(_EVIDENCE_SQL.format(extra="AND a.attempt_id = ANY($3::text[]::uuid[])"), student, subject,
                                list(attempt_ids))
    return [_row(r) for r in rows]


async def load_tags(conn, subject: str, item_ids: Iterable[str]) -> dict[tuple[str, str], Tag]:
    """(地图版本, 题目) → 打标。按证据自身的版本取，所以读全部版本。"""
    ids = sorted(set(item_ids))
    if not ids:
        return {}
    rows = await conn.fetch(
        """SELECT map_version_id::text, item_id::text, main_node_id::text, node_ids::text[], cognitive_level, family_id::text,
                  tag_review_status
             FROM kge.item_tag WHERE subject = $1::platform.subject AND item_id = ANY($2::text[])""", subject, ids)
    return {(r[0], r[1]): Tag(r[2], tuple(r[3] or ()), r[4], r[5], r[6]) for r in rows}


@dataclass(frozen=True)
class Mapped:
    """一条证据在当前地图上的归属：主节点、次要节点、层次与题目族；main 为空即不归属任何单元（编码 untagged）。"""
    main: str | None
    secondary: tuple[str, ...]
    level: str | None
    family_id: str | None


def map_row(row: EvidenceRow, tags: Mapping[tuple[str, str], Tag], ctx: MapContext) -> Mapped:
    tag = tags.get((row.map_version_id, row.item_id)) if row.map_version_id else None
    if tag is None or tag.review_status == "rejected" or tag.cognitive_level is None:
        return Mapped(None, (), None, None)
    main, status = resolve_node(ctx, row.map_version_id, tag.main_node_id)
    if status != "ok" or main not in ctx.nodes:
        return Mapped(None, (), None, None)
    secondary = []
    for n in tag.node_ids:
        m, st = resolve_node(ctx, row.map_version_id, n)
        if st == "ok" and m in ctx.nodes and m != main and m not in secondary:
            secondary.append(m)
    # 题目族按版本区分（item_family 的主键带版本）；没有题目族的题由 Evidence.family 按单题自成一族
    return Mapped(main, tuple(secondary), tag.cognitive_level, tag.family_id)


def to_evidence(row: EvidenceRow, m: Mapped, item_type: str | None) -> Evidence:
    return Evidence(
        evidence_id=row.evidence_id, recorded_at=row.recorded_at, item_id=row.item_id,
        source="diagnose" if row.source_method == "diagnose" or row.in_diagnosis else "grade",
        attempt_id=row.attempt_id, family_id=m.family_id, item_type=item_type, tagged=m.main is not None,
        main_node_id=m.main, secondary_node_ids=m.secondary, cognitive_level=m.level,
        evidence_kind=row.evidence_kind, evidence_level=row.evidence_level, hint_level=row.hint_level,
        admission=row.admission, has_grade=row.grade_id is not None, has_response_ref=True,
        verdict=row.verdict, grade_confidence=row.grade_confidence, human_review_required=row.human_review_required,
        review_reasons=row.review_reasons, semantics=row.semantics, self_report=row.self_report,
        error_category=row.error_category, occurred_at=row.occurred_at)


def facts_of(row: EvidenceRow) -> EvidenceFacts:
    return EvidenceFacts(evidence_level=row.evidence_level, admission=row.admission,
                         human_review_required=row.human_review_required or row.grade_id is None, semantics=row.semantics)


@dataclass
class StudentEvidence:
    """一个学生一科的全部作答证据，已映射到当前地图。"""
    rows: list[EvidenceRow]
    mapped: dict[str, Mapped]                     # evidence_id → 归属
    item_type_of: ItemTypeOf
    subject: str

    def for_unit(self, unit: Unit) -> list[Evidence]:
        out = []
        for r in self.rows:
            m = self.mapped[r.evidence_id]
            if (m.main == unit.node_id and m.level == unit.cognitive_level) or unit.node_id in m.secondary:
                out.append(to_evidence(r, m, self.item_type_of(self.subject, r.item_id)))
        return out

    def units(self, attempt_ids: Iterable[str] | None = None) -> set[Unit]:
        wanted = None if attempt_ids is None else set(attempt_ids)
        return {Unit(m.main, m.level) for r in self.rows for m in (self.mapped[r.evidence_id],)
                if m.main is not None and m.level is not None and (wanted is None or r.attempt_id in wanted)}

    def facts(self) -> dict[str, EvidenceFacts]:
        return {r.evidence_id: facts_of(r) for r in self.rows}

    def attempt_of(self) -> dict[str, str]:
        return {r.evidence_id: r.attempt_id for r in self.rows}


async def load_student(conn, student: str, subject: str, ctx: MapContext, item_type_of: ItemTypeOf = no_item_type,
                       *, attempt_ids: Sequence[str] | None = None) -> StudentEvidence:
    rows = await load_rows(conn, student, subject, attempt_ids=attempt_ids)
    tags = await load_tags(conn, subject, (r.item_id for r in rows))
    return StudentEvidence(rows, {r.evidence_id: map_row(r, tags, ctx) for r in rows}, item_type_of, subject)


# ---------- 当前掌握记录与开着的任务 ----------
@dataclass(frozen=True)
class CurrentEstimate:
    id: str
    stored: StoredEstimate
    map_version_id: str | None
    evidence_ids: tuple[str, ...]


async def current_estimate(conn, student: str, subject: str, unit: Unit) -> CurrentEstimate | None:
    r = await conn.fetchrow(
        """SELECT id::text, state::text, p_mastery, confidence, next_retest_at, algorithm_version::text, map_version_id::text,
                  evidence_ids::text[]
             FROM platform.mastery_estimate
            WHERE student_id = $1::uuid AND subject = $2::platform.subject AND node_id = $3 AND cognitive_level = $4 AND is_current""",
        student, subject, unit.node_id, unit.cognitive_level)
    if r is None:
        return None
    return CurrentEstimate(r[0], StoredEstimate(r[1], float(r[2] if r[2] is not None else 0), float(r[3]), r[4], r[5]), r[6],
                           tuple(r[7]))


@dataclass(frozen=True)
class OpenTask:
    id: str
    reason: str
    rung: str | None
    due_at: datetime
    scheduled_by_estimate_id: str | None


async def open_tasks(conn, student: str, subject: str, unit: Unit) -> list[OpenTask]:
    rows = await conn.fetch(
        """SELECT id::text, reason, rung, due_at, scheduled_by_estimate_id::text FROM platform.retest_task
            WHERE student_id = $1::uuid AND subject = $2::platform.subject AND node_id = $3 AND cognitive_level = $4
              AND status = 'open' ORDER BY created_at, id""", student, subject, unit.node_id, unit.cognitive_level)
    return [OpenTask(*r) for r in rows]


async def due_tasks(conn, student: str, subject: str, now: datetime) -> list[dict]:
    """到期的开着的任务（复测、验证）：只回单元、原因、轮次与时间，不含题目。供 plan / 统一壳读“待复测”。"""
    rows = await conn.fetch(
        """SELECT id::text, node_id, cognitive_level, reason, rung, due_at FROM platform.retest_task
            WHERE student_id = $1::uuid AND subject = $2::platform.subject AND status = 'open' AND due_at <= $3::timestamptz
            ORDER BY due_at, id""", student, subject, now)
    return [dict(r) for r in rows]


# ---------- 能力视图：读时汇总，不写库、不点亮（第一步 spec 第 1 节；ADR 11 §3；规则 2） ----------
@dataclass(frozen=True)
class NodeRow:
    node_id: str
    cognitive_level: str
    state: str
    p_mastery: float | None
    next_retest_at: datetime | None


def aggregate_skill_view(rows: Sequence[NodeRow], links: Sequence[tuple[str, str, float]],
                         node_levels: Mapping[str, Sequence[str]], method: str = "weighted_mean") -> dict[str, dict[str, Any]]:
    """skill_node（local_id）→ {state, p_mastery?, next_retest_at?}。只由节点结果与 skill_link.weight 算出：
    - 只认当前地图里有效的单元（node_levels = 当前已发布版本可计算节点 → 认知层次）：节点不在、层次被删或类型变了的记录不进汇总；
    - p_mastery：挂接节点（有记录的）各层次 p 的均值，再按 weight 加权平均（没有记录的层次不进 p）；
    - state：挂接节点里最低的门槛；有记录的节点里没有记录的层次按 unseen 计入（保守；能力视图不单独点亮，所以不给 star_lit）；
    - next_retest_at：挂接节点里最早的复测时间。
    没有任何挂接节点有记录的能力点不出现。links = [(local_id, node_id, weight)]。"""
    if method != "weighted_mean":
        raise ValueError("skill_view_aggregation.method 不认识")
    by_node: dict[str, list[NodeRow]] = {}
    for r in rows:
        if r.cognitive_level in node_levels.get(r.node_id, ()):
            by_node.setdefault(r.node_id, []).append(r)
    out: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[tuple[str, float]]] = {}
    for local_id, node_id, w in links:
        grouped.setdefault(local_id, []).append((node_id, float(w)))
    for local_id, members in sorted(grouped.items()):
        have = [(n, w) for n, w in members if n in by_node]
        if not have:
            continue
        num = den = 0.0
        gate = GATES[-1]
        retest: datetime | None = None
        for n, w in have:
            ps = [r.p_mastery for r in by_node[n] if r.p_mastery is not None]
            if ps:
                num += w * (sum(ps) / len(ps))
                den += w
            if {r.cognitive_level for r in by_node[n]} != set(node_levels[n]):
                gate = "unseen"  # 该节点还有层次没有任何记录
            for r in by_node[n]:
                if RANK[r.state] < RANK[gate]:
                    gate = r.state
                if r.next_retest_at is not None and (retest is None or r.next_retest_at < retest):
                    retest = r.next_retest_at
        v: dict[str, Any] = {"state": gate}
        if den > 0:
            v["p_mastery"] = min(1.0, max(0.0, num / den))
        if retest is not None:
            v["next_retest_at"] = retest
        out[local_id] = v
    return out


async def skill_view(conn, student: str, subject: str) -> dict[str, dict[str, Any]]:
    """读当前节点键记录与当前已发布版本的 skill_link，读时汇总成能力视图（不写库）。
    plan 的连接是登录角色本身（继承 starbuddy_learning），kge 表的读权限与策略对它同样生效。"""
    rows = [NodeRow(r[0], r[1], r[2], None if r[3] is None else float(r[3]), r[4]) for r in await conn.fetch(
        """SELECT node_id, cognitive_level, state::text, p_mastery, next_retest_at FROM platform.mastery_estimate
            WHERE student_id = $1::uuid AND subject = $2::platform.subject AND is_current AND node_id IS NOT NULL""",
        student, subject)]
    if not rows:
        return {}
    links = [(r[0], r[1], float(r[2])) for r in await conn.fetch(
        """SELECT l.local_id::text, l.node_id::text, l.weight
             FROM kge.skill_link l JOIN kge.map_version v ON v.id = l.map_version_id
            WHERE v.status = 'published' AND v.subject = $1::platform.subject AND l.subject = $1::platform.subject""", subject)]
    ctx = await load_map(conn, subject)  # 只认当前地图里有效的单元（审查意见 7）
    return aggregate_skill_view(rows, links, ctx.nodes)
