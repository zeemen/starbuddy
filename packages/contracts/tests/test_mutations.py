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
        if not cs.run_service_checks(docs):
            missed.append(name)

    total = len(MUTATIONS) + len(SERVICE_MUTATIONS)
    if missed:
        print(f"mutation-test: 失败，{len(missed)}/{total} 个变异没有被发现：")
        for m in missed:
            print(f"  - {m}")
        return 1
    print(f"mutation-test: 通过（{total} 个变异全部被发现，其中服务契约 {len(SERVICE_MUTATIONS)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
