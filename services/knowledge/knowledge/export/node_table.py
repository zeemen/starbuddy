"""节点表导出（KGE-12 A；中台需求单 ZT-02 与附录 A.2）：中台按这张表给知识点、题目打 KGE 节点 ID，不自创节点。

    python -m knowledge.export.node_table --map-snapshot <已发布地图快照> --out <目录> [--publishers <版本编码对照表.yaml>]

输入是已发布地图版本的公共层快照（knowledge.atoms 格式，与对照评测、编译器同一来源）。输出三个文件：
- node-table.jsonl：每行一个节点；
- node-table.csv：同样的内容，教材投影一列放 JSON（便于表格软件查看）；
- node-table.manifest.json：格式、地图版本、学科、行数、两个文件的 sha256。

规则（specs/kge-12-weknora-integration/spec.md，2026-11-06 用户确认默认值）：
- 只导出已发布（status = published）的快照；只导出生效（status = active）的节点，撤下的节点不出现；
- 每个节点带 `taggable`：只有 level = node 的节点可以被打标，领域与主题只给层级；
- 教材投影只导出已核对（mapping_state = verified）的；教材原句（quote_text）不导出（只参与哈希，体检规则也不让它外流）；
- `aliases`：地图里没有别名字段，输出空数组；
- `publisher`：投影只有版本编码。给了 --publishers（版本编码 → 出版社全称，由教研提供）就填上，缺编码时报错列出；
  不给时为 null；
- 同样输入逐字节相同（按节点 ID 排序，投影按版本、年份、册、章、页码排序；不含时间与本机路径）。
公共层只读，不含任何个人数据（规则 13）。退出码：0 成功；2 参数或输入错误。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..atoms import Snapshot, SnapshotError, load_snapshot

FORMAT = "kge-node-table/1"
TAGGABLE_LEVEL = "node"
CSV_COLUMNS = ("node_id", "map_version", "subject", "name", "aliases", "parent_node_id", "level", "kind", "taggable",
               "includes", "excludes", "cognitive_levels", "textbook_projections")


class ExportError(ValueError):
    """输入不符合导出条件（消息只含字段名与 ID，不含正文）"""


def load_publishers(path: str | Path) -> dict[str, str]:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise ExportError("出版社对照表不是可读的 UTF-8 YAML") from None
    table = doc.get("publishers") if isinstance(doc, dict) else None
    if not isinstance(table, dict) or not table:
        raise ExportError("出版社对照表须有非空的 publishers 映射（版本编码 → 出版社全称）")
    out: dict[str, str] = {}
    for k, v in table.items():
        if not isinstance(k, str) or not isinstance(v, str) or not k.strip() or not v.strip():
            raise ExportError("出版社对照表的键与值都必须是非空字符串")
        out[k] = v.strip()
    return out


def _projection(rec: Mapping[str, Any], publishers: Mapping[str, str] | None) -> dict[str, Any]:
    edition = str(rec["edition"])
    return {"edition": edition, "publisher": publishers.get(edition) if publishers is not None else None,
            "edition_year": rec["edition_year"], "volume": rec["volume"], "chapter": rec["chapter"],
            "page_from": rec["page_from"], "page_to": rec["page_to"]}


def build_rows(snap: Snapshot, publishers: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    if snap.status != "published":
        raise ExportError(f"只导出已发布的地图版本（快照 status 是 {snap.status}）")
    nodes = [a for a in snap.of_type("node") if a.record.get("status", "active") == "active"]
    active = {a.id for a in nodes}
    verified = [a for a in snap.of_type("projection") if a.record.get("mapping_state") == "verified" and a.record["node_id"] in active]
    if publishers is not None:
        missing = sorted({str(a.record["edition"]) for a in verified} - set(publishers))
        if missing:
            raise ExportError(f"出版社对照表缺这些版本编码：{'、'.join(missing)}")
    proj_of: dict[str, list[dict[str, Any]]] = {}
    for a in verified:
        proj_of.setdefault(a.record["node_id"], []).append(_projection(a.record, publishers))
    rows = []
    for a in nodes:
        r = a.record
        parent = r.get("parent_id")
        projections = sorted(proj_of.get(a.id, []), key=lambda p: (p["edition"], p["edition_year"], str(p["volume"]),
                                                                    str(p["chapter"]), p["page_from"], p["page_to"]))
        rows.append({
            "node_id": a.id, "map_version": snap.map_version, "subject": snap.subject, "name": r["name"], "aliases": [],
            "parent_node_id": parent if parent in active else None, "level": r["level"], "kind": r.get("kind"),
            "taggable": r["level"] == TAGGABLE_LEVEL,
            "includes": list(r.get("includes") or []), "excludes": list(r.get("excludes") or []),
            "cognitive_levels": list(r.get("cognitive_levels") or []),
            "textbook_projections": projections,
        })
    return rows


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_jsonl(rows: list[dict[str, Any]]) -> bytes:
    return "".join(_dumps(r) + "\n" for r in rows).encode("utf-8")


def _cell(v: Any) -> str:
    """CSV 单元格：列表与对象写 JSON，布尔写 true / false，空值写空串"""
    if isinstance(v, (list, dict)):
        return _dumps(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def render_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in rows:
        w.writerow([_cell(r[c]) for c in CSV_COLUMNS])
    return buf.getvalue().encode("utf-8")


def export(snap: Snapshot, out: str | Path, publishers: Mapping[str, str] | None = None) -> dict[str, Any]:
    rows = build_rows(snap, publishers)
    jsonl, csvb = render_jsonl(rows), render_csv(rows)
    manifest = {"format": FORMAT, "map_version": snap.map_version, "subject": snap.subject, "rows": len(rows),
                "taggable_rows": sum(1 for r in rows if r["taggable"]),
                "projections": sum(len(r["textbook_projections"]) for r in rows),
                "publishers_filled": publishers is not None,
                "files": {"node-table.jsonl": hashlib.sha256(jsonl).hexdigest(), "node-table.csv": hashlib.sha256(csvb).hexdigest()}}
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    (d / "node-table.jsonl").write_bytes(jsonl)
    (d / "node-table.csv").write_bytes(csvb)
    (d / "node-table.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                                encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="节点表导出（给中台打标用，KGE-12 A）")
    ap.add_argument("--map-snapshot", required=True, help="已发布地图版本的快照（knowledge.atoms 格式）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--publishers", help="版本编码 → 出版社全称的对照表（YAML，publishers 映射；由教研提供）")
    args = ap.parse_args(argv)
    try:
        publishers = load_publishers(args.publishers) if args.publishers else None
        m = export(load_snapshot(args.map_snapshot), args.out, publishers)
    except (ExportError, SnapshotError, OSError) as e:
        print(f"节点表导出失败：{e}", file=sys.stderr)
        return 2
    print(f"已导出 {m['subject']} 地图 {m['map_version']}：{m['rows']} 个节点（可打标 {m['taggable_rows']} 个），"
          f"已核对投影 {m['projections']} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
