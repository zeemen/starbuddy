"""讲评课目录（specs/lesson-catalog PR B）：经适配器的只读方法 list_lessons（插件契约 1.1.0 草稿内追加）取一科已发布的讲评课。

- 请求用契约的 PresentItemContext：与学生无关、不计量，数据级别 L1，不带幂等键；内容版本与诊断判分、题面请求用同一组六段；
- 响应按契约 ListLessonsResponse 校验，再逐条按白名单投影（只留引用、节点、题、标题、步数），节点与题的学科前缀必须是这一科，
  同一节课不能出现两次；任何一项不合格整份目录拒绝（502 ADAPTER_RESULT_REJECTED），不缓存；
- 目录与学生无关，按学科在进程内缓存 5 分钟（时钟可注入）。适配器暂时失败 → 503（不缓存）；适配器没有这个方法（501）→ 空目录（缓存）；
- 学习服务对外的目录（/internal/v1/lesson/catalog）只给编号、版本、标题与步数；节点与题只在服务内用：
  打开讲评课时按目录补全，再练与复测条目按题找讲这道题的那一节（题目引用不出学习服务）。
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..adapter_client import call_adapter
from ..config import AdapterTarget
from ..contract import plugin_contract
from ..diagnosis.service import CONTENT_VERSION
from ..errors import IngestError

CONTRACT = "1.1.0"
TTL_SECONDS = 300.0
TIMEOUT = 10.0
REF_RE = re.compile(r"^([a-z]+):([A-Za-z0-9][A-Za-z0-9_.\-]{0,63})$")
FIELDS = frozenset({"lesson_ref", "skill_node", "item_ref", "title", "step_count"})
# 目录只按诊断那一组六段内容版本取（与题面请求同口径）；其余学科没有讲评课
CONTENT_VERSIONS = {CONTENT_VERSION["subject"]: dict(CONTENT_VERSION)}


@dataclass(frozen=True)
class CatalogEntry:
    lesson_id: str
    lesson_version: str
    skill_node: str
    item_ref: str | None
    title: str
    step_count: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.lesson_id, self.lesson_version)

    def public(self) -> dict:
        """对 BFF 的视图：只有编号、版本、标题与步数"""
        return {"lesson_ref": {"lesson_id": self.lesson_id, "lesson_version": self.lesson_version}, "title": self.title,
                "step_count": self.step_count}


def _rejected() -> IngestError:
    return IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter lesson catalog does not match the contract")


def project(data: object, subject: str) -> tuple[CatalogEntry, ...]:
    """校验并投影适配器的 list_lessons 响应；不合格整体拒绝"""
    if plugin_contract(CONTRACT).validate("ListLessonsResponse", data):
        raise _rejected()
    out: dict[tuple[str, str], CatalogEntry] = {}
    for e in data["result"]["lessons"]:  # type: ignore[index]
        if not set(e) <= FIELDS:
            raise _rejected()
        node, item = REF_RE.fullmatch(e["skill_node"]), REF_RE.fullmatch(e["item_ref"]) if "item_ref" in e else None
        if node is None or node.group(1) != subject or ("item_ref" in e and (item is None or item.group(1) != subject)):
            raise _rejected()
        entry = CatalogEntry(e["lesson_ref"]["lesson_id"], e["lesson_ref"]["lesson_version"], e["skill_node"], e.get("item_ref"),
                             e["title"], e["step_count"])
        if entry.key in out:
            raise _rejected()
        out[entry.key] = entry
    return tuple(sorted(out.values(), key=lambda x: x.key))


class LessonCatalog:
    def __init__(self, client: httpx.AsyncClient, adapters: dict[str, AdapterTarget], *, ttl: float = TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic, timeout: float = TIMEOUT) -> None:
        plugin_contract(CONTRACT)  # 候选开关没开时启动即失败（与 present_item、讲评课会话同一口径）
        self.client = client
        self.adapters = adapters
        self.ttl = ttl
        self.clock = clock
        self.timeout = timeout
        self._cache: dict[str, tuple[float, tuple[CatalogEntry, ...]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def supports(self, subject: str) -> bool:
        return subject in CONTENT_VERSIONS and subject in self.adapters

    async def entries(self, subject: str) -> tuple[CatalogEntry, ...]:
        """这一科的目录（按编号、版本排序）。没有讲评课的学科、没配适配器 → 空；适配器暂时失败 → 503"""
        if not self.supports(subject):
            return ()
        hit = self._cache.get(subject)
        if hit is not None and self.clock() < hit[0]:
            return hit[1]
        lock = self._locks.setdefault(subject, asyncio.Lock())
        async with lock:
            hit = self._cache.get(subject)
            if hit is not None and self.clock() < hit[0]:
                return hit[1]
            got = await self._fetch(subject)
            self._cache[subject] = (self.clock() + self.ttl, got)
            return got

    async def _fetch(self, subject: str) -> tuple[CatalogEntry, ...]:
        req = {"context": {"request_id": str(uuid.uuid4()), "contract_version": CONTRACT, "subject": subject, "data_class": "L1"},
               "content_version": dict(CONTENT_VERSIONS[subject])}
        if plugin_contract(CONTRACT).validate("ListLessonsRequest", req):
            raise IngestError(500, "INTERNAL", "assembled lesson catalog request does not match the contract", retryable=True)
        try:
            data = await call_adapter(self.client, self.adapters[subject], "list_lessons", req, self.timeout)
        except IngestError as e:
            if getattr(e, "upstream_status", None) == 501:
                return ()  # 适配器没有这个方法（501 METHOD_NOT_IMPLEMENTED）：没有讲评课
            raise IngestError(503, "UPSTREAM_UNAVAILABLE", "lesson catalog is temporarily unavailable", retryable=True) from None
        return project(data, subject)

    async def find(self, subject: str, lesson_id: str, lesson_version: str) -> CatalogEntry | None:
        return next((e for e in await self.entries(subject) if e.key == (lesson_id, lesson_version)), None)

    async def by_item(self, subject: str) -> dict[str, dict]:
        """题目引用 → 讲这道题的那节课的引用（同一道题有几节时取编号、版本最小的一节）；取不到目录时为空，不影响调用方"""
        try:
            entries = await self.entries(subject)
        except IngestError:
            return {}
        out: dict[str, dict] = {}
        for e in entries:
            if e.item_ref is not None and e.item_ref not in out:
                out[e.item_ref] = {"lesson_id": e.lesson_id, "lesson_version": e.lesson_version}
        return out
