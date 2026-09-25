"""适配器结果的平台核对（spec 范围第 4 条）。纯函数，不连库：登记情况由调用方查好传进来。

任何一条不通过，就抛出 rejected(...)，整体拒绝、一行不写。通过则返回 Verified：
落库要用的证据（准入状态已按平台改写）、效力、复核标记、作答 ↔ 作答证据的对应关系。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from .contract import plugin_contract
from .errors import rejected

# 准入状态由松到严（契约 Admission；aya adapter-design §3.1）
ADMISSION_RANK = {"admitted": 0, "pending_review": 1, "source_unverified": 2, "rejected": 3}
RESPONSE_KINDS = ("response_text", "response_choice", "ocr_transcription", "asr_transcription")
TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d{1,6})?([Zz]|[+-]\d{2}:\d{2})")
# 这些证据含照片、语音转写，属于 L3（ADR 09 §2；契约 RequestContext.data_class）
L3_KINDS = ("ocr_transcription", "asr_transcription", "speech_features")


def local_id(ref: str) -> str:
    return ref.split(":", 1)[1]


def parse_time(s: str) -> datetime:
    """RFC 3339 时间，必须带时区。jsonschema 在没装格式插件时不检查 date-time，
    Postgres 又接受 epoch / infinity 这类值，所以这里自己严格解析（安全审查 S4）。"""
    if not isinstance(s, str) or not TIME_RE.fullmatch(s):
        raise ValueError("occurred_at is not an RFC 3339 date-time")
    t = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("occurred_at must carry a time zone")
    return t


def _walk(node: object, key: str):
    """深度遍历，产出所有名为 key 的字段值。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)


def _numbers(node: object):
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield float(node)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _numbers(v)
    elif isinstance(node, list):
        for v in node:
            yield from _numbers(v)


def _request_media_refs(request: dict) -> set[str]:
    refs: set[str] = set()
    attempts = [request["attempt"]] if "attempt" in request else request.get("attempts", [])
    for a in attempts:
        for k in ("image_ref", "transcript_ref"):
            if k in a["response"]:
                refs.add(a["response"][k])
    refs.update(request.get("paper_image_refs", []))
    refs.update(p["media_ref"] for p in request.get("prior_evidence", []) if "media_ref" in p)
    return refs


def collect_refs(response: dict) -> tuple[set[str], set[str]]:
    """响应里引用的题目与能力点（带学科前缀），用于查平台登记。"""
    items = {v for v in _walk(response, "item_ref") if isinstance(v, str)}
    nodes = {v for v in _walk(response, "skill_node") if isinstance(v, str)}
    return items, nodes


@dataclass
class Verified:
    evidence: list[dict]
    semantics: str
    human_review_required: bool
    review_reasons: list[str]
    # 第 i 道作答 → 它的作答证据 ID
    response_evidence_ids: list[str]
    admission_rewritten: list[str] = field(default_factory=list)


