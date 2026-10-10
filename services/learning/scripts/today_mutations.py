"""今日计划跨科编排（PLT-11，specs/today-plan-orchestration-plt11）的变异测试：把去重、排序、预算截断、来源失败降级、只取 due、
只读、开关与鉴权、响应字段等关键检查逐条改坏，断言今日编排的测试能发现（spec 验收：去重失效、截断失效、来源失败整体报错、泄露字段）。

做法与复测的变异脚本相同（复用 scripts/practice_mutations.py 的临时目录复制与运行）：每个变异在临时目录里改一处源码再跑测试；
只有“测试失败”（退出码 1）算发现。先跑一次不变异的对照组，必须通过。两组：
- 纯函数：tests/test_today_pure.py，不连库；
- 库上：tests/test_today_service.py，一次性 Postgres（每个变异起一个库）；没有本机 Postgres 时退出码 3（Makefile 决定是否算失败）。
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
PURE_TESTS = ["tests/test_today_pure.py"]
DB_TESTS = ["tests/test_today_service.py"]
MG, C, SV, API, APP = ("learning/today/merge.py", "learning/today/config.py", "learning/today/service.py", "learning/today/api.py",
                       "learning/app.py")

PURE: dict[str, list[tuple[str, str, str]]] = {
    # 去重
    "计划 retest 任务不去重": [(MG, "kept_plan = [p for p in plan if not (p.kind == \"retest\" and p.node_id is not None and (p.subject, p.node_id) in covered)]",
                            "kept_plan = list(plan)")],
    "去重不看任务类别（同节点的计划任务全去掉）": [(MG, 'if not (p.kind == "retest" and p.node_id is not None', "if not (p.node_id is not None")],
    "去重不分学科": [(MG, "and (p.subject, p.node_id) in covered)]", "and p.node_id in {n for _, n in covered})]")],
    "再练不与复测去重": [(MG, "kept_practice = [p for p in practice if (p.subject, p.node_id) not in covered]", "kept_practice = list(practice)")],
    "再练与复测同节点时留再练": [(MG, "return list(retest), kept_practice, kept_plan",
                              "return [r for r in retest if (r.subject, r.node_id) not in {(p.subject, p.node_id) for p in practice}], "
                              "list(practice), kept_plan")],
    # 排序
    "优先级不按配置": [(MG, "    for source in priority:\n", "    for source in (\"retest\", \"practice\", \"plan\"):\n")],
    "不做学科轮转": [(MG, "out += rotate(groups.get(source, ()), subjects)", "out += list(groups.get(source, ()))")],
    "轮转不按请求里学科的顺序": [(MG, "    per = {s: [i for i in items if i.subject == s] for s in subjects}",
                              "    subjects = sorted(subjects)\n    per = {s: [i for i in items if i.subject == s] for s in subjects}")],
    # 截断
    "截断失效": [(MG, "if kept and total + i.estimated_minutes > budget:", "if False:")],
    "截断后跳着往后塞": [(MG, "            return kept, len(items) - n", "            continue")],
    "截掉的计数恒为 0": [(MG, "            return kept, len(items) - n", "            return kept, 0")],
    "第一条超预算时整张列表为空": [(MG, "if kept and total + i.estimated_minutes > budget:", "if total + i.estimated_minutes > budget:")],
    "预算等于时也截": [(MG, "if kept and total + i.estimated_minutes > budget:", "if kept and total + i.estimated_minutes >= budget:")],
    # 字段与配置
    "条目多带置信度（泄露字段）": [(MG, "        return asdict(self)", "        return {**asdict(self), \"confidence\": 1.0}")],
    "预算范围放宽": [(C, "BUDGET_RANGE = (20, 40)", "BUDGET_RANGE = (1, 600)")],
    "来源优先级可以缺项": [(C, "_need(isinstance(pr, list) and sorted(pr) == sorted(SOURCES),", "_need(isinstance(pr, list),")],
    "合成配置在 pilot 也能用": [(C, "if env not in ALLOWED_ENVS and not cfg.signed:", "if False:")],
    "请求可以带未知键": [(SV, "or set(body) - QUERY_KEYS - OPTIONAL_KEYS:", ":")],
}

DB: dict[str, list[tuple[str, str, str]]] = {
    # 来源失败降级（spec 验收：来源失败整体报错必须被发现）
    "复测来源失败整体报错": [(SV, "        except Exception:  # noqa: BLE001 来源失败（连接池忙、读库出错、条目不合格）：只跳过这一来源\n",
                    "        except IngestError:\n")],
    "复测条目不合格整体报错（只接住连接类错误）": [(SV, "        except Exception:  # noqa: BLE001 来源失败（连接池忙、读库出错、条目不合格）：只跳过这一来源\n",
                                     "        except (ConnectionError, OSError, RuntimeError):\n")],
    "再练来源失败整体报错": [(SV, "        except Exception:  # noqa: BLE001 同上\n", "        except IngestError:\n")],
    "计划来源失败整体报错": [(SV, "        except Exception:  # noqa: BLE001 计划整体失败：各科的计划来源都不可用（BFF 按科也显示“暂无安排”）\n",
                    "        except IngestError:\n")],
    "来源状态一律标 ok": [(SV, '"status": by_source[src][s][0]}', '"status": "ok"}')],
    "未启用标成不可用（计入 partial）": [(SV, "            return DISABLED, None                 # 复测模块没启动", "            return UNAVAILABLE, None  # 复测模块没启动")],
    "没配适配器的计划标成不可用": [(SV, "out: dict[str, Got] = {s: (DISABLED, None) if s not in self.settings.adapters else (UNAVAILABLE, None) for s in subjects}",
                          "out: dict[str, Got] = {s: (UNAVAILABLE, None) for s in subjects}")],
    # 只取 due
    "复测列表的不可用条目也列": [(SV, 'if e["status"] != "due":\n                    continue\n', "")],
    "再练列表的不可用条目也列": [(SV, 'for e in out["entries"] if e["status"] == "due"]', 'for e in out["entries"]]')],
    # 字段（spec 验收：泄露字段必须被发现）
    "响应带出单元层次": [(SV, 'return {"items": [i.view() for i in items],', 'return {"items": [{**i.view(), "cognitive_level": "apply"} for i in items],')],
    "响应带出证据 ID": [(SV, '"plan": digest}', '"plan": digest, "evidence_ids": []}')],
    # 每页只排一次计划：附带的计划与 /internal/v1/plan 一致
    "不附带计划": [(SV, '"plan": digest}', '"plan": None}')],
    "计划带上预算（与按科 /plan 不一致）": [(SV, 'query = {"request_id": str(uuid.uuid4()), "student_ref": student, "subjects": list(subjects)}',
                                 'query = {"request_id": str(uuid.uuid4()), "student_ref": student, "subjects": list(subjects), '
                                 '"time_budget_minutes": 30}')],
    "复测条目带出任务原因": [(SV, 'minutes[e["kind"]], "retest", e["entry_id"], None)', 'minutes[e["kind"]], "retest", e["entry_id"], "scheduled")')],
    # 数值
    "复测条目分钟取错": [(SV, 'minutes[e["kind"]], "retest"', 'minutes["practice"], "retest"')],
    "预算缺省不取配置": [(SV, 'q.get("time_budget_minutes", self.cfg.default_budget_minutes)', 'q.get("time_budget_minutes", 40)')],
    "计划任务丢掉目标节点（去重失效）": [(SV, '(t["target_node_ids"] or [None])[0]', "None")],
    # 只读（不写任何表）
    "汇总时写一行": [(SV, "        items, cut = M.build(",
                  "        _pool = self.state[\"plan\"][0]\n"
                  "        await _pool.execute(\"INSERT INTO platform.replay_seal (method, idempotency_key, student_id, usage_id) "
                  "VALUES ('grade', $1, $2::uuid, $3::uuid)\", str(uuid.uuid4()), student, str(uuid.uuid4()))\n"
                  "        items, cut = M.build(")],
    # 审查意见 3：只比行数发现不了 UPDATE；库上测试比内容校验和
    "汇总时改一行（UPDATE）": [(SV, '        log.info(json.dumps({"event": "today"',
                          "        async with self.state[\"plan\"][0].acquire() as _c:\n"
                          "            async with _c.transaction():\n"
                          "                await _c.execute(\"SET LOCAL ROLE starbuddy_learning\")\n"
                          "                await _c.execute(\"UPDATE platform.retest_task SET status = 'cancelled' "
                          "WHERE student_id = $1::uuid AND status = 'open'\", student)\n"
                          '        log.info(json.dumps({"event": "today"')],
    # 开关与鉴权
    "鉴权不查": [(API, "if not authorized(request):", "if False:")],
    "不看 LEARNING_INTERNAL_V1": [(API, "if t is None or not internal_v1_enabled():", "if t is None:")],
    "也认编排层令牌": [(APP, 'today_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)',
                    'today_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential) or '
                    '_bearer_ok(r.headers.get("authorization"), settings.orchestrator_client_credential or "-"), state)')],
    "未配置也启动": [(APP, "        if today_cfg is not None:\n", "        if True:\n")],
}


def group(label: str, mutations: dict[str, list[tuple[str, str, str]]], tests: list[str], workers: int) -> int:
    with tempfile.TemporaryDirectory(prefix="today-mut.") as t:
        rc, out = PM._run(PM._copy(Path(t)), tests)
    if rc != 0:
        print(out)
        print(f"today-mutation（{label}）：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda kv: PM.one(kv[0], kv[1], tests), mutations.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"today-mutation（{label}）：失败，{len(missed)}/{len(mutations)} 个变异没有被发现")
        return 1
    print(f"today-mutation（{label}）：通过（{len(mutations)} 个变异全部被发现）")
    return 0


def main() -> int:
    if not (HERE / ".venv/bin/python").exists():
        print("today-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    rc = group("纯函数", PURE, PURE_TESTS, 6)
    if rc:
        return rc
    from glob import glob
    if not (os.environ.get("PGBIN") or glob("/usr/lib/postgresql/*/bin/initdb")):
        print("today-mutation（库上）：跳过（本机没有 Postgres 服务端）")
        return 3
    return group("库上", DB, DB_TESTS, 4)


if __name__ == "__main__":
    sys.exit(main())
