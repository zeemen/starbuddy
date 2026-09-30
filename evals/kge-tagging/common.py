"""KGE-02 打标评测的公共部分：标注 CSV、打标报告、候选的读取与门槛（specs/kge-02-atoms-tagging/spec.md 设计要点 5）。

只用 Python 标准库。所有比例用 fractions.Fraction 精确计算，门槛判定不受浮点误差影响（例如 kappa 恰好 0.7 算达标）。
输入按不可信数据核对；holdout（留出集）模式下错误信息不带题目 ID 与节点（只给行号或字段名），避免把留出集内容带进日志。
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

# 门槛（KGE V1.1 :745–753 起点值）：kappa ≥ 0.7（两位教师主考点标注）；Top-1 ≥ 90%（金标集）；留出集无处可标率 < 2%
KAPPA_MIN = Fraction(7, 10)
TOP1_MIN = Fraction(9, 10)
UNPLACEABLE_MAX = Fraction(2, 100)  # 严格小于

# 与 db/migrations/0011 的 kge.node_id 域一致
NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")
# 学科参数化（冲刺 4-K：道法也用同一套脚本）：platform.subject → 节点 ID 前缀（与 kge.subject_prefix 一致）。
# 不给学科时不核对前缀（保持 KGE-02 数学用法不变）；给了学科，每个节点 ID 的首段都必须是该学科的前缀。
SUBJECT_PREFIX = {"aya": "MATH", "wenli": "CHN", "zhixing": "MORAL"}
# 与 platform.skill_local_id 域一致
ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
ANNOTATOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,31}$")
GOLD_COLUMNS = ("item_id", "annotator", "main_node_id", "node_ids")
LAYERS = ("rule", "vector", "model", "human")
REPORT_FORMAT = "kge-tagging-report/1"  # tools/kge-import/src/items/report-types.ts


class InputError(Exception):
    """输入不合法。redacted 是不含题目 ID 与节点的说法，留出集模式下只打印它。"""

    def __init__(self, message: str, redacted: str | None = None):
        super().__init__(message)
        self.redacted = redacted or message


@dataclass(frozen=True)
class Annotation:
    item_id: str
    annotator: str
    main_node_id: str | None  # None = 标注者认为无处可标
    node_ids: frozenset[str]  # 不含主考点；集合比较时用 node_set()

    def node_set(self) -> frozenset[str]:
        return self.node_ids | {self.main_node_id} if self.main_node_id else self.node_ids


@dataclass(frozen=True)
class Prediction:
    item_id: str
    main_node_id: str | None
    node_ids: tuple[str, ...]
    candidates: tuple[tuple[str, Fraction], ...] | None  # 报告里自带的主考点候选（node_id, 置信度），没有为 None


def check_subject(subject: str | None) -> str | None:
    if subject is not None and subject not in SUBJECT_PREFIX:
        raise InputError(f"学科只能是 {'、'.join(sorted(SUBJECT_PREFIX))}")
    return subject


def _node(v: str, where: str, subject: str | None = None) -> str:
    if not NODE_ID.match(v):
        raise InputError(f"{where}：节点 ID 不合法：{v!r}", f"{where}：节点 ID 不合法")
    if subject is not None and v.split(".")[0] != SUBJECT_PREFIX[subject]:
        raise InputError(f"{where}：节点 {v} 不是 {subject}（{SUBJECT_PREFIX[subject]}.）的节点",
                         f"{where}：节点不属于指定学科")
    return v


def read_annotations(path: str | Path, subject: str | None = None) -> dict[str, dict[str, Annotation]]:
    """读标注 CSV（格式见 README）：返回 {标注者: {题目 ID: 标注}}。给了 subject 时核对节点 ID 的学科前缀。"""
    check_subject(subject)
    out: dict[str, dict[str, Annotation]] = {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or tuple(h.strip() for h in reader.fieldnames) != GOLD_COLUMNS:
            raise InputError(f"标注文件表头应为 {','.join(GOLD_COLUMNS)}")
        for row in reader:
            line = reader.line_num
            where = f"第 {line} 行"
            if None in row or any(v is None for v in row.values()):
                raise InputError(f"{where}：列数不对")
            item = row["item_id"].strip()
            ann = row["annotator"].strip()
            if not ITEM_ID.match(item):
                raise InputError(f"{where}：题目 ID 不合法：{item!r}", f"{where}：题目 ID 不合法")
            if not ANNOTATOR.match(ann):
                raise InputError(f"{where}：标注者代号不合法：{ann!r}", f"{where}：标注者代号不合法")
            main = row["main_node_id"].strip() or None
            if main is not None:
                _node(main, where, subject)
            nodes = frozenset(_node(x.strip(), where, subject) for x in row["node_ids"].split(";") if x.strip())
            if item in out.setdefault(ann, {}):
                raise InputError(f"{where}：{ann} 对 {item} 重复标注", f"{where}：同一标注者重复标注同一题")
            out[ann][item] = Annotation(item, ann, main, nodes - ({main} if main else set()))
    return out


def _conf(v: object, where: str) -> Fraction:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
        raise InputError(f"{where}：confidence 应在 0–1", f"{where}：confidence 应在 0–1")
    return Fraction(str(v))


def _cand_list(raw: object, where: str, subject: str | None = None) -> tuple[tuple[str, Fraction], ...]:
    if not isinstance(raw, list):
        raise InputError(f"{where}：candidates 应为数组")
    out = []
    for k, c in enumerate(raw):
        w = f"{where} candidates[{k}]"
        if not isinstance(c, dict) or not isinstance(c.get("node_id"), str):
            raise InputError(f"{w}：缺 node_id")
        if c.get("role", "main") != "main":  # 只取主考点候选（迁移 0024 的 role）
            continue
        out.append((_node(c["node_id"], w, subject), _conf(c.get("confidence"), w)))
    return tuple(out)


def read_tagging_report(path: str | Path, subject: str | None = None) -> tuple[str, dict[str, Prediction]]:
    """读打标报告 tagging-report.json：返回 (地图版本号, {题目 ID: 预测})。
    格式以打标器的类型为准（tools/kge-import/src/items/report-types.ts，format = kge-tagging-report/1）；也接受没有 format 的简化格式
    （map_version 为字符串）。未导入主题的题（status = out_of_scope）主考点为空，在金标里出现时按“无处可标”计。
    给了 subject 时核对节点 ID 的学科前缀。"""
    check_subject(subject)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise InputError(f"打标报告读不了：{e}", "打标报告读不了或不是 JSON") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise InputError("打标报告应有 map_version 与 items")
    if raw.get("format") not in (None, REPORT_FORMAT):
        # format 字段的值来自输入文件，留出集模式下不回显（可能被写成任意内容）
        raise InputError(f"不认识的打标报告格式：{raw.get('format')!r}（只认 {REPORT_FORMAT}）",
                         f"不认识的打标报告格式（只认 {REPORT_FORMAT}）")
    mv = raw.get("map_version")
    if isinstance(mv, dict) and isinstance(mv.get("version"), str):  # 正式格式：{id, version, imported_chapters}
        mv = mv["version"]
    if not isinstance(mv, str) or not mv:
        raise InputError("打标报告缺 map_version")
    preds: dict[str, Prediction] = {}
    for k, it in enumerate(raw["items"]):
        where = f"items[{k}]"
        if not isinstance(it, dict) or not isinstance(it.get("item_id"), str) or not ITEM_ID.match(it["item_id"]):
            raise InputError(f"{where}：item_id 不合法")
        item = it["item_id"]
        if item in preds:
            raise InputError(f"{where}：题目 {item} 重复", f"{where}：题目重复")
        main = it.get("main_node_id")
        if main is not None:
            if not isinstance(main, str):
                raise InputError(f"{where}：main_node_id 应为字符串或 null")
            _node(main, where, subject)
        nodes = it.get("node_ids", [])
        if not isinstance(nodes, list) or not all(isinstance(x, str) for x in nodes):
            raise InputError(f"{where}：node_ids 应为字符串数组")
        cands = _cand_list(it["candidates"], where, subject) if "candidates" in it else None
        preds[item] = Prediction(item, main, tuple(_node(x, where, subject) for x in nodes), cands)
    return mv, preds


def read_candidates(path: str | Path, subject: str | None = None) -> dict[str, tuple[tuple[str, Fraction], ...]]:
    """读 kge.tag_candidate 格式的候选（迁移 0024）：CSV（表头含 item_id,node_id,confidence，可选 role、layer 等）或 JSON 数组。
    只取 role = main（没有 role 列时全部当主考点候选）。给了 subject 时核对节点 ID 的学科前缀。"""
    check_subject(subject)
    p = Path(path)
    rows: list[tuple[str, dict]] = []
    if p.suffix.lower() == ".json":
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise InputError(f"候选文件读不了：{e}", "候选文件读不了或不是 JSON") from None
        if not isinstance(raw, list):
            raise InputError("候选 JSON 应为数组")
        rows = [(f"候选[{k}]", r) for k, r in enumerate(raw)]
    else:
        with open(p, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or not {"item_id", "node_id", "confidence"} <= set(reader.fieldnames):
                raise InputError("候选 CSV 表头至少要有 item_id,node_id,confidence")
            for r in reader:
                c = r.get("confidence")
                try:
                    r = {**r, "confidence": float(c) if c not in (None, "") else None}
                except ValueError:
                    raise InputError(f"候选第 {reader.line_num} 行：confidence 不是数字") from None
                rows.append((f"候选第 {reader.line_num} 行", r))
    out: dict[str, list[tuple[str, Fraction]]] = {}
    for where, r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("item_id"), str) or not ITEM_ID.match(r["item_id"]):
            raise InputError(f"{where}：item_id 不合法")
        if not isinstance(r.get("node_id"), str):
            raise InputError(f"{where}：缺 node_id")
        if (r.get("role") or "main") != "main":
            continue
        if r.get("layer") not in (None, "", *LAYERS):
            raise InputError(f"{where}：layer 不合法")
        out.setdefault(r["item_id"], []).append((_node(r["node_id"], where, subject), _conf(r.get("confidence"), where)))
    return {k: tuple(v) for k, v in out.items()}


def rank(cands: tuple[tuple[str, Fraction], ...]) -> list[str]:
    """候选排序：同一节点取最高置信度；置信度降序，相同时按节点 ID 码点升序（确定性）。"""
    best: dict[str, Fraction] = {}
    for node, c in cands:
        if node not in best or c > best[node]:
            best[node] = c
    return [n for n, _ in sorted(best.items(), key=lambda x: (-x[1], x[0]))]


def fmt(x: Fraction | None, pct: bool = False) -> str:
    if x is None:
        return "无法计算"
    return f"{float(x) * 100:.1f}%" if pct else f"{float(x):.4f}"


def as_float(x: Fraction | None) -> float | None:
    return None if x is None else round(float(x), 6)
