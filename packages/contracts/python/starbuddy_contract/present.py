"""present_item 题面的平台侧白名单（契约 1.1.0 候选版，PLT-12：specs/present-item-contract/spec.md）。

题面只有题干（受限富文本）、题型、选项、作答格式、版权状态与内容版本，**不含**答案、解析、采分点、Rubric、提示与错因陷阱。
契约 schema 已经在每一层用 additionalProperties: false 禁止多余字段；这里是消费方（学习服务）收到后的第二道：
按白名单逐字段重建，任何不在白名单里的键都整体拒绝（不是静默丢弃，免得适配器把答案藏进新字段时没人发现）。
契约检查（scripts/check_contract.py）与一致性测试（conformance）也用这里的答案类字段名黑名单，三处只有这一份。
纯函数，不连库、不读时钟。错误消息只含编码化说明，不含题面或作答原文。
"""
from __future__ import annotations

import re

from .checks import ContractViolation

# 答案类字段名（契约检查对整棵 PresentItemResponse schema 树、一致性测试与学习服务对实例都查）。题面里出现任何一个都算泄露
ANSWER_FIELD_NAMES = frozenset({
    "answer", "answers", "answer_key", "answer_keys", "correct", "is_correct", "correct_answer", "correct_answers",
    "correct_option", "correct_options", "correct_choice", "correct_choices", "standard_answer", "reference_answer",
    "solution", "solutions", "worked_solution", "explanation", "explanations", "analysis", "rationale",
    "hint", "hints", "hint_path", "hint_ladder", "rubric", "rubric_version", "rubric_ref", "scoring_point", "scoring_points",
    "points", "key_points", "grading", "grading_rule", "score", "max_score", "traps", "trap", "wrong_answer_traps",
    "distractor_reason", "error_cause",
})

ITEM_TYPES = ("single_choice", "multi_choice", "fill_blank", "short_answer")
CONTENT_STATES = ("complete", "withheld")
LICENSE_STATES = ("unverified", "licensed", "original", "public", "restricted")
ANSWER_KINDS = ("choice", "number", "expression", "text")

# 与契约 RichTextRun.text / RichMathRun.latex / RichImageRun 同一写法（契约改了这里要一起改；check_contract 的探针会对照）
# 文字与图片说明另排除 C1 控制字符、零宽与方向标记、行/段分隔符、双向文本控制符与 BOM（审查建议 4：显示内容与实际内容不一致）
_PLAIN = r"[^<>\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]"
TEXT_RE = re.compile(_PLAIN + r"{1,2000}")
ALT_RE = re.compile(_PLAIN + r"{1,200}")
LATEX_RE = re.compile(r"(?!.*\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData|def|gdef|edef|xdef|let|newcommand"
                      r"|renewcommand|providecommand|newenvironment|input|include|write|immediate|catcode|csname)(?![A-Za-z]))[\x20-\x7e]{1,1000}")
IMAGE_REF_RE = re.compile(r"objstore://[a-z0-9][a-z0-9/_.\-]{0,255}")
OPTION_KEY_RE = re.compile(r"[A-Za-z0-9]{1,8}")
ITEM_REF_RE = re.compile(r"(wenli|aya|zhixing|echo):[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")
VERSIONED_RE = re.compile(r"[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}")
MAX_PARAGRAPHS, MAX_RUNS, MAX_OPTIONS, MAX_OPTION_RUNS = 20, 64, 10, 32
# 题面总字数上限（题干与选项里全部 text、latex、alt 按码点合计；契约 PresentedItem 的说明，schema 表达不了）
MAX_TOTAL_CHARS = 8000


def total_chars(item: dict) -> int:
    """题面总字数：题干与选项全部片段的 text、latex、alt 长度之和（按 Unicode 码点）。"""
    runs = [r for p in item.get("stem") or [] for r in p.get("runs", [])]
    runs += [r for o in item.get("options") or [] for r in o.get("content") or []]
    return sum(len(r.get("text", "")) + len(r.get("latex", "")) + len(r.get("alt", "")) for r in runs)


def answer_field_paths(node: object, path: str = "") -> list[str]:
    """实例里所有答案类字段名出现的位置（只报路径，不带值）。"""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}/{k}"
            if isinstance(k, str) and k.lower() in ANSWER_FIELD_NAMES:
                out.append(here)
            out.extend(answer_field_paths(v, here))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(answer_field_paths(v, f"{path}/{i}"))
    return out


def _bad(reason: str) -> ContractViolation:
    return ContractViolation(f"present_item: {reason}")


def _exact_keys(obj: object, required: set[str], optional: set[str], where: str) -> dict:
    if not isinstance(obj, dict):
        raise _bad(f"{where} is not an object")
    keys = set(obj)
    if not required <= keys or keys - required - optional:
        raise _bad(f"{where} has fields outside the whitelist")
    return obj


