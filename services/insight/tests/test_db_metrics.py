"""中圈指标库上测试：从一次性 Postgres 的合成事实（tests/pginsight.py 的 S3）读取、计算、写成 visibility = {agent} 的
metric 判断。数值与手算一致；学生、家长看不到；再算一次不写（幂等）。"""
from __future__ import annotations

import asyncpg
import pytest

from insight.shared import forbidden_matcher
from insight.store import InsightStore, create_pool
from pginsight import APP_ROLE, BFF_ROLE, S3, SA3, TPL_METRIC
from support import make_policy

pytestmark = pytest.mark.anyio
P = make_policy()
TAGS = {("aya", f"syn-item-{i:03d}"): ("MATH.SYN.EQ.LINEAR", "apply") for i in range(1, 27)}


async def test_metrics_match_hand_calculation(insightdb):
    insightdb.psql(f"DELETE FROM insight.claim WHERE student_id = '{S3}';")
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        results, sync = await store.recompute_metrics(S3, P, forbidden_matcher(), TAGS)
        got = {(r.key, r.subject, r.qualifier): r.value for r in results}
        # 手算（数据见 pginsight.py 的 S3 段）
        assert got[("independent_solve_rate", "aya", "apply")] == {"v": 0.75, "n": 12}      # 1–12 无提示首次作答 9 对（第 1 题的旧判分已被取代）
        assert got[("hint_dependency", "aya", None)] == {"v": round((2 + 3 + 4 + 6) / 6 / 16, 4), "n": 16}
        assert got[("transfer_gap", "aya", None)] == {"v": round(0.6 - 0.75, 4), "n_transfer": 10, "n_mother": 12}
        brier = (1 + 3 + 5 * 0.25) / 20   # 能：10 错 1 次；不能：13–15 对 3 次；不确定：16–20 全对
        bias = (1 - 3 - 5 * 0.5) / 20
        assert got[("self_assessment_calibration", None, None)] == {"v": round(brier, 4), "bias": round(bias, 4), "dir": "under", "n": 20}
        assert got[("retention_rate", "aya", None)] == {"v": 0.7, "n": 10}
        assert got[("plan_completion_rate", None, None)] == {"v": 0.6, "n": 5}
        assert got[("reading_question_error_share", None, None)] == {"v": 0.3, "n": 10, "subjects": 2}
        days = int(insightdb.psql(
            f"""SELECT count(DISTINCT d) FROM (
                  SELECT (occurred_at AT TIME ZONE 'Asia/Shanghai')::date AS d FROM platform.attempt WHERE student_id = '{S3}'
                  UNION ALL SELECT (occurred_at AT TIME ZONE 'Asia/Shanghai')::date FROM insight.behavior_event
                   WHERE student_id = '{S3}' AND kind = 'session_start') x
                WHERE d > (now() AT TIME ZONE 'Asia/Shanghai')::date - 28""").strip())
        assert got[("persistence", None, None)]["v"] == days / 4 and got[("persistence", None, None)]["weeks"] == 4
        assert len(results) == 8 and sync.missing_templates == ()

        c = await asyncpg.connect(**insightdb.connect_kwargs("postgres"))
        try:
            rows = await c.fetch(f"SELECT target_type::text AS t, visibility, status::text AS s, template_key, basis_id, cardinality(evidence_ids) AS n "
                                 f"FROM insight.claim WHERE student_id = '{S3}'")
        finally:
            await c.close()
        assert len(rows) == 8
        assert all(r["t"] == "metric" and list(r["visibility"]) == ["agent"] and r["s"] == "hypothesis"
                   and r["template_key"] == TPL_METRIC and r["basis_id"] is None and r["n"] >= 1 for r in rows)
        # 前端看不到：本服务的读取与 0010 的 BFF 行级策略
        assert await store.visible_claims(S3, "student") == [] and await store.visible_claims(S3, "guardian") == []
        assert len(await store.visible_claims(S3, "agent")) == 8
        b = await asyncpg.connect(**insightdb.connect_kwargs(BFF_ROLE))
        try:
            await b.execute("SET ROLE starbuddy_bff")
            await b.execute("SELECT set_config('app.account_id', $1, false)", SA3)
            assert await b.fetchval(f"SELECT count(*) FROM insight.claim WHERE student_id = '{S3}'") == 0
        finally:
            await b.close()
        # 再算一次：没有写入
        _, again = await store.recompute_metrics(S3, P, forbidden_matcher(), TAGS)
        assert again.ops == ()
        # 没有题目打标（洞察服务暂无 kge.item_tag 读权限）：依赖节点与认知层次的两个指标不出结果、已有判断不动
        res_no_tags, sync2 = await store.recompute_metrics(S3, P, forbidden_matcher())
        assert {r.key for r in res_no_tags} == {"hint_dependency", "self_assessment_calibration", "retention_rate",
                                                "plan_completion_rate", "reading_question_error_share", "persistence"}
        assert sync2.ops == ()
    finally:
        await pool.close()
