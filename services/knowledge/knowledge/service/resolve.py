"""node.resolve 的规则解析（PR ②）：节点 ID 直引 + 节点名称 / includes 关键词匹配。

全文检索（jieba 分词 + tsvector 或进程内 BM25）属于 PR ③，接口 `Resolver` 留好：实现同一个 resolve() 即可替换或叠加。
输入文本可能来自学生（契约：数据级别 L2）：只在内存里用完即弃，不落库、不写日志、不进异常消息（规则 13）。
置信度是规则分数，不是概率；低于 LOW_CONFIDENCE 时整体要求复核（review_reasons = low_confidence），复核只带候选节点。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

RESOLVER_VERSION = "algo:kge-node-resolve@0.1.0"
LOW_CONFIDENCE = 0.5
_NODE_ID_IN_TEXT = re.compile(r"[A-Z][A-Z0-9]{1,9}(?:\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}")
_STRIP = re.compile(r"[\s，。、；：！？,.;:!?（）()《》“”\"'【】\[\]—\-]+")
_SYNTHETIC_PREFIX = re.compile(r"^合成[^：:]{0,8}[：:]")  # 合成夹具的“合成节点：”前缀不参与匹配


@dataclass(frozen=True)
class Candidate:
    node_id: str
    level: str
    kind: str | None
    confidence: float


class Resolver(Protocol):
    def resolve(self, text: str, nodes: Iterable[Mapping[str, Any]], max_candidates: int) -> list[Candidate]: ...


def _norm(s: str) -> str:
    return _STRIP.sub("", unicodedata.normalize("NFKC", s)).lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def _overlap(phrase: str, text_grams: set[str]) -> float:
    g = _bigrams(phrase)
    return len(g & text_grams) / len(g) if g else 0.0


class RuleResolver:
    """确定性规则：
    - 文本里直接写了某个节点 ID：1.0
    - 节点名称（去掉标点、合成前缀）整体出现在文本里：0.6 + 0.3 × min(名称长度, 6) / 6
    - 否则取名称与各条 includes 的二元组覆盖率的最大值 × 0.7（覆盖率低于 0.34 不算命中）
    领域与主题层不作候选（它们不是可教的节点）。同分按节点 ID 排序，结果确定。
    """

    def resolve(self, text: str, nodes: Iterable[Mapping[str, Any]], max_candidates: int) -> list[Candidate]:
        t = _norm(text)
        grams = _bigrams(t)
        ids_in_text = set(_NODE_ID_IN_TEXT.findall(unicodedata.normalize("NFKC", text)))
        scored: list[Candidate] = []
        for n in nodes:
            if n["level"] not in ("node", "detail"):
                continue
            nid = str(n["id"])
            if nid in ids_in_text:
                score = 1.0
            else:
                name = _norm(_SYNTHETIC_PREFIX.sub("", str(n["name"])))
                if name and name in t:
                    score = 0.6 + 0.3 * min(len(name), 6) / 6
                else:
                    phrases = [name] + [_norm(_SYNTHETIC_PREFIX.sub("", str(x))) for x in (n.get("includes") or [])]
                    best = max((_overlap(p, grams) for p in phrases if p), default=0.0)
                    score = round(best * 0.7, 3) if best >= 0.34 else 0.0
            if score > 0:
                scored.append(Candidate(nid, str(n["level"]), n.get("kind"), round(min(score, 1.0), 3)))
        scored.sort(key=lambda c: (-c.confidence, c.node_id))
        return scored[:max_candidates]
