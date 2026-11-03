"""掌握读取接口的实现（learning-internal 1.1.0 `POST /internal/v1/mastery/get`、`/mastery/explain`；
KGE-04 第二步 PR 3，specs/kge-04-mastery-step2/spec.md 第 3 节）。只读，不写任何表。

形状照搬 knowledge-service v0.9 的 mastery.get / mastery.explain（统一信封：result、evidence_ids、confidence、source_version、
model_version、human_review_required、contract_version）。
- 只认当前已发布地图里有效的可计算节点与层次（第二步 lessons 待人确认 K：改名、合并、拆分、撤下的旧单元记录不出现）；
- 每个请求节点按其当前地图里的全部认知层次各回一行，没有记录的层次是 unseen；
- star_lit 是节点的星亮：按该科掌握配置的 star_level_rule（gates.star_lit），同一节点各行取值相同；没有可用配置的学科为 false；
- explain 的证据链 = 各层次当前记录引用的证据；weight 是重放时的更新强度（gates.Replay.strengths），当前已不计入的为 0；
  门槛条件的编码与数值不外露（第一步 #17），只在 explain_gates（服务内）里。

另有星图 v1 的节点星态（learning-internal 1.1.0 草稿内追加的 `POST /internal/v1/star-map/nodes`，H5 星图 v1 PR 1，
specs/star-map-v1-h5/spec.md 第 2 节）：只给 BFF；星 = 当前已发布地图里全部有效的 level = node 节点（统一壳 #9 默认），
其中可计算类型且带认知层次的节点星态取 gates.star_basis_level 那一层（与 star_lit 同一份口径），其余节点一律 unseen、不亮、无依据；
只回 node_id、state、star_lit、basis_id。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..store import learning_tx
from . import store as S
from .config import ALGORITHM, ALGORITHM_VERSION, COGNITIVE_LEVELS, MasteryConfig
from .evidence import Unit
from .gates import replay, star_basis_level, star_lit
from .recompute import MasteryUnavailable, norm_student

KS_CONTRACT_VERSION = "0.9.0"   # 知识服务形状的版本（信封 contract_version 是 const）
MODEL_VERSION = f"algo:{ALGORITHM}@{ALGORITHM_VERSION}"
HINT_MAX = 5                    # 契约 EvidenceLink.hint_level 上限；平台提示阶梯 0–6，6 级按 5 记
MAX_CHAIN = 200                 # 契约 chain 的 maxItems：超出时取最近的 200 条
EVIDENCE_KINDS = ("independent", "hinted", "transfer", "delayed_retest")
_MAX_SEGMENTS = 4               # source_version 每类最多 4 段（契约一共最多 8 段）


async def _nodes(conn, node_ids: Sequence[str]) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """请求节点在当前已发布地图里的 (学科, 地图版本号, 认知层次)；不在任何当前地图里的节点不出现。
    不另加 level = 'node'：迁移 0011 的 node_detail_kind（细目只能是 pitfall / exam_point）与 node_kind_by_level（领域、主题没有 kind）
    保证可计算类型（kp、ability_kp、viewpoint）只出现在 level = 'node'，按 kind 过滤已经足够。"""
    rows = await conn.fetch(
        """SELECT n.id::text, v.subject::text, v.version, n.cognitive_levels
             FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id
            WHERE v.status = 'published' AND n.status = 'active' AND n.kind = ANY($2::text[]) AND n.id = ANY($1::text[])
            ORDER BY v.subject, n.id""", list(node_ids), list(S.COMPUTABLE_KINDS))
    out: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for node_id, subject, version, levels in rows:
        lv = tuple(x for x in COGNITIVE_LEVELS if x in (levels or ()))
        if lv and node_id not in out:  # 节点 ID 带学科前缀，跨学科重复不应出现；出现时取学科序第一个（确定性）
            out[node_id] = (subject, version, lv)
    return out


async def _records(conn, student: str, nodes: Mapping[str, tuple[str, str, tuple[str, ...]]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    """(节点, 层次) → 当前节点键记录；只留学科与层次都对得上当前地图的。"""
    if not nodes:
        return {}
    rows = await conn.fetch(
        """SELECT subject::text AS subject, node_id, cognitive_level, state::text AS state, p_mastery, confidence,
                  evidence_ids::text[] AS evidence_ids, next_retest_at, algorithm_version::text AS algorithm_version
             FROM platform.mastery_estimate
            WHERE student_id = $1::uuid AND is_current AND node_id = ANY($2::text[])""", student, list(nodes))
    out = {}
    for r in rows:
        subject, _, levels = nodes[r["node_id"]]
        if r["subject"] == subject and r["cognitive_level"] in levels:
            out[(r["node_id"], r["cognitive_level"])] = r
    return out


def _envelope(result: dict, evidence_ids: Sequence[str], confidences: Sequence[float], algos: Sequence[str],
              graphs: Sequence[str]) -> dict:
    algo = sorted(set(algos))[:_MAX_SEGMENTS] or [f"{ALGORITHM}@{ALGORITHM_VERSION}"]
    seg = [f"algo:{a}" for a in algo] + [f"graph:{g}" for g in sorted(set(graphs))[:_MAX_SEGMENTS]]
    return {"contract_version": KS_CONTRACT_VERSION, "result": result, "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "confidence": round(min(confidences), 6) if confidences else 0.0, "source_version": ";".join(seg),
            "model_version": MODEL_VERSION, "human_review_required": False}


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.isoformat()


async def mastery_get(pool, configs: Mapping[str, MasteryConfig], body: Mapping[str, Any]) -> dict:
    student = norm_student(body["context"]["student_ref"])
    node_ids = list(dict.fromkeys(body["node_ids"]))
    async with learning_tx(pool) as conn:
        nodes = await _nodes(conn, node_ids)
        recs = await _records(conn, student, nodes)
    states, evidence, confs, algos, graphs = [], [], [], [], []
    for node_id in node_ids:
        if node_id not in nodes:
            continue
        subject, version, levels = nodes[node_id]
        graphs.append(f"{subject}-map@{version}")
        gates = {lv: (recs[(node_id, lv)]["state"] if (node_id, lv) in recs else "unseen") for lv in levels}
        cfg = configs.get(subject)
        lit = bool(cfg is not None and cfg.subject == subject and star_lit(node_id, levels, gates, cfg))
        for lv in levels:
            r = recs.get((node_id, lv))
            st: dict[str, Any] = {"node_id": node_id, "cognitive_level": lv, "gate": gates[lv]}
            if r is not None and r["p_mastery"] is not None:
                st["p_mastery"] = min(1.0, max(0.0, float(r["p_mastery"])))
            st["star_lit"] = lit
            if r is not None and r["next_retest_at"] is not None:
                st["next_review_at"] = _iso(r["next_retest_at"])
            states.append(st)
            if r is not None:
                evidence.extend(r["evidence_ids"])
                confs.append(float(r["confidence"]))
                algos.append(r["algorithm_version"])
    return _envelope({"states": states}, evidence, confs, algos, graphs)


async def star_map_nodes(pool, configs: Mapping[str, MasteryConfig], student_ref: str, subject: str) -> dict:
    """星图 v1 的节点星态。该科没有已发布地图：status = no_map；地图已发布但没有可用掌握配置：MasteryUnavailable（接口回 409）。
    每颗星只有 node_id、state（星亮层次上的门槛，没有当前记录为 unseen）、star_lit、basis_id（决定 state 的当前记录 ID）。"""
    student = norm_student(student_ref)
    async with learning_tx(pool) as conn:
        cur = await conn.fetchrow(
            "SELECT id::text, version FROM kge.map_version WHERE subject = $1::platform.subject AND status = 'published'", subject)
        if cur is None:
            return {"subject": subject, "status": "no_map", "map_version": None, "nodes": []}
        cfg = configs.get(subject)
        if cfg is None or cfg.subject != subject:
            raise MasteryUnavailable(subject)
        # 星 = 当前已发布地图里全部有效的 level = 'node' 节点（统一壳 #9 默认）；领域、主题、细目不成星
        rows = await conn.fetch(
            """SELECT id::text, kind, cognitive_levels FROM kge.knowledge_node
                WHERE map_version_id = $1::uuid AND status = 'active' AND level = 'node'
                ORDER BY id""", cur["id"])
        stars: list[tuple[str, tuple[str, ...]]] = []
        for node_id, kind, levels in rows:
            # 只有可计算类型且带认知层次的节点参与掌握计算（与 mastery_get 同口径）；其余节点层次为空 → unseen、不亮、无依据
            lv = tuple(x for x in COGNITIVE_LEVELS if x in (levels or ())) if kind in S.COMPUTABLE_KINDS else ()
            stars.append((node_id, lv))
        computable = [n for n, lv in stars if lv]
        recs: dict[tuple[str, str], Mapping[str, Any]] = {}
        if computable:
            # 只读决定星态所需的列（不读 p_mastery、confidence、证据）
            for r in await conn.fetch(
                    """SELECT id::text AS id, node_id, cognitive_level, state::text AS state FROM platform.mastery_estimate
                        WHERE student_id = $1::uuid AND subject = $2::platform.subject AND is_current AND node_id = ANY($3::text[])""",
                    student, subject, computable):
                recs[(r["node_id"], r["cognitive_level"])] = r
    out = []
    for node_id, levels in stars:
        gates = {lv: (recs[(node_id, lv)]["state"] if (node_id, lv) in recs else "unseen") for lv in levels}
        level = star_basis_level(node_id, levels, gates, cfg)
        basis = recs.get((node_id, level)) if level is not None else None
        out.append({"node_id": node_id, "state": basis["state"] if basis is not None else "unseen",
                    "star_lit": bool(star_lit(node_id, levels, gates, cfg)), "basis_id": basis["id"] if basis is not None else None})
    return {"subject": subject, "status": "ok", "map_version": cur["version"], "nodes": out}


async def mastery_explain(pool, configs: Mapping[str, MasteryConfig], body: Mapping[str, Any], now: datetime,
                          item_type_of: S.ItemTypeOf = S.no_item_type) -> dict:
    student = norm_student(body["context"]["student_ref"])
    node_id = body["node_id"]
    chain: list[dict] = []
    evidence: list[str] = []
    confs: list[float] = []
    algos: list[str] = []
    graphs: list[str] = []
    async with learning_tx(pool) as conn:
        nodes = await _nodes(conn, [node_id])
        recs = await _records(conn, student, nodes)
        if node_id in nodes and recs:
            subject, version, levels = nodes[node_id]
            graphs.append(f"{subject}-map@{version}")
            cited: list[str] = []
            for lv in levels:
                r = recs.get((node_id, lv))
                if r is not None:
                    cited.extend(r["evidence_ids"])
                    confs.append(float(r["confidence"]))
                    algos.append(r["algorithm_version"])
            cited = list(dict.fromkeys(cited))
            evidence = cited
            ctx = await S.load_map(conn, subject)
            ev = await S.load_student(conn, student, subject, ctx, item_type_of)
            by_id = {row.evidence_id: row for row in ev.rows}
            # 重放（只读、与写入同一套纯函数）取每条证据的生效情境、等级与更新强度；没有可用配置时不重放，强度记 0
            cls: dict[str, Any] = {}
            strength: dict[str, float] = {}
            cfg = configs.get(subject)
            if cfg is not None and cfg.subject == subject:
                for lv in levels:
                    if (node_id, lv) not in recs:
                        continue
                    unit = Unit(node_id, lv)
                    rep = replay(ev.for_unit(unit), unit, cfg, now)
                    for eid, c in rep.counted:
                        cls[eid] = c
                    strength.update(rep.strengths)
            rows = sorted((by_id[e] for e in cited if e in by_id), key=lambda x: (x.recorded_at, x.evidence_id))[-MAX_CHAIN:]
            for row in rows:
                c = cls.get(row.evidence_id)
                kind = c.kind if c is not None else row.evidence_kind
                level = c.level if c is not None else row.evidence_level
                link: dict[str, Any] = {"evidence_id": row.evidence_id, "occurred_at": _iso(row.occurred_at),
                                        "evidence_level": level if level in ("A", "B") else "B",
                                        "weight": round(min(1.0, max(0.0, strength.get(row.evidence_id, 0.0))), 6)}
                if kind in EVIDENCE_KINDS:
                    link["evidence_kind"] = kind
                if row.hint_level is not None:
                    link["hint_level"] = min(HINT_MAX, max(0, int(row.hint_level)))
                chain.append(link)
    return _envelope({"node_id": node_id, "chain": chain}, evidence, confs, algos, graphs)
