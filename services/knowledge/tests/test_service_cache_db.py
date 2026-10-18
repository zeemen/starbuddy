"""PLT-19 第一步：库上（一次性 Postgres，合成数据）对比公共层只读缓存开关前后的 HTTP 响应逐字节相同（含错误体），
并核对应用按配置接上缓存（缺省关；开时计数在 app.state.knowledge["cache"]）。不缓存的单测在 tests/test_service_cache.py。"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from pgkge import APP_ROLE, APPLY, LINEAR, OLD, TRANSPOSE
from svc import build_pages, call, running_app, write_snapshot_dir

COMMIT = "e" * 40


@pytest.fixture(scope="module")
def db_snap(kgedb):
    from knowledge.service.reader import DbAtomSource
    return DbAtomSource(kgedb.dsn(APP_ROLE), "aya").load()


def fixed_ctx(caller: str = "orchestrator") -> dict:
    # 同一个 request_id 跑三遍（关、开未命中、开命中）：错误体回显 request_id，固定后整段字节可比
    return {"request_id": str(uuid.UUID(int=0x5EED)), "contract_version": "0.9.0", "caller": caller, "data_class": "L1"}


def cases() -> list[tuple[str, dict]]:
    c = fixed_ctx()
    return [
        ("node.get", {"context": c, "node_id": LINEAR}),
        ("node.get", {"context": c, "node_id": LINEAR, "edition": "syn-ed-a"}),
        ("node.get", {"context": c, "node_id": LINEAR, "edition": "syn-ed-b", "region": "syn-region"}),
        ("node.get", {"context": c, "node_id": LINEAR, "region": "syn-other"}),
        ("node.get", {"context": c, "node_id": LINEAR, "edition": "syn-ed-c"}),
        ("node.get", {"context": c, "node_id": OLD}),                                   # 404：已撤下的节点
        ("node.get", {"context": {**c, "map_version": "0.8.0"}, "node_id": LINEAR}),    # 404：请求的版本不是已发布版本
        ("node.path", {"context": c, "node_id": APPLY, "direction": "up"}),
        ("node.path", {"context": c, "node_id": TRANSPOSE, "direction": "down", "max_depth": 1}),
        ("page.get", {"context": c, "page_id": "math.syn.eq.linear"}),
        ("page.get", {"context": c, "page_id": "math.syn.eq.linear", "edition": "syn-ed-a"}),
        ("page.get", {"context": c, "page_id": "math.syn.eq.linear", "edition": "syn-ed-b", "region": "syn-region"}),
        ("page.get", {"context": c, "page_id": "math.syn.eq.transpose", "region": "syn-other"}),
        ("page.get", {"context": c, "page_id": "math.syn.eq.apply"}),                  # 404：快照里没有这一页
        ("item.search", {"context": c, "subject": "aya", "node_id": LINEAR}),
    ]


def pages_dir(root: Path, snapshot) -> Path:
    return write_snapshot_dir(root, build_pages(snapshot, [LINEAR, TRANSPOSE]), commit=COMMIT)


async def responses(kgedb, pages, **settings_kw) -> tuple[list[tuple[int, str, bytes]], object]:
    out = []
    async with running_app(kgedb, pages=pages, **settings_kw) as client:
        state = client.app.state.knowledge  # type: ignore[attr-defined]
        for tool, body in cases():
            r = await call(client, tool, body)
            out.append((r.status_code, r.headers.get("content-type", ""), r.content))
        return out, state.get("cache")


@pytest.mark.anyio
async def test_db_cache_on_off_responses_byte_identical(kgedb, db_snap, tmp_path):
    from knowledge.service.pages import load_snapshot_dir
    pages = load_snapshot_dir(pages_dir(tmp_path / "snap", db_snap))
    off, off_cache = await responses(kgedb, pages)
    assert off_cache is None  # 缺省关
    assert {s for s, _, _ in off} == {200, 404}
    async with running_app(kgedb, pages=pages, read_cache=True, read_cache_size=64, read_cache_max_bytes=1 << 20,
                           read_cache_log_every=7) as client:
        cache = client.app.state.knowledge["cache"]  # type: ignore[attr-defined]
        assert cache is not None and cache.size == 64 and cache.max_bytes == 1 << 20 and cache.log_every == 7
        for round_ in ("miss", "hit"):
            got = []
            for tool, body in cases():
                r = await call(client, tool, body)
                got.append((r.status_code, r.headers.get("content-type", ""), r.content))
            assert got == off, round_
        stats = cache.snapshot()
    ok = {t: sum(1 for (tool, _), (s, _, _) in zip(cases(), off) if tool == t and s == 200) for t in ("node.get", "node.path", "page.get")}
    for tool, n in ok.items():
        assert stats[tool]["hits"] == n and stats[tool]["entries"] == n, (tool, stats[tool])
