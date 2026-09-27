#!/usr/bin/env python3
"""SIE 画像字段字典检查与渲染（specs/sie-00-field-dictionary/spec.md 设计要点 2）。

检查（每项都有内置反例，--self-test 逐项改坏一份副本，要求被拦下）：
1. 结构与取值合法；id 唯一且形如 圈.组.名、与 circle/group 一致；九组都有字段；
2. teaching_decision 非空（只有合规用途字段可以改写 compliance_purpose）；
3. 外圈不对学生可见；家庭层 g8 与学生自述字段必须家长可删（g1 必填的基础身份除外，走整档删除）；不可删的字段要写明删除途径；
4. 中圈指标在效度检验通过前只给 agent；指标的定义、最低样本、效度检验三项齐全；
   对学生或家长可见的推断只能是首发维度（数学的知识掌握、知识迁移）；可见的事实只能由学生或家长自己填写；
5. 推断类字段落在推断层，并经判断账本 insight.claim 对外；
6. storage 指向的已有表与列在 db/migrations 里真实存在；还没建的表必须写 built_by，且状态不能是 live；
7. 字段的 id、名称、说明不命中禁用推断词表，也不复用 retired 段里的旧字段名与别名；
8. 人读版 field-dictionary.md 与 YAML 一致（--write 重新生成）。
同时跑禁用推断词表的正反例自检（sie_lexicon.py）。

用法：python docs/design/sie/check_field_dictionary.py [--write]
缺 PyYAML 时退出码 3（由 Makefile 决定是否算失败）。
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
YAML_PATH = HERE / "field-dictionary.v0.9.yaml"
MD_PATH = HERE / "field-dictionary.md"
MIGRATIONS = ROOT / "db/migrations"
BEGIN, END = "<!-- BEGIN GENERATED: dictionary -->", "<!-- END GENERATED: dictionary -->"

try:
    import yaml
except ImportError:  # pragma: no cover
    print("sie-dictionary-check: 跳过（缺少 PyYAML；pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)

sys.path.insert(0, str(HERE))
import sie_lexicon  # noqa: E402

CIRCLE_OF_GROUP = {"g1": "outer", "g8": "outer", "g2": "inner", "g3": "inner", "g4": "inner",
                   "g5": "middle", "g6": "middle", "g7": "middle", "g9": "through"}
LAYERS = {"fact", "evidence", "inference"}
SOURCES = {"objective", "self", "guardian", "inferred"}
VIS = {"agent", "student", "guardian"}
LEVELS = {"required", "suggested", "optional", "automatic"}
TIMINGS = {"onboarding", "periodic", "continuous"}
STATUSES = {"live", "silent", "planned"}
ID_RE = re.compile(r"^(outer|inner|middle|through)\.(g[1-9])\.[a-z][a-z0-9_]{1,47}$")
STORAGE_RE = re.compile(r"^(platform|insight|kge)\.([a-z_]+)\.([a-z_]+)$")
REQUIRED_KEYS = ["id", "name", "layer", "source_type", "collect", "visibility", "storage", "status", "guardian_deletable"]
LAUNCH_SUBJECTS = ["aya"]

# SIE V1.1 条目 → 字典字段（验收：覆盖第三章九组、第四章采集清单、第五章八个中圈指标、第九章 insight 模型的画像字段）。
# 值是字段 id 列表；以“不属画像字段：”开头的字符串说明为什么不进字典。
COVERAGE: dict[str, list[str] | str] = {
    "第四章 建档必填：年级": ["outer.g1.grade"],
    "第四章 建档必填：地区": ["outer.g1.region"],
    "第四章 建档必填：各科教材版本": ["outer.g1.textbook_version"],
    "第四章 建档必填：最想改善的学科": ["through.g9.focus_subject"],
    "第四章 建档必填：近期目标": ["through.g9.near_term_goal"],
    "第四章 建档必填：每周可用时间": ["outer.g1.weekly_available_time"],
    "第四章 建档必填：学习方式偏好": ["middle.g6.learning_mode_preference"],
    "第四章 建议：最近一次大考成绩与排名区间": ["inner.g2.exam_score", "inner.g2.exam_rank_band"],
    "第四章 建议：带批改的试卷": ["inner.g2.graded_paper_upload"],
    "第四章 建议：学生自评痛点": ["middle.g6.self_pain_points"],
    "第四章 可选：家长问卷（作息、陪读方式、期望与可配合程度）": [
        "outer.g8.study_routine", "outer.g8.accompany_style", "outer.g8.expectation", "outer.g8.cooperation_capacity"],
    "第四章 可选：错题本、作业、教师评语": ["inner.g2.graded_paper_upload"],
    "第四章 可选：校外补习情况": ["outer.g1.tutoring"],
    "第四章 周期：考试成绩与范围、考前冻结预测": ["inner.g2.exam_score", "inner.g2.exam_scope", "inner.g2.exam_prediction"],
    "第四章 持续：平台内作答与学习行为": ["inner.g3.evidence_events", "middle.g5.behavior_events"],
    "第四章 持续：做题前“能做对吗”": ["middle.g7.confidence_probe"],
    "第三章 ① 基础身份与学习环境（设备）": ["outer.g1.devices"],
    "第三章 ③ 掌握状态、五道门槛、错因、复习时间": [
        "inner.g3.node_mastery", "inner.g3.error_attribution", "inner.g3.next_retest_at"],
    "第三章 ④ 各科能力视图；迁移差": ["inner.g4.skill_view", "inner.g4.transfer_gap_math"],
    "第三章 ⑥ 本人说出的兴趣、主动发起学习": ["middle.g6.stated_interests", "middle.g6.self_initiated_sessions"],
    "第三章 ⑧ 家校沟通": ["outer.g8.school_communication"],
    "第三章 ⑨ 目标、阶段干预、复测结果、每周快照、点亮记录": [
        "through.g9.near_term_goal", "through.g9.interventions", "through.g9.weekly_snapshot", "through.g9.star_lit_history"],
    "第五章 指标：独立解题率": ["middle.g7.independent_solve_rate"],
    "第五章 指标：提示依赖度": ["middle.g7.hint_dependency"],
    "第五章 指标：迁移差": ["middle.g7.transfer_gap"],
    "第五章 指标：自评校准": ["middle.g7.self_assessment_calibration"],
    "第五章 指标：保持率": ["middle.g7.retention_rate"],
    "第五章 指标：计划完成率": ["middle.g5.plan_completion_rate"],
    "第五章 指标：审题类错因占比": ["middle.g7.reading_question_error_share"],
    "第五章 指标：持续性": ["middle.g5.persistence"],
    "第五章 讲法响应": ["middle.g7.teaching_response"],
    "第九章 exam_record": ["inner.g2.exam_score", "inner.g2.exam_scope", "inner.g2.exam_rank_band", "inner.g2.exam_difficulty_note"],
    "第九章 behavior_event": ["middle.g5.behavior_events"],
    "第九章 self_report": ["outer.g1.weekly_available_time", "middle.g6.stated_interests", "middle.g6.learning_mode_preference",
                          "middle.g6.self_pain_points", "through.g9.focus_subject", "through.g9.near_term_goal"],
    "第九章 guardian_report": ["outer.g1.tutoring", "outer.g8.cooperation_capacity"],
    "第九章 confidence_probe": ["middle.g7.confidence_probe"],
    "第九章 claim / claim_history": ["inner.g3.node_mastery", "through.g9.star_lit_history"],
    "第九章 learner_metric": ["middle.g7.independent_solve_rate", "middle.g5.plan_completion_rate"],
    "第九章 exam_prediction": ["inner.g2.exam_prediction"],
    "第九章 teaching_response": ["middle.g7.teaching_response"],
    "第九章 dispute": ["through.g9.disputes"],
    "第九章 intervention_log": ["through.g9.interventions"],
    "第九章 profile_snapshot": ["through.g9.weekly_snapshot"],
    "第九章 purpose_grant": "不属画像字段：用途分项授权是治理记录，等合规方案后建表（对照文档 #6）",
    "第九章 consent_record（已有 platform.consent_record）": "不属画像字段：监护人同意记录，由 0002 管理",
}


def load() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


# ---------------- 迁移里真实存在的表与列（文本解析，不连库） ----------------
def migration_columns() -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for f in sorted(MIGRATIONS.glob("*.up.sql")):
        sql = f.read_text(encoding="utf-8")
        for m in re.finditer(r"CREATE TABLE (\w+)\.(\w+) \((.*?)\n\)(?: PARTITION BY [^;]*)?;", sql, re.S):
            cols = tables.setdefault(f"{m.group(1)}.{m.group(2)}", set())
            for line in m.group(3).splitlines():
                c = re.match(r"^\s*([a-z_][a-z0-9_]*)\s+\S", line)
                if c and c.group(1).upper() not in {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT", "EXCLUDE"}:
                    cols.add(c.group(1))
        for m in re.finditer(r"ALTER TABLE (\w+)\.(\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?([a-z_][a-z0-9_]*)", sql):
            tables.setdefault(f"{m.group(1)}.{m.group(2)}", set()).add(m.group(3))
    return tables


# ---------------- 检查 ----------------
def check(data: dict, lex: sie_lexicon.Lexicon, tables: dict[str, set[str]]) -> list[str]:
    errors: list[str] = []
    fields = data.get("fields") or []
    groups = data.get("groups") or {}
    dims = data.get("dimensions") or {}
    retired = data.get("retired") or []

    # 1. 结构
    for g, spec in groups.items():
        if CIRCLE_OF_GROUP.get(g) != spec.get("circle"):
            errors.append(f"组 {g} 的圈应为 {CIRCLE_OF_GROUP.get(g)}")
    ids = [f.get("id") for f in fields]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"字段 id 重复：{dup}")
    for f in fields:
        fid = f.get("id", "?")
        for k in REQUIRED_KEYS:
            if k not in f:
                errors.append(f"{fid}：缺少键 {k}")
        m = ID_RE.match(str(fid))
        if not m:
            errors.append(f"{fid}：id 格式应为 圈.组.名")
        elif CIRCLE_OF_GROUP.get(m.group(2)) != m.group(1):
            errors.append(f"{fid}：id 的圈与组不一致（{m.group(2)} 属于 {CIRCLE_OF_GROUP.get(m.group(2))}）")
        if f.get("layer") not in LAYERS:
            errors.append(f"{fid}：layer 取值非法")
        if not f.get("source_type") or not set(f.get("source_type")) <= SOURCES:
            errors.append(f"{fid}：source_type 取值非法")
        if not f.get("visibility") or not set(f.get("visibility")) <= VIS or "agent" not in f.get("visibility"):
            errors.append(f"{fid}：visibility 必须是 agent / student / guardian 的子集且包含 agent")
        c = f.get("collect") or {}
        if c.get("level") not in LEVELS or c.get("timing") not in TIMINGS:
            errors.append(f"{fid}：collect 的 level / timing 取值非法")
        if f.get("status") not in STATUSES:
            errors.append(f"{fid}：status 取值非法")
        if f.get("dimension") is not None and f.get("dimension") not in dims:
            errors.append(f"{fid}：dimension 不在六维里")
    present = {ID_RE.match(str(i)).group(2) for i in ids if ID_RE.match(str(i))}
    for g in sorted(set(CIRCLE_OF_GROUP) - present):
        errors.append(f"组 {g} 没有任何字段（九组都要有）")

    for f in fields:
        fid = f.get("id", "?")
        vis = set(f.get("visibility") or [])
        src = set(f.get("source_type") or [])
        m = ID_RE.match(str(fid))
        group = m.group(2) if m else None
        circle = m.group(1) if m else None
        # 2. 教学决策
        if not str(f.get("teaching_decision") or "").strip() and not str(f.get("compliance_purpose") or "").strip():
            errors.append(f"{fid}：teaching_decision 为空（写不出它改变哪个教学决策的字段不准入）")
        # 3. 外圈与可删
        if circle == "outer" and "student" in vis:
            errors.append(f"{fid}：外圈字段不能对学生可见")
        basic_identity = group == "g1" and (f.get("collect") or {}).get("level") == "required"
        if (group == "g8" or ("self" in src and not basic_identity)) and f.get("guardian_deletable") is not True:
            errors.append(f"{fid}：家庭层与学生自述字段必须家长可删（g1 的必填基础身份除外，走整档删除）")
        if f.get("guardian_deletable") is False and "删除" not in str(f.get("note") or "") and not f.get("compliance_purpose"):
            errors.append(f"{fid}：不可删的字段要在 note 里写明删除途径")
        # 4. 指标与可见范围
        metric = f.get("metric")
        if circle == "middle" and f.get("layer") == "inference" and not metric and f.get("status") != "planned":
            errors.append(f"{fid}：中圈推断字段必须写 metric")
        if metric:
            for k in ("definition", "min_sample", "validity"):
                if not str(metric.get(k) or "").strip():
                    errors.append(f"{fid}：metric 缺少 {k}")
            if metric.get("validated") is not True and vis != {"agent"}:
                errors.append(f"{fid}：效度检验通过前只能对 agent 可见")
        if vis & {"student", "guardian"}:
            if f.get("layer") == "inference":
                dim = dims.get(f.get("dimension") or "", {})
                if dim.get("launch") != "visible" or f.get("launch_subjects") != LAUNCH_SUBJECTS:
                    errors.append(f"{fid}：对学生或家长可见的推断只能是首发维度（数学的知识掌握、知识迁移）")
            elif not src <= {"self", "guardian"}:
                errors.append(f"{fid}：对学生或家长可见的事实只能由学生或家长自己填写")
        # 5. 推断经判断账本
        if "inferred" in src:
            if f.get("layer") != "inference":
                errors.append(f"{fid}：推断来源的字段必须在推断层")
            if f.get("exposed_via") != "insight.claim":
                errors.append(f"{fid}：推断类字段必须经 insight.claim 对外（exposed_via）")
        # 6. 落库位置
        sm = STORAGE_RE.match(str(f.get("storage") or ""))
        if not sm:
            errors.append(f"{fid}：storage 格式应为 schema.table.column")
        else:
            table = f"{sm.group(1)}.{sm.group(2)}"
            if table in tables:
                if sm.group(3) not in tables[table]:
                    errors.append(f"{fid}：{table} 里不存在列 {sm.group(3)}")
                if f.get("built_by"):
                    errors.append(f"{fid}：{table} 已存在，不应写 built_by")
            else:
                if not f.get("built_by"):
                    errors.append(f"{fid}：{table} 还没建，必须写 built_by（由哪个任务包建）")
                if f.get("status") == "live":
                    errors.append(f"{fid}：表还没建，status 不能是 live")
        if f.get("exposed_via") and f.get("exposed_via") not in tables:
            errors.append(f"{fid}：exposed_via 指向的 {f.get('exposed_via')} 不存在")
        # 7. 禁用推断词表与旧字段
        for key in ("id", "name", "desc"):
            text = str(f.get(key) or "")
            for h in lex.find(text):
                errors.append(f"{fid}：{key} 命中禁用推断词表 {h.category}（{h.match}）")
        names = {str(f.get("id", "")).split(".")[-1].lower(), str(f.get("name", "")).lower()}
        for r in retired:
            for old in [r["name"], *r.get("aliases", [])]:
                o = str(old).lower().split(".")[-1]
                if o in names or any(o and o in n for n in names if len(o) >= 4):
                    errors.append(f"{fid}：复用了已退役的旧字段 {r['name']}（{r['reason']}）")

    # 覆盖对照表里的字段都存在
    for item, target in COVERAGE.items():
        if isinstance(target, list):
            for t in target:
                if t not in ids:
                    errors.append(f"覆盖对照“{item}”引用了不存在的字段 {t}")
    return errors


# ---------------- 人读版 ----------------
def cell(v: object) -> str:
    if isinstance(v, list):
        return "、".join(str(x) for x in v)
    return str(v).replace("|", "\\|").replace("\n", " ")


def render(data: dict) -> str:
    groups, dims = data["groups"], data["dimensions"]
    vis_name = {"agent": "Agent", "student": "学生", "guardian": "家长"}
    src_name = {"objective": "客观记录", "self": "学生自述", "guardian": "家长观察", "inferred": "模型推断"}
    out = [f"字典版本 {data['version']}（{data['status']}）；字段 {len(data['fields'])} 个。", ""]
    for g, spec in groups.items():
        rows = [f for f in data["fields"] if f["id"].split(".")[1] == g]
        out += [f"### {g} {spec['name']}（{spec['circle']}）", "",
                "| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |", "|---|---|---|---|---|---|---|---|"]
        for f in rows:
            decision = f.get("teaching_decision") or f"（合规用途）{f.get('compliance_purpose', '')}"
            storage = f["storage"] + (f"（{f['built_by']} 建）" if f.get("built_by") else "")
            out.append(f"| `{f['id']}` | {cell(f['name'])} | {f['layer']} | {cell([src_name[s] for s in f['source_type']])} | "
                       f"{cell([vis_name[v] for v in f['visibility']])} | `{cell(storage)}` | {f['status']} | {cell(decision)} |")
        out.append("")
    out += ["### 中圈指标（效度检验通过前只给 Agent）", "", "| id | 定义 | 最低样本 | 效度检验 |", "|---|---|---|---|"]
    for f in data["fields"]:
        if f.get("metric"):
            m = f["metric"]
            out.append(f"| `{f['id']}` | {cell(m['definition'])} | {cell(m['min_sample'])} | {cell(m['validity'])} |")
    out += ["", "### 六维画像（呈现视图，不存独立分数）", "", "| 维度 | 首发 | 取自字段 |", "|---|---|---|"]
    for d, spec in dims.items():
        used = [f["id"] for f in data["fields"] if f.get("dimension") == d]
        out.append(f"| {spec['name']} | {'对外展示（仅数学）' if spec['launch'] == 'visible' else '静默采集'} | {cell([f'`{u}`' for u in used]) or '—'} |")
    out += ["", "### 不再采集的旧字段（retired）", "", "| 旧字段 | 来源 | 别名 | 原因 |", "|---|---|---|---|"]
    for r in data["retired"]:
        out.append(f"| {cell(r['name'])} | {cell(r['source'])} | {cell(r.get('aliases') or '—')} | {cell(r['reason'])} |")
    out += ["", "### 旧产品对应字段（只追溯，不迁移数值）", "", "| 字段 | 旧产品对应 |", "|---|---|"]
    for f in data["fields"]:
        if f.get("legacy_sources"):
            out.append(f"| `{f['id']}` | {cell(f['legacy_sources'])} |")
    out += ["", "### 与 SIE V1.1 的覆盖对照", "", "| SIE 条目 | 字典字段 |", "|---|---|"]
    for item, target in COVERAGE.items():
        out.append(f"| {item} | {cell([f'`{t}`' for t in target]) if isinstance(target, list) else target} |")
    return "\n".join(out) + "\n"


def splice(md: str, body: str) -> str:
    head, rest = md.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{body}{END}{tail}"


def check_generated(md: str, data: dict) -> list[str]:
    if BEGIN not in md or END not in md:
        return ["field-dictionary.md 缺少生成标记"]
    return [] if splice(md, render(data)) == md else ["field-dictionary.md 与 YAML 不一致（运行 --write 重新生成）"]


# ---------------- 反例自检 ----------------
def self_test(data: dict, lex: sie_lexicon.Lexicon, tables: dict[str, set[str]], md: str) -> list[str]:
    """逐项改坏字典副本，要求 check 报出对应错误（设计要点 2：8 项检查各有反例）。"""
    def field(d: dict, fid: str) -> dict:
        return next(f for f in d["fields"] if f["id"] == fid)

    def m_dup(d): d["fields"].append(copy.deepcopy(d["fields"][0]))
    def m_circle(d): field(d, "outer.g1.devices")["id"] = "inner.g1.devices"
    def m_group(d): d["fields"] = [f for f in d["fields"] if ".g5." not in f["id"]]
    def m_decision(d): field(d, "outer.g1.devices")["teaching_decision"] = " "
    def m_outer_student(d): field(d, "outer.g8.expectation")["visibility"] = ["agent", "student", "guardian"]
    def m_not_deletable(d): field(d, "middle.g6.stated_interests")["guardian_deletable"] = False
    def m_metric_visible(d): field(d, "middle.g7.hint_dependency")["visibility"] = ["agent", "guardian"]
    def m_metric_incomplete(d): field(d, "middle.g7.retention_rate")["metric"]["min_sample"] = ""
    def m_launch(d): field(d, "inner.g3.node_mastery")["launch_subjects"] = ["aya", "wenli"]
    def m_visible_objective(d): field(d, "inner.g3.evidence_events")["visibility"] = ["agent", "student"]
    def m_inferred_layer(d): field(d, "middle.g7.hint_dependency")["layer"] = "fact"
    def m_exposed(d): field(d, "inner.g3.error_attribution").pop("exposed_via")
    def m_column(d): field(d, "outer.g1.region")["storage"] = "platform.student_profile.school_name"
    def m_built_by(d): field(d, "outer.g1.devices").pop("built_by")
    def m_live_planned(d): field(d, "outer.g1.devices")["status"] = "live"
    def m_lexicon(d): field(d, "middle.g6.stated_interests")["name"] = "学生性格与兴趣"
    def m_retired(d): field(d, "inner.g2.exam_rank_band")["id"] = "inner.g2.rank_range"

    cases = [
        ("id 重复", m_dup, "重复"), ("id 的圈与组不一致", m_circle, "圈与组不一致"),
        ("缺一组", m_group, "组 g5 没有任何字段"), ("教学决策为空", m_decision, "teaching_decision 为空"),
        ("外圈对学生可见", m_outer_student, "外圈字段不能对学生可见"), ("自述字段不可删", m_not_deletable, "必须家长可删"),
        ("未验证指标对家长可见", m_metric_visible, "效度检验通过前只能对 agent 可见"),
        ("指标缺最低样本", m_metric_incomplete, "metric 缺少 min_sample"),
        ("非首发学科可见", m_launch, "只能是首发维度"), ("客观事实对学生可见", m_visible_objective, "只能由学生或家长自己填写"),
        ("推断不在推断层", m_inferred_layer, "必须在推断层"), ("推断不经判断账本", m_exposed, "必须经 insight.claim"),
        ("落库列不存在", m_column, "不存在列 school_name"), ("未建表缺 built_by", m_built_by, "必须写 built_by"),
        ("未建表标 live", m_live_planned, "status 不能是 live"), ("命中禁用推断词表", m_lexicon, "命中禁用推断词表 personality"),
        ("复用旧字段", m_retired, "复用了已退役的旧字段 rank_range"),
    ]
    errors: list[str] = []
    for name, mutate, expected in cases:
        d = copy.deepcopy(data)
        mutate(d)
        got = check(d, lex, tables)
        if not any(expected in e for e in got):
            errors.append(f"反例“{name}”没有被拦下（期望包含“{expected}”，实际：{'；'.join(got) or '无错误'}）")
    tampered = md.replace("| `outer.g1.grade` | 年级 |", "| `outer.g1.grade` | 年级（手改） |")
    if tampered == md or not check_generated(tampered, data):
        errors.append("反例“人读版被手改”没有被拦下")
    return errors


def main(argv: list[str]) -> int:
    data = load()
    lex = sie_lexicon.load()
    tables = migration_columns()
    if "--write" in argv:
        md = MD_PATH.read_text(encoding="utf-8")
        MD_PATH.write_text(splice(md, render(data)), encoding="utf-8")
        print(f"sie-dictionary-check: 已重新生成 {MD_PATH.relative_to(ROOT)}")
    md = MD_PATH.read_text(encoding="utf-8")
    errors = sie_lexicon.self_test(lex)
    errors += check(data, lex, tables)
    errors += check_generated(md, data)
    errors += self_test(data, lex, tables, md)
    for e in errors:
        print(f"sie-dictionary-check: {e}")
    if errors:
        print(f"sie-dictionary-check: 失败（{len(errors)} 处）")
        return 1
    print(f"sie-dictionary-check: 通过（{len(data['fields'])} 个字段、{len(data['retired'])} 个退役旧字段、"
          f"词表 {len(lex.categories)} 类；17 个反例与人读版篡改全部被拦下）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
