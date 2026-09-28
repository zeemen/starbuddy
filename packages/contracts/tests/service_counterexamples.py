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
         "EvidenceWriteRequest", "EvidenceWriteRequest",
         _chain(_set("context.data_class", "L3"), _set("evidences.0.channel", "photo"))),
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
    # ---------- 安全审查（2026-09-28）：调用方、受众与代表谁 ----------
    Case("BFF 以 Agent 受众访问", "安全 B1：BFF 只能以学生或家长受众访问", "insight",
         "InsightExplainRequest", "InsightExplainRequest", _set("context.audience", "agent")),
    Case("BFF 不说明代表谁", "安全 B1：BFF 必须带 actor", "insight",
         "InsightExplainRequest", "InsightExplainRequest", _set("context.actor", _DELETE)),
    Case("学生受众却以家长身份访问", "安全 B1：受众与身份一致", "insight",
         "InsightExplainRequest", "InsightExplainRequest",
         _set("context.actor", {"role": "guardian", "actor_ref": "00000000-0000-4000-8000-00000000a201",
                                "link_id": "00000000-0000-4000-8000-00000000a301"})),
    Case("家长访问不带绑定关系", "安全 B1、规则 5：按 guardian_student_link 授权", "insight",
         "InsightExportRequest", "InsightExportRequest", _set("context.actor.link_id", _DELETE)),
    Case("编排层带代表身份", "安全 B1：编排层以 Agent 受众访问，不代表任何人", "insight",
         "InsightContextRequest", "InsightContextRequest",
         _set("context.actor", {"role": "student", "actor_ref": "00000000-0000-4000-8000-00000000a101"})),
    Case("BFF 调用上下文包", "安全 B1：insight.context 只给编排层", "insight",
         "InsightContextRequest", "InsightContextRequest",
         _chain(_set("context.caller", "bff"), _set("context.audience", "student"),
                _set("context.actor", {"role": "student", "actor_ref": "00000000-0000-4000-8000-00000000a101"}))),
    Case("学生发起删除", "安全 B3：删除只给家长", "insight",
         "InsightEraseRequest", "InsightEraseRequest",
         _chain(_set("context.audience", "student"),
                _set("context.actor", {"role": "student", "actor_ref": "00000000-0000-4000-8000-00000000a101"}))),
    Case("删除不带确认码", "安全 B3：删除必须二次确认", "insight",
         "InsightEraseRequest", "InsightEraseRequest", _set("confirmation_code", _DELETE)),
    Case("删除回执的范围为空", "安全 B3：回执必须列出删除范围", "insight",
         "InsightEraseResponse", "InsightEraseResponse", _set("result.scopes", [])),
    Case("导出完成却不给下载凭据", "安全 B3：完成时必须给凭据与失效时间", "insight",
         "InsightExportResponse", "InsightExportResponse", _set("result.status", "ready")),
    Case("下载凭据是对象存储路径", "安全 B3：下载凭据不透明、不含学生标识", "insight",
         "InsightExportResponse", "InsightExportResponse",
         _chain(_set("result.status", "ready"), _set("result.expires_at", "2026-10-10T08:00:00Z"),
                _set("result.download_ref", "objstore://exports/00000000-0000-4000-8000-00000000a001.zip"))),
    Case("家长以学生自述的名义记录", "安全 B4、规则 6：来源不能冒充", "insight",
         "InsightRecordRequest", "InsightRecordRequest", _set("event.event_type", "self_report")),
    Case("家长替学生说“那次只是失误”", "安全 B4、规则 6：只能由学生本人提出", "insight",
         "InsightDisputeRequest", "InsightDisputeRequest",
         _chain(_set("kind", "one_off_slip"), _set("context.audience", "guardian"),
                _set("context.actor", {"role": "guardian", "actor_ref": "00000000-0000-4000-8000-00000000a201",
                                       "link_id": "00000000-0000-4000-8000-00000000a301"}))),
    Case("BFF 记录行为事件", "安全 B4：行为事件只由编排层记录", "insight",
         "InsightRecordRequest", "InsightRecordRequest",
         _set("event", {"event_type": "behavior", "kind": "session_start", "occurred_at": "2026-10-09T20:00:00+08:00"})),
    Case("取值像姓名与学校", "安全 S2：取值只能是小写编码", "insight",
         "InsightRecordRequest", "InsightRecordRequest", _set("event.value", "ZhangSan_Nanjing_No29_Middle")),
    Case("取值是手机号", "安全 S2：取值只能是小写编码", "insight",
         "InsightRecordRequest", "InsightRecordRequest", _set("event.value", "13812345678")),
    Case("BFF 调用 node.resolve", "安全 B5：只给编排层", "knowledge",
         "NodeResolveRequest", "NodeResolveRequest", _set("context.caller", "bff")),
    Case("node.resolve 声明为 L1", "安全 B5：学生文本按 L2", "knowledge",
         "NodeResolveRequest", "NodeResolveRequest", _set("context.data_class", "L1")),
    Case("公共层请求声明为 L3", "公共层只有 L1 / L2", "knowledge",
         "NodeGetRequest", "NodeGetRequest", _set("context.data_class", "L3")),
    Case("公共层响应引用学生证据", "安全 S1：公共层不引用学生证据", "knowledge",
         "NodeGetResponse", "NodeGetResponse", _set("evidence_ids", ["00000000-0000-4000-8000-00000000e101"])),
    Case("拍照证据声明为 L2", "安全 S3：照片、语音是 L3", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest",
         _chain(_set("evidences.0.channel", "photo"), _set("evidences.0.evidence_level", "B"))),
    Case("带原始素材引用却声明为 L2", "安全 S3：带原始素材的证据是 L3", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.draft.media_ref", "objstore://synthetic/p1.jpg")),
    Case("BFF 读取掌握状态", "个人层工具只给编排层与学习服务", "knowledge",
         "MasteryGetRequest", "MasteryGetRequest", _set("context.caller", "bff")),
    # ---------- 契约审查（2026-09-28） ----------
    Case("陈述不带展示方式", "契约审查 2：待验证只画虚线必须可强制", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.statement.display", _DELETE)),
    Case("成立的判断画虚线", "假设与成立画实线", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.statement.display", "dashed")),
    Case("陈述不带置信度说明", "SIE 第八章：家长看不到无置信度的推断", "insight",
         "InsightExplainResponse", "InsightExplainResponse", _set("result.statement.confidence_band", _DELETE)),
    Case("判断摘要不带来源", "契约审查 3、规则 6：每条数据标来源", "insight",
         "InsightClaimsResponse", "InsightClaimsResponse", _set("result.claims.0.source_type", _DELETE)),
    Case("推断类判断不带模型版本", "规则 6：推断记录算法版本", "insight",
         "InsightClaimsResponse", "InsightClaimsResponse", _set("result.claims.0.model_version", _DELETE)),
    Case("BFF 冻结考试预测", "契约审查 5：预测 v0.9 只给 Agent", "insight",
         "InsightPredictRequest", "InsightPredictRequest",
         _chain(_set("context.caller", "bff"), _set("context.audience", "guardian"),
                _set("context.actor", {"role": "guardian", "actor_ref": "00000000-0000-4000-8000-00000000a201",
                                       "link_id": "00000000-0000-4000-8000-00000000a301"}))),
    Case("BFF 读取对账卡", "契约审查 5：对账卡 v0.9 只给 Agent", "insight",
         "InsightReconcileRequest", "InsightReconcileRequest",
         _chain(_set("context.caller", "bff"), _set("context.audience", "guardian"),
                _set("context.actor", {"role": "guardian", "actor_ref": "00000000-0000-4000-8000-00000000a201",
                                       "link_id": "00000000-0000-4000-8000-00000000a301"}))),
    Case("拍照识别的内容把通道填成键盘", "契约审查 6：OCR 只能来自拍照", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.draft.kind", "ocr_transcription")),
    Case("语音转写把通道填成键盘", "契约审查 6：ASR 只能来自语音", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.draft.kind", "asr_transcription")),
    Case("拍照的外部题以 B 级写入", "技术蓝图 v2.1 §3.2：外部题最高 C 级", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest",
         _chain(_set("context.data_class", "L3"), _set("evidences.0.channel", "photo"),
                _set("evidences.0.evidence_level", "B"), _set("evidences.0.draft.item_ref", _DELETE))),
    Case("画布作答未经确认就是 A 级", "技术蓝图 v2.1 §4.2：回显确认后才是 A 级", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.channel", "canvas")),
    Case("未准入内容上的作答是 A 级", "未准入内容不能点亮星", "knowledge",
         "EvidenceWriteRequest", "EvidenceWriteRequest", _set("evidences.0.draft.admission", "pending_review")),
    Case("采分点结果不带地图版本", "规则 3：判分可按版本复算", "knowledge",
         "RubricGetResponse", "RubricGetResponse", _set("result.map_version", _DELETE)),
]
