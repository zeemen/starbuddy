"""适配器结果的平台核对（spec 范围第 4 条）。纯函数，不连库：登记情况由调用方查好传进来。

任何一条不通过，就抛出 rejected(...)，整体拒绝、一行不写。通过则返回 Verified：
落库要用的证据（准入状态已按平台改写）、效力、复核标记、作答 ↔ 作答证据的对应关系。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .contract import plugin_contract
from .errors import rejected

# 准入状态由松到严（契约 Admission；aya adapter-design §3.1）
ADMISSION_RANK = {"admitted": 0, "pending_review": 1, "source_unverified": 2, "rejected": 3}
RESPONSE_KINDS = ("response_text", "response_choice", "ocr_transcription", "asr_transcription")


def local_id(ref: str) -> str:
    return ref.split(":", 1)[1]


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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
) -> Verified:
    """registered_items：local item_id → 平台准入状态；registered_nodes：已登记的 local skill id。"""
    subject = request["context"]["subject"]
    schema = "GradeResponse" if method == "grade" else "DiagnoseResponse"

    # 1. 契约 schema
    errors = plugin_contract().validate(schema, response)
    if errors:
        raise rejected("contract: " + "; ".join(errors[:10]))

    evidence: list[dict] = [dict(e) for e in response.get("evidence", [])]
    new_ids = [e["evidence_id"] for e in evidence]
    if len(set(new_ids)) != len(new_ids):
        raise rejected("duplicate evidence_id in response")

    # 2. 证据 ID 闭合：引用只能指向本次证据或请求里的既有证据
    prior = {p["evidence_id"]: p for p in request.get("prior_evidence", [])}
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

    return Verified(
        evidence=evidence,
        semantics=semantics,
        human_review_required=hrr,
        review_reasons=reasons,
        response_evidence_ids=response_evidence_ids,
        admission_rewritten=rewritten,
    )
