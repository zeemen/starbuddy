"""KGE-09 地图校准工具（specs/kge09-map-calibration）的变异测试：把统计公式、门槛、守卫、扰动、口径与合成队列的埋入结构逐条改坏，
断言 tests/test_calibrate.py 能发现。

做法同 tools/gate1-evidence/mutations.py：每个变异在临时目录里复制一份本工具、改一处源码再跑测试；只有“测试失败”（退出码 1）算发现。
先跑一次不变异的对照组，必须通过。跑变异时不选“与仓库样例逐字节一致”这一条，逼纯函数测试与反例自己发现问题。
仓库里其他服务的源码（门槛取值、快照白名单）经 KGE_CALIBRATE_REPO 指回本仓库，只读核对。
用洞察服务的 venv 运行（make kge-calibrate-mutation）；缺 pytest 时退出码 3（Makefile 决定非严格跳过、严格失败）。
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
ST, CF, IN, CA, RE, SY = ("kge_calibrate/stats.py", "kge_calibrate/config.py", "kge_calibrate/inputs.py",
                          "kge_calibrate/calibrate.py", "kge_calibrate/report.py", "kge_calibrate/synth.py")
ED_INSUFF = 'rec.update(conclusion=INSUFFICIENT, reason=f"四格（A 掌握与否 × B 掌握与否）有一格人数不足门槛 {cfg.min_cell}", disclosed=False)'

MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # 统计函数
    "Wilson 中心不加 z²/2n": [(ST, "center = (p + z2 / (2.0 * n)) / denom", "center = p / denom")],
    "Wilson 半宽少 z²/4n² 项": [(ST, "p * (1.0 - p) / n + z2 / (4.0 * n * n)", "p * (1.0 - p) / n")],
    "Wilson 下端点浮点残差": [(ST, "lo = 0.0 if x == 0 else max(0.0, center - half)", "lo = max(0.0, center - half)")],
    "Wilson 上端点浮点残差": [(ST, "hi = 1.0 if x == n else min(1.0, center + half)", "hi = min(1.0, center + half)")],
    "Newcombe 下界组合错": [(ST, "lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)", "lo = d - math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)")],
    "Newcombe 上界组合错": [(ST, "hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)", "hi = d + math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)")],
    "两比例检验不合并方差": [(ST, "var = pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2)",
                           "var = (x1 / n1) * (1 - x1 / n1) / n1 + (x2 / n2) * (1 - x2 / n2) / n2")],
    "两比例检验改单侧": [(ST, "    return math.erfc(abs(z) / math.sqrt(2.0))", "    return 0.5 * math.erfc(abs(z) / math.sqrt(2.0))")],
    "BH 不取累计最小": [(ST, "running = min(running, pvalues[i] * m / rank)", "running = pvalues[i] * m / rank")],
    "BH 换成 Bonferroni": [(ST, "running = min(running, pvalues[i] * m / rank)", "running = min(running, pvalues[i] * m)")],
    "BH 不乘 m": [(ST, "running = min(running, pvalues[i] * m / rank)", "running = min(running, pvalues[i] / rank)")],
    "phi 取绝对值": [(ST, "return (a * d - b * c) / math.sqrt(den)", "return abs(a * d - b * c) / math.sqrt(den)")],
    "phi 行列为 0 时返回 0": [(ST, "    if den == 0:\n        return None", "    if den == 0:\n        return 0.0")],
    "卡方 p 值自由度口径错": [(ST, "math.sqrt(n * phi_value * phi_value / 2.0)", "math.sqrt(n * phi_value * phi_value)")],
    "Woolf 标准误少一项": [(ST, "1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d", "1.0 / a + 1.0 / b + 1.0 / c")],
    "分位数改单侧": [(ST, "_STD.inv_cdf(0.5 + confidence / 2.0)", "_STD.inv_cdf(confidence)")],
    # 配置与掌握口径
    "最小格可以低于 20": [(CF, '_int(doc["min_cell"], "min_cell", MIN_CELL_FLOOR, 100_000)', '_int(doc["min_cell"], "min_cell", 1, 100_000)')],
    "多余配置位放行": [(CF, 'extra = set(doc) - required - {"$comment"}', "extra = set()")],
    "掌握口径可以不是 star_lit": [(CF, '_need(m["rule"] in MASTERY_RULES,', "_need(True,")],
    "星亮门槛多收 stable": [(CF, 'LIT = frozenset({"transfer", "retain"})', 'LIT = frozenset({"stable", "transfer", "retain"})')],
    "分层开关不必是布尔": [(CF, '_need(isinstance(h["condition_on_shared_prerequisites"], bool)', "_need(True")],
    "门槛取值与学习服务不一致": [(CF, '"stable", "transfer", "retain")', '"stable", "transfer", "retain", "lit")')],
    # 输入守卫
    "不拦个人信息列名": [(IN, "_need(not FORBIDDEN_COLUMN.search(col),", "_need(True,")],
    "个人信息列名漏掉中文": [(IN, r"|id_?card|identity|身份证|证件", r"|id_?card|identity")],
    "接受非合成数据": [(IN, "_need(synthetic, ", "_need(True, ")],
    "合成标记写死": [(IN, "return Mastery(synthetic, len(pseudonyms)", "return Mastery(False, len(pseudonyms)")],
    "不核对学科与地图版本": [(IN, '_need(meta["subject"] == mp.subject and meta["map_version"] == mp.map_version,', "_need(True,")],
    "伪名放宽": [(IN, 'PSEUDONYM = re.compile(r"^(syn|pil)-[a-z]{10,24}$")', 'PSEUDONYM = re.compile(r"^[a-z][a-z0-9-]{5,63}$")')],
    "合成样例收 pil- 伪名": [(IN, "_need(not synthetic or pid.startswith(SYNTHETIC_PREFIX),", "_need(True,")],
    "不查单元格个人信息": [(IN, "return any(r.search(t) for r in PERSONAL)", "return False")],
    "单元格不拒汉字": [(IN, ',\n            re.compile(r"[㐀-鿿]"))', ")")],
    "接受地图外的节点": [(IN, "_need(nid in mp.nodes,", "_need(True,")],
    "同一伪名同一节点可重复": [(IN, "_need((pid, nid) not in seen,", "_need(True,")],
    "门槛状态不校验": [(IN, '_need(gate == "" or gate in GATES,', "_need(True,")],
    "掌握概率不校验": [(IN, "_need(math.isfinite(p) and 0.0 <= p <= 1.0,", "_need(True,")],
    "unseen 当作未掌握": [(IN, 'if gate in ("unseen", ""):', 'if gate == "":')],
    "空门槛当作未掌握": [(IN, 'if gate in ("unseen", ""):', 'if gate == "unseen":')],
    "读文件不查普通文件": [(IN, "_need(stat.S_ISREG(st.st_mode),", "_need(True,")],
    "撤下的节点也计算": [(IN, 'n["level"] == "node" and n.get("status", "active") == "active" and', 'n["level"] == "node" and')],
    "撤下的边也计算": [(IN, 'if e["type"] != "prerequisite" or e.get("status", "active") != "active":', 'if e["type"] != "prerequisite":')],
    "缺省强度当作弱前置": [(IN, 'strength = e.get("strength", "strong")', 'strength = e.get("strength", "weak")')],
    "快照白名单放行": [(IN, '_need(not (set(raw) - set(spec)),', "_need(True,")],
    "快照顶层白名单放行": [(IN, '_need(not (set(doc) - set(_TOP)), "地图快照顶层有白名单以外的字段")', "pass")],
    "不查节点学科前缀": [(IN, ' and nid.split(".")[0] == prefix,', ",")],
    # 扰动与粗分档（安全 B1）
    "前置边不加扰动": [(CA, "    if do_perturb:\n        t = perturb(t)\n", "")],
    "节点对不加扰动": [(CA, "                if noisy:\n                    t = perturb(t)\n", "")],
    "表键与表内学生无关": [(CA, "return Table2(a, b, c, d, set_key(keys))", "return Table2(a, b, c, d, 0)")],
    "扰动幅度超过 NOISE_MAX": [(CA, "NOISE_TABLE = ((-2, 0.1), (-1, 0.3), (0, 0.7), (1, 0.9), (2, 1.0))",
                                "NOISE_TABLE = ((-3, 0.1), (-1, 0.3), (0, 0.7), (1, 0.9), (3, 1.0))")],
    "前置边门槛不留扰动余量": [(CA, "disclosable(t, cfg.min_cell, NOISE_MAX if do_perturb else 0)", "disclosable(t, cfg.min_cell)")],
    "节点对门槛不留扰动余量": [(CA, "margin = NOISE_MAX if noisy else 0", "margin = 0")],
    "观测率分母不加扰动": [(CA, 'total = len(members) + (noise(set_key(members), "obs") if do_perturb else 0)', "total = len(members)")],
    "人数不分档": [(CA, "return (n // 10) * 10", "return n")],
    "比率保留 4 位": [(CA, "v = round(float(x), 2)", "v = round(float(x), 4)")],
    "p 值不分档": [(CA, "disclosed=True, p_adj_band=p_band(adj[i]), **nums)", "disclosed=True, p_adj_band=adj[i], **nums)")],
    # 前置边检验
    "最小格比较改严格": [(CA, "return min(t.cells()) >= min_cell + margin", "return min(t.cells()) > min_cell + margin")],
    "最小格只看两格": [(CA, "return min(t.cells()) >= min_cell + margin", "return min(t.a, t.d) >= min_cell + margin")],
    "分组算错（A 未掌握组取都未掌握）": [(CA, "n2, x2 = t.c + t.d, t.c", "n2, x2 = t.c + t.d, t.d")],
    "成立不看 BH 校正": [(CA, "_strong_conclusion(d, lo, hi, hi_adj, adj[i], cfg)", '_strong_conclusion(d, lo, hi, hi_adj, r["_p"], cfg)')],
    "效应量判据不用按族校正的区间": [(CA, "_strong_conclusion(d, lo, hi, hi_adj, adj[i], cfg)", "_strong_conclusion(d, lo, hi, hi, adj[i], cfg)")],
    "成立不看效应量": [(CA, " and p_adj <= cfg.prereq_fdr_q and d >= cfg.min_effect:", " and p_adj <= cfg.prereq_fdr_q:")],
    "成立不看显著性": [(CA, " and p_adj <= cfg.prereq_fdr_q and d >= cfg.min_effect:", " and d >= cfg.min_effect:")],
    "不报反向": [(CA, "    if hi < 0.0:\n        return NOT_HOLDS", "    if False:\n        return NOT_HOLDS")],
    "区间低于效应量不算不成立": [(CA, "    if hi_adj < cfg.min_effect:\n        return NOT_HOLDS", "    if False:\n        return NOT_HOLDS")],
    "强弱前置同一族校正": [(CA, 'fam = [i for i, r in enumerate(raws) if r["strength"] == strength and r["_eligible"]]',
                         'fam = [i for i, r in enumerate(raws) if r["_eligible"]] if strength == "strong" else []')],
    "弱前置也出候选": [(CA, 'if r["strength"] == "strong":\n            hi_adj', 'if True:\n            hi_adj')],
    "样本不足也给数值": [(CA, ED_INSUFF, ED_INSUFF[:-1] + ", n_approx=0)")],
    # 隐藏依赖
    "已有边的对也扫描": [(CA, "                if frozenset((u, v)) in direct:\n                    excluded[\"direct_edge\"] += 1\n                    continue\n", "")],
    "传递可达的对也扫描": [(CA, "                    excluded[\"transitive\"] += 1\n                    continue\n", "                    excluded[\"transitive\"] += 1\n")],
    "只看单向传递": [(CA, "if v in closure.get(u, ()) or u in closure.get(v, ()):", "if v in closure.get(u, ()):")],
    "跨主题也扫描": [(CA, "by_topic.setdefault(topic, []).append(nid)", 'by_topic.setdefault("all", []).append(nid)')],
    "主题节点对数上限不生效": [(CA, "if total > cfg.max_pairs_per_topic:", "if False:")],
    "不按共同前置分层": [(CA, "if shared and cfg.condition_on_shared:", "if False:")],
    "共同前置取并集": [(CA, "common = anc.get(u, frozenset()) & anc.get(v, frozenset())", "common = anc.get(u, frozenset()) | anc.get(v, frozenset())")],
    "共同前置含不可计算节点": [(CA, "return sorted(a for a in common if a in mp.nodes and mastery.observed.get(a))", "return sorted(common)")],
    "共同前置含无观测节点": [(CA, "return sorted(a for a in common if a in mp.nodes and mastery.observed.get(a))",
                            "return sorted(a for a in common if a in mp.nodes)")],
    "分层只要求观测不要求掌握": [(CA, "sets = [{k for k, m in mastery.observed.get(n, {}).items() if m} for n in nodes]",
                                "sets = [set(mastery.observed.get(n, {})) for n in nodes]")],
    "候选不看 phi 阈值": [(CA, "ok = ph is not None and ph >= cfg.min_phi and p_adj <= cfg.hidden_fdr_q",
                          "ok = ph is not None and p_adj <= cfg.hidden_fdr_q")],
    "候选不看显著性": [(CA, "ok = ph is not None and ph >= cfg.min_phi and p_adj <= cfg.hidden_fdr_q", "ok = ph is not None and ph >= cfg.min_phi")],
    "隐藏依赖不做 BH": [(CA, "adjusted = dict(zip(eligible, S.bh_adjust(pvals)))", "adjusted = dict(zip(eligible, pvals))")],
    "候选只列一个方向": [(CA, 'rec["directions"] = [[r["u"], r["v"]], [r["v"], r["u"]]]', 'rec["directions"] = [[r["u"], r["v"]]]')],
    "未观测学生也进四格": [(CA, "    keys = set(mu) & set(mv)\n", "    keys = set(mu) | set(mv)\n")],
    # 报表与出口
    "出口不查伪名": [(RE, "    assert_no_pseudonyms(files, mastery.pseudonyms)\n", "")],
    "学生数不足也输出": [(RE, "students_ok = mastery.students >= cfg.min_cell", "students_ok = True")],
    "学生数不分档": [(RE, '"students_approx": n_approx(mastery.students) if students_ok', '"students_approx": mastery.students if students_ok')],
    "不检查输出目录": [(RE, "    check_out_dir(out_dir)\n", "")],
    "输出目录是符号链接也放行": [(RE, "    if out_dir.is_symlink():\n        raise OutputRefused", "    if False:\n        raise OutputRefused")],
    "非空目录也放行": [(RE, "    if any(out_dir.iterdir()):\n        raise OutputRefused", "    if False:\n        raise OutputRefused")],
    "文件权限放宽": [(RE, "FILE_MODE = 0o600", "FILE_MODE = 0o644")],
    "去掉合成标注": [(RE, "SYNTHETIC_NOTICE if mastery.synthetic else \"\"", '""')],
    "工作单不出不成立候选": [(RE, 'nh = [e for e in edges if e["conclusion"] == NOT_HOLDS]', "nh = []")],
    "工作单少“修改为弱前置”": [(RE, '"- [ ] 修改为弱前置", "- [ ] 驳回（保留强前置）"', '"- [ ] 驳回（保留强前置）"')],
    "工作单候选只列一向": [(RE, 'f"- [ ] 确认 {v} → {u}", ', "")],
    # 合成队列
    "合成队列没有隐藏依赖": [(SY, "NodeSpec(_nid(2, 5), (_nid(2, 4),), 0.0)", "NodeSpec(_nid(2, 5), (), 0.5)")],
    "合成队列的不成立前置其实成立": [(SY, "NodeSpec(_nid(1, 4), (), 0.5)", "NodeSpec(_nid(1, 4), (_nid(1, 3),), 0.0)")],
    "合成队列没有未观测": [(SY, "if rng.random() < UNSEEN_RATE:", "if False:")],
    "合成队列门槛与概率完全对齐": [(SY, "MISALIGN_RATE = 0.15", "MISALIGN_RATE = 0.0")],
    "合成伪名含数字": [(SY, '"syn-" + "".join(chr(97 + b % 26) for b in h[:10])', '"syn-" + h.hex()[:10]')],
    "合成队列不标 synthetic": [(SY, "synthetic=true subject=", "synthetic=false subject=")],
}


def _copy(dst: Path) -> Path:
    tool = dst / "kge-calibrate"
    shutil.copytree(HERE, tool, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "sample"))
    return tool


def _run(tool: Path) -> int:
    env = {**os.environ, "KGE_CALIBRATE_REPO": str(REPO), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tool)}
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
        print("kge-calibrate-mutation: 跳过（当前解释器没有 pytest；用洞察服务的 venv）")
        return 3
    with tempfile.TemporaryDirectory(prefix="kge-cal-mut.") as t:
        base = Path(t)
        control = _run(_copy(base / "control"))
        if control != 0:
            print(f"kge-calibrate-mutation: 对照组（不变异）没有通过（退出码 {control}）")
            return 1
        workers = min(8, os.cpu_count() or 2)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda kv: _mutate(kv[0], kv[1], base), MUTATIONS.items()))
    bad = [(n, s) for n, s in results if s != "caught"]
    for n, s in results:
        print(f"  {'发现' if s == 'caught' else s}：{n}")
    print(f"kge-calibrate-mutation: {len(results) - len(bad)}/{len(results)} 个变异被测试发现")
    if bad:
        print("kge-calibrate-mutation: 未被发现或锚点失效：" + "；".join(f"{n}（{s}）" for n, s in bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
