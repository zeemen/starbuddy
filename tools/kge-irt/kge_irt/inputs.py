"""输入：题目元数据快照（JSON，公共层）、作答表与掌握估计基线（CSV，个人层，只在内存里），以及输入守卫。

题目元数据快照（kge.item_tag 的公共层字段）：
    {"format": "kge-irt-items/1", "synthetic": true, "subject": "aya", "map_version": "0.1.0",
     "items": [{"item_id": "…", "node_id": "…", "cognitive_level": "apply", "item_type": "…", "prior_difficulty": 0.4,
                "family_code": "…"}]}
  node_id = kge.item_tag.main_node_id（只计主节点，次要节点不计，与掌握计算 evidence.py #16 同一口径）；cognitive_level 同
  kge.item_tag.cognitive_level（必填）；item_type 为题型（可为空，基线按题型取 p_guess）；prior_difficulty 为 [0, 1]、越大越难、
  至多 3 位小数，可为空（空的题不进两种“先验难度”对照与评估）；family_code = kge.item_family.code（可为空，只随报表列出）。
作答表（伪名 × 题目 × 节点 × 对错 × 作答时间序 × 段；每名学生每道题一行，取独立作答的第一次，口径同 KGE-09，由导出负责）：
    # format=kge-irt-responses/1 synthetic=true subject=aya map_version=0.1.0
    pseudonym,item_id,node_id,correct,seq,segment      （seq：全表唯一的非负整数时间序；segment：fit / eval，由导出按切分点标）
掌握估计基线（同一切分点上现行掌握估计的掌握概率，一个单元 = 节点 × 认知层次，与 platform.mastery_estimate 同粒度）：
    # format=kge-irt-baseline/1 synthetic=true subject=aya map_version=0.1.0
    pseudonym,node_id,cognitive_level,p_mastery

守卫复用 kge-calibrate（KGE-09）的同一组函数，不另立口径：元数据行、synthetic=true 才收、合成样例伪名只能 syn- 开头、个人信息列名先于
“不认识的列”单独拒绝、伪名格式、单元格疑似手机号 / 证件号 / 邮箱 / 汉字一律拒绝（快照里的题目 ID、题型、题目族编码同样过 looks_personal）、
读前 stat 限长、行数上限（kge_calibrate.responses._rows 与 read_limited）；题目 ID 与知识服务 ITEM_ID 同一格式、拒绝形似 UUID 的题目 ID；
节点 ID 与 kge.node_id 同一格式、学科前缀一致；认知层次取值与 kge.item_tag 同一组。
另：作答的节点必须是快照里这道题的主节点；fit 段的时间序必须全部早于 eval 段；实际拟合比例须在配置范围内；基线与作答的伪名须有交集。
synthetic 标记一律取自元数据（快照的 synthetic 键、CSV 的元数据行），不写死。
错误信息只给行号与原因，不回显输入内容。伪名只在内存里对齐同一学生，任何输出都不含伪名（report.py 另有出口检查）。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from kge_calibrate.inputs import NODE_ID, SUBJECT_PREFIX, InputRefused, canonical_digest, looks_personal, read_limited
from kge_calibrate.responses import COGNITIVE_LEVELS, ITEM_ID, UUID_LIKE, _rows, _text

ITEMS_FORMAT = "kge-irt-items/1"
RESPONSES_FORMAT = "kge-irt-responses/1"
BASELINE_FORMAT = "kge-irt-baseline/1"
RESPONSE_COLUMNS = ("pseudonym", "item_id", "node_id", "correct", "seq", "segment")
BASELINE_COLUMNS = ("pseudonym", "node_id", "cognitive_level", "p_mastery")
SEGMENTS = ("fit", "eval")
MAP_VERSION = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,4}")   # 与 kge_calibrate.inputs.parse_map 同一写法
ITEM_KEYS = {"item_id", "node_id", "cognitive_level", "item_type", "prior_difficulty", "family_code"}
MAX_ITEMS_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 200_000
MAX_SEQ = 10**12
__all__ = ["InputRefused", "ItemsView", "Responses", "Baseline", "parse_items", "parse_responses", "parse_baseline", "load_items",
           "load_responses", "load_baseline"]


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise InputRefused(msg)


def _code(v: Any) -> bool:
    """公共层编码（题目 ID、题型、题目族编码）：ITEM_ID 格式、不形似 UUID、不像个人信息"""
    return isinstance(v, str) and ITEM_ID.fullmatch(v) is not None and UUID_LIKE.fullmatch(v) is None and not looks_personal(v)


def meta_synthetic(text: str) -> bool:
    """CSV 元数据行里的 synthetic（守卫已要求它是 true；这里如实取出，报表的合成标注由它决定）"""
    head = text.split("\n", 1)[0].rstrip("\r")
    toks = dict(t.partition("=")[::2] for t in head[2:].split()) if head.startswith("# ") else {}
    return toks.get("synthetic") == "true"


@dataclass(frozen=True)
class Item:
    item_id: str
    node_id: str
    cognitive_level: str
    item_type: str | None
    prior: float | None       # 先验难度（[0, 1]，越大越难）；None = 无先验
    family_code: str | None


@dataclass(frozen=True)
class ItemsView:
    """题目快照（公共层）。subject / map_version / nodes 三个属性与 kge-calibrate 的 MapView 同名（复用它的作答行守卫）"""
    subject: str
    map_version: str
    digest: str
    items: Mapping[str, Item]
    nodes: frozenset[str]
    synthetic: bool = True


def parse_items(doc: Any) -> ItemsView:
    what = "题目快照"
    _need(isinstance(doc, dict) and set(doc) == {"format", "synthetic", "subject", "map_version", "items"},
          f"{what}只收 format、synthetic、subject、map_version、items 五个键")
    _need(doc["format"] == ITEMS_FORMAT, f"{what} format 必须是 {ITEMS_FORMAT}")
    _need(doc["synthetic"] is True, f"本步只接受合成样例（synthetic=true）；试点数据的运行另起任务、由人确认（{what}）")
    _need(doc["subject"] in SUBJECT_PREFIX, f"{what} subject 只收平台学科编码")
    _need(isinstance(doc["map_version"], str) and MAP_VERSION.fullmatch(doc["map_version"]) is not None,
          f"{what} map_version 必须是 主.次.修订")
    raw = doc["items"]
    _need(isinstance(raw, list) and 0 < len(raw) <= MAX_ITEMS, f"{what} items 必须是非空数组（至多 {MAX_ITEMS} 道）")
    prefix = SUBJECT_PREFIX[doc["subject"]]
    items: dict[str, Item] = {}
    for i, it in enumerate(raw):
        where = f"{what} items[{i}]"
        _need(isinstance(it, dict) and set(it) == ITEM_KEYS, f"{where}：只收 {sorted(ITEM_KEYS)}")
        iid, nid, prior, fam = it["item_id"], it["node_id"], it["prior_difficulty"], it["family_code"]
        _need(_code(iid), f"{where}：题目 ID 格式不对（或形似 UUID、疑似个人信息）")
        _need(iid not in items, f"{where}：题目重复")
        _need(isinstance(nid, str) and NODE_ID.fullmatch(nid) is not None and nid.split(".")[0] == prefix,
              f"{where}：节点 ID 不合规范或学科前缀不符")
        _need(it["cognitive_level"] in COGNITIVE_LEVELS, f"{where}：认知层次不合法")
        _need(it["item_type"] is None or _code(it["item_type"]), f"{where}：题型编码格式不对（或疑似个人信息）")
        if prior is not None:
            _need(isinstance(prior, (int, float)) and not isinstance(prior, bool) and math.isfinite(prior) and 0.0 <= prior <= 1.0
                  and abs(round(prior, 3) - prior) < 1e-12, f"{where}：先验难度必须是 [0, 1] 内、至多 3 位小数的数（kge.item_tag numeric(4, 3)）")
        _need(fam is None or _code(fam), f"{where}：题目族编码格式不对（或疑似个人信息）")
        items[iid] = Item(iid, nid, it["cognitive_level"], it["item_type"], None if prior is None else float(prior), fam)
    try:
        digest = canonical_digest(doc)
    except (TypeError, ValueError):
        raise InputRefused(f"{what}无法规范化") from None
    return ItemsView(doc["subject"], doc["map_version"], digest, dict(sorted(items.items())),
                     frozenset(it.node_id for it in items.values()), doc["synthetic"] is True)


def load_items(path: str | Path) -> ItemsView:
    p = Path(path)
    _need(p.suffix == ".json", "题目快照只收 .json")
    data = read_limited(p, MAX_ITEMS_BYTES, "题目快照")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InputRefused("题目快照不是合法的 UTF-8 JSON") from None
    return parse_items(doc)


# ---------- 作答 ----------
@dataclass(frozen=True)
class Responses:
    """fit / eval：节点 → {伪名 → ((题目, 是否答对), …)}，按时间序排列"""
    synthetic: bool
    fit: Mapping[str, Mapping[str, tuple[tuple[str, bool], ...]]]
    eval: Mapping[str, Mapping[str, tuple[tuple[str, bool], ...]]]
    fit_fraction: float
    pseudonyms: frozenset[str] = field(repr=False)

    @property
    def students(self) -> int:
        return len(self.pseudonyms)


def parse_responses(text: str, iv: ItemsView, min_fit_fraction: float, max_fit_fraction: float) -> Responses:
    what = "作答表"
    acc: dict[str, dict[str, dict[str, list[tuple[int, str, bool]]]]] = {s: {} for s in SEGMENTS}
    seen: set[tuple[str, str]] = set()
    seqs: set[int] = set()
    pids: set[str] = set()
    last_fit, first_eval = -1, MAX_SEQ + 1
    for line_no, r in _rows(text, iv, RESPONSES_FORMAT, RESPONSE_COLUMNS, what):
        item = iv.items.get(r["item_id"])
        _need(item is not None, f"{what}第 {line_no} 行的题目不在题目快照里")
        _need(r["node_id"] == item.node_id, f"{what}第 {line_no} 行的节点与题目快照里这道题的主节点不一致")
        _need(r["correct"] in ("0", "1"), f"{what}第 {line_no} 行 correct 只能是 0 / 1")
        _need(r["seq"].isascii() and r["seq"].isdigit() and len(r["seq"]) <= 12, f"{what}第 {line_no} 行 seq 必须是非负整数")
        seq = int(r["seq"])
        _need(seq not in seqs, f"{what}第 {line_no} 行 seq 重复（时间序全表唯一）")
        seqs.add(seq)
        _need(r["segment"] in SEGMENTS, f"{what}第 {line_no} 行 segment 只能是 fit / eval")
        key = (r["pseudonym"], r["item_id"])
        _need(key not in seen, f"{what}第 {line_no} 行重复（同一伪名同一题目只能一行：独立作答的第一次）")
        seen.add(key)
        if r["segment"] == "fit":
            last_fit = max(last_fit, seq)
        else:
            first_eval = min(first_eval, seq)
        acc[r["segment"]].setdefault(item.node_id, {}).setdefault(r["pseudonym"], []).append((seq, r["item_id"], r["correct"] == "1"))
        pids.add(r["pseudonym"])
    _need(last_fit < first_eval, f"{what}：fit 段的时间序必须全部早于 eval 段（切分点前后不能交错）")
    n_fit = sum(len(v) for d in acc["fit"].values() for v in d.values())
    frac = n_fit / len(seen)
    _need(min_fit_fraction <= frac <= max_fit_fraction, f"{what}：拟合段占比不在配置范围 [{min_fit_fraction}, {max_fit_fraction}] 内")
    out = {s: {n: {p: tuple((i, x) for _, i, x in sorted(v)) for p, v in sorted(d.items())} for n, d in sorted(acc[s].items())}
           for s in SEGMENTS}
    return Responses(meta_synthetic(text), out["fit"], out["eval"], frac, frozenset(pids))


def load_responses(path: str | Path, iv: ItemsView, min_fit_fraction: float, max_fit_fraction: float) -> Responses:
    return parse_responses(_text(path, "作答表"), iv, min_fit_fraction, max_fit_fraction)


# ---------- 掌握估计基线 ----------
@dataclass(frozen=True)
class Baseline:
    synthetic: bool
    p_mastery: Mapping[tuple[str, str], Mapping[str, float]]   # (节点, 认知层次) → {伪名 → 掌握概率}
    pseudonyms: frozenset[str] = field(repr=False)


def parse_baseline(text: str, iv: ItemsView, responses: Responses) -> Baseline:
    what = "掌握估计基线"
    pm: dict[tuple[str, str], dict[str, float]] = {}
    pids: set[str] = set()
    for line_no, r in _rows(text, iv, BASELINE_FORMAT, BASELINE_COLUMNS, what):
        _need(r["cognitive_level"] in COGNITIVE_LEVELS, f"{what}第 {line_no} 行认知层次不合法")
        try:
            p = float(r["p_mastery"])
        except ValueError:
            p = math.nan
        _need(math.isfinite(p) and 0.0 <= p <= 1.0, f"{what}第 {line_no} 行掌握概率不在 [0, 1] 内")
        d = pm.setdefault((r["node_id"], r["cognitive_level"]), {})
        _need(r["pseudonym"] not in d, f"{what}第 {line_no} 行重复（同一伪名同一单元只能一行）")
        d[r["pseudonym"]] = p
        pids.add(r["pseudonym"])
    _need(bool(pids & responses.pseudonyms), f"{what}与作答表的伪名完全不相交：不是同一次导出，无法对齐（拒绝）")
    return Baseline(meta_synthetic(text), {k: dict(sorted(d.items())) for k, d in sorted(pm.items())}, frozenset(pids))


def load_baseline(path: str | Path, iv: ItemsView, responses: Responses) -> Baseline:
    return parse_baseline(_text(path, "掌握估计基线"), iv, responses)
