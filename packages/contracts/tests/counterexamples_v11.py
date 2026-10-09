"""v1.1 候选版（PLT-12 present_item）的反例：从 v1.1 的合法示例出发只改一处，必须被 v1.1 拒绝。

覆盖 spec 范围第 1、2 条：题面不得含答案、解析、采分点、Rubric（“答案类字段名”反例）；受限富文本不能夹带 HTML、链接与危险
LaTeX；不产生证据；只读、非消耗类（不带 usage_id）；题型、选项与作答格式的组合；版权受限与题干缺失时不给题面。
全部为合成数据（CLAUDE.md 规则 8）。
"""
from __future__ import annotations

from counterexamples import _DELETE, Case, _set

# 注意：Case.build 用 mutate 的返回值（不为 None 时）当实例，所以删字段一律用 _set(路径, _DELETE)，不用会返回值的 dict.pop

REQ = "PresentItemRequestAya"
RESP = "PresentItemResponseAya"
WITHHELD = "PresentItemResponseAyaWithheld"
ZX = "PresentItemResponseZhixing"
EVIDENCE = "00000000-0000-4000-8000-00000000e701"


def _add_evidence(v):
    v["evidence_ids"] = [EVIDENCE]
    v["evidence"] = [{"evidence_id": EVIDENCE, "kind": "interaction_signal", "occurred_at": "2026-10-09T10:00:00+08:00",
                      "admission": "admitted", "payload": {"signal_code": "item_presented"}}]


def _run(run: dict):
    return lambda v: v["result"]["stem"][0]["runs"].append(run)


