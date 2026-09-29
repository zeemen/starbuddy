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
    "留出集比例不查": ("scorer/cases.py", "if abs(k - round(n * HOLDOUT_RATIO)) > 1 or k == 0:", "if False:"),
    "星亮不看引擎事件": ("scorer/judge.py", '        if engine.get("lit_event"):\n            return hits', '        if True:\n            return hits'),
    "共性陈述不要求字段": ("scorer/judge.py", 'if ok and not items and lex.patterns["common_cause_claim"]["regex"].search(text):', "if False:"),
    "负向用例只查不出现也放行": ("scorer/cases.py", 'if c["polarity"] == "negative" and all(ch.get("expect") == "no_match" for ch in c["checks"]):', "if False:"),
    "E-03 不放过与自己过去比较": ("diandian/lexicon/forbidden.yaml", "的星比(?!上周|上个?月|昨天|前天|之前|以前|过去)", "的星比"),
    "不扫个人信息": ("scorer/cases.py", "return [name for name, pat in PII_PATTERNS.items() if pat.search(t)]", "return []"),
    "不做全角与分隔符归一化": ("scorer/cases.py", 'return re.sub(r"[\\s\\-‐‑–—]", "", unicodedata.normalize("NFKC", text))[:MAX_TEXT]', "return text[:MAX_TEXT]"),
    "不扫夹具与词表": ("scorer/cases.py", "    for f in extra_files:", "    for f in ():"),
    "全流程允许写进仓库": ("diandian/run.py", "if out_dir == ROOT.resolve() or ROOT.resolve() in out_dir.parents:", "if False:"),
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
    # 冲刺 2-I：对照运行器与首轮差异报告
    "自助法种子不固定": ("scorer/compare_report.py", "rng = random.Random(seed)", "rng = random.Random()"),
    "配对差值方向反了": ("scorer/compare_report.py", "d = [x - y for x, y in zip(a, b)]", "d = [y - x for x, y in zip(a, b)]"),
    "用例级得分只取第一次": ("scorer/compare_report.py", '"_case_score": {c: sum(per_case[c]) / len(per_case[c]) for c in case_ids}',
                   '"_case_score": {c: float(per_case[c][0]) for c in case_ids}'),
    "不稳定按失败计": ("scorer/compare_report.py", "unstable = [c for c in case_ids if len(set(per_case[c])) > 1]",
                "unstable = [c for c in case_ids if not all(per_case[c])]"),
    "红线不单列": ("scorer/compare_report.py", 'for v in rep["redline_violations"]]', "for v in []]"),
    "区间上界取错分位": ("scorer/compare_report.py", "hi = means[int(math.ceil((1 - alpha) * resamples)) - 1]", "hi = means[-1]"),
    "对照默认跑留出集": ("diandian/compare.py", 'cases = [c for c in all_cases if c["case_id"] not in holdout]', "cases = list(all_cases)"),
    "对照允许写进仓库": ("diandian/compare.py", "    if _inside(out, ROOT):", "    if False:"),
    "境外候选放行": ("diandian/compare.py", "if v.residency is not Residency.DOMESTIC:", "if False:"),
    "缺人设 Prompt 也放行真实运行": ("diandian/compare.py", '    if env != "test":\n        raise CompareError(f"人设 Prompt', '    if False:\n        raise CompareError(f"人设 Prompt'),
    # 冲刺 2-I 安全审查（S1、S3、S5、N1、N2）与契约审查
    "非 test 不查 allowed_hosts": ("diandian/compare.py", 'if env != "test" and not v.allowed_hosts:', "if False:"),
    "非 dev 不要求 https": ("diandian/compare.py", 'if env != "dev" and parts.scheme != "https":', "if False:"),
    "note 不查换行与竖线": ("diandian/compare.py", "    if _NOTE_FORBIDDEN.search(note):", "    if False:"),
    "留出集条数不核对": ("diandian/compare.py", "if len(ids) != len(set(ids)) or len(ids) != HOLDOUT_FROZEN_COUNT:", "if False:"),
    "调用上限不查": ("diandian/compare.py", "    if planned > limit:", "    if False:"),
    "非 test 不要求显式上限": ("diandian/compare.py", 'if env != "test" and max_calls is None:', "if False:"),
    "候选可匹配非 T1 路由": ("diandian/compare.py",
                     'targets = [copy.deepcopy(t) for t in (t1.get("primary"), t1.get("backup")) if same(t)]',
                     'targets = [copy.deepcopy(t) for r in routes.values() if isinstance(r, dict) for t in (r.get("primary"), r.get("backup")) if same(t)]'),
    "推理参数不取 T1 路由": ("diandian/compare.py", "return dict(self.config.routes[CANDIDATE_TASK].primary.params)", "return {}"),
    "对照允许 pilot、prod": ("diandian/compare.py", "    if env not in RUN_ENVS:", "    if False:"),
    "结构字段检查点按全过计": ("scorer/compare_report.py", 'struct_passed += sum(c["verdict"] == "pass" for c in structural)',
                     "struct_passed += len(structural)"),
    "身份披露模式失效": ("diandian/lexicon/forbidden.yaml", 'regex: "(AI|人工智能).{0,12}(扮演|陪练|角色|搭子)|我是\\\\s*(AI|人工智能)|(?<!谁说我)(?<!谁说)不是真人"', 'regex: "(?!)"'),
}


def main() -> int:
    missed: list[str] = []
    for name, (rel, pattern, replacement) in MUTATIONS.items():
        with tempfile.TemporaryDirectory(prefix="diandian-mut.") as tmp:
            dst = Path(tmp) / "evals" / "persona"
            shutil.copytree(HERE, dst, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "scripts"))
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
