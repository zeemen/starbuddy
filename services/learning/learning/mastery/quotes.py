"""引语的原文核对（specs/quote-verbatim-guard 第二节第 3–5 条；待人确认 #1–#5 按默认）。

陈述—判断一致性检查（consistency.py 第 7–12 条）只看结构，不读原文；“引语逐字出自学生原话”由这里查证据原文核对，
结果按 (节, 引语 key) 交回 consistency.check_page(quote_verdicts=…)。

- 逐字口径（#1）：两边都过 consistency.normalize（NFKC、去空白与不可见字符），此外不做任何归一——标点、简繁、同义都不放过；
  规范化后引语必须是原文的连续子串，且长度在 1..QUOTE_MAX_CHARS。
- 只认学生自己键盘输入的作答（#2）：platform.evidence_event 里 kind = response_text 且 channel = keypad、属于该生的那一条；
  拍照、手写板、语音（OCR / ASR、channel 为 photo / canvas / voice 或为空）一律不认。
- 引语必须是所引判断的依据：判断属于该生、学习服务读得到（0010 claim_agent_read：可见范围含 agent、未过期、非终态），
  证据在判断的 evidence_ids 里。所引判断有未结的“数据有误”复核任务（platform.review_task，source_kind = claim）时不认。
- 结果编码：ok / not_found / disputed / no_match。证据不存在、不属于该生、种类或通道不对、不在判断依据里、判断读不到，
  一律 not_found（同一个编码，不能拿来试探别人的证据）；已删除的学生证据已物理删除，同样是 not_found。
- 一次最多 VERIFY_MAX_QUOTES 段；超出、ID 不是 UUID 形状一律按 not_found 或拒绝，不查库。
- 只读，不写任何表；日志不记引语文字与原文（规则 8）。本模块不提供对外接口：洞察服务调用要经 learning-internal 契约追加
  接口（契约变更，必停，lessons 待人确认）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from . import consistency

VERIFY_MAX_QUOTES = 8
OK, NOT_FOUND, DISPUTED, NO_MATCH = consistency.QUOTE_OK, "not_found", "disputed", "no_match"
CODES = (OK, NOT_FOUND, DISPUTED, NO_MATCH)
ALLOWED_KIND, ALLOWED_CHANNEL = "response_text", "keypad"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def matches(source: str, quote: str) -> bool:
    """规范化后引语是原文的连续子串（逐字口径，#1）"""
    q = consistency.normalize(quote)
    return 1 <= len(q) <= consistency.QUOTE_MAX_CHARS and q in consistency.normalize(source)


@dataclass(frozen=True)
class QuoteCheck:
    key: str
    claim_id: str
    evidence_id: str
    text: str


class TooManyQuotes(ValueError):
    """一次核对超过 VERIFY_MAX_QUOTES 段"""


_SQL = """
SELECT q.ord::int AS ord, e.payload ->> 'text' AS source,
       EXISTS (SELECT 1 FROM platform.review_task t
                WHERE t.source_kind = 'claim' AND t.source_id = c.id AND t.status <> 'resolved') AS disputed
  FROM unnest($2::uuid[], $3::uuid[]) WITH ORDINALITY AS q(claim_id, evidence_id, ord)
  JOIN insight.claim c ON c.id = q.claim_id AND c.student_id = $1::uuid AND q.evidence_id = ANY (c.evidence_ids)
  JOIN platform.evidence_event e ON e.evidence_id = q.evidence_id AND e.student_id = $1::uuid
                                AND e.kind = $4 AND e.channel = $5
"""


async def verify(conn: Any, student_id: str, checks: Sequence[QuoteCheck]) -> dict[str, str]:
    """逐段核对，返回 key → 编码。conn 是 starbuddy_learning 身份的连接（只读）。"""
    if len(checks) > VERIFY_MAX_QUOTES:
        raise TooManyQuotes(f"一次最多核对 {VERIFY_MAX_QUOTES} 段引语")
    if len({c.key for c in checks}) != len(checks):
        raise ValueError("引语 key 重复")
    out = {c.key: NOT_FOUND for c in checks}
    if not _UUID.fullmatch(student_id or ""):
        return out
    todo = [c for c in checks if _UUID.fullmatch(c.claim_id or "") and _UUID.fullmatch(c.evidence_id or "")]
    if not todo:
        return out
    rows = await conn.fetch(_SQL, student_id, [c.claim_id for c in todo], [c.evidence_id for c in todo],
                            ALLOWED_KIND, ALLOWED_CHANNEL)
    for r in rows:
        c = todo[r["ord"] - 1]
        if r["disputed"]:
            out[c.key] = DISPUTED
        elif isinstance(r["source"], str) and matches(r["source"], c.text):
            out[c.key] = OK
        else:
            out[c.key] = NO_MATCH
    return out


async def page_verdicts(conn: Any, student_id: str,
                        sections: Sequence[consistency.Section]) -> dict[tuple[str, str], str]:
    """一页里全部引语的核对结果，形状即 check_page 的 quote_verdicts。没有引语不查库。"""
    reqs = consistency.quote_requests(sections)
    if not reqs:
        return {}
    if len(reqs) > VERIFY_MAX_QUOTES:   # 超出上限不查库，整页按未通过（fail closed）
        return {(sec, q.key): NOT_FOUND for sec, q in reqs}
    checks = [QuoteCheck(f"k{i}", q.claim_id, q.evidence_id, q.text) for i, (_, q) in enumerate(reqs)]
    res = await verify(conn, student_id, checks)
    return {(sec, q.key): res[f"k{i}"] for i, (sec, q) in enumerate(reqs)}
