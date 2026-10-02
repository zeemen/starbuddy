"""联调栈的合成掌握配置（KGE-04 第二步 PR 3；第二步 spec 第 4 节、待人确认 #9）：复测改由 platform.retest_task 驱动。

只在一次性库上、以超级用户在 replica 模式写（同 services/learning/tests/test_mastery_db.py 造夹具的做法），全部是合成占位：
- 一个已发布的合成数学地图（两个节点：代数式、几何与坐标，层次只有 apply），把队列用到的阿鸦题打到这两个节点上（每题自成一族），
  并把阿鸦内容版本绑定到这个地图（证据才带地图版本、能归属到单元）；
- 掌握配置取 services/learning/config/mastery/synthetic-test.json（合成、未签字，只允许 test 环境），只改一处：
  当日复测间隔 rung0_min_gap_seconds = 0，使复测轮在一次运行里就能到期执行（服务器记录时间不能注入）；
- 知行、文砺没有掌握配置：不写掌握记录、不建复测任务（与生产口径一致）。
联调结束把这张地图置为已撤下（不删：证据引用着它）并解除内容版本绑定，同一会话库里别的测试不受影响。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from learning.mastery import parse_config, replay
from learning.mastery import store as S
from learning.mastery.config import CONFIG_DIR, MasteryConfig
from learning.mastery.evidence import Unit
from learning.store import learning_tx

from .cohort import AYA_BANK, CONTENT_VERSION

MAP_ID = "5b8e1c52-e2e0-4c0f-8a5d-0000000000a0"
LEVEL = "apply"
NODES = {"algebra": "MATH.E2E.T1.ALG", "geometry": "MATH.E2E.T1.GEO"}
NODE_OF_ITEM = {item: NODES[area] for area, items in AYA_BANK.items() for item, _, _ in items}
ITEMS_OF_NODE = {node: [item for item, n in sorted(NODE_OF_ITEM.items()) if n == node] for node in NODES.values()}


def config() -> MasteryConfig:
    doc = json.loads((CONFIG_DIR / "synthetic-test.json").read_text(encoding="utf-8"))
    doc["retest"]["rung0_min_gap_seconds"] = 0
    return parse_config(doc, env="test")


def seed_sql() -> str:
    cv = CONTENT_VERSION["aya"]
    nodes = ",\n  ".join(f"('{MAP_ID}', '{n}', 'node', 'MATH.E2E.T1', 'kp', '（合成联调）{area}', '{{{LEVEL}}}')"
                         for area, n in sorted(NODES.items()))
    tags = ",\n  ".join(f"('{MAP_ID}', 'aya', '{item}', '{node}', '{LEVEL}', 'original', 'approved')"
                        for item, node in sorted(NODE_OF_ITEM.items()))
    return f"""
SET session_replication_role = replica;
INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES
  ('{MAP_ID}', 'aya', 2022, '0.0.1', 'published', now());
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels) VALUES
  ('{MAP_ID}', 'MATH.E2E', 'domain', NULL, NULL, '（合成联调）领域', '{{}}'),
  ('{MAP_ID}', 'MATH.E2E.T1', 'topic', 'MATH.E2E', NULL, '（合成联调）主题', '{{}}'),
  {nodes};
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, cognitive_level, source_kind, tag_review_status) VALUES
  {tags};
UPDATE platform.curriculum_version SET map_version_id = '{MAP_ID}'
 WHERE subject = 'aya' AND region = '{cv["region"]}' AND exam_year = {cv["exam_year"]} AND grade = {cv["grade"]}
   AND textbook_version = '{cv["textbook_version"]}' AND content_version = '{cv["content_version"]}';
SET session_replication_role = origin;
"""


def retire_sql() -> str:
    return f"""SET session_replication_role = replica;
UPDATE kge.map_version SET status = 'retired' WHERE id = '{MAP_ID}';
UPDATE platform.curriculum_version SET map_version_id = NULL WHERE map_version_id = '{MAP_ID}';
SET session_replication_role = origin;
"""


async def explanation(app_pool, students: list[str], cfg: MasteryConfig) -> dict:
    """掌握重算为什么到了 / 没到复测：按学习服务同一套读取层与重放（只读），汇总每个单元的不计入原因、计入条数与门槛，
    再数库里的节点键掌握记录与复测任务。只含编码与计数。"""
    reasons: Counter = Counter()
    gates: Counter = Counter()
    counted = units = 0
    now = datetime.now(timezone.utc)
    async with learning_tx(app_pool) as conn:
        ctx = await S.load_map(conn, "aya")
        for sid in students:
            ev = await S.load_student(conn, sid, "aya", ctx)
            for u in sorted(ev.units(), key=lambda u: (u.node_id, u.cognitive_level)):
                rep = replay(ev.for_unit(Unit(u.node_id, u.cognitive_level)), u, cfg, now)
                units += 1
                counted += len(rep.counted)
                gates[rep.result.state or "none"] += 1
                for _, rs in rep.excluded:
                    reasons.update(rs)
        records = await conn.fetchval(
            """SELECT count(*) FROM platform.mastery_estimate
                WHERE student_id = ANY($1::uuid[]) AND node_id IS NOT NULL AND is_current""", students)
        tasks = {f"{r[0]}/{r[1]}": r[2] for r in await conn.fetch(
            """SELECT reason, status, count(*) FROM platform.retest_task WHERE student_id = ANY($1::uuid[])
                GROUP BY 1, 2 ORDER BY 1, 2""", students)}
    return {"subject": "aya", "map": "合成联调地图（2 个节点，层次 apply）", "config": "synthetic-test（未签字）+ rung0_min_gap_seconds = 0",
            "units": units, "evidence_counted": counted, "excluded_reasons": dict(sorted(reasons.items())),
            "replay_gates": dict(sorted(gates.items())), "node_key_records": int(records), "retest_tasks": tasks,
            "subjects_without_config": ["wenli", "zhixing"]}
