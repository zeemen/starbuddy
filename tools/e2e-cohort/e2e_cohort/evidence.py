"""Gate 0 证据包（spec 设计要点 3）：确定性输出、只含合成数据与 ID，标注“合成联调，不是内测结论”。

目录内容：
  summary.md         说明、断言结果一览、数据文件的 SHA-256（不含提交号，保证同一输入逐字节相同）
  students.csv       合成学生：标签、student_id、年级、作答模式、选科、家庭 ID
  events.csv         每名学生 事件计数 × 科目 × 事件类型（来源方法/作答情境；复测 = grade/delayed_retest）
  nodes.csv          节点查询摘要：节点 ID、证据数、观察计数、状态（能力点视图，evidence_only）
  retests.csv        复测：由 platform.retest_task 驱动（KGE-04 第二步），每行对应一条到期的复测轮任务；合成配置下没有任务时只有表头
  checks.json        断言与计数
  run_metadata.json  提交号、契约版本、适配器与内容包版本、队列种子与摘要、星图截图记录的位置（唯一随提交变化的文件）
  starmap/           GATE0_STARMAP=1 时生成（starmap.py）：starmap.json（status = captured / failed、页面路径、选取规则、视口、
                     星数统计与对账、截取提交号、截图哈希）、截图（失败时删除）与 provenance.md（截图来历）。上面几个文件的内容与
                     是否截图无关（test_e2e_cohort 与样例逐字节比对）；截图失败时 run_metadata.json 与 summary.md 另加 failed 标记与原因。
                     不带 GATE0_STARMAP=1 时不截图、也不删已有的 starmap/，只重写 provenance.md（截取提交号与本次不同则标“截图沿用”）。
                     v0 截图只证明真实会话 + RLS 能打开该学生页、星空等于在读科目；硬1 的同一学生模型主要由 events.csv、nodes.csv、
                     checks.json 证明。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from . import SYNTHETIC_NOTICE
from .cohort import PATTERN_LABELS
from .starmap import PAGE_VERSION, WHAT_IT_PROVES

DATA_FILES = ("students.csv", "events.csv", "nodes.csv", "retests.csv", "checks.json")


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
    # 复测证据（ingest.purpose = retest → delayed_retest）由 retest_task 驱动（KGE-04 第二步），不再标“桩”
    events = sorted(({"label": label[e["student_id"]], **e} for e in report.events),
                    key=lambda r: (r["label"], r["subject"], r["event_type"]))
    nodes = sorted(({"label": label[n["student_id"]], **n} for n in report.nodes),
                   key=lambda r: (r["label"], r["subject"], r["node_id"]))
    retests = sorted(report.retests, key=lambda r: (r["label"], r["subject"], r["node_id"], r["rung"]))
    checks = {"notice": SYNTHETIC_NOTICE, "passed": report.passed, "checks": [c.as_dict() for c in report.checks]}
    files = {
        "students.csv": _csv(students, ["label", "student_id", "grade", "pattern", "pattern_zh", "enrollment", "household_id"]),
        "events.csv": _csv(events, ["label", "student_id", "subject", "event_type", "count"]),
        "nodes.csv": _csv(nodes, ["label", "student_id", "subject", "node_id", "evidence_count", "observations_demonstrated",
                                  "observations_not_demonstrated", "observations_other", "status"]),
        # 到期时间是服务器时间（每次运行不同），不进样例；轮次与原因足以说明是哪条任务
        "retests.csv": _csv(retests, ["label", "student_id", "subject", "node_id", "cognitive_level", "rung", "reason", "item",
                                      "scheduler", "status"]),
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
        "> 复测由 KGE-04 第二步的真实调度驱动（platform.retest_task；联调用合成、未签字的数学掌握配置，知行与文砺不配置）。"
        "合成配置下到不了复测时如实为 0 次，原因见 checks.json 的 retest_via_retest_task。英语未纳入（适配器搁置）。",
        "",
        f"- 合成学生：{len(report.cohort.students)} 名；三科（数学 aya、语文 wenli、道法 zhixing）都有事件的：{students_all3} 名",
        f"- 证据事件：" + "，".join(f"{k} {v}" for k, v in sorted(by_subject.items())),
        f"- 节点查询行：{len(report.nodes)}；复测（retest_task 驱动）：{len(report.retests)} 次",
        f"- 断言：{'全部通过' if report.passed else '有失败'}",
        "",
        "| 断言 | 结果 |",
        "|---|---|",
    ]
    lines += [f"| {c.name} | {c.label} |" for c in report.checks]
    lines += ["", "## 数据文件 SHA-256", ""]
    lines += [f"- `{name}` {hashlib.sha256(files[name].encode()).hexdigest()}" for name in DATA_FILES]
    lines += ["", "## 星图截图", "",
              f"- **{PAGE_VERSION}。** 页面状态只有 evidence_only / 中性底星（KGE-04 第二步前不写掌握），截图不是掌握结论。",
              f"- {WHAT_IT_PROVES}。",
              "- `make gate0-evidence GATE0_STARMAP=1` 时生成 `starmap/`：合成学生经真实会话打开壳内星图页的截图（学生视角若干张、家长视角 1 张），"
              "`starmap/starmap.json` 记录 status（captured / failed）、页面路径、学生选取规则、视口、截取提交号、截图哈希与对账"
              "（每条规则在 v0 下的效力也写在里面）；对账不一致即 failed。",
              "- 截图对应的页面版本与截取提交号见 `starmap/provenance.md`；与本证据包提交号不同时标“截图沿用”（只在星图页面改版时重截）。"]
    sm = report.metadata.get("starmap_screenshot", {})
    if sm.get("status") == "failed":
        lines += [f"- **星图截图失败**：{sm.get('reason', '原因见 starmap/starmap.json')}"]
    lines += ["", "运行元数据（提交号、契约与适配器版本、星图截图记录位置）见 `run_metadata.json`。", ""]
    return "\n".join(lines)


def write_pack(report, out_dir: Path, starmap_record: dict | None = None) -> list[Path]:
    """写证据包。starmap_record 是 starmap.capture 的结果（None = 没有要求截图：已有的 starmap/ 保留不删）：写成 starmap/starmap.json；
    status ≠ captured 时删掉本轮截图，并在 run_metadata.json 与 summary.md 标 failed 与原因（成功时这两个文件与不截图时逐字节相同）。
    有 starmap/starmap.json 时都重写 starmap/provenance.md（截取提交号与本证据包提交号不同则标“截图沿用”）。"""
    from .starmap import drop_images, write_provenance
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if starmap_record is not None:
        if starmap_record.get("status") != "captured":
            removed = drop_images(out_dir)
            if removed:
                starmap_record = {**starmap_record, "images_removed": removed}
            report.metadata["starmap_screenshot"] = {**report.metadata["starmap_screenshot"], "status": "failed",
                                                     "reason": starmap_record.get("reason", "未知")}
        p = out_dir / "starmap" / "starmap.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(starmap_record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        written.append(p)
    prov = write_provenance(out_dir, str(report.metadata.get("commit", "unknown")))
    if prov is not None:
        written.append(prov)
    for name, content in render(report).items():
        p = out_dir / name
        p.write_text(content, encoding="utf-8")
        written.append(p)
    return written
