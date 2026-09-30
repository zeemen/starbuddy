"""Gate 0 证据包（spec 设计要点 3）：确定性输出、只含合成数据与 ID，标注“合成联调，不是内测结论”。

目录内容：
  summary.md         说明、断言结果一览、数据文件的 SHA-256（不含提交号，保证同一输入逐字节相同）
  students.csv       合成学生：标签、student_id、年级、作答模式、选科、家庭 ID
  events.csv         每名学生 事件计数 × 科目 × 事件类型（来源方法/作答情境；复测 = grade/delayed_retest（桩））
  nodes.csv          节点查询摘要：节点 ID、证据数、观察计数、状态（KGE-04 第二步前为 evidence_only）
  retests.csv        复测（桩）：调度方一律 stub
  checks.json        断言与计数
  run_metadata.json  提交号、契约版本、适配器与内容包版本、队列种子与摘要、星图截图状态（唯一随提交变化的文件）
星图截图是可选步骤：依赖 4-H 壳页面合入，接口见 capture_starmap；未提供时在 run_metadata.json 里记“skipped”。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Callable

from . import SYNTHETIC_NOTICE
from .cohort import PATTERN_LABELS

DATA_FILES = ("students.csv", "events.csv", "nodes.csv", "retests.csv", "checks.json")
StarmapCapture = Callable[[Path, list[str]], list[dict]]


def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r[k] for k in columns})
    return buf.getvalue()


def render(report) -> dict[str, str]:
    """把 Report 渲染成 {文件名: 内容}；纯函数，同一 Report 输出逐字节相同。"""
    cohort = report.cohort
    label = {s.student_id: s.label for s in cohort.students}
    students = [{"label": s.label, "student_id": s.student_id, "grade": s.grade, "pattern": s.pattern,
                 "pattern_zh": PATTERN_LABELS[s.pattern], "enrollment": "+".join(s.enrollment), "household_id": s.household_id}
                for s in cohort.students]
    # 复测证据（ingest.purpose = retest → delayed_retest）由调度桩产生：事件类型加“（桩）”后缀；KGE-04 第二步合入后去掉并重生样例
    events = sorted(({"label": label[e["student_id"]], **e,
                      "event_type": e["event_type"] + ("（桩）" if e["event_type"].endswith("/delayed_retest") else "")}
                     for e in report.events),
                    key=lambda r: (r["label"], r["subject"], r["event_type"]))
    nodes = sorted(({"label": label[n["student_id"]], **n} for n in report.nodes),
                   key=lambda r: (r["label"], r["subject"], r["node_id"]))
    retests = [{**r, "scheduler": f"{r['scheduler']}（桩）"} for r in sorted(report.retests, key=lambda r: (r["label"], r["subject"]))]
    checks = {"notice": SYNTHETIC_NOTICE, "passed": report.passed, "checks": [c.as_dict() for c in report.checks]}
    files = {
        "students.csv": _csv(students, ["label", "student_id", "grade", "pattern", "pattern_zh", "enrollment", "household_id"]),
        "events.csv": _csv(events, ["label", "student_id", "subject", "event_type", "count"]),
        "nodes.csv": _csv(nodes, ["label", "student_id", "subject", "node_id", "evidence_count", "observations_demonstrated",
                                  "observations_not_demonstrated", "observations_other", "status"]),
        "retests.csv": _csv(retests, ["label", "student_id", "subject", "item", "due_at", "source_step", "scheduler", "status"]),
        "checks.json": json.dumps(checks, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
    }
    files["summary.md"] = _summary(report, files)
    files["run_metadata.json"] = json.dumps(report.metadata, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    return files


def _summary(report, files: dict[str, str]) -> str:
    by_subject: dict[str, int] = {}
    for e in report.events:
        by_subject[e["subject"]] = by_subject.get(e["subject"], 0) + e["count"]
    students_all3 = len({e["student_id"] for e in report.events if e["subject"] == "aya"}
                        & {e["student_id"] for e in report.events if e["subject"] == "wenli"}
                        & {e["student_id"] for e in report.events if e["subject"] == "zhixing"})
    lines = [
        "# Gate 0 证据包（合成）",
        "",
        f"> **{SYNTHETIC_NOTICE}。** 全部学生、家庭、作答均为固定种子生成的合成数据（仓库总则规则 8）；"
        "判分经 test 环境假供应商与各科 Rubric / 验证器得出，不代表任何真实学生或真实模型表现。",
        "> 复测调度为接口桩（KGE-04 第二步前），证据包里所有复测都标“桩”。英语未纳入（适配器搁置）。",
        "",
        f"- 合成学生：{len(report.cohort.students)} 名；三科（数学 aya、语文 wenli、道法 zhixing）都有事件的：{students_all3} 名",
        f"- 证据事件：" + "，".join(f"{k} {v}" for k, v in sorted(by_subject.items())),
        f"- 节点查询行：{len(report.nodes)}；复测（桩）：{len(report.retests)} 次",
        f"- 断言：{'全部通过' if report.passed else '有失败'}",
        "",
        "| 断言 | 结果 |",
        "|---|---|",
    ]
    lines += [f"| {c.name} | {'通过' if c.passed else '失败'} |" for c in report.checks]
    lines += ["", "## 数据文件 SHA-256", ""]
    lines += [f"- `{name}` {hashlib.sha256(files[name].encode()).hexdigest()}" for name in DATA_FILES]
    lines += ["", "运行元数据（提交号、契约与适配器版本、星图截图状态）见 `run_metadata.json`。", ""]
    return "\n".join(lines)


def write_pack(report, out_dir: Path, starmap: StarmapCapture | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if starmap is not None:
        shots = starmap(out_dir / "starmap", [s.student_id for s in report.cohort.students])
        report.metadata["starmap_screenshot"] = {"status": "captured", "files": shots}
    written = []
    for name, content in render(report).items():
        p = out_dir / name
        p.write_text(content, encoding="utf-8")
        written.append(p)
    return written


def capture_starmap_unavailable(out_dir: Path, student_ids: list[str]) -> list[dict]:
    """星图截图的接口（可选）。4-H 壳页面合入后实现：Playwright 以合成家庭登录壳内星图页，每名学生一张截图，
    返回 [{"student_id", "file", "pixel_sha256"}]（截图不确定，附像素哈希）。现在直接说明未就绪。"""
    raise NotImplementedError("星图截图依赖 4-H 壳页面合入（specs/e2e-integration-sprint5/lessons.md），本次不生成")
