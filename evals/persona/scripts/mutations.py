"""点点评分器的变异测试（specs/diandian-cases-v0-sprint1/spec.md 设计要点 4）。

每个变异把评分器或词表的一处关键判定改坏，复制到临时目录里跑 tests/（含自检），断言测试能发现。
只有测试断言失败（退出码 1）算发现；收集或导入错误不算。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]  # evals/persona
ROOT = HERE.parents[1]

MUTATIONS: dict[str, tuple[str, str, str]] = {
    "禁语不匹配": ("scorer/judge.py", "            if cls in classes:", "            if False:"),
    "红线不进清单": ("scorer/report.py", 'if v.redline and v.verdict == "fail":', "if False:"),
    "评审检查点算失败": ("scorer/report.py", 'auto = [v for v in verdicts if v.verdict != "review"]', "auto = verdicts"),
    "any 当成 all": ("scorer/judge.py", "passed = any(ok_turns)", "passed = all(ok_turns)"),
    "first 当成 all": ("scorer/judge.py", 'if t == "first":\n        return [0] if n else []', 'if t == "first":\n        return list(range(n))'),
    "留出集种子变了": ("scorer/cases.py", 'HOLDOUT_SEED = "diandian-cases-v0"', 'HOLDOUT_SEED = "other-seed"'),
    "不扫个人信息": ("scorer/cases.py", "                if pat.search(text):", "                if False:"),
    "不查条数": ("scorer/cases.py", "        if counts.get(cat, 0) != n:", "        if False:"),
    "不查正负样例": ("scorer/cases.py", 'if pols != {"positive", "negative"}:', "if False:"),
    "星图句式不查": ("scorer/judge.py", 'if p["mention"].search(s) and not p["allowed"].search(s):', "if False:"),
    "复述标记不查": ("scorer/judge.py", "pos = text.find(marker)", "pos = -1"),
    "上下文取值不替换": ("scorer/judge.py", "return (case.get(\"context\") or {}).get(m.group(1))", "return value"),
    "根因指标不核对来源": ("scorer/judge.py", 'x.get("metric_id") in allowed', 'x.get("metric_id")'),
    "报告带出命中原文": ("scorer/judge.py", 'hits.append({"rule": rid, "start": m.start(), "end": m.end()})',
                  'hits.append({"rule": rid, "start": m.start(), "end": m.end(), "text": m.group(0)})'),
    "缺轮次算通过": ("scorer/judge.py", '        v.verdict = "fail"\n        v.hits.append({"rule": "missing_reply"', '        v.verdict = "pass"\n        v.hits.append({"rule": "missing_reply"'),
    "词表丢了 D 类亲密称呼": ("diandian/lexicon/forbidden.yaml", 'class: D, regex: "(亲爱的|宝贝', 'class: D, regex: "(不存在的称呼|不存在'),
    "身份披露模式失效": ("diandian/lexicon/forbidden.yaml", 'regex: "(AI|人工智能).{0,12}(扮演|陪练|角色)|不是真人"', 'regex: "(?!)"'),
}


def main() -> int:
    missed: list[str] = []
    for name, (rel, pattern, replacement) in MUTATIONS.items():
        with tempfile.TemporaryDirectory(prefix="diandian-mut.") as tmp:
            dst = Path(tmp) / "evals" / "persona"
            shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__", "scripts"))
            # 离线全流程要用模型路由：services 软链接回仓库
            os.symlink(ROOT / "services", Path(tmp) / "services")
            target = dst / rel
            src = target.read_text(encoding="utf-8")
            if pattern not in src:
                print(f"  变异本身写错（找不到原文）：{name}")
                missed.append(name)
                continue
            target.write_text(src.replace(pattern, replacement, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"], cwd=dst,
                               capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
            if r.returncode != 1:
                print(f"  {'漏检' if r.returncode == 0 else f'不是被断言发现（退出码 {r.returncode}）'}：{name}")
                missed.append(name)
    if missed:
        print(f"eval-persona-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"eval-persona-mutation: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
