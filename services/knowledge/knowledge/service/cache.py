"""PLT-19 第一步：公共层读工具的进程内只读缓存（specs/knowledge-read-cache-plt19）。开关缺省关。

只缓存 node.get、node.path、page.get 三个工具的成功结果（处理器返回的 result 与 meta，信封与响应校验每次照常做）：
- 键 = 工具名 + 学科 + 已发布地图版本的 map_version_id + 页面快照的 commit 与快照代号（仅 page.get）+ 规范化请求参数
  （按工具列出入键字段：edition、region 必须入键；契约有缺省值的字段先补上缺省值再入键（node.path 的 max_depth）；
  context 整个不入键，request_id、caller、data_class 都是每请求字段，context.map_version 每请求都与已发布版本核对，不影响结果）。
  参数里出现入键字段以外的字段 → 不缓存（不猜它是否影响结果）；
- 快照代号（generation）：每次热切换装入快照（new_snapshot）加一。同一个 commit 可能对应内容不同的快照（对账扣页 held_pages
  不同：被扣页沿用上一份快照的内容），所以只靠 commit 不够；
- 每请求仍先查一次 published_version（ToolCore 里做，再算键）：发布、撤下即时生效；版本变了键就变，旧键不会再命中；
- 只缓存成功结果：处理器抛错（404、501、500）时不入缓存；page.get 另有三种结果不入缓存：请求途中快照被换掉
  （ToolCore 在入缓存前核对“当前快照仍是请求开头取到的那一份”）、没有任何教材投影、单条超过字节上限的一部分；
- 入缓存与命中时都深拷贝：调用方改返回值不会污染缓存；
- 容量两道上限：条目数（size）与总字节数（max_bytes，按条目 JSON 序列化后的 UTF-8 长度估算），任一超出按 LRU 淘汰；
  单条超过 max_bytes // ENTRY_FRACTION（缺省 1 MiB）不入缓存；
- 页面快照热切换时清空 page.get 条目并换快照代号（清空是正确性所必需的一层，见 hotswap.py）；
- 请求参数（含 context）里出现学生 ID 类字段名时不缓存并告警。契约的 PublicContext 本就不收这些字段，请求校验会先回 400；
  这里是第二道防线，防契约或校验改坏后把带个人标识的请求结果留在进程里。告警只记工具名与命中的类别（student、phone 等），
  不记原始键名（键名里可能拼着姓名）与值。
观测：按工具的命中、未命中、淘汰、绕过（bypass：三种不走缓存的请求）、未入缓存（not_stored）计数与当前条目数、字节数；
经 app.state.knowledge["cache"].snapshot() 读，并每 log_every 次请求打一条计数日志（只有数字，不含参数）。
不设 TTL：键含版本，版本变则自然失效（待人确认 #1）。照 services/learning/learning/present_item.py 的 OrderedDict LRU 写法。

并发：知识服务是单进程、单事件循环（uvicorn 单 worker，HTTP 与 MCP 同一个循环；热切换只把核对放进线程，install 回到循环里做）。
本类的方法全部是同步的、中间没有 await，在事件循环里天然互斥，所以不加锁；若以后改成多线程或多 worker 再加锁 / 换共享存储。
"""
from __future__ import annotations

import copy
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .config import DEFAULT_CACHE_LOG_EVERY, DEFAULT_CACHE_MAX_BYTES
from .config import DEFAULT_CACHE_SIZE as DEFAULT_SIZE

log = logging.getLogger("starbuddy.knowledge.cache")

ENTRY_FRACTION = 64  # 单条上限 = 总字节上限 // 64（缺省 64 MiB → 1 MiB）
# 入键字段：请求体里 context 以外、会影响结果的字段（与契约 NodeGetRequest / NodePathRequest / PageGetRequest 对应）
KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "node.get": ("node_id", "edition", "region"),
    "node.path": ("node_id", "direction", "max_depth"),
    "page.get": ("page_id", "edition", "region"),
}
# 契约里有缺省值的入键字段：没传时按缺省值入键（{} 与 {max_depth: 3} 是同一个请求）。测试核对与契约一致
KEY_DEFAULTS: dict[str, dict[str, Any]] = {"node.path": {"max_depth": 3}}
CACHED_TOOLS = frozenset(KEY_FIELDS)
# 学生 ID 类字段名（任意层级的键名）：学生、学习者、家长、账号、手机号等。告警只记命中的类别（小写），不记原始键名
PERSONAL_FIELD = re.compile(r"(student|learner|pupil|child|guardian|parent|account|user|member|phone|mobile|wechat|openid)",
                            re.I)


def personal_fields(value: Any, _depth: int = 0) -> list[str]:
    """请求体里键名像学生 ID 类字段的那些键命中的类别（小写；只看键名，不看值；深度有限）"""
    out: list[str] = []
    if _depth > 8:
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            m = PERSONAL_FIELD.search(k) if isinstance(k, str) else None
            if m:
                out.append(m.group(1).lower())
            out += personal_fields(v, _depth + 1)
    elif isinstance(value, list):
        for v in value:
            out += personal_fields(v, _depth + 1)
    return out


def cacheable(tool: str, value: Any) -> bool:
    """处理器成功结果里哪些可以入缓存：page.get 没有任何教材投影（atom_refs 里没有 textbook_projection）的不入缓存"""
    if tool == "page.get":
        result = value[0] if isinstance(value, tuple) and value else {}
        return any(str(r).startswith("textbook_projection:") for r in result.get("atom_refs", []))
    return True


