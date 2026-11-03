"""第二步的输入：作答聚合行、错因行（CSV，个人层，只在内存里）与易错点编码对照（JSON，公共层），以及输入守卫。

作答聚合行（伪名 × 题目 × 节点 × 对错；每名学生每道题每个节点一行，取独立作答、按掌握口径取证据的第一次作答，由导出负责）：
    # format=kge-calibrate-attempts/1 synthetic=true subject=aya map_version=0.1.0
    pseudonym,item_id,node_id,correct[,cognitive_level]   （correct 只收 0 / 1；认知层次可缺省，同一题必须一致，公共层，随报表列出）
错因行（伪名 × 节点 × 学科错因编码 × 跨科大类 × 置信度；只取当前行 superseded_by IS NULL，与作答同口径筛选，由导出负责）：
    # format=kge-calibrate-errors/1 synthetic=true subject=aya map_version=0.1.0
    pseudonym,node_id,subject_category,cross_subject_category,confidence
  错因分类表里状态为待定或敏感的编码（如 xinli_xiguan）：整行丢弃，不进任何计数（只校验格式与映射）。
易错点编码对照（教研给出的公共层对照：易错点 → 它覆盖的学科错因编码）：
    {"format": "kge-calibrate-pitfall-codes/1", "subject": "aya", "map_version": "0.1.0", "pitfalls": {"<易错点 ID>": ["<编码>", …]}}
  这份对照只是教研的临时口径：知识服务快照白名单不含错因字段，易错点的错因大类以 kge.knowledge_node.error_type 为准；
  所以同一易错点的编码必须映射到同一个跨科大类（与 error_type 一个易错点一个大类的约束一致）。

作答表与错因表同时给出时，两表伪名必须有交集（否则必是两次不同导出，不能对齐，拒绝）。

守卫与第一步同一组（任一不满足即拒绝，错误信息只给行号与原因，不回显输入内容）：只接受 synthetic=true 且伪名 syn- 开头；
个人信息列名先于“不认识的列”单独拒绝；伪名 ^(syn|pil)-[a-z]{10,24}$；任何单元格疑似手机号、证件号、邮箱或含汉字一律拒绝；
学科与地图版本必须与快照一致；节点必须是快照里有效、可计算的知识节点；读前 stat 限长、行数上限。
另：题目 ID 与知识服务 knowledge.m2 的 ITEM_ID 同一格式（测试逐字核对）；学科错因编码必须在错因分类表里，跨科大类必须与分类表的映射一致。
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from .inputs import (FORBIDDEN_COLUMN, PSEUDONYM, SYNTHETIC_PREFIX, InputRefused, MapView, looks_personal, read_limited)
from .taxonomy import Taxonomy

ATTEMPTS_FORMAT = "kge-calibrate-attempts/1"
ERRORS_FORMAT = "kge-calibrate-errors/1"
PITFALL_CODES_FORMAT = "kge-calibrate-pitfall-codes/1"
ATTEMPT_COLUMNS = ("pseudonym", "item_id", "node_id", "correct")
ATTEMPT_OPTIONAL = ("cognitive_level",)
ERROR_COLUMNS = ("pseudonym", "node_id", "subject_category", "cross_subject_category", "confidence")
META_KEYS = ("format", "synthetic", "subject", "map_version")
# services/knowledge/knowledge/m2.py 的 ITEM_ID（题目 ID 属公共层，可以列出）
ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
# 形似 UUID 或 32 位十六进制的“题目 ID”一律拒绝（可能是 attempt_id、student_id 等个人层标识混进来）
UUID_LIKE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
COGNITIVE_LEVELS = ("know", "understand", "apply", "synthesize")   # 迁移 0011 kge.item_tag.cognitive_level
MAX_ROWS_BYTES = 128 * 1024 * 1024
MAX_ROWS = 4_000_000
MAX_PITFALL_CODES_BYTES = 4 * 1024 * 1024


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise InputRefused(msg)


def _rows(text: str, mp: MapView, fmt: str, columns: tuple[str, ...], what: str,
          optional: tuple[str, ...] = ()) -> Iterator[tuple[int, dict[str, str]]]:
    """元数据行 + 表头 + 数据行的公共守卫；逐行给出 (行号, {列: 值})，伪名与节点已校验"""
    lines = text.split("\n", 1)
    head = lines[0].rstrip("\r")
    _need(head.startswith("# "), f"{what}第 1 行必须是元数据行（# format={fmt} synthetic=true subject=… map_version=…）")
    meta: dict[str, str] = {}
    for tok in head[2:].split():
        k, sep, v = tok.partition("=")
        _need(sep == "=" and k in META_KEYS and k not in meta, f"{what}元数据行有不认识或重复的键")
        meta[k] = v
    _need(set(meta) == set(META_KEYS), f"{what}元数据行必须含 {META_KEYS}")
    _need(meta["format"] == fmt, f"{what} format 必须是 {fmt}")
    _need(meta["synthetic"] in ("true", "false"), f"{what} synthetic 只能是 true / false")
    _need(meta["synthetic"] == "true", f"本步只接受合成样例（synthetic=true）；试点数据的运行另起任务、由人确认（{what}）")
    _need(meta["subject"] == mp.subject and meta["map_version"] == mp.map_version, f"{what}的学科或地图版本与地图快照不一致")
    reader = csv.reader(io.StringIO(lines[1] if len(lines) > 1 else ""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise InputRefused(f"{what}缺表头") from None
    for i, col in enumerate(header):
        _need(not FORBIDDEN_COLUMN.search(col), f"{what}第 {i + 1} 列疑似个人信息字段（姓名、学校、手机号、证件、住址、账号等一律拒绝）")
        _need(col in columns + optional, f"{what}第 {i + 1} 列不认识（只收 {'、'.join(columns + optional)}）")
    _need(len(set(header)) == len(header), f"{what}列重复")
    _need(set(columns) <= set(header), f"{what}必须有 {'、'.join(columns)} 列")
    rows = 0
    try:
        for row in reader:
            line_no = reader.line_num + 1
            if not row:
                continue
            rows += 1
            _need(rows <= MAX_ROWS, f"{what}超过行数上限")
            _need(len(row) == len(header), f"{what}第 {line_no} 行列数不对")
            for cell in row:
                _need(not looks_personal(cell), f"{what}第 {line_no} 行疑似含个人信息（手机号、证件号、邮箱或汉字）")
            rec = dict(zip(header, row))
            _need(PSEUDONYM.fullmatch(rec["pseudonym"]) is not None, f"{what}第 {line_no} 行伪名格式不对（syn- 或 pil- 加 10–24 个小写字母）")
            _need(rec["pseudonym"].startswith(SYNTHETIC_PREFIX), f"{what}第 {line_no} 行：合成样例的伪名只能是 syn- 开头")
            _need(rec["node_id"] in mp.nodes, f"{what}第 {line_no} 行的节点不是地图快照里有效、可计算的知识节点")
            yield line_no, rec
    except csv.Error:
        raise InputRefused(f"{what}不是合法的 CSV") from None
    _need(rows > 0, f"{what}没有数据行")


def _text(path: str | Path, what: str) -> str:
    data = read_limited(path, MAX_ROWS_BYTES, what)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputRefused(f"{what}不是 UTF-8") from None


# ---------- 作答聚合行 ----------
@dataclass(frozen=True)
class Attempts:
    synthetic: bool
    by_node: Mapping[str, Mapping[str, Mapping[str, bool]]]   # 节点 → 题目 → {伪名 → 是否答对}
    pseudonyms: frozenset[str] = field(repr=False)
    levels: Mapping[str, str] = field(default_factory=dict)   # 题目 → 认知层次（公共层；未给出的不在里面）

    @property
    def students(self) -> int:
        return len(self.pseudonyms)


def parse_attempts(text: str, mp: MapView) -> Attempts:
    what = "作答聚合表"
    by_node: dict[str, dict[str, dict[str, bool]]] = {}
    levels: dict[str, str] = {}
    pids: set[str] = set()
    for line_no, r in _rows(text, mp, ATTEMPTS_FORMAT, ATTEMPT_COLUMNS, what, ATTEMPT_OPTIONAL):
        _need(ITEM_ID.fullmatch(r["item_id"]) is not None, f"{what}第 {line_no} 行题目 ID 格式不对")
        _need(UUID_LIKE.fullmatch(r["item_id"]) is None, f"{what}第 {line_no} 行题目 ID 形似 UUID（疑似个人层标识）")
        _need(r["correct"] in ("0", "1"), f"{what}第 {line_no} 行 correct 只能是 0 / 1")
        lv = r.get("cognitive_level", "")
        _need(lv == "" or lv in COGNITIVE_LEVELS, f"{what}第 {line_no} 行认知层次不合法")
        _need(levels.get(r["item_id"], lv) == lv, f"{what}第 {line_no} 行：同一题目的认知层次前后不一致")
        if lv:
            levels[r["item_id"]] = lv
        items = by_node.setdefault(r["node_id"], {}).setdefault(r["item_id"], {})
        _need(r["pseudonym"] not in items, f"{what}第 {line_no} 行重复（同一伪名同一题目同一节点只能一行）")
        items[r["pseudonym"]] = r["correct"] == "1"
        pids.add(r["pseudonym"])
    return Attempts(True, by_node, frozenset(pids), dict(sorted(levels.items())))


def load_attempts(path: str | Path, mp: MapView) -> Attempts:
    return parse_attempts(_text(path, "作答聚合表"), mp)


# ---------- 错因行 ----------
@dataclass(frozen=True)
class Errors:
    """episodes：节点 → {伪名 → 这名学生在这个节点上高置信错因行的学科编码集合}；集合为空 = 只有低置信行（难归类）"""
    synthetic: bool
    episodes: Mapping[str, Mapping[str, frozenset[str]]]
    pseudonyms: frozenset[str] = field(repr=False)

    @property
    def students(self) -> int:
        return len(self.pseudonyms)


def parse_errors(text: str, mp: MapView, tax: Taxonomy, min_confidence: float) -> Errors:
    what = "错因表"
    codes = tax.codes.get(mp.subject, {})
    _need(bool(codes), "错因分类表里没有该学科的编码")
    usable = set(tax.subject_codes(mp.subject))
    acc: dict[str, dict[str, set[str]]] = {}
    pids: set[str] = set()
    for line_no, r in _rows(text, mp, ERRORS_FORMAT, ERROR_COLUMNS, what):
        code, cat = r["subject_category"], r["cross_subject_category"]
        _need(code in codes, f"{what}第 {line_no} 行的学科错因编码不在错因分类表里")
        _need(cat == codes[code], f"{what}第 {line_no} 行的跨科大类与错因分类表的映射不一致")
        try:
            conf = float(r["confidence"])
        except ValueError:
            conf = math.nan
        _need(math.isfinite(conf) and 0.0 <= conf <= 1.0, f"{what}第 {line_no} 行置信度不在 [0, 1] 内")
        if code not in usable:
            continue                                   # 待定或敏感编码：整行丢弃，不进任何计数
        ep = acc.setdefault(r["node_id"], {}).setdefault(r["pseudonym"], set())
        if conf >= min_confidence:
            ep.add(code)
        pids.add(r["pseudonym"])
    return Errors(True, {n: {p: frozenset(c) for p, c in d.items()} for n, d in acc.items()}, frozenset(pids))


def load_errors(path: str | Path, mp: MapView, tax: Taxonomy, min_confidence: float) -> Errors:
    return parse_errors(_text(path, "错因表"), mp, tax, min_confidence)


@dataclass(frozen=True)
class Step2:
    """第二步的输入（都可缺省：没有错因输入就不出易错候选与难归类，没有作答输入就不出拆分候选）"""
    taxonomy: Taxonomy | None = None
    attempts: Attempts | None = None
    errors: Errors | None = None
    pitfall_codes: Mapping[str, frozenset[str]] | None = None

    def __post_init__(self) -> None:
        if self.attempts is not None and self.errors is not None and self.attempts.pseudonyms and self.errors.pseudonyms:
            _need(bool(self.attempts.pseudonyms & self.errors.pseudonyms),
                  "作答聚合表与错因表的伪名完全不相交：不是同一次导出，无法对齐（拒绝）")

    def pseudonyms(self) -> frozenset[str]:
        out: frozenset[str] = frozenset()
        for src in (self.attempts, self.errors):
            if src is not None:
                out |= src.pseudonyms
        return out


# ---------- 易错点编码对照（公共层） ----------
def parse_pitfall_codes(doc: Any, mp: MapView, tax: Taxonomy) -> dict[str, frozenset[str]]:
    what = "易错点编码对照"
    _need(isinstance(doc, dict) and set(doc) == {"format", "subject", "map_version", "pitfalls"},
          f"{what}只收 format、subject、map_version、pitfalls 四个键")
    _need(doc["format"] == PITFALL_CODES_FORMAT, f"{what} format 必须是 {PITFALL_CODES_FORMAT}")
    _need(doc["subject"] == mp.subject and doc["map_version"] == mp.map_version, f"{what}的学科或地图版本与地图快照不一致")
    pits = doc["pitfalls"]
    _need(isinstance(pits, dict), f"{what} pitfalls 必须是对象")
    codes = tax.codes.get(mp.subject, {})
    out: dict[str, frozenset[str]] = {}
    for i, (pid, lst) in enumerate(sorted(pits.items())):
        _need(pid in mp.pitfall_ids, f"{what}第 {i + 1} 项不是地图快照里有效的易错点")
        _need(isinstance(lst, list) and len(lst) == len(set(lst)) and all(isinstance(c, str) and c in codes for c in lst),
              f"{what}第 {i + 1} 项的编码必须是错因分类表里该学科的编码且不重复")
        _need(len({codes[c] for c in lst}) <= 1,
              f"{what}第 {i + 1} 项的编码映射到不同的跨科大类（一个易错点只有一个错因大类，以 kge 的 error_type 为准）")
        out[pid] = frozenset(lst)
    return out


def load_pitfall_codes(path: str | Path, mp: MapView, tax: Taxonomy) -> dict[str, frozenset[str]]:
    p = Path(path)
    _need(p.suffix == ".json", "易错点编码对照只收 .json")
    data = read_limited(p, MAX_PITFALL_CODES_BYTES, "易错点编码对照")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InputRefused("易错点编码对照不是合法的 UTF-8 JSON") from None
    return parse_pitfall_codes(doc, mp, tax)
