"""v1.0 候选版的反例（specs/plugin-api-v1.0/spec.md 验收第 2、3 条）。

- BREAKING：每条破坏性变更一个实例，从 v0.9 的示例出发构造，必须 **v0.9 接受、v1.0 拒绝**
  （check_contract.check_breaking 把实例里的 contract_version 换成 1.0.0 后交给 v1.0 校验）。
  `formats=False` 的用例在不启用 format 校验时比较：N10 要证明的正是“不依赖 format 插件”。
- V1_CASES：v1.0 新增字段与约束的反例，从 v1.0 的示例出发，必须被 v1.0 拒绝。
全部为合成数据（CLAUDE.md 规则 8）。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from counterexamples import Case, _set


@dataclass(frozen=True)
class BreakingCase:
    name: str
    rule: str
    schema: str
    base: str
    mutate: Callable[[dict], Any]
    formats: bool = True

    def build(self, examples: dict) -> dict:
        value = copy.deepcopy(examples[self.base]["value"])
        out = self.mutate(value)
        return value if out is None else out


def to_v1(value):
    """把实例里的契约版本从 0.9.0 换成 1.0.0（其余不动）。"""
    if isinstance(value, dict):
        return {k: ("1.0.0" if k == "contract_version" and v == "0.9.0" else to_v1(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [to_v1(v) for v in value]
    return value


def _identity(v):
    return v


def _add_signal(code):
    def f(v):
        v["evidence"].append({
            "evidence_id": "00000000-0000-4000-8000-00000000e002", "kind": "interaction_signal",
            "occurred_at": "2026-10-08T10:00:00+08:00", "admission": "pending_review",
            "payload": {"signal_code": code, "value": 0.5}})
        v["evidence_ids"].append("00000000-0000-4000-8000-00000000e002")
    return f


def _forecast(v):
    v["result"]["forecast"] = {"low": 95, "high": 105, "basis_admitted_items": 12}


def _verified_claims(v):
    v["result"]["verified_claims"] = [{"claim": "（合成示例）x=1 代入成立", "verdict": "verified"}]


def _upper_point(v):
    v["result"]["scoring_points"] = [{"point_id": "P1", "achieved": True,
                                      "evidence_ids": ["00000000-0000-4000-8000-00000000e401"]}]


BREAKING = [
    BreakingCase("作答类证据没有 attempt_id", "N3：作答类证据必须指出属于哪道作答", "GradeResponse",
                 "GradeResponseWenliEssay", _identity),
    BreakingCase("作答没有 attempt_id", "N3、N4：attempt_id 由平台生成，必填", "GradeRequest",
                 "GradeRequestEchoTranscript", _identity),
    BreakingCase("数学判分引用语文题目", "N7：请求学科与 item_ref 前缀一致", "GradeRequest", "GradeRequestAyaObjective",
                 _set("attempt.item_ref", "wenli:synthetic-q05")),
    BreakingCase("诊断作答引用别科题目", "N7：请求学科与 item_ref 前缀一致", "DiagnoseRequest", "DiagnoseRequestWenliPaper",
                 lambda v: v.__setitem__("attempts", [{"attempt_id": "00000000-0000-4000-8000-0000000000a9",
                                                       "item_ref": "aya:synthetic-0301-07",
                                                       "occurred_at": "2026-10-08T10:00:00+08:00",
                                                       "response": {"text": "（合成示例）"}}])),
    BreakingCase("变式排除列表混入别科题目", "N7：请求学科与引用前缀一致", "GenerateVariationRequest",
                 "GenerateVariationRequestZhixing", _set("exclude_item_refs", ["aya:synthetic-0301-07"])),
    BreakingCase("数学讲解挂语文能力点", "N7：请求学科与 skill_node 前缀一致", "TeachRequest", "TeachRequestAya",
                 _set("skill_node", "wenli:R3-L2")),
    BreakingCase("作答时间不带时区", "N10：时间字段必须是带时区的 RFC 3339（不依赖 format 插件）", "GradeRequest",
                 "GradeRequestAyaObjective", _set("attempt.occurred_at", "2026-10-08 19:30:00"), formats=False),
    BreakingCase("报告带考试预测", "sie-#2：预测只由洞察服务提供，只给编排层", "ReportResponse",
                 "ReportResponseWenliParent", _forecast),
    BreakingCase("讲解结果用旧名 verified_claims", "sie-#4：改名 verified_statements", "TeachResponse",
                 "TeachResponseAya", _verified_claims),
    BreakingCase("采分点编号用大写", "svc-#7：point_id 只允许小写", "GradeResponse", "GradeResponseAyaObjective", _upper_point),
    BreakingCase("证据信号记录声纹", "svc-#9：不用声纹做推断（规则 12）", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
                 _add_signal("voiceprint_match")),
    BreakingCase("证据信号记录面部", "svc-#9：不用面部、眼动做推断（规则 12）", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
                 _add_signal("face_attention")),
    BreakingCase("证据信号记录情绪", "svc-#9：不记录情绪状态（规则 12）", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
                 _add_signal("emotion_frustrated")),
    BreakingCase("学生 ID 用大写 UUID", "N13：student_ref 强制小写 UUID", "GradeRequest", "GradeRequestAyaObjective",
                 _set("context.student_ref", "00000000-0000-4000-8000-00000000A002"), formats=False),
]

_SUBJ = "GradeRequestAyaWithContext"
V1_CASES = [
    Case("证据等级写 D", "ADR 15：证据等级只有 A / B / C", "GradeRequest", _SUBJ, _set("evidence_level", "D")),
    Case("作答证据引用不是 UUID", "ADR 15：作答证据引用", "GradeRequest", _SUBJ, _set("answer_evidence_ref", "photo-0001")),
    Case("考试满分为 0", "pol-#1：满分取自已发布方案", "GradeRequest", _SUBJ, _set("exam_config.full_score", 0)),
    Case("考试配置缺届别", "pol-#1：配置按学生届别读取", "GradeRequest", _SUBJ,
         lambda v: (v["exam_config"].pop("cohort"), None)[1]),
    Case("采分点夹带自由文本", "svc-#5：采分点结构来自知识服务", "GradeRequest", _SUBJ,
         lambda v: v["scoring_points"][0].__setitem__("note", "（合成示例）")),
    Case("作答同时给两种形态", "作答形态多选一", "GradeRequest", _SUBJ,
         lambda v: v["attempt"]["response"].__setitem__("text", "2(x-1)")),
    Case("作图语义操作名含空格", "ADR 15：作图语义是结构化的", "GradeRequest", _SUBJ,
         _set("attempt.response", {"semantic": [{"op": "connect A", "args": ["A", "D"]}]})),
    Case("诊断的画像上下文夹带自由文本", "svc-#1：上下文条目来自洞察服务", "DiagnoseRequest", "DiagnoseRequestWenliPaper",
         _set("learner_context", [{"field_id": "inner.g1.goal", "source_type": "student_self", "value": "x", "note": "y"}])),
    Case("时间戳带非法时区", "N10", "GradeRequest", "GradeRequestAyaObjective", _set("attempt.occurred_at", "2026-10-08T19:30:00+8")),
    Case("未认证错误码拼错", "N1：错误码是枚举", "Problem", "GradeRequestAyaObjective",
         lambda v: {"type": "about:blank", "title": "x", "status": 401, "code": "UNAUTHORIZED", "retryable": False}),
]
