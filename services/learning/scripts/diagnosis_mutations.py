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
E, SV, SRC, ST, PR = ("learning/diagnosis/engine.py", "learning/diagnosis/service.py", "learning/diagnosis/source.py",
                      "learning/diagnosis/store.py", "learning/diagnosis/presenter.py")

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
        (SV, 'f"{self._prefix(s, seq)}{n}"', 'f"{self._prefix(s, seq)}{uuid.uuid4().hex[:6]}"')],
    "续做时新开会话（另占一份额度）": [(SV, "s = await store.open_session(conn, student, subject, lock=True)", "s = None")],
    "过程题不进复核队列": [(SV, "await store.enqueue_process_observation(conn, subject=s.subject, student=s.student_id, step_id=step.id)",
                   "pass")],
    "过程题的书写原文不入库": [(SV, "await self._record_work(conn, s, step, work, occurred_at, summary)", "pass")],
    "作答返回对错": [(SV, '"usage_id": s.usage_id,\n                    "replayed": replayed}',
                  '"usage_id": s.usage_id,\n                    "replayed": replayed, "verdict": recorded.get("verdict")}')],
    "题面带出夹具字段（节点、答案等）": [(PR, "return _clean(view)", "return dict(it)")],
    "探针不核对题目": [(SV, 'or p["item_id"] != cur.item_id\n', "\n")],
    "别的学生能读写这个会话": [(ST, "WHERE id = $1::uuid AND student_id = $2::uuid FOR UPDATE", "WHERE id = $1::uuid AND $2::text IS NOT NULL FOR UPDATE")],
    "暂停中仍可作答": [(E, '    if session_status == "paused":\n        return State.PAUSED\n', "")],
    "合成题面在非 test 环境可用": [(SRC, 'if env != "test":\n        raise RuntimeError', 'if False:\n        raise RuntimeError')],
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
