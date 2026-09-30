"""KGE M2 报表与准入检查（冲刺 5-K，specs/kge-m2-gate-sprint5/spec.md）：数学（aya）、道法（zhixing）切片各出一页。

两件事：
1. 核心节点内容完整率（本模块计算）。核心节点 = 单独的核心节点清单（`kge-m2-core-nodes/1`：地图版本 + 节点 ID，教研签字，
   spec 待人确认 #1 的落法见 lessons）里的有效节点；地图快照保持 knowledge.atoms 的白名单格式不动；
   完整 = 节点声明的每个认知层次（`cognitive_levels`，#2）都有 ≥ 1 道已发布题目（主考点是该节点、该层次，
   `admission = admitted` 且 `tag_review_status = approved`），并且已发布页面快照里有 map_version 与本地图版本一致的页面。
   完整率 = 完整的核心节点数 / 核心节点数，门槛 ≥ 95%；并列核心节点页面发布率（门槛 100%，Gate 0）。
   没有核心节点清单（或清单为空）时报“不可计算：核心节点未标注”，不推算。缺口清单按节点 × 层次列出，只含 ID。
2. M2 汇总报表：只汇总已有脚本的报告与执行人填入的汇总数字，不重算——M1 报告（knowledge.m1）、kappa（evals/kge-tagging/kappa.py --json）、
   Top-1（top1.py --json）、道法采分点一致率（evals/daofa-grading/grading.py --json，仅道法）、影子评审比对（evals/sie-shadow
   compare-shadow.json，只取汇总数字，不取分歧清单里的学生编码）、执行人填的准入记录（`kge-m2-attestation/1`：影子评审人数与排班
   覆盖周数、留出集无处可标的汇总数字与签字、C 级约束、监护人同意与删除、安全测试，后三项引用证据文件）。

每项输出：数值、门槛、判定（达到 / 未达到 / 不可计算 / 待填）、来源文件 sha256。缺输入一律“待填”，不当作 0。
报表不下总结论（过门由人定），但列出全部未达到、不可计算与待填项。输出确定（同样输入逐字节相同），不含时间、本机路径与学生数据；
留出集本身不读（规则 14），只收执行人填入的汇总数字。

    python -m knowledge.m2 --subject aya --inputs <输入目录> --out <输出目录>
    python -m knowledge.m2 --subject zhixing --map-snapshot <快照> --items <题目清单> --page-snapshot <目录> ... --out <目录>
输入目录的约定文件名见 INPUT_FILES；单独给的参数优先于输入目录。退出码：0 生成成功；2 参数或输入错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml  # type: ignore[import-untyped]  # 本机没有 types-PyYAML 存根

from .atoms import VERB_LEVELS, Snapshot, SnapshotError, parse_snapshot
from .m1 import NOT_STORED as M1_NOT_STORED
from .m1 import REPORT_FORMAT as M1_FORMAT
from .m1 import Ratio
from .nodeid import is_node_id

REPORT_FORMAT = "kge-m2-report/1"
ITEMS_FORMAT = "kge-m2-items/1"
ATTESTATION_FORMAT = "kge-m2-attestation/1"
SHADOW_FORMAT = "sie-shadow-compare/0.1"
CORE_FORMAT = "kge-m2-core-nodes/1"
SUBJECTS = {"aya": "数学", "zhixing": "道法"}
NOT_TAGGED = "不可计算：核心节点未标注"

VERDICTS = {"met": "达到", "not_met": "未达到", "not_computable": "不可计算", "pending": "待填"}
GAP_TEXT = {"no_cognitive_levels": "未声明认知层次", "no_published_item": "该层次没有已发布题目",
            "no_page": "没有已发布页面", "page_stale": "页面地图版本落后（待重编）"}
C_LEVEL_NOTE = "学习服务侧 C 级约束已有测试；洞察服务侧约束测试待补（A3）"
HOLDOUT_NOTE = ("留出集由教研负责人在封存环境用 evals/kge-tagging/run_holdout.sh 执行（规则 14）；本报表不读留出集，"
                "只收执行人填入的汇总数字与签字")

# 输入目录里的约定文件名（按顺序找第一个存在的；同一项两个后缀都在视为输入错误）
INPUT_FILES: dict[str, tuple[str, ...]] = {
    "map_snapshot": ("map-snapshot.yaml", "map-snapshot.json"),
    "core_nodes": ("core-nodes.yaml", "core-nodes.json"),
    "items": ("items.json", "items.yaml"),
    "page_snapshot": ("page-snapshot",),
    "m1_report": ("m1-report.json",),
    "kappa_report": ("kappa-report.json",),
    "top1_report": ("top1-report.json",),
    "daofa_grading_report": ("daofa-grading-report.json",),
    "shadow_compare": ("shadow-compare.json",),
    "attestation": ("attestation.yaml", "attestation.json"),
}

ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
MAP_VERSION = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,4}$")  # 知识服务契约 MapVersion（kge.map_version.version）
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
EVIDENCE_MAX_BYTES = 10 * 1024 * 1024
EXECUTOR_ROLES = ("教研负责人",)  # 留出集执行人只写角色（规则 14），不写姓名
# M1 报告里的“不可计算”说明只认本仓 knowledge.m1 的固定文案；其他一律用固定文案，不回显输入
M1_KNOWN_NOTES = (M1_NOT_STORED, "不可计算：该课标年份没有课标条目", "不可计算：地图里没有有效的知识点（kp）节点")
ADMISSION = ("admitted", "pending_review", "source_unverified", "rejected")  # platform.admission
TAG_REVIEW = ("pending", "approved", "rejected")                            # kge.item_tag.tag_review_status


class M2InputError(ValueError):
    """输入不合格式（消息只含字段名与编号，不回显内容）"""


# ---------- 输入 ----------
@dataclass(frozen=True)
class Source:
    """一个已读入的输入：原始字节的 sha256 与解析结果（目录类输入取 manifest.json 的哈希）"""
    key: str
    sha256: str
    data: Any = field(repr=False, default=None)
    path: Path | None = field(repr=False, default=None)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse(raw: bytes, suffix: str, key: str) -> Any:
    try:
        text = raw.decode("utf-8")
        return json.loads(text) if suffix == ".json" else yaml.safe_load(text)
    except (UnicodeDecodeError, ValueError, yaml.YAMLError):
        raise M2InputError(f"{key}：无法解析（需要 UTF-8 的 JSON / YAML）") from None


def read_source(key: str, path: str | Path) -> Source:
    p = Path(path)
    if key == "page_snapshot":
        if not p.is_dir():
            raise M2InputError("page_snapshot：目录不存在")
        try:
            raw = (p / "manifest.json").read_bytes()
        except OSError:
            raise M2InputError("page_snapshot：缺 manifest.json") from None
        return Source(key, _sha(raw), None, p)
    try:
        raw = p.read_bytes()
    except OSError:
        raise M2InputError(f"{key}：文件读不了") from None
    return Source(key, _sha(raw), _parse(raw, p.suffix.lower(), key), p)


def resolve_inputs(inputs_dir: str | Path | None, explicit: Mapping[str, str | None]) -> dict[str, Source]:
    found: dict[str, Path] = {}
    if inputs_dir is not None:
        d = Path(inputs_dir)
        if not d.is_dir():
            raise M2InputError("--inputs 不是目录")
        for key, names in INPUT_FILES.items():
            hits = [d / n for n in names if (d / n).exists()]
            if len(hits) > 1:
                raise M2InputError(f"{key}：输入目录里同时有 {'、'.join(n for n in names)}，只能留一个")
            if hits:
                found[key] = hits[0]
    for key, val in explicit.items():
        if val is not None:
            found[key] = Path(val)
    return {k: read_source(k, p) for k, p in sorted(found.items())}


# ---------- 核心节点内容完整率 ----------
def parse_map(doc: Any) -> Snapshot:
    try:
        return parse_snapshot(doc)
    except SnapshotError as e:
        raise M2InputError(f"map_snapshot：地图快照不合格：{e}") from None


def parse_core_nodes(doc: Any, subject: str, snap: Snapshot) -> list[str]:
    """核心节点清单（`kge-m2-core-nodes/1`，教研签字的单独文件，spec 待人确认 #1）→ 有效的核心节点 ID（码点序）。
    地图快照保持 knowledge.atoms 的白名单格式不动；清单的地图版本必须与快照一致，列的节点必须在快照里；已撤下的节点不进分母"""
    if not isinstance(doc, dict) or doc.get("format") != CORE_FORMAT or set(doc) != {"format", "subject", "map_version", "node_ids"}:
        raise M2InputError(f"core_nodes：必须是 {CORE_FORMAT}，且只有 format、subject、map_version、node_ids")
    if doc["subject"] != subject:
        raise M2InputError("core_nodes：学科与 --subject 不一致")
    if not isinstance(doc["map_version"], str) or not MAP_VERSION.fullmatch(doc["map_version"]):
        raise M2InputError("core_nodes：map_version 不合格式（主.次.修订）")
    if doc["map_version"] != snap.map_version:
        raise M2InputError("core_nodes：地图版本与地图快照不一致")
    ids = doc["node_ids"]
    if not isinstance(ids, list) or not all(is_node_id(x) for x in ids) or len(set(ids)) != len(ids):
        raise M2InputError("core_nodes：node_ids 必须是不重复的节点 ID 数组")
    if any(snap.node(x) is None for x in ids):
        raise M2InputError("core_nodes：node_ids 里有地图快照里不存在的节点")
    return sorted(x for x in ids if (snap.node(x) or {}).get("status", "active") == "active")


def parse_items(doc: Any, subject: str, map_version: str | None) -> list[dict[str, Any]]:
    """题目清单（kge.item_tag 与 platform.item.admission 的只读导出）：只收 ID 与状态，题干、答案、作答一律拒收（规则 13）"""
    if not isinstance(doc, dict) or doc.get("format") != ITEMS_FORMAT or set(doc) != {"format", "subject", "map_version", "items"}:
        raise M2InputError(f"items：必须是 {ITEMS_FORMAT}，且只有 format、subject、map_version、items")
    if doc["subject"] != subject:
        raise M2InputError("items：学科与 --subject 不一致")
    if map_version is not None and str(doc["map_version"]) != map_version:
        raise M2InputError("items：地图版本与地图快照不一致")
    if not isinstance(doc["items"], list):
        raise M2InputError("items：items 必须是数组")
    keys = {"item_id", "main_node_id", "cognitive_level", "admission", "tag_review_status"}
    seen: set[str] = set()
    out = []
    for i, it in enumerate(doc["items"]):
        where = f"items[{i}]"
        if not isinstance(it, dict) or set(it) != keys:
            raise M2InputError(f"items：{where} 字段必须恰好是 {sorted(keys)}")
        if not isinstance(it["item_id"], str) or not ITEM_ID.fullmatch(it["item_id"]) or it["item_id"] in seen:
            raise M2InputError(f"items：{where}.item_id 不合法或重复")
        seen.add(it["item_id"])
        if not isinstance(it["main_node_id"], str):
            raise M2InputError(f"items：{where}.main_node_id 必须是字符串")
        if it["cognitive_level"] is not None and it["cognitive_level"] not in VERB_LEVELS:
            raise M2InputError(f"items：{where}.cognitive_level 不合法")
        if it["admission"] not in ADMISSION or it["tag_review_status"] not in TAG_REVIEW:
            raise M2InputError(f"items：{where} 的 admission / tag_review_status 不合法")
        out.append(dict(it))
    return out


def load_pages(src: Source) -> tuple[dict[str, str], str]:
    """已发布页面快照 → （节点 ID → 页面 map_version，提交号）；逐页核对沿用 knowledge.service.pages"""
    from .service.pages import SnapshotLoadError, load_snapshot_dir
    assert src.path is not None
    try:
        ps = load_snapshot_dir(src.path)
    except SnapshotLoadError as e:
        raise M2InputError(f"page_snapshot：页面快照不合格：{e}") from None
    return {pp.node_id: pp.map_version for pp in ps.pages.values()}, ps.commit


def core_completeness(snap: Snapshot, core: list[str] | None, items: list[dict[str, Any]] | None,
                      pages: Mapping[str, str] | None) -> dict[str, Any]:
    """core：有效的核心节点 ID（None 或空 = 未标注）。返回 {status, ...}：status = computed / not_computable / pending；
    computed 时含完整率、页面发布率与缺口清单"""
    if not core:
        return {"status": "not_computable", "note": NOT_TAGGED}
    missing = [k for k, v in (("items", items), ("page_snapshot", pages)) if v is None]
    if missing:
        return {"status": "pending", "core_nodes": len(core), "note": f"待填：缺 {'、'.join(missing)}"}
    assert items is not None and pages is not None
    published: dict[tuple[str, str], int] = {}
    unleveled = undeclared = 0
    for it in items:
        if it["admission"] != "admitted" or it["tag_review_status"] != "approved":
            continue
        if it["cognitive_level"] is None:
            unleveled += it["main_node_id"] in core
            continue
        if it["main_node_id"] in core and it["cognitive_level"] not in ((snap.node(it["main_node_id"]) or {}).get("cognitive_levels") or []):
            undeclared += 1  # 层次不在节点声明里：不补任何层次，只计数
        k = (it["main_node_id"], it["cognitive_level"])
        published[k] = published.get(k, 0) + 1
    gaps: list[dict[str, Any]] = []
    complete = page_ok = 0
    for nid in core:
        node = snap.node(nid) or {}
        levels = [lv for lv in VERB_LEVELS if lv in (node.get("cognitive_levels") or [])]  # 课标四级的固定顺序
        node_gaps: list[dict[str, Any]] = []
        if not levels:
            node_gaps.append({"node_id": nid, "level": None, "gap": "no_cognitive_levels"})
        for lv in levels:
            if published.get((nid, lv), 0) < 1:
                node_gaps.append({"node_id": nid, "level": lv, "gap": "no_published_item"})
        if nid not in pages:
            node_gaps.append({"node_id": nid, "level": None, "gap": "no_page"})
        elif pages[nid] != snap.map_version:
            node_gaps.append({"node_id": nid, "level": None, "gap": "page_stale"})
        else:
            page_ok += 1
        complete += not node_gaps
        gaps += node_gaps
    rate, prate = Ratio(complete, len(core)), Ratio(page_ok, len(core))
    not_kp = [nid for nid in core if (snap.node(nid) or {}).get("kind") != "kp"]
    return {"status": "computed", "core_nodes": len(core), "completeness": rate, "page_rate": prate,
            "gaps": gaps, "core_published_items_without_level": unleveled,
            "core_published_items_level_undeclared": undeclared, "core_nodes_not_kp": not_kp}


# ---------- 汇总项 ----------
def _item(key: str, label: str, threshold: str, verdict: str, *, value: str | None = None, detail: Any = None,
          note: str | None = None, sources: list[Source] | None = None, extra_sources: list[dict[str, str]] | None = None
          ) -> dict[str, Any]:
    srcs = [{"input": s.key, "sha256": s.sha256} for s in (sources or [])] + list(extra_sources or [])
    return {"key": key, "label": label, "threshold": threshold, "value": value, "detail": detail, "verdict": verdict,
            "verdict_label": VERDICTS[verdict], "note": note, "sources": srcs}


def _pending(key: str, label: str, threshold: str, missing: str) -> dict[str, Any]:
    return _item(key, label, threshold, "pending", note=f"待填：缺 {missing}")


def _ratio_value(r: Ratio) -> str:
    return f"{r.percent}（{r.num} / {r.den}）"


def _obj(src: Source, keys: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(src.data, dict):
        raise M2InputError(f"{src.key}：必须是对象")
    if keys is not None and not keys <= set(src.data):
        raise M2InputError(f"{src.key}：缺字段 {sorted(keys - set(src.data))}")
    return src.data


def _num(v: Any, where: str, *, allow_none: bool = False) -> float | None:
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise M2InputError(f"{where} 必须是数字")
    return float(v)


def _int(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise M2InputError(f"{where} 必须是非负整数")
    return v


def _check_passed(value: float, threshold: float, passed: Any, where: str) -> bool:
    """报表只采用来源脚本的判定（它按精确分数算）；与报告里的数值明显矛盾时按输入错误处理"""
    if not isinstance(passed, bool):
        raise M2InputError(f"{where}：passed 必须是布尔值")
    if abs(value - threshold) > 1e-6 and passed != (value > threshold):
        raise M2InputError(f"{where}：数值与判定矛盾")
    return passed


M1_ITEMS = (("curriculum_coverage", "课标承接率", "100%"), ("kp_page_publish_rate", "知识点页发布率", "100%"),
            ("lint_blocks", "体检阻断数", "0"))


def m1_items(src: Source | None, subject: str, map_version: str | None) -> list[dict[str, Any]]:
    if src is None:
        return [_pending(f"m1.{k}", label, thr, "M1 报告（m1_report）") for k, label, thr in M1_ITEMS]
    rep = _obj(src, {"format", "map", "metrics"})
    if rep["format"] != M1_FORMAT or not isinstance(rep["map"], dict) or not isinstance(rep["metrics"], dict):
        raise M2InputError(f"m1_report：必须是 {M1_FORMAT}")
    if rep["map"].get("subject") != subject:
        raise M2InputError("m1_report：学科与 --subject 不一致")
    if map_version is not None and str(rep["map"].get("map_version")) != map_version:
        raise M2InputError("m1_report：地图版本与地图快照不一致")
    out = []
    for k, label, thr in M1_ITEMS:
        m = rep["metrics"].get(k)
        if not isinstance(m, dict) or m.get("status") not in ("computed", "not_computable"):
            raise M2InputError(f"m1_report：metrics.{k} 不合格式")
        if m["status"] == "not_computable":
            note = m.get("note")
            if isinstance(note, str) and note.startswith("不可计算：没有给"):  # M1 运行时没给该项输入：待填，不是不可计算
                out.append(_item(f"m1.{k}", label, thr, "pending", note="待填：M1 报告运行时没有给该项的输入", sources=[src]))
            else:
                out.append(_item(f"m1.{k}", label, thr, "not_computable", sources=[src],
                                 note=note if note in M1_KNOWN_NOTES else "不可计算（见 M1 报告）"))
            continue
        if not isinstance(m.get("met"), bool):
            raise M2InputError(f"m1_report：metrics.{k}.met 必须是布尔值")
        detail: dict[str, Any]
        if k == "lint_blocks":
            blocks = _int(m.get("blocks"), f"m1_report：metrics.{k}.blocks")
            met, value, detail = blocks == 0, str(blocks), {"blocks": blocks}
        else:
            r = Ratio(_int(m.get("numerator"), f"m1_report：metrics.{k}.numerator"),
                      _int(m.get("denominator"), f"m1_report：metrics.{k}.denominator"))
            if r.den == 0 or r.num > r.den:
                raise M2InputError(f"m1_report：metrics.{k} 的分子分母不合理")
            met, value, detail = r.num == r.den, _ratio_value(r), r.to_dict()
            if k == "kp_page_publish_rate":
                commit = m.get("snapshot_commit")
                if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
                    raise M2InputError("m1_report：metrics.kp_page_publish_rate.snapshot_commit 不合格式")
                detail["snapshot_commit"] = commit
        if m["met"] != met:
            raise M2InputError(f"m1_report：metrics.{k}.met 与数值不一致")
        out.append(_item(f"m1.{k}", label, thr, "met" if met else "not_met", value=value, detail=detail, sources=[src]))
    return out


def completeness_items(map_src: Source | None, core_src: Source | None, items_src: Source | None, pages_src: Source | None,
                       subject: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """返回（两项汇总项，缺口清单，地图信息）"""
    k1, l1, t1 = "core.completeness", "核心节点内容完整率", "≥ 95%"
    k2, l2, t2 = "core.page_publish_rate", "核心节点页面发布率", "100%"
    if map_src is None:
        return [_pending(k1, l1, t1, "地图快照（map_snapshot）"), _pending(k2, l2, t2, "地图快照（map_snapshot）")], [], None
    snap = parse_map(map_src.data)
    if snap.subject != subject:
        raise M2InputError("map_snapshot：学科与 --subject 不一致")
    if not MAP_VERSION.fullmatch(snap.map_version):
        raise M2InputError("map_snapshot：map_version 不合格式（主.次.修订）")
    core = parse_core_nodes(core_src.data, subject, snap) if core_src is not None else None
    info = {"subject": snap.subject, "map_version": snap.map_version, "status": snap.status, "standard_year": snap.standard_year}
    items = parse_items(items_src.data, subject, snap.map_version) if items_src is not None else None
    pages, commit = load_pages(pages_src) if pages_src is not None else (None, None)
    if commit is not None:
        info["page_snapshot_commit"] = commit
    res = core_completeness(snap, core, items, pages)
    srcs = [s for s in (map_src, core_src, items_src, pages_src) if s is not None]
    if res["status"] == "not_computable":
        nsrc = [s for s in (map_src, core_src) if s is not None]
        return [_item(k1, l1, t1, "not_computable", note=res["note"], sources=nsrc),
                _item(k2, l2, t2, "not_computable", note=res["note"], sources=nsrc)], [], info
    if res["status"] == "pending":
        return [_item(k1, l1, t1, "pending", note=res["note"], sources=srcs),
                _item(k2, l2, t2, "pending", note=res["note"], sources=srcs)], [], info
    c, p = res["completeness"], res["page_rate"]
    detail = {**c.to_dict(), "core_nodes": res["core_nodes"], "gaps": len(res["gaps"]),
              "core_published_items_without_level": res["core_published_items_without_level"],
              "core_published_items_level_undeclared": res["core_published_items_level_undeclared"],
              "core_nodes_not_kp": res["core_nodes_not_kp"]}
    notes = []
    if res["core_published_items_without_level"]:
        notes.append(f"核心节点上有 {res['core_published_items_without_level']} 道已发布题目未标认知层次，不计入任何层次")
    if res["core_published_items_level_undeclared"]:
        notes.append(f"核心节点上有 {res['core_published_items_level_undeclared']} 道已发布题目的认知层次不在节点声明的层次里，不计入")
    if res["core_nodes_not_kp"]:
        notes.append(f"{len(res['core_nodes_not_kp'])} 个核心节点的类型不是 kp（如道法 viewpoint）：首期页面层只有知识点页，"
                     "这类节点的页面体检报孤立页、进不了已发布快照")
    note = "；".join(notes) or None
    return [_item(k1, l1, t1, "met" if c.num * 20 >= c.den * 19 else "not_met", value=_ratio_value(c), detail=detail,
                  note=note, sources=srcs),
            _item(k2, l2, t2, "met" if p.num == p.den else "not_met", value=_ratio_value(p), detail=p.to_dict(),
                  note=notes[-1] if res["core_nodes_not_kp"] else None,
                  sources=[s for s in (map_src, core_src, pages_src) if s is not None])], res["gaps"], info


def _subject_of(rep: Mapping[str, Any], subject: str, key: str) -> None:
    if "subject" in rep and rep["subject"] != subject:
        raise M2InputError(f"{key}：学科与 --subject 不一致")


def kappa_item(src: Source | None, subject: str) -> dict[str, Any]:
    key, label, thr = "tagging.kappa", "两名教师主考点标注 kappa", "≥ 0.7"
    if src is None:
        return _pending(key, label, thr, "kappa 报告（kappa_report，kappa.py --json）")
    rep = _obj(src, {"kappa", "items", "threshold"})
    _subject_of(rep, subject, src.key)
    th = rep["threshold"]
    if not isinstance(th, dict) or th.get("kappa_min") != 0.7:
        raise M2InputError("kappa_report：threshold.kappa_min 必须是 0.7（与门槛一致）")
    items = _int(rep["items"], "kappa_report：items")
    v = _num(rep["kappa"], "kappa_report：kappa", allow_none=True)
    if v is None:
        return _item(key, label, thr, "not_computable", note="不可计算：两人没有共同标注的题", sources=[src])
    ok = _check_passed(v, 0.7, th.get("passed"), "kappa_report")
    return _item(key, label, thr, "met" if ok else "not_met", value=f"{v:g}（共同标注 {items} 题）",
                 detail={"kappa": v, "items": items}, note=None if "subject" in rep else "报告未标学科（kappa.py 未加 --subject）",
                 sources=[src])


def top1_item(src: Source | None, subject: str, map_version: str | None) -> dict[str, Any]:
    key, label, thr = "tagging.top1", "打标主考点 Top-1（金标集）", "≥ 90%"
    if src is None:
        return _pending(key, label, thr, "Top-1 报告（top1_report，top1.py --json）")
    rep = _obj(src, {"top1", "scored", "thresholds", "map_version"})
    _subject_of(rep, subject, src.key)
    th = rep["thresholds"].get("top1") if isinstance(rep["thresholds"], dict) else None
    if not isinstance(th, dict) or th.get("rule") != ">= 0.9":
        raise M2InputError("top1_report：thresholds.top1.rule 必须是 >= 0.9（与门槛一致）")
    scored = _int(rep["scored"], "top1_report：scored")
    if not isinstance(rep["map_version"], str) or not MAP_VERSION.fullmatch(rep["map_version"]):
        raise M2InputError("top1_report：map_version 不合格式（主.次.修订）")
    if map_version is not None and rep["map_version"] != map_version:
        return _item(key, label, thr, "not_computable", sources=[src],
                     note=f"不可计算：打标报告的地图版本（{rep['map_version']}）与本报表地图版本（{map_version}）不一致")
    v = _num(rep["top1"], "top1_report：top1", allow_none=True)
    if v is None:
        return _item(key, label, thr, "not_computable", note="不可计算：金标没有带主考点的题", sources=[src])
    ok = _check_passed(v, 0.9, th.get("passed"), "top1_report")
    return _item(key, label, thr, "met" if ok else "not_met", value=f"{v:g}（计分 {scored} 题）",
                 detail={"top1": v, "scored": scored}, note=None if "subject" in rep else "报告未标学科（top1.py 未加 --subject）",
                 sources=[src])


def daofa_item(src: Source | None) -> dict[str, Any]:
    key, label, thr = "daofa.point_agreement", "道法采分点判分一致率（逐点）", "≥ 90%"
    if src is None:
        return _pending(key, label, thr, "道法一致率报告（daofa_grading_report，grading.py --json）")
    rep = _obj(src, {"label", "points", "point_agree", "threshold"})
    th = rep["threshold"]
    if not isinstance(th, dict) or th.get("point_agreement_min") != 0.9:
        raise M2InputError("daofa_grading_report：threshold.point_agreement_min 必须是 0.9（与门槛一致）")
    if rep["label"] not in ("金标", "流程样例"):
        raise M2InputError("daofa_grading_report：label 只能是 金标 / 流程样例")
    points, agree = _int(rep["points"], "daofa_grading_report：points"), _int(rep["point_agree"], "daofa_grading_report：point_agree")
    if points == 0 or agree > points:
        raise M2InputError("daofa_grading_report：points / point_agree 不合理")
    r = Ratio(agree, points)
    if rep["label"] != "金标":
        return _item(key, label, thr, "not_computable", value=_ratio_value(r), detail=r.to_dict(), sources=[src],
                     note="不可计算：流程样例（非教研金标），数字不代表判分质量")
    return _item(key, label, thr, "met" if r.num * 10 >= r.den * 9 else "not_met", value=_ratio_value(r), detail=r.to_dict(),
                 sources=[src])


def shadow_compare_item(src: Source | None) -> dict[str, Any]:
    key, label, thr = "shadow.compare_flow", "影子评审比对流程可用", "有比对报告且可比项 > 0"
    if src is None:
        return _pending(key, label, thr, "影子评审比对报告（shadow_compare，compare_shadow.py 的 compare-shadow.json）")
    rep = _obj(src, {"format", "sample", "comparable", "agree", "reviewers"})
    if rep["format"] != SHADOW_FORMAT or not isinstance(rep["sample"], bool):
        raise M2InputError(f"shadow_compare：必须是 {SHADOW_FORMAT}")
    comparable, agree = _int(rep["comparable"], "shadow_compare：comparable"), _int(rep["agree"], "shadow_compare：agree")
    reviewers = _int(rep["reviewers"], "shadow_compare：reviewers")
    if agree > comparable:
        raise M2InputError("shadow_compare：agree 大于 comparable")
    # 只取汇总数字；分歧清单里有学生编码，不进本报表
    detail: dict[str, Any] = {"comparable": comparable, "agree": agree, "reviewers": reviewers, "sample": rep["sample"]}
    value = f"可比项 {comparable}；一致 {agree}" + (f"（{Ratio(agree, comparable).percent}）" if comparable else "") + f"；评审教师 {reviewers} 人"
    if rep["sample"]:  # 与道法一致率同一口径：流程样例不作判定
        return _item(key, label, thr, "not_computable", value=value, detail=detail, sources=[src],
                     note="不可计算：流程样例（合成数据），只证明比对脚本可运行")
    return _item(key, label, thr, "met" if comparable > 0 else "not_met", value=value, detail=detail, sources=[src])


# ---------- 执行人填的准入记录 ----------
ATTEST_SECTIONS: dict[str, set[str]] = {
    "shadow_review": {"reviewers", "weeks_covered", "weeks_required"},
    "holdout": {"untaggable", "total", "executor_role", "signed"},
    "c_level": {"status", "evidence"},
    "guardian": {"deletion_e2e", "consent_flow", "evidence"},
    "security": {"pentest_round2", "evidence"},
}


def parse_attestation(src: Source, subject: str) -> dict[str, Any]:
    doc = _obj(src, {"format", "subject"})
    if doc["format"] != ATTESTATION_FORMAT:
        raise M2InputError(f"attestation：必须是 {ATTESTATION_FORMAT}")
    if doc["subject"] != subject:
        raise M2InputError("attestation：学科与 --subject 不一致")
    unknown = sorted(set(doc) - {"format", "subject"} - set(ATTEST_SECTIONS))
    if unknown:
        raise M2InputError(f"attestation：未知字段 {unknown}（只收汇总数字与状态，不收名册）")
    for sec, keys in ATTEST_SECTIONS.items():
        if sec in doc and (not isinstance(doc[sec], dict) or not set(doc[sec]) <= keys):
            raise M2InputError(f"attestation：{sec} 只能有 {sorted(keys)}")
    return doc


def _evidence(sec: Mapping[str, Any], name: str, att: Source) -> list[dict[str, str]]:
    """证据文件：相对准入记录所在目录的路径，报表只记写法与 sha256。
    每段只收白名单字符（不许绝对路径、..、隐藏文件）；任何一段是符号链接即拒；解析后必须仍在准入记录所在目录内；单个文件 ≤ 10 MB"""
    raw = sec.get("evidence", [])
    if not isinstance(raw, list) or not all(isinstance(x, str) and x for x in raw):
        raise M2InputError(f"attestation：{name}.evidence 必须是相对路径的数组")
    assert att.path is not None
    out = []
    for rel in sorted(set(raw)):
        parts = rel.split("/")
        if not all(EVIDENCE_SEGMENT.fullmatch(seg) and seg not in (".", "..") for seg in parts):
            raise M2InputError(f"attestation：{name}.evidence 只收相对路径（每段字母数字开头，不许 ..）")
        try:
            base = att.path.parent.resolve(strict=True)
            cur = att.path.parent
            for seg in parts:
                cur = cur / seg
                if cur.is_symlink():
                    raise M2InputError(f"attestation：{name}.evidence 不收符号链接")
            f = cur.resolve(strict=True)
            if not f.is_relative_to(base) or not f.is_file():
                raise M2InputError(f"attestation：{name}.evidence 必须是准入记录所在目录内的普通文件")
            if f.stat().st_size > EVIDENCE_MAX_BYTES:
                raise M2InputError(f"attestation：{name}.evidence 的文件超过 10 MB")
            data = f.read_bytes()
        except OSError:
            raise M2InputError(f"attestation：{name}.evidence 列的文件读不了或不存在") from None
        out.append({"input": f"evidence:{rel}", "sha256": _sha(data)})
    return out


def _enum(sec: Mapping[str, Any], k: str, allowed: tuple[str, ...], name: str) -> str | None:
    v = sec.get(k)
    if v is not None and v not in allowed:
        raise M2InputError(f"attestation：{name}.{k} 只能是 {' / '.join(allowed)}")
    return v


def attestation_items(src: Source | None, subject: str) -> list[dict[str, Any]]:
    specs: list[tuple[str, str, str, str, Callable[[dict[str, Any], Source], dict[str, Any]]]] = [
        ("holdout.untaggable_rate", "留出集无处可标率", "< 2%", "holdout", _holdout),
        ("shadow.staffing", "影子评审就位（人数与排班）", "≥ 2 人，排班覆盖封测全周期", "shadow_review", _staffing),
        ("c_level.no_conclusion", "C 级内容不生成结论", "约束测试通过", "c_level", _c_level),
        ("guardian.consent_deletion", "监护人同意与删除可用", "删除端到端通过且同意流程已上线", "guardian", _guardian),
        ("security.pentest", "安全测试通过", "第二轮渗透测试通过", "security", _security),
    ]
    out = []
    doc = parse_attestation(src, subject) if src is not None else None
    for key, label, thr, sec, fn in specs:
        if doc is None or sec not in doc:
            out.append(_pending(key, label, thr, f"准入记录 attestation.{sec}"))
            continue
        assert src is not None
        out.append({**fn(doc[sec], src), "key": key, "label": label, "threshold": thr})
    return out


def _holdout(sec: dict[str, Any], att: Source) -> dict[str, Any]:
    if not {"untaggable", "total"} <= set(sec):
        raise M2InputError("attestation：holdout 需要 untaggable 与 total")
    n, d = _int(sec["untaggable"], "attestation：holdout.untaggable"), _int(sec["total"], "attestation：holdout.total")
    if d == 0 or n > d:
        raise M2InputError("attestation：holdout 的 untaggable / total 不合理")
    role = sec.get("executor_role")
    if role is not None and role not in EXECUTOR_ROLES:
        raise M2InputError(f"attestation：holdout.executor_role 只能是 {' / '.join(EXECUTOR_ROLES)}（写角色，不写姓名）")
    r = Ratio(n, d)
    detail = {**r.to_dict(), "executor_role": role, "signed": sec.get("signed") is True}
    if sec.get("signed") is not True or role is None:
        return _item("", "", "", "pending", value=_ratio_value(r), detail=detail, sources=[att], note=f"待填：执行人未签字。{HOLDOUT_NOTE}")
    return _item("", "", "", "met" if r.num * 50 < r.den else "not_met", value=_ratio_value(r), detail=detail, sources=[att],
                 note=HOLDOUT_NOTE)


def _staffing(sec: dict[str, Any], att: Source) -> dict[str, Any]:
    if set(sec) != ATTEST_SECTIONS["shadow_review"]:
        raise M2InputError("attestation：shadow_review 需要 reviewers、weeks_covered、weeks_required")
    r, c, w = (_int(sec[k], f"attestation：shadow_review.{k}") for k in ("reviewers", "weeks_covered", "weeks_required"))
    if w == 0:
        raise M2InputError("attestation：shadow_review.weeks_required 必须 ≥ 1")
    ok = r >= 2 and c >= w
    return _item("", "", "", "met" if ok else "not_met", value=f"{r} 人；排班覆盖 {c} / {w} 周",
                 detail={"reviewers": r, "weeks_covered": c, "weeks_required": w}, sources=[att],
                 note="名册不入仓，只收教研负责人填的汇总数字")


def _with_evidence(verdict: str, value: str, detail: dict[str, Any], ev: list[dict[str, str]], att: Source,
                   note: str | None = None) -> dict[str, Any]:
    if verdict == "met" and not ev:
        return _item("", "", "", "pending", value=value, detail=detail, sources=[att], note="待填：缺证据文件（evidence）")
    return _item("", "", "", verdict, value=value, detail=detail, sources=[att], extra_sources=ev, note=note)


def _c_level(sec: dict[str, Any], att: Source) -> dict[str, Any]:
    st = _enum(sec, "status", ("passed", "failed"), "c_level")
    ev = _evidence(sec, "c_level", att)
    if st is None:
        return _item("", "", "", "pending", sources=[att], extra_sources=ev, note=f"待填：c_level.status 未填。{C_LEVEL_NOTE}")
    return _with_evidence("met" if st == "passed" else "not_met", "通过" if st == "passed" else "未通过", {"status": st}, ev, att,
                          C_LEVEL_NOTE)


def _guardian(sec: dict[str, Any], att: Source) -> dict[str, Any]:
    d = _enum(sec, "deletion_e2e", ("passed", "failed"), "guardian")
    c = _enum(sec, "consent_flow", ("live", "not_live"), "guardian")
    ev = _evidence(sec, "guardian", att)
    value = f"删除端到端：{ {'passed': '通过', 'failed': '未通过', None: '未填'}[d] }；同意流程：{ {'live': '已上线', 'not_live': '未上线', None: '未填'}[c] }"
    detail = {"deletion_e2e": d, "consent_flow": c}
    if d == "failed" or c == "not_live":
        return _item("", "", "", "not_met", value=value, detail=detail, sources=[att], extra_sources=ev,
                     note="SIE-03 删除端到端与 J 流同意流程任一未达到即未达到")
    if d is None or c is None:
        return _item("", "", "", "pending", value=value, detail=detail, sources=[att], extra_sources=ev, note="待填：删除或同意流程状态未填")
    return _with_evidence("met", value, detail, ev, att)


def _security(sec: dict[str, Any], att: Source) -> dict[str, Any]:
    p = _enum(sec, "pentest_round2", ("passed", "failed"), "security")
    ev = _evidence(sec, "security", att)
    if p is None:
        return _item("", "", "", "pending", sources=[att], extra_sources=ev, note="待填：第二轮渗透测试未执行或未填")
    return _with_evidence("met" if p == "passed" else "not_met", "通过" if p == "passed" else "未通过", {"pentest_round2": p}, ev, att)


# ---------- 报表 ----------
def _check_page_commits(m1: list[dict[str, Any]], comp: list[dict[str, Any]], info: Mapping[str, Any] | None) -> None:
    """M1 报告用的页面快照与本报表读的页面快照不是同一个提交：两项页面指标改报“不可计算”（数字不能并排比较）"""
    kp = next(i for i in m1 if i["key"] == "m1.kp_page_publish_rate")
    m1_commit = (kp["detail"] or {}).get("snapshot_commit") if kp["verdict"] in ("met", "not_met") else None
    commit = (info or {}).get("page_snapshot_commit")
    if m1_commit is None or commit is None or m1_commit == commit:
        return
    note = "不可计算：M1 报告的页面快照提交号与本报表读取的页面快照提交号不一致"
    for it in (kp, next(i for i in comp if i["key"] == "core.page_publish_rate")):
        it.update(verdict="not_computable", verdict_label=VERDICTS["not_computable"], note=note)


def build_report(subject: str, sources: Mapping[str, Source]) -> dict[str, Any]:
    if subject not in SUBJECTS:
        raise M2InputError("--subject 只收 aya（数学）或 zhixing（道法）")
    if subject != "zhixing" and "daofa_grading_report" in sources:
        raise M2InputError("daofa_grading_report 只用于道法（zhixing）")
    comp, gaps, info = completeness_items(sources.get("map_snapshot"), sources.get("core_nodes"), sources.get("items"),
                                          sources.get("page_snapshot"), subject)
    mv = info["map_version"] if info else None
    if info is None:  # 没有地图快照时题目清单与页面快照无从对照，但仍按格式核对（格式错一律退出 2）
        if "items" in sources:
            parse_items(sources["items"].data, subject, None)
        if "page_snapshot" in sources:
            load_pages(sources["page_snapshot"])
    m1 = m1_items(sources.get("m1_report"), subject, mv)
    _check_page_commits(m1, comp, info)
    items = [*m1, *comp, kappa_item(sources.get("kappa_report"), subject), top1_item(sources.get("top1_report"), subject, mv)]
    if subject == "zhixing":
        items.append(daofa_item(sources.get("daofa_grading_report")))
    att = attestation_items(sources.get("attestation"), subject)
    items += [att[0], att[1], shadow_compare_item(sources.get("shadow_compare")), *att[2:]]
    by = {v: [i["key"] for i in items if i["verdict"] == v] for v in ("not_met", "not_computable", "pending")}
    return {
        "format": REPORT_FORMAT,
        "note": "报表只列数值、门槛与判定，不下总结论：切片是否过门由人依据本报表判定（KGE V1.1 第九章 M2；开发计划 v2.0 Gate 0）",
        "subject": subject, "subject_label": SUBJECTS[subject],
        "map": info,
        "items": items,
        "open_items": by,
        "core_gaps": gaps,
        "inputs": [{"input": s.key, "sha256": s.sha256} for s in sorted(sources.values(), key=lambda s: s.key)],
    }


def _cell(text: Any) -> str:
    return "—" if text is None or text == "" else str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(rep: Mapping[str, Any]) -> str:
    mp = rep["map"]
    head = (f"- 学科：{rep['subject_label']}（{rep['subject']}）；" +
            (f"地图版本：{mp['map_version']}（{mp['status']}）；课标年份：{mp['standard_year']}" if mp else "地图版本：待填（没有地图快照）") +
            (f"；页面快照提交号：{mp['page_snapshot_commit']}" if mp and "page_snapshot_commit" in mp else ""))
    lines = [f"# KGE M2 报表 · {rep['subject_label']}", "", f"> {rep['note']}", "", head, "",
             "| 项目 | 门槛 | 数值 | 判定 | 说明 | 来源 |", "|---|---|---|---|---|---|"]
    for it in rep["items"]:
        srcs = "、".join(s["input"] for s in it["sources"]) or "—"
        lines.append(f"| {_cell(it['label'])} | {_cell(it['threshold'])} | {_cell(it['value'])} | {it['verdict_label']} | "
                     f"{_cell(it['note'])} | {_cell(srcs)} |")
    label = {i["key"]: i["label"] for i in rep["items"]}
    lines += ["", "## 未达到、不可计算与待填（不下总结论）", ""]
    for v in ("not_met", "not_computable", "pending"):
        keys = rep["open_items"][v]
        lines.append(f"- {VERDICTS[v]}（{len(keys)}）：" + ("、".join(label[k] for k in keys) or "无"))
    lines += ["", "## 核心节点内容缺口（只含 ID）", ""]
    if rep["core_gaps"]:
        lines += ["| 节点 | 认知层次 | 缺口 |", "|---|---|---|"]
        lines += [f"| {g['node_id']} | {g['level'] or '—'} | {GAP_TEXT[g['gap']]} |" for g in rep["core_gaps"]]
    else:
        comp = next(i for i in rep["items"] if i["key"] == "core.completeness")
        lines.append("无" if comp["verdict"] in ("met", "not_met") else f"未计算（{comp['verdict_label']}）")
    lines += ["", "## 来源文件（sha256）", "", "| 输入 | sha256 |", "|---|---|"]
    ev = sorted({(s["input"], s["sha256"]) for it in rep["items"] for s in it["sources"] if s["input"].startswith("evidence:")})
    lines += [f"| {_cell(s['input'])} | {s['sha256']} |" for s in rep["inputs"]]
    lines += [f"| {_cell(k)} | {h} |" for k, h in ev]
    lines.append("")
    return "\n".join(lines)


def write_report(rep: Mapping[str, Any], out: str | Path) -> tuple[Path, Path]:
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    name = f"m2-{rep['subject']}"
    md, js = d / f"{name}.md", d / f"{name}.json"
    md.write_text(render_markdown(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.m2", description="KGE M2 报表（核心节点内容完整率 + 两科准入汇总）")
    ap.add_argument("--subject", required=True, choices=sorted(SUBJECTS))
    ap.add_argument("--inputs", help="输入目录（约定文件名见模块说明）；缺的文件报“待填”")
    for key in INPUT_FILES:
        ap.add_argument("--" + key.replace("_", "-"), dest=key)
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args(argv)
    try:
        sources = resolve_inputs(args.inputs, {k: getattr(args, k) for k in INPUT_FILES})
        rep = build_report(args.subject, sources)
        md, js = write_report(rep, args.out)
    except M2InputError as e:
        print(f"M2 报表无法生成：{e}", file=sys.stderr)
        return 2
    except OSError:
        print("M2 报表无法生成：输出目录写不了", file=sys.stderr)
        return 2
    print(f"已生成：{md.name}、{js.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