def verify(
    method: str,
    request: dict,
    response: dict,
    *,
    registered_items: dict[str, str],
    registered_nodes: set[str],
    prior_admission: dict[str, str] | None = None,
) -> Verified:
    """registered_items：local item_id → 平台准入状态；registered_nodes：已登记的 local skill id。"""
    subject = request["context"]["subject"]
    schema = "GradeResponse" if method == "grade" else "DiagnoseResponse"

    # 1. 契约 schema
    errors = plugin_contract().validate(schema, response)
    if errors:
        raise rejected("contract: " + "; ".join(errors[:10]))

    # 1b. 数值必须有限；时间必须可解析（NaN 能穿过 schema 的 minimum/maximum）
    for v in _numbers(response):
        if not math.isfinite(v):
            raise rejected("non-finite number in response")
    for e in response.get("evidence", []):
        try:
            parse_time(e["occurred_at"])
        except ValueError:
            raise rejected("evidence occurred_at is not a valid date-time") from None

    # 1c. 契约写明由平台校验的项（契约守护 / 安全审查 S2）
    if method == "grade":
        if response["result"]["rubric_version"] != request["rubric_version"]:
            raise rejected("result.rubric_version differs from the request")

    evidence: list[dict] = [dict(e) for e in response.get("evidence", [])]
    new_ids = [e["evidence_id"] for e in evidence]
    if len(set(new_ids)) != len(new_ids):
        raise rejected("duplicate evidence_id in response")

    # 2. 证据 ID 闭合：引用只能指向本次证据或请求里的既有证据
    # 既有证据只认平台库里查到的（契约守护阻断 1）：请求里自带的 admission 不作数
    prior_admission = prior_admission or {}
    prior = {p["evidence_id"]: {**p, "admission": prior_admission[p["evidence_id"]]}
             for p in request.get("prior_evidence", []) if p["evidence_id"] in prior_admission}
    allowed = set(new_ids) | set(prior)
    referenced = set(response.get("evidence_ids", []))
    for ids in _walk(response.get("result", {}), "evidence_ids"):
        referenced.update(ids)
    for e in evidence:
        src = e.get("payload", {}).get("source_evidence_id")
        if src:
            referenced.add(src)
    dangling = referenced - allowed
    if dangling:
        raise rejected(f"evidence_ids not closed: {len(dangling)} unknown id(s)")

    # 3. 学科一致
    items, nodes = collect_refs(response)
    for ref in items | nodes:
        if ref.split(":", 1)[0] != subject:
            raise rejected("item_ref or skill_node outside the request subject")

    # 4. 已登记
    missing_items = {local_id(r) for r in items} - set(registered_items)
    missing_nodes = {local_id(r) for r in nodes} - registered_nodes
    if missing_items or missing_nodes:
        raise rejected(f"unregistered content: {len(missing_items)} item(s), {len(missing_nodes)} skill node(s)")

    # 4b. media_ref 只能是本次请求里出现过的引用，防止把别的学生的照片记在这个学生名下（安全审查 B2）
    allowed_media = _request_media_refs(request)
    for e in evidence:
        if "media_ref" in e and e["media_ref"] not in allowed_media:
            raise rejected("evidence media_ref does not come from this request")

    # 4c. 含照片、语音的证据要求请求声明为 L3（ADR 04 第 2 条：声明低于实际级别时平台拒绝）
    if request["context"]["data_class"] != "L3" and any(e["kind"] in L3_KINDS or "media_ref" in e for e in evidence):
        raise rejected("L3 evidence in a request declared below L3")

    # 5. 准入以平台为准：只能往更严的方向改
    rewritten = []
    for e in evidence:
        if "item_ref" not in e:
            continue
        platform_adm = registered_items[local_id(e["item_ref"])]
        if ADMISSION_RANK[platform_adm] > ADMISSION_RANK[e["admission"]]:
            rewritten.append(e["evidence_id"])
            e["admission"] = platform_adm

    # 6. 效力以平台为准：全部证据（本次 + 引用到的既有证据）都已准入才可能是 formal
    used_prior = [prior[i] for i in referenced & set(prior)]
    all_admitted = all(e["admission"] == "admitted" for e in evidence + used_prior)
    semantics = "formal" if response["semantics"] == "formal" and all_admitted else "practice"
    reasons = list(response.get("review_reasons", []))
    if not all_admitted and "content_not_admitted" not in reasons:
        reasons.append("content_not_admitted")
    # 申诉重判必须进复核（契约 GradeRequest.appeal）；适配器漏标时平台补上，只会更严
    if method == "grade" and request.get("appeal") is True and "student_appeal" not in reasons:
        reasons.append("student_appeal")
    hrr = bool(response["human_review_required"]) or bool(reasons)

    # 7. 作答 ↔ 作答证据：按 item_ref + occurred_at 匹配，不依赖数组顺序；零条或多条都拒绝
    attempts = [request["attempt"]] if method == "grade" else request["attempts"]
    response_evidence_ids = []
    for a in attempts:
        t = parse_time(a["occurred_at"])
        matches = [
            e["evidence_id"]
            for e in evidence
            if e["kind"] in RESPONSE_KINDS and e.get("item_ref") == a["item_ref"] and parse_time(e["occurred_at"]) == t
        ]
        if len(matches) != 1:
            raise rejected(f"attempt must match exactly one response evidence, got {len(matches)}")
        response_evidence_ids.append(matches[0])
    if len(set(response_evidence_ids)) != len(response_evidence_ids):
        raise rejected("two attempts matched the same response evidence")

    # 8. 作答类证据只能是本次作答本身：内容必须等于请求里的作答，题目必须是本次作答的题目。
    #    否则适配器可以把讲解或正确答案当成“学生原文”存下来（安全审查 S3）
    by_id = {e["evidence_id"]: e for e in evidence}
    for a, ev_id in zip(attempts, response_evidence_ids):
        e = by_id[ev_id]
        given = a["response"]
        if e["kind"] == "response_text" and e["payload"].get("text") != given.get("text"):
            raise rejected("response_text evidence differs from the student's response")
        if e["kind"] == "response_choice" and e["payload"].get("choice") != given.get("choice"):
            raise rejected("response_choice evidence differs from the student's response")
    extra = [e for e in evidence if e["kind"] in ("response_text", "response_choice") and e["evidence_id"] not in response_evidence_ids]
    if extra:
        raise rejected("response evidence that does not belong to any attempt")

    return Verified(
        evidence=evidence,
        semantics=semantics,
        human_review_required=hrr,
        review_reasons=reasons,
        response_evidence_ids=response_evidence_ids,
        admission_rewritten=rewritten,
    )
