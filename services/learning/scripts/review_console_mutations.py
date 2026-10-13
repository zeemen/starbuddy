"""审核台 KGE-06（specs/kge06-weekly-revision 范围 1、2）的变异测试：把低置信工作单（开关、排序与游标、只列待处理、只经签字身份
决定、改标核对与驳回原候选）与修订发布预检（版本号段、两人签、版本号比较、结构性以库为准）的关键检查逐条改坏，断言
tests/test_review_revision.py 能发现。

做法与今日编排、复测的变异脚本相同（复用 scripts/practice_mutations.py 的临时目录复制与运行）：每个变异在临时目录里改一处源码再跑测试；
只有“测试失败”（退出码 1）算发现。先跑一次不变异的对照组，必须通过。两组：
- 纯函数：tests/test_review_revision.py 里不连库的用例；
- 库上：整个 tests/test_review_revision.py，一次性 Postgres（每个变异起一个库）；没有本机 Postgres 时退出码 3（Makefile 决定是否算失败）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import practice_mutations as PM  # noqa: E402

HERE = PM.HERE
TEST = "tests/test_review_revision.py"
PURE_TESTS = [TEST, "-k", "not (features_off or structural_and_non_structural or worklist_order)"]
DB_TESTS = [TEST]
RV, SV, CF, APP, ST = ("review_console/revision.py", "review_console/service.py", "review_console/config.py", "review_console/app.py",
                       "review_console/store.py")

PURE: dict[str, list[tuple[str, str, str]]] = {
    # 预检：版本号段与签字人数
    "结构性也只建议升修订号": [(RV, 'bump, suggested = "minor", (latest[0], latest[1] + 1, 0)',
                         'bump, suggested = "patch", (latest[0], latest[1], latest[2] + 1)')],
    "结构性只要一人签": [(RV, "required = 2 if structural else 1", "required = 1")],
    "结构性不要求升次版本": [(RV, "if structural and d[:2] == latest[:2]:", "if False:")],
    "版本号等于最大版本也放过": [(RV, "if d <= latest:", "if d < latest:")],
    "版本号按字符串比": [(RV, "if d <= latest:", "if draft_version <= fmt(latest):")],
    "非草稿不报问题": [(RV, '    if status != "draft":\n        problems.append("not_draft")\n', "")],
    "格式不对不报问题": [(RV, '        problems.append("version_format")', '        pass')],
    "升了次版本不提示": [(RV, '                advice.append("minor_not_required")', '                pass')],
    "跳过版本号不提示": [(RV, '                advice.append("skips_versions")', '                pass')],
    "升主版本不提示": [(RV, '                advice.append("major_bump")', '                pass')],
    "改标不核对原候选已处理": [(SV, 'if original["status"] != "open" or target["status"] != "open":', 'if target["status"] != "open":')],
    "结构性依据含已驳回的变更单": [(RV, 'and r.get("status") != "rejected"]', "]")],
    "结构性依据含非结构性差异": [(RV, 'for c in changes if c.get("risk", {}).get("structural")]', "for c in changes]")],
    # 改标核对与可选项
    "改标不核对同题": [(SV, 'for k in ("map_version_id", "subject", "item_id", "role"))', 'for k in ("map_version_id", "subject", "role"))')],
    "改标不核对已处理": [(SV, 'if original["status"] != "open" or target["status"] != "open":', "if False:")],
    "改标可以换成同一节点": [(SV, 'if not same or original["node_id"] == target["node_id"] or original["id"] == target["id"]:',
                       "if not same:")],
    "改标可选项不分角色": [(SV, ' and s["role"] == c["role"] and s["node_id"] != c["node_id"]]', ' and s["node_id"] != c["node_id"]]')],
    # 开关
    "开关能在 pilot 打开": [(CF, "    if env not in ALLOWED_ENVS:\n        raise RuntimeError(\"REVIEW_CONSOLE_FEATURES",
                         "    if False:\n        raise RuntimeError(\"REVIEW_CONSOLE_FEATURES")],
    "开关收不认识的名字": [(CF, "    if unknown:\n        raise RuntimeError(f\"REVIEW_CONSOLE_FEATURES",
                       "    if False:\n        raise RuntimeError(f\"REVIEW_CONSOLE_FEATURES")],
}

DB: dict[str, list[tuple[str, str, str]]] = {
    # 开关默认关：关着不注册
    "开关关着也注册工作单": [(APP, '    if "tag_worklist" in enabled:', "    if True:")],
    "开关关着也注册预检": [(APP, '    if "revision_preflight" in enabled:', "    if True:")],
    # 只有签字身份能决定、处理人由库记下
    "决定走知识服务身份": [(APP, 'status = await state["review"].decide_tag(staff, candidate_id,',
                       'status = await ReviewStore(state["kge"].pool).decide_tag(staff, candidate_id,')],
    "决定不带 staff 账号": [(ST, 'return await self._as_staff(staff, lambda c: c.fetchval("SELECT kge.decide_tag_candidate($1, $2)", candidate, decision))',
                        'return await self.pool.fetchval("SELECT kge.decide_tag_candidate($1, $2)", candidate, decision)')],
    "接受与驳回对调": [(APP, '"accepted" if body.decision == "accept" else "rejected"', '"rejected" if body.decision == "accept" else "accepted"')],
    "改标不调核对": [(APP, "                service.check_relabel(await state[\"kge\"].tag_candidate(candidate_id), "
                       "await state[\"kge\"].tag_candidate(body.target_id))\n", "")],
    "改标不驳回原候选": [(ST, "            await c.fetchval(\"SELECT kge.decide_tag_candidate($1, 'rejected')\", original)\n", "")],
    "改标吞掉“原候选已处理”（恢复保存点吞异常）": [(
        ST, "            await c.fetchval(\"SELECT kge.decide_tag_candidate($1, 'rejected')\", original)\n",
        "            try:\n                async with c.transaction():\n"
        "                    await c.fetchval(\"SELECT kge.decide_tag_candidate($1, 'rejected')\", original)\n"
        "            except asyncpg.CheckViolationError:\n                pass\n")],
    "改标先接受后驳回": [(
        ST, "            await c.fetchval(\"SELECT kge.decide_tag_candidate($1, 'rejected')\", original)\n"
            "            return await c.fetchval(\"SELECT kge.decide_tag_candidate($1, 'accepted')\", target)\n",
        "            out = await c.fetchval(\"SELECT kge.decide_tag_candidate($1, 'accepted')\", target)\n"
        "            await c.fetchval(\"SELECT kge.decide_tag_candidate($1, 'rejected')\", original)\n            return out\n")],
    "预检的已有版本混入草稿": [(SV, 'x["status"] in ("published", "retired")]', 'x["status"] != "never"]')],
    # 工作单：排序、游标、只列待处理
    "工作单按置信度降序": [(ST, "ORDER BY c.confidence, c.id LIMIT $5", "ORDER BY c.confidence DESC, c.id LIMIT $5")],
    "游标不按 id 断同分": [(ST, "(c.confidence, c.id) > ($3::numeric, $4::uuid)", "c.confidence > $3::numeric AND $4::uuid IS NOT NULL")],
    "工作单列出已处理的": [(ST, "WHERE c.status = 'open' AND c.subject = $1::platform.subject AND ($2::uuid IS NULL OR c.map_version_id = $2)",
                       "WHERE c.subject = $1::platform.subject AND ($2::uuid IS NULL OR c.map_version_id = $2)")],
    "工作单不按地图版本过滤": [(ST, "AND ($2::uuid IS NULL OR c.map_version_id = $2)\n                   AND ($3::numeric",
                         "AND ($2::uuid IS NULL OR true)\n                   AND ($3::numeric")],
    "翻页上限放宽": [(APP, "if not 1 <= limit <= service.WORKLIST_MAX or", "if not 1 <= limit or")],
    # 预检：结构性以库为准
    "预检不用库的结构性判定": [(SV, 'structural=bool(s["structural"]))', "structural=False)")],
    "签字进度不看人数": [(SV, 'and s["approvals"] >= pf["required_signers"])', ")")],
}


def group(label: str, mutations: dict[str, list[tuple[str, str, str]]], tests: list[str], workers: int) -> int:
    with tempfile.TemporaryDirectory(prefix="review-mut.") as t:
        rc, out = PM._run(PM._copy(Path(t)), tests)
    if rc != 0:
        print(out)
        print(f"review-console-mutation（{label}）：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda kv: PM.one(kv[0], kv[1], tests), mutations.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"review-console-mutation（{label}）：失败，{len(missed)}/{len(mutations)} 个变异没有被发现")
        return 1
    print(f"review-console-mutation（{label}）：通过（{len(mutations)} 个变异全部被发现）")
    return 0


def main() -> int:
    if not (HERE / ".venv/bin/python").exists():
        print("review-console-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    rc = group("纯函数", PURE, PURE_TESTS, 6)
    if rc:
        return rc
    from glob import glob
    if not (os.environ.get("PGBIN") or glob("/usr/lib/postgresql/*/bin/initdb")):
        print("review-console-mutation（库上）：跳过（本机没有 Postgres 服务端）")
        return 3
    return group("库上", DB, DB_TESTS, 4)


if __name__ == "__main__":
    sys.exit(main())
