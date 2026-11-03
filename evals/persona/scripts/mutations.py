"""点点评分器与人设治理组件的变异测试（specs/diandian-cases-v0-sprint1/spec.md 设计要点 4；specs/persona-governance-sprint3 验收；
specs/diandian-consistency-r2-sprint3 后处理、两组数字、修订与验收判定）。

每个变异把评分器、词表、guard 分类器、登记表检查、七类家族级检查器、漂移 / 泄露检查器或回放框架的一处关键判定改坏，
复制到临时目录里跑指定的测试，断言测试能发现。只有测试断言失败（退出码 1）算发现；收集或导入错误不算。
临时目录保持仓库结构：evals/persona 与 packages/personas 复制一份（变异要改它们），其余顶层目录软链接回仓库。
先跑一次不变异的对照，必须通过；变异并行执行（默认 4 路）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
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
    "E-03 不放过与自己过去比较": ("packages/personas/forbidden/E.yaml", "的星比(?!上周|上个?月|昨天|前天|之前|以前|过去)", "的星比"),
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
    "词表丢了 D 类亲密称呼": ("packages/personas/forbidden/D.yaml", 'method: rule, pattern: "(亲爱的|宝贝', 'method: rule, pattern: "(不存在的称呼|不存在'),
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
    "身份披露模式失效": ("packages/personas/patterns/diandian.yaml", 'regex: "(AI|人工智能).{0,12}(扮演|陪练|角色|搭子)|我是\\\\s*(AI|人工智能)|(?<!谁说我)(?<!谁说)不是真人"', 'regex: "(?!)"'),
}


# 冲刺 3-F 人设治理组件：(文件（相对仓库根）, 原文, 替换为, 测试（相对仓库根，空格分隔）)
GUARD_T = "packages/personas/tests/test_guard.py"
REG_T = "packages/personas/tests/test_registry.py"
GOV_T = "evals/persona/tests/test_governance.py"
G = "packages/personas/guard/__init__.py"
R = "packages/personas/registry.py"
F = "evals/persona/governance/family.py"
D = "evals/persona/governance/drift.py"
P = "evals/persona/governance/replay.py"
RUN = "evals/persona/governance/run.py"
GOVERNANCE: dict[str, tuple[str, str, str, str]] = {
    # guard 分类器：规则层与结构上下文层的关键判断
    "guard：规则层不匹配": (G, "return [(m.start(), m.end()) for m in lex.rx(r).finditer(text) if m.end() > m.start()]", "return []", GUARD_T),
    "guard：不按角色的 forbidden_classes（全部类都判）": (G, "        elif r.cls in classes:\n            out.append(r)", "        else:\n            out.append(r)", GUARD_T),
    "guard：平台级条目不看角色类型": (G, "if ptype in r.applies_to_types:", "if True:", GUARD_T),
    "guard：越级提示不判": (G, "used is not None and used > allowed else []", "used is not None and used > allowed + 99 else []", GUARD_T),
    "guard：对错陈述不看验证字段": (G, "return [] if verified else [(m.start(), m.end())]", "return []", GUARD_T),
    "guard：考官型非判分时刻开口不判": (G, 'return whole if c.get("grading_moment") is False else []', "return []", GUARD_T),
    "guard：星的两处限制失效": (G, 'return [(m.start(), m.end())] if m and c.get("moment") not in STAR_MOMENTS else []', "return []", GUARD_T),
    "guard：句长上限不查": (G, "if sentence_chars(t) > int(r.max_chars or 0)]", "if False]", GUARD_T),
    "guard：没有上下文也判结构层": (G, "spans = [] if context is None else _context_layer(r, text, context)", "spans = _context_layer(r, text, context or {})", GUARD_T),
    "guard：模型层条目不列为待判定": (G, "            pending.append(r.rule_id)\n            continue", "            continue", GUARD_T),
    # 登记表与指纹锁
    "登记表：内容变了版本没升不报": (R, 'if cur["sha256"] != old.get("sha256") and cur["version"] == old.get("version"):', "if False:", REG_T),
    "登记表：锁文件可以绕过升版本": (R, 'if o and o.get("sha256") != cur["sha256"] and o.get("version") == cur["version"]:', "if False:", REG_T),
    "登记表：不扫含“你是”的长字符串": (R, "if _prompt_like(m.group(0)):", "if False:", REG_T),
    "登记表：豁免不要求原因": (R, "elif not mark.group(1).strip():", "elif False:", REG_T),
    "登记表：代码版本常量不核对": (R, "if e.version_symbol and const_value(root / e.path, e.version_symbol, e.path) != e.version:", "if False:", REG_T),
    "登记表：词表旧位置不核对": (R, "    if old != new:\n", "    if False:\n", GUARD_T),
    # 七类家族级检查器
    "家族：跨科名单为空": (F, "    bad = _other_subjects(env, p.get(\"subject\")) + _other_names(env, pid)\n    return [finding(\"family_cross_subject\"",
                     "    bad = []\n    return [finding(\"family_cross_subject\"", GOV_T),
    "家族：比喻词表不做交集": (F, "        common = ta & tb\n", "        common = set()\n", GOV_T),
    "家族：转写不查别科比喻": (F, "others = [(oid, w) for oid, o in env.personas.items() if oid != pid for w in _terms(o, 2)]\n    return [finding(\"family_metaphor\"",
                      "others = []\n    return [finding(\"family_metaphor\"", GOV_T),
    "家族：称谓不按登记表": (F, "            if term:\n", "            if False:\n", GOV_T),
    "家族：叠字不查": (F, "        for n in nick:", "        for n in ():", GOV_T),
    "家族：场景角色判分讲解不查": (F, '            for rx, what in ((GRADE_RX, "判分"), (EXPLAIN_RX, "讲解")):', "            for rx, what in ():", GOV_T),
    "家族：考官型非判分环节发言不计": (F, 'for i, t in _speaker_turns(turns, pid) if (t.context or {}).get("grading_moment") is not True]',
                           "for i, t in _speaker_turns(turns, pid) if False]", GOV_T),
    "家族：转介不查真实资源": (F, '        if not res_rx.search(t.text):\n            errs.append(finding("family_referral", pid, "没有指向真实资源"',
                      '        if False:\n            errs.append(finding("family_referral", pid, "没有指向真实资源"', GOV_T),
    "家族：转介不查承接倾诉": (F, '        if CONFIDE_RX.search(t.text):\n            errs.append(finding("family_referral", pid, "承接了倾诉"',
                      '        if False:\n            errs.append(finding("family_referral", pid, "承接了倾诉"', GOV_T),
    "家族：转介不查 referral_triggered": (F, 'if t.fields.get("referral_triggered") is not True:', "if False:", GOV_T),
    "家族：合同对照表空格不查": (F, 'if not c or not str(c.get("spec") or "").strip():', "if False:", GOV_T),
    "家族：合同对照表引用不存在的用例也放过": (F, "elif cid not in known_case_ids:", "elif False:", GOV_T),
    "家族：未立项算通过": (RUN, '                acc.add(pid, f"family_{chk}", "not_run")\n                continue\n            if pid not in per:',
                    '                acc.add(pid, f"family_{chk}", "pass")\n                continue\n            if pid not in per:', GOV_T),
    # 长对话漂移与 N16 泄露
    "漂移：身份披露只查首轮": (D, "        elif shown is True:", "        elif False:", GOV_T),
    "漂移：风格窗口不查": (D, "if len(set(seg)) > 1 or None in seg:", "if False:", GOV_T),
    "漂移：设定元素总数上限不查": (D, "        if total > cap:", "        if False:", GOV_T),
    "漂移：轮数不查": (D, "if not MIN_ROUNDS <= n <= MAX_ROUNDS:", "if False:", GOV_T),
    "漂移：退场后开口不查": (D, "if done is not None and done < n - 1:", "if False:", GOV_T),
    "漂移：考官型形容词副词不查": (D, "m = EXAMINER_MODIFIERS.search(t.text)", "m = None", GOV_T),
    "泄露：不比对标准答案": (D, "if allowed < reveal and any(f in norm(t.text) for f in forms):", "if False:", GOV_T),
    # 录制回放与结论
    "回放：Prompt 指纹不核对": (P, 'if doc["prompt_fingerprints"] != want_p:', "if False:", GOV_T),
    "回放：用例指纹不核对": (P, 'if doc["case_fingerprint"] != (case_fp or case_fingerprint(case)):', "if False:", GOV_T),
    "回放：缺录音非严格也算通过": (RUN, "return (1 if strict else 3), rep", "return 0, rep", GOV_T),
    "回放：严格模式不报“需要重新录制”": (RUN, 'if strict or r["status"] == "invalid":', 'if r["status"] == "invalid":', GOV_T),
    "回放：禁语命中不算失败": (RUN, "    if forbidden_items:\n        reasons.append(", "    if False:\n        reasons.append(", GOV_T),
}
# 冲刺 3-I 点点一致性二轮：后处理 v0、两组数字、修订流程、验收判定、二轮报告（只跑 test_r2.py，约 12 秒）
PP = "evals/persona/diandian/postproc/__init__.py"
R2R = "evals/persona/diandian/r2report.py"
CMP = "evals/persona/diandian/compare.py"
R2_T = "evals/persona/tests/test_r2.py"
R2: dict[str, tuple[str, str, str, str]] = {
    "后处理：禁语命中漏拦": (PP, "            if hits:\n                text = self.replacements[\"safe_reply\"].text",
                    "            if False:\n                text = self.replacements[\"safe_reply\"].text", R2_T),
    "后处理：拦截后造新句": (PP, 'text = self.replacements["safe_reply"].text', 'text = "我们先换个话题吧。"', R2_T),
    "后处理：替代句不核对规格原文": (PP, 'if text not in src.read_text(encoding="utf-8"):', "if False:", R2_T),
    "后处理：占位不查格式": (PP, "if not PLACEHOLDER.match(text):", "if False:", R2_T),
    "后处理：首轮缺披露不注入": (PP, "if first_contact and turn_index == 0 and not self._disclosure_rx.search(text):", "if False:", R2_T),
    "后处理：非首轮也注入披露": (PP, "if first_contact and turn_index == 0 and not self._disclosure_rx.search(text):",
                        "if first_contact and not self._disclosure_rx.search(text):", R2_T),
    "后处理：情感信号不转介": (PP, "        if signals:\n            text = self.replacements[\"referral\"].text",
                      "        if False:\n            text = self.replacements[\"referral\"].text", R2_T),
    "后处理：转介不置 referral 字段": (PP, 'out_fields["referral"] = True', "pass", R2_T),
    "两组数字混算（后处理组用原始回复评分）": (CMP, '"runs": _score_write(p_runs,', '"runs": _score_write(per_run,', R2_T),
    "两组数字混算（只 Prompt 组用后处理结果）": (CMP, 'sets = {"prompt_only": compare_report.build(meta, side.raw)',
                                'sets = {"prompt_only": compare_report.build(meta, side.post)', R2_T),
    "修订：某项下降仍采纳": (R2R, "if b is not None and a is not None and a < b:", "if False:", R2_T),
    "修订：禁语触发率 > 0 仍采纳": (R2R, 'if fa["triggered"] > 0:', "if False:", R2_T),
    "修订：次数上限不查": (R2R, "if not 1 <= n <= criteria.max_revisions:", "if n < 1:", R2_T),
    "修订：前后两个版本用同一套行为": (CMP, "_with_profiles(cands, rev.fake_profiles.get(name), categories)",
                           '_with_profiles(cands, rev.fake_profiles.get("after"), categories)', R2_T),
    "修订：修订链不核对": (R2R, 'if side_from(res["before"]).key() != base.key():', "if False:", R2_T),
    "修订：记录签字后被改不核对": (R2R, 'if not src.is_file() or _sha(src) != res.get("yaml_sha256"):', "if not src.is_file():", R2_T),
    "修订：Prompt 指纹不核对": (CMP, "if side.prompt_sha256 and sha != side.prompt_sha256:", "if False:", R2_T),
    "判定：差值阈值错": (R2R, "diff_ok = max_diff <= criteria.max_core_diff_pp", "diff_ok = max_diff <= criteria.max_core_diff_pp * 2", R2_T),
    "判定：核心下限不查": (R2R, "floor_ok = all(r is not None and r >= criteria.core_floor for r in rates.values())", "floor_ok = True", R2_T),
    "判定：红线不进判定": (R2R, "red_ok = all(v <= criteria.redline_max for v in red.values())", "red_ok = True", R2_T),
    "判定：按只 Prompt 一组": (R2R, 'v = per_set[basis]["verdict"]', 'v = per_set["prompt_only"]["verdict"]', R2_T),
    "报告：红线未单列（只 Prompt 一组不列）": (R2R, '    for s in SETS:\n        lines += ["", f"### 红线命中',
                                 '    for s in SETS[1:]:\n        lines += ["", f"### 红线命中', R2_T),
    "报告：占位未标“待人设负责人提供”": (R2R, 'if post and post.get("pending"):', "if False:", R2_T),
    "留出集汇总收用例级字段": (R2R, 'set(v) != {"core_rate", "redline"}', 'not {"core_rate", "redline"} <= set(v)', R2_T),
}
WORKERS = int(os.environ.get("PERSONA_MUTATION_WORKERS", "4"))
TEST_ENV = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


def _sandbox(tmp: Path) -> None:
    """保持仓库结构：evals/persona 与 packages/personas 复制，其余顶层目录与 packages 下的其他包软链接回仓库"""
    ign = shutil.ignore_patterns("__pycache__", ".pytest_cache", "scripts")
    shutil.copytree(HERE, tmp / "evals" / "persona", symlinks=True, ignore=ign)
    (tmp / "packages").mkdir()
    shutil.copytree(ROOT / "packages" / "personas", tmp / "packages" / "personas", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for p in (ROOT / "packages").iterdir():
        if p.name != "personas":
            os.symlink(p, tmp / "packages" / p.name)
    for p in ROOT.iterdir():
        if p.name not in ("evals", "packages", ".git") and not (tmp / p.name).exists():
            os.symlink(p, tmp / p.name)
    for p in (ROOT / "evals").iterdir():
        if p.name != "persona":
            os.symlink(p, tmp / "evals" / p.name)


def _pytest(tmp: Path, tests: str) -> int:
    targets = tests.split()
    cwd = tmp / "evals" / "persona" if targets == ["tests"] else tmp
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *targets], cwd=cwd,
                       capture_output=True, text=True, env=TEST_ENV)
    return r.returncode


def _one(name: str, rel: str, pattern: str, replacement: str, tests: str) -> tuple[str, str | None]:
    with tempfile.TemporaryDirectory(prefix="persona-mut.") as t:
        tmp = Path(t)
        _sandbox(tmp)
        target = tmp / rel if rel.startswith(("packages/", "evals/")) else tmp / "evals" / "persona" / rel
        src = target.read_text(encoding="utf-8")
        if src.count(pattern) != 1:
            return name, "变异本身写错（原文找不到或不唯一）"
        target.write_text(src.replace(pattern, replacement, 1), encoding="utf-8")
        rc = _pytest(tmp, tests)
        if rc != 1:
            return name, "漏检" if rc == 0 else f"不是被断言发现（退出码 {rc}）"
        return name, None


def main() -> int:
    jobs = [(n, rel, pat, rep, "tests") for n, (rel, pat, rep) in MUTATIONS.items()]
    jobs += [(n, rel, pat, rep, tests) for n, (rel, pat, rep, tests) in GOVERNANCE.items()]
    jobs += [(n, rel, pat, rep, tests) for n, (rel, pat, rep, tests) in R2.items()]
    with tempfile.TemporaryDirectory(prefix="persona-mut-control.") as t:
        _sandbox(Path(t))
        for tests in ("tests", f"{GUARD_T} {REG_T}"):
            rc = _pytest(Path(t), tests)
            if rc != 0:
                print(f"eval-persona-mutation: 对照（未变异）没有通过（{tests}，退出码 {rc}），变异结果不可信")
                return 1
    with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as ex:
        results = list(ex.map(lambda j: _one(*j), jobs))
    missed = [(n, why) for n, why in results if why]
    for n, why in missed:
        print(f"  {why}：{n}")
    if missed:
        print(f"eval-persona-mutation: 失败，{len(missed)}/{len(jobs)} 个变异没有被发现")
        return 1
    print(f"eval-persona-mutation: 通过（{len(jobs)} 个变异全部被发现：点点评分器 {len(MUTATIONS)} 个，人设治理组件 {len(GOVERNANCE)} 个，"
          f"一致性二轮 {len(R2)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
