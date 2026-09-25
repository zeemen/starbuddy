"""契约反例集：每个反例都是一个“应该被 schema 拒绝”的请求或响应。

每个反例从一个合法示例出发，做一处违规修改。check_contract.py 会逐个校验，
任何一个反例被 schema 接受，契约检查就失败。

新增的规则应当同时新增反例；contract-guardian 审查（2026-09-25）指出的反例都已收录。
所有数据均为合成数据（CLAUDE.md 规则 8）。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Case:
    name: str
    rule: str          # 这个反例守的是哪条规则
    schema: str        # 用哪个 schema 校验
    base: str          # 从哪个示例出发
    mutate: Callable[[dict], Any]

    def build(self, examples: dict) -> dict:
        value = copy.deepcopy(examples[self.base]["value"])
        out = self.mutate(value)
        return value if out is None else out


def _set(path: str, val):
    def f(v):
        node = v
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[int(k)] if k.isdigit() else node[k]
        last = keys[-1]
        if val is _DELETE:
            del node[int(last) if last.isdigit() else last]
        elif last.isdigit():
            node[int(last)] = val
        else:
            node[last] = val
    return f


def _chain(*fns):
    def f(v):
        for fn in fns:
            fn(v)
    return f


_DELETE = object()
G = "GradeResponseAyaObjective"
E = "GradeResponseWenliEssay"

CASES: list[Case] = [
    # ---- 判分只来自验证器或 Rubric，主观判分低置信加复核（规则 3；wenli/zhixing A2、echo A3）----
    Case("模型判分标成非主观", "规则 3 / A2", "GradeResponse", E,
         _set("result.dimensions.0.subjective", False)),
    Case("主观维度高置信且不复核", "规则 3 / A2", "GradeResponse", E,
         _chain(_set("result.dimensions.0.confidence", 0.99), _set("confidence", 0.99),
                _set("human_review_required", False), _set("review_reasons", _DELETE))),
    Case("单个主观维度高置信（信封正常、已复核）", "A2 维度级", "GradeResponse", E,
         _set("result.dimensions.0.confidence", 0.9)),
    Case("主观维度复核但原因缺 subjective_dimension", "A2", "GradeResponse", E,
         _set("review_reasons", ["spot_check"])),
    Case("needs_review 却不复核", "aya adapter-design §2", "GradeResponse", G,
         _set("result.verdict", "needs_review")),
    Case("维度分为 null 但不列 excluded_dimensions", "echo A3", "GradeResponse", G,
         _chain(_set("result.dimensions.0.score", None),
                _set("human_review_required", True), _set("review_reasons", ["dimension_unavailable"]))),
    Case("维度分为 null 但不复核", "echo A3", "GradeResponse", G,
         _chain(_set("result.dimensions.0.score", None), _set("result.score.excluded_dimensions", ["answer"]))),
    # ---- 溯源闭环（规则 3、6）----
    Case("grade 没有任何证据", "规则 3", "GradeResponse", G,
         _chain(_set("evidence_ids", []), _set("evidence", _DELETE),
                _set("result.dimensions.0.evidence_ids", []))),
    Case("维度不带证据", "规则 3", "GradeResponse", G, _set("result.dimensions.0.evidence_ids", [])),
    Case("信封不带新证据（维度仍引用旧 ID）", "规则 3", "GradeResponse", G,
         _chain(_set("evidence_ids", []), _set("evidence", _DELETE))),
    Case("验证器判分却不带 verifier 对象", "规则 3", "GradeResponse", G, _set("result.verifier", _DELETE)),
    Case("结果不带 rubric_version", "规则 3", "GradeResponse", G, _set("result.rubric_version", _DELETE)),
    Case("model_version 写 routed", "wenli KD-04 / zhixing KD-07", "GradeResponse", G,
         _set("model_version", "routed")),
    Case("dimensions 为空", "规则 3", "GradeResponse", G, _set("result.dimensions", [])),
    # ---- 自由文本通道（规则 3）----
    Case("信封顶层夹带评语", "规则 3", "GradeResponse", G, _set("comment", "做得很好")),
    Case("证据 payload 夹带评语与模型分", "规则 3", "GradeResponse", G,
         _set("evidence.0.payload", {"sampled_points": 8, "agreeing_points": 8, "feedback": "满分！", "llm_score": 10})),
    Case("excluded_dimensions 写成中文句子", "规则 3", "GradeResponse", G,
         _chain(_set("result.dimensions.0.score", None),
                _set("result.score.excluded_dimensions", ["这篇作文很好所以发音维不计"]),
                _set("human_review_required", True), _set("review_reasons", ["dimension_unavailable"]))),
    Case("source_version 写自然语言", "规则 3", "GradeResponse", G,
         _set("source_version", "老师觉得这题应该给满分")),
    Case("verifier_id 写自然语言", "规则 3", "GradeResponse", G,
         _set("result.verifier.verifier_id", "我觉得对")),
    Case("错因 algorithm_version 写自然语言", "规则 6", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
         _set("result.error_attributions.0.algorithm_version", "凭感觉判断")),
    # ---- 内容准入（aya #3/#11、wenli 3.1）----
    Case("未准入证据却给出 formal 且不复核", "内容准入", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
         _chain(_set("semantics", "formal"), _set("human_review_required", False), _set("review_reasons", _DELETE))),
    Case("缺 semantics", "内容准入", "PlanResponse", "PlanResponseAya", _set("semantics", _DELETE)),
    Case("变式候选未准入", "aya #9 等", "GenerateVariationResult", "GenerateVariationResponseZhixingEmpty",
         lambda v: {"candidates": [{
             "item_ref": "zhixing:synthetic-m3-02", "admission": "pending_review",
             "content_version": {"region": "nanjing", "exam_year": 2027, "grade": 9,
                                 "textbook_version": "bubian-2024", "subject": "zhixing", "content_version": "0.1.0"},
             "match": {"same_skill_node": True, "same_error_category": True}}]}),
    Case("变式候选既不同节点也不同错因", "四科选题语义", "GenerateVariationResult", "GenerateVariationResponseZhixingEmpty",
         lambda v: {"candidates": [{
             "item_ref": "zhixing:synthetic-m3-02", "admission": "admitted",
             "content_version": {"region": "nanjing", "exam_year": 2027, "grade": 9,
                                 "textbook_version": "bubian-2024", "subject": "zhixing", "content_version": "0.1.0"},
             "match": {"same_skill_node": False, "same_error_category": False}}]}),
    Case("候选为空却不给原因", "四科选题语义", "GenerateVariationResponse", "GenerateVariationResponseZhixingEmpty",
         _set("result.empty_reason", _DELETE)),
    # ---- 唯一真相源（规则 2）与报告红线 ----
    Case("证据 payload 写入 XP", "echo #12 / 规则 2", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
         _set("evidence.0.payload", {"text": "（合成）", "xp": 120})),
    Case("观测等级写成长句", "规则 2", "DiagnoseResponse", "DiagnoseResponseWenliPaper",
         _set("result.observations.0.local_level", "已经完全掌握这个能力点可以点亮星星")),
    Case("家长报告带排名", "开发计划 冲刺 5-H", "ReportResponse", "ReportResponseWenliParent",
         _set("result.rank", 3)),
    # ---- 计量与幂等（规则 4；ADR 05/06）----
    Case("teach 缺 usage_id", "规则 4", "TeachRequest", "TeachRequestAya", _set("context.usage_id", _DELETE)),
    Case("grade 缺 idempotency_key", "规则 4", "GradeRequest", "GradeRequestAyaObjective",
         _set("context.idempotency_key", _DELETE)),
    Case("grade 缺 content_version", "可追溯", "GradeRequest", "GradeRequestAyaObjective",
         _set("context.content_version", _DELETE)),
    Case("证据缺 occurred_at", "ADR 05", "GradeResponse", G, _set("evidence.0.occurred_at", _DELETE)),
    Case("异步受理不回传 usage_id", "ADR 06", "JobAccepted", "JobAcceptedEcho", _set("usage_id", _DELETE)),
    # ---- 数据级别（ADR 04/09）----
    Case("带 student_ref 却声明 L1", "ADR 09 §2", "PlanRequest", "PlanRequestAya", _set("context.data_class", "L1")),
    # ---- 人设边界（完整方案 5.4）----
    Case("teach 由考官型方圆主持", "5.4", "TeachRequest", "TeachRequestAya", _set("persona.persona_id", "fangyuan")),
    # ---- 复核原因 ----
    Case("复核但不给原因", "信封", "PlanResponse", "PlanResponseAya", _set("human_review_required", True)),
    # ---- 异步任务绑定 ----
    Case("method=grade 却返回诊断结果", "JobStatus", "JobStatus", "JobAcceptedEcho",
         lambda v: {"job_id": v["job_id"], "method": "grade", "status": "succeeded", "usage_id": v["usage_id"],
                    "response": _example("DiagnoseResponseWenliPaper")}),
    Case("queued 状态却带结果", "JobStatus", "JobStatus", "JobAcceptedEcho",
         lambda v: {"job_id": v["job_id"], "method": "grade", "status": "queued", "usage_id": v["usage_id"],
                    "response": _example(G)}),
    Case("succeeded 却不带结果", "JobStatus", "JobStatus", "JobAcceptedEcho",
         lambda v: {"job_id": v["job_id"], "method": "grade", "status": "succeeded", "usage_id": v["usage_id"]}),
]

_EXAMPLES: dict = {}


def _example(name: str) -> dict:
    return copy.deepcopy(_EXAMPLES[name]["value"])


# check_contract 在调用 build 之前没有传入全部示例给 lambda，这里通过 bind 注入
_orig_build = Case.build


def _build_with_examples(self: Case, examples: dict) -> dict:
    _EXAMPLES.clear()
    _EXAMPLES.update(examples)
    return _orig_build(self, examples)


Case.build = _build_with_examples  # type: ignore[method-assign]
