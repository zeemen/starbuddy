"""报表与工作单：calibration-report.json（每条边、每个节点对的粗分档人数、比率、区间、p 分档与结论）与 worklist.md（教研勾选）。

同一输入逐字节一致：键排序、数值保留 2 位、列表按节点 ID 排序、不写时间与路径。
不含任何精确计数（人数向下取整到 10，学生总数同样处理，不足 min_cell 写“样本不足”）。
出口检查：渲染后的文本里出现任何输入伪名即拒绝写出（防御性；正常路径上报表根本不接触伪名）。
写出：目标目录必须不存在或为空；每个文件以 O_CREAT | O_EXCL | O_NOFOLLOW、权限 0600 新建（不覆盖、不跟随符号链接）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from . import GENERATOR, SYNTHETIC_NOTICE
from .calibrate import (BELOW, CANDIDATE, HOLDS, INSUFFICIENT, NOISE_MAX, NOT_HOLDS, UNDECIDED, WEAK_ONLY, check_edges,
                        n_approx, scan_pairs)
from .config import Config
from .inputs import MapView, Mastery
from .pitfall import (COVERED, LOW_CONCLUSIONS, MAPPING_MISSING, NEW_PITFALL, PITFALL_CONCLUSIONS, UNCLASSIFIABLE, scan_pitfalls,
                      scan_unclassifiable)
from .responses import Step2
from .split import SPLIT, SPLIT_CONCLUSIONS, TIERS, scan_splits

SCHEMA_VERSION = "kge-calibrate-report/0.3"
NOT_PROVIDED_ERRORS = "未提供错因输入"
NOT_PROVIDED_ATTEMPTS = "未提供作答输入"
REPORT_NAME = "calibration-report.json"
WORKLIST_NAME = "worklist.md"
OUTPUT_NAMES = (REPORT_NAME, WORKLIST_NAME)
STATUS = "候选：待教研在审核台确认、修改或驳回（结构性变更两人确认）；本工具不改地图、不写库"
EDGE_CONCLUSIONS = (HOLDS, NOT_HOLDS, UNDECIDED, INSUFFICIENT, WEAK_ONLY)
PAIR_CONCLUSIONS = (CANDIDATE, BELOW, INSUFFICIENT)
FILE_MODE = 0o600
DIR_MODE = 0o700


class OutputRefused(ValueError):
    """输出不安全或目标目录不合适：拒绝写出"""


def _approx(n: int, cfg: Config) -> int | str:
    return n_approx(n) if n >= cfg.min_cell else INSUFFICIENT


def build_step2(mp: MapView, cfg: Config, step2: Step2 | None) -> dict[str, Any]:
    """第二步的报表段落：易错候选、错因难归类信号、节点内部分化（缺哪项输入，哪一段只写“未提供”）"""
    s = step2 or Step2()
    out: dict[str, Any] = {"cohort": {}}
    if s.errors is not None and s.taxonomy is not None:
        tests, insufficient = scan_pitfalls(mp, s.errors, s.taxonomy, s.pitfall_codes, cfg)
        low = scan_unclassifiable(mp, s.errors, cfg)
        out["cohort"]["errors_students_approx"] = _approx(s.errors.students, cfg)
        # 未达阈值的检验只列“节点 · 编码”（数值从略），其余结论列全部数值
        out["pitfall"] = {"provided": True, "pitfall_codes_provided": s.pitfall_codes is not None,
                          "tests": [t for t in tests if t["conclusion"] != BELOW],
                          "below_tests": [f"{t['node']} · {t['code']}" for t in tests if t["conclusion"] == BELOW],
                          "insufficient_tests": insufficient,
                          "excluded_codes": list(s.taxonomy.excluded_codes(mp.subject)),
                          "nodes_with_linked_pitfalls": {n: list(p) for n, p in mp.pitfalls_of.items()}}
        # 样本不足的节点只列 ID（节点清单与数据无关；没有任何数值）
        out["unclassifiable"] = {"provided": True, "nodes": [r for r in low if r["disclosed"]],
                                 "insufficient_nodes": [r["node"] for r in low if not r["disclosed"]]}
    else:
        out["pitfall"] = {"provided": False, "note": NOT_PROVIDED_ERRORS}
        out["unclassifiable"] = {"provided": False, "note": NOT_PROVIDED_ERRORS}
    if s.attempts is not None:
        out["cohort"]["attempts_students_approx"] = _approx(s.attempts.students, cfg)
        splits = scan_splits(mp, s.attempts, s.errors, s.taxonomy, cfg)
        keep = [r for r in splits if r["tested"] or r["items"]]    # 没检验、也没有可给出的题目：只列 ID
        out["node_split"] = {"provided": True, "nodes": keep,
                             "insufficient_nodes": [r["node"] for r in splits if not (r["tested"] or r["items"])]}
    else:
        out["node_split"] = {"provided": False, "note": NOT_PROVIDED_ATTEMPTS}
    return out


def build_report(mp: MapView, mastery: Mastery, cfg: Config, step2: Step2 | None = None) -> dict[str, Any]:
    edges = check_edges(mp, mastery, cfg)
    pairs, excluded = scan_pairs(mp, mastery, cfg)
    students_ok = mastery.students >= cfg.min_cell
    s2 = build_step2(mp, cfg, step2)
    pf, lc, sp = s2["pitfall"], s2["unclassifiable"], s2["node_split"]
    pc, sc = cfg.pitfall, cfg.split
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "notice": SYNTHETIC_NOTICE if mastery.synthetic else "",
        "status": STATUS,
        "config": cfg.provenance(),
        "map": {"subject": mp.subject, "map_version": mp.map_version, "snapshot_digest": mp.digest,
                "computable_nodes": len(mp.nodes),
                "prerequisite_edges": {s: sum(1 for e in mp.edges if e.strength == s) for s in ("strong", "weak")}},
        "cohort": {"students_approx": n_approx(mastery.students) if students_ok else INSUFFICIENT, **s2["cohort"]},
        "method": {
            "mastery": cfg.mastery.describe(),
            "min_cell": cfg.min_cell,
            "guard": (f"四格表各格先按表键加 −{NOISE_MAX}…+{NOISE_MAX} 的确定性扰动，扰动后任一格 < {cfg.min_cell + NOISE_MAX} "
                      f"即只写“样本不足”（保证真实每格 ≥ {cfg.min_cell}）；数值与结论都由扰动后的四格算出；不输出精确计数："
                      "人数向下取整到 10，比率与区间保留 2 位，p 值只给分档；输出不含学生伪名"),
            "prerequisite": {"interval": "Wilson（各组）、Newcombe 混合分数（差值）", "confidence": cfg.confidence,
                             "test": "两比例 z 检验（合并方差，双侧）", "multiple_comparison": "Benjamini–Hochberg（强前置一族、弱前置一族）",
                             "fdr_q": cfg.prereq_fdr_q, "min_effect": cfg.min_effect,
                             "min_effect_interval": "置信水平按族校正：1 − (1 − confidence) / 族内检验数",
                             "b_observed_rate": "组内在 B 上有观测的比例（保留 1 位）"},
            "hidden_dependency": {"scope": "同一主题内、无前置边（任一方向、强弱都算）、不传递可达的节点对",
                                  "association": "phi 系数；对数优势比（Woolf 区间）", "test": "卡方检验 df = 1（无连续性校正）",
                                  "multiple_comparison": "Benjamini–Hochberg", "fdr_q": cfg.hidden_fdr_q, "min_phi": cfg.min_phi,
                                  "condition_on_shared_prerequisites": cfg.condition_on_shared,
                                  "max_pairs_per_topic": cfg.max_pairs_per_topic},
            "pitfall": {"unit": "学生（一名学生在一个节点上的全部当前错因行合为一条记录；只有低置信行 = 难归类）",
                        "min_confidence": pc.min_confidence,
                        "baseline": "本主题其他节点有、本节点没有高置信错因记录的学生（每人按伪名哈希只取一个节点的记录）",
                        "test": "两比例 z 检验（合并方差，双侧）；差值 Newcombe 区间", "multiple_comparison": "Benjamini–Hochberg（全部可检验的节点 × 编码一族）",
                        "fdr_q": pc.fdr_q, "min_share": pc.min_share, "min_lift": pc.min_lift,
                        "coverage": "has_pitfall 边或细目级易错点 + 易错点编码对照（教研给出的公共层对照）",
                        "low_conf_share": pc.low_conf_share,
                        "unclassifiable_interval": "Wilson，置信水平按族校正：1 − (1 − confidence) / 可给出的节点数"},
            "node_split": {"item_rate_min_attempts": sc.min_item_attempts, "min_items_per_group": sc.min_items_per_group,
                           "item_guard": (f"答对、答错两格各扰动，扰动后作答人数 ≥ {sc.min_item_attempts + 2 * NOISE_MAX} 才参与；"
                                          f"两格都 ≥ {cfg.min_cell + NOISE_MAX} 才给 2 位小数正确率，否则只给分档（≥0.90 / ≤0.10 / 0.10–0.90）；"
                                          "参与检验的题目有分档时，两群均值、差值、R² 只给 1 位小数"),
                           "max_items_per_node": sc.max_items_per_node,
                           "test": "按正确率排序的最佳二分（组间平方和占比 R²）+ 参数自助法（正态零假设）",
                           "bootstrap_draws": sc.bootstrap_draws, "multiple_comparison": "Benjamini–Hochberg（全部可检验节点一族）",
                           "fdr_q": sc.fdr_q, "min_gap": sc.min_gap, "pass_rate": sc.pass_rate,
                           "student_level": ("Loevinger H = 1 − 实际“难的过关、易的不过关”人数 ÷ 独立时的期望（嵌套结构 → H 高）；"
                                             "H 比值 = 两群之间的 H ÷ 两群群内两半 H 的均值（消去猜对与失误的整体压低）"),
                           "max_h_ratio": sc.max_h_ratio,
                           "error_evidence": "只失败一群的学生 × 有无某编码，取 |phi| 最大的编码，Bonferroni",
                           "error_min_phi": sc.error_min_phi},
        },
        "prerequisite_tests": edges,
        "hidden_dependency": {"excluded_pairs": excluded, "pairs": pairs},
        "pitfall": pf,
        "unclassifiable": lc,
        "node_split": sp,
        "summary": {"prerequisite": {c: sum(1 for e in edges if e["conclusion"] == c) for c in EDGE_CONCLUSIONS},
                    "hidden_dependency": {c: sum(1 for p in pairs if p["conclusion"] == c) for c in PAIR_CONCLUSIONS},
                    "pitfall": ({c: sum(1 for t in pf["tests"] if t["conclusion"] == c) for c in PITFALL_CONCLUSIONS}
                                | {BELOW: len(pf["below_tests"]), INSUFFICIENT: pf["insufficient_tests"]})
                               if pf["provided"] else NOT_PROVIDED_ERRORS,
                    "unclassifiable": ({c: sum(1 for t in lc["nodes"] if t["conclusion"] == c)
                                        + (len(lc["insufficient_nodes"]) if c == INSUFFICIENT else 0) for c in LOW_CONCLUSIONS}
                                       if lc["provided"] else NOT_PROVIDED_ERRORS),
                    "node_split": ({c: sum(1 for t in sp["nodes"] if t["conclusion"] == c)
                                    + (len(sp["insufficient_nodes"]) if c == INSUFFICIENT else 0) for c in SPLIT_CONCLUSIONS}
                                   if sp["provided"] else NOT_PROVIDED_ATTEMPTS)},
    }


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _name(names: dict[str, str], nid: str) -> str:
    return f"{nid}（{names.get(nid, '')}）"


def _f(x: float) -> str:
    return f"{x:.2f}"


def _ci(ci: list[float]) -> str:
    return f"[{_f(ci[0])}, {_f(ci[1])}]"


def _edge_evidence(e: dict[str, Any], conf: float) -> str:
    a, b = e["a_mastered"], e["a_not_mastered"]
    level = f"{round(conf * 100)}%"
    return (f"证据包摘要：A 已掌握组约 {a['n_approx']} 人（组内 B 有观测的比例约 {a['b_observed_rate']}），"
            f"B 掌握率 {_f(a['b_rate'])}（{level} Wilson 区间 {_ci(a['b_rate_ci'])}）；"
            f"A 未掌握组约 {b['n_approx']} 人（约 {b['b_observed_rate']}），B 掌握率 {_f(b['b_rate'])}（{_ci(b['b_rate_ci'])}）；"
            f"差值 {_f(e['diff'])}（{level} Newcombe 区间 {_ci(e['diff_ci'])}），BH 校正后 p {e['p_adj_band']}。")


def _pair_evidence(p: dict[str, Any], conf: float) -> str:
    lor = p["log_odds_ratio"]
    ci = p["log_odds_ratio_ci"]
    level = f"{round(conf * 100)}%"
    cond = ""
    if p["shared_prerequisites"]:
        cond = ("只在共同前置全部已掌握的学生里计算；" if p["conditioned_on_shared"] else "两节点有共同前置（未分层，可能由共同前置引起）；")
    lor_txt = "无定义" if lor is None else f"{_f(lor)}（{level} Woolf 区间 {_ci(ci)}）"
    return (f"证据包摘要：{cond}约 {p['n_approx']} 人，四格每格都不少于门槛；前者掌握率 {_f(p['u_rate'])}，后者掌握率 {_f(p['v_rate'])}；"
            f"phi = {_f(p['phi'])}；对数优势比 {lor_txt}；BH 校正后 p {p['p_adj_band']}。")


def render_worklist(report: dict[str, Any], names: dict[str, str]) -> str:
    cfg, mp = report["config"], report["map"]
    conf = report["method"]["prerequisite"]["confidence"]
    edges, pairs = report["prerequisite_tests"], report["hidden_dependency"]["pairs"]
    lines = ["# 地图校准工作单（KGE-09 第一步、第二步）", ""]
    if report["notice"]:
        lines += [f"> **{report['notice']}。** 输入是固定种子的合成队列，结果只用来检验工具本身。", ""]
    lines += [f"> {report['status']}。",
              f"> 只含聚合数：四格表加了确定性扰动，任何格子不足门槛（{report['method']['min_cell']} 人）只写“样本不足”；"
              "人数向下取整到 10，比率保留 2 位，p 值只给分档；不含学生伪名。",
              f"> 配置 {cfg['config_version']}（指纹 {cfg['fingerprint']}，{cfg['signoff']}）；"
              f"地图 {mp['subject']} {mp['map_version']}（快照摘要 {mp['snapshot_digest'][:16]}）。",
              f"> 掌握口径：{report['method']['mastery']}。", ""]

    lines += ["## 一、前置不成立候选（强前置）", "", "每条只勾一项。", ""]
    nh = [e for e in edges if e["conclusion"] == NOT_HOLDS]
    if not nh:
        lines += ["（无）", ""]
    for i, e in enumerate(nh, 1):
        lines += [f"### 1.{i} {_name(names, e['from'])} → {_name(names, e['to'])}", "",
                  f"主题：{_name(names, e['from_topic'])}；依据：{e['reason']}。", "",
                  "- [ ] 确认（删除这条前置边）", "- [ ] 修改为弱前置", "- [ ] 驳回（保留强前置）", "",
                  _edge_evidence(e, conf), ""]

    lines += ["## 二、新增前置边候选（隐藏依赖，方向待定）", "", "每对只勾一项；方向由教研判断。", ""]
    cand = [p for p in pairs if p["conclusion"] == CANDIDATE]
    if not cand:
        lines += ["（无）", ""]
    for i, p in enumerate(cand, 1):
        u, v = p["u"], p["v"]
        lines += [f"### 2.{i} {_name(names, u)} — {_name(names, v)}", "", f"主题：{_name(names, p['topic'])}。", "",
                  f"- [ ] 确认 {u} → {v}", f"- [ ] 确认 {v} → {u}", "- [ ] 修改为弱前置（方向：____）", "- [ ] 驳回", "",
                  _pair_evidence(p, conf), ""]

    lines += _step2_sections(report, names, conf)

    lines += ["## 六、其余结论（供参考，不需勾选）", ""]
    for label, items in ((HOLDS, [e for e in edges if e["conclusion"] == HOLDS]),
                         (UNDECIDED, [e for e in edges if e["conclusion"] == UNDECIDED]),
                         (WEAK_ONLY, [e for e in edges if e["conclusion"] == WEAK_ONLY]),
                         (f"前置边{INSUFFICIENT}", [e for e in edges if e["conclusion"] == INSUFFICIENT])):
        body = "；".join(f"{e['from']} → {e['to']}" for e in items) or "无"
        lines.append(f"- {label}（{len(items)} 条）：{body}")
    ins = [p for p in pairs if p["conclusion"] == INSUFFICIENT]
    ex = report["hidden_dependency"]["excluded_pairs"]
    lines += [f"- 节点对{INSUFFICIENT}（{len(ins)} 对）：" + ("；".join(f"{p['u']} — {p['v']}" for p in ins) or "无"),
              f"- 节点对{BELOW}：{sum(1 for p in pairs if p['conclusion'] == BELOW)} 对；"
              f"已有前置边而不检验：{ex['direct_edge']} 对；传递可达而排除：{ex['transitive']} 对；"
              f"主题节点对数超上限而未扫描：{ex['topic_over_limit']} 对；主题内没有任何掌握数据而未扫描：{ex['topic_no_data']} 对"]
    lines += _step2_reference(report)
    lines += ["", "详细数值见同目录 calibration-report.json。", ""]
    return "\n".join(lines)


def _rate_txt(g: dict[str, Any], key: str) -> str:
    return f"约 {g['n_approx']} 人，占比 {_f(g[key])}（区间 {_ci(g[key + '_ci'])}）"


def _step2_sections(report: dict[str, Any], names: dict[str, str], conf: float) -> list[str]:
    level = f"{round(conf * 100)}%"
    pf, lc, sp = report["pitfall"], report["unclassifiable"], report["node_split"]
    lines = ["## 三、新增易错点候选（错因无法由已有易错点覆盖）", "",
             "每条只勾一项；易错点文本、识别特征由教研撰写（本工具不生成）。", ""]
    if not pf["provided"]:
        lines += [f"（{pf['note']}）", ""]
    else:
        cand = [t for t in pf["tests"] if t["conclusion"] == NEW_PITFALL]
        if not cand:
            lines += ["（无）", ""]
        for i, t in enumerate(cand, 1):
            lines += [f"### 3.{i} {_name(names, t['node'])} · 错因编码 `{t['code']}`（跨科大类 {t['cross_subject_category']}）", "",
                      f"主题：{_name(names, t['topic'])}；依据：{t['reason']}。", "",
                      "- [ ] 确认新增易错点（文本由教研撰写）", "- [ ] 归入已有易错点：____（并补易错点编码对照）", "- [ ] 驳回", "",
                      f"证据包摘要：本节点有高置信错因记录的学生{_rate_txt(t['node_group'], 'share')}；"
                      f"本主题基线{_rate_txt(t['baseline'], 'share')}；差值 {_f(t['diff'])}（{level} Newcombe 区间 {_ci(t['diff_ci'])}），"
                      f"BH 校正后 p {t['p_adj_band']}。", ""]

    lines += ["## 四、错因难归类信号（只列出供教研看，不提议新编码）", ""]
    if not lc["provided"]:
        lines += [f"（{lc['note']}）", ""]
    else:
        sig = [t for t in lc["nodes"] if t["conclusion"] == UNCLASSIFIABLE]
        if not sig:
            lines += ["（无）", ""]
        for i, t in enumerate(sig, 1):
            lines += [f"### 4.{i} {_name(names, t['node'])}", "", f"主题：{_name(names, t['topic'])}；依据：{t['reason']}。", "",
                      "- [ ] 已查看：需细化错因编码、判分 Rubric 或题目", "- [ ] 已查看：无需处理", "",
                      f"证据包摘要：有错因记录的学生约 {t['n_approx']} 人，只有低置信错因的占比 {_f(t['low_conf_rate'])}"
                      f"（{level} Wilson 区间 {_ci(t['low_conf_rate_ci'])}）。", ""]

    lines += ["## 五、拆分节点候选（节点内部分化）", "", "每条只勾一项；两群题目 ID 属公共层。", ""]
    if not sp["provided"]:
        lines += [f"（{sp['note']}）", ""]
    else:
        cand = [t for t in sp["nodes"] if t["conclusion"] == SPLIT]
        if not cand:
            lines += ["（无）", ""]
        for i, t in enumerate(cand, 1):
            cg, ev = t["cross_group"], t["error_evidence"]
            ev_txt = ev["conclusion"]
            if "code" in ev:
                ev_txt += f"（编码 `{ev['code']}`，phi = {_f(ev['phi'])}，校正后 p {ev['p_adj_band']}）"
            lines += [f"### 5.{i} {_name(names, t['node'])}", "", f"主题：{_name(names, t['topic'])}；依据：{t['reason']}。", "",
                      "- [ ] 确认拆分（按下列两群）", "- [ ] 修改分群：____", "- [ ] 驳回（保留一个节点）", "",
                      f"- 低正确率一群（平均 {_f(t['low_mean'])}）：{'、'.join(t['low_group'])}",
                      f"- 高正确率一群（平均 {_f(t['high_mean'])}）：{'、'.join(t['high_group'])}", "",
                      f"证据包摘要：两群之差 {_f(t['gap'])}，R² = {_f(t['r2'])}，BH 校正后 p {t['p_adj_band']}；"
                      f"两群都作答过的学生约 {cg['n_approx']} 人，两群之间 Loevinger H = {_f(cg['loevinger_h'])}，"
                      f"群内两半 H = {_f(cg['within_h'][0])} / {_f(cg['within_h'][1])}，H 比值 = {_f(cg['h_ratio'])}"
                      f"（phi = {_f(cg['phi'])}）；错因加强证据：{ev_txt}。"
                      f"各题正确率见 calibration-report.json（作答人数不足 {report['method']['node_split']['item_rate_min_attempts']} 的题不列出）。", ""]
    return lines


def _step2_reference(report: dict[str, Any]) -> list[str]:
    pf, lc, sp = report["pitfall"], report["unclassifiable"], report["node_split"]
    out = []
    if pf["provided"]:
        for label in (COVERED, MAPPING_MISSING):
            items = [t for t in pf["tests"] if t["conclusion"] == label]
            out.append(f"- {label}（{len(items)} 条）：" + ("；".join(f"{t['node']} · {t['code']}" for t in items) or "无"))
        out.append("- 错因分类表里待定或敏感、不参与检验的编码：" + ("、".join(f"`{c}`" for c in pf["excluded_codes"]) or "无"))
        out.append(f"- 节点 × 编码{BELOW}：{len(pf['below_tests'])} 条；"
                   f"四格不足门槛而未检验：{pf['insufficient_tests']} 条")
    if lc["provided"]:
        out.append(f"- 难归类{INSUFFICIENT}：{len(lc['insufficient_nodes'])} 个节点")
    if sp["provided"]:
        for label in (TIERS, UNDECIDED):
            items = [t for t in sp["nodes"] if t["conclusion"] == label]
            out.append(f"- 节点内部分化{label}（{len(items)} 个）：" + ("；".join(t["node"] for t in items) or "无"))
        out.append(f"- 节点内部分化{BELOW}：{sum(1 for t in sp['nodes'] if t['conclusion'] == BELOW)} 个；"
                   f"{INSUFFICIENT}：{sum(1 for t in sp['nodes'] if t['conclusion'] == INSUFFICIENT) + len(sp['insufficient_nodes'])} 个")
    return out


def render(report: dict[str, Any], names: dict[str, str]) -> dict[str, str]:
    return {REPORT_NAME: render_json(report), WORKLIST_NAME: render_worklist(report, names)}


def assert_no_pseudonyms(files: dict[str, str], pseudonyms: Iterable[str]) -> None:
    """出口检查：任何输出文本里出现输入伪名即拒绝（消息不回显伪名）"""
    for name, text in files.items():
        for pid in pseudonyms:
            if pid in text:
                raise OutputRefused(f"{name} 含输入里的学生伪名：拒绝写出")


def check_out_dir(out_dir: Path) -> None:
    """目标目录必须不存在或为空，且不是符号链接（不往别人的目录里混写、不覆盖）"""
    if out_dir.is_symlink():
        raise OutputRefused("输出目录是符号链接：请指定新目录或空目录")
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise OutputRefused("输出路径已存在且不是目录：请指定新目录或空目录")
    if any(out_dir.iterdir()):
        raise OutputRefused("输出目录非空：请指定新目录或空目录")


def write_files(files: dict[str, str], out_dir: Path) -> list[Path]:
    check_out_dir(out_dir)
    out_dir.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    written = []
    for name in sorted(files):
        path = out_dir / name
        try:
            fd = os.open(path, flags, FILE_MODE)
        except FileExistsError:
            raise OutputRefused("输出文件已存在：请指定新目录或空目录") from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(files[name])
        written.append(path)
    return written


def run(mp: MapView, mastery: Mastery, cfg: Config, step2: Step2 | None = None) -> dict[str, str]:
    files = render(build_report(mp, mastery, cfg, step2), dict(mp.names))
    assert_no_pseudonyms(files, mastery.pseudonyms | (step2.pseudonyms() if step2 else frozenset()))
    return files
