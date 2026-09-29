"""PR ③：兜底检索（分词、BM25、只切公共层、查阅路径、补页面信号、node.resolve 的全文检索）。全部是合成数据（规则 8）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import APPLY, LINEAR, SQUARE, TRANSPOSE, make_page_snapshot, map_doc, node_rec
from knowledge.atoms import SnapshotError, parse_snapshot
from knowledge.retrieval.bm25 import BM25
from knowledge.retrieval.chunks import build_chunks
from knowledge.retrieval.pipeline import (PAGE_SECTIONS, ChunkRetriever, Corpus, PageFirstRetriever)
from knowledge.retrieval.resolver import RESOLVER_VERSION, CombinedResolver, FullTextResolver
from knowledge.retrieval.signals import SIGNAL_FIELDS, GapSignal, JsonlSink, MemorySink, QueryDigester, summarize
from knowledge.retrieval.tokenize import tokenize
from knowledge.retrieval.vector import VectorSearchNotConfigured
from knowledge.service.pages import load_snapshot_dir
from knowledge.service.resolve import Candidate

PERFECT = "MATH.SYN.EXPR.PERFECT_SQUARE"
KEY = b"synthetic-digest-key-0000000001"


def with_perfect_square(doc: dict) -> dict:
    """多一个没有页面的 kp 节点（合成）与它承接的课标条目"""
    doc["nodes"].append({"id": PERFECT, "level": "node", "parent_id": "MATH.SYN.EXPR", "kind": "kp", "name": "完全平方公式",
                         "includes": ["两数和的平方等于它们的平方和加上它们积的两倍"]})
    doc["curriculum_items"].append({"standard_year": 2022, "code": "SYN-EXPR-02", "domain": "合成领域", "verb_level": "apply",
                                    "text": "合成课标条目：能用完全平方公式进行简单计算。", "source_ref": "synthetic"})
    doc["curriculum_links"].append({"node_id": PERFECT, "standard_year": 2022, "code": "SYN-EXPR-02"})
    return doc


@pytest.fixture()
def pages(tmp_path: Path):
    return load_snapshot_dir(make_page_snapshot(tmp_path / "snap"))


@pytest.fixture()
def corpus(pages):
    return Corpus(pages, parse_snapshot(with_perfect_square(map_doc())))


# ---------- 分词 ----------
def test_tokenize_deterministic_and_isolated():
    text = "解一元一次方程的一般步骤是去分母、去括号、移项"
    first = tokenize(text)
    assert first == tokenize(text)
    assert tokenize("平方差公式怎么用") == ["平方", "方差", "平方差", "公式"]  # 停用词“怎么”“用”去掉；搜索引擎模式切出短词
    import jieba  # 别处改全局分词器（加用户词）不影响这里：用的是独立实例
    jieba.add_word("一般步骤是去分母")
    assert tokenize(text) == first


def test_tokenize_strips_markup_and_stopwords():
    assert tokenize("移项 [@node:MATH.SYN.EQ.TRANSPOSE] [[MATH.SYN.EQ.LINEAR]] {{quote:syn-tp-002}}") == tokenize("移项")
    assert tokenize("的 了 是 什么 怎么 如何") == []
    assert tokenize("，。、  ") == []
    assert tokenize("ＡＢ１２") == ["ab12"]  # NFKC + 小写


# ---------- BM25：与手算一致 ----------
def test_bm25_matches_hand_calculation():
    """N = 3，avgdl = (3 + 2 + 1) / 3 = 2，k1 = 1.2，b = 0.75。
    idf(a)：df = 1 → ln(1 + 2.5 / 1.5) = 0.980829；idf(b) = idf(c)：df = 2 → ln(1 + 1.5 / 2.5) = ln 1.6 = 0.470004。
    d1 = [a, b, a]（|d| = 3，分母常数 1.2 × (0.25 + 0.75 × 1.5) = 1.65）：
        a：0.980829 × 2 × 2.2 / (2 + 1.65) = 1.182369；b：0.470004 × 1 × 2.2 / (1 + 1.65) = 0.390192；合计 1.572561
    d2 = [b, c]（|d| = 2，分母常数 1.2）：b：0.470004 × 2.2 / 2.2 = 0.470004
    d3 = [c]：不含 a、b，得 0，不返回。"""
    idx = BM25([("d3", ["c"]), ("d1", ["a", "b", "a"]), ("d2", ["b", "c"])])
    hits = idx.search(["a", "b"], 10)
    assert [(h.key, h.score) for h in hits] == [("d1", 1.572561), ("d2", 0.470004)]
    assert idx.search(["a", "b", "a", "b"], 10) == hits  # 查询词去重
    assert round(idx.idf("a"), 6) == 0.980829 and round(idx.idf("zzz"), 6) == 2.079442  # 没见过的词：df = 0 → ln(1 + 3.5 / 0.5) = ln 8
    # 覆盖率：d2 对查询 [a, b] 只命中 b：0.470004 / (0.980829 + 0.470004) = 0.323954
    assert idx.coverage(["a", "b"], ["b", "c"]) == 0.323954
    assert idx.search(["a"], 0) == [] and idx.search([], 5) == []


def test_bm25_ties_broken_by_key_and_order_independent():
    docs = [("k2", ["x", "y"]), ("k1", ["x", "y"]), ("k3", ["x", "y"])]
    a = BM25(docs).search(["x"], 3)
    b = BM25(list(reversed(docs))).search(["x"], 3)
    assert [h.key for h in a] == ["k1", "k2", "k3"] == [h.key for h in b]
    assert [h.score for h in a] == [h.score for h in b]
    with pytest.raises(ValueError):
        BM25([("k", ["x"]), ("k", ["y"])])


# ---------- 只切公共层内容 ----------
def test_chunks_only_published_public_content(pages):
    snap = parse_snapshot(map_doc())
    chunks = build_chunks(pages, snap)
    page_keys = {(pp.node_id, pp.page_version) for pp in pages.pages.values()}
    for c in chunks:
        if c.source.kind == "page":
            assert (c.source.page_id, c.source.page_version) in page_keys
            assert c.source.section != "textbook"  # 教材位置段只有原句占位，按版次渲染，不进检索
        else:
            atom = snap.atoms.get(c.source.atom_ref)
            assert atom is not None and atom.hash == c.source.atom_hash and atom.type in ("node", "curriculum")
    text = json.dumps([c.text for c in chunks], ensure_ascii=False)
    for _, quote in snap.quote_texts():
        assert quote not in text  # 教材原句不切
    assert "[@" not in text and "{{quote:" not in text and "kge:section" not in text
    assert len({c.key for c in chunks}) == len(chunks)
    sections = {c.source.section for c in chunks if c.source.kind == "page"}
    assert {"definition", "key_points", "pitfalls", "frequent_questions"} <= sections


def test_chunks_refuse_anything_but_published_snapshots(pages):
    snap = parse_snapshot(map_doc())
    with pytest.raises(TypeError):
        build_chunks(pages, map_doc())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        build_chunks({"pages": {}}, snap)  # type: ignore[arg-type]
    draft = map_doc()
    draft["status"] = "draft"
    with pytest.raises(ValueError, match="published"):
        build_chunks(pages, parse_snapshot(draft))
    doc = map_doc()
    node_rec(doc, LINEAR)["student_id"] = "syn-s-1"  # 个人层字段在快照解析时就被拒收
    with pytest.raises(SnapshotError):
        parse_snapshot(doc)


def test_chunks_skip_pages_of_retired_or_unpublished_nodes(tmp_path):
    snap_dir = make_page_snapshot(tmp_path / "s", drop=(APPLY,))
    pages = load_snapshot_dir(snap_dir)
    doc = map_doc()
    node_rec(doc, SQUARE)["status"] = "deprecated"
    chunks = build_chunks(pages, parse_snapshot(doc))
    page_ids = {c.source.page_id for c in chunks if c.source.kind == "page"}
    assert page_ids == {LINEAR, TRANSPOSE}  # APPLY 没进 manifest（未发布）；SQUARE 的节点已撤下
    assert all(c.node_id != SQUARE for c in chunks)


# ---------- 查阅路径 ----------
def test_page_first_reads_published_page_with_citations(corpus):
    sink = MemorySink()
    r = PageFirstRetriever(corpus, sink=sink, digester=QueryDigester(KEY))
    pack = r.lookup("移项的依据是什么？")
    assert pack.node_id == TRANSPOSE and pack.fallback_reason is None and sink.signals == []
    assert pack.steps == ("resolve", "page")
    assert [s.source.section for s in pack.segments] == list(PAGE_SECTIONS)
    assert all(s.source.kind == "page" and s.source.page_id == TRANSPOSE and s.source.page_version == "1.0.0" for s in pack.segments)
    assert "等式的基本性质" in pack.text and "[@" not in pack.text
    assert pack.snapshot_commit == "d" * 40 and pack.map_version == "0.9.0"
    given = r.lookup("随便问问", node_id=LINEAR)
    assert given.node_id == LINEAR and given.confidence == 1.0 and given.steps[0] == "node_given"


def test_page_first_atoms_then_fallback_when_no_page(corpus):
    sink = MemorySink()
    pack = PageFirstRetriever(corpus, sink=sink, digester=QueryDigester(KEY)).lookup("完全平方公式是什么？")
    assert pack.node_id == PERFECT and pack.fallback_reason == "no_page"
    assert pack.steps == ("resolve", "atoms", "fallback")
    atoms = [s for s in pack.segments if s.via == "atom"]
    assert [s.source.atom_ref for s in atoms] == [f"node:{PERFECT}", "curriculum:2022/SYN-EXPR-02"]
    assert all(s.source.atom_hash.startswith("sha256:") for s in atoms)
    assert [s.via for s in pack.segments].count("fallback") >= 1
    (sig,) = sink.signals
    assert sig.node_id == PERFECT and sig.reason == "no_page"
    assert sig.hits == tuple(s.source.key for s in pack.segments if s.via == "fallback")


def test_fallback_on_no_node_low_confidence_and_low_page_coverage(corpus):
    sink = MemorySink()
    r = PageFirstRetriever(corpus, sink=sink, digester=QueryDigester(KEY))
    assert r.lookup("完全无关的文字").fallback_reason == "no_node"
    pack = r.lookup("移项最容易犯什么错误？")
    assert pack.node_id == TRANSPOSE and pack.fallback_reason == "page_low_coverage"
    page_keys = {s.source.key for s in pack.segments if s.via == "page"}
    fb = [s.source.key for s in pack.segments if s.via == "fallback"]
    assert fb and not page_keys & set(fb)  # 兜底不重复页面已给的段

    class Low:
        def resolve(self, text, nodes, max_candidates):
            return [Candidate(LINEAR, "node", "kp", 0.3)]
    low = PageFirstRetriever(corpus, resolver=Low(), sink=sink, digester=QueryDigester(KEY)).lookup("一元一次方程")
    assert low.node_id == LINEAR and low.fallback_reason == "low_confidence"
    assert [s.reason for s in sink.signals] == ["no_node", "page_low_coverage", "low_confidence"]
    assert summarize(sink.signals)[0]["count"] == 1


def test_context_budget_drops_whole_segments(corpus):
    full = PageFirstRetriever(corpus).lookup("移项的依据是什么？")
    small = PageFirstRetriever(corpus).lookup("移项的依据是什么？", max_chars=len(full.segments[0].text) + 1)
    assert len(small.text) <= len(full.segments[0].text) + 1 and small.segments[0] == full.segments[0]
    assert all(s in full.segments for s in small.segments)


def test_chunk_retriever_is_bm25_over_same_chunks(corpus):
    pack = ChunkRetriever(corpus, top_k=3).lookup("平方差公式最常见的混淆")
    assert pack.path == "chunk" and len(pack.segments) == 3
    hits = corpus.search(tokenize("平方差公式最常见的混淆"), 3)
    assert pack.source_keys == tuple(c.source.key for c, _ in hits)
    assert pack.to_dict()["segments"][0]["source"]["kind"] in ("page", "atom")


# ---------- 补页面信号：不含原始查询、不含学生标识 ----------
def test_gap_signal_has_no_query_text(corpus, tmp_path):
    query = "请问完全平方公式怎么展开两数和的平方"
    sink = MemorySink()
    PageFirstRetriever(corpus, sink=sink, digester=QueryDigester(KEY)).lookup(query)
    (sig,) = sink.signals
    d = sig.to_dict()
    assert tuple(d) == SIGNAL_FIELDS
    blob = json.dumps(d, ensure_ascii=False)
    assert query not in blob
    for t in tokenize(query):
        if len(t) >= 2:
            assert t not in blob.replace(PERFECT, "")
    assert not {"student_id", "text", "query", "user", "session"} & set(d)
    js = JsonlSink(tmp_path / "signals.jsonl")
    js.emit(sig)
    line = json.loads((tmp_path / "signals.jsonl").read_text(encoding="utf-8"))
    assert set(line) == set(SIGNAL_FIELDS) and query not in json.dumps(line, ensure_ascii=False)


def test_query_digest_is_keyed():
    a, b = QueryDigester(KEY), QueryDigester(b"another-synthetic-key-000000002")
    assert a.digest("移项") == a.digest(" 移项 ") and len(a.digest("移项")) == 16
    assert a.digest("移项") != b.digest("移项")
    assert QueryDigester().digest("移项") != QueryDigester().digest("移项")  # 缺省每个进程（实例）随机密钥
    with pytest.raises(ValueError):
        QueryDigester(b"short")
    with pytest.raises(ValueError):
        GapSignal(None, "whatever", "0" * 16, 1, (), 0.0)


# ---------- node.resolve 的全文检索（经 Resolver 接口） ----------
NODES = [dict(r) for r in (map_doc()["nodes"])]


def test_fulltext_resolver_confidence_and_threshold():
    r = FullTextResolver()
    out = r.resolve("两数和乘两数差", NODES, 5)  # 名称不在文本里，规则解析不命中；全文命中 includes
    assert [c.node_id for c in out] == [SQUARE] and 0 < out[0].confidence <= 0.6
    assert r.resolve("方程的根与系数", NODES, 5) == []  # 覆盖率低于 0.34 不算命中
    assert r.resolve("的了吗", NODES, 5) == []
    assert all(c.node_id not in ("MATH.SYN", "MATH.SYN.EQ") for c in r.resolve("合成方程单元 方程", NODES, 5))
    assert r.resolve("两数和乘两数差", list(reversed(NODES)), 5) == out


def test_combined_resolver_takes_max_per_node():
    class Fixed:
        def __init__(self, cands):
            self.cands = cands

        def resolve(self, text, nodes, max_candidates):
            return self.cands[:max_candidates]
    c = CombinedResolver([Fixed([Candidate("X.A", "node", None, 0.3), Candidate("X.B", "node", None, 0.4)]),
                          Fixed([Candidate("X.A", "node", None, 0.8)])])
    assert [(x.node_id, x.confidence) for x in c.resolve("t", [], 5)] == [("X.A", 0.8), ("X.B", 0.4)]
    assert len(c.resolve("t", [], 1)) == 1
    real = CombinedResolver().resolve("一元一次方程怎么移项", NODES, 5)
    assert [x.node_id for x in real[:2]] == [LINEAR, TRANSPOSE] and real[0].confidence == 0.9  # 规则分数保留


def test_tool_core_uses_rule_plus_fulltext_by_default():
    from knowledge.service.contract import contract
    from knowledge.service.tools import ToolCore
    core = ToolCore(None, contract())  # type: ignore[arg-type]
    assert core.resolver.version == RESOLVER_VERSION == "algo:kge-node-resolve@0.2.0"
    assert [c.node_id for c in core.resolver.resolve("两数和乘两数差", NODES, 5)] == [SQUARE]


def test_vector_search_is_interface_only():
    with pytest.raises(NotImplementedError):
        VectorSearchNotConfigured().search("x", 3)
