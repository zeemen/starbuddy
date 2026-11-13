"""知识服务契约 v0.10 候选版（KGE-12 B，standard.search）的反例：从 v0.10 的合法示例出发只改一处，必须被 v0.10 拒绝。

覆盖 specs/kge-12-weknora-integration/spec.md 第一节 B：缺必填过滤维度不检索（先过滤后检索）；上下文是公共层、不带学生标识、
数据级别固定 L2；请求与命中都不放开多余字段；命中里没有答案类字段（规则 3）；公共层不引用学生证据；统一信封字段齐全。
全部为合成数据（CLAUDE.md 规则 8）。
"""
from __future__ import annotations

from counterexamples import _DELETE, Case, _set

# 注意：Case.build 用 mutate 的返回值（不为 None 时）当实例，所以删字段一律用 _set(路径, _DELETE)

REQ = "StandardSearchRequest"
RESP = "StandardSearchResponse"
EMPTY = "StandardSearchResponseEmpty"
STUDENT = "00000000-0000-4000-8000-00000000a001"
EVIDENCE = "00000000-0000-4000-8000-00000000e101"


def _many_hits(v):
    hit = v["result"]["hits"][0]
    v["result"]["hits"] = [{**hit, "content_id": f"syn-zt:tb-{i:04d}", "weknora_id": f"syn-wk-{i:04d}"} for i in range(21)]


V010_CASES = [
    # ---- 先过滤后检索：六个教材维度缺一不可（ADR 16 第 3.2 节） ----
    *[Case(f"过滤条件缺 {dim}", "ADR 16 第 3.2 节：缺必填维度返回 400，不做全库检索", REQ, REQ, _set(f"filters.{dim}", _DELETE))
      for dim in ("subject", "grade", "province", "publisher", "edition", "semester")],
    Case("没有过滤条件", "ADR 16 第 3.2 节：先过滤后检索", REQ, REQ, _set("filters", _DELETE)),
    Case("过滤条件夹带未定义维度", "过滤维度是封闭清单", REQ, REQ, _set("filters.school", "syn-school")),
    Case("年级超出初高中", "过滤维度取值受约束", REQ, REQ, _set("filters.grade", 6)),
    Case("学期取值不在枚举里", "过滤维度取值受约束", REQ, REQ, _set("filters.semester", "summer")),
    Case("省份编码含大写与空格", "编码值是小写字母数字与连字符", REQ, REQ, _set("filters.province", "Syn Province")),
    Case("节点过滤为空数组", "node_ids 给了就至少一个", REQ, REQ, _set("filters.node_ids", [])),
    # ---- 请求的其余字段 ----
    Case("请求夹带多余字段", "请求不放开多余字段", REQ, REQ, _set("raw_filter", "subject = 'aya'")),
    Case("检索文本为空", "query 非空", REQ, REQ, _set("query", "")),
    Case("检索文本超长", "query 至多 500 字", REQ, REQ, _set("query", "合" * 501)),
    Case("没有内容类型", "content_types 必填", REQ, REQ, _set("content_types", _DELETE)),
    Case("内容类型为空数组", "content_types 至少一个", REQ, REQ, _set("content_types", [])),
    Case("内容类型不在枚举里", "content_types 是封闭清单", REQ, REQ, _set("content_types", ["student_note"])),
    Case("top_k 超过 20", "top_k 1–20", REQ, REQ, _set("top_k", 50)),
    Case("top_k 为 0", "top_k 1–20", REQ, REQ, _set("top_k", 0)),
    # ---- 上下文：公共层、不带学生标识、L2（规则 13；ADR 16 第 3.5 节） ----
    Case("上下文带学生标识", "规则 13：公共层工具不带学生标识", REQ, REQ, _set("context.student_ref", STUDENT)),
    Case("数据级别标成 L1", "ADR 16 第 3.5 节：查询文本可能来自学生，按 L2", REQ, REQ, _set("context.data_class", "L1")),
    Case("学习服务直接调用", "standard.search 只给 BFF 与编排层", REQ, REQ, _set("context.caller", "learning")),
    Case("请求的契约版本还是 0.9.0", "v0.10 的请求只认 0.10.0", REQ, REQ, _set("context.contract_version", "0.9.0")),
    # ---- 命中：不放开多余字段，没有答案类字段（规则 3） ----
    Case("命中夹带答案", "规则 3：WeKnora 的答案只是讲解素材，不以答案字段交出", RESP, RESP, _set("result.hits.0.answer", "（合成）B")),
    Case("命中夹带解析", "规则 3：不以解析字段交出", RESP, RESP, _set("result.hits.0.solution", "（合成）先求交点")),
    Case("命中夹带学生标识", "规则 13：公共层结果不含个人层字段", RESP, RESP, _set("result.hits.0.student_ref", STUDENT)),
    Case("命中缺中台内容 ID", "ID 映射：命中必须带中台内容 ID", RESP, RESP, _set("result.hits.0.content_id", _DELETE)),
    Case("命中缺 WeKnora 文档 ID", "ID 映射：命中必须带 WeKnora 文档 ID", RESP, RESP, _set("result.hits.0.weknora_id", _DELETE)),
    Case("命中缺来源", "中台需求单 ZT-13：来源字段必带", RESP, RESP, _set("result.hits.0.source", _DELETE)),
    Case("命中分数大于 1", "score 在 0–1", RESP, RESP, _set("result.hits.0.score", 1.5)),
    Case("命中正文为空", "text 非空", RESP, RESP, _set("result.hits.0.text", "")),
    Case("来源夹带未定义字段", "来源字段是封闭清单", RESP, RESP, _set("result.hits.0.source.url", "https://example.invalid/")),
    Case("编译页引用缺页面版本", "page_ref 必须能定位到页面版本", RESP, RESP, _set("result.hits.1.page_ref.page_version", _DELETE)),
    Case("命中超过 20 条", "hits 至多 20 条", RESP, RESP, _many_hits),
    Case("结果夹带多余字段", "result 不放开多余字段", RESP, RESP, _set("result.total", 2)),
    # ---- 统一信封（规则 3 的五项溯源字段） ----
    Case("公共层结果引用学生证据", "公共层不引用任何学生证据", RESP, RESP, _set("evidence_ids", [EVIDENCE])),
    Case("缺 source_version", "规则 3：返回必须带 source_version", RESP, EMPTY, _set("source_version", _DELETE)),
    Case("source_version 类别不合法", "source_version 是 类别:名称@版本", RESP, EMPTY, _set("source_version", "weknora:syn-std-kb")),
    Case("缺 human_review_required", "规则 3：返回必须带 human_review_required", RESP, EMPTY,
         _set("human_review_required", _DELETE)),
    Case("信封夹带多余字段", "unevaluatedProperties: false", RESP, EMPTY, _set("weknora_raw", {"chunks": []})),
]
