"""Gate 1 证据包（spec 范围 2）：确定性渲染，只含合成数据与合成 ID，标“合成，不是试点结论”。

目录内容：
  students.csv       合成学生：标签、合成 ID、分组键（wave、grade）、满分、会话数、练习作答数、活跃周数
  retests.csv        每名学生首轮与 8 周后复测的已判分题数与做对数（G1-2）
  exams.csv          每名学生 × 学科 × 轮次：80% 区间、中值、实得、区间位置（reconcile_plan 口径，G1-3）
  metrics.csv        中圈指标值与结局的配对（G1-4；指标值由洞察服务 metrics.py 纯函数计算）
  fairness.csv       分组误差（附加项；只用合成的非敏感分组键）
  checks.json        六条 Gate 1 条件与附加项：报表结果（对照未签字阈值）与 Gate 1 判定（一律“无法自动判断（需人）”）
  summary.md         说明、结果一览、数据文件 SHA-256（不含提交号，同一输入逐字节相同）
  run_metadata.json  提交号与工作区状态（唯一随提交变化的字段）、种子、队列摘要、配置指纹、洞察服务算法版本
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from insight import metrics as insight_metrics
from insight import predict as insight_predict
from insight.policy import Policy, synthetic_policy
from insight.predict import reconcile_plan
from insight.sie03_policy import Sie03Policy, synthetic_sie03

from . import GENERATOR, SYNTHETIC_NOTICE
from .cohort import SEED, Cohort, generate
from .config import Config, load_config
from .reports import (BELOW, INSUFFICIENT, MEETS, exam_report, fairness_report, group_values, retest_report, student_pairs,
                      validity_pairs, validity_report)

DATA_FILES = ("students.csv", "retests.csv", "exams.csv", "metrics.csv", "fairness.csv", "checks.json")
VOLATILE_METADATA = ("commit", "worktree_dirty")
NEEDS_HUMAN = "无法自动判断（需人）"
SYNTHETIC_REASON = "合成数据，不是试点结论；方法与阈值未签字（tools/gate1-evidence/config.unsigned.json）"
RESULT_ZH = {INSUFFICIENT: "样本不足（不出结论）", MEETS: "达到未签字阈值", BELOW: "未达到未签字阈值"}

REFERENCE_ITEMS = {
    "G1-1": ("各科批改与教师一致率达标，发布阻断指标无超阈值", [
        "docs/plan/完整方案-v1.1.html:643（可信度发布阻断六项：批改与教师一致率、验证器拦截率、不确定度标注率、代写拦截率、引用幻觉率、人设禁语触发率）",
        "evals/daofa-grading（道法采分点一致率评测，make daofa-grading-eval-test）",
        "Makefile 的 eval-grade（各科判分评测入口，目前打印“evals 尚未建立，跳过”）",
        "make eval-persona（人设回归，禁语触发）",
        "教师一致率需要真实教师标注，本管线不生成"]),
    "G1-5": ("四科通过分科验收（完整方案 5.9），含知识切片与画像两项", [
        "docs/plan/完整方案-v1.1.html:498（5.9 分科验收）",
        "docs/gates/gate-0-evidence.md（H4 知识切片、H5 画像底线的既有判定）"]),
    "G1-6": ("合规路径明确", [
        "docs/plan/完整方案-v1.1.html:623（未成年人、安全与上线前决策）",
        "docs/compliance/vendors.md",
        "docs/gates/human-checklist.md"]),
}
CONDITIONS = {
    "G1-2": "试点用户 8 周延迟复测改善，且续用",
    "G1-3": "至少两轮学校考试对账，预测区间覆盖率接近名义值（80% 区间落在 70%–90%），误差较首轮下降",
    "G1-4": "至少两个中圈指标通过效度检验并上线（“上线”不在本管线范围：可见范围与 validated 状态由人决定）",
    "G1-附加": "画像误差的群体公平性检查（分组误差差异；只用合成的非敏感分组键）",
}
ORDER = ("G1-1", "G1-2", "G1-3", "G1-4", "G1-5", "G1-6", "G1-附加")
NOT_COUNTED = "观察用，不计入"
# SIE 分册（docs/plan/SIE-学生洞察引擎-V1.1.html:404-412）效度检验一栏原文，及本管线的结局是否为替代口径
VALIDITY_ORIGINAL = {
    "independent_solve_rate": ("与延迟复测结果相关", False),
    "hint_dependency": ("下降是否伴随独立解题率上升；这里用与 8 周后复测正确率的负相关代替", True),
    "transfer_gap": ("预测综合题得分；这里用最后一轮考试得分率代替", True),
    "retention_rate": ("预测学校考试同节点得分；这里用最后一轮考试整卷得分率代替", True),
    "self_assessment_calibration": ("与考试预测偏差方向一致性；这里用 Brier 分数与最后一轮考试得分率的负相关代替", True),
    "plan_completion_rate": ("与后续进步相关（观察用）；这里用 8 周后复测正确率代替“后续进步”", True),
    "reading_question_error_share": ("针对训练后是否下降；合成队列没有针对训练，这里用与最后一轮考试得分率的负相关代替", True),
    "persistence": ("观察用，不参与评价；这里只用周活跃天数（不含中断后恢复率）对 8 周后复测正确率", True),
}
GENERATOR_CAVEATS = (
    "合成世界的相关由生成器预设，不得当作效度证据：",
    "- 审题类错因占比随学生基础 θ 变化（基础较弱审题类略多），计划完成倾向随每周增量 gain 变化——这两项的相关是生成器写进去的；",
    "- 其余指标与结局同出自生成器的潜在能力（θ、gain、迁移缺口），相关大小只反映生成器设定，只用于检验管线能否算出、能否发现方向错误；",
    "- persistence 只用周活跃天数（metrics.py 结果的 v），不含中断后恢复率；会话时段是合成占位，不做时段分析。",
)


@dataclass(frozen=True)
class Pack:
    cohort: Cohort
    config: Config
    policy: Policy
    sie03: Sie03Policy
    g12: dict[str, Any]
    g13: dict[str, Any]
    g14: dict[str, Any]
    fairness: dict[str, Any]
    metric_rows: list[dict[str, Any]]


def build(seed: int = SEED, config: Config | None = None) -> Pack:
    """生成合成队列并跑四项报表（纯计算，不连库）。预测参数与指标最低样本取洞察服务的合成、未签字配置"""
    cfg = config or load_config()
    policy, sie = synthetic_policy(), synthetic_sie03()
    cohort = generate(sie.exam.interval, sie.exam.scales, sie.config_version, seed)
    rows = validity_pairs(cohort, policy, cfg.validity)
    return Pack(cohort, cfg, policy, sie,
                retest_report(cohort.students, cohort.retests, cohort.sessions, cohort.horizon_end, cfg.retest),
                exam_report(cohort.exams, sie.exam.interval.z, cfg.exam),
                validity_report(student_pairs(rows), cfg.validity),
                fairness_report(cohort.exams, group_values(cohort.students), cfg.fairness), rows)


def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r[k] for k in columns})
    return buf.getvalue()


def _students(c: Cohort) -> list[dict]:
    out = []
    for s in c.students:
        times = [x.occurred_at for x in c.sessions if x.student_id == s.student_id]
        weeks = {(t - c.base).days // 7 + 1 for t in times}
        out.append({"label": s.label, "student_id": s.student_id, "wave": s.wave, "grade": s.grade, "full_score": s.full_score,
                    "enrolled_day": (s.enrolled_at - c.base).days, "sessions": len(times), "practice_attempts": sum(1 for a in c.attempts if a.student_id == s.student_id
                                                                     and a.retest_round is None),
                    "active_weeks": len(weeks)})
    return out


def _retests(c: Cohort) -> list[dict]:
    out = []
    for s in c.students:
        row: dict[str, Any] = {"label": s.label, "student_id": s.student_id}
        for rnd in ("first", "week8"):
            xs = [a.fact for a in c.retests if a.student_id == s.student_id and a.retest_round == rnd
                  and a.fact.verdict in ("correct", "incorrect")]
            row[f"{rnd}_graded"] = len(xs)
            row[f"{rnd}_correct"] = sum(1 for a in xs if a.verdict == "correct")
        out.append(row)
    return out


def _exams(c: Cohort, z: float) -> list[dict]:
    label = {s.student_id: s.label for s in c.students}
    rows = [{"label": label[e.student_id], "student_id": e.student_id, "subject": e.subject, "round": e.round,
             "exam_type": e.exam_type, "week": e.week, "full_score": e.full_score, "low": e.low, "high": e.high,
             "median": e.median, "actual": e.actual, "position": reconcile_plan(e.actual, e.low, e.high, e.basis, z).position}
            for e in c.exams]
    return sorted(rows, key=lambda r: (r["label"], r["subject"], r["round"]))


def checks(p: Pack) -> dict[str, Any]:
    reports = {"G1-2": p.g12, "G1-3": p.g13, "G1-4": p.g14,
               "G1-附加": {k: v for k, v in p.fairness.items() if k != "rows"}}
    items = []
    for key in ORDER:
        if key in REFERENCE_ITEMS:
            cond, refs = REFERENCE_ITEMS[key]
            items.append({"id": key, "condition": cond, "gate_judgement": NEEDS_HUMAN,
                          "reason": "本管线不计算，只引用既有评测或文档位置", "references": refs})
        else:
            items.append({"id": key, "condition": CONDITIONS[key], "gate_judgement": NEEDS_HUMAN, "reason": SYNTHETIC_REASON,
                          "report": reports[key], "result": reports[key]["result"]})
    return {
        "notice": SYNTHETIC_NOTICE,
        "generator": GENERATOR,
        "config": p.config.provenance(),
        "inputs": {
            "predict_algorithm": insight_predict.MODEL_VERSION,
            "reconcile_algorithm": insight_predict.RECONCILE_MODEL_VERSION,
            "metrics_algorithm": insight_metrics.MODEL_VERSION,
            "claims_policy": {"config_version": p.policy.config_version, "fingerprint": p.policy.fingerprint,
                              "synthetic": p.policy.synthetic, "signed": p.policy.signed},
            "sie03_policy": {"config_version": p.sie03.config_version, "fingerprint": p.sie03.fingerprint,
                             "synthetic": p.sie03.synthetic, "signed": p.sie03.signed},
        },
        "items": items,
    }


def render(p: Pack, metadata: dict[str, Any]) -> dict[str, str]:
    """{文件名: 内容}；纯函数，同一输入逐字节相同。metadata 只进 run_metadata.json"""
    files = {
        "students.csv": _csv(_students(p.cohort), ["label", "student_id", "wave", "grade", "full_score", "enrolled_day",
                                                   "sessions", "practice_attempts", "active_weeks"]),
        "retests.csv": _csv(_retests(p.cohort), ["label", "student_id", "first_graded", "first_correct", "week8_graded",
                                                 "week8_correct"]),
        "exams.csv": _csv(_exams(p.cohort, p.sie03.exam.interval.z), ["label", "student_id", "subject", "round", "exam_type",
                                                                      "week", "full_score", "low", "high", "median", "actual",
                                                                      "position"]),
        "metrics.csv": _csv(p.metric_rows, ["label", "student_id", "subject", "metric", "value", "n", "outcome", "outcome_value"]),
        "fairness.csv": _csv(p.fairness["rows"], ["group_key", "group", "n", "students", "mae", "bias"]),
        "checks.json": json.dumps(checks(p), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
    }
    files["summary.md"] = _summary(p, files)
    meta = {**metadata, "notice": SYNTHETIC_NOTICE, "generator": GENERATOR,
            "cohort": {"seed": p.cohort.seed, "students": len(p.cohort.students), "weeks": p.cohort.weeks,
                       "digest": p.cohort.digest},
            "config": p.config.provenance()}
    files["run_metadata.json"] = json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    return files


def _fmt(x: Any) -> str:
    return "—" if x is None else f"{x:.4f}" if isinstance(x, float) else str(x)


def _summary(p: Pack, files: dict[str, str]) -> str:
    c = p.cohort
    lines = [
        "# Gate 1 证据包（合成）",
        "",
        f"> **{SYNTHETIC_NOTICE}。** 全部学生、作答、复测、考试均为固定种子生成的合成数据（仓库总则规则 8），只有合成 ID 与非敏感分组键；"
        "方法与阈值来自未签字配置，结果只是“对照未签字阈值”，不是 Gate 1 判定。Gate 1 是否通过由人决定。",
        "",
        f"- 合成学生：{len(c.students)} 名，{c.weeks} 周；会话 {len(c.sessions)} 次，练习作答 "
        f"{sum(1 for a in c.attempts if a.retest_round is None)} 道，复测 {len(c.retests)} 道，考试记录 {len(c.exams)} 条",
        f"- 中圈指标原料：做题前探针 {len(c.probes)} 次，计划事件 {len(c.plans)} 条（{len({p.fact.plan_task_id for p in c.plans})} 个任务，"
        f"同一任务取最后一次），错因 {len(c.errors)} 条（其中审题类 "
        f"{sum(1 for e in c.errors if e.fact.cross_subject_category == 'reading_the_question')} 条）",
        f"- 配置：`{p.config.config_version}`，指纹 `{p.config.fingerprint}`，{p.config.provenance()['signoff']}",
        f"- 预测与指标：洞察服务 `{insight_predict.MODEL_VERSION}`、`{insight_metrics.MODEL_VERSION}`（合成、未签字的洞察服务配置）",
        "",
        "| 条件 | 内容 | 报表结果（对照未签字阈值） | Gate 1 判定 |",
        "|---|---|---|---|",
    ]
    for it in checks(p)["items"]:
        res = RESULT_ZH[it["result"]] if "result" in it else "—（只引用）"
        lines.append(f"| {it['id']} | {it['condition']} | {res} | {it['gate_judgement']} |")
    imp, cont = p.g12["improvement"], p.g12["continuation"]
    lines += ["", "## G1-2 8 周延迟复测与续用", "",
              f"- 配对学生 {imp['paired_students']}（门槛 {imp['min_paired_students']}）：首轮 {_fmt(imp.get('first_accuracy'))}，"
              f"8 周后 {_fmt(imp.get('week8_accuracy'))}，均值差 {_fmt(imp.get('mean_delta'))}，改善占比 {_fmt(imp.get('share_improved'))}"
              f"（{RESULT_ZH[imp['result']]}）",
              "- 改善按幸存者口径：只有两轮都做够题的学生进入配对，中途停用的学生体现在续用率里（口径待签字）",
              f"- 续用：入组 {cont['enrolled']}，观察窗完整 {cont['evaluable']}（门槛 {cont['min_enrolled_students']}，"
              f"其中从未使用 {cont['never_used_evaluable']}；删失 {cont['censored']}），"
              f"续用率 {_fmt(cont.get('rate'))}（{RESULT_ZH[cont['result']]}）",
              "", "## G1-3 考试对账", "",
              "| 轮次 | 类型 | 记录数 | 学生数 | 覆盖率 | 区间下 / 内 / 上 | 中值 MAE（占满分） | 偏差 | 结果 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in p.g13["rounds"]:
        spread = f"{r['below']} / {r['inside']} / {r['above']}" if "inside" in r else "—"
        lines.append(f"| {r['round']} | {r['exam_type']} | {r['n']} | {r['students']} | {_fmt(r.get('coverage'))} | {spread} | "
                     f"{_fmt(r.get('mae'))} | {_fmt(r.get('bias'))} | {RESULT_ZH[r['result']]} |")
    if "mae_decreased_vs_first" in p.g13:
        lines += ["", f"- 误差较首轮下降：{'是' if p.g13['mae_decreased_vs_first'] else '否'}（区间内外按 reconcile_plan 口径）"]
    lines += ["", "## G1-4 中圈指标效度", "",
              "覆盖洞察服务 metrics.py 的全部 8 个静默指标；按学生聚合（各科取均值，一名学生一对）后求皮尔逊相关。"
              "跨科指标每名学生一个值，结局取各科均值。新增四项的结局变量与方向待签字（config.unsigned.json）；样本不足的指标不出结论。", "",
              "| 指标 | 结局 | 预期方向 | 学生数 | r | 结果 | 计入 Gate 1 |", "|---|---|---|---|---|---|---|"]
    for m in p.g14["metrics"]:
        lines.append(f"| {m['metric']} | {m['outcome']} | {m['expected_direction']} | {m['students']} | {_fmt(m.get('r'))} | "
                     f"{RESULT_ZH[m['result']]} | {'计入' if m['counts_toward_gate'] else NOT_COUNTED} |")
    lines += ["", "结局口径（逐项对照 SIE 分册效度检验一栏原文）：", ""]
    for m in p.g14["metrics"]:
        original, substitute = VALIDITY_ORIGINAL[m["metric"]]
        lines.append(f"- {m['metric']}：" + (f"结局为替代口径（字典原文：{original}）" if substitute
                                            else f"结局与字典原文一致（字典原文：{original}）"))
    lines += ["", *GENERATOR_CAVEATS, "",
              f"- 计入 Gate 1 的指标中对照未签字阈值通过：{p.g14['passing']}（需要 {p.g14['min_metrics_passing']}）；"
              f"观察用、不计入的指标中达到阈值：{p.g14['observational_meeting']}；“上线”不在本管线范围",
              "", "## 附加：分组误差差异", "", "| 分组键 | 组样本（记录 / 学生） | MAE 差距 | 偏差差距 | 结果 |", "|---|---|---|---|---|"]
    for k in p.fairness["keys"]:
        sizes = "，".join(f"{g}: {n} / {k['students'][g]}" for g, n in k["groups"].items())
        lines.append(f"| {k['group_key']} | {sizes} | {_fmt(k.get('mae_gap'))} | {_fmt(k.get('bias_gap'))} | {RESULT_ZH[k['result']]} |")
    lines += ["", "## 只引用、不计算的条件", ""]
    for key in ("G1-1", "G1-5", "G1-6"):
        cond, refs = REFERENCE_ITEMS[key]
        lines.append(f"- {key} {cond}：{NEEDS_HUMAN}。依据：" + "；".join(refs))
    lines += ["", "## 数据文件 SHA-256", ""]
    lines += [f"- `{name}` {hashlib.sha256(files[name].encode()).hexdigest()}" for name in DATA_FILES]
    lines += ["", "运行元数据（提交号、种子、队列摘要、配置指纹）见 `run_metadata.json`。", ""]
    return "\n".join(lines)


class PackRefused(Exception):
    """输出目录不合要求：不写任何文件"""


def check_out_dir(out_dir: Path, names: set[str]) -> None:
    """目标目录必须不存在、为空，或只含本工具同名的普通文件（重生仓库样例时覆盖旧版）；
    其余情况一律拒绝（与 Gate 0 产物目录规则一致：不往别人的目录里混写、不跟随符号链接）"""
    if out_dir.is_symlink():
        raise PackRefused("输出目录是符号链接：请指定新目录或空目录")
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise PackRefused("输出路径已存在且不是目录：请指定新目录或空目录")
    foreign = sorted(e.name for e in out_dir.iterdir() if e.name not in names or e.is_symlink() or not e.is_file())
    if foreign:
        raise PackRefused(f"输出目录非空且含本工具以外的内容（{len(foreign)} 项）：请指定新目录或空目录")


def write_pack(files: dict[str, str], out_dir: Path) -> list[Path]:
    check_out_dir(out_dir, set(files))
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
