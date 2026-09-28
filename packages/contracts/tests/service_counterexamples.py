"""知识服务与洞察服务契约的反例集（specs/service-openapi-v0.9/spec.md 第 6 节）。

每个反例从一个合法示例出发做一处违规修改；scripts/check_services.py 逐个校验，任何一个被 schema 接受，检查就失败。
所有数据均为合成数据（CLAUDE.md 规则 8）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Case:
    name: str
    rule: str
    service: str   # knowledge / insight
    schema: str
    base: str      # 从哪个示例出发
    mutate: Callable[[Any], Any]


def _set(path: str, val):
    def f(v):
        node = v
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[int(k)] if k.isdigit() else node[k]
        last = keys[-1]
        if val is _DELETE:
            del node[int(last) if last.isdigit() else last]
        else:
            node[int(last) if last.isdigit() else last] = val
    return f


def _chain(*fns):
    def f(v):
        for fn in fns:
            fn(v)
    return f


_DELETE = object()

CASES = [
    # ---------- 洞察服务：判断先于展示 ----------
    Case("陈述不带 claim_id", "规则 11：面向学生家长的文字必须引用判断", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.statement.claim_id", _DELETE)),
    Case("陈述引用已推翻的判断", "规则 11：只能引用假设、成立（待验证画虚线）", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.statement.status", "refuted")),
    Case("待验证的判断以实线展示", "ADR 13 待人确认 #2：待验证只画虚线", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.statement.status", "to_verify")),
    Case("陈述超过 300 字", "模板渲染的陈述限长", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.statement.text", "合" * 301)),
    Case("上下文包里的推断不带判断 ID", "规则 11：推断类条目必须引用判断", "insight",
         "InsightContextResponse", "InsightContextResponse", _set("result.entries.0.claim_id", _DELETE)),
    Case("上下文条目的取值是自由文本", "规则 12：取值只能是编码", "insight",
         "InsightContextResponse", "InsightContextResponse", _set("result.entries.1.value", "合成 自由 文本")),
    Case("响应夹带额外的评语字段", "信封外不允许额外字段", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.comment", "合成评语")),
    Case("预测结果带概率", "平台规划 v1.3 §3：不给概率", "insight",
         "InsightPredictResponse", "InsightPredictResponse", _set("result.probability", 0.62)),
    Case("预测结果带名次", "平台规划 v1.3 §3：不显示个人名次", "insight",
         "InsightPredictResponse", "InsightPredictResponse", _set("result.rank", 12)),
    Case("调用方自己填写来源类型", "规则 6：来源由事件类型决定", "insight",
         "InsightRecordRequest", "InsightRecordRequest", _set("event.source_type", "objective")),
    Case("家长观察写成自由文本", "规则 6、12：取值只能是编码", "insight",
         "InsightRecordRequest", "InsightRecordRequest", _set("event.value", "合成 自由 文本")),
    Case("写入类请求缺 idempotency_key", "幂等", "insight",
         "InsightDisputeRequest", "InsightDisputeRequest", _set("context.idempotency_key", _DELETE)),
    Case("异议类型不在三条路里", "SIE 第八章三条路", "insight",
         "InsightDisputeRequest", "InsightDisputeRequest", _set("kind", "other")),
    Case("安排验证题却不给题目", "异议 i_know 必须安排验证题", "insight",
         "InsightDisputeResponse", "InsightDisputeResponse", _set("result.verification_item_refs", _DELETE)),
    Case("请求不带受众", "可见范围按受众过滤", "insight",
         "InsightExplainRequest", "InsightExplainRequest", _set("context.audience", _DELETE)),
    Case("请求带计量 usage_id", "规则 4：本接口不计次", "insight",
         "InsightExplainRequest", "InsightExplainRequest",
         _set("context.usage_id", "00000000-0000-4000-8000-00000000b001")),
    Case("需要复核却不给原因", "统一信封", "insight",
         "InsightClaimsResponse", "InsightClaimsResponse", _set("human_review_required", True)),
    # ---------- 知识服务 ----------
    Case("公共层响应夹带 student_id", "规则 13：公共层不含学生数据", "knowledge",
         "NodeGetResponse", "NodeGetResponse", _set("result.node.student_id", "00000000-0000-4000-8000-00000000a001")),
    Case("公共层请求带学生标识", "规则 13：公共层请求不带学生标识", "knowledge",
         "NodeGetRequest", "NodeGetRequest", _set("context.student_ref", "00000000-0000-4000-8000-00000000a001")),
    Case("信封缺 source_version", "KGE 第八章统一信封", "knowledge",
         "NodeGetResponse", "NodeGetResponse", _set("source_version", _DELETE)),
    Case("节点 ID 用小写", "kge.node_id 正则", "knowledge",
         "NodeGetRequest", "NodeGetRequest", _set("node_id", "math.func.quad")),
    Case("题目检索结果夹带题干", "kge schema v0.9 待人确认 #3：题干不经本接口", "knowledge",
         "ItemSearchResponse", "ItemSearchResponse", _set("result.items.0.stem", "合成题干")),
    Case("采分点编号不合规", "采分点编号与判分维度同一写法", "knowledge",
         "RubricGetResponse", "RubricGetResponse", _set("result.points.0.point_id", "Vertex Point")),
    Case("写入 C 级证据", "技术蓝图 v2.1：C 级不进学习者模型", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.evidence_level", "C")),
    Case("拍照证据标为 A 级", "技术蓝图 v2.1：拍照最高 B 级", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.channel", "photo")),
    Case("证据不带等级", "技术蓝图 v2.1：证据必须分级", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.evidence_level", _DELETE)),
    Case("证据写入缺 idempotency_key", "幂等", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("context.idempotency_key", _DELETE)),
    Case("证据内联原图", "原图切片与校正日志只留在平台侧作答证据", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.raw_image", "data:image/png;base64,AAAA")),
    Case("知识服务需要复核却不给原因", "统一信封", "knowledge",
         "NodeResolveResponse", "NodeResolveResponse", _set("human_review_required", True)),
    Case("掌握状态用自造门槛", "五道门槛", "knowledge",
         "MasteryGetResponse", "MasteryGetResponse", _set("result.states.0.gate", "mastered")),
    Case("掌握读取带计量 usage_id", "规则 4：本接口不计次", "knowledge",
         "MasteryGetRequest", "MasteryGetRequest", _set("context.usage_id", "00000000-0000-4000-8000-00000000b001")),
]
