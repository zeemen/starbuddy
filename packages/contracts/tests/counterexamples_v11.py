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
TQ = "TeachRequestAyaQuestion"
LO = "TeachRequestAyaLessonOpen"
LR = "TeachResponseAyaLessonOpen"
LLQ = "ListLessonsRequestAya"
LLR = "ListLessonsResponseAya"
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
    # ---- teach 的结构化提问（v1.1 草稿内追加，specs/stage-point-to-ask-answer PR ①）：不收自由文字、只能是讲评课、只含 id 与编码 ----
    Case("提问用在提示模式", "带 question 只能是 mode: lesson", "TeachRequest", TQ, _set("mode", "hint")),
    Case("提问同时带自由文字对话", "带 question 时 conversation 为空", "TeachRequest", TQ,
         _set("conversation", [{"turn_id": "00000000-0000-4000-8000-0000000000c2", "role": "student", "content": "（合成）为什么"}])),
    Case("提问夹带自由文字", "TeachQuestion 不收自由文字", "TeachRequest", TQ, _set("question.text", "（合成）这一步为什么要移项")),
    Case("提问夹带讲解 DSL", "讲解内容由平台按 lesson_ref 取，请求不携带", "TeachRequest", TQ, _set("question.dsl", {"steps": []})),
    Case("提问夹带学生作答", "提问对象不含学生数据", "TeachRequest", TQ, _set("question.answer_text", "（合成）x=3")),
    Case("提问意图是再讲一遍", "再讲一遍在前端本地处理，不进契约", "TeachRequest", TQ, _set("question.intent", "again")),
    Case("提问意图是任意文字", "意图只有三种编码", "TeachRequest", TQ, _set("question.intent", "why")),
    Case("锚点类型不在枚举里", "锚点只能是 element / slot / step", "TeachRequest", TQ, _set("question.anchor.kind", "node")),
    Case("锚点 id 不合 id 规则", "锚点 id 与讲解 DSL 的 ID_RE 一致", "TeachRequest", TQ, _set("question.anchor.id", "F1")),
    Case("锚点夹带坐标", "锚点只有 kind 与 id", "TeachRequest", TQ, _set("question.anchor.x", 40)),
    Case("提问步数为 0", "cursor 从 1 起", "TeachRequest", TQ, _set("question.cursor", 0)),
    Case("提问步数超过讲解 DSL 上限", "cursor ≤ 100", "TeachRequest", TQ, _set("question.cursor", 101)),
    Case("提问缺讲评课引用", "TeachQuestion 必填 lesson_ref", "TeachRequest", TQ, _set("question.lesson_ref", _DELETE)),
    Case("讲评课版本不是 x.y.z", "lesson_version 是语义化版本", "TeachRequest", TQ, _set("question.lesson_ref.lesson_version", "v1")),
    Case("讲评课引用夹带讲解内容", "LessonRef 只有 id 与版本", "TeachRequest", TQ, _set("question.lesson_ref.body", "（合成）")),
    Case("提问缺讲解 DSL 版本", "TeachQuestion 必填 dsl_version", "TeachRequest", TQ, _set("question.dsl_version", _DELETE)),
    Case("作答已提交写成字符串", "answer_submitted 是布尔", "TeachRequest", TQ, _set("answer_submitted", "yes")),
    # ---- 打开讲评课与台词稿（v1.1 草稿内追加，specs/stage-lesson-session ③b-1） ----
    Case("打开讲评课用在提示模式", "带 lesson_ref 只能是 mode: lesson", "TeachRequest", LO, _set("mode", "hint")),
    Case("打开讲评课同时带提问", "打开讲评课不同时带 question", "TeachRequest", LO,
         _set("question", {"lesson_ref": {"lesson_id": "syn.lesson.qf_0301", "lesson_version": "0.1.0"}, "dsl_version": "0.3.0",
                           "cursor": 1, "anchor": {"kind": "step", "id": "s1"}, "intent": "why_step"})),
    Case("打开讲评课带自由文字对话", "打开讲评课时 conversation 为空", "TeachRequest", LO,
         _set("conversation", [{"turn_id": "00000000-0000-4000-8000-0000000000c3", "role": "student", "content": "（合成）开始"}])),
    Case("讲评课引用夹带讲解内容（打开时）", "LessonRef 只有 id 与版本", "TeachRequest", LO, _set("lesson_ref.dsl", {"steps": []})),
    Case("台词稿挂在 text 片段上", "台词稿只跟着讲解 DSL 走", "TeachResponse", LR,
         lambda v: v["result"]["segments"].append({"type": "text", "text": "（合成）", "script": {"dsl_version": "0.3.0", "lines": []}})),
    Case("带台词稿的片段缺讲解 DSL", "台词稿与 dsl、dsl_version 同在", "TeachResponse", LR, _set("result.segments.0.dsl", _DELETE)),
    Case("台词稿夹带多余字段", "StageScript 不放开字段", "TeachResponse", LR, _set("result.segments.0.script.note", "（合成）")),
    Case("台词稿缺 lines", "StageScript 必填 lines", "TeachResponse", LR, _set("result.segments.0.script.lines", _DELETE)),
    Case("台词超过 60 个字", "台词每句 ≤ 60 码点", "TeachResponse", LR, _set("result.segments.0.script.lines.0.text", "（合成）" + "长" * 60)),
    Case("台词夹带 HTML", "台词是纯文本", "TeachResponse", LR, _set("result.segments.0.script.lines.0.text", "<b>（合成）重点</b>")),
    Case("台词夹带零宽字符", "台词不含不可见字符", "TeachResponse", LR, _set("result.segments.0.script.lines.0.text", "（合成）\u200b先看")),
    Case("台词角色不在名单里", "台词角色只有 teacher / buddy", "TeachResponse", LR, _set("result.segments.0.script.lines.0.role", "student")),
    Case("台词编号不合 id 规则", "台词编号与讲解 DSL 的 ID_RE 一致", "TeachResponse", LR, _set("result.segments.0.script.lines.0.id", "L1")),
    Case("台词夹带说话人姓名", "台词只有 id、role、text", "TeachResponse", LR, _set("result.segments.0.script.lines.0.speaker_name", "（合成）")),
    Case("答疑问法是再讲一遍", "答疑问法只有三种", "TeachResponse", LR, _set("result.segments.0.script.answers.0.intent", "again")),
    Case("答疑夹带正文", "答疑只引用台词编号", "TeachResponse", LR, _set("result.segments.0.script.answers.0.text", "（合成）因为……")),
    Case("答疑步骤不合 id 规则", "答疑步骤与讲解 DSL 的 ID_RE 一致", "TeachResponse", LR, _set("result.segments.0.script.answers.0.step", "S1")),
    # ---- 讲评课目录 list_lessons（v1.1 草稿内追加（再续），specs/lesson-catalog） ----
    Case("目录请求带学生 ID", "目录与学生无关（PresentItemContext）", "ListLessonsRequest", LLQ,
         _set("context.student_ref", "00000000-0000-4000-8000-00000000a7ff")),
    Case("目录请求带计量 ID", "目录只读、不计量", "ListLessonsRequest", LLQ, _set("context.usage_id", "00000000-0000-4000-8000-00000000b7ff")),
    Case("目录请求缺内容版本", "ListLessonsRequest 必填 content_version", "ListLessonsRequest", LLQ, _set("content_version", _DELETE)),
    Case("目录请求夹带讲评课编号过滤", "目录请求只有 context 与 content_version", "ListLessonsRequest", LLQ, _set("skill_node", "aya:synthetic-0301")),
    Case("目录条目夹带讲解 DSL", "目录不含讲解内容", "ListLessonsResponse", LLR, _set("result.lessons.0.dsl", {"steps": []})),
    Case("目录条目夹带台词稿", "目录不含台词与答疑", "ListLessonsResponse", LLR, _set("result.lessons.0.script", {"dsl_version": "0.3.0", "lines": []})),
    Case("目录条目夹带答案", "目录不含答案", "ListLessonsResponse", LLR, _set("result.lessons.0.answer", ["2(x-1)"])),
    Case("目录条目缺标题", "LessonCatalogEntry 必填 title", "ListLessonsResponse", LLR, _set("result.lessons.0.title", _DELETE)),
    Case("目录标题夹带 HTML", "标题是纯文本", "ListLessonsResponse", LLR, _set("result.lessons.0.title", "<b>（合成）讲评</b>")),
    Case("目录标题超过 40 个字", "标题 ≤ 40（packages/stage 的标题上限）", "ListLessonsResponse", LLR,
         _set("result.lessons.0.title", "（合成）" + "长" * 40)),
    Case("目录标题夹带换行", "标题不收换行", "ListLessonsResponse", LLR, _set("result.lessons.0.title", "（合成）第一行\n第二行")),
    Case("目录步数为 0", "step_count 从 1 起", "ListLessonsResponse", LLR, _set("result.lessons.0.step_count", 0)),
    Case("目录步数超过讲解 DSL 上限", "step_count ≤ 100", "ListLessonsResponse", LLR, _set("result.lessons.0.step_count", 101)),
    Case("目录产生证据", "目录不产生证据", "ListLessonsResponse", LLR, _add_evidence),
    Case("目录信封外夹带评语", "信封之外不允许任何字段", "ListLessonsResponse", LLR, _set("commentary", "（合成）推荐你看这节")),
    Case("目录结果夹带推荐理由", "目录不写关于学生的陈述", "ListLessonsResponse", LLR, _set("result.recommended_for", "（合成）薄弱节点")),
]
