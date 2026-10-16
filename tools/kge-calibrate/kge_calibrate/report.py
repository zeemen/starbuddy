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

SCHEMA_VERSION = "kge-calibrate-report/0.2"
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


def build_report(mp: MapView, mastery: Mastery, cfg: Config) -> dict[str, Any]:
    edges = check_edges(mp, mastery, cfg)
    pairs, excluded = scan_pairs(mp, mastery, cfg)
    students_ok = mastery.students >= cfg.min_cell
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "notice": SYNTHETIC_NOTICE if mastery.synthetic else "",
        "status": STATUS,
        "config": cfg.provenance(),
        "map": {"subject": mp.subject, "map_version": mp.map_version, "snapshot_digest": mp.digest,
                "computable_nodes": len(mp.nodes),
                "prerequisite_edges": {s: sum(1 for e in mp.edges if e.strength == s) for s in ("strong", "weak")}},
        "cohort": {"students_approx": n_approx(mastery.students) if students_ok else INSUFFICIENT},
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
        },
        "prerequisite_tests": edges,
        "hidden_dependency": {"excluded_pairs": excluded, "pairs": pairs},
        "summary": {"prerequisite": {c: sum(1 for e in edges if e["conclusion"] == c) for c in EDGE_CONCLUSIONS},
                    "hidden_dependency": {c: sum(1 for p in pairs if p["conclusion"] == c) for c in PAIR_CONCLUSIONS}},
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
    lines = ["# 地图校准工作单（KGE-09 第一步）", ""]
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

    lines += ["## 三、其余结论（供参考，不需勾选）", ""]
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
              f"主题节点对数超上限而未扫描：{ex['topic_over_limit']} 对", "",
              "详细数值见同目录 calibration-report.json。", ""]
    return "\n".join(lines)


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


def run(mp: MapView, mastery: Mastery, cfg: Config) -> dict[str, str]:
    files = render(build_report(mp, mastery, cfg), dict(mp.names))
    assert_no_pseudonyms(files, mastery.pseudonyms)
    return files
