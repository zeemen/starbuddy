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

    if missed:
        print(f"mutation-test: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现：")
        for m in missed:
            print(f"  - {m}")
        return 1
    print(f"mutation-test: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
