"""WeKnora 后端（KGE-12 C）：交付清单校验、允许清单过滤、ID 映射、standard.search 的输出符合契约 v0.10 候选版、
查询文本不进日志与异常、出错时的退路；库上：开关开时 item.similar 的候选经 kge.item_tag 再核对，开关关时行为不变。
只用模拟 WeKnora（tools/weknora-probe 的 fake，只监听 127.0.0.1）与合成数据（MATH.SYN.*、syn-*）。"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from conftest import ROOT
from knowledge.service.config import Settings
from knowledge.weknora import UpstreamError
from knowledge.weknora.backend import WeKnoraBackend
from knowledge.weknora.client import WeKnoraClient
from knowledge.weknora.filters import AllowlistStrategy, FilterTooBroad
from knowledge.weknora.manifest import FORMAT, ManifestError, load_manifest, parse_manifest
from weknora_probe.fake import FakeWeKnora

pytestmark = pytest.mark.anyio
INGEST, READ = "syn-ingest-key-000000000000000000", "syn-retrieve-key-00000000000000000"
KB = "kb-syn-std"
GRAPH = "MATH.FUNC.LINEAR.GRAPH"
LINEAR = "MATH.SYN.EQ.LINEAR"
FILTERS = {"subject": "aya", "grade": 8, "province": "syn-province", "publisher": "syn-press", "edition": "syn-ed-2024",
           "semester": "first", "node_ids": [GRAPH]}
QUERY = "合成查询：一次函数图像经过哪几个象限"
TB = {"subject": "aya", "grade": 8, "province": "syn-province", "publisher": "syn-press", "edition": "syn-ed-2024", "semester": "first"}
EXAM = {"subject": "aya", "grade": 8, "province": "syn-province", "exam_type": "zhongkao", "exam_year": 2025, "exam_region": "syn-city"}

# （名字, 正文, 清单条目里除 weknora_id 以外的字段；None = 只在 WeKnora 里、不在清单里）
DOCS = [
    ("tb1", "合成正文：一次函数的图像是一条直线，k 与 b 的符号决定它经过的象限。",
     {"content_id": "syn-zt:tb-0001", "content_version": 3, "content_type": "textbook_section", "status": "active", **TB,
      "node_ids": [GRAPH], "source": {"title": "合成教材八年级上册", "org": "合成出版社", "year": 2024, "locator": "第 80 页"}}),
    ("tb_other_press", "合成正文：一次函数图像经过的象限由 k 与 b 决定。",
     {"content_id": "syn-zt:tb-0002", "content_version": 1, "content_type": "textbook_section", "status": "active",
      **{**TB, "publisher": "syn-press-b"}, "node_ids": [GRAPH], "source": {"title": "合成教材乙"}}),
    ("page1", "合成编译页段落：画一次函数图像时先找与两条坐标轴的交点，再判断经过哪几个象限。",
     {"content_id": "syn-zt:page-0001", "content_version": 1, "content_type": "compiled_page", "status": "active", **TB,
      "node_ids": [GRAPH], "page_ref": {"node_id": GRAPH, "page_version": "1.0.0"}, "source": {"title": "合成编译页"}}),
    ("exam1", "合成中考题：一次函数 y=2x+1 的图像经过哪几个象限？",
     {"content_id": "syn-zt:item-0101", "content_version": 2, "content_type": "item", "status": "active", **EXAM,
      "node_ids": [GRAPH], "item_ref": "aya:SYN-E1", "source": {"title": "合成中考卷", "year": 2025, "locator": "第 5 题"}}),
    ("tb_superseded", "合成正文（旧版）：一次函数图像经过哪几个象限，看 k 与 b。",
     {"content_id": "syn-zt:tb-0001", "content_version": 2, "content_type": "textbook_section", "status": "superseded", **TB,
      "node_ids": [GRAPH], "source": {"title": "合成教材八年级上册"}}),
    ("tb_grade9", "合成正文：一次函数图像经过的象限（九年级复习）。",
     {"content_id": "syn-zt:tb-0009", "content_version": 1, "content_type": "textbook_section", "status": "active",
      **{**TB, "grade": 9}, "node_ids": [GRAPH], "source": {"title": "合成教材九年级"}}),
    ("undelivered", "合成：一次函数图像经过哪几个象限（没有交付记录的文档）。", None),
    # 相似题（库里的合成题 SYN-I1…I5，见 tests/pgkge.py）
    ("i1", "合成题目：解一元一次方程 3x+5=11，先移项再把系数化为一。",
     {"content_id": "syn-zt:item-0001", "content_version": 1, "content_type": "item", "status": "active", **EXAM,
      "node_ids": [LINEAR], "item_ref": "aya:SYN-I1", "source": {"title": "合成练习"}}),
    ("i2", "合成题目：解一元一次方程 2x+1=7，先移项再把系数化为一。",
     {"content_id": "syn-zt:item-0002", "content_version": 1, "content_type": "item", "status": "active", **EXAM,
      "node_ids": [LINEAR], "item_ref": "aya:SYN-I2", "source": {"title": "合成练习"}}),
    ("i3", "合成题目：列一元一次方程解决行程问题，先设未知数。",
     {"content_id": "syn-zt:item-0003", "content_version": 1, "content_type": "item", "status": "active", **EXAM,
      "node_ids": [LINEAR], "item_ref": "aya:SYN-I3", "source": {"title": "合成练习"}}),
    ("i4", "合成题目：解一元一次方程 5x+1=11，先移项再把系数化为一。",   # 打标待审（库里 pending）：必须被 SQL 核对挡掉
     {"content_id": "syn-zt:item-0004", "content_version": 1, "content_type": "item", "status": "active", **EXAM,
      "node_ids": [LINEAR], "item_ref": "aya:SYN-I4", "source": {"title": "合成练习"}}),
    ("z1", "合成题目：解一元一次方程 3x+5=11，先移项再把系数化为一（另一学科）。",
     {"content_id": "syn-zt:item-0901", "content_version": 1, "content_type": "item", "status": "active",
      **{**EXAM, "subject": "zhixing"}, "item_ref": "zhixing:SYN-Z1", "source": {"title": "合成练习"}}),
]


@pytest.fixture
def world(tmp_path):
    """模拟 WeKnora + 交付清单文件。返回 (fake, 文档名 → WeKnora ID, 清单路径)"""
    with FakeWeKnora() as fk:
        fk.add_kb(KB)
        fk.add_key(INGEST, {"ingest", "retrieve"}, {KB})
        fk.add_key(READ, {"retrieve"}, {KB})
        admin = WeKnoraClient(fk.base_url, INGEST, timeout=5)
        ids = {name: admin.create_manual(KB, f"合成文档 {name}", text) for name, text, _ in DOCS}
        entries = [{**meta, "weknora_id": ids[name]} for name, _, meta in DOCS if meta is not None]
        path = tmp_path / "delivery.json"
        path.write_text(json.dumps({"format": FORMAT, "kb": {"name": "syn-std-kb", "version": "20261107.1", "ids": [KB]},
                                    "entries": entries}, ensure_ascii=False), encoding="utf-8")
        yield fk, ids, path


def backend_for(fk, path) -> WeKnoraBackend:
    return WeKnoraBackend(WeKnoraClient(fk.base_url, READ, timeout=5), load_manifest(path))


def request(**kw) -> dict:
    body = {"context": {"request_id": "00000000-0000-4000-8000-00000000c101", "contract_version": "0.10.0", "caller": "orchestrator",
                        "data_class": "L2"}, "query": QUERY, "filters": dict(FILTERS), "content_types": ["textbook_section", "compiled_page"],
            "top_k": 5}
    body.update(kw)
    return body


# ---------- 契约 v0.10 候选版的校验器（只在测试里用；服务端在签字前仍按 v0.9） ----------
OPENAPI = ROOT / "packages" / "contracts" / "openapi"


def v010_validator(schema: str) -> Draft202012Validator:
    reg = Registry()
    for name in ("knowledge-service.v0.10.yaml", "plugin-api.v0.9.yaml"):
        p = (OPENAPI / name).resolve()
        reg = reg.with_resource(p.as_uri(), Resource.from_contents(yaml.safe_load(p.read_text(encoding="utf-8")),
                                                                   default_specification=DRAFT202012))
    uri = (OPENAPI / "knowledge-service.v0.10.yaml").resolve().as_uri()
    return Draft202012Validator({"$ref": f"{uri}#/components/schemas/{schema}"}, registry=reg, format_checker=FormatChecker())


def envelope(result: dict, meta: dict) -> dict:
    """与 tools.py 组信封的做法相同"""
    return {"contract_version": "0.10.0", "result": result, "evidence_ids": [], "confidence": meta["confidence"],
            "source_version": meta["source_version"], "model_version": meta["model_version"], "human_review_required": False}


# ---------- 交付清单 ----------
def _doc(entries: list[dict]) -> dict:
    return {"format": FORMAT, "kb": {"name": "syn-std-kb", "version": "1", "ids": [KB]}, "entries": entries}


GOOD = {**DOCS[0][2], "weknora_id": "syn-wk-0001"}


@pytest.mark.parametrize("bad", [
    {**GOOD, "answer": "（合成）B"},                                          # 未定义字段
    {k: v for k, v in GOOD.items() if k != "source"},                        # 缺来源
    {**GOOD, "content_type": "item"},                                        # 题目没有 item_ref
    {**GOOD, "item_ref": "aya:SYN-I1"},                                      # 非题目带 item_ref
    {**GOOD, "content_type": "item", "item_ref": "wenli:SYN-I1"},            # item_ref 学科不一致
    {**GOOD, "content_type": "compiled_page"},                               # 编译页没有 page_ref
    {**GOOD, "grade": 6},
    {**GOOD, "province": "Syn Province"},
    {**GOOD, "status": "deleted"},
    {**GOOD, "node_ids": ["not-a-node"]},
    {**GOOD, "source": {"title": "合成", "url": "https://example.invalid/"}},
    {**GOOD, "weknora_id": "../etc/passwd"},
])
def test_manifest_rejects_bad_entries_without_echoing_values(bad):
    with pytest.raises(ManifestError) as ei:
        parse_manifest(_doc([bad]))
    assert "合成" not in str(ei.value) and "example.invalid" not in str(ei.value) and "passwd" not in str(ei.value)


def test_manifest_rejects_duplicates_and_bad_files(tmp_path):
    with pytest.raises(ManifestError, match="weknora_id 重复"):
        parse_manifest(_doc([GOOD, {**GOOD, "content_id": "syn-zt:other"}]))
    with pytest.raises(ManifestError, match="content_id"):
        parse_manifest(_doc([GOOD, {**GOOD, "weknora_id": "syn-wk-0002"}]))
    parse_manifest(_doc([GOOD, {**GOOD, "weknora_id": "syn-wk-0002", "status": "superseded"}]))   # 旧版本可以同 content_id
    with pytest.raises(ManifestError):
        parse_manifest({**_doc([GOOD]), "format": "kge-weknora-delivery/0"})
    with pytest.raises(ManifestError):
        parse_manifest({**_doc([GOOD]), "extra": 1})
    real = tmp_path / "m.json"
    real.write_text(json.dumps(_doc([GOOD])), encoding="utf-8")
    assert load_manifest(real).source_version == "items:syn-std-kb@1"
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ManifestError, match="符号链接"):
        load_manifest(link)
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "bad.json")


# ---------- 允许清单 ----------
def test_allowlist_plan_matches_filters(world):
    fk, ids, path = world
    m = load_manifest(path)
    plan = AllowlistStrategy(m).plan(FILTERS, ["textbook_section", "compiled_page", "item"])
    # 出版社不同、已取代、年级不同、只在 WeKnora 里的文档都不在；试卷题没有出版社与学期，视为不适用，留下
    assert set(plan.knowledge_ids) == {ids["tb1"], ids["page1"], ids["exam1"]}
    assert plan.kb_ids == (KB,)
    assert set(AllowlistStrategy(m).plan({**FILTERS, "exam_type": "zhongkao"}, ["textbook_section", "item"]).knowledge_ids) == {ids["exam1"]}
    assert AllowlistStrategy(m).plan({**FILTERS, "node_ids": [LINEAR]}, ["textbook_section"]).empty
    with pytest.raises(FilterTooBroad):
        AllowlistStrategy(m, max_ids=1).plan(FILTERS, ["textbook_section", "compiled_page"])


# ---------- standard.search ----------
async def test_standard_search_filters_maps_ids_and_conforms_to_v010(world):
    fk, ids, path = world
    req = request(content_types=["textbook_section", "compiled_page", "item"])
    assert not list(v010_validator("StandardSearchRequest").iter_errors(req))
    result, meta = await backend_for(fk, path).standard_search(req)
    hits = result["hits"]
    assert {h["weknora_id"] for h in hits} == {ids["tb1"], ids["page1"], ids["exam1"]}
    by = {h["weknora_id"]: h for h in hits}
    assert by[ids["tb1"]]["content_id"] == "syn-zt:tb-0001" and by[ids["tb1"]]["content_version"] == 3
    assert by[ids["tb1"]]["source"] == {"title": "合成教材八年级上册", "org": "合成出版社", "year": 2024, "locator": "第 80 页"}
    assert by[ids["page1"]]["page_ref"] == {"node_id": GRAPH, "page_version": "1.0.0"} and "item_ref" not in by[ids["page1"]]
    assert by[ids["exam1"]]["item_ref"] == "aya:SYN-E1" and by[ids["exam1"]]["content_type"] == "item"
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert meta["source_version"] == "items:syn-std-kb@20261107.1" and meta["confidence"] == hits[0]["score"] and meta["dropped"] == 0
    env = envelope(result, meta)
    assert not sorted(e.message for e in v010_validator("StandardSearchResponse").iter_errors(env))


async def test_standard_search_top_k_and_empty_allowlist_skips_weknora(world):
    fk, ids, path = world
    b = backend_for(fk, path)
    result, _ = await b.standard_search(request(top_k=1))
    assert len(result["hits"]) == 1
    before = len(fk.state.queries)
    result, meta = await b.standard_search(request(filters={**FILTERS, "province": "syn-elsewhere"}))
    assert result == {"hits": []} and meta["confidence"] == 0 and len(fk.state.queries) == before   # 不做全库检索
    assert not list(v010_validator("StandardSearchResponse").iter_errors(envelope(result, meta)))


async def test_hits_outside_allowlist_are_dropped_even_if_weknora_ignores_filters(world):
    fk, ids, path = world
    fk.state.defects.ignore_filters = True
    result, meta = await backend_for(fk, path).standard_search(request())
    assert {h["weknora_id"] for h in result["hits"]} == {ids["tb1"], ids["page1"]}
    assert meta["dropped"] > 0


async def test_query_text_never_logged_or_in_errors(world, caplog):
    fk, ids, path = world
    caplog.set_level(logging.DEBUG)
    b = backend_for(fk, path)
    await b.standard_search(request())
    assert QUERY not in caplog.text and "一次函数" not in caplog.text
    b.client.base_url = "http://127.0.0.1:9"     # 连不上
    with pytest.raises(UpstreamError) as ei:
        await b.standard_search(request())
    assert QUERY not in str(ei.value) and "一次函数" not in str(ei.value) and ei.value.status is None
    assert QUERY not in caplog.text


# ---------- 相似题（后端部分） ----------
async def test_similar_items_from_weknora(world):
    fk, ids, path = world
    b = backend_for(fk, path)
    sim = await b.similar_items("aya:SYN-I1", 5)
    assert sim.reason == "weknora" and sim.refs is not None
    assert "aya:SYN-I1" not in sim.refs and "zhixing:SYN-Z1" not in sim.refs and sim.refs[0] in ("aya:SYN-I2", "aya:SYN-I4")
    assert set(sim.refs) <= {"aya:SYN-I2", "aya:SYN-I3", "aya:SYN-I4", "aya:SYN-E1"}
    assert (await b.similar_items("aya:SYN-I3x", 5)).refs is None        # 没交付进 WeKnora：调用方走 SQL
    narrow = WeKnoraBackend(b.client, b.manifest, AllowlistStrategy(b.manifest, max_ids=1))
    assert await narrow.similar_items("aya:SYN-I1", 5) == type(sim)(None, "too_broad")   # 允许清单装不下：走 SQL，不回 500
    fk.state.defects.ignore_filters = True
    sim2 = await b.similar_items("aya:SYN-I1", 5)
    assert "zhixing:SYN-Z1" not in sim2.refs and sim2.dropped > 0


# ---------- 配置 ----------
def test_settings_flag_default_off_and_manifest_required(tmp_path):
    base = {"KNOWLEDGE_ENV": "test", "KNOWLEDGE_DATABASE_URL": "postgresql://unused", "KNOWLEDGE_TOKEN_BFF": "b" * 32}
    s = Settings.from_env(base)
    assert s.weknora_backend is False and s.weknora_manifest is None and s.weknora_timeout_ms == 3000
    for bad in ({"KNOWLEDGE_WEKNORA_BACKEND": "yes"}, {"KNOWLEDGE_WEKNORA_BACKEND": "1"},
                {"KNOWLEDGE_WEKNORA_BACKEND": "1", "KNOWLEDGE_WEKNORA_MANIFEST": "relative.json"},
                {"KNOWLEDGE_WEKNORA_TIMEOUT_MS": "50"}):
        with pytest.raises(RuntimeError):
            Settings.from_env({**base, **bad})
    s = Settings.from_env({**base, "KNOWLEDGE_WEKNORA_BACKEND": "1", "KNOWLEDGE_WEKNORA_MANIFEST": str(tmp_path / "m.json")})
    assert s.weknora_backend is True and s.weknora_manifest == tmp_path / "m.json"


def test_build_weknora_requires_test_target_and_valid_manifest(world, monkeypatch):
    from knowledge.service.app import build_weknora
    fk, ids, path = world
    s = Settings(env="test", database_url="postgresql://unused", tokens={"bff": "b" * 32}, weknora_backend=True,
                 weknora_manifest=path, weknora_timeout_ms=1500)
    monkeypatch.setenv("WEKNORA_BASE_URL", fk.base_url)
    monkeypatch.setenv("WEKNORA_API_KEY", READ)
    monkeypatch.delenv("WEKNORA_TARGET", raising=False)
    with pytest.raises(RuntimeError) as ei:
        build_weknora(s)
    assert READ not in str(ei.value)
    monkeypatch.setenv("WEKNORA_TARGET", "test")
    b = build_weknora(s)
    assert b.client.timeout == 1.5 and READ not in repr(b.client)
    bad = path.with_name("bad.json")
    bad.write_text(json.dumps({"format": FORMAT}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="WeKnora 后端配置不合格"):
        build_weknora(Settings(**{**s.__dict__, "weknora_manifest": bad}))


# ---------- 库上：item.similar 开关开 / 关 ----------
async def test_item_similar_with_weknora_rechecks_tags_in_db(world, kgedb, caplog):
    from svc import call, ctx, running_app
    fk, ids, path = world
    caplog.set_level(logging.INFO, logger="starbuddy.knowledge")
    body = {"context": ctx(), "item_ref": "aya:SYN-I1"}
    async with running_app(kgedb) as client:
        off = (await call(client, "item.similar", copy.deepcopy(body))).json()
    async with running_app(kgedb, weknora=backend_for(fk, path)) as client:
        r = await call(client, "item.similar", copy.deepcopy(body))
        assert r.status_code == 200, r.text
        on = r.json()
        refs = [i["item"]["item_ref"] for i in on["result"]["items"]]
        # SYN-I4 打标待审、SYN-E1 不在已发布版本、SYN-Z1 是另一学科：都不出现；I2 同题族、I3 不同
        assert refs == ["aya:SYN-I2", "aya:SYN-I3"]
        assert [i["relation"] for i in on["result"]["items"]] == ["same_family", "similar"]
        assert on["source_version"].endswith(";items:syn-std-kb@20261107.1") and on["model_version"] == "algo:kge-item-similar-weknora@0.1.0"
        assert on["result"]["items"][0] == off["result"]["items"][0]          # 题目视图与 SQL 实现同一份
        logs = [json.loads(m) for m in caplog.messages if m.startswith("{") and '"item.similar"' in m]
        assert logs[-1]["backend"] == "weknora" and "合成题目" not in caplog.text


async def test_item_similar_not_delivered_uses_sql_unchanged(world, kgedb, caplog):
    """题目没交付进 WeKnora（清单里没有它）：走 SQL，响应与开关关时逐字段相同，访问日志记 sql_not_delivered"""
    from svc import call, ctx, running_app
    fk, ids, path = world
    caplog.set_level(logging.INFO, logger="starbuddy.knowledge")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"] = [e for e in doc["entries"] if e.get("item_ref") != "aya:SYN-I2"]
    b = WeKnoraBackend(WeKnoraClient(fk.base_url, READ, timeout=5), parse_manifest(doc))
    body = {"context": ctx(), "item_ref": "aya:SYN-I2"}
    async with running_app(kgedb) as client:
        off = (await call(client, "item.similar", copy.deepcopy(body))).json()
    async with running_app(kgedb, weknora=b) as client:
        on = (await call(client, "item.similar", copy.deepcopy(body))).json()
    assert {k: v for k, v in on.items()} == {k: v for k, v in off.items()}
    logs = [json.loads(m) for m in caplog.messages if m.startswith("{") and '"item.similar"' in m]
    assert "backend" not in logs[0] and logs[-1]["backend"] == "sql_not_delivered"


async def test_item_similar_falls_back_to_sql_when_weknora_is_down(world, kgedb, caplog):
    from svc import call, ctx, running_app
    fk, ids, path = world
    caplog.set_level(logging.INFO, logger="starbuddy.knowledge")
    b = backend_for(fk, path)
    b.client.base_url = "http://127.0.0.1:9"
    body = {"context": ctx(), "item_ref": "aya:SYN-I1"}
    async with running_app(kgedb) as client:
        off = (await call(client, "item.similar", copy.deepcopy(body))).json()
    async with running_app(kgedb, weknora=b) as client:
        on = (await call(client, "item.similar", copy.deepcopy(body))).json()
    assert on["result"] == off["result"] and on["source_version"] == off["source_version"] and on["model_version"] == off["model_version"]
    logs = [json.loads(m) for m in caplog.messages if m.startswith("{") and '"item.similar"' in m]
    assert logs[-1]["backend"] == "sql_fallback" and "backend" not in logs[0]


async def test_items_by_ids_sql_filters_without_rls(kgedb):
    """第二道防线：在不受 RLS 约束的连接上，items_by_ids 也只取已发布版本里打标审核通过的题（待审、驳回、只在草稿里的都不取）"""
    import asyncpg

    from knowledge.service.reader import KgeReader
    pool = await asyncpg.create_pool(**kgedb.connect_kwargs("postgres"), min_size=1, max_size=2)
    try:
        r = KgeReader(pool)
        v = await r.published_version("aya")
        rows = await r.items_by_ids(v.id, "aya", ["SYN-I1", "SYN-I4", "SYN-I5", "SYN-I6", "SYN-NOPE"])
        assert [x["item_id"] for x in rows] == ["SYN-I1"]
        assert await r.items_by_ids(v.id, "zhixing", ["SYN-I1"]) == [] and await r.items_by_ids(v.id, "aya", []) == []
    finally:
        await pool.close()
