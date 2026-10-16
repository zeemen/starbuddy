"""输入：地图快照（JSON）与掌握状态表（CSV），以及输入守卫。

地图快照沿用知识服务 knowledge.atoms 的快照白名单格式（与 knowledge.m1 / m2 读的 map-snapshot.json 同一份文件，不加任何字段；
本工具纯标准库，只收 .json，不收 YAML）。白名单逐字复制自 services/knowledge/knowledge/atoms.py，测试逐字核对。

掌握状态表（伪名 × 节点 × 星亮层次那一行的门槛状态与掌握概率）：首行是元数据行，其余是 CSV。
    # format=kge-calibrate-mastery/1 synthetic=true subject=aya map_version=0.1.0
    pseudonym,node_id,gate,p_mastery
每名学生每个节点一行，取按掌握配置 star_level_rule 选出的星亮层次（与学习服务 star_basis_level 同口径，由导出负责），
门槛与概率取自同一行；判定只看门槛（config.MasteryRule，与 star_lit 同口径），掌握概率只校验取值、不参与判定。
守卫（任一不满足即拒绝，错误信息只给行号与原因，不回显输入内容）：
- 本步只接受合成样例：元数据行必须 synthetic=true（spec 范围 1：试点数据的运行另起任务由人确认），且合成样例的伪名只能是 syn- 开头；
- 列只收 pseudonym、node_id、gate（必有）与 p_mastery；疑似姓名、学校、手机号、证件、住址、账号等字段先于“不认识的列”单独拒绝；
- 伪名只收 ^(syn|pil)-[a-z]{10,24}$（不可能是 UUID 或手机号）；任何单元格疑似手机号、身份证号、邮箱或含汉字一律拒绝；
- 学科与地图版本必须与地图快照一致；节点必须是快照里有效、可计算的知识节点；同一伪名同一节点只能一行；
- 资源上限：读文件前先查大小（只收普通文件），再按上限读；行数上限。
伪名只在内存里用来对齐同一学生的不同节点，任何输出都不含伪名（report.py 另有出口检查）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import GATES, MasteryRule

# ---------- 地图快照：knowledge.atoms 的白名单（逐字复制，测试核对） ----------
_FIELDS: dict[str, dict[str, bool]] = {  # 字段 → 是否必填
    "node": {"id": True, "level": True, "parent_id": False, "kind": False, "name": True, "includes": False, "excludes": False,
             "examples": False, "scope": False, "cognitive_levels": False, "status": False},
    "edge": {"from_id": True, "to_id": True, "type": True, "strength": False, "status": False},
    "curriculum": {"standard_year": True, "code": True, "domain": True, "text": True, "verb_level": True, "source_ref": True},
    "curriculum_link": {"node_id": True, "standard_year": True, "code": True},
    "projection": {"id": True, "node_id": True, "edition": True, "edition_year": True, "printing": False, "volume": True,
                   "chapter": True, "page_from": True, "page_to": True, "quote_text": False, "mapping_state": False},
    "region": {"id": True, "node_id": True, "region": True, "exam_year": True, "weight": True, "typical_slots": False,
               "style_tags": False, "source_ref": True},
    "affair": {"id": True, "node_ids": True, "source": True, "valid_from": True, "valid_until": True, "review_status": False},
}
_TOP = {"map_version": True, "subject": True, "status": True, "standard_year": True, "nodes": True, "edges": False,
        "curriculum_items": False, "curriculum_links": False, "textbook_projections": False, "region_projections": False,
        "current_affairs": False}
_LISTS = {"nodes": "node", "edges": "edge", "curriculum_items": "curriculum", "curriculum_links": "curriculum_link",
          "textbook_projections": "projection", "region_projections": "region", "current_affairs": "affair"}
EDGE_TYPES = ("prerequisite", "part_of", "related", "confusable", "uses_method", "has_pitfall", "aligned_to")
# knowledge/nodeid.py（迁移 0011 的 kge.node_id 域）
NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")
SUBJECT_PREFIX: dict[str, str] = {"aya": "MATH", "wenli": "CHN", "zhixing": "MORAL", "echo": "ENG"}
# 只有这三类知识节点带认知层次、参与掌握计算（迁移 0011；services/learning/learning/mastery/store.py 的 COMPUTABLE_KINDS）
COMPUTABLE_KINDS = ("kp", "ability_kp", "viewpoint")
STRENGTHS = ("strong", "weak")

MAX_MAP_BYTES = 16 * 1024 * 1024
MAX_MASTERY_BYTES = 64 * 1024 * 1024
MAX_MASTERY_ROWS = 2_000_000

MASTERY_FORMAT = "kge-calibrate-mastery/1"
META_KEYS = ("format", "synthetic", "subject", "map_version")
MASTERY_COLUMNS = ("pseudonym", "node_id", "gate", "p_mastery")
# 疑似个人信息的列名（中英文，大小写不敏感）：先于“不认识的列”单独拒绝
FORBIDDEN_COLUMN = re.compile(
    r"name|姓名|名字|school|学校|class|班级|grade_?level|年级|phone|mobile|tel|手机|电话|e-?mail|邮箱|address|住址|地址"
    r"|id_?card|identity|身份证|证件|birth|生日|出生|guardian|parent|家长|监护|wechat|微信|qq|student_?id|user_?id|account|账号"
    r"|gender|sex|性别|region|district|city|地区|区县|城市",
    re.IGNORECASE)
PSEUDONYM = re.compile(r"^(syn|pil)-[a-z]{10,24}$")
SYNTHETIC_PREFIX = "syn-"
# 个人信息模式（与 KGE-01 导入器、KGE-08 输入守卫同一组）：NFKC、去空白后匹配；另拒绝任何汉字（伪名、节点、门槛都不该有）
PERSONAL = (re.compile(r"1[3-9]\d{9}"), re.compile(r"\d{17}[\dXx]"), re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.]+"),
            re.compile(r"[㐀-鿿]"))


class InputRefused(ValueError):
    """输入不合格或疑似含个人信息：拒绝运行（消息只含行号、字段位置与原因）"""


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise InputRefused(msg)


def read_limited(path: str | Path, limit: int, what: str) -> bytes:
    """先 stat（只收普通文件、不超过上限），再最多读 limit + 1 字节（读的过程中文件变大也拦得住）"""
    try:
        st = os.stat(path)
    except OSError:
        raise InputRefused(f"{what}读不了") from None
    _need(stat.S_ISREG(st.st_mode), f"{what}必须是普通文件")
    _need(st.st_size <= limit, f"{what}超过大小上限")
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    _need(len(data) <= limit, f"{what}超过大小上限")
    return data


def looks_personal(value: str) -> bool:
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    return any(r.search(t) for r in PERSONAL)


def canonical_digest(doc: Any) -> str:
    """地图快照的规范化摘要，与 knowledge.m1.snapshot_digest 同一口径（按键排序、无多余空白、保留中文、sha256）"""
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------- 地图 ----------
@dataclass(frozen=True)
class Edge:
    from_id: str
    to_id: str
    strength: str


@dataclass(frozen=True)
class MapView:
    subject: str
    map_version: str
    digest: str
    nodes: Mapping[str, str]                 # 可计算的有效知识节点 → 所属主题（parent_id）
    names: Mapping[str, str]                 # 节点与主题 → 名称（公共层）
    edges: tuple[Edge, ...]                  # 有效前置边（两端都是可计算节点），按 (from, to) 排序
    all_prereq: tuple[tuple[str, str], ...]  # 全部有效前置边（含不可计算节点），用于传递可达


def _pick(kind: str, raw: Any, where: str) -> dict[str, Any]:
    _need(isinstance(raw, dict), f"地图快照 {where}：必须是对象")
    spec = _FIELDS[kind]
    _need(not (set(raw) - set(spec)), f"地图快照 {where}：有白名单以外的字段（快照只收公共层白名单字段）")
    missing = [k for k, req in spec.items() if req and raw.get(k) in (None, "")]
    _need(not missing, f"地图快照 {where}：缺字段 {missing}")
    return dict(raw)


def parse_map(doc: Any) -> MapView:
    _need(isinstance(doc, dict), "地图快照必须是对象")
    _need(not (set(doc) - set(_TOP)), "地图快照顶层有白名单以外的字段")
    missing = [k for k, req in _TOP.items() if req and doc.get(k) in (None, "")]
    _need(not missing, f"地图快照顶层缺字段 {missing}")
    subject = doc["subject"]
    _need(subject in SUBJECT_PREFIX, "地图快照 subject 只收平台学科编码")
    _need(isinstance(doc["map_version"], str) and re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,4}", doc["map_version"]) is not None,
          "地图快照 map_version 必须是 主.次.修订")
    lists: dict[str, list[dict[str, Any]]] = {}
    for key, kind in _LISTS.items():
        raw = doc.get(key) or []
        _need(isinstance(raw, list), f"地图快照 {key}：必须是数组")
        lists[key] = [_pick(kind, r, f"{key}[{i}]") for i, r in enumerate(raw)]

    prefix = SUBJECT_PREFIX[subject]
    all_ids: set[str] = set()
    nodes: dict[str, str] = {}
    names: dict[str, str] = {}
    for i, n in enumerate(lists["nodes"]):
        nid = n["id"]
        _need(isinstance(nid, str) and NODE_ID.fullmatch(nid) is not None and nid.split(".")[0] == prefix,
              f"地图快照 nodes[{i}]：节点 ID 不合规范或学科前缀不符")
        _need(nid not in all_ids, f"地图快照 nodes[{i}]：节点重复")
        all_ids.add(nid)
        _need(isinstance(n["name"], str), f"地图快照 nodes[{i}]：name 必须是字符串")
        names[nid] = n["name"]
        if (n["level"] == "node" and n.get("status", "active") == "active" and n.get("kind") in COMPUTABLE_KINDS
                and isinstance(n.get("parent_id"), str)):
            nodes[nid] = n["parent_id"]
    edges: dict[tuple[str, str], Edge] = {}
    all_prereq: set[tuple[str, str]] = set()
    for i, e in enumerate(lists["edges"]):
        _need(e["type"] in EDGE_TYPES, f"地图快照 edges[{i}]：边类型不合法")
        _need(e["from_id"] in all_ids and e["to_id"] in all_ids, f"地图快照 edges[{i}]：指向不存在的节点")
        _need(e["from_id"] != e["to_id"], f"地图快照 edges[{i}]：自环")
        strength = e.get("strength", "strong")
        _need(strength in STRENGTHS, f"地图快照 edges[{i}]：strength 只能是 strong / weak")
        if e["type"] != "prerequisite" or e.get("status", "active") != "active":
            continue
        key = (e["from_id"], e["to_id"])
        _need(key not in all_prereq, f"地图快照 edges[{i}]：前置边重复")
        all_prereq.add(key)
        if e["from_id"] in nodes and e["to_id"] in nodes:
            edges[key] = Edge(e["from_id"], e["to_id"], strength)
    _need(bool(nodes), "地图快照里没有可计算的有效知识节点")
    try:
        digest = canonical_digest(doc)
    except (TypeError, ValueError):
        raise InputRefused("地图快照无法规范化") from None
    return MapView(subject, doc["map_version"], digest, dict(sorted(nodes.items())), dict(sorted(names.items())),
                   tuple(edges[k] for k in sorted(edges)), tuple(sorted(all_prereq)))


def load_map(path: str | Path) -> MapView:
    p = Path(path)
    _need(p.suffix == ".json", "地图快照只收 .json（knowledge.atoms 快照格式的 JSON 写法）")
    data = read_limited(p, MAX_MAP_BYTES, "地图快照")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InputRefused("地图快照不是合法的 UTF-8 JSON") from None
    return parse_map(doc)


# ---------- 掌握状态 ----------
@dataclass(frozen=True)
class Mastery:
    synthetic: bool
    students: int                                    # 不同伪名数（只在聚合时用）
    observed: Mapping[str, Mapping[str, bool]]       # 节点 → {伪名 → 是否掌握}；未观测的不在里面
    pseudonyms: frozenset[str] = field(repr=False)   # 只用于出口检查（输出里不得出现）


def _meta(line: str) -> dict[str, str]:
    _need(line.startswith("# "), f"掌握状态表第 1 行必须是元数据行（# format={MASTERY_FORMAT} synthetic=true subject=… map_version=…）")
    out: dict[str, str] = {}
    for tok in line[2:].split():
        k, sep, v = tok.partition("=")
        _need(sep == "=" and k in META_KEYS and k not in out, "掌握状态表元数据行有不认识或重复的键")
        out[k] = v
    _need(set(out) == set(META_KEYS), f"掌握状态表元数据行必须含 {META_KEYS}")
    _need(out["format"] == MASTERY_FORMAT, f"掌握状态表 format 必须是 {MASTERY_FORMAT}")
    _need(out["synthetic"] in ("true", "false"), "掌握状态表 synthetic 只能是 true / false")
    return out


def _mastered(rule: MasteryRule, gate: str) -> bool | None:
    """掌握 → True，未掌握 → False，未观测（unseen 或空门槛）→ None。只看门槛，不看掌握概率（star_lit 同口径）"""
    if gate in ("unseen", ""):
        return None
    return rule.mastered(gate)


def parse_mastery(text: str, mp: MapView, rule: MasteryRule) -> Mastery:
    lines = text.split("\n", 1)
    meta = _meta(lines[0].rstrip("\r"))
    synthetic = meta["synthetic"] == "true"
    _need(synthetic, "本步只接受合成样例（synthetic=true）；试点数据的运行另起任务、由人确认（spec 范围 1）")
    _need(meta["subject"] == mp.subject and meta["map_version"] == mp.map_version, "掌握状态表的学科或地图版本与地图快照不一致")
    reader = csv.reader(io.StringIO(lines[1] if len(lines) > 1 else ""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise InputRefused("掌握状态表缺表头") from None
    for i, col in enumerate(header):
        _need(not FORBIDDEN_COLUMN.search(col), f"掌握状态表第 {i + 1} 列疑似个人信息字段（姓名、学校、手机号、证件、住址、账号等一律拒绝）")
        _need(col in MASTERY_COLUMNS, f"掌握状态表第 {i + 1} 列不认识（只收 {'、'.join(MASTERY_COLUMNS)}）")
    _need(len(set(header)) == len(header), "掌握状态表列重复")
    _need("pseudonym" in header and "node_id" in header, "掌握状态表必须有 pseudonym 与 node_id 列")
    _need("gate" in header, "掌握按星亮口径只看门槛状态，掌握状态表必须有 gate 列")
    idx = {c: header.index(c) for c in header}

    observed: dict[str, dict[str, bool]] = {}
    seen: set[tuple[str, str]] = set()
    pseudonyms: set[str] = set()
    rows = 0
    try:
        for row in reader:
            line_no = reader.line_num + 1
            if not row:
                continue
            rows += 1
            _need(rows <= MAX_MASTERY_ROWS, "掌握状态表超过行数上限")
            _need(len(row) == len(header), f"掌握状态表第 {line_no} 行列数不对")
            for cell in row:
                _need(not looks_personal(cell), f"掌握状态表第 {line_no} 行疑似含个人信息（手机号、证件号、邮箱或汉字）")
            pid, nid = row[idx["pseudonym"]], row[idx["node_id"]]
            _need(PSEUDONYM.fullmatch(pid) is not None, f"掌握状态表第 {line_no} 行伪名格式不对（syn- 或 pil- 加 10–24 个小写字母）")
            _need(not synthetic or pid.startswith(SYNTHETIC_PREFIX), f"掌握状态表第 {line_no} 行：合成样例的伪名只能是 syn- 开头")
            _need(nid in mp.nodes, f"掌握状态表第 {line_no} 行的节点不是地图快照里有效、可计算的知识节点")
            _need((pid, nid) not in seen, f"掌握状态表第 {line_no} 行重复（同一伪名同一节点只能一行）")
            seen.add((pid, nid))
            gate = row[idx["gate"]]
            _need(gate == "" or gate in GATES, f"掌握状态表第 {line_no} 行门槛状态不合法")
            if "p_mastery" in idx and row[idx["p_mastery"]] != "":   # 只校验取值，不参与判定
                try:
                    p = float(row[idx["p_mastery"]])
                except ValueError:
                    p = math.nan
                _need(math.isfinite(p) and 0.0 <= p <= 1.0, f"掌握状态表第 {line_no} 行掌握概率不在 [0, 1] 内")
            pseudonyms.add(pid)
            m = _mastered(rule, gate)
            if m is not None:
                observed.setdefault(nid, {})[pid] = m
    except csv.Error:
        raise InputRefused("掌握状态表不是合法的 CSV") from None
    _need(rows > 0, "掌握状态表没有数据行")
    return Mastery(synthetic, len(pseudonyms), observed, frozenset(pseudonyms))


def load_mastery(path: str | Path, mp: MapView, rule: MasteryRule) -> Mastery:
    data = read_limited(path, MAX_MASTERY_BYTES, "掌握状态表")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputRefused("掌握状态表不是 UTF-8") from None
    return parse_mastery(text, mp, rule)
