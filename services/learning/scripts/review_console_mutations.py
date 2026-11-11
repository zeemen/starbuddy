"""审核台 KGE-06（specs/kge06-weekly-revision 范围 1、2）的变异测试：把低置信工作单（开关、排序与游标、只列待处理、只经签字身份
决定、改标核对与驳回原候选）与修订发布预检（版本号段、两人签、版本号比较、结构性以库为准）的关键检查逐条改坏，断言
tests/test_review_revision.py 能发现。

做法与今日编排、复测的变异脚本相同（复用 scripts/practice_mutations.py 的临时目录复制与运行）：每个变异在临时目录里改一处源码再跑测试；
只有“测试失败”（退出码 1）算发现。先跑一次不变异的对照组，必须通过。两组：
- 纯函数：tests/test_review_revision.py 里不连库的用例；
- 库上：整个 tests/test_review_revision.py，一次性 Postgres（每个变异起一个库）；没有本机 Postgres 时退出码 3（Makefile 决定是否算失败）。
另两组是 LT-06（specs/launch-lt06-internal-test-ops）：反馈列表、改状态、退出状态与开关，断言 tests/test_review_internal_test.py 能发现。
响应模型两组（specs/review-console-responses）：导出过期（纯函数）与服务层、模型对不上（库上，响应校验 500）。
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

# ---------- LT-06 内部成员测试运营（specs/launch-lt06-internal-test-ops）：反馈列表、改状态、退出状态与开关 ----------
LT6_TEST = "tests/test_review_internal_test.py"
LT6_PURE_TESTS = [LT6_TEST, "-k", "switch_parsing"]
LT6_DB_TESTS = [LT6_TEST]
LT6_PURE: dict[str, list[tuple[str, str, str]]] = {
    "内测：开关能在 pilot 打开": [(CF, '    if env not in SERVICE_ENVS:\n        raise RuntimeError("REVIEW_CONSOLE_INTERNAL_TEST',
                            '    if False:\n        raise RuntimeError("REVIEW_CONSOLE_INTERNAL_TEST')],
    "内测：开关收别的取值": [(CF, "    if v != INTERNAL_TEST_FEATURE:", "    if False:")],
    "内测：开关缺省开": [(CF, '    if not v:\n        return False\n    if env not in SERVICE_ENVS:',
                      '    if not v:\n        return True\n    if env not in SERVICE_ENVS:')],
    "内测：环境变量不接线": [(CF, 'internal_test=parse_internal_test(e.get("REVIEW_CONSOLE_INTERNAL_TEST", ""), env))',
                         "internal_test=False)")],
}
LT6_DB: dict[str, list[tuple[str, str, str]]] = {
    "内测：开关关着也注册": [(APP, "    if internal_on:", "    if True:")],
    "内测：注入池时缺省开": [(APP, "(settings.internal_test if settings is not None else False))",
                         "(settings.internal_test if settings is not None else True))")],
    "内测：只带 before_id 不报错": [(APP, "or not 1 <= limit <= 200 \\\n                    or (before_id is not None and before is None):",
                                "or not 1 <= limit <= 200:")],
    "内测：before 不带时区放过": [(APP, '                raise ReviewError(422, "INVALID_PARAMETER", "before 必须带时区")\n            items = await state["review"].feedback_list(',
                              '                pass\n            items = await state["review"].feedback_list(')],
    "内测：游标不带 id": [(APP, 'nxt = {"before": items[-1]["created_at"], "before_id": items[-1]["id"]} if len(items) == limit else None\n            return {"items": items, "next": nxt}\n\n        @app.post("/review/v0/feedback/',
                       'nxt = {"before": items[-1]["created_at"]} if len(items) == limit else None\n            return {"items": items, "next": nxt}\n\n        @app.post("/review/v0/feedback/')],
    "内测：列表不给下一页": [(APP, 'nxt = {"before": items[-1]["created_at"], "before_id": items[-1]["id"]} if len(items) == limit else None\n            return {"items": items, "next": nxt}\n\n        @app.post("/review/v0/feedback/',
                         'nxt = None\n            return {"items": items, "next": nxt}\n\n        @app.post("/review/v0/feedback/')],
    "内测：列表不经 staff 身份": [(ST, 'rows = await self._as_staff(staff, lambda c: c.fetch(\n            "SELECT * FROM platform.review_feedback_list($1, $2, $3, $4)", status, limit, before, before_id))',
                              'rows = await self.pool.fetch(\n            "SELECT * FROM platform.review_feedback_list($1, $2, $3, $4)", status, limit, before, before_id)')],
    "内测：改状态不经 staff 身份": [(ST, 'return await self._as_staff(staff, lambda c: c.fetchval("SELECT platform.review_feedback_set_status($1, $2)", feedback_id, status))',
                               'return await self.pool.fetchval("SELECT platform.review_feedback_set_status($1, $2)", feedback_id, status)')],
    "内测：改状态写死 closed": [(APP, "feedback_set_status(request.state.staff, feedback_id, body.status)",
                             'feedback_set_status(request.state.staff, feedback_id, "closed")')],
    "内测：退出列表丢掉撤回类型": [(ST, '"consents_revoked": list(r["consents_revoked"])}', '"consents_revoked": []}')],
    "内测：退出列表不经 staff 身份": [(ST, 'rows = await self._as_staff(staff, lambda c: c.fetch("SELECT * FROM platform.review_internal_test_exits($1)", limit))',
                                'rows = await self.pool.fetch("SELECT * FROM platform.review_internal_test_exits($1)", limit)')],
}


# 发布门禁的 C9 检查（specs/tech-debt-batch3 B，review_console/admission_export.py 的 verify）：断言 tests/test_admission_export.py 不连库的用例能发现
AX_TESTS = ["tests/test_admission_export.py", "-k", "hint_scan"]
AX = "review_console/admission_export.py"
AX_PURE: dict[str, list[tuple[str, str, str]]] = {
    "C9 非严格模式也放行": [(AX, 'or mode != "strict" or not isinstance(flagged, list):', 'or not isinstance(flagged, list):')],
    "C9 检查项不核对": [(AX, 'scan.get("check") != "C9" or ', '')],
    "C9 标记的题不列": [(AX, '        scan_problems.append({"item_id": iid, "pack": None, "platform": None, "reason": "hint_scan_flagged"})',
                     '        pass')],
    "C9 问题不计入不合格": [(AX, "    violations = scan_problems + violations\n", "")],
    "缺 C9 检查当严格零标记": [(AX, '    scan = pack.get("hint_scan")\n',
                          '    scan = pack.get("hint_scan") or {"check": "C9", "mode": "strict", "flagged_items": []}\n')],
}

# 请求体导出（W13 T35，specs/review-console-types-t35）：后端改了请求体模型而没重新导出，tests/test_review_requests_export.py 必须发现
RQ_TESTS = ["tests/test_review_requests_export.py"]
RQ_APP = "review_console/app.py"
RQ_PURE: dict[str, list[tuple[str, str, str]]] = {
    "签字请求加必填字段不导出": [(RQ_APP, "class SignBody(_Strict):\n    decision:", "class SignBody(_Strict):\n    reason: str\n    decision:")],
    "反馈状态枚举加取值不导出": [(RQ_APP, 'status: Literal["new", "triaged", "closed"]', 'status: Literal["new", "triaged", "closed", "reopened"]')],
    "可选字段改必填不导出": [(RQ_APP, "    target_id: uuid.UUID | None = None", "    target_id: uuid.UUID")],
}

# 响应模型（specs/review-console-responses）：导出过期由 tests/test_review_responses_export.py 发现；服务层与模型对不上由库上的审核台测试发现
# （响应校验 500）
RS_TESTS = ["tests/test_review_responses_export.py"]
RS = "review_console/responses.py"
RS_PURE: dict[str, list[tuple[str, str, str]]] = {
    "响应模型删字段不导出": [(RS, "    scope: str\n    cognitive_levels: list[str]\n    error_type: str | None\n",
                         "    scope: str\n    cognitive_levels: list[str]\n")],
    "响应枚举加取值不导出": [(RS, 'Level = Literal["low", "medium", "high"]', 'Level = Literal["low", "medium", "high", "critical"]')],
}
RS_DB_TESTS = ["tests/test_review_console.py"]  # test_review_pages.py 有一条要读知识服务代码，临时副本里没有
RS_DB: dict[str, list[tuple[str, str, str]]] = {
    "服务层多拼一个键": [(SV, '        "signoffs": signoffs,\n    })', '        "signoffs": signoffs,\n        "debug": True,\n    })')],
    "有时不给的字段改必填": [(RS, "    rubric_version: str = None", "    rubric_version: str")],
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
    rc = (group("纯函数", PURE, PURE_TESTS, 6) or group("内测纯函数", LT6_PURE, LT6_PURE_TESTS, 4)
          or group("发布门禁 C9", AX_PURE, AX_TESTS, 5) or group("请求体导出", RQ_PURE, RQ_TESTS, 3)
          or group("响应导出", RS_PURE, RS_TESTS, 2))
    if rc:
        return rc
    from glob import glob
    if not (os.environ.get("PGBIN") or glob("/usr/lib/postgresql/*/bin/initdb")):
        print("review-console-mutation（库上）：跳过（本机没有 Postgres 服务端）")
        return 3
    return (group("库上", DB, DB_TESTS, 4) or group("内测库上", LT6_DB, LT6_DB_TESTS, 4)
            or group("响应模型库上", RS_DB, RS_DB_TESTS, 2))


if __name__ == "__main__":
    sys.exit(main())
