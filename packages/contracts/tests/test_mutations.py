#!/usr/bin/env python3
"""契约检查的变异测试：把契约故意改坏，断言 check_contract 能发现。

证明两件事：
1. 契约里的每条关键规则都在起作用，删掉它，就会有反例漏过或检查报错；
2. 检查脚本没有“永远通过”的盲区（contract-guardian 审查 2026-09-25 阻断 6）。

用法：test_mutations.py （退出码 0 = 所有变异都被发现）
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import check_contract as cc  # noqa: E402
import check_services as cs  # noqa: E402

S = "components.schemas"


def _get(doc, path):
    node = doc
    for k in path.split("."):
        node = node[int(k)] if k.isdigit() else node[k]
    return node


def delete(path):
    def f(doc):
        parent, key = path.rsplit(".", 1)
        node = _get(doc, parent)
        del node[int(key) if key.isdigit() else key]
    return f


def assign(path, val):
    def f(doc):
        parent, key = path.rsplit(".", 1)
        _get(doc, parent)[int(key) if key.isdigit() else key] = copy.deepcopy(val)
    return f


def drop_allof_clause(schema, predicate_text):
    """删掉 schema.allOf 里 description 含 predicate_text 的那一条。"""
    def f(doc):
        node = _get(doc, f"{S}.{schema}")
        before = len(node["allOf"])
        node["allOf"] = [c for c in node["allOf"] if predicate_text not in c.get("description", "")]
        assert len(node["allOf"]) == before - 1, f"{schema} 里没找到 {predicate_text}"
    return f


def drop_job_binding(method):
    def f(doc):
        node = _get(doc, f"{S}.JobStatus")
        node["allOf"] = [c for c in node["allOf"]
                         if c.get("if", {}).get("properties", {}).get("method", {}).get("const") != method]
    return f


MUTATIONS = {
    # contract-guardian 阻断 6 列出的变异
    "删除 GradeResponse.unevaluatedProperties": delete(f"{S}.GradeResponse.unevaluatedProperties"),
    "GradeResult 改为开放对象": assign(f"{S}.GradeResult.additionalProperties", True),
    "GradeResult 加自由文本 remarks": assign(f"{S}.GradeResult.properties.remarks", {"type": "string"}),
    "GradeResult 加 patternProperties": assign(f"{S}.GradeResult.patternProperties", {".*": {"type": "string"}}),
    "GradeResponse 顶层加 teacher_comment": assign(f"{S}.GradeResponse.allOf.1.properties.teacher_comment", {"type": "string"}),
    "去掉 JobStatus 对 grade 的绑定": drop_job_binding("grade"),
    "删除 review_reasons 的必填条件": drop_allof_clause("PluginResultBase", "必须给出原因"),
    "删除 ModelVersion 的 pattern": delete(f"{S}.ModelVersion.pattern"),
    "occurred_at 改为可选": assign(f"{S}.EvidenceDraft.required", ["evidence_id", "kind", "admission"]),
    "删除候选题 admission 的 const": delete(f"{S}.VariationCandidate.properties.admission.const"),
    # 本轮修复新增的规则
    "删除“模型判分一律主观”": drop_allof_clause("DimensionScore", "模型判分一律是主观维度"),
    "删除“主观维度低置信”": drop_allof_clause("DimensionScore", "主观维度一律低置信"),
    "删除“主观维度必须复核”": drop_allof_clause("GradeResponse", "含主观维度"),
    "删除“needs_review 必须复核”": drop_allof_clause("GradeResponse", "needs_review → 复核"),
    "删除“null 维度必须复核”": drop_allof_clause("GradeResponse", "有维度分为 null → 复核"),
    "删除“null 维度必须列出”": drop_allof_clause("GradeResult", "总分必须列出被排除的维度"),
    "删除“验证器判分必须带 verifier”": drop_allof_clause("GradeResult", "必须给出验证器对象"),
    "删除“未准入 → practice”": drop_allof_clause("PluginResultBase", "未准入"),
    "grade 允许无证据": assign(f"{S}.GradeResponse.allOf.1", {"properties": {"result": {"$ref": "#/components/schemas/GradeResult"}}}),
    "DimensionScore.evidence_ids 允许为空": delete(f"{S}.DimensionScore.properties.evidence_ids.minItems"),
    "证据 payload 改为开放对象": assign(f"{S}.EvidenceDraft.properties.payload", {"type": "object", "additionalProperties": True}),
    "SourceVersion 去掉 pattern": delete(f"{S}.SourceVersion.pattern"),
    "semantics 改为可选": assign(f"{S}.PluginResultBase.required",
                              ["result", "evidence_ids", "confidence", "source_version", "model_version",
                               "human_review_required", "contract_version"]),
    "data_class 允许 L0/L1": assign(f"{S}.RequestContext.properties.data_class", {"$ref": "#/components/schemas/DataClass"}),
    "teach 允许考官型人设": assign(f"{S}.TeachRequest.properties.persona.properties.persona_id.enum",
                               ["aya", "miaobi", "diandian", "fangyuan", "athena"]),
    "变式 match 允许全 false": delete(f"{S}.VariationCandidate.properties.match.anyOf"),
    "teach 改为非消耗类": assign(f"{S}.TeachRequest.properties.context", {"$ref": "#/components/schemas/RequestContext"}),
    "idempotency_key 改为可选": assign(f"{S}.RequestContext.required",
                                     ["request_id", "subject", "student_ref", "data_class", "contract_version"]),
    "报告 ReportFields 改为开放对象": assign(f"{S}.ReportFields.additionalProperties", True),
    "异步受理不回传 usage_id": assign(f"{S}.JobAccepted.required", ["job_id", "method", "status", "poll_after_ms"]),
    # contract-guardian 复核（2026-09-25）发现漏检的变异
    "删除 kind→payload 绑定": delete(f"{S}.EvidenceDraft.allOf"),
    "DimensionScore.subjective 改为可选": assign(f"{S}.DimensionScore.required",
                                              ["dimension_id", "score", "max_score", "judged_by", "confidence", "evidence_ids"]),
    "删除主观判分的信封级置信上限": delete(f"{S}.GradeResponse.allOf.2.then.properties.confidence"),
    "删除未准入条件里的 content_not_admitted": delete(f"{S}.PluginResultBase.allOf.1.then.properties.review_reasons"),
    "ScoringPoint.evidence_ids 允许为空": delete(f"{S}.ScoringPoint.properties.evidence_ids.minItems"),
    "verifier.version 改为可选": assign(f"{S}.GradeResult.properties.verifier.required", ["verifier_id", "reason_codes"]),
    "prior_evidence 改回只传 ID": lambda d: (_get(d, f"{S}.DiagnoseRequest.properties").pop("prior_evidence"),
                                          _get(d, f"{S}.DiagnoseRequest.properties").__setitem__(
                                              "prior_evidence_ids", {"type": "array", "items": {"$ref": "#/components/schemas/EvidenceId"}})),
    "删除 mastery_history": delete(f"{S}.ReportRequest.properties.mastery_history"),
    "objstore 引用改回弱 pattern": assign(f"{S}.EvidenceDraft.properties.media_ref.pattern", "^objstore://"),
    # 本轮新增规则
    "删除“验证器边界原因码 → 复核”": drop_allof_clause("GradeResponse", "边界情况原因码"),
    "删除“needs_review 低置信”": drop_allof_clause("GradeResponse", "整体低置信"),
    "删除“采分点无法判定 → 复核”": drop_allof_clause("GradeResponse", "采分点无法判定"),
    "删除“规则判分必带 rule_ref”": drop_allof_clause("DimensionScore", "已登记的规则引用"),
    "证据 payload 改为可选": assign(f"{S}.EvidenceDraft.required", ["evidence_id", "kind", "occurred_at", "admission"]),
    "交互信号允许 xp_ 前缀": assign(f"{S}.EvidencePayloadSignal.properties.signal_code.pattern", "^[a-z0-9_]{1,48}$"),
    "Problem.title 去掉长度限制": delete(f"{S}.Problem.properties.title.maxLength"),
}


# ---------- 知识服务与洞察服务（specs/service-openapi-v0.9/spec.md 第 6 节） ----------
def svc(service, fn):
    """把针对单份文档的变异包装成针对 {plugin, knowledge, insight} 的变异。"""
    return lambda docs: fn(docs[service])


def svc_drop_allof(service, schema, predicate_text):
    """删掉 allOf 里的那一条；删空时连 allOf 一起删（空 allOf 本身不合法，会被 OpenAPI 校验先拦下，证明不了反例有效）。"""
    def f(doc):
        drop_allof_clause(schema, predicate_text)(doc)
        node = _get(doc, f"{S}.{schema}")
        if not node["allOf"]:
            del node["allOf"]
    return svc(service, f)


SERVICE_MUTATIONS = {
    "陈述的 claim_id 改为可选": svc("insight", assign(f"{S}.RenderedStatement.required",
                                                   ["template_key", "template_version", "status", "display", "text"])),
    "可展示状态加入 refuted": svc("insight", assign(f"{S}.ClaimStatusVisible.enum",
                                                ["hypothesis", "established", "to_verify", "refuted"])),
    "删除“待验证只画虚线”": svc_drop_allof("insight", "RenderedStatement", "待验证的判断只能以虚线展示"),
    "陈述文本去掉限长": svc("insight", delete(f"{S}.RenderedStatement.properties.text.maxLength")),
    "删除“推断条目必须引用判断”": svc_drop_allof("insight", "ContextEntry", "推断类条目必须引用判断"),
    "上下文取值允许自由文本": svc("insight", assign(f"{S}.CodeValue.oneOf.2", {"type": "string"})),
    "判断摘要加自由文本 note": svc("insight", assign(f"{S}.ClaimSummary.properties.note", {"type": "string", "maxLength": 200})),
    "预测结果加 probability": svc("insight", assign(
        f"{S}.InsightPredictResponse.allOf.1.properties.result.properties.probability", {"type": "number"})),
    "对账卡加 class_rank": svc("insight", assign(
        f"{S}.InsightReconcileResponse.allOf.1.properties.result.properties.class_rank", {"type": "integer"})),
    "记录事件允许调用方填来源": svc("insight", assign(f"{S}.ReportEvent.properties.source_type",
                                                  {"$ref": "#/components/schemas/SourceType"})),
    "洞察写入上下文不要求幂等键": svc("insight", assign(f"{S}.InsightWriteContext.allOf.1.required", [])),
    "异议改用普通上下文": svc("insight", assign(f"{S}.InsightDisputeRequest.properties.context",
                                            {"$ref": "#/components/schemas/InsightContext"})),
    "受众改为可选": svc("insight", assign(f"{S}.InsightContext.required",
                                          ["request_id", "contract_version", "caller", "data_class", "student_ref"])),
    "洞察上下文加 usage_id": svc("insight", assign(f"{S}.InsightContext.properties.usage_id", {"type": "string", "format": "uuid"})),
    "洞察描述写入禁用推断": svc("insight", assign(f"{S}.RenderedStatement.description", "例如：孩子很聪明")),
    "示例用字段字典之外的字段": svc("insight", assign(
        "components.examples.InsightRecordRequest.value.event.field_id", "outer.g1.not_in_dictionary")),
    "删除 insight.erase 路径": svc("insight", lambda d: d["paths"].pop("/v0/insight.erase")),
    "insight.explain 的 operationId 写错": svc("insight", lambda d: d["paths"]["/v0/insight.explain"]["post"].__setitem__("operationId", "explain")),
    "洞察响应去掉 unevaluatedProperties": svc("insight", delete(f"{S}.InsightExplainResponse.unevaluatedProperties")),
    "信封去掉 source_version 必填": svc("knowledge", assign(f"{S}.ServiceResultBase.required",
                                                     ["result", "evidence_ids", "confidence", "model_version",
                                                      "human_review_required", "contract_version"])),
    "删除“复核必须给原因”": svc_drop_allof("knowledge", "ServiceResultBase", "需要复核时必须给出原因"),
    "信封版本与 info 不一致": svc("knowledge", assign(f"{S}.ServiceResultBase.properties.contract_version.const", "0.9.1")),
    "公共层节点加 student_id": svc("knowledge", assign(f"{S}.NodeView.properties.student_id", {"type": "string", "format": "uuid"})),
    "公共层上下文加 student_ref": svc("knowledge", assign(f"{S}.PublicContext.properties.student_ref", {"type": "string", "format": "uuid"})),
    "节点改为开放对象": svc("knowledge", assign(f"{S}.NodeView.additionalProperties", True)),
    "页面正文去掉限长": svc("knowledge", delete(f"{S}.PageGetResponse.allOf.1.properties.result.properties.body_md.maxLength")),
    "题目标签加题干": svc("knowledge", assign(f"{S}.ItemTagView.properties.stem", {"type": "string"})),
    "证据等级加入 C": svc("knowledge", assign(f"{S}.EvidenceLevel.enum", ["A", "B", "C"])),
    "删除“拍照只能是 B 级”": svc_drop_allof("knowledge", "EvidenceWriteItem", "拍照通道只能是 B 级"),
    "证据等级改为可选": svc("knowledge", assign(f"{S}.EvidenceWriteItem.required", ["draft", "channel"])),
    "证据写入不要求幂等键": svc("knowledge", assign(f"{S}.WriteContext.allOf.1.required", [])),
    "证据写入加 usage_id": svc("knowledge", assign(f"{S}.StudentContext.properties.usage_id", {"type": "string", "format": "uuid"})),
    "采分点编号去掉 pattern": svc("knowledge", delete(f"{S}.ScoringPointView.properties.point_id.pattern")),
    "删除 mastery.explain 路径": svc("knowledge", lambda d: d["paths"].pop("/v0/mastery.explain")),
    "多出一个 node.delete": svc("knowledge", lambda d: d["paths"].__setitem__("/v0/node.delete", {"post": {
        "operationId": "node_delete", "responses": {"200": {"description": "x"}}}})),
    "示例证据引用不闭合": svc("knowledge", assign("components.examples.MasteryExplainResponse.value.evidence_ids", [])),
    "示例出现手机号": svc("knowledge", assign("components.examples.NodeResolveRequest.value.text", "合成 13812345678")),
    # 安全审查（2026-09-28）新增规则
    "删除“BFF 受众与身份绑定”": svc_drop_allof("insight", "InsightContext", "BFF 代表学生或家长访问"),
    "删除“编排层只用 Agent 受众”": svc_drop_allof("insight", "InsightContext", "编排层只能以 Agent 受众访问"),
    "删除“家长必须带绑定关系”": svc_drop_allof("insight", "Actor", "家长访问必须带绑定关系"),
    "上下文包对 BFF 开放": svc("insight", assign(f"{S}.InsightContextRequest.properties.context",
                                              {"$ref": "#/components/schemas/InsightContext"})),
    "删除改为学生也能发起": svc("insight", assign(f"{S}.InsightEraseRequest.properties.context",
                                               {"$ref": "#/components/schemas/InsightWriteContext"})),
    "删除的确认码改为可选": svc("insight", assign(f"{S}.InsightEraseRequest.required", ["context"])),
    "删除范围允许为空": svc("insight", delete(
        f"{S}.InsightEraseResponse.allOf.1.properties.result.properties.scopes.minItems")),
    "导出完成可以不给凭据": svc("insight", delete(f"{S}.InsightExportResponse.allOf.1.properties.result.allOf")),
    "下载凭据允许任意引用": svc("insight", assign(
        f"{S}.InsightExportResponse.allOf.1.properties.result.properties.download_ref.pattern", "^.{1,300}$")),
    "删除“学生自述只由本人提交”": svc_drop_allof("insight", "InsightRecordRequest", "学生自述只能由学生本人提交"),
    "删除“行为事件只由编排层记录”": svc_drop_allof("insight", "InsightRecordRequest", "行为事件只能由编排层记录"),
    "删除“失误只能学生本人提出”": svc_drop_allof("insight", "InsightDisputeRequest", "只能由学生本人提出"),
    "取值编码允许大写": svc("insight", assign(f"{S}.CodeValue.oneOf.2.pattern", "^[A-Za-z0-9][A-Za-z0-9_.:\\-]{0,63}$")),
    "洞察调用方加入 adapter": svc("insight", assign(f"{S}.InsightContext.properties.caller.enum", ["bff", "orchestrator", "adapter"])),
    "幂等键去掉 pattern": svc("insight", delete(f"{S}.InsightContext.properties.idempotency_key.pattern")),
    "上下文条目加 display_name": svc("insight", assign(f"{S}.ContextEntry.properties.display_name", {"type": "string", "maxLength": 32})),
    "node.resolve 对 BFF 开放": svc("knowledge", assign(f"{S}.NodeResolveRequest.properties.context",
                                                     {"$ref": "#/components/schemas/PublicContext"})),
    "公共层响应可引用学生证据": svc("knowledge", delete(f"{S}.NodeGetResponse.allOf.1.properties.evidence_ids")),
    "删除“照片语音是 L3”": svc("knowledge", delete(f"{S}.EvidenceWriteRequest.allOf")),
    "个人层工具对 BFF 开放": svc("knowledge", assign(f"{S}.StudentContext.properties.caller.enum", ["bff", "orchestrator", "learning"])),
    "公共层上下文加 user_ref": svc("knowledge", assign(f"{S}.PublicContext.properties.user_ref", {"type": "string", "format": "uuid"})),
    "公共层允许 L3": svc("knowledge", assign(f"{S}.PublicContext.properties.data_class.allOf.1.enum", ["L1", "L2", "L3"])),
    "证据内联 base64 音频": svc("knowledge", assign(f"{S}.EvidenceWriteItem.properties.audio",
                                                {"type": "string", "contentEncoding": "base64", "maxLength": 100000})),
    # 契约审查（2026-09-28）新增规则
    "对账卡陈述改为宽松 pattern 的字符串": svc("insight", assign(f"{S}.InsightReconcileResponse.allOf.1.properties.result.properties.statements",
        {"type": "array", "maxItems": 20, "items": {"type": "string", "pattern": "^[\\s\\S]{1,300}$"}})),
    "字符串改用自定义 format": svc("insight", assign(f"{S}.ClaimSummary.properties.target_id", {"type": "string", "format": "text"})),
    "字段改为没有类型的 {}": svc("insight", assign(f"{S}.ClaimSummary.properties.extra", {})),
    "字段改为没有字段清单的对象": svc("insight", assign(f"{S}.ClaimSummary.properties.extra", {"type": "object"})),
    "对账卡结果 unevaluatedProperties: true": svc("insight", assign(
        f"{S}.InsightReconcileResponse.allOf.1.properties.result.unevaluatedProperties", True)),
    "偏差节点改用 prefixItems": svc("insight", assign(f"{S}.InsightReconcileResponse.allOf.1.properties.result.properties.deviation_nodes.prefixItems", [{"type": "string"}])),
    "陈述的 display 改为可选": svc("insight", assign(f"{S}.RenderedStatement.required",
                                                  ["claim_id", "template_key", "template_version", "status", "text", "confidence_band"])),
    "删除“假设与成立画实线”": svc_drop_allof("insight", "RenderedStatement", "假设与成立的判断以实线展示"),
    "陈述不再要求置信度说明": svc("insight", assign(f"{S}.RenderedStatement.required",
                                                ["claim_id", "template_key", "template_version", "status", "display", "text"])),
    "判断摘要的来源改为可选": svc("insight", assign(f"{S}.ClaimSummary.required",
        ["claim_id", "target_type", "target_id", "template_key", "template_version", "status", "confidence", "evidence_ids", "expires_at"])),
    "删除“推断必须带模型版本”": svc_drop_allof("insight", "ClaimSummary", "推断类判断必须带模型版本"),
    "预测改为 BFF 可调用": svc("insight", assign(f"{S}.InsightPredictRequest.properties.context",
                                              {"$ref": "#/components/schemas/InsightWriteContext"})),
    "对账卡改为 BFF 可调用": svc("insight", assign(f"{S}.InsightReconcileRequest.properties.context",
                                                {"$ref": "#/components/schemas/InsightContext"})),
    "解释证据的等级内联并加入 C": svc("insight", assign(f"{S}.ExplainEvidence.properties.evidence_level",
                                                   {"type": "string", "enum": ["A", "B", "C"]})),
    "判断摘要加 iq_score": svc("insight", assign(f"{S}.ClaimSummary.properties.iq_score", {"type": "number"})),
    "对账方向加入 learning_style": svc("insight", assign(f"{S}.InsightReconcileResponse.allOf.1.properties.result.properties.deviation_directions.items.enum",
        ["item_type_transfer", "time_shortage", "calculation_slip", "diagnosis_optimistic", "learning_style"])),
    "预测结果加 likelihood": svc("insight", assign(
        f"{S}.InsightPredictResponse.allOf.1.properties.result.properties.likelihood", {"type": "number"})),
    "对账卡加 peer_average": svc("insight", assign(f"{S}.InsightReconcileResponse.allOf.1.properties.result.properties.peer_average", {"type": "number"})),
    "示例以家长观察记录只给 Agent 的推断字段": svc("insight", assign(
        "components.examples.InsightRecordRequest.value.event.field_id", "inner.g2.exam_prediction")),
    "掌握状态加 class_rank": svc("knowledge", assign(f"{S}.MasteryState.properties.class_rank", {"type": "integer"})),
    "删除“OCR 只能来自拍照”": svc_drop_allof("knowledge", "EvidenceWriteItem", "识别转写只能来自对应的通道"),
    "删除“ASR 只能来自语音”": svc_drop_allof("knowledge", "EvidenceWriteItem", "语音转写只能来自语音通道"),
    "删除“拍照必须匹配题库”": svc_drop_allof("knowledge", "EvidenceWriteItem", "拍照证据必须匹配到题库题"),
    "删除“画布语音需确认才是 A 级”": svc_drop_allof("knowledge", "EvidenceWriteItem", "画布与语音只有经学生确认后才是 A 级"),
    "删除“未准入不能是 A 级”": svc_drop_allof("knowledge", "EvidenceWriteItem", "未准入内容上的作答不能是 A 级"),
    "采分点结果的地图版本改为可选": svc("knowledge", assign(
        f"{S}.RubricGetResponse.allOf.1.properties.result.required", ["item_ref", "rubric_version", "points"])),
    # 首份画像报告 insight.report（SIE-02b 第二步）
    "删除 insight.report 路径": svc("insight", lambda d: d["paths"].pop("/v0/insight.report")),
    "报告句子的 claim_id 改为可选": svc("insight", assign(f"{S}.ReportSentence.required",
                                                     ["template_key", "template_version", "status", "display", "text"])),
    "报告句子状态加入 to_verify": svc("insight", assign(f"{S}.ReportSentence.properties.status.enum", ["hypothesis", "established", "to_verify"])),
    "报告句子可画虚线": svc("insight", assign(f"{S}.ReportSentence.properties.display", {"type": "string", "enum": ["solid", "dashed"]})),
    "删除“假设必须标初步判断”": svc_drop_allof("insight", "ReportSentence", "假设必须标"),
    "删除“成立不标初步判断”": svc_drop_allof("insight", "ReportSentence", "成立的判断不标"),
    "删除“家长版每句带置信度”": svc("insight", delete(f"{S}.InsightReportResponse.allOf.1.properties.result.allOf.0")),
    "删除“学生版不带置信度”": svc("insight", delete(f"{S}.InsightReportResponse.allOf.1.properties.result.allOf.1")),
    "报告对编排层开放": svc("insight", assign(f"{S}.InsightReportRequest.properties.context",
                                            {"$ref": "#/components/schemas/InsightContext"})),
    "报告提示语去掉限长": svc("insight", delete(f"{S}.InsightReportResponse.allOf.1.properties.result.properties.notice.maxLength")),
    "报告句子加自由文本 note": svc("insight", assign(f"{S}.ReportSentence.properties.note", {"type": "string", "maxLength": 200})),
    "报告加 percentile": svc("insight", assign(f"{S}.InsightReportResponse.allOf.1.properties.result.properties.percentile", {"type": "number"})),
    "报告允许空节": svc("insight", delete(f"{S}.InsightReportResponse.allOf.1.properties.result.properties.sections.minItems")),
    "报告标题限长放宽到 1000": svc("insight", assign(
        f"{S}.InsightReportResponse.allOf.1.properties.result.properties.title.maxLength", 1000)),
    "报告标题去掉限长": svc("insight", delete(f"{S}.InsightReportResponse.allOf.1.properties.result.properties.title.maxLength")),
    "衔接短语限长放宽到 1000": svc("insight", assign(f"{S}.ReportSection.properties.connective.maxLength", 1000)),
    "衔接短语去掉限长": svc("insight", delete(f"{S}.ReportSection.properties.connective.maxLength")),
    "每节句数上限放宽到 1000": svc("insight", assign(f"{S}.ReportSection.properties.sentences.maxItems", 1000)),
    "报告的策略版本改为可选": svc("insight", assign(f"{S}.InsightReportResponse.allOf.1.properties.result.required",
                                                ["format", "audience", "notice", "title", "sections", "claim_ids"])),
    "报告的策略版本去掉 pattern": svc("insight", delete(
        f"{S}.InsightReportResponse.allOf.1.properties.result.properties.policy_version.pattern")),
    "报告的策略版本接受任意文字": svc("insight", assign(
        f"{S}.InsightReportResponse.allOf.1.properties.result.properties.policy_version.pattern", "^.{1,200}$")),
    # 允许清单与契约脱节：字段改名后清单里的旧项失效
    "衔接短语改名（允许清单里的旧项失效）": svc("insight", lambda d: d["components"]["schemas"]["ReportSection"]["properties"].__setitem__(
        "connective_text", d["components"]["schemas"]["ReportSection"]["properties"].pop("connective"))),
}

# 洞察服务自由文本允许清单本身的变异（check_services.FREE_TEXT_ALLOW["insight"] 必须正好是 5 项且含 RenderedStatement.text）：
# 每个变异拿到清单的副本，改完后临时替换模块常量跑一遍服务契约检查，跑完恢复
ALLOWLIST_MUTATIONS = {
    "洞察服务允许清单多一项自由文本": lambda a: a.add(("ReportSentence", "note")),
    "洞察服务允许清单去掉 RenderedStatement.text 换成别的": lambda a: (a.discard(("RenderedStatement", "text")),
                                                              a.add(("ReportSection", "key"))),
    "洞察服务允许清单少一项": lambda a: a.discard(("InsightReportResponse.result", "title")),
}


# ---------- v1.0 候选版（specs/plugin-api-v1.0/spec.md 验收第 4 条） ----------
V1_MUTATIONS = {
    "删除“作答类证据必带 attempt_id”": drop_allof_clause("EvidenceDraft", "作答类证据必须指出"),
    "作答 attempt_id 改回可选": assign(f"{S}.AttemptInput.required", ["item_ref", "occurred_at", "response"]),
    "删除判分请求的前缀规则": delete(f"{S}.GradeRequest.allOf"),
    "删除诊断请求的前缀规则": delete(f"{S}.DiagnoseRequest.allOf"),
    "删除讲解请求的前缀规则": delete(f"{S}.TeachRequest.allOf"),
    "删除变式请求的前缀规则": delete(f"{S}.GenerateVariationRequest.allOf"),
    "时间戳去掉 pattern": delete(f"{S}.Timestamp.pattern"),
    "报告恢复 forecast": assign(f"{S}.ReportFields.properties.forecast",
                              {"type": "object", "properties": {"low": {"type": "number"}, "high": {"type": "number"},
                                                                "basis_admitted_items": {"type": "integer"}}}),
    "讲解恢复 verified_claims": assign(f"{S}.TeachResult.properties.verified_claims",
                                     {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                 "properties": {"claim": {"type": "string", "maxLength": 500},
                                                                                "verdict": {"type": "string", "enum": ["verified"]}}}}),
    "采分点编号允许大写": assign(f"{S}.ScoringPoint.properties.point_id.pattern", "^[A-Za-z0-9_\\-]{1,48}$"),
    "信号编码放开声纹与面部": assign(f"{S}.EvidencePayloadSignal.properties.signal_code.pattern",
                                   "^(?!xp_|achievement_|mastery_)[a-z0-9_]{1,48}$"),
    "学生 ID 去掉小写 pattern": delete(f"{S}.RequestContext.properties.student_ref.pattern"),
    "证据等级加入 D": assign(f"{S}.GradeRequest.properties.evidence_level.enum", ["A", "B", "C", "D"]),
    "考试满分允许 0": assign(f"{S}.ExamConfig.properties.full_score.minimum", 0),
    "作答形态不再多选一": delete(f"{S}.AttemptInput.properties.response.maxProperties"),
    "信封版本停在 0.9.0": assign(f"{S}.PluginResultBase.properties.contract_version.const", "0.9.0"),
    "删除错误码 UNAUTHENTICATED": lambda d: _get(d, f"{S}.Problem.properties.code.enum").remove("UNAUTHENTICATED"),
    "删除衔接字段 learner_context": delete(f"{S}.DiagnoseRequest.properties.learner_context"),
    "衔接字段 scoring_points 改为必填": assign(f"{S}.GradeRequest.required", ["context", "attempt", "rubric_version", "scoring_points"]),
    "只删判分请求里数学的前缀子句": lambda d: _get(d, f"{S}.GradeRequest").__setitem__("allOf", [
        c for c in _get(d, f"{S}.GradeRequest.allOf") if "为 aya 时" not in c.get("description", "")]),
    "删除计划请求的前缀规则": delete(f"{S}.PlanRequest.allOf"),
    "幂等键放开为任意文本": assign(f"{S}.RequestContext.properties.idempotency_key",
                                  {"type": "string", "minLength": 16, "maxLength": 128}),
    "latex 去掉字符与命令约束": delete(f"{S}.AttemptInput.properties.response.properties.latex.pattern"),
    "信号编码不拦眼动与心情": assign(f"{S}.EvidencePayloadSignal.properties.signal_code.pattern",
                                   "^(?!xp_|achievement_|mastery_)(?!(?:[a-z0-9]+_)*(?:voiceprint|voice|speaker|face|facial|emotion|emotional|affect|affective)(?:_|$))[a-z0-9_]{1,48}$"),
    "选择题选项放开": assign(f"{S}.AttemptInput.properties.response.properties.choice.items", {"type": "string"}),
    "作图语义的操作名放开": assign(f"{S}.AttemptInput.properties.response.properties.semantic.items.properties.op",
                                  {"type": "string", "maxLength": 32}),
}


# ---------- 学习服务内部 API v1（1.1.0：掌握读取与验证任务，KGE-04 第二步 PR 3） ----------
P = "paths"
INTERNAL_MUTATIONS = {
    "验证请求放开多余字段": assign(f"{S}.VerificationRequest.additionalProperties", True),
    "验证请求不要求学生": assign(f"{S}.VerificationRequest.required",
                                ["request_id", "dispute_ref", "subject", "node_id", "cognitive_level", "reason", "item_count"]),
    "验证请求去掉原因常量": delete(f"{S}.VerificationRequest.properties.reason.const"),
    "验证请求题数不设上限": delete(f"{S}.VerificationRequest.properties.item_count.maximum"),
    "验证请求学生 ID 不限小写": delete(f"{S}.VerificationRequest.properties.student_ref.pattern"),
    "验证请求学科放开英语": assign(f"{S}.VerificationRequest.properties.subject.enum", ["wenli", "aya", "zhixing", "echo"]),
    "验证任务放开多余字段（可夹带题目）": assign(f"{S}.VerificationTask.additionalProperties", True),
    "验证任务状态放开": assign(f"{S}.VerificationTask.properties.status", {"type": "string", "maxLength": 16}),
    "验证接口改用 BFF 服务令牌": assign(f"{P}./internal/v1/verification.post.security", [{"bffServiceToken": []}]),
    "验证接口去掉专用鉴权（回落到顶层服务令牌）": delete(f"{P}./internal/v1/verification.post.security"),
    "掌握读取也接受洞察服务令牌": assign(f"{P}./internal/v1/mastery/get.post.security", [{"insightServiceToken": []}]),
    "掌握读取改用 BFF 服务令牌": assign(f"{P}./internal/v1/mastery/explain.post.security", [{"bffServiceToken": []}]),
    "掌握读取去掉专用鉴权（回落到顶层服务令牌）": delete(f"{P}./internal/v1/mastery/get.post.security"),
    "计划接口接受编排层令牌": assign(f"{P}./internal/v1/plan.post.security", [{"orchestratorServiceToken": []}]),
    "撤销接口改用 BFF 服务令牌": assign(f"{P}./internal/v1/verification/cancel.post.security", [{"bffServiceToken": []}]),
    "撤销请求放开多余字段": assign(f"{S}.VerificationCancelRequest.additionalProperties", True),
    "撤销请求不要求学生": assign(f"{S}.VerificationCancelRequest.required", ["request_id", "task_id"]),
    "撤销结果状态放开": assign(f"{S}.VerificationCancelResult.properties.status", {"type": "string", "maxLength": 16}),
    "删掉撤销接口": delete(f"{P}./internal/v1/verification/cancel"),
    "掌握读取的响应另写一份开放对象": lambda d: d["paths"]["/internal/v1/mastery/get"]["post"]["responses"]["200"]["content"][
        "application/json"].__setitem__("schema", {"type": "object"}),
    "掌握解释的请求改成内部自定义": assign(
        f"{P}./internal/v1/mastery/explain.post.requestBody.content.application/json.schema", {"$ref": "#/components/schemas/PlanQuery"}),
    "删掉验证任务接口": delete(f"{P}./internal/v1/verification"),
    "版本号没有升到 1.1.0": assign("info.version", "1.0.0"),
    # PR 4：学情档案页（1.1.0 草稿内追加）
    "档案页读取也接受洞察服务令牌": assign(f"{P}./internal/v1/profile-page/read.post.security",
                                      [{"orchestratorServiceToken": []}, {"insightServiceToken": []}]),
    "档案页 ensure 改用 BFF 服务令牌": assign(f"{P}./internal/v1/profile-page/ensure.post.security", [{"bffServiceToken": []}]),
    "档案页 ensure 去掉专用鉴权（回落到顶层服务令牌）": delete(f"{P}./internal/v1/profile-page/ensure.post.security"),
    "档案页读取去掉专用鉴权（BFF 能读到 Agent 版待办）": delete(f"{P}./internal/v1/profile-page/read.post.security"),
    "档案页陈述可以是待验证": assign(f"{S}.ProfileStatement.properties.status.enum", ["hypothesis", "established", "to_verify"]),
    "档案页陈述不要求 claim_id": assign(f"{S}.ProfileStatement.required", ["template_key", "template_version", "status", "text"]),
    "档案页待办可以带文字": assign(f"{S}.ClaimRef.additionalProperties", True),
    "档案页衔接语可含数字": delete(f"{S}.ProfileSection.properties.connective.pattern"),
    "档案页节不限六节": assign(f"{S}.ProfileSectionKey.enum", ["current_stage", "mastered", "working_on", "root_cause", "recent_progress",
                                                       "next_step", "personality"]),
    "档案页 ensure 触发点放开": assign(f"{S}.ProfilePageEnsureRequest.properties.trigger.enum", ["session_start", "weekly", "attempt"]),
    "档案页 ensure 结果放开多余字段（可夹带文字）": assign(f"{S}.ProfilePageEnsureResult.additionalProperties", True),
    "档案页放开多余字段（可夹带模型版本）": assign(f"{S}.ProfilePage.additionalProperties", True),
    "删掉档案页读取接口": delete(f"{P}./internal/v1/profile-page/read"),
    # H5 星图 v1 PR 1（1.1.0 草稿内追加）：只给 BFF、只有星态
    "星图节点夹带掌握概率": assign(f"{S}.StarMapNode.properties.p_mastery", {"type": "number", "minimum": 0, "maximum": 1}),
    "星图节点夹带置信度（且不在 required 里，示例照样通过）": assign(f"{S}.StarMapNode.properties.confidence", {"type": "number"}),
    "星图节点夹带证据 ID": assign(f"{S}.StarMapNode.properties.evidence_ids", {"type": "array", "items": {"type": "string"}}),
    "星图节点夹带各层次明细": assign(f"{S}.StarMapNode.properties.levels", {"type": "array", "maxItems": 4, "items": {"type": "object", "additionalProperties": False}}),
    "星图响应顶层夹带置信度": assign(f"{S}.StarMapNodes.properties.confidence", {"type": "number"}),
    "星图节点放开多余字段": assign(f"{S}.StarMapNode.additionalProperties", True),
    "星图响应放开多余字段": delete(f"{S}.StarMapNodes.additionalProperties"),
    "星图节点改引用掌握状态（带 p_mastery）": assign(f"{S}.StarMapNodes.properties.nodes.items",
                                           {"$ref": "./knowledge-service.v0.9.yaml#/components/schemas/MasteryState"}),
    "星图节点不要求 basis_id": assign(f"{S}.StarMapNode.required", ["node_id", "state", "star_lit"]),
    "星图去掉“无记录即 unseen”": lambda d: d["components"]["schemas"]["StarMapNode"]["allOf"].pop(0),
    "星图去掉“星亮当且仅当迁移及以上”": lambda d: d["components"]["schemas"]["StarMapNode"]["allOf"].pop(1),
    "星图无地图也可带星": lambda d: d["components"]["schemas"]["StarMapNodes"]["allOf"].pop(0),
    "星图请求不要求调用方": assign(f"{S}.StarMapNodesRequest.required", ["request_id", "student_ref", "subject"]),
    "星图请求放开多余字段": assign(f"{S}.StarMapNodesRequest.additionalProperties", True),
    "星图请求学生 ID 不限小写": delete(f"{S}.StarMapNodesRequest.properties.student_ref.pattern"),
    "星图学科放开英语": assign(f"{S}.StarMapSubject.enum", ["wenli", "aya", "zhixing", "echo"]),
    "星图接口接受编排层令牌": assign(f"{P}./internal/v1/star-map/nodes.post.security", [{"bffServiceToken": []}, {"orchestratorServiceToken": []}]),
    "星图接口改用洞察服务令牌": assign(f"{P}./internal/v1/star-map/nodes.post.security", [{"insightServiceToken": []}]),
    "星图接口去掉显式鉴权（回落到顶层）": delete(f"{P}./internal/v1/star-map/nodes.post.security"),
    "星图响应另写一份开放对象": lambda d: d["paths"]["/internal/v1/star-map/nodes"]["post"]["responses"]["200"]["content"][
        "application/json"].__setitem__("schema", {"type": "object"}),
    "删掉星图节点接口": delete(f"{P}./internal/v1/star-map/nodes"),
}


def main() -> int:
    base = cc.load(cc.DEFAULT_SPEC)
    baseline = cc.run_checks(base)
    if baseline:
        print("mutation-test: 基线契约本身就不通过，先修契约：")
        for e in baseline:
            print(f"  - {e}")
        return 1

    missed = []
    for name, mutate in MUTATIONS.items():
        doc = copy.deepcopy(base)
        mutate(doc)
        if not cc.run_checks(doc):
            missed.append(name)

    service_base = cs.load_all()
    service_baseline = cs.run_service_checks(service_base)
    if service_baseline:
        print("mutation-test: 服务契约基线本身就不通过，先修契约：")
        for e in service_baseline:
            print(f"  - {e}")
        return 1
    for name, mutate in SERVICE_MUTATIONS.items():
        docs = copy.deepcopy(service_base)
        mutate(docs)
        errs = cs.run_service_checks(docs)
        # 只被 OpenAPI 合法性或“无法完成”拦下，不能证明对应规则有效（契约审查建议 9）
        if not [e for e in errs if "OpenAPI 3.1 校验失败" not in e and "无法完成" not in e]:
            missed.append(name)
    original_allow = cs.FREE_TEXT_ALLOW
    for name, mutate in ALLOWLIST_MUTATIONS.items():
        allow = set(original_allow["insight"])
        mutate(allow)
        cs.FREE_TEXT_ALLOW = {**original_allow, "insight": allow}
        try:
            errs = cs.run_service_checks(copy.deepcopy(service_base))
        finally:
            cs.FREE_TEXT_ALLOW = original_allow
        if not [e for e in errs if "OpenAPI 3.1 校验失败" not in e and "无法完成" not in e]:
            missed.append(f"允许清单：{name}")

    v09 = cc.load(cc.DEFAULT_SPEC)
    v1_base = cc.load(cc.V1_SPEC)
    v1_baseline = cc.run_checks(v1_base, cc.V1_SPEC)
    cc.check_breaking(v09, v1_base, v1_baseline)
    if v1_baseline:
        print("mutation-test: v1.0 候选版基线本身就不通过，先修契约：")
        for e in v1_baseline:
            print(f"  - {e}")
        return 1
    for name, mutate in V1_MUTATIONS.items():
        doc = copy.deepcopy(v1_base)
        mutate(doc)
        errs = cc.run_checks(doc, cc.V1_SPEC)
        cc.check_breaking(v09, doc, errs)
        if not [e for e in errs if "OpenAPI 3.1 校验失败" not in e and "无法完成" not in e]:
            missed.append(f"v1.0：{name}")

    internal_base = cc.load(cc.INTERNAL_SPECS[1])
    internal_baseline: list[str] = []
    cc._check_internal_v1_examples(internal_baseline, copy.deepcopy(internal_base))
    if internal_baseline:
        print("mutation-test: learning-internal v1 基线本身就不通过，先修契约：")
        for e in internal_baseline:
            print(f"  - {e}")
        return 1
    for name, mutate in INTERNAL_MUTATIONS.items():
        doc = copy.deepcopy(internal_base)
        mutate(doc)
        errs: list[str] = []
        try:
            cc._check_internal_v1_examples(errs, doc)
        except Exception as exc:  # noqa: BLE001 - 结构被改坏导致检查崩溃不算“发现”
            errs = [f"无法完成：{type(exc).__name__}"]
        if not [e for e in errs if "无法完成" not in e]:
            missed.append(f"learning-internal v1：{name}")

    total = len(MUTATIONS) + len(SERVICE_MUTATIONS) + len(ALLOWLIST_MUTATIONS) + len(V1_MUTATIONS) + len(INTERNAL_MUTATIONS)
    if missed:
        print(f"mutation-test: 失败，{len(missed)}/{total} 个变异没有被发现：")
        for m in missed:
            print(f"  - {m}")
        return 1
    print(f"mutation-test: 通过（{total} 个变异全部被发现，其中服务契约 {len(SERVICE_MUTATIONS)} 个、允许清单 {len(ALLOWLIST_MUTATIONS)} 个、v1.0 候选版 {len(V1_MUTATIONS)} 个、"
          f"learning-internal v1 {len(INTERNAL_MUTATIONS)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