def entry_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


@dataclass
class ToolStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    bypass: int = 0      # 不走缓存的请求：参数里有学生 ID 类字段、有未知字段、快照没配 / 页面不在快照 / 学科不明
    not_stored: int = 0  # 未命中且成功、但没入缓存：请求途中换了快照、page.get 没有教材投影、单条超过字节上限


@dataclass
class ReadCache:
    """进程内 LRU：键 → （result, meta）的深拷贝。计数按工具（进程内，经 app.state.knowledge["cache"] 与日志读）"""
    size: int = DEFAULT_SIZE
    max_bytes: int = DEFAULT_CACHE_MAX_BYTES
    log_every: int = DEFAULT_CACHE_LOG_EVERY
    stats: dict[str, ToolStats] = field(default_factory=lambda: {t: ToolStats() for t in sorted(CACHED_TOOLS)})
    generation: int = 0  # 页面快照代号：new_snapshot 时加一，进 page.get 的键
    bytes: int = 0       # 当前全部条目的估算字节数
    _entries: OrderedDict[tuple, tuple[str, Any, int]] = field(default_factory=OrderedDict, repr=False)
    _requests: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.size < 1 or self.max_bytes < 1 or self.log_every < 1:
            raise ValueError("cache size, max_bytes and log_every must be positive")

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entry_limit(self) -> int:
        return max(1, self.max_bytes // ENTRY_FRACTION)

    def count(self, tool: str) -> int:
        return sum(1 for t, _, _ in self._entries.values() if t == tool)

    def tick(self) -> None:
        """每个走到缓存判断的请求调一次；每 log_every 次打一条计数日志（只有数字）"""
        self._requests += 1
        if self._requests % self.log_every == 0:
            log.info(json.dumps({"cache_stats": self.snapshot(), "requests": self._requests, "entries": len(self._entries),
                                 "bytes": self.bytes, "generation": self.generation}, ensure_ascii=False, sort_keys=True))

    def note_bypass(self, tool: str) -> None:
        self.stats[tool].bypass += 1

    def note_not_stored(self, tool: str) -> None:
        self.stats[tool].not_stored += 1

    def params(self, tool: str, body: dict) -> str | None:
        """规范化请求参数（补齐缺省值、排序键的 JSON）；不该缓存时返回 None（计 bypass，学生 ID 类字段另告警）"""
        if tool not in CACHED_TOOLS:
            return None
        found = personal_fields(body)
        if found:
            self.note_bypass(tool)
            log.warning(json.dumps({"tool": tool, "cache": "bypass", "reason": "personal_field", "categories": sorted(set(found))},
                                   ensure_ascii=False))
            return None
        allowed = KEY_FIELDS[tool]
        extra = [k for k in body if k != "context" and k not in allowed]
        if extra:
            self.note_bypass(tool)
            return None
        normalized = {**KEY_DEFAULTS.get(tool, {}), **{k: body[k] for k in allowed if k in body}}
        return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def key(tool: str, subject: str, version_id: Any, commit: str | None, generation: int | None, params: str) -> tuple:
        return (tool, subject, str(version_id), commit or "", generation, params)

    def get(self, key: tuple) -> Any | None:
        tool = key[0]
        hit = self._entries.get(key)
        if hit is None:
            self.stats[tool].misses += 1
            return None
        self._entries.move_to_end(key)
        self.stats[tool].hits += 1
        return copy.deepcopy(hit[1])

    def put(self, key: tuple, value: Any) -> bool:
        """入缓存；单条超过上限的一部分不入（返回 False，计 not_stored）。超出条目数或总字节数时按 LRU 淘汰"""
        tool = key[0]
        size = entry_bytes(value)
        if size > self.entry_limit:
            self.note_not_stored(tool)
            return False
        old = self._entries.pop(key, None)
        if old is not None:
            self.bytes -= old[2]
        self._entries[key] = (tool, copy.deepcopy(value), size)
        self.bytes += size
        while len(self._entries) > self.size or self.bytes > self.max_bytes:
            _, (gone_tool, _, gone_size) = self._entries.popitem(last=False)
            self.bytes -= gone_size
            self.stats[gone_tool].evictions += 1
        return True

    def drop_tool(self, tool: str) -> int:
        """清掉某个工具的全部条目；返回清掉的条数"""
        gone = [k for k, (t, _, _) in self._entries.items() if t == tool]
        for k in gone:
            self.bytes -= self._entries.pop(k)[2]
        return len(gone)

    def new_snapshot(self) -> int:
        """热切换装入新快照：换快照代号并清掉 page.get 条目（同 commit 的快照内容也可能不同）；返回新代号"""
        self.generation += 1
        self.drop_tool("page.get")
        return self.generation

    def snapshot(self) -> dict[str, dict[str, int]]:
        """计数快照（不含键与值，只有数字）"""
        out: dict[str, dict[str, int]] = {}
        for t, s in self.stats.items():
            sizes = [n for tool, _, n in self._entries.values() if tool == t]
            out[t] = {"hits": s.hits, "misses": s.misses, "evictions": s.evictions, "bypass": s.bypass,
                      "not_stored": s.not_stored, "entries": len(sizes), "bytes": sum(sizes)}
        return out
