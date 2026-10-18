"""KGE-10 IRT 试点评估工具（specs/kge10-irt-pilot）的变异测试：把求积、似然、梯度、EM、指标、门槛、扰动、取整、守卫与合成队列逐条改坏，
断言 tests/test_irt.py 能发现。

做法同 tools/kge-calibrate/mutations.py：每个变异在临时目录里复制一份本工具、改一处源码再跑测试；只有“测试失败”（退出码 1）算发现。
先跑一次不变异的对照组，必须通过。跑变异时不选“与仓库样例逐字节一致”这一条，逼纯函数测试与反例自己发现问题。
复用的 tools/kge-calibrate 与学习服务的配置、bkt.py 经 KGE_IRT_REPO 指回本仓库，只读。
用洞察服务的 venv 运行（make kge-irt-mutation）；缺 pytest 时退出码 3（Makefile 决定非严格跳过、严格失败）。
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
REPO = HERE.parents[1]
QD, IR, MT, AN, RE, IN, CF, SY = ("kge_irt/quad.py", "kge_irt/irt.py", "kge_irt/metrics.py", "kge_irt/analysis.py", "kge_irt/report.py",
                                  "kge_irt/inputs.py", "kge_irt/config.py", "kge_irt/synth.py")

MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # 求积
    "求积权重不除 √π": [(QD, "tuple(v / s for v in w)", "tuple(v for v in w)")],
    "求积节点不乘 √2": [(QD, "tuple(math.sqrt(2.0) * v for v in x)", "tuple(v for v in x)")],
    "Hermite 递推系数错": [(QD, "p1 = z * math.sqrt(2.0 / j) * p2 - math.sqrt((j - 1) / j) * p3", "p1 = z * math.sqrt(2.0 / j) * p2 - math.sqrt(j / (j + 1)) * p3")],
    "求积权重公式错": [(QD, "w[i] = w[n - 1 - i] = 2.0 / (pp * pp)", "w[i] = w[n - 1 - i] = 1.0 / (pp * pp)")],
    # 似然、梯度与 M 步
    "E 步不加对数权重": [(IR, "    ll = logw\n", "    ll = [0.0] * Q\n")],
    "E 步答错用答对的概率": [(IR, "v = lp[j] if x else lq[j]", "v = lp[j]")],
    "边际似然少 logsumexp 的最大值": [(IR, "total += wt * (m + math.log(s))", "total += wt * math.log(s)")],
    "期望计数不乘人数": [(IR, "wp = [wt * v for v in post]", "wp = list(post)")],
    "答对计数含答错": [(IR, "            if x:\n                R[j]", "            if True:\n                R[j]")],
    "梯度对 α 少乘 z": [(IR, "dz[pos[k]] = dz.get(pos[k], 0.0) + c * z", "dz[pos[k]] = dz.get(pos[k], 0.0) + c")],
    "梯度对 b 不乘 a": [(IR, "dz[pos[k]] = dz.get(pos[k], 0.0) - a * c", "dz[pos[k]] = dz.get(pos[k], 0.0) - c")],
    "梯度少先验项": [(IR, "        g[i] -= (phi[k] - st.prior_mean[k]) / sd2\n", "")],
    "信息矩阵少先验项": [(IR, "        info[i][i] += 1.0 / sd2\n", "")],
    "目标函数少先验": [(IR, "    total = log_prior(st, phi, pidx)\n", "    total = 0.0\n")],
    "先验方差少平方": [(IR, "(phi[k] - st.prior_mean[k]) ** 2 / (2.0 * st.prior_sd[k] ** 2)", "(phi[k] - st.prior_mean[k]) ** 2 / (2.0 * st.prior_sd[k])")],
    "M 步不减半直接接受": [(IR, "                if m_objective(st, comp, cand, es, quad) >= f0:", "                if True:")],
    "M 步不截断参数界": [(IR, "                cand = _clip(st, cand)\n", "")],
    "高斯消元不换主元": [(IR, "        M[c], M[piv] = M[piv], M[c]\n", "")],
    "先验难度结构不减 0.5": [(IR, "(2, 0.0 if p is None else p - 0.5)", "(2, 0.0 if p is None else p)")],
    "1PL 共同区分度不估计": [(IR, "((tuple(range(J + 1)), tuple(range(J))),)", "((tuple(range(1, J + 1)), tuple(range(J))),)")],
    "初值不取负 logit": [(IR, "bs.append(-math.log(p / (1.0 - p)))", "bs.append(math.log(p / (1.0 - p)))")],
    "EM 不到收敛就停": [(IR, "        if delta < fc.tolerance:", "        if True:")],
    "后验预测不用学生后验": [(AN, "pred[method] = M.predictive(posts[kind].get(pid, w), a, bb, theta)", "pred[method] = M.predictive(w, a, bb, theta)")],
    "分位数不插值": [(IR, "    return s[lo] + (h - lo) * (s[hi] - s[lo])", "    return s[lo]")],
    "自助法不重抽": [(IR, "counts[bd.pattern_of[rng.randrange(n)]] += 1.0", "rng.randrange(n)\n        counts[bd.pattern_of[_ % n]] += 1.0")],
    "只用先验难度不减 0.5": [(IR, "        v[-1] = prior - 0.5", "        v[-1] = prior")],
    "只用先验难度共用截距": [(IR, "        v[pos[node]] = 1.0", "        v[0] = 1.0")],
    "逻辑回归不更新": [(IR, "        beta = [b + v for b, v in zip(beta, d)]", "        beta = [b + 0.0 * v for b, v in zip(beta, d)]")],
    # 指标
    "对数损失不截断": [(MT, "    q = clip(p, eps)\n    return -math.log(q if y else 1.0 - q)", "    q = min(1.0 - 1e-15, max(1e-15, p))\n    return -math.log(q if y else 1.0 - q)")],
    "Brier 不平方": [(MT, "return (p - (1.0 if y else 0.0)) ** 2", "return abs(p - (1.0 if y else 0.0))")],
    "AUC 同分不记一半": [(MT, "total += gp * (below + 0.5 * gn)", "total += gp * below")],
    "AUC 不按人加权": [(MT, "out = [(p, y, 1.0 / k[pid]) for pid, y, p in rows]", "out = [(p, y, 1.0) for pid, y, p in rows]")],
    "按作答平均而不是每生一个点": [(MT, "return sum(v[which] for v in points.values()) / len(points)",
                                  "return sum(v[which] * v[2] for v in points.values()) / sum(v[2] for v in points.values())")],
    "平均秩不取平均": [(MT, "avg = (i + k) / 2.0 + 1.0", "avg = i + 1.0")],
    "Theil–Sen 用均值": [(MT, "    s = median(slopes)", "    s = sum(slopes) / len(slopes)")],
    # 分析口径
    "BKT 基线不含猜对": [(AN, "return p_mastery * (1.0 - ref.p_slip) + (1.0 - p_mastery) * ref.p_guess(item_type)", "return p_mastery * (1.0 - ref.p_slip)")],
    "BKT 基线缺省用 0.5": [(AN, "pm.get(pid, ref.p_init)", "pm.get(pid, 0.5)")],
    "BKT 基线不按认知层次取单元": [(AN, "base.p_mastery.get((b.node, it.cognitive_level), {})", "base.p_mastery.get((b.node, \"understand\"), {})")],
    "BKT 基线不按题型取 p_guess": [(AN, "ref, it.item_type)", "ref, None)")],
    "冷启动并入有拟合数据": [(AN, 'warm = pid in posts["2pl"]', "warm = True")],
    "Platt 不交叉拟合": [(AN, "for pid, y, pred in rows if fold_of(pid) != f]", "for pid, y, pred in rows]")],
    "非合成输入不查基线签字": [(AN, "    require_signed_baseline(cfg, resp.synthetic and iv.synthetic and base.synthetic)\n", "")],
    "块学生数不加扰动": [(AN, 'return max(0, len(bd.students) + noise(set_key(bd.students), f"irt/block/{bd.node}/n"))', "return len(bd.students)")],
    "拟合门槛不留余量": [(AN, "return block_students_noisy(bd) >= cfg.fit.min_block_students + NOISE_MAX", "return block_students_noisy(bd) >= cfg.fit.min_block_students")],
    # 防泄露：门槛、取整与扰动
    "参数不加扰动": [(RE, "k = round(v / step) + (noise(key, slot) if noisy else 0)", "k = round(v / step)")],
    "题目表键与成员无关": [(RE, "    key = set_key(b.data.respondents(j))\n", "    key = 0\n")],
    "题目两格不扰动": [(RE, 'cells = perturb_cells(cells, key, f"irt/item/{b.node}/{b.data.items[j]}/")', "cells = cells")],
    "题目人数门槛只留一份余量": [(RE, "enough = n >= cfg.min_respondents + 2 * margin", "enough = n >= cfg.min_respondents + margin")],
    "题目两格门槛不留余量": [(RE, "outcome = min(cells) >= cfg.min_outcome_cell + margin", "outcome = min(cells) >= cfg.min_outcome_cell")],
    "全对全错也给参数": [(RE, "disclosed = b.fitted and enough and outcome", "disclosed = b.fitted and enough")],
    "人数不取整": [(RE, 'rec["n_approx"] = n_approx(n) if enough else INSUFFICIENT', 'rec["n_approx"] = n if enough else INSUFFICIENT')],
    "区间端点不加扰动": [(RE, 'lo = coarse(M.percentile(xs, lo_q), step, key, f"{tag}2pl/{name}/lo", noisy)',
                          'lo = coarse(M.percentile(xs, lo_q), step, key, f"{tag}2pl/{name}/lo", False)')],
    "区间上端点不加扰动": [(RE, 'hi = coarse(M.percentile(xs, hi_q), step, key, f"{tag}2pl/{name}/hi", noisy)',
                            'hi = coarse(M.percentile(xs, hi_q), step, key, f"{tag}2pl/{name}/hi", False)')],
    "1PL 共同区分度不扰动": [(RE, 'cfg.discrimination_step, key, f"irt/{b.node}/1pl/a", noisy)', 'cfg.discrimination_step, key, f"irt/{b.node}/1pl/a", False)')],
    "1PL 与 2PL 难度共用扰动": [(RE, 'tag + "1pl/b", noisy)', 'tag + "2pl/b", noisy)')],
    "难度格点不随单人影响加粗": [(RE, "        if g >= 2.0 * influence - 1e-12:\n            return g", "        if True:\n            return g")],
    "未收敛告警不看人数门槛": [(RE, "        if enough:                                 # 未收敛告警", "        if True:                                 # 未收敛告警")],
    "评估组人数不扰动": [(RE, 'n = max(0, len(students) + (noise(key, slot + "n") if noisy else 0))', "n = len(students)")],
    "评估组表键与成员无关": [(RE, "    students = sorted({pid for pid, _, _ in rows})\n    key = set_key(students)\n",
                              "    students = sorted({pid for pid, _, _ in rows})\n    key = 0\n")],
    "评估组门槛不留余量": [(RE, "    if n < cfg.min_eval_students + margin:", "    if n < cfg.min_eval_students:")],
    "指标粒度固定 0.001": [(RE, "steps = {m: grid_for(spread[m], n) for m in METRICS}", "steps = {m: 0.001 for m in METRICS}")],
    "粒度向下取": [(RE, "        if g >= need - 1e-12:\n            return g", "        if g >= need / 4:\n            return g")],
    "对数损失不加扰动": [(RE, 'steps["log_loss"], key, slot + method + "/log_loss", noisy)', 'steps["log_loss"], key, slot + method + "/log_loss", False)')],
    "Brier 不加扰动": [(RE, 'steps["brier"], key, slot + method + "/brier", noisy)', 'steps["brier"], key, slot + method + "/brier", False)')],
    "AUC 不加扰动": [(RE, 'steps["auc"], key, slot + method + "/auc", noisy)', 'steps["auc"], key, slot + method + "/auc", False)')],
    "AUC 占比门槛用精确值": [(RE, '    if noisy:\n        w_pos += noise(key, slot + "auc/pos")\n        w_neg += noise(key, slot + "auc/neg")\n', "")],
    "AUC 占比门槛不留余量": [(RE, "auc_ok = min(w_pos, w_neg) >= AUC_MIN_SHARE * (w_pos + w_neg) + margin", "auc_ok = min(w_pos, w_neg) >= AUC_MIN_SHARE * (w_pos + w_neg)")],
    "AUC 不查正例占比": [(RE, "auc_ok = min(w_pos, w_neg) >= AUC_MIN_SHARE * (w_pos + w_neg) + margin", "auc_ok = True")],
    "判定不看配对区间": [(RE, "verdict = (BETTER if lo > 0 and m_pub", "verdict = (BETTER if m_pub")],
    "判定方向反了": [(RE, "d = [points[other][s][0] - points[irt][s][0] for s in students]", "d = [points[irt][s][0] - points[other][s][0] for s in students]")],
    "配对自助法不重抽": [(RE, "boots = [sum(d[i] for i in idx) / len(idx) for idx in res]", "boots = [mean for idx in res]")],
    "配对差不加扰动": [(RE, 'm_pub = coarse(mean, step, key, slot + "mean", noisy)', 'm_pub = coarse(mean, step, key, slot + "mean", False)')],
    "疑似标错不看残差绝对值": [(RE, "if abs(resid) > cfg.prior_mismatch_logits and outside:", "if resid > cfg.prior_mismatch_logits and outside:")],
    "疑似标错不看自助法区间": [(RE, "if abs(resid) > cfg.prior_mismatch_logits and outside:", "if abs(resid) > cfg.prior_mismatch_logits:")],
    "秩相关题数门槛不生效": [(RE, "        if len(items) < cfg.min_items_for_rank:\n            return INSUFFICIENT", "        if len(items) < 2:\n            return INSUFFICIENT")],
    "出口不查伪名": [(RE, "    assert_no_pseudonyms(files, frozenset(resp.pseudonyms) | frozenset(pseudonyms))\n", "")],
    "出口不查基线伪名": [(RE, "frozenset(resp.pseudonyms) | frozenset(pseudonyms)", "frozenset(resp.pseudonyms)")],
    "去掉合成标注": [(RE, '"notice": SYNTHETIC_NOTICE if (resp.synthetic or iv.synthetic) else "",', '"notice": "",')],
    "去掉非真相源标记": [(RE, '"not_a_source_of_truth": True,', '"not_a_source_of_truth": False,')],
    "工作单恢复勾选口径": [(RE, '"**候选仅供教研参考**', '"- [ ] 确认先验难度需复核修改\\n**候选仅供教研参考**')],
    "学生总数不取整": [(RE, '"students_approx": n_approx(n_all) if n_all', '"students_approx": n_all if n_all')],
    "区间不包含点估计": [(RE, 'two[f"{name}_ci"] = [min(lo, two[name]), max(hi, two[name])]', 'two[f"{name}_ci"] = [lo, hi]')],
    # 输入守卫
    "题目快照收非合成": [(IN, '_need(doc["synthetic"] is True,', "_need(True,")],
    "题目 ID 收 UUID": [(IN, "ITEM_ID.fullmatch(v) is not None and UUID_LIKE.fullmatch(v) is None", "ITEM_ID.fullmatch(v) is not None")],
    "快照编码不查个人信息": [(IN, " and not looks_personal(v)", "")],
    "认知层次不校验": [(IN, '_need(it["cognitive_level"] in COGNITIVE_LEVELS,', "_need(True,")],
    "基线认知层次不校验": [(IN, '_need(r["cognitive_level"] in COGNITIVE_LEVELS,', "_need(True,")],
    "合成标记写死 False": [(IN, "return Responses(meta_synthetic(text),", "return Responses(False,")],
    "元数据合成标记写死 True": [(IN, '    return toks.get("synthetic") == "true"', "    return True")],
    "节点学科前缀不查": [(IN, ' and nid.split(".")[0] == prefix,', ",")],
    "先验难度不查范围": [(IN, "math.isfinite(prior) and 0.0 <= prior <= 1.0", "math.isfinite(prior)")],
    "先验难度不查位数": [(IN, "\n                  and abs(round(prior, 3) - prior) < 1e-12,", ",")],
    "题目重复放行": [(IN, '_need(iid not in items, f"{where}：题目重复")', "pass")],
    "作答节点不核对": [(IN, '_need(r["node_id"] == item.node_id,', "_need(True,")],
    "作答题目不在快照也收": [(IN, "        _need(item is not None,", "        if item is None:\n            continue\n        _need(True,")],
    "correct 收任意值": [(IN, '_need(r["correct"] in ("0", "1"),', "_need(True,")],
    "seq 可重复": [(IN, "_need(seq not in seqs,", "_need(True,")],
    "segment 收任意值": [(IN, '_need(r["segment"] in SEGMENTS,', "_need(True,")],
    "同一伪名同一题可重复": [(IN, "_need(key not in seen,", "_need(True,")],
    "fit / eval 可交错": [(IN, "_need(last_fit < first_eval,", "_need(True,")],
    "拟合比例不查": [(IN, "_need(min_fit_fraction <= frac <= max_fit_fraction,", "_need(True,")],
    "基线概率不查": [(IN, '_need(math.isfinite(p) and 0.0 <= p <= 1.0, f"{what}第 {line_no} 行掌握概率不在 [0, 1] 内")', "pass")],
    "基线可重复": [(IN, '_need(r["pseudonym"] not in d,', "_need(True,")],
    "基线与作答不相交也收": [(IN, "_need(bool(pids & responses.pseudonyms),", "_need(True,")],
    # 配置
    "题目人数门槛可以低于 20": [(CF, '"disclosure.min_respondents", MIN_ITEM_ATTEMPTS_FLOOR, 100_000)', '"disclosure.min_respondents", 1, 100_000)')],
    "两格门槛可以低于 20": [(CF, '"disclosure.min_outcome_cell", MIN_CELL_FLOOR, 100_000)', '"disclosure.min_outcome_cell", 1, 100_000)')],
    "评估组门槛可以低于 20": [(CF, '"disclosure.min_eval_students", MIN_CELL_FLOOR, 100_000)', '"disclosure.min_eval_students", 1, 100_000)')],
    "族共享参数放行": [(CF, '_need(bl["share_family_parameters"] is False,', "_need(True,")],
    "slip + guess 不查": [(CF, "_need(all(ref.p_slip + v < 1", "_need(True or all(ref.p_slip + v < 1")],
    "p_guess 不按题型": [(CF, '        return self.p_guess_by_type.get(item_type or "", self.p_guess_default)', "        return self.p_guess_default")],
    "强度少来源权重": [(CF, "            return abs(self.w_correct) * self.level_w * self.source_w", "            return abs(self.w_correct) * self.level_w")],
    "非合成输入收未签字基线": [(CF, "_need(cfg.bkt.signed and not cfg.bkt.synthetic,", "_need(True,")],
    "签字只看 signed_by": [(CF, 'bool(doc.get("signed_by")) and bool(doc.get("signed_at"))', 'bool(doc.get("signed_by"))')],
    "基线配置路径可以出仓": [(CF, '\n          and ".." not in ba["source"], "baseline.source', ', "baseline.source')],
    # 合成队列
    "合成 BKT 同族不只计第一次": [(SY, "        if m[\"family_code\"] in fams:          # 同一题目族只计第一次（gates.py #9）\n            continue\n", "")],
    "合成 BKT 强度固定 1": [(SY, "bkt_step(p, y, ref.strength(y),", "bkt_step(p, y, 1.0,")],
    "合成队列没有标错的先验": [(SY, "            if iid in MISLABELED:\n                prior = 1.0 - prior\n", "")],
    "合成队列全是 1PL": [(SY, "                y = rng.random() < 1.0 / (1.0 + math.exp(-a * (theta - b)))",
                           "                y = rng.random() < 1.0 / (1.0 + math.exp(-(theta - b)))")],
    "合成 BKT 做对不加学习": [(SY, "        target = post + (1 - post) * learn", "        target = post")],
    "合成队列不标 synthetic": [(SY, 'meta = f"synthetic=true subject=', 'meta = f"synthetic=false subject=')],
}


def _copy(dst: Path) -> Path:
    tool = dst / "kge-irt"
    shutil.copytree(HERE, tool, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "sample"))
    return tool


def _run(tool: Path) -> int:
    env = {**os.environ, "KGE_IRT_REPO": str(REPO), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tool)}
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--rootdir", str(tool),
           "-k", "not matches_committed_sample", str(tool / "tests")]
    return subprocess.run(cmd, cwd=tool, env=env, capture_output=True, text=True).returncode


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
        print("kge-irt-mutation: 跳过（当前解释器没有 pytest；用洞察服务的 venv）")
        return 3
    only = set(sys.argv[1:])
    todo = {k: v for k, v in MUTATIONS.items() if not only or k in only}
    with tempfile.TemporaryDirectory(prefix="kge-irt-mut.") as t:
        base = Path(t)
        control = _run(_copy(base / "control"))
        if control != 0:
            print(f"kge-irt-mutation: 对照组（不变异）没有通过（退出码 {control}）")
            return 1
        workers = min(8, os.cpu_count() or 2)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda kv: _mutate(kv[0], kv[1], base), todo.items()))
    bad = [(n, s) for n, s in results if s != "caught"]
    for n, s in results:
        print(f"  {'发现' if s == 'caught' else s}：{n}")
    print(f"kge-irt-mutation: {len(results) - len(bad)}/{len(results)} 个变异被测试发现")
    if bad:
        print("kge-irt-mutation: 未被发现或锚点失效：" + "；".join(f"{n}（{s}）" for n, s in bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