V11_CASES = [
    # ---- 答案类字段（spec 范围第 1 条：不得含答案、解析、采分点、rubric） ----
    Case("题面结果夹带答案", "PLT-12：题面不含答案", "PresentItemResponse", RESP, _set("result.answer", ["A"])),
    Case("题面结果夹带正确选项", "PLT-12：题面不含答案", "PresentItemResponse", RESP, _set("result.correct_options", ["A"])),
    Case("选项夹带 is_correct", "PLT-12：选项没有“是否正确”", "PresentItemResponse", RESP,
         lambda v: v["result"]["options"][0].__setitem__("is_correct", True)),
    Case("题面结果夹带解析", "PLT-12：题面不含解析", "PresentItemResponse", RESP, _set("result.explanation", "（合成）先提公因式")),
    Case("题面结果夹带采分点", "PLT-12：题面不含采分点", "PresentItemResponse", ZX,
         _set("result.scoring_points", [{"point_id": "p1", "score": 2}])),
    Case("题面结果夹带 rubric_version", "PLT-12：题面不含 Rubric", "PresentItemResponse", ZX,
         _set("result.rubric_version", "zhixing-m3@0.1.0")),
    Case("题面结果夹带提示阶梯", "PLT-12：提示只经 teach 按等级给", "PresentItemResponse", RESP,
         _set("result.hint_path", ["（合成）提示"])),
    Case("作答格式夹带标准答案", "PLT-12：作答格式不得从答案推出", "PresentItemResponse", ZX,
         _set("result.answer_format.answer", "（合成）答案")),
    Case("信封外夹带答案", "PLT-12：信封外不允许字段", "PresentItemResponse", RESP, _set("answer", ["A"])),
    Case("段落夹带解析", "PLT-12：段落只有 type 与 runs", "PresentItemResponse", RESP,
         lambda v: v["result"]["stem"][0].__setitem__("analysis", "（合成）解析")),
    # ---- 不产生证据 ----
    Case("题面产生证据", "PLT-12：只读，不产生证据", "PresentItemResponse", RESP, _add_evidence),
    Case("题面引用证据 ID", "PLT-12：只读，不产生证据", "PresentItemResponse", RESP, _set("evidence_ids", [EVIDENCE])),
    # ---- 受限富文本 ----
    Case("文字片段含 HTML 标签", "PLT-12：无 HTML 注入", "PresentItemResponse", RESP,
         _run({"type": "text", "text": "<img src=x onerror=alert(1)>"})),
    Case("片段类型是 html", "PLT-12：只有文字、公式、图片三种片段", "PresentItemResponse", RESP,
         _run({"type": "html", "html": "<b>x</b>"})),
    Case("片段类型是 link", "PLT-12：没有链接", "PresentItemResponse", RESP, _run({"type": "link", "href": "https://x.invalid"})),
    Case("公式含 \\href", "PLT-12：公式不能带链接", "PresentItemResponse", RESP,
         _run({"type": "math", "latex": "\\href{https://x.invalid}{x}"})),
    Case("公式含宏定义", "PLT-12：公式不能定义宏", "PresentItemResponse", RESP, _run({"type": "math", "latex": "\\def\\a{1}\\a"})),
    Case("公式含中文", "PLT-12：公式只允许可打印 ASCII", "PresentItemResponse", RESP, _run({"type": "math", "latex": "解：x=1"})),
    Case("图片引用是公网 URL", "PLT-12：图片只给对象存储引用", "PresentItemResponse", RESP,
         _run({"type": "image", "image_ref": "https://x.invalid/a.png"})),
    Case("图片说明含 HTML", "PLT-12：图片说明是纯文本", "PresentItemResponse", RESP,
         _run({"type": "image", "image_ref": "objstore://synthetic/aya/fig-01.png", "alt": "<b>图</b>"})),
    Case("文字片段含 RLO 双向控制符", "PLT-12 审查：文字不得含双向文本控制符", "PresentItemResponse", RESP,
         _run({"type": "text", "text": "（合成）x=1\u202e2=y"})),
    Case("文字片段含 LRI 隔离符", "PLT-12 审查：文字不得含双向隔离符", "PresentItemResponse", RESP,
         _run({"type": "text", "text": "a\u2066b"})),
    Case("文字片段含零宽空格", "PLT-12 审查：文字不得含零宽字符", "PresentItemResponse", RESP, _run({"type": "text", "text": "a\u200bb"})),
    Case("文字片段含 BOM", "PLT-12 审查：文字不得含 U+FEFF", "PresentItemResponse", RESP, _run({"type": "text", "text": "a\ufeffb"})),
    Case("文字片段含行分隔符", "PLT-12 审查：文字不得含 U+2028", "PresentItemResponse", RESP, _run({"type": "text", "text": "a\u2028b"})),
    Case("文字片段含 C1 控制字符", "PLT-12 审查：文字不得含 U+0080–009F", "PresentItemResponse", RESP,
         _run({"type": "text", "text": "a\u0085b"})),
    Case("图片说明含双向控制符", "PLT-12 审查：alt 同文字片段", "PresentItemResponse", RESP,
         _run({"type": "image", "image_ref": "objstore://synthetic/aya/fig-01.png", "alt": "a\u202db"})),
    Case("文字片段带样式", "PLT-12：没有样式", "PresentItemResponse", RESP,
         lambda v: v["result"]["stem"][0]["runs"][0].__setitem__("style", "color:red")),
    Case("空段落", "PLT-12：段落至少一个片段", "PresentItemResponse", RESP, _set("result.stem.0.runs", [])),
    # ---- 题面完整性、版权与题型 ----
    Case("withheld 却给题干", "PLT-12：题干缺失时不给题面", "PresentItemResponse", RESP, _set("result.content_state", "withheld")),
    Case("withheld 却给选项内容", "PLT-12：题干缺失时不给选项内容", "PresentItemResponse", WITHHELD,
         lambda v: v["result"]["options"][0].__setitem__("content", [{"type": "text", "text": "（合成）"}])),
    Case("版权受限仍给题面", "PLT-12：restricted 的题不给题面", "PresentItemResponse", RESP, _set("result.license_state", "restricted")),
    Case("完整题面缺题干", "PLT-12：complete 必须有题干", "PresentItemResponse", ZX, _set("result.stem", _DELETE)),
    Case("完整题面的选项缺内容", "PLT-12：complete 的选项都要有内容", "PresentItemResponse", RESP,
         _set("result.options.1.content", _DELETE)),
    Case("选择题缺选项", "PLT-12：选择题必须给选项", "PresentItemResponse", RESP, _set("result.options", _DELETE)),
    Case("简答题给选项", "PLT-12：非选择题不给选项", "PresentItemResponse", ZX,
         _set("result.options", [{"key": "A", "content": [{"type": "text", "text": "甲"}]},
                                 {"key": "B", "content": [{"type": "text", "text": "乙"}]}])),
    Case("单选允许选两项", "PLT-12：单选只能选一项", "PresentItemResponse", RESP, _set("result.answer_format.max_choices", 2)),
    Case("选择题的作答格式写成数字", "PLT-12：选择题的作答是选项", "PresentItemResponse", RESP,
         _set("result.answer_format", {"kind": "number", "max_length": 20})),
    Case("简答题的作答格式写成表达式", "PLT-12：简答题的作答是文字", "PresentItemResponse", ZX,
         _set("result.answer_format", {"kind": "expression", "max_length": 200})),
    Case("文字作答缺长度上限", "PLT-12：非选项作答给最大长度", "PresentItemResponse", ZX, _set("result.answer_format", {"kind": "text"})),
    Case("题型不在枚举里", "PLT-12：题型是编码", "PresentItemResponse", ZX, _set("result.item_type", "essay")),
    Case("版权状态写成 cleared", "PLT-12：版权状态与知识服务同一枚举", "PresentItemResponse", ZX, _set("result.license_state", "cleared")),
    Case("题面版本是自由文本", "PLT-12：item_version 是 名称@版本", "PresentItemResponse", ZX, _set("result.item_version", "最新版")),
    Case("选项键夹带文字", "PLT-12：选项键是编码", "PresentItemResponse", RESP, _set("result.options.0.key", "A 正确")),
    # ---- 请求：只读、非消耗类、前缀一致 ----
    Case("题面请求带 usage_id", "PLT-12：present_item 不计量", "PresentItemRequest", REQ,
         _set("context.usage_id", "00000000-0000-4000-8000-00000000b701")),
    Case("题面请求缺 content_version", "PLT-12：入参含 content_version", "PresentItemRequest", REQ, _set("content_version", _DELETE)),
    Case("题面请求缺 item_ref", "PLT-12：入参含 item_ref", "PresentItemRequest", REQ, _set("item_ref", _DELETE)),
    Case("题面请求带学生 ID", "PLT-12 审查：题面与学生无关，PresentItemContext 不带 student_ref", "PresentItemRequest", REQ,
         _set("context.student_ref", "00000000-0000-4000-8000-00000000a002")),
    Case("题面请求的数据级别是 L0", "PLT-12 审查：题面请求最低 L1", "PresentItemRequest", REQ, _set("context.data_class", "L0")),
    Case("题面请求的幂等键不是 UUIDv4", "幂等键可选，但给了就是小写 UUIDv4", "PresentItemRequest", REQ,
         _set("context.idempotency_key", "00000000-0000-1000-8000-00000000c701")),
    Case("题面请求缺 request_id", "PresentItemContext 必填 request_id", "PresentItemRequest", REQ, _set("context.request_id", _DELETE)),
    Case("题面请求上下文夹带多余字段", "PresentItemContext 不放开字段", "PresentItemRequest", REQ, _set("context.student_name", "（合成）")),
    Case("题面请求引用别科题目", "N7：请求学科与 item_ref 前缀一致", "PresentItemRequest", REQ, _set("item_ref", "zhixing:synthetic-m3-01")),
    Case("题面请求的内容版本是别科", "N7：内容版本的学科与请求一致", "PresentItemRequest", REQ, _set("content_version.subject", "wenli")),
    Case("题面请求按 1.0.0 组装", "present_item 只在 1.1.0", "PresentItemRequest", REQ, _set("context.contract_version", "1.0.0")),
    Case("题面请求要渲染成 HTML", "PLT-12：没有渲染格式参数，只有受限富文本", "PresentItemRequest", REQ, _set("render", "html")),
    # ---- 能力声明 ----
    Case("能力声明缺 present_item", "v1.1：能力声明逐一声明七个方法", "Capabilities", "CapabilitiesAya",
         _set("methods.present_item", _DELETE)),
]
