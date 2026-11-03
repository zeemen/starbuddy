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
TX, RS, PF, SP = ("kge_calibrate/taxonomy.py", "kge_calibrate/responses.py", "kge_calibrate/pitfall.py", "kge_calibrate/split.py")
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
    "出口不查伪名": [(RE, "    assert_no_pseudonyms(files, mastery.pseudonyms | (step2.pseudonyms() if step2 else frozenset()))\n", "")],
    "出口不查第二步伪名": [(RE, "mastery.pseudonyms | (step2.pseudonyms() if step2 else frozenset())", "mastery.pseudonyms")],
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
    "合成队列不标 synthetic": [(SY, 'head = f"# format={MASTERY_FORMAT} synthetic=true subject=',
                                'head = f"# format={MASTERY_FORMAT} synthetic=false subject=')],
    # ---------- 第二步（spec-step2.md） ----------
    # 错因分类表与地图里的易错点
    "分类表不收下辖标签": [(TX, 'tags = _TICK.findall(cols[6].split("下辖标签：", 1)[1].split("；", 1)[0]) if "下辖标签：" in cols[6] else []',
                           "tags = []")],
    "分类表大类不校验": [(TX, "target[0] not in CROSS_SUBJECT or ", "")],
    "分类表学科内重复放行": [(TX, "if not CODE.fullmatch(c) or c in own:", "if not CODE.fullmatch(c):")],
    "跨科大类多一类": [(TX, '"expression_convention", "strategy_and_time", "knowledge")', '"expression_convention", "strategy_and_time", "knowledge", "careless")')],
    "has_pitfall 不看终点是否易错点": [(IN, ' == "active" and e["to_id"] in pits:', ' == "active":')],
    "撤下的 has_pitfall 边也算": [(IN, 'if e["type"] == "has_pitfall" and e.get("status", "active") == "active" and', 'if e["type"] == "has_pitfall" and')],
    "细目级易错点不按 parent_id 关联": [(IN, "pit_links: set[tuple[str, str]] = {(parent, p) for p, parent in pit_parent.items()}",
                                       "pit_links: set[tuple[str, str]] = set()")],
    "撤下的易错点也算": [(IN, 'if n.get("kind") == "pitfall" and n.get("status", "active") == "active" and', 'if n.get("kind") == "pitfall" and')],
    # 第二步配置与输入守卫
    "题目人数门槛可以低于 20": [(CF, '"split.min_item_attempts", MIN_ITEM_ATTEMPTS_FLOOR, 100_000)', '"split.min_item_attempts", 1, 100_000)')],
    "第二步不拦个人信息列名": [(RS, "_need(not FORBIDDEN_COLUMN.search(col),", "_need(True,")],
    "第二步接受非合成数据": [(RS, '_need(meta["synthetic"] == "true",', "_need(True,")],
    "第二步不核对学科与地图版本": [(RS, '_need(meta["subject"] == mp.subject and meta["map_version"] == mp.map_version,', "_need(True,")],
    "第二步收 pil- 伪名": [(RS, '_need(rec["pseudonym"].startswith(SYNTHETIC_PREFIX),', "_need(True,")],
    "第二步伪名放宽": [(RS, '_need(PSEUDONYM.fullmatch(rec["pseudonym"]) is not None,', "_need(True,")],
    "第二步不查单元格个人信息": [(RS, "_need(not looks_personal(cell),", "_need(True,")],
    "第二步接受地图外的节点": [(RS, '_need(rec["node_id"] in mp.nodes,', "_need(True,")],
    "题目 ID 不校验": [(RS, '_need(ITEM_ID.fullmatch(r["item_id"]) is not None,', "_need(True,")],
    "correct 收任意值": [(RS, '_need(r["correct"] in ("0", "1"),', "_need(True,")],
    "作答可重复": [(RS, '_need(r["pseudonym"] not in items,', "_need(True,")],
    "错因编码不查分类表": [(RS, "_need(code in codes,", "_need(True,")],
    "跨科大类不核对分类表": [(RS, "_need(cat == codes[code],", "_need(True,")],
    "置信度不校验": [(RS, "_need(math.isfinite(conf) and 0.0 <= conf <= 1.0,", "_need(True,")],
    "低置信也进编码分布": [(RS, "if conf >= min_confidence:", "if True:")],
    "置信度门槛改严格": [(RS, "if conf >= min_confidence:", "if conf > min_confidence:")],
    "对照不查易错点 ID": [(RS, "_need(pid in mp.pitfall_ids,", "_need(True,")],
    "对照不查编码": [(RS, "all(isinstance(c, str) and c in codes for c in lst)", "all(isinstance(c, str) for c in lst)")],
    "对照不核对版本": [(RS, '_need(doc["subject"] == mp.subject and doc["map_version"] == mp.map_version,', "_need(True,")],
    # 易错候选
    "易错候选不加扰动": [(PF, 'cells = perturb_pitfall(t["cells"], t["key"], t["node"], t["code"]) if noisy else t["cells"]',
                           'cells = t["cells"]')],
    "易错“无”格只留一份余量": [(PF, "min(b, d) >= min_cell + 2 * m", "min(b, d) >= min_cell + m")],
    "易错“有”格不留余量": [(PF, "return min(a, c) >= min_cell + m and", "return min(a, c) >= min_cell and")],
    "易错候选最小格只看本节点": [(PF, "return min(a, c) >= min_cell + m and min(b, d) >= min_cell + 2 * m", "return min(a, b) >= min_cell + m")],
    "各编码表行人数扰动各自独立": [(PF, 'n1 = max(0, a + b + noise(key, f"pitfall/{node}/n1"))',
                                   'n1 = max(0, a + b + noise(key, f"pitfall/{node}/{code}/n1"))')],
    "基线含本节点有记录的学生": [(PF, "base = {p: high[w][p] for p, w in pick.items() if p not in hv}", "base = {p: high[w][p] for p, w in pick.items()}")],
    "基线取第一个节点": [(PF, "pick = {p: baseline_pick(p, ns) for p, ns in where.items()}", "pick = {p: ns[0] for p, ns in where.items()}")],
    "只有低置信也进易错四格": [(PF, "high = {n: {p: c for p, c in errors.episodes.get(n, {}).items() if c} for n in ids}",
                               "high = {n: dict(errors.episodes.get(n, {})) for n in ids}")],
    "易错候选不做 BH": [(PF, "for r, p_adj in zip(eligible, S.bh_adjust(pvals)):", "for r, p_adj in zip(eligible, pvals):")],
    "易错候选不看差值阈值": [(PF, " and d >= pc.min_lift:", ":")],
    "易错候选不看占比阈值": [(PF, " and share >= pc.min_share", "")],
    "易错候选不看显著性": [(PF, " and p_adj <= pc.fdr_q", "")],
    "已覆盖也报新增": [(PF, "        if covering:\n            return COVERED", "        if False:\n            return COVERED")],
    "对照缺失也报新增": [(PF, "        if unmapped:\n            return MAPPING_MISSING", "        if False:\n            return MAPPING_MISSING")],
    "覆盖只看关联不看编码": [(PF, "sorted(p for p in linked if code in codes.get(p, ()))", "sorted(p for p in linked if p in codes)")],
    "难归类不加扰动": [(PF, 'cells = perturb_cells(cells, set_key(ep), f"low/{v}/")', "cells = cells")],
    "难归类门槛不留扰动余量": [(PF, '"cells": cells, "ok": min(cells) >= cfg.min_cell + margin})\n    m = ',
                               '"cells": cells, "ok": min(cells) >= cfg.min_cell})\n    m = ')],
    "难归类不按族校正": [(PF, "z_adj = S.z_for(1.0 - (1.0 - cfg.confidence) / max(1, m))", "z_adj = z")],
    "难归类用点估计": [(PF, "lo_adj = S.wilson(low, low + rest, z_adj)[0]", "lo_adj = low / (low + rest)")],
    # 节点内部分化
    "最佳二分不限每群题数": [(SP, "for k in range(min_group, m - min_group + 1):", "for k in range(1, m):")],
    "R² 算法错": [(SP, "ssb = k * (ml - mean) ** 2 + (m - k) * (mh - mean) ** 2", "ssb = (ml - mean) ** 2 + (mh - mean) ** 2")],
    "自助法 p 值不加一": [(SP, "return (1 + sum(1 for x in null if x >= r2_obs - 1e-12)) / (draws + 1)",
                          "return sum(1 for x in null if x >= r2_obs - 1e-12) / draws")],
    "拆分不做 BH": [(SP, 'adjusted = dict(zip(tested, S.bh_adjust([raws[i]["p"] for i in tested])))',
                     'adjusted = dict(zip(tested, [raws[i]["p"] for i in tested]))')],
    "拆分不看 H 比值": [(SP, "elif ratio > sc.max_h_ratio:", "elif False:")],
    "H 比值不用群内参照": [(SP, "ratio = None if h is None or ref is None or ref <= 0.0 else h / ref",
                           "ratio = None if h is None or ref is None or ref <= 0.0 else h")],
    "Loevinger H 方向写死": [(SP, "obs, expected = (t.b, n * p1 * (1 - p2)) if p1 <= p2 else (t.c, n * p2 * (1 - p1))",
                             "obs, expected = (t.b, n * p1 * (1 - p2))")],
    "群内参照四格不足也用": [(SP, "    if min(t.cells()) < min_cell + (NOISE_MAX if noisy else 0):\n        return None\n", "")],
    "群内参照不按扰动": [(SP, "pass_rate), node, noisy, tag)", "pass_rate), node, False, tag)")],
    "拆分不看两群之差": [(SP, "if not (p_adj <= sc.fdr_q and gap >= sc.min_gap):", "if not (p_adj <= sc.fdr_q):")],
    "拆分不看显著性": [(SP, "if not (p_adj <= sc.fdr_q and gap >= sc.min_gap):", "if not (gap >= sc.min_gap):")],
    "题目人数门槛只留一份扰动余量": [(SP, "if n >= cfg.split.min_item_attempts + 2 * margin:", "if n >= cfg.split.min_item_attempts + margin:")],
    "精确比率只看答对一格": [(SP, '"exact": min(cells) >= cfg.min_cell + margin', '"exact": cells[0] >= cfg.min_cell + margin')],
    "精确比率不留扰动余量": [(SP, '"exact": min(cells) >= cfg.min_cell + margin', '"exact": min(cells) >= cfg.min_cell')],
    "分档时均值仍给 2 位": [(SP, 'rnd = r1 if r["coarse"] else r2', "rnd = r2")],
    "题数上限不生效": [(SP, '[:sc.max_items_per_node]', "")],
    "分档端点写错": [(SP, "    if rate >= 0.90:\n", "    if rate > 0.90:\n")],
    "题目正确率不加扰动": [(SP, 'cells = perturb_cells(cells, set_key(resp), f"item/{node}/{item}/")', "cells = cells")],
    "学生层面四格不加扰动": [(SP, 't, fail_low, fail_high = cross_table(lp, hp, r["node"], noisy)', 't, fail_low, fail_high = cross_table(lp, hp, r["node"], False)')],
    "学生层面门槛不留扰动余量": [(SP, "cross_ok = min(t.cells()) >= cfg.min_cell + margin", "cross_ok = min(t.cells()) >= cfg.min_cell")],
    "错因证据不加扰动": [(SP, 't = _table((a, len(r1) - a, c, len(r2_) - c), key, f"err/{node}/{k}/", noisy)',
                          't = _table((a, len(r1) - a, c, len(r2_) - c), key, f"err/{node}/{k}/", False)')],
    "错因证据不做 Bonferroni": [(SP, "p_bonf = min(1.0, S.chi2_p(ph, t.n) * tested)", "p_bonf = S.chi2_p(ph, t.n)")],
    "错因证据取第一个编码": [(SP, "if ph is not None and (best is None or abs(ph) > abs(best[0]) + 1e-12):", "if ph is not None and best is None:")],
    "错因证据含只有低置信的学生": [(SP, "r1 = [p for p in fail_low if ep.get(p)]\n    r2_ = [p for p in fail_high if ep.get(p)]",
                                    "r1 = [p for p in fail_low if p in ep]\n    r2_ = [p for p in fail_high if p in ep]")],
    "过关门槛用严格大于": [(SP, "return {pid: r / n >= pass_rate for pid, (r, n) in tot.items()}", "return {pid: r / n > pass_rate for pid, (r, n) in tot.items()}")],
    # 第二步报表与工作单
    "第二步人数不分档": [(RE, "return n_approx(n) if n >= cfg.min_cell else INSUFFICIENT", "return n if n >= cfg.min_cell else INSUFFICIENT")],
    "第二步人数不足也给": [(RE, "return n_approx(n) if n >= cfg.min_cell else INSUFFICIENT", "return n_approx(n)")],
    "工作单不出新增易错点": [(RE, 'cand = [t for t in pf["tests"] if t["conclusion"] == NEW_PITFALL]', "cand = []")],
    "工作单不出难归类": [(RE, 'sig = [t for t in lc["nodes"] if t["conclusion"] == UNCLASSIFIABLE]', "sig = []")],
    "工作单不出拆分候选": [(RE, 'cand = [t for t in sp["nodes"] if t["conclusion"] == SPLIT]', "cand = []")],
    # 第二步合成队列
    "合成队列没有未覆盖的高频错因": [(SY, 'PLANTED_CODE = {N_UNCOVERED: "shijian_wenti", ', "PLANTED_CODE = {")],
    "合成难度分档没有猜对": [(SY, "TIERS_GUESS, TIERS_SLIP, TIERS_DIFFICULTY = 0.3, 0.15, 1.1", "TIERS_GUESS, TIERS_SLIP, TIERS_DIFFICULTY = 0.0, 0.0, 1.1")],
    # 审查阻断与建议（安全 B3、契约 S3、契约 B2、建议 6）
    "待定敏感编码也进检验": [(TX, "return tuple(sorted(c for c in self.codes.get(subject, {}) if st.get(c) == CLEAR))",
                              "return tuple(sorted(self.codes.get(subject, {})))")],
    "待定编码行不丢弃": [(RS, "        if code not in usable:\n            continue", "        if False:\n            continue")],
    "状态列不校验": [(TX, '    raise InputRefused("错因分类表有一行的“状态”列不认识")', "    return CLEAR")],
    "敏感编码当待定": [(TX, '    if cell == "⚠️ 待定（敏感）":\n        return SENSITIVE', '    if cell == "⚠️ 待定（敏感）":\n        return DISPUTED')],
    "不认识的学科行静默跳过": [(TX, '            raise InputRefused("错因分类表有一行的学科不认识（不能静默跳过）")', "            continue")],
    "对照不查同一大类": [(RS, "_need(len({codes[c] for c in lst}) <= 1,", "_need(True,")],
    "不拒 UUID 题目": [(RS, '_need(UUID_LIKE.fullmatch(r["item_id"]) is None,', "_need(True,")],
    "认知层次不校验": [(RS, '_need(lv == "" or lv in COGNITIVE_LEVELS,', "_need(True,")],
    "认知层次前后不一致放行": [(RS, '_need(levels.get(r["item_id"], lv) == lv,', "_need(True,")],
    "两表伪名不相交不拒": [(RS, "_need(bool(self.attempts.pseudonyms & self.errors.pseudonyms),", "_need(True,")],
    "自助法次数上限放宽": [(CF, '"split.bootstrap_draws", 200, 10_000)', '"split.bootstrap_draws", 200, 100_000)')],
    "题数上限可小于两群下限": [(CF, '_need(split.max_items_per_node >= 2 * split.min_items_per_group,', "_need(True,")],
    "第一步扫描无数据主题": [(CA, "if not any(mastery.observed.get(n) for n in ids):", "if False:")],
    "合成队列没有难归类聚集": [(SY, "LOW_ONLY_HOT = 0.45", "LOW_ONLY_HOT = 0.1")],
    "合成拆分节点只考一种能力": [(SY, "p = (0.9 if skills[g] else 0.1) + jitter[it]", "p = (0.9 if skills[0] else 0.1) + jitter[it]")],
    "合成错因不随失败的一群": [(SY, "codes = {SPLIT_CODES[g] for g in sorted(failed_groups) if rng.random() < 0.75}", "codes = set()")],
    "合成作答与错因不标 synthetic": [(SY, 'meta = f"synthetic=true subject=', 'meta = f"synthetic=false subject=')],
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
