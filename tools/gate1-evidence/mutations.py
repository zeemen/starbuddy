"""Gate 1 证据管线（specs/gate1-evidence-sie04）的变异测试：把报表的样本门槛、边界、方向、口径、配置校验与证据包标注逐条改坏，
断言 tests/test_gate1.py 能发现（spec 验收：覆盖率边界、MAE、轮间、相关方向、样本门槛）。

做法同学习服务的变异脚本：每个变异在临时目录里复制一份本工具、改一处源码再跑测试；只有“测试失败”（退出码 1）算发现。
先跑一次不变异的对照组，必须通过。跑变异时不选“与仓库样例逐字节一致”这一条，逼纯函数测试与反例自己发现问题。
用洞察服务的 venv 运行（make gate1-mutation）；缺 pytest 时退出码 3（Makefile 决定非严格跳过、严格失败）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSIGHT = HERE.parents[1] / "services" / "insight"
RP, CF, CO, EV = ("gate1_evidence/reports.py", "gate1_evidence/config.py", "gate1_evidence/cohort.py", "gate1_evidence/evidence.py")

MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # G1-2 改善
    "改善不要求严格大于": [(RP, "ok = mean_delta > cfg.min_mean_delta and", "ok = mean_delta >= cfg.min_mean_delta and")],
    "改善占比门槛改成严格": [(RP, "share >= cfg.min_share_improved", "share > cfg.min_share_improved")],
    "不看改善占比": [(RP, "ok = mean_delta > cfg.min_mean_delta and share >= cfg.min_share_improved", "ok = mean_delta > cfg.min_mean_delta")],
    "配对不要求两轮都够题": [(RP, "if len(f) >= cfg.min_items_per_round and len(w) >= cfg.min_items_per_round:", "if f and w:")],
    "复测样本不足仍出结论": [(RP, "if len(pairs) < cfg.min_paired_students:", "if not pairs:")],
    "needs_review 计入复测": [(RP, 'if a.retest_round in ("first", "week8") and a.fact.verdict in GRADED:',
                              'if a.retest_round in ("first", "week8"):')],
    # G1-2 续用
    "续用窗口终点闭区间": [(RP, "continued += any(start <= t < end for t in times)", "continued += any(start <= t <= end for t in times)")],
    "续用窗口起点开区间": [(RP, "continued += any(start <= t < end for t in times)", "continued += any(start < t < end for t in times)")],
    "观察窗超出队列也计入（不删失）": [(RP, "if end > horizon_end:", "if False:")],
    "续用样本不足仍出结论": [(RP, "if evaluable < cfg.min_enrolled_students:", "if evaluable == 0:")],
    "续用锚点改成首次会话": [(RP, "start = st.enrolled_at + timedelta(days=cfg.continuation_after_days)",
                          "start = min(by.get(st.student_id, [st.enrolled_at])) + timedelta(days=cfg.continuation_after_days)")],
    "续用分母不含从未使用者": [(RP, "        evaluable += 1\n        times = by.get(st.student_id, [])\n",
                           "        times = by.get(st.student_id, [])\n        if not times:\n            continue\n        evaluable += 1\n")],
    "续用率门槛改成严格":[(RP, "rate >= cfg.continuation_min_rate", "rate > cfg.continuation_min_rate")],
    "合并结论时忽略样本不足": [(RP, "    if INSUFFICIENT in results:\n        return INSUFFICIENT\n", "")],
    "G1-2 只看复测不看续用": [(RP, '_combine(imp["result"], cont["result"])', '_combine(imp["result"])')],
    # G1-3 考试对账
    "覆盖率下界开区间": [(RP, "within = cfg.coverage_low <= cov <= cfg.coverage_high", "within = cfg.coverage_low < cov <= cfg.coverage_high")],
    "覆盖率上界开区间": [(RP, "within = cfg.coverage_low <= cov <= cfg.coverage_high", "within = cfg.coverage_low <= cov < cfg.coverage_high")],
    "不看覆盖率上界": [(RP, "within = cfg.coverage_low <= cov <= cfg.coverage_high", "within = cfg.coverage_low <= cov")],
    "区间内外不用 reconcile_plan（自写开区间）": [(RP, "pos[reconcile_plan(e.actual, e.low, e.high, e.basis, z).position] += 1",
                                         'pos["inside" if e.low < e.actual < e.high else "below" if e.actual <= e.low else "above"] += 1')],
    "MAE 不按满分归一": [(RP, "mae = _mean([abs(e.actual - m) / e.full_score for e, m in zip(xs, med)])",
                      "mae = _mean([abs(e.actual - m) / 100 for e, m in zip(xs, med)])")],
    "MAE 用区间中点代替中值": [(RP, "med = [predicted_median(e.basis, e.full_score) for e in xs]", "med = [(e.low + e.high) / 2 for e in xs]")],
    "中值取记录字段而不是预测依据": [(RP, "med = [predicted_median(e.basis, e.full_score) for e in xs]", "med = [e.median for e in xs]")],
    "中值保留两位": [("gate1_evidence/basis.py", "return round(float(mean) * full_score, 1)", "return round(float(mean) * full_score, 2)")],
    "中值不校验预测依据": [("gate1_evidence/basis.py", "        raise BasisError(", "        return 0.0\n        raise BasisError(")],
    "偏差符号反了": [(RP, "bias = _mean([(e.actual - m) / e.full_score", "bias = _mean([(m - e.actual) / e.full_score")],
    "每轮记录数门槛失效": [(RP, "if len(xs) < cfg.min_records_per_round or students", "if students")],
    "每轮不按学生计": [(RP, " or students < cfg.min_students_per_round:", ":")],
    "轮数不足仍出结论": [(RP, "if len(raw) < cfg.min_rounds:", "if not raw:")],
    "误差下降比较第二轮而不是最后一轮": [(RP, "last = raw[-1]", "last = raw[1]")],
    "误差持平算下降": [(RP, "decreased = last[2] < first[2] and", "decreased = last[2] <= first[2] and")],
    "忽略相对下降门槛": [(RP, " and (first[2] - last[2]) >= cfg.min_mae_relative_drop * first[2]", "")],
    "只看误差不看覆盖率": [(RP, '"result": MEETS if all_within and decreased else BELOW}', '"result": MEETS if decreased else BELOW}')],
    "只看覆盖率不看误差": [(RP, '"result": MEETS if all_within and decreased else BELOW}', '"result": MEETS if all_within else BELOW}')],
    # G1-4 效度
    "不看相关方向": [(RP, "ok = observed == expected and abs(r) >= cfg.min_abs_r", "ok = abs(r) >= cfg.min_abs_r")],
    "|r| 门槛改成严格": [(RP, "ok = observed == expected and abs(r) >= cfg.min_abs_r", "ok = observed == expected and abs(r) > cfg.min_abs_r")],
    "不看 |r|": [(RP, "ok = observed == expected and abs(r) >= cfg.min_abs_r", "ok = observed == expected")],
    "学生数门槛失效": [(RP, "if len(xs) < cfg.min_students:", "if len(xs) < 2:")],
    "效度不按学生聚合（学生 × 学科各算一对）": [(RP, 'acc.setdefault(r["metric"], {}).setdefault(r["student_id"], [])',
                                       'acc.setdefault(r["metric"], {}).setdefault((r["student_id"], r["subject"]), [])')],
    "聚合时只取第一科": [(RP, "[(_mean([x for x, _ in v]), _mean([y for _, y in v]))", "[(v[0][0], v[0][1])")],
    "零方差当作 0 相关": [(RP, "    if sxx <= 1e-12 or syy <= 1e-12:\n        return None", "    if sxx <= 1e-12 or syy <= 1e-12:\n        return 0.0")],
    "相关公式不开方": [(RP, "/ math.sqrt(sxx * syy)", "/ (sxx * syy)")],
    "效度样本不足当作未达到": [(RP, "INSUFFICIENT if passing + pending >= cfg.min_metrics_passing else BELOW", "BELOW")],
    "一项通过就算": [(RP, "result = MEETS if passing >= cfg.min_metrics_passing", "result = MEETS if passing >= 1")],
    "指标用到测量时点之后的作答（泄漏结局）": [(RP, "values = _metric_values(metric_inputs(cohort, st.student_id, now), policy, now)",
                                       "values = _metric_values(metric_inputs(cohort, st.student_id, cohort.horizon_end), policy, "
                                       "cohort.horizon_end)")],
    # G1-4 · SIE-05：全部 8 个静默指标与原料
    "效度漏掉学习持续性": [(RP, "{calc.__name__: calc for calc in M.CALCULATORS}",
                          "{calc.__name__: calc for calc in M.CALCULATORS if calc.__name__ != \"persistence\"}")],
    "效度漏掉自评校准": [(RP, "{calc.__name__: calc for calc in M.CALCULATORS}",
                        "{calc.__name__: calc for calc in M.CALCULATORS if calc.__name__ != \"self_assessment_calibration\"}")],
    "计划完成率换成自写口径（不按任务去重）": [(RP, "{calc.__name__: calc for calc in M.CALCULATORS}",
                                       '{**{calc.__name__: calc for calc in M.CALCULATORS}, "plan_completion_rate": lambda i, p, n: '
                                       '[M.MetricResult("plan_completion_rate", None, None, '
                                       '{"v": sum(x.kind == "plan_done" for x in i.plans) / len(i.plans)}, len(i.plans), 1, ()) ] '
                                       'if i.plans and min(x.occurred_at for x in i.plans) <= n - timedelta(days=14) else []}')],
    # 审查阻断：Gate 1 只数 counts_toward_gate = true 的指标
    "观察类指标被计入 Gate 1": [(RP, '    counted = [i for i in items if i["counts_toward_gate"]]', "    counted = items")],
    "观察类样本不足也算待定": [(RP, 'pending = sum(1 for i in counted if i["result"] == INSUFFICIENT)',
                            'pending = sum(1 for i in items if i["result"] == INSUFFICIENT)')],
    "报表不标是否计入": [(RP, '"counts_toward_gate": key in cfg.counted}', '"counts_toward_gate": True}')],
    "counts_toward_gate 不必是布尔": [(CF, '_need(isinstance(m["counts_toward_gate"], bool)', "_need(True")],
    "counts_toward_gate 可缺省（缺省计入）": [
        (CF, '_keys(m, {"outcome", "direction", "counts_toward_gate"}, f"validity_g14.metrics.{k}")',
             '_keys(m, {"outcome", "direction"} | ({"counts_toward_gate"} & set(m)), f"validity_g14.metrics.{k}")'),
        (CF, '_need(isinstance(m["counts_toward_gate"], bool)', '_need(isinstance(m.get("counts_toward_gate", True), bool)'),
        (CF, '        if m["counts_toward_gate"]:', '        if m.get("counts_toward_gate", True):')],
    "摘要观察用也写“计入”": [(EV, "{'计入' if m['counts_toward_gate'] else NOT_COUNTED}", "计入")],
    "摘要不标替代口径": [(EV, 'f"结局为替代口径（字典原文：{original}）" if substitute', 'f"结局与字典原文一致（字典原文：{original}）" if substitute')],
    "摘要不写生成器预设的相关": [(EV, '    lines += ["", *GENERATOR_CAVEATS, "",', '    lines += ["", "",')],
    "错因不按测量时点截断": [(RP, "errors = tuple(e.fact for e in cohort.errors if e.student_id == student_id and e.created_at <= now)",
                          "errors = tuple(e.fact for e in cohort.errors if e.student_id == student_id)")],
    "会话事件不进活跃": [(RP, "    activity = tuple(M.ActivityFact(s.fact_id, s.occurred_at) for s in cohort.sessions\n"
                              "                     if s.student_id == student_id and s.occurred_at <= now)\n"
                              "    activity += tuple(", "    activity = tuple(")],
    "探针不分学生": [(RP, "probes = tuple(p.fact for p in cohort.probes if p.student_id == student_id and p.fact.asked_at <= now)",
                     "probes = tuple(p.fact for p in cohort.probes if p.fact.asked_at <= now)")],
    "计划事件不分学生": [(RP, "plans = tuple(p.fact for p in cohort.plans if p.student_id == student_id and p.fact.occurred_at <= now)",
                       "plans = tuple(p.fact for p in cohort.plans if p.fact.occurred_at <= now)")],
    "跨科指标结局只取一科": [(RP, "    return _mean(ys) if ys else None", "    return ys[0] if ys else None")],
    "跨科指标结局当作单科查（不成对）": [(RP, "    if subject != CROSS:\n", "    if True:\n")],
    "新增指标的结局可以随便写": [(CF, "_need(m[\"outcome\"] in OUTCOMES and m[\"direction\"] in DIRECTIONS,",
                              "_need(m[\"direction\"] in DIRECTIONS,")],
    "配置可以写未实现的指标": [(CF, "set(v[\"metrics\"]) <= set(VALIDITY_METRICS)", "True")],
    "合成队列没有补做的计划任务": [(CO, "if not done and r.random() < REDO_RATE:", "if False:")],
    "合成队列没有审题类错因": [(CO, "cat = READING if xr.random() < habits.reading else xr.choice(OTHER_ERRORS)",
                            "cat = xr.choice(OTHER_ERRORS)")],
    "合成队列探针都说“能”": [(CO, "return \"yes\" if x >= 0.65 else \"no\" if x <= 0.35 else \"unsure\"", "return \"yes\"")],
    "合成队列没有只浏览的会话": [(CO, "or br.random() >= BROWSE_RATE:", "or True:")],
    "队列摘要不含探针": [(CO, "        \"probes\": [[p.student_id,", "        \"probes_\": [] and [[p.student_id,")],
    "结局混入首轮复测": [(RP, 'if a.retest_round == "week8" and a.fact.verdict in GRADED:', "if a.retest_round is not None and a.fact.verdict in GRADED:")],
    "考试结局取首轮": [(RP, "last = max(e.round_index for e in cohort.exams)", "last = min(e.round_index for e in cohort.exams)")],
    # 附加：分组误差差异
    "不检查分组键": [(RP, "        check_group_key(key)\n", "")],
    "分组记录数门槛失效": [(RP, "or min(sizes.values()) < cfg.min_records_per_group", "or False")],
    "分组不按学生计": [(RP, "\n                or min(heads.values()) < cfg.min_students_per_group)", ")")],
    "只有一组也出结论": [(RP, "if (len(errs) < 2 or", "if (not errs or")],
    "分组误差用记录里的中值字段": [(RP, "errs.setdefault(g, []).append((e.actual - predicted_median(e.basis, e.full_score)) / e.full_score)",
                               "errs.setdefault(g, []).append((e.actual - e.median + 1.0) / e.full_score)")],
    "不看 MAE 差距": [(RP, "ok = mae_gap <= cfg.max_mae_gap and bias_gap <= cfg.max_bias_gap", "ok = bias_gap <= cfg.max_bias_gap")],
    "不看偏差差距": [(RP, "ok = mae_gap <= cfg.max_mae_gap and bias_gap <= cfg.max_bias_gap", "ok = mae_gap <= cfg.max_mae_gap")],
    "偏差取绝对值": [(RP, "bias = {g: _mean(v) for g, v in errs.items()}", "bias = {g: _mean([abs(x) for x in v]) for g, v in errs.items()}")],
    # 配置校验
    "分组键不限于合成键": [(CF, "key in SYNTHETIC_GROUP_KEYS and not FORBIDDEN_GROUP_KEY.search(key)", "not FORBIDDEN_GROUP_KEY.search(key)")],
    "覆盖率区间不必包住名义值": [(CF, '_need(lo < 0.8 < hi, "覆盖率区间必须包住名义值 0.8")', '_need(lo < hi, "覆盖率区间必须包住名义值 0.8")')],
    "未签字配置可自称已签字": [(CF, '_need(signed or doc[s]["signoff"] == UNSIGNED', "_need(True")],
    "已签字配置可保留“待签字”": [(CF, '_need(not signed or doc[s]["signoff"] != UNSIGNED', "_need(True")],
    "多余配置位放行":[(CF, 'extra = set(doc) - required - {"$comment"}', "extra = set()")],
    # 合成队列
    "8 周后复测间隔改成 7 周": [(CO, "WEEK8_RETEST_DAY = FIRST_RETEST_DAY + 56", "WEEK8_RETEST_DAY = FIRST_RETEST_DAY + 49")],
    "停用学生也有 8 周后复测": [(CO, "            if not lat.active(day):\n                continue\n            t0 = BASE + timedelta(days=day, hours=20)",
                              "            t0 = BASE + timedelta(days=day, hours=20)")],
    # 证据包
    "达到未签字阈值就写“满足”": [(EV, '"gate_judgement": NEEDS_HUMAN, "reason": SYNTHETIC_REASON',
                               '"gate_judgement": "满足" if reports[key]["result"] == MEETS else NEEDS_HUMAN, "reason": SYNTHETIC_REASON')],
    "不检查输出目录": [(EV, "    check_out_dir(out_dir, set(files))\n", "")],
    "输出目录里的同名目录与符号链接放行": [(EV, "if e.name not in names or e.is_symlink() or not e.is_file()", "if e.name not in names")],
    "输出目录本身是符号链接也放行": [(EV, "    if out_dir.is_symlink():\n        raise PackRefused", "    if False:\n        raise PackRefused")],
    "摘要去掉合成标注":[(EV, 'f"> **{SYNTHETIC_NOTICE}。** 全部学生', 'f"> **试点证据。** 全部学生')],
}


def _copy(dst: Path) -> Path:
    tool = dst / "gate1-evidence"
    shutil.copytree(HERE, tool, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return tool


def _run(tool: Path) -> int:
    env = {**os.environ, "GATE1_INSIGHT_DIR": str(INSIGHT), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tool)}
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--rootdir", str(tool),
           "-k", "not matches_committed_sample", str(tool / "tests")]
    return subprocess.run(cmd, cwd=INSIGHT, env=env, capture_output=True, text=True).returncode


def _mutate(name: str, edits: list[tuple[str, str, str]], base: Path) -> tuple[str, str]:
    work = Path(tempfile.mkdtemp(prefix="m.", dir=base))
    tool = _copy(work)
    for rel, old, new in edits:
        p = tool / rel
        src = p.read_text(encoding="utf-8")
        if src.count(old) != 1:
            return name, "anchor"
        p.write_text(src.replace(old, new), encoding="utf-8")
    rc = _run(tool)
    return name, "caught" if rc == 1 else "survived" if rc == 0 else f"error({rc})"


def main() -> int:
    try:
        import pytest  # noqa: F401
    except ImportError:
        print("gate1-mutation: 跳过（当前解释器没有 pytest；用洞察服务的 venv）")
        return 3
    with tempfile.TemporaryDirectory(prefix="gate1-mut.") as t:
        base = Path(t)
        control = _run(_copy(base / "control"))
        if control != 0:
            print(f"gate1-mutation: 对照组（不变异）没有通过（退出码 {control}）")
            return 1
        workers = min(8, os.cpu_count() or 2)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda kv: _mutate(kv[0], kv[1], base), MUTATIONS.items()))
    bad = [(n, s) for n, s in results if s != "caught"]
    for n, s in results:
        print(f"  {'发现' if s == 'caught' else s}：{n}")
    print(f"gate1-mutation: {len(results) - len(bad)}/{len(results)} 个变异被测试发现")
    if bad:
        print("gate1-mutation: 未被发现或锚点失效：" + "；".join(f"{n}（{s}）" for n, s in bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
