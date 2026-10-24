"""learning-internal v1 签字后的写入防护（LT-04，specs/launch-lt04-learning-internal-v1 决定记录与决定记录（续）第 2–4 条）的变异测试：
把服务身份限定、计量核对（放行状态、方法、重放先于核对、409 码）、用途与证据等级的服务端推导逐条改坏，断言写入路径的测试能发现；
决定记录（再续）起另有 v0 写入下线（410）、启动核对执行权、测试夹具防护（不授学习服务角色、只进一次性测试库）。
诊断会话的放行规则（started_in_time）的变异在 scripts/diagnosis_mutations.py；BFF 的契约版本校验在 services/bff/tests/mutations.sh。

做法同 scripts/diagnosis_mutations.py：每个变异在临时目录里改一处源码再跑测试；只有“测试失败”（退出码 1）算发现，
收集错误、导入错误不算。临时目录保持仓库结构（services/learning 复制，db、adapters、packages、evals、docs 软链接回仓库）。
先跑一次不变异的对照组，必须通过。需要本机 Postgres；缺依赖时退出码 3（Makefile 决定是否算失败）。
数据库函数本身（0058 的判据与授权）的变异在 db/tests/mutations.sh（DBMUT_FILTER=计量核对）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]          # services/learning
REPO = HERE.parents[1]
TESTS = ["tests/test_internal_v1_guard.py", "tests/test_evidence_attributes.py", "tests/test_v1_replay.py", "tests/test_processing_gate.py"]
SV, AT, LM, APP, PR = ("learning/service.py", "learning/attributes.py", "learning/meter.py", "learning/app.py", "learning/processing.py")

# 名字 → [(文件, 原文, 替换为), …]
MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # 计量核对
    "v1 写入不核对计量": [(SV, "                    await M.require(self.pool, meter, method=method, ctx=ctx)\n", "                    pass\n")],
    "直接写入也放行已结算": [(LM, 'DIRECT = MeterBasis(frozenset({"reserved"}))', 'DIRECT = MeterBasis(frozenset({"reserved", "settled"}))')],
    "直接写入也放行过期未处理": [(LM, 'DIRECT = MeterBasis(frozenset({"reserved"}))', 'DIRECT = MeterBasis(frozenset({"reserved", "lapsed"}))')],
    "直接写入也认自练计次": [(LM, 'DIRECT = MeterBasis(frozenset({"reserved"}))', 'DIRECT = MeterBasis(frozenset({"reserved", "counted"}))')],
    "核对方法写死 grade": [(LM, "method=basis.method or method)", 'method="grade")')],
    "只核对幂等键、usage_id 用预留里的": [(LM, 'usage = basis.usage_id if basis.usage_id is not None else ctx.get("usage_id")',
                                  'usage = basis.usage_id if basis.usage_id is not None else ctx.get("usage_id")\n'
                                  '    usage = usage if basis.usage_id is not None else None')],
    "核对不通过回 400（不是 409 IDEMPOTENCY_KEY_SEALED）": [(LM, 'raise IngestError(409, "IDEMPOTENCY_KEY_SEALED", SEALED)',
                                                      'raise IngestError(400, "INVALID_REQUEST", SEALED)')],
    "核对不通过可重试": [(LM, 'raise IngestError(409, "IDEMPOTENCY_KEY_SEALED", SEALED)',
                   'raise IngestError(409, "IDEMPOTENCY_KEY_SEALED", SEALED, retryable=True)')],
    "核对用请求里的另一个 usage_id": [(LM, 'usage = basis.usage_id if basis.usage_id is not None else ctx.get("usage_id")',
                                'usage = basis.usage_id if basis.usage_id is not None else ctx.get("request_id")')],
    "幂等重放也核对计量（结算后重试拿不回原结果）": [(SV, '            if c.kind == "done":\n                return c.summary or {}, True',
                                       '            if c.kind == "done":\n                if ingest is not None:\n'
                                       '                    await M.require(self.pool, meter, method=method, ctx=ctx)\n'
                                       '                return c.summary or {}, True')],
    # 用途与证据等级由学习服务推导
    "直接写入可自报用途": [(AT, 'if purpose != "practice" and not trusted_purpose:', "if False:")],
    "可信调用方也不能写复测用途": [(AT, 'if purpose != "practice" and not trusted_purpose:', 'if purpose != "practice":')],
    "可自报证据等级": [(AT, 'if (method == "grade" and request.get("evidence_level") is not None) or ingest.get("evidence_level") is not None:',
                  "if False:")],
    "非键盘通道放行": [(AT, '    if channel != "keypad":\n', '    if False:\n')],
    "申诉不记 C": [(AT, 'return Attributes(channel=channel, evidence_level="C", purpose=purpose)',
                'return Attributes(channel=channel, evidence_level="A", purpose=purpose)')],
    # 服务身份
    "编排层令牌也能写": [(APP, 'if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):\n'
                            '                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")\n'
                            '            raw = await _read_limited(request)\n            try:\n                body = strict_json(raw)\n'
                            '            except ValueError:\n                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None\n'
                            '            inner =',
                       'if not (_bearer_ok(request.headers.get("authorization"), settings.service_credential) or '
                       '_bearer_ok(request.headers.get("authorization"), settings.orchestrator_client_credential or "-")):\n'
                       '                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")\n'
                       '            raw = await _read_limited(request)\n            try:\n                body = strict_json(raw)\n'
                       '            except ValueError:\n                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None\n'
                       '            inner =')],
    "洞察服务令牌也能写": [(APP, 'if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):\n'
                             '                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")\n'
                             '            raw = await _read_limited(request)\n            try:\n                body = strict_json(raw)\n'
                             '            except ValueError:\n                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None\n'
                             '            inner =',
                        'if not (_bearer_ok(request.headers.get("authorization"), settings.service_credential) or '
                        '_bearer_ok(request.headers.get("authorization"), settings.insight_client_credential or "-")):\n'
                        '                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")\n'
                        '            raw = await _read_limited(request)\n            try:\n                body = strict_json(raw)\n'
                        '            except ValueError:\n                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None\n'
                        '            inner =')],
    "启动不查迁移 0058": [(APP, 'if not await p.fetchval("SELECT to_regprocedure($1) IS NOT NULL", store.METER_CHECK_FUNCTION):', "if False:")],
    # 决定记录（再续）第 4 条：启动时同时核对执行权
    "启动不查核对函数的执行权": [(APP, """if not await p.fetchval("SELECT has_function_privilege(current_user, $1, 'EXECUTE')", store.METER_CHECK_FUNCTION):""",
                         "if False:")],
    # 决定记录（再续）第 1 条：v0 写入下线（410）
    "v0 判分写入仍执行": [(APP, 'return retired_v0("grade")', 'return await handle("grade", request, "0.9.0")')],
    "v0 诊断写入仍执行": [(APP, 'return retired_v0("diagnose")', 'return await handle("diagnose", request, "0.9.0")')],
    "v0 下线回 404": [(APP, 'return _problem(IngestError(410, "INVALID_REQUEST", V0_RETIRED), None)',
                    'return _problem(IngestError(404, "INVALID_REQUEST", V0_RETIRED), None)')],
    "v0 下线标成可重试（BFF 不释放预留）": [(APP, 'return _problem(IngestError(410, "INVALID_REQUEST", V0_RETIRED), None)',
                                  'return _problem(IngestError(410, "INVALID_REQUEST", V0_RETIRED, retryable=True), None)')],
    "v0 下线前先读请求体": [(APP, 'return retired_v0("grade")', 'await request.body()\n        return retired_v0("grade")')],
    # 测试夹具防护（安全审查阻断）：代建函数不授学习服务角色；夹具只进 starbuddy_dev* / starbuddy_test* 库
    "夹具把代建函数授给学习服务": [("tests/fixtures/meter_provision.sql",
                         "REVOKE EXECUTE ON FUNCTION learningtest.provision_meter(uuid, text, uuid, text, boolean, boolean) FROM starbuddy_learning;",
                         "GRANT USAGE ON SCHEMA learningtest TO starbuddy_learning;\n"
                         "GRANT EXECUTE ON FUNCTION learningtest.provision_meter(uuid, text, uuid, text, boolean, boolean) TO starbuddy_learning;")],
    "夹具不核对库名": [("tests/fixtures/meter_provision.sql", "  IF current_database() !~ '^starbuddy_(dev|test)' THEN\n    RAISE EXCEPTION 'meter_provision.sql:",
                    "  IF false THEN\n    RAISE EXCEPTION 'meter_provision.sql:")],
    "夹具防护失败后继续执行": [("tests/fixtures/meter_provision.sql", "\\set ON_ERROR_STOP on\n", "\\set ON_ERROR_STOP off\n")],
    # LT-06 决定记录（续）第 4 条：库层处理判据（learning/processing.py 及各入口；tests/test_processing_gate.py）
    "处理判据：v1 写入不问库": [(SV, '        await processing.require(self.pool, self.processing, ctx["student_ref"])\n', "")],
    "处理判据：启动时当作不可用": [(APP, '        state["processing"] = await processing.available(p)', '        state["processing"] = False')],
    "处理判据：今日计划不问库": [(APP, "            await processing.require(pool_, bool(state.get(\"processing\")), body.get(\"student_ref\")",
                         "            pass  # (pool_, bool(state.get(\"processing\")), body.get(\"student_ref\")")],
    "处理判据：拦下的判据写反": [(PR, 'AND NOT platform.student_processing_allowed($1::uuid)', 'AND platform.student_processing_allowed($1::uuid)')],
    "处理判据：拦下不回 404": [(PR, 'raise IngestError(404, "STUDENT_NOT_FOUND", NOT_AVAILABLE)', 'raise IngestError(409, "STUDENT_NOT_FOUND", NOT_AVAILABLE)')],
    "处理判据：大写 UUID 绕过": [(PR, "    s = student.lower()\n", "    s = student\n    if s != s.lower():\n        return\n")],
    "处理判据：诊断会话不问库": [("learning/diagnosis/api.py", "    allowed = processing.gate(state)\n",
                         "    async def allowed(_s):\n        return None\n")],
    "处理判据：再练不问库": [("learning/practice/api.py", "    allowed = processing.gate(state)\n", "    async def allowed(_s):\n        return None\n")],
    "处理判据：复测不问库": [("learning/retest/api.py", "    allowed = processing.gate(state)\n", "    async def allowed(_s):\n        return None\n")],
    "处理判据：档案页与验证任务不问库": [("learning/mastery_routes.py", "    allowed = processing.gate(state)\n",
                              "    async def allowed(_s):\n        return None\n")],
    "处理判据：今日编排不问库": [("learning/today/api.py",
                         '            await processing.require(state.get("pool"), bool(state.get("processing")), body.get("student_ref"))\n', "")],
    "处理判据：掌握对账选人不过滤": [("learning/mastery/jobs.py", "    gate = await processing.available(engine.pool)", "    gate = False")],
}


def _copy(tmp: Path) -> Path:
    root = tmp / "repo"
    dst = root / "services" / "learning"
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__", ".venv", ".cache", "*.pyc"))
    for name in ("db", "adapters", "packages", "evals", "docs"):
        (root / name).symlink_to(REPO / name)
    return dst


def _run(dst: Path) -> tuple[int, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "LEARNING_TEST_STRICT": "1",
           "HOME": os.environ.get("HOME", "/tmp")}
    r = subprocess.run([str(HERE / ".venv/bin/python"), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                       cwd=dst, capture_output=True, text=True, env=env, timeout=600)
    return r.returncode, r.stdout[-2000:]


def one(name: str, edits: list[tuple[str, str, str]]) -> str | None:
    with tempfile.TemporaryDirectory(prefix="internal-v1-mut.") as t:
        dst = _copy(Path(t))
        for fname, pattern, replacement in edits:
            f = dst / fname
            text = f.read_text(encoding="utf-8")
            if pattern not in text:
                return f"变异本身写错（找不到原文）：{name}"
            f.write_text(text.replace(pattern, replacement, 1), encoding="utf-8")
        rc, _ = _run(dst)
        if rc != 1:
            return f"{'漏检' if rc == 0 else f'不是被断言发现（退出码 {rc}）'}：{name}"
    return None


def main() -> int:
    if not (HERE / ".venv/bin/python").exists():
        print("internal-v1-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    with tempfile.TemporaryDirectory(prefix="internal-v1-mut.") as t:
        rc, out = _run(_copy(Path(t)))
    if rc != 0:
        print(out)
        if "skipped" in out and "passed" not in out:
            print("internal-v1-mutation: 跳过（本机没有 Postgres）")
            return 3
        print(f"internal-v1-mutation: 失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda kv: one(*kv), MUTATIONS.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"internal-v1-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"internal-v1-mutation: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
