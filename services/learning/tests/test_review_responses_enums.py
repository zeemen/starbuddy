"""审核台响应模型的枚举与库里约束一致（specs/review-console-responses 补记）。

响应模型 extra="forbid" 且枚举写成 Literal，运行时会核对：库里约束多了一个取值而模型没跟上，带这个取值的那一行就让整个接口 500
（复核任务的来源种类 weekly_report 就是这样漏掉的，0036 加的）。这里在测试库上读每列的 CHECK 约束或枚举类型，与模型的 Literal 比：
- equal：原样透传的列，取值集合必须相同；
- subset：查询时过滤过的列（例如结论去掉了 pending），模型的取值必须都在库里。
"""
from __future__ import annotations

import re
import typing

import pytest

from review_console import responses as R

# (schema, 表, 列, 模型, 字段, 口径)
CASES = [
    ("platform", "review_task", "source_kind", R.QueueItem, "source_kind", "equal"),
    ("platform", "review_task", "status", R.QueueItem, "status", "equal"),
    ("platform", "feedback", "status", R.FeedbackRow, "status", "equal"),
    ("kge", "map_version", "status", R.MapVersion, "status", "equal"),
    ("kge", "map_change", "status", R.ChangeRequest, "status", "equal"),
    ("kge", "content_review", "risk_level", R.Signoff, "risk_level", "equal"),
    ("kge", "content_review", "decision", R.Signoff, "decision", "subset"),   # 版本记录去掉了 pending
    ("kge", "content_review", "action", R.Signoff, "action", "equal"),
    ("kge", "tag_candidate", "role", R.TagCandidate, "role", "equal"),
    ("kge", "tag_candidate", "layer", R.TagCandidate, "layer", "equal"),
    ("kge", "tag_candidate", "grade", R.TagCandidate, "grade", "equal"),
    ("policy", "exam_scheme", "status", R.SchemeRow, "status", "equal"),
    ("policy", "source_domain", "status", R.SourceDomain, "status", "equal"),
    # 复核任务的来源快照（specs/review-task-source-models）
    ("platform", "grade_result", "verdict", R.GradeSource, "verdict", "equal"),
    ("platform", "grade_result", "verdict", R.DiagnosisStepSource, "verdict", "equal"),
    ("platform", "grade_dimension", "judged_by", R.GradeDimensionSource, "judged_by", "equal"),
    ("platform", "diagnosis_step", "kind", R.DiagnosisStepSource, "kind", "equal"),
    ("insight", "claim", "target_type", R.ClaimSource, "target_type", "equal"),
    ("insight", "claim", "status", R.ClaimSource, "status", "equal"),
    ("insight", "claim", "source_type", R.ClaimSource, "source_type", "equal"),
    ("insight", "weekly_report", "audience", R.WeeklyReportSource, "audience", "equal"),
    ("insight", "weekly_report", "status", R.WeeklyReportSource, "status", "equal"),
]


def literal_values(model: type, field: str) -> set[str]:
    ann = model.model_fields[field].annotation
    vals: set[str] = set()
    for part in typing.get_args(ann) or (ann,):
        if typing.get_origin(part) is typing.Literal:
            vals |= set(typing.get_args(part))
    if typing.get_origin(ann) is typing.Literal:
        vals |= set(typing.get_args(ann))
    assert vals, f"{model.__name__}.{field} 不是 Literal"
    return vals


async def db_values(admin, schema: str, table: str, column: str) -> set[str]:
    """列的取值：枚举类型取 pg_enum；文本列取引用这一列的 CHECK 约束里的字面量（IN (...) 归一后是 = ANY (ARRAY[...])）。"""
    row = await admin.fetchrow(
        """SELECT a.attnum, t.typtype::text AS typtype, t.oid AS typ FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_type t ON t.oid = a.atttypid
            WHERE n.nspname = $1 AND c.relname = $2 AND a.attname = $3""", schema, table, column)
    assert row, f"{schema}.{table}.{column} 不存在"
    if row["typtype"] == "e":
        return {r["enumlabel"] for r in await admin.fetch("SELECT enumlabel FROM pg_enum WHERE enumtypid = $1", row["typ"])}
    defs = [r["d"] for r in await admin.fetch(
        """SELECT pg_get_constraintdef(k.oid) AS d FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = $1 AND c.relname = $2 AND k.contype = 'c' AND k.conkey = ARRAY[$3::smallint]""",
        schema, table, row["attnum"])]
    vals: set[str] = set()
    for d in defs:
        m = re.search(r"ANY \(+ARRAY\[(.*?)\]", d)
        if m:
            vals |= set(re.findall(r"'([^']*)'", m.group(1)))
    assert vals, f"{schema}.{table}.{column} 没有单列的取值约束：{defs}"
    return vals


@pytest.mark.parametrize("schema,table,column,model,field,mode", CASES, ids=[f"{c[0]}.{c[1]}.{c[2]}-{c[3].__name__}" for c in CASES])
async def test_response_literal_matches_db_constraint(pools, schema, table, column, model, field, mode):
    _, admin = pools
    db = await db_values(admin, schema, table, column)
    lit = literal_values(model, field)
    if mode == "equal":
        assert lit == db, f"{model.__name__}.{field} 与 {schema}.{table}.{column} 不同：模型多 {sorted(lit - db)}，库里多 {sorted(db - lit)}"
    else:
        assert lit <= db, f"{model.__name__}.{field} 有库里没有的取值 {sorted(lit - db)}"
