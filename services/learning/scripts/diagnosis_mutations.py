"""自适应诊断（SIE-02a）的变异测试：把选题规则、状态机、幂等与可见范围的关键检查逐条改坏，断言诊断测试能发现。

做法同 scripts/model_router_mutations.py：每个变异在临时目录里改一处（或几处）源码再跑测试；只有“测试失败”（退出码 1）算发现，
收集错误、导入错误不算。临时目录保持仓库结构（services/learning 复制，db、adapters、packages 软链接回仓库），
测试按 parents[3] 找到的迁移、阿鸦适配器都是仓库里的原件。先跑一次不变异的对照组，必须通过。
需要本机 Postgres 与阿鸦适配器依赖（同 make learning-test）；缺依赖时退出码 3（Makefile 决定是否算失败）。
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
TESTS = ["tests/test_diagnosis_engine.py", "tests/test_diagnosis_session.py"]
E, SV, SRC, ST, PR, API, APP = ("learning/diagnosis/engine.py", "learning/diagnosis/service.py", "learning/diagnosis/source.py",
                                "learning/diagnosis/store.py", "learning/diagnosis/presenter.py", "learning/diagnosis/api.py",
                                "learning/app.py")

# 名字 → [(文件, 原文, 替换为), …]
MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    "答对不跳过前置": [(E, 'for a in sorted(source.ancestors(node)):\n                    status.setdefault(a, "skipped")',
                   'for a in []:\n                    pass')],
    "走弱边（夹具不按强边过滤）": [(SRC, 'for e in doc["edges"] if e["strength"] == "strong"]', 'for e in doc["edges"]]')],
    "知识服务答非所问也接受": [(SRC, 'if res.get("root") != root or res.get("direction") != "up":', "if False:")],
    "超时不停": [(E, "t.elapsed_sec + p.adaptive_step_sec + reserve_sec <= p.time_budget_sec", "True"),
             (E, "t.elapsed_sec + p.process_step_sec <= p.time_budget_sec", "True")],
    "题数不停": [(E, "return t.answered < cap_items and", "return"), (E, "return t.answered < p.item_limit and", "return")],
    "答错不往下追溯": [(E, "pending.update(q for q in source.prereqs(node) if q not in status)", "pass")],
    "不出确认题": [(E, 'if out == "incorrect" and len(items) > 1:', "if False:")],
    "下层优先（不是上层优先）": [(E, "key=lambda n: (-source.height(n), n)", "key=lambda n: (source.height(n), n)")],
    "中断后重放换了判分键（重复判分、重复写证据）": [
        (SV, 'if r["status"] == "succeeded":\n                    recorded = json.loads(r["summary"])\n                    break',
         'if False:\n                    break'),
        (SV, "body = self._grade_body(s, cur, answer, occurred_at, elapsed, keys[n])",
         "body = self._grade_body(s, cur, answer, occurred_at, elapsed, str(uuid.uuid4()))")],
    "续做时新开会话（另占一份额度）": [(SV, "s = await store.open_session(conn, student, subject, lock=True)", "s = None")],
    "过程题不进复核队列": [(SV, "await store.enqueue_process_observation(conn, subject=s.subject, student=s.student_id, step_id=step.id)",
                   "pass")],
    "过程题的书写原文不入库": [(SV, "await self._record_work(conn, s, step, work, occurred_at)", "pass")],
    "作答返回对错": [(SV, '"usage_id": s.usage_id,\n                    "replayed": replayed}',
                  '"usage_id": s.usage_id,\n                    "replayed": replayed, "verdict": recorded.get("verdict")}')],
    "题面带出夹具字段（节点、答案等）": [(PR, "return _clean(view)", "return dict(it)")],
    "探针不核对题目": [(SV, 'or p["item_id"] != cur.item_id\n', "\n")],
    "别的学生能读写这个会话": [(ST, "WHERE id = $1::uuid AND student_id = $2::uuid FOR UPDATE", "WHERE id = $1::uuid AND $2::text IS NOT NULL FOR UPDATE")],
    "暂停中仍可作答": [(E, '    if session_status == "paused":\n        return State.PAUSED\n', "")],
    "合成题面在非 test 环境可用": [(SRC, 'if env != "test":\n        raise RuntimeError', 'if False:\n        raise RuntimeError')],
    # 审查处理（安全审查 S1、S3、S5、S6，契约审查 B1、建议 1–4、9）
    "用时不按墙钟封顶": [(SV, "return max(0, min(reported, before + wall, 7200))", "return reported")],
    "续做不更新续做时间": [(SV, "await store.resume(conn, s.id)", 'await store.set_status(conn, s.id, "active")')],
    "同一步换内容作答不报冲突": [(SV, 'raise err(409, "ANSWER_CONFLICT", "this step was already answered with different content")',
                         "return self._replay(s, step)")],
    "过程原文证据不记到步骤上": [(SV, "await store.set_work_evidence(conn, step.id, evidence_id)", "pass")],
    "过程原文的准入跟题目走": [(SV, '"payload": {"text": work}, "admission": "pending_review"}',
                        '"payload": {"text": work}, "admission": (await store.item_admission(conn, s.subject, step.item_id or "")) or "pending_review"}')],
    "作答不标键盘通道（走 v0 写入路径）": [(SV, 'GRADE_VERSION = "1.0.0"', 'GRADE_VERSION = "0.9.0"')],
    "合成题面不核对库名": [(APP, 'if not SYNTHETIC_DB.fullmatch(db or ""):', "if False:")],
    "知识服务响应不按契约校验": [(SRC, "if knowledge_contract().validate(schema, out):", "if False:")],
    "前置链到最大深度当作没有更下层": [(SRC, 'if s.get("depth", 0) >= MAX_DEPTH:', "if False:")],
    "版权受限的题也出": [(SRC, '\n                              and i.get("license_state") != "restricted")', ")")],
    "学生 ID 可以放在 URL 里": [(API, 'if "student_ref" in request.query_params:', "if False:")],
    "探针不能按派生键找回": [(SV, 'probe_id = await store.probe_by_key(conn, student, probe_key or "")', "probe_id = None")],
    "放弃时不报已答题数": [(SV, '"item_count": s.item_count, "abandon_reason": reason, "replayed": False}',
                    '"item_count": 0, "abandon_reason": reason, "replayed": False}')],
    # 届别 PR3（specs/policy-decisions-v0.9.1 #12，迁移 0049）：会话中途届别被改、建立时刻的年级变了 → 续做 409
    "续做不核对建立时的选题年级": [(SV, "(s.start_grade is not None and grade != s.start_grade)", "False")],
    "建立会话不记选题年级": [(SV, "created_at=at, start_grade=grade)", "created_at=at, start_grade=None)")],
    "读会话不取选题年级": [(ST, "resumed_at, created_at, start_grade, abandon_reason\"\"\"", "resumed_at, created_at, abandon_reason\"\"\"")],
    "年级越界不报 409": [(SV, "return grade is None or (", "return (grade is not None) and (")],
    "409 不带会话 ID": [(SV, 'extra={"session_id": s.id, "reason": "grade_changed"}', 'extra={"reason": "grade_changed"}')],
    "放弃不记届别变化原因": [(SV, 'reason = "grade_changed" if s.start_grade is not None and grade_changed(s, grade) else None', "reason = None")],
    "视图不带放弃原因": [(SV, '"abandon_reason": s.abandon_reason, "step": None}', '"abandon_reason": None, "step": None}')],
    "重放放弃不返回原因": [(SV, '"abandon_reason": s.abandon_reason,\n                        "replayed": True}', '"abandon_reason": None,\n                        "replayed": True}')],
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
    with tempfile.TemporaryDirectory(prefix="diagnosis-mut.") as t:
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
        print("diagnosis-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    with tempfile.TemporaryDirectory(prefix="diagnosis-mut.") as t:
        rc, out = _run(_copy(Path(t)))
    if rc != 0:
        print(out)
        print(f"diagnosis-mutation: 失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda kv: one(*kv), MUTATIONS.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"diagnosis-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"diagnosis-mutation: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