def _run(run: object, where: str) -> dict:
    t = run.get("type") if isinstance(run, dict) else None
    if t == "text":
        r = _exact_keys(run, {"type", "text"}, set(), where)
        if not isinstance(r["text"], str) or not TEXT_RE.fullmatch(r["text"]):
            raise _bad(f"{where}.text is not plain text")
        return {"type": "text", "text": r["text"]}
    if t == "math":
        r = _exact_keys(run, {"type", "latex"}, set(), where)
        if not isinstance(r["latex"], str) or not LATEX_RE.fullmatch(r["latex"]):
            raise _bad(f"{where}.latex is not allowed")
        return {"type": "math", "latex": r["latex"]}
    if t == "image":
        r = _exact_keys(run, {"type", "image_ref"}, {"alt"}, where)
        if not isinstance(r["image_ref"], str) or not IMAGE_REF_RE.fullmatch(r["image_ref"]):
            raise _bad(f"{where}.image_ref is not an object-store reference")
        out = {"type": "image", "image_ref": r["image_ref"]}
        if "alt" in r:
            if not isinstance(r["alt"], str) or not ALT_RE.fullmatch(r["alt"]):
                raise _bad(f"{where}.alt is not plain text")
            out["alt"] = r["alt"]
        return out
    raise _bad(f"{where} has an unknown run type")


def _runs(runs: object, where: str, limit: int) -> list[dict]:
    if not isinstance(runs, list) or not 1 <= len(runs) <= limit:
        raise _bad(f"{where} must be a non-empty list")
    return [_run(r, f"{where}[{i}]") for i, r in enumerate(runs)]


def project_presented_item(result: object) -> dict:
    """按白名单重建 PresentedItem（契约 1.1.0）。任何多余字段、答案类字段、超出受限富文本的内容 → ContractViolation。
    只做结构与字段白名单；题型与作答格式的组合由契约 schema 约束（调用方先按契约校验）。"""
    if answer_field_paths(result):
        raise _bad("result carries answer-like fields")
    r = _exact_keys(result, {"item_ref", "item_version", "item_type", "content_state", "answer_format", "license_state"},
                    {"stem", "options"}, "result")
    if not isinstance(r["item_ref"], str) or not ITEM_REF_RE.fullmatch(r["item_ref"]):
        raise _bad("item_ref is not an item reference")
    if not isinstance(r["item_version"], str) or not VERSIONED_RE.fullmatch(r["item_version"]):
        raise _bad("item_version is not name@version")
    if r["item_type"] not in ITEM_TYPES or r["content_state"] not in CONTENT_STATES or r["license_state"] not in LICENSE_STATES:
        raise _bad("item_type, content_state or license_state is not a known code")
    out: dict = {"item_ref": r["item_ref"], "item_version": r["item_version"], "item_type": r["item_type"],
                 "content_state": r["content_state"], "license_state": r["license_state"]}
    complete = r["content_state"] == "complete"
    if complete != ("stem" in r):
        raise _bad("stem must be present exactly when content_state is complete")
    if "stem" in r:
        stem = r["stem"]
        if not isinstance(stem, list) or not 1 <= len(stem) <= MAX_PARAGRAPHS:
            raise _bad("stem must be a non-empty list of paragraphs")
        out["stem"] = []
        for i, p in enumerate(stem):
            para = _exact_keys(p, {"type", "runs"}, set(), f"stem[{i}]")
            if para["type"] != "paragraph":
                raise _bad(f"stem[{i}] is not a paragraph")
            out["stem"].append({"type": "paragraph", "runs": _runs(para["runs"], f"stem[{i}].runs", MAX_RUNS)})
    if "options" in r:
        opts = r["options"]
        if not isinstance(opts, list) or not 2 <= len(opts) <= MAX_OPTIONS:
            raise _bad("options must list 2 to 10 options")
        out["options"] = []
        for i, o in enumerate(opts):
            opt = _exact_keys(o, {"key"}, {"content"}, f"options[{i}]")
            if not isinstance(opt["key"], str) or not OPTION_KEY_RE.fullmatch(opt["key"]):
                raise _bad(f"options[{i}].key is not an option key")
            if complete != ("content" in opt):
                raise _bad(f"options[{i}].content must be present exactly when content_state is complete")
            item = {"key": opt["key"]}
            if "content" in opt:
                item["content"] = _runs(opt["content"], f"options[{i}].content", MAX_OPTION_RUNS)
            out["options"].append(item)
        if len({o["key"] for o in out["options"]}) != len(out["options"]):
            raise _bad("option keys repeat")
    af = _exact_keys(r["answer_format"], {"kind"}, {"min_choices", "max_choices", "max_length"}, "answer_format")
    if af["kind"] not in ANSWER_KINDS:
        raise _bad("answer_format.kind is not a known code")
    out["answer_format"] = {"kind": af["kind"]}
    for k in ("min_choices", "max_choices", "max_length"):
        if k in af:
            v = af[k]
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise _bad(f"answer_format.{k} is not a positive integer")
            out["answer_format"][k] = v
    if total_chars(out) > MAX_TOTAL_CHARS:
        raise _bad(f"presented item exceeds {MAX_TOTAL_CHARS} characters in total")
    return out


def plain_text(runs: list[dict]) -> str:
    """片段的纯文字形态（给不渲染富文本的旧客户端作兜底）：文字原样，公式写 LaTeX 源码，图片写 [图]。"""
    parts = []
    for r in runs:
        if r["type"] == "text":
            parts.append(r["text"])
        elif r["type"] == "math":
            parts.append(r["latex"])
        else:
            parts.append("[图]")
    return "".join(parts)
