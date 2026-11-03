"""v1.0 候选版的反例（specs/plugin-api-v1.0/spec.md 验收第 2、3 条）。

- BREAKING：每条破坏性变更一个实例，从 v0.9 的示例出发构造，必须 **v0.9 接受、v1.0 拒绝**
  （check_contract.check_breaking 把实例里的 contract_version 换成 1.0.0 后交给 v1.0 校验）。
  `formats=False` 的用例在不启用 format 校验时比较：N10 要证明的正是“不依赖 format 插件”。
- V1_CASES：v1.0 新增字段与约束的反例，从 v1.0 的示例出发，必须被 v1.0 拒绝。
全部为合成数据（CLAUDE.md 规则 8）。
"""
from __future__ import annotations

import copy
import hashlib
import re
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
    convert_keys: bool = True
    # 对照实例：同一个示例不做这处修改（必要时先补齐 v1.0 的其他要求），升到 v1.0 写法后必须被 v1.0 接受
    fix_control: Callable[[dict], Any] | None = None

    def build(self, examples: dict) -> dict:
        value = copy.deepcopy(examples[self.base]["value"])
        out = self.mutate(value)
        return value if out is None else out

    def control(self, examples: dict) -> dict:
        value = copy.deepcopy(examples[self.base]["value"])
        if self.fix_control:
            self.fix_control(value)
        return value


def _uuid4_like(text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def to_v1(value, convert_keys: bool = True):
    """把实例升到 v1.0 的写法：契约版本 0.9.0 → 1.0.0；v0.9 示例里可读的幂等键换成确定性的 UUIDv4（v1.0 只收 UUIDv4）。
    其余不动，这样 v1.0 拒绝的原因只能是用例本身要证明的那条收紧。convert_keys=False 用于证明幂等键收紧的那条用例。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "contract_version" and v == "0.9.0":
                out[k] = "1.0.0"
            elif k == "idempotency_key" and convert_keys and isinstance(v, str) and not UUID4.match(v):
                out[k] = _uuid4_like(v)
            else:
                out[k] = to_v1(v, convert_keys)
        return out
    if isinstance(value, list):
        return [to_v1(v, convert_keys) for v in value]
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
                 "GradeResponseWenliEssay", _identity,
                 fix_control=_set("evidence.0.attempt_id", "00000000-0000-4000-8000-0000000000a2")),
    BreakingCase("作答没有 attempt_id", "N3、N4：attempt_id 由平台生成，必填", "GradeRequest",
                 "GradeRequestEchoTranscript", _identity,
                 fix_control=_set("attempt.attempt_id", "00000000-0000-4000-8000-0000000000a3")),
    BreakingCase("数学判分引用语文题目", "N7：请求学科与 item_ref 前缀一致", "GradeRequest", "GradeRequestAyaObjective",
                 _set("attempt.item_ref", "wenli:synthetic-q05")),
    BreakingCase("诊断作答引用别科题目", "N7：请求学科与 item_ref 前缀一致", "DiagnoseRequest", "DiagnoseRequestWenliPaper",
                 lambda v: v.__setitem__("attempts", [{"attempt_id": "00000000-0000-4000-8000-0000000000a9",
                                                       "item_ref": "aya:synthetic-0301-07",
                                                       "occurred_at": "2026-10-08T10:00:00+08:00",
                                                       "response": {"text": "（合成示例）"}}])),
    BreakingCase("变式排除列表混入别科题目", "N7：请求学科与引用前缀一致", "GenerateVariationRequest",
                 "GenerateVariationRequestZhixing", _set("exclude_item_refs", ["aya:synthetic-0301-07"])),
    BreakingCase("数学计划的掌握快照混入语文能力点", "N7：计划请求的能力点前缀一致", "PlanRequest", "PlanRequestAya",
                 _set("mastery.0.skill_node", "wenli:R3-L2")),
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
    BreakingCase("证据信号记录眼动（词根在中间）", "svc-#9：按分段拦截眼动（安全审查必须改 1）", "DiagnoseResponse",
                 "DiagnoseResponseWenliPaper", _add_signal("eye_gaze_ratio")),
    BreakingCase("证据信号记录面部（facial）", "svc-#9：按分段拦截面部", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
                 _add_signal("facial_tension")),
    BreakingCase("证据信号记录语音身份", "svc-#9：按分段拦截声纹与说话人", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
                 _add_signal("speaker_match")),
    BreakingCase("证据信号记录心情（后缀）", "svc-#9：按分段拦截情绪", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
                 _add_signal("session_mood")),
    BreakingCase("幂等键是可读文本", "安全审查必须改 2：幂等键只能是 UUIDv4", "GradeRequest", "GradeRequestAyaObjective",
                 _set("context.idempotency_key", "zhangsan-grade-0001-abcdef"), convert_keys=False),
    BreakingCase("选择题选项夹带文字", "安全审查建议 1：选项只能是编码", "GradeRequest", "GradeRequestAyaObjective",
                 _set("attempt.response", {"choice": ["我觉得是A"]})),
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
         _set("learner_context", [{"field_id": "outer.g1.weekly_available_time", "source_type": "self", "value": 300,
                                   "note": "（合成示例）夹带的文字"}])),
    Case("画像条目取值是自由文本", "svc-#1：取值只能是编码", "DiagnoseRequest", "DiagnoseRequestAyaWithContext",
         _set("learner_context.0.value", "最近状态不好")),
    Case("推断条目不带判断 ID", "svc-#1、规则 11：推断条目必须引用判断", "DiagnoseRequest", "DiagnoseRequestAyaWithContext",
         lambda v: (v["learner_context"][0].pop("claim_id"), None)[1]),
    Case("latex 夹带 \\text 文字", "安全审查建议 1：归一化内容不能夹带自由文本", "DiagnoseRequest", "DiagnoseRequestAyaWithContext",
         _set("attempts.0.response", {"latex": "2(x-1) \\text{老师我不会}"})),
    Case("latex 含中文", "安全审查建议 1：只允许可打印 ASCII", "DiagnoseRequest", "DiagnoseRequestAyaWithContext",
         _set("attempts.0.response", {"latex": "解：2(x-1)"})),
    Case("知识页面夹带学生字段", "规则 13：知识页面是公共层内容", "TeachRequest", "TeachRequestAyaWithPage",
         _set("knowledge_page.student_ref", "00000000-0000-4000-8000-00000000a002")),
    Case("时间戳带非法时区", "N10", "GradeRequest", "GradeRequestAyaObjective", _set("attempt.occurred_at", "2026-10-08T19:30:00+8")),
    Case("未认证错误码拼错", "N1：错误码是枚举", "Problem", "GradeRequestAyaObjective",
         lambda v: {"type": "about:blank", "title": "x", "status": 401, "code": "UNAUTHORIZED", "retryable": False}),
]
