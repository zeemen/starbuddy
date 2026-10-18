"""PLT-19 第一步：公共层只读缓存（knowledge/service/cache.py）。不连库：ToolCore + 内存里的合成读库替身。全部合成数据（规则 8）。

覆盖 spec 第 4 条：开关缺省关、关时不碰缓存；开时命中与未命中、与关时逐字节相同；每请求都查已发布版本（撤下、换版即时生效）；
edition / region 不串；不缓存的工具不缓存；LRU 容量与淘汰；命中返回副本；错误不入缓存；热切换清空 page.get 条目、
切换途中入缓存的旧快照结果不会被新快照命中；参数里有学生 ID 类字段不缓存并告警；并发（单事件循环）；访问日志与计数不含个人信息。
库上的开关前后逐字节对比在 tests/test_service_cache_db.py。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import uuid
from typing import Any

import pytest

from conftest import APPLY, LINEAR, TRANSPOSE, make_page_snapshot
from knowledge.service.cache import CACHED_TOOLS, KEY_DEFAULTS, KEY_FIELDS, ReadCache, entry_bytes, personal_fields
from knowledge.service.config import DEFAULT_CACHE_SIZE, Settings
from knowledge.service.contract import contract
from knowledge.service.hotswap import SnapshotSwapper, UnsignedVerifier
from knowledge.service.pages import load_snapshot_dir
from knowledge.service.reader import MapVersionRow
from knowledge.service.tools import ToolCore

C = contract()
ENV = {"KNOWLEDGE_ENV": "test", "KNOWLEDGE_DATABASE_URL": "postgresql://synthetic@/x", "KNOWLEDGE_TOKEN_ORCHESTRATOR": "o" * 32}
A, B = "a" * 40, "b" * 40
HELD = "f" * 40  # 被扣页沿用的上一份快照的提交号（合成）
PAGE = "math.syn.eq.linear"
V1 = MapVersionRow("syn-v1", "aya", "0.9.0", 2022)
V2 = MapVersionRow("syn-v2", "aya", "0.9.1", 2022)


def _node(nid: str, name: str, **kw: Any) -> dict:
    return {"id": nid, "level": "node", "parent_id": "MATH.SYN.EQ", "kind": "kp", "name": name, "includes": ["合成边界"],
            "excludes": [], "examples": [], "scope": "required", "cognitive_levels": ["understand"], "error_type": None,
            "status": "active", **kw}


def _tp(n: int, nid: str, edition: str, quote: str) -> dict:
    return {"id": f"syn-tp-row-{n}", "node_id": nid, "edition": edition, "edition_year": 2024, "printing": None, "volume": "合成七上",
            "chapter": "合成第三章", "page_from": 80 + n, "page_to": 80 + n, "quote_text": quote, "quote_hash": f"{n:064x}",
            "mapping_state": "verified"}


def _rp(n: int, nid: str, region: str, weight: float) -> dict:
    return {"id": f"syn-rp-row-{n}", "node_id": nid, "region": region, "exam_year": 2025, "weight": weight,
            "typical_slots": ["填空题"], "style_tags": [], "source_ref": "synthetic"}


def _item(item_id: str) -> dict:
    return {"subject": "aya", "item_id": item_id, "main_node_id": LINEAR, "node_ids": [], "cognitive_level": "apply",
            "prior_difficulty": 0.4, "family_code": None, "license_state": "original", "family_id": None, "same_family": False}


class FakeReader:
    """KgeReader 的内存替身：按 map_version_id 分版本的合成数据；记录每个方法的调用次数；hook 在 node() 里插入一步（模拟请求途中的切换）"""

    def __init__(self) -> None:
        self.published: dict[str, MapVersionRow | None] = {"aya": V1}
        self.data: dict[str, dict[str, Any]] = {
            V1.id: {"nodes": [_node(LINEAR, "一元一次方程"), _node(TRANSPOSE, "移项"), _node(APPLY, "一元一次方程的应用")],
                    "edges": [(TRANSPOSE, LINEAR), (LINEAR, APPLY)],
                    "tps": [_tp(1, LINEAR, "syn-ed-a", "合成原句甲。"), _tp(2, LINEAR, "syn-ed-b", "合成原句乙。")],
                    "rps": [_rp(1, LINEAR, "syn-region", 0.03), _rp(2, LINEAR, "syn-other", 0.02)]},
            V2.id: {"nodes": [_node(LINEAR, "一元一次方程（新版）"), _node(TRANSPOSE, "移项"), _node(APPLY, "一元一次方程的应用")],
                    "edges": [(TRANSPOSE, LINEAR)],
                    "tps": [_tp(3, LINEAR, "syn-ed-a", "合成原句丙。")], "rps": []},
        }
        self.calls: dict[str, int] = {}
        self.hook: Any = None

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def published_version(self, subject: str) -> MapVersionRow | None:
        self._count("published_version")
        await asyncio.sleep(0)
        return self.published.get(subject)

    async def node(self, version_id: Any, node_id: str) -> dict | None:
        self._count("node")
        if self.hook is not None:
            hook, self.hook = self.hook, None
            await hook()
        await asyncio.sleep(0)
        return next((dict(n) for n in self.data[version_id]["nodes"] if n["id"] == node_id), None)

    async def nodes(self, version_id: Any) -> list[dict]:
        self._count("nodes")
        await asyncio.sleep(0)
        return [dict(n) for n in self.data[version_id]["nodes"]]

    async def textbook_projections(self, version_id: Any, node_id: str | None, edition: str | None = None) -> list[dict]:
        self._count("textbook_projections")
        return [dict(r) for r in self.data[version_id]["tps"] if r["node_id"] == node_id and edition in (None, r["edition"])]

    async def region_projections(self, version_id: Any, node_id: str | None, region: str | None = None) -> list[dict]:
        self._count("region_projections")
        return [dict(r) for r in self.data[version_id]["rps"] if r["node_id"] == node_id and region in (None, r["region"])]

    async def prerequisite_step(self, version_id: Any, frontier: list[str], direction: str) -> list[tuple[str, str]]:
        self._count("prerequisite_step")
        edges = self.data[version_id]["edges"]
        return [(f, t) for f, t in edges if (t if direction == "up" else f) in frontier]

    async def item_search(self, version_id: Any, subject: str, node_id: str | None, *args: Any) -> list[dict]:
        self._count("item_search")
        return [_item("SYN-I1"), _item("SYN-I2")]

    async def item(self, version_id: Any, subject: str, item_id: str) -> dict | None:
        self._count("item")
        return _item(item_id)

    async def item_similar(self, *args: Any) -> list[dict]:
        self._count("item_similar")
        return [_item("SYN-I2")]

    async def scoring_points(self, *args: Any) -> list[dict]:
        self._count("scoring_points")
        return [{"point_id": "p1", "score": 2, "node_ids": [LINEAR], "partial_credit_rule": {}, "subject_dimension": None,
                 "keyword_family": None}]


def ctx(caller: str = "orchestrator", **extra: Any) -> dict:
    return {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": caller, "data_class": "L1", **extra}


def node_get(**kw: Any) -> dict:
    return {"context": ctx(), "node_id": LINEAR, **kw}


def node_path(direction: str = "up", **kw: Any) -> dict:
    return {"context": ctx(), "node_id": APPLY, "direction": direction, **kw}


def page_get(**kw: Any) -> dict:
    return {"context": ctx(), "page_id": PAGE, **kw}


def snapshot(tmp_path, lexicon, commit: str):
    return load_snapshot_dir(make_page_snapshot(tmp_path / commit[:4], commit=commit), lexicon=lexicon)


def core_pair(tmp_path, lexicon, size: int = 64) -> tuple[ToolCore, ToolCore, FakeReader, FakeReader]:
    """（缓存开的 core，缓存关的 core，各自的读库替身）：两边同一份合成数据与快照"""
    snap = snapshot(tmp_path, lexicon, A)
    on_reader, off_reader = FakeReader(), FakeReader()
    return (ToolCore(on_reader, C, snap, cache=ReadCache(size=size)), ToolCore(off_reader, C, snap), on_reader, off_reader)


def raw(out: tuple[int, dict]) -> bytes:
    """与 JSONResponse 相同的序列化（逐字节比较用）"""
    status, body = out
    return str(status).encode() + b" " + json.dumps(body, ensure_ascii=False, allow_nan=False, indent=None,
                                                    separators=(",", ":")).encode("utf-8")


def source_commit(out: tuple[int, dict]) -> str:
    return out[1]["source_version"].rsplit("kge-pages@", 1)[1]


# ---------- 配置 ----------
def test_settings_cache_off_by_default_and_validated():
    s = Settings.from_env(ENV)
    assert s.read_cache is False and s.read_cache_size == DEFAULT_CACHE_SIZE
    assert Settings(env="test", database_url="x").read_cache is False
    on = Settings.from_env({**ENV, "KNOWLEDGE_READ_CACHE": "1", "KNOWLEDGE_READ_CACHE_SIZE": "8"})
    assert on.read_cache is True and on.read_cache_size == 8
    assert Settings.from_env({**ENV, "KNOWLEDGE_READ_CACHE": "0"}).read_cache is False
    assert s.read_cache_max_bytes == 64 * 1024 * 1024 and s.read_cache_log_every == 1000
    assert Settings.from_env({**ENV, "KNOWLEDGE_READ_CACHE_SIZE": "10000"}).read_cache_size == 10000
    sized = Settings.from_env({**ENV, "KNOWLEDGE_READ_CACHE_MAX_BYTES": "65536", "KNOWLEDGE_READ_CACHE_LOG_EVERY": "5"})
    assert sized.read_cache_max_bytes == 65536 and sized.read_cache_log_every == 5
    for bad in ({"KNOWLEDGE_READ_CACHE": "yes"}, {"KNOWLEDGE_READ_CACHE": "true"}, {"KNOWLEDGE_READ_CACHE_SIZE": "0"},
                {"KNOWLEDGE_READ_CACHE_SIZE": "-1"}, {"KNOWLEDGE_READ_CACHE_SIZE": "10001"}, {"KNOWLEDGE_READ_CACHE_SIZE": "x"},
                {"KNOWLEDGE_READ_CACHE_SIZE": "١٢"}, {"KNOWLEDGE_READ_CACHE_SIZE": "²"}, {"KNOWLEDGE_READ_CACHE_SIZE": " 8"},
                {"KNOWLEDGE_READ_CACHE_SIZE": "+8"}, {"KNOWLEDGE_READ_CACHE_SIZE": ""},
                {"KNOWLEDGE_READ_CACHE_MAX_BYTES": "65535"}, {"KNOWLEDGE_READ_CACHE_MAX_BYTES": str(1024 ** 3 + 1)},
                {"KNOWLEDGE_READ_CACHE_MAX_BYTES": "6.4e7"}, {"KNOWLEDGE_READ_CACHE_LOG_EVERY": "0"},
                {"KNOWLEDGE_READ_CACHE_LOG_EVERY": "١٠"}):
        with pytest.raises(RuntimeError):
            Settings.from_env({**ENV, **bad})
    for kw in ({"size": 0}, {"max_bytes": 0}, {"log_every": 0}):
        with pytest.raises(ValueError):
            ReadCache(**kw)


def test_cached_tools_are_exactly_the_three_versioned_reads():
    assert CACHED_TOOLS == {"node.get", "node.path", "page.get"}
    assert "edition" in KEY_FIELDS["node.get"] and "region" in KEY_FIELDS["node.get"]
    assert "edition" in KEY_FIELDS["page.get"] and "region" in KEY_FIELDS["page.get"]
    for tool, fields in KEY_FIELDS.items():  # 入键字段 = 契约请求里 context 以外的全部字段（漏一个就可能串）
        props = set(C.doc["components"]["schemas"][C.tools[tool].request_schema]["properties"]) - {"context"}
        assert set(fields) == props, tool
        schema_props = C.doc["components"]["schemas"][C.tools[tool].request_schema]["properties"]
        defaults = {k: p["default"] for k, p in schema_props.items() if isinstance(p, dict) and "default" in p}
        assert KEY_DEFAULTS.get(tool, {}) == defaults, tool  # 有缺省值的字段按契约缺省值补齐后入键
    assert KEY_DEFAULTS["node.path"] == {"max_depth": 3}


# ---------- 开关关：不碰缓存 ----------
@pytest.mark.anyio
async def test_off_reads_every_time_and_logs_no_cache_field(tmp_path, lexicon, caplog):
    _, off, _, reader = core_pair(tmp_path, lexicon)
    assert off.cache is None
    caplog.set_level(logging.INFO, logger="starbuddy.knowledge")
    for _ in range(3):
        assert (await off.call("node.get", node_get(), "orchestrator"))[0] == 200
        assert (await off.call("page.get", page_get(), "orchestrator"))[0] == 200
    assert reader.calls["node"] == 6 and reader.calls["published_version"] == 6
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "starbuddy.knowledge"]
    assert records and all("cache" not in r for r in records)


# ---------- 开关开：命中与未命中、与关时逐字节相同 ----------
REQUESTS = [
    ("node.get", lambda: node_get()), ("node.get", lambda: node_get(edition="syn-ed-a")),
    ("node.get", lambda: node_get(edition="syn-ed-b")), ("node.get", lambda: node_get(region="syn-region")),
    ("node.get", lambda: node_get(edition="syn-ed-b", region="syn-other")),
    ("node.path", lambda: node_path("up")), ("node.path", lambda: node_path("down")),
    ("node.path", lambda: node_path("up", max_depth=1)),
    ("page.get", lambda: page_get()), ("page.get", lambda: page_get(edition="syn-ed-a")),
    ("page.get", lambda: page_get(edition="syn-ed-b")), ("page.get", lambda: page_get(region="syn-region")),
    ("page.get", lambda: page_get(edition="syn-ed-a", region="syn-other")),
]


@pytest.mark.anyio
async def test_hit_and_miss_byte_identical_to_cache_off(tmp_path, lexicon):
    on, off, on_reader, _ = core_pair(tmp_path, lexicon)
    for tool, make in REQUESTS:
        want = raw(await off.call(tool, make(), "orchestrator"))
        miss = raw(await on.call(tool, make(), "orchestrator"))
        as_bff = make()
        as_bff["context"]["caller"] = "bff"
        hit = raw(await on.call(tool, as_bff, "bff"))  # 调用方、request_id 不同也命中（每请求字段不入键）
        assert want.startswith(b"200 ") and miss == want and hit == want, (tool, make())
    stats = on.cache.snapshot()  # type: ignore[union-attr]
    for tool in CACHED_TOOLS:
        n = sum(1 for t, _ in REQUESTS if t == tool)
        assert stats[tool]["misses"] == n and stats[tool]["hits"] == n and stats[tool]["entries"] == n, tool
    assert on_reader.calls["published_version"] == 2 * len(REQUESTS)  # 每请求都查已发布版本，命中也查
    assert on_reader.calls["node"] == 5 + 5  # node.get 与 page.get 各 5 次未命中；命中不再读节点


@pytest.mark.anyio
async def test_edition_and_region_do_not_mix(tmp_path, lexicon):
    on, _, _, _ = core_pair(tmp_path, lexicon)
    a = await on.call("node.get", node_get(edition="syn-ed-a"), "orchestrator")
    b = await on.call("node.get", node_get(edition="syn-ed-b"), "orchestrator")
    assert [p["edition"] for p in a[1]["result"]["textbook_projections"]] == ["syn-ed-a"]
    assert [p["edition"] for p in b[1]["result"]["textbook_projections"]] == ["syn-ed-b"]
    r1 = await on.call("node.get", node_get(region="syn-region"), "orchestrator")
    r2 = await on.call("node.get", node_get(region="syn-other"), "orchestrator")
    assert [w["region"] for w in r1[1]["result"]["region_weights"]] == ["syn-region"]
    assert [w["region"] for w in r2[1]["result"]["region_weights"]] == ["syn-other"]
    pa = await on.call("page.get", page_get(edition="syn-ed-a"), "orchestrator")
    pb = await on.call("page.get", page_get(edition="syn-ed-b"), "orchestrator")
    assert "合成原句甲" in pa[1]["result"]["body_md"] and "合成原句乙" not in pa[1]["result"]["body_md"]
    assert "合成原句乙" in pb[1]["result"]["body_md"] and "合成原句甲" not in pb[1]["result"]["body_md"]
    ga = await on.call("page.get", page_get(region="syn-region"), "orchestrator")
    go = await on.call("page.get", page_get(region="syn-other"), "orchestrator")
    assert ga[1]["result"]["body_md"] != go[1]["result"]["body_md"]
    # 第二轮全部命中，结果仍各自对得上
    for body, want in ((node_get(edition="syn-ed-a"), a), (node_get(edition="syn-ed-b"), b), (node_get(region="syn-region"), r1),
                       (node_get(region="syn-other"), r2)):
        assert raw(await on.call("node.get", body, "orchestrator")) == raw(want)
    for body, want in ((page_get(edition="syn-ed-a"), pa), (page_get(edition="syn-ed-b"), pb),
                       (page_get(region="syn-region"), ga), (page_get(region="syn-other"), go)):
        assert raw(await on.call("page.get", body, "orchestrator")) == raw(want)
    assert on.cache.snapshot()["page.get"]["hits"] == 4  # type: ignore[union-attr]


# ---------- 版本发布、撤下 ----------
@pytest.mark.anyio
async def test_retire_and_republish_take_effect_immediately(tmp_path, lexicon):
    on, _, reader, _ = core_pair(tmp_path, lexicon)
    for tool, body in (("node.get", node_get), ("node.path", node_path), ("page.get", page_get)):
        assert (await on.call(tool, body(), "orchestrator"))[0] == 200
        assert (await on.call(tool, body(), "orchestrator"))[0] == 200
    reader.published["aya"] = None  # 撤下：没有已发布版本
    for tool, body in (("node.get", node_get), ("node.path", node_path), ("page.get", page_get)):
        status, out = await on.call(tool, body(), "orchestrator")
        assert status == 404 and out["code"] == "CONTENT_VERSION_NOT_FOUND", tool
    reader.published["aya"] = V2  # 发布新版本：节点名、边、投影都变了
    status, out = await on.call("node.get", node_get(), "orchestrator")
    assert status == 200 and out["result"]["node"]["name"] == "一元一次方程（新版）"
    assert out["result"]["node"]["map_version"] == "0.9.1" and out["source_version"].endswith("@0.9.1")
    status, out = await on.call("node.path", node_path("up"), "orchestrator")
    assert status == 200 and out["result"]["steps"] == []  # 新版本没有 LINEAR→APPLY
    status, _ = await on.call("page.get", page_get(), "orchestrator")
    assert status == 404  # 页面按 0.9.0 编译，新版本发布后不服务（与缓存关时一致）
    reader.published["aya"] = V1  # 回到旧版本：旧键照常命中、结果是旧版本的
    status, out = await on.call("node.get", node_get(), "orchestrator")
    assert status == 200 and out["result"]["node"]["name"] == "一元一次方程"


@pytest.mark.anyio
async def test_requested_map_version_still_checked_on_hit(tmp_path, lexicon):
    on, _, _, _ = core_pair(tmp_path, lexicon)
    assert (await on.call("node.get", node_get(), "orchestrator"))[0] == 200
    body = node_get()
    body["context"]["map_version"] = "0.8.0"
    assert (await on.call("node.get", body, "orchestrator"))[0] == 404
    body["context"]["map_version"] = "0.9.0"
    assert (await on.call("node.get", body, "orchestrator"))[0] == 200


# ---------- 不缓存的工具 ----------
@pytest.mark.anyio
async def test_uncached_tools_are_not_cached(tmp_path, lexicon, caplog):
    on, _, reader, _ = core_pair(tmp_path, lexicon)
    caplog.set_level(logging.INFO, logger="starbuddy.knowledge")
    bodies = {"item.search": lambda: {"context": ctx(), "subject": "aya", "node_id": LINEAR},
              "item.similar": lambda: {"context": ctx(), "item_ref": "aya:SYN-I1"},
              "rubric.get": lambda: {"context": ctx(), "item_ref": "aya:SYN-I1", "rubric_version": "syn-rubric@0.1.0"}}
    for tool, make in bodies.items():
        for _ in range(2):
            status, _ = await on.call(tool, make(), "orchestrator")
            assert status == 200, tool
    assert reader.calls["item_search"] == 2 and reader.calls["item_similar"] == 2 and reader.calls["scoring_points"] == 2
    assert len(on.cache) == 0  # type: ignore[arg-type]
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "starbuddy.knowledge"]
    assert len(records) == 6 and all("cache" not in r for r in records)


# ---------- LRU ----------
@pytest.mark.anyio
async def test_lru_capacity_and_eviction(tmp_path, lexicon):
    on, _, reader, _ = core_pair(tmp_path, lexicon, size=2)
    e_a, e_b = node_get(edition="syn-ed-a"), node_get(edition="syn-ed-b")
    await on.call("node.get", e_a, "orchestrator")
    await on.call("node.get", e_b, "orchestrator")
    await on.call("node.get", e_a, "orchestrator")         # 命中：a 变成最近用过
    await on.call("node.get", node_get(), "orchestrator")  # 第三个：淘汰最久没用的 b
    cache = on.cache
    assert cache is not None and len(cache) == 2
    s = cache.snapshot()["node.get"]
    assert {k: s[k] for k in ("hits", "misses", "evictions", "bypass", "not_stored", "entries")} == \
        {"hits": 1, "misses": 3, "evictions": 1, "bypass": 0, "not_stored": 0, "entries": 2}
    assert s["bytes"] == cache.bytes > 0
    before = reader.calls["node"]
    await on.call("node.get", e_a, "orchestrator")  # a 还在
    assert reader.calls["node"] == before
    await on.call("node.get", e_b, "orchestrator")  # b 被淘汰过：重新读
    assert reader.calls["node"] == before + 1
    assert cache.snapshot()["node.get"]["evictions"] == 2 and len(cache) == 2


# ---------- 副本 ----------
@pytest.mark.anyio
async def test_hit_returns_a_copy_callers_cannot_poison_the_cache(tmp_path, lexicon):
    on, off, _, _ = core_pair(tmp_path, lexicon)
    want = raw(await off.call("node.get", node_get(), "orchestrator"))
    first = await on.call("node.get", node_get(), "orchestrator")   # 未命中：返回的是入缓存前的那份
    first[1]["result"]["node"]["name"] = "被调用方改掉"
    first[1]["result"]["textbook_projections"].clear()
    second = await on.call("node.get", node_get(), "orchestrator")  # 命中
    assert raw(second) == want
    second[1]["result"]["node"]["includes"].append("又被改")
    second[1]["result"]["region_weights"].clear()
    assert raw(await on.call("node.get", node_get(), "orchestrator")) == want
    cache = ReadCache(size=4)
    key = cache.key("node.get", "aya", "v", None, None, "{}")
    value = ({"a": [1]}, {"source_version": "x"})
    cache.put(key, value)
    value[0]["a"].append(2)
    got = cache.get(key)
    assert got == ({"a": [1]}, {"source_version": "x"}) and got is not cache.get(key)


# ---------- 错误不入缓存 ----------
@pytest.mark.anyio
async def test_errors_are_not_cached(tmp_path, lexicon):
    on, _, reader, _ = core_pair(tmp_path, lexicon)
    missing = lambda: {"context": ctx(), "node_id": "MATH.SYN.EQ.NOPE"}  # noqa: E731
    for _ in range(2):
        status, out = await on.call("node.get", missing(), "orchestrator")
        assert status == 404 and out["code"] == "CONTENT_VERSION_NOT_FOUND"
    assert reader.calls["node"] == 2
    for _ in range(2):
        assert (await on.call("node.path", {"context": ctx(), "node_id": "MATH.SYN.EQ.NOPE", "direction": "up"}, "orchestrator"))[0] == 404
        assert (await on.call("page.get", page_get(page_id="math.syn.eq.nope"), "orchestrator"))[0] == 404
    assert len(on.cache) == 0  # type: ignore[arg-type]
    on.pages = None  # 没配快照：501 也不缓存
    assert (await on.call("page.get", page_get(), "orchestrator"))[0] == 501
    assert len(on.cache) == 0  # type: ignore[arg-type]
    # 节点从数据里消失（模拟读不到）：未命中时照常 404，不留条目
    on.pages = snapshot(tmp_path, lexicon, B)
    reader.data[V1.id]["nodes"] = [n for n in reader.data[V1.id]["nodes"] if n["id"] != LINEAR]
    for _ in range(2):
        assert (await on.call("node.get", node_get(), "orchestrator"))[0] == 404
        assert (await on.call("page.get", page_get(), "orchestrator"))[0] == 404
    assert len(on.cache) == 0  # type: ignore[arg-type]


# ---------- 热切换 ----------
@pytest.mark.anyio
async def test_hot_swap_drops_page_entries_and_serves_new_commit(tmp_path, lexicon):
    on, _, _, _ = core_pair(tmp_path, lexicon)
    swapper = SnapshotSwapper(on, UnsignedVerifier())
    swapper.install(on.pages)  # type: ignore[arg-type]
    await on.call("page.get", page_get(), "orchestrator")
    await on.call("page.get", page_get(edition="syn-ed-a"), "orchestrator")
    await on.call("node.get", node_get(), "orchestrator")
    cache = on.cache
    assert cache is not None and cache.count("page.get") == 2 and cache.count("node.get") == 1
    swapper.install(snapshot(tmp_path, lexicon, B))
    assert cache.count("page.get") == 0 and cache.count("node.get") == 1  # 只清 page.get
    out = await on.call("page.get", page_get(), "orchestrator")
    assert out[0] == 200 and source_commit(out) == B[:12]
    assert cache.snapshot()["page.get"]["misses"] == 3


@pytest.mark.anyio
async def test_swap_during_a_miss_old_commit_entry_never_served_after_swap(tmp_path, lexicon):
    """请求拿着快照 A 未命中、读库途中热切换到 B（清空发生在入缓存之前），A 的结果仍以 A 的键入缓存；
    之后的请求用 B 的键，不会拿到 A 的页面（键含 commit 的意义）"""
    on, _, reader, _ = core_pair(tmp_path, lexicon)
    swapper = SnapshotSwapper(on, UnsignedVerifier())
    snap_b = snapshot(tmp_path, lexicon, B)

    async def swap() -> None:
        swapper.install(snap_b)
    reader.hook = swap
    first = await on.call("page.get", page_get(), "orchestrator")
    assert first[0] == 200 and source_commit(first) == A[:12]  # 整个请求用开头那份快照
    assert on.pages is snap_b
    second = await on.call("page.get", page_get(), "orchestrator")
    assert second[0] == 200 and source_commit(second) == B[:12]
    third = await on.call("page.get", page_get(), "orchestrator")
    assert source_commit(third) == B[:12]
    s = on.cache.snapshot()["page.get"]  # type: ignore[union-attr]
    assert s["misses"] == 2 and s["hits"] == 1


def same_commit_variant(snap):
    """同 commit、不同内容的快照（模拟对账扣页 held_pages 不同：LINEAR 页沿用上一份快照，带自己的 source_commit）"""
    pages = dict(snap.pages)
    pages[PAGE] = dataclasses.replace(pages[PAGE], source_commit=HELD)
    return dataclasses.replace(snap, pages=pages)


@pytest.mark.anyio
async def test_same_commit_swap_during_a_miss_is_not_stored(tmp_path, lexicon):
    """请求拿着快照 A1 未命中、读库途中热切换到同 commit 的 A2（内容不同）：A1 的结果不能入缓存，之后的请求拿到 A2 的内容"""
    on, _, reader, _ = core_pair(tmp_path, lexicon)
    swapper = SnapshotSwapper(on, UnsignedVerifier())
    a1 = on.pages
    swapper.install(a1)  # type: ignore[arg-type]
    a2 = same_commit_variant(a1)
    assert a2.commit == a1.commit  # type: ignore[union-attr]

    async def swap() -> None:
        swapper.install(a2)
    reader.hook = swap
    first = await on.call("page.get", page_get(), "orchestrator")
    assert first[0] == 200 and source_commit(first) == A[:12]  # 整个请求用开头那份快照
    assert on.cache.count("page.get") == 0  # type: ignore[union-attr]  # 切换途中的结果不入缓存
    second = await on.call("page.get", page_get(), "orchestrator")
    assert second[0] == 200 and source_commit(second) == HELD[:12]
    third = await on.call("page.get", page_get(), "orchestrator")
    assert source_commit(third) == HELD[:12]


@pytest.mark.anyio
async def test_snapshot_generation_is_part_of_the_page_key(tmp_path, lexicon, monkeypatch):
    """即使热切换没清掉旧条目（这里把清空换成空操作），同 commit 的新快照也不命中旧快照的条目：键里有快照代号"""
    on, _, _, _ = core_pair(tmp_path, lexicon)
    swapper = SnapshotSwapper(on, UnsignedVerifier())
    a1 = on.pages
    swapper.install(a1)  # type: ignore[arg-type]
    assert source_commit(await on.call("page.get", page_get(), "orchestrator")) == A[:12]
    monkeypatch.setattr(on.cache, "drop_tool", lambda tool: 0)
    swapper.install(same_commit_variant(a1))
    out = await on.call("page.get", page_get(), "orchestrator")
    assert out[0] == 200 and source_commit(out) == HELD[:12]


# ---------- 学生 ID 类字段 ----------
def test_personal_fields_detection():
    assert personal_fields({"context": {"student_ref": "x"}, "node_id": "y"}) == ["student"]
    assert sorted(personal_fields({"a": [{"guardian_id": 1}], "LearnerId": 2, "PHONE": 3})) == ["guardian", "learner", "phone"]
    assert personal_fields(node_get(edition="syn-ed-a", region="syn-region")) == []
    assert personal_fields(page_get()) == [] and personal_fields(node_path(max_depth=2)) == []


@pytest.mark.anyio
async def test_personal_field_in_request_is_not_cached_and_warns(tmp_path, lexicon, caplog):
    on, _, reader, _ = core_pair(tmp_path, lexicon)
    sid = str(uuid.uuid4())
    caplog.set_level(logging.WARNING, logger="starbuddy.knowledge.cache")
    # 公共路径：契约的 PublicContext 不收 student_ref，请求校验先回 400（不进缓存）
    body = node_get()
    body["context"]["student_ref"] = sid
    assert (await on.call("node.get", body, "orchestrator"))[0] == 400
    assert len(on.cache) == 0  # type: ignore[arg-type]
    # 第二道防线：即使请求越过了校验，缓存路径也不缓存并告警（直接调缓存路径，模拟校验被改坏）
    record: dict[str, Any] = {}
    for tool, b in (("node.get", node_get()), ("page.get", page_get()), ("node.path", node_path())):
        b["context"]["student_ref"] = sid
        for _ in range(2):
            await on._cached(tool, b, on.cache, record)  # type: ignore[arg-type]
            assert record["cache"] == "bypass"
    assert len(on.cache) == 0 and reader.calls["node"] == 4  # type: ignore[arg-type]
    warnings = [r.getMessage() for r in caplog.records if r.name == "starbuddy.knowledge.cache" and r.levelno == logging.WARNING]
    assert len(warnings) == 6 and all('"categories": ["student"]' in w and "personal_field" in w for w in warnings)
    assert all(sid not in w and "student_ref" not in w for w in warnings)  # 只记类别，不记原始键名与值
    # 键名里拼着姓名（合成）：日志只出现类别，不出现键名里的姓名
    caplog.clear()
    named = page_get()
    named["context"]["student_WangXiaoming_合成"] = sid
    await on._cached("page.get", named, on.cache, record)  # type: ignore[arg-type]
    logged = [r.getMessage() for r in caplog.records if r.name == "starbuddy.knowledge.cache"]
    assert len(logged) == 1 and '"categories": ["student"]' in logged[0]
    assert "WangXiaoming" not in logged[0] and "合成" not in logged[0] and sid not in logged[0]
    assert on.cache.snapshot()["node.get"]["bypass"] == 2  # type: ignore[union-attr]
    # 入键字段以外的未知字段也不缓存（不猜它是否影响结果）
    cache = ReadCache(size=4)
    assert cache.params("node.get", {**node_get(), "unknown_param": 1}) is None
    assert cache.params("node.get", node_get()) is not None and cache.params("item.search", {"context": {}}) is None


def test_key_ignores_per_request_fields_and_keeps_everything_else():
    cache = ReadCache(size=4)
    p1 = cache.params("node.get", {"context": ctx("orchestrator"), "node_id": LINEAR, "edition": "syn-ed-a"})
    p2 = cache.params("node.get", {"edition": "syn-ed-a", "node_id": LINEAR, "context": ctx("bff", map_version="0.9.0")})
    assert p1 == p2 and p1 is not None and "request_id" not in p1 and "orchestrator" not in p1
    p3 = cache.params("node.get", {"context": ctx(), "node_id": LINEAR, "edition": "syn-ed-b"})
    p4 = cache.params("node.get", {"context": ctx(), "node_id": LINEAR, "region": "syn-ed-a"})
    assert len({p1, p3, p4}) == 3
    keys = {cache.key("page.get", "aya", "v1", A, 1, p1), cache.key("page.get", "aya", "v1", B, 1, p1),
            cache.key("page.get", "aya", "v2", A, 1, p1), cache.key("node.get", "aya", "v1", A, 1, p1),
            cache.key("page.get", "zhixing", "v1", A, 1, p1), cache.key("page.get", "aya", "v1", A, 2, p1)}
    assert len(keys) == 6
    # 契约缺省值补齐后入键：不传 max_depth 与传 3 是同一个键，传 2 不同
    d0 = cache.params("node.path", {"context": ctx(), "node_id": APPLY, "direction": "up"})
    d3 = cache.params("node.path", {"context": ctx(), "node_id": APPLY, "direction": "up", "max_depth": 3})
    d2 = cache.params("node.path", {"context": ctx(), "node_id": APPLY, "direction": "up", "max_depth": 2})
    assert d0 == d3 and d0 != d2


# ---------- 并发（单事件循环） ----------
@pytest.mark.anyio
async def test_concurrent_requests_all_match_uncached_results(tmp_path, lexicon):
    """知识服务是单进程单事件循环；ReadCache 的方法全是同步的（中间没有 await），在循环里天然互斥，所以不加锁。
    这里让读库替身在每次读之间让出循环，大量并发请求交错执行，结果必须与缓存关时逐字节相同、计数守恒"""
    on, off, _, _ = core_pair(tmp_path, lexicon, size=8)
    want = {i: raw(await off.call(t, make(), "orchestrator")) for i, (t, make) in enumerate(REQUESTS)}
    jobs = [(i % len(REQUESTS)) for i in range(120)]
    outs = await asyncio.gather(*[on.call(REQUESTS[i][0], REQUESTS[i][1](), "orchestrator") for i in jobs])
    assert all(raw(o) == want[i] for o, i in zip(outs, jobs))
    stats = on.cache.snapshot()  # type: ignore[union-attr]
    assert sum(s["hits"] + s["misses"] for s in stats.values()) == 120
    assert len(on.cache) <= 8  # type: ignore[arg-type]


# ---------- 观测 ----------
@pytest.mark.anyio
async def test_access_log_and_stats_carry_no_personal_information(tmp_path, lexicon, caplog):
    on, _, _, _ = core_pair(tmp_path, lexicon)
    caplog.set_level(logging.INFO, logger="starbuddy.knowledge")
    b1, b2 = node_get(edition="syn-ed-a"), node_get(edition="syn-ed-a")
    await on.call("node.get", b1, "orchestrator")
    await on.call("node.get", b2, "orchestrator")
    await on.call("item.search", {"context": ctx(), "subject": "aya"}, "orchestrator")
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "starbuddy.knowledge"]
    assert [r.get("cache") for r in records] == ["miss", "hit", None]
    for r in records:
        assert set(r) <= {"tool", "caller", "status", "code", "error_class", "request_id", "duration_ms", "cache"}
    text = json.dumps(records, ensure_ascii=False)
    assert "syn-ed-a" not in text and LINEAR not in text  # 不记请求参数
    stats = on.cache.snapshot()  # type: ignore[union-attr]
    assert set(stats) == CACHED_TOOLS
    assert {k: v for k, v in stats["node.get"].items() if k != "bytes"} == \
        {"hits": 1, "misses": 1, "evictions": 0, "bypass": 0, "not_stored": 0, "entries": 1}
    assert all(isinstance(v, int) for s in stats.values() for v in s.values())


@pytest.mark.anyio
async def test_periodic_stats_log_has_counts_only(tmp_path, lexicon, caplog):
    on, _, _, _ = core_pair(tmp_path, lexicon)
    on.cache = ReadCache(size=4, log_every=3)
    caplog.set_level(logging.INFO, logger="starbuddy.knowledge.cache")
    for body in (node_get(edition="syn-ed-a"), node_get(edition="syn-ed-a"), page_get(region="syn-region"),
                 node_get(), node_get()):
        await on.call("page.get" if "page_id" in body else "node.get", body, "orchestrator")
    await on.call("item.search", {"context": ctx(), "subject": "aya"}, "orchestrator")  # 不缓存的工具不计
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "starbuddy.knowledge.cache"]
    assert len(lines) == 1  # 5 次缓存判断、每 3 次一条
    line = lines[0]
    assert line["requests"] == 3 and set(line) == {"cache_stats", "requests", "entries", "bytes", "generation"}
    assert line["cache_stats"]["node.get"]["hits"] == 1 and line["cache_stats"]["page.get"]["misses"] == 1
    assert line["bytes"] == sum(s["bytes"] for s in line["cache_stats"].values()) > 0
    text = json.dumps(line, ensure_ascii=False)
    assert "syn-ed-a" not in text and "syn-region" not in text and LINEAR not in text and PAGE not in text


# ---------- 字节上限、不入缓存的结果、缺省值、绕过计数 ----------
def test_byte_budget_evicts_lru_and_oversized_entries_are_not_stored():
    small = ({"text": "甲" * 100}, {"source_version": "x"})
    n = entry_bytes(small)
    cache = ReadCache(size=10_000, max_bytes=64 * 2 * n)  # 总字节只放得下 128 条；单条上限 2n：放得下 small
    assert cache.entry_limit == 2 * n
    keys = [cache.key("node.get", "aya", "v", None, None, str(i)) for i in range(300)]
    for k in keys[:200]:
        assert cache.put(k, small) is True
    assert len(cache) == 128 and cache.bytes == 128 * n <= cache.max_bytes  # 条目数没到上限，按字节淘汰
    assert cache.stats["node.get"].evictions == 72
    assert cache.get(keys[71]) is None and cache.get(keys[72]) is not None  # 按 LRU 淘汰最早的
    big = ({"text": "乙" * cache.entry_limit}, {"source_version": "x"})
    before = (len(cache), cache.bytes)
    assert cache.put(keys[299], big) is False  # 单条超过上限的一部分：不入缓存、不挤掉别的条目
    assert (len(cache), cache.bytes) == before and cache.stats["node.get"].not_stored == 1
    cache.put(keys[150], small)  # 同键重放：字节数不重复累计
    assert cache.bytes == len(cache) * n
    entries = len(cache)
    assert cache.drop_tool("node.get") == entries and cache.bytes == 0 and len(cache) == 0


@pytest.mark.anyio
async def test_page_without_textbook_projections_is_not_cached(tmp_path, lexicon):
    on, off, reader, _ = core_pair(tmp_path, lexicon)
    for body in (page_get(edition="syn-ed-zz"), page_get(page_id="math.syn.eq.apply")):
        for _ in range(2):
            got = await on.call("page.get", body, "orchestrator")
            assert got[0] == 200 and raw(got) == raw(await off.call("page.get", body, "orchestrator"))
    s = on.cache.snapshot()["page.get"]  # type: ignore[union-attr]
    assert s["entries"] == 0 and s["misses"] == 4 and s["not_stored"] == 4 and s["hits"] == 0
    assert (await on.call("page.get", page_get(edition="syn-ed-a"), "orchestrator"))[0] == 200
    assert on.cache.count("page.get") == 1  # type: ignore[union-attr]  # 有投影的照常入缓存


@pytest.mark.anyio
async def test_contract_default_filled_before_keying(tmp_path, lexicon):
    on, _, reader, _ = core_pair(tmp_path, lexicon)
    await on.call("node.path", node_path("up"), "orchestrator")
    await on.call("node.path", node_path("up", max_depth=3), "orchestrator")
    s = on.cache.snapshot()["node.path"]  # type: ignore[union-attr]
    assert s["hits"] == 1 and s["entries"] == 1 and reader.calls["nodes"] == 1


@pytest.mark.anyio
async def test_all_three_bypass_kinds_are_counted(tmp_path, lexicon):
    on, _, _, _ = core_pair(tmp_path, lexicon)
    record: dict[str, Any] = {}
    named = node_get()
    named["context"]["student_ref"] = str(uuid.uuid4())
    cache = on.cache
    assert cache is not None
    await on._cached("node.get", named, cache, record)                      # 学生 ID 类字段
    await on._cached("node.get", {**node_get(), "extra": 1}, cache, record)  # 未知字段
    assert (await on.call("page.get", page_get(page_id="math.syn.eq.nope"), "orchestrator"))[0] == 404  # 页面不在快照
    on.pages = None
    assert (await on.call("page.get", page_get(), "orchestrator"))[0] == 501  # 快照没配
    stats = on.cache.snapshot()  # type: ignore[union-attr]
    assert stats["node.get"]["bypass"] == 2 and stats["page.get"]["bypass"] == 2
    assert stats["node.get"]["misses"] == 0 and stats["page.get"]["misses"] == 0
