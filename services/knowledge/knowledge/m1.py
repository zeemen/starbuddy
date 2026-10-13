"""KGE M1 报表（spec 设计要点 8；KGE V1.1 第九章 M1：课标承接 100%、留出集无处可标 < 2%、知识点页发布率 100%、体检零阻断）。

放在 services/knowledge 而不是 tools/kge-coverage 的理由：三项输入——地图快照的 `curriculum_links`（PR ① 的快照格式，
`knowledge.atoms`）、已发布页面快照（manifest 与逐页哈希核对，`knowledge.service.pages`）、体检 JSON（`kge-lint-report/1`，
`knowledge.lint`）——的解析与校验都在本包；kge-coverage 读的是 KGE-01 导入产物与打标报告，没有承接关系、页面与体检。

口径：
- 课标承接率 = 被至少一个有效节点承接的课标条目数 / 该地图课标年份的课标条目数。
  快照里没有 `curriculum_links` 字段（例如数据库数据源：kge.* 尚无承接表）时如实报“不可计算：承接关系未入库”，不推算；
  字段在但为空列表时照算（结果为 0%）。
- 知识点页发布率 = 在已发布页面快照（manifest）里有页面、且页面 map_version 与本地图版本一致的 kp 节点数 / 有效 kp 节点数
  （kind = kp，status = active）。map_version 落后的页面 page.get 不服务（回 404），所以不进主口径的分子；
  报表并列“含落后页”口径（分子含落后页），落后页单列“待重编”。
- 体检阻断数 = 体检 JSON 的 summary.blocks（与 issues 里 severity = block 的条数核对，不一致即输入错误）。
- 留出集无处可标率：不在这里算。来源是 KGE-02 的 `evals/kge-tagging/run_holdout.sh`，由教研负责人在封存环境执行（规则 14），
  构建方不接触留出集；报表只写明来源与执行人。
报表不下结论：M1 是否达成由人定。输出确定（同样输入逐字节相同，不含时间与本机路径）。

快照摘要（KGE-06，技术债 T33）：报告记下两份快照的摘要，供 M2 核对“M1 用的快照”与“M2 读的快照”是同一份内容——
- map.snapshot_digest：地图快照的规范化摘要（snapshot_digest：解析后的文档按键排序、无多余空白、保留中文的 JSON 的 sha256），
  YAML 与 JSON 写法、缩进与键顺序不影响摘要；M2 对它读入的地图快照用同一个函数计算；
- metrics.kp_page_publish_rate.manifest_sha256：页面快照 manifest.json 原始字节的 sha256，与 M2 在 inputs 里记的 page_snapshot
  的 sha256 同一口径（M2 对目录类输入取 manifest.json 的哈希）。

    python -m knowledge.m1 --map-snapshot <快照> [--page-snapshot <快照目录>] [--lint-report <体检 JSON>] --out <目录>
    python -m knowledge.m1 --database-url <只读连接串> --subject aya ...   # 库上数据：承接率必为“不可计算”
退出码：0 生成成功；2 参数或输入错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from .atoms import SnapshotError, parse_snapshot, read_structured
from .lint.issues import REPORT_FORMAT as LINT_FORMAT

REPORT_FORMAT = "kge-m1-report/1"
NOT_STORED = "不可计算：承接关系未入库"
HOLDOUT_SOURCE = ("KGE-02 `evals/kge-tagging/run_holdout.sh`（留出集盲测），由教研负责人在封存环境执行（CLAUDE.md 规则 14）；"
                  "构建方不接触留出集，本报表不读取、不计算，结果由执行人填入 M1 验收记录")
TARGETS = {"curriculum_coverage": "100%", "kp_page_publish_rate": "100%", "lint_blocks": "0", "holdout_untaggable_rate": "< 2%"}


class M1InputError(ValueError):
    """输入不合格式（消息不含内容，只含字段名）"""


def _canon(v: Any) -> Any:
    # YAML 会把日期解析成 date；库上数据源可能有 Decimal。其余类型一律拒绝（不让摘要依赖对象的 repr）
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v, "f")
    raise TypeError("地图快照里有不能规范化的值")


def snapshot_digest(doc: Any) -> str:
    """地图快照的规范化摘要（T33）：解析后的文档按键排序、无多余空白、保留中文序列化（不收 NaN / Infinity），取 sha256 十六进制。
    M1 报告记它，M2 对自己读入的地图快照用同一个函数算，两边相等才算同一份快照。"""
    try:
        raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_canon)
    except (TypeError, ValueError):
        raise M1InputError("地图快照无法规范化（含不能序列化的值）") from None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Ratio:
    num: int
    den: int

    @property
    def percent(self) -> str:
        # 精确分数四舍五入到 0.1%（不经浮点），便于手算核对
        v = Fraction(self.num * 1000, self.den)
        tenths = int(v) + (1 if v - int(v) >= Fraction(1, 2) else 0)
        return f"{tenths // 10}.{tenths % 10}%"

    def to_dict(self) -> dict[str, Any]:
        return {"numerator": self.num, "denominator": self.den, "percent": self.percent}


def curriculum_coverage(doc: Mapping[str, Any]) -> dict[str, Any]:
    snap = parse_snapshot(doc)
    if "curriculum_links" not in doc:
        return {"status": "not_computable", "note": NOT_STORED}
    items = sorted(a.id for a in snap.of_type("curriculum") if int(a.record["standard_year"]) == snap.standard_year)
    if not items:
        return {"status": "not_computable", "note": "不可计算：该课标年份没有课标条目"}
    covered = set()
    for a in snap.of_type("node"):
        if a.record.get("status", "active") != "active":
            continue
        for c in a.record.get("curriculum") or []:
            covered.add(f"{c['standard_year']}/{c['code']}")
    r = Ratio(sum(1 for i in items if i in covered), len(items))
    return {"status": "computed", **r.to_dict(), "met": r.num == r.den,
            "uncovered": [i for i in items if i not in covered]}


def kp_publish_rate(doc: Mapping[str, Any], page_snapshot_dir: str | Path | None) -> dict[str, Any]:
    snap = parse_snapshot(doc)
    kp = sorted(a.id for a in snap.of_type("node") if a.record.get("kind") == "kp" and a.record.get("status", "active") == "active")
    if page_snapshot_dir is None:
        return {"status": "not_computable", "note": "不可计算：没有给已发布页面快照"}
    if not kp:
        return {"status": "not_computable", "note": "不可计算：地图里没有有效的知识点（kp）节点"}
    from .service.pages import SnapshotLoadError, load_snapshot_dir, read_safe
    try:
        # manifest 只读一次：摘要与解析用同一份字节（摘要必须对应真正载入的那一份）
        manifest_raw = read_safe(Path(page_snapshot_dir) / "manifest.json", "页面快照缺少 manifest.json")
        pages = load_snapshot_dir(page_snapshot_dir, manifest_raw=manifest_raw)
    except SnapshotLoadError as e:
        raise M1InputError(f"页面快照不合格：{e}") from None
    by_node = {pp.node_id: pp for pp in pages.pages.values()}
    published = [n for n in kp if n in by_node]
    stale = [n for n in published if by_node[n].map_version != snap.map_version]
    r = Ratio(len(published) - len(stale), len(kp))
    incl = Ratio(len(published), len(kp))
    return {"status": "computed", **r.to_dict(), "met": r.num == r.den, "snapshot_commit": pages.commit,
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "including_stale": {**incl.to_dict(), "met": incl.num == incl.den},
            "missing": [n for n in kp if n not in by_node], "stale_map_version": stale,
            "pages_not_on_active_kp": sorted(n for n in by_node if n not in kp)}


def lint_blocks(report: Any | None) -> dict[str, Any]:
    if report is None:
        return {"status": "not_computable", "note": "不可计算：没有给体检 JSON"}
    if not isinstance(report, dict) or report.get("format") != LINT_FORMAT or not isinstance(report.get("summary"), dict) \
            or not isinstance(report.get("issues"), list):
        raise M1InputError(f"体检 JSON 不是 {LINT_FORMAT}")
    blocks = report["summary"].get("blocks")
    counted = sum(1 for i in report["issues"] if isinstance(i, dict) and i.get("severity") == "block")
    if not isinstance(blocks, int) or isinstance(blocks, bool) or blocks != counted:
        raise M1InputError("体检 JSON 的 summary.blocks 与 issues 里的阻断条数不一致")
    by_check: dict[str, int] = {}
    for i in report["issues"]:
        if isinstance(i, dict) and i.get("severity") == "block":
            k = str(i.get("check_type"))
            by_check[k] = by_check.get(k, 0) + 1
    return {"status": "computed", "blocks": blocks, "met": blocks == 0, "pages": report["summary"].get("pages"),
            "by_check": dict(sorted(by_check.items()))}


def build_report(doc: Mapping[str, Any], *, page_snapshot_dir: str | Path | None = None, lint_report: Any | None = None,
                 data_source: str = "file") -> dict[str, Any]:
    try:
        snap = parse_snapshot(doc)
    except SnapshotError as e:
        raise M1InputError(f"地图快照不合格：{e}") from None
    return {
        "format": REPORT_FORMAT,
        "note": "报表只列数字，不下结论：M1 是否达成由人定（KGE V1.1 第九章）",
        "map": {"subject": snap.subject, "map_version": snap.map_version, "status": snap.status,
                "standard_year": snap.standard_year, "data_source": data_source, "snapshot_digest": snapshot_digest(doc)},
        "targets": TARGETS,
        "metrics": {
            "curriculum_coverage": curriculum_coverage(doc),
            "kp_page_publish_rate": kp_publish_rate(doc, page_snapshot_dir),
            "lint_blocks": lint_blocks(lint_report),
            "holdout_untaggable_rate": {"status": "external", "note": HOLDOUT_SOURCE},
        },
    }


def _cell(m: Mapping[str, Any]) -> tuple[str, str]:
    if m["status"] == "computed":
        value = f"{m['percent']}（{m['numerator']} / {m['denominator']}）" if "percent" in m else str(m["blocks"])
        return value, "达到" if m["met"] else "未达到"
    return "—", str(m["note"])


def render_markdown(rep: Mapping[str, Any]) -> str:
    mp, ms = rep["map"], rep["metrics"]
    names = {"curriculum_coverage": "课标承接率", "kp_page_publish_rate": "知识点页发布率", "lint_blocks": "体检阻断数",
             "holdout_untaggable_rate": "留出集无处可标率"}
    lines = ["# KGE M1 报表", "", f"> {rep['note']}", "",
             f"- 学科：{mp['subject']}；地图版本：{mp['map_version']}（{mp['status']}）；课标年份：{mp['standard_year']}；数据源：{mp['data_source']}",
             f"- 地图快照规范化摘要（sha256）：{mp['snapshot_digest']}",
             "", "| 指标 | 门槛 | 数值 | 对照门槛 |", "|---|---|---|---|"]
    for k, label in names.items():
        value, verdict = _cell(ms[k])
        lines.append(f"| {label} | {rep['targets'][k]} | {value} | {verdict} |")
        if k == "kp_page_publish_rate" and ms[k]["status"] == "computed":
            inc = ms[k]["including_stale"]
            lines.append(f"| {label}（含地图版本落后的页） | {rep['targets'][k]} | {inc['percent']}（{inc['numerator']} / {inc['denominator']}） | "
                         f"{'达到' if inc['met'] else '未达到'}（仅供对照：落后页不对外服务） |")
    cc, kp, lb = ms["curriculum_coverage"], ms["kp_page_publish_rate"], ms["lint_blocks"]
    lines += ["", "## 明细", ""]
    if cc["status"] == "computed":
        lines.append(f"- 未被承接的课标条目（{len(cc['uncovered'])}）：" + ("、".join(cc["uncovered"]) or "无"))
    if kp["status"] == "computed":
        lines.append(f"- 页面快照提交号：{kp['snapshot_commit']}；manifest.json 的 sha256：{kp['manifest_sha256']}")
        lines.append(f"- 没有已发布页面的 kp 节点（{len(kp['missing'])}）：" + ("、".join(kp["missing"]) or "无"))
        lines.append(f"- 页面地图版本落后、待重编（{len(kp['stale_map_version'])}）：" + ("、".join(kp["stale_map_version"]) or "无"))
        lines.append(f"- 快照里不对应有效 kp 节点的页面（{len(kp['pages_not_on_active_kp'])}）：" + ("、".join(kp["pages_not_on_active_kp"]) or "无"))
    if lb["status"] == "computed":
        lines.append("- 体检阻断按类型：" + ("、".join(f"{k} {v}" for k, v in lb["by_check"].items()) or "无"))
    lines += ["", "## 留出集无处可标率", "", ms["holdout_untaggable_rate"]["note"], ""]
    return "\n".join(lines)


def write_report(rep: Mapping[str, Any], out: str | Path) -> tuple[Path, Path]:
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    name = f"m1-{rep['map']['subject']}-{rep['map']['map_version']}"
    md, js = d / f"{name}.md", d / f"{name}.json"
    md.write_text(render_markdown(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md, js


def _db_doc(dsn: str, subject: str) -> dict[str, Any]:
    import asyncio

    from .service.reader import KgeReader, create_reader_pool

    async def run() -> dict[str, Any]:
        pool = await create_reader_pool(dsn)
        try:
            return await KgeReader(pool).snapshot_doc(subject)
        finally:
            await pool.close()
    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.m1", description="KGE M1 报表（课标承接率、知识点页发布率、体检阻断数）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--map-snapshot", help="已发布地图版本的快照（JSON / YAML，knowledge.atoms 格式）")
    src.add_argument("--database-url", help="只读连接串（登录角色只继承 starbuddy_kge_read）；库上没有承接关系")
    ap.add_argument("--subject", help="配合 --database-url：学科编码")
    ap.add_argument("--page-snapshot", help="已发布页面快照目录（knowledge.compiler.snapshot 导出）")
    ap.add_argument("--lint-report", help="体检 JSON（python -m knowledge.lint ... --json 的输出）")
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args(argv)
    try:
        if args.database_url:
            if not args.subject:
                print("--database-url 需要 --subject", file=sys.stderr)
                return 2
            doc, source = _db_doc(args.database_url, args.subject), "database"
        else:
            doc, source = read_structured(args.map_snapshot), "file"
        lint = json.loads(Path(args.lint_report).read_text(encoding="utf-8")) if args.lint_report else None
        rep = build_report(doc, page_snapshot_dir=args.page_snapshot, lint_report=lint, data_source=source)
        md, js = write_report(rep, args.out)
    except (M1InputError, SnapshotError, OSError, ValueError) as e:
        print(f"M1 报表无法生成：{e}", file=sys.stderr)
        return 2
    print(f"已生成：{md.name}、{js.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
