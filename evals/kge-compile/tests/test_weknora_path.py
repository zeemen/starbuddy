"""WeKnora 路径（weknora_path.py）对模拟 WeKnora 的测试：导入、按本次运行的标签检索、来源映射回原切块、清理、报告不含原文与密钥。
模拟服务在 tools/weknora-probe/weknora_probe/fake.py（只监听 127.0.0.1）；它不代表 WeKnora 的真实检索质量，
这里只核对流程与报告，数字没有意义。全部是合成数据。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import compare_retrieval as cr  # 先导入：它把 services/knowledge 加进 sys.path
import weknora_path as wp      # 它把 tools/weknora-probe 加进 sys.path
from knowledge.atoms import load_snapshot
from knowledge.retrieval.pipeline import Corpus
from knowledge.service.pages import load_snapshot_dir
from weknora_probe.client import WeKnoraClient
from weknora_probe.fake import FakeWeKnora

HERE = Path(__file__).resolve().parents[1]
FIX = HERE / "fixtures"
GOLD, MAP, SNAP = FIX / "gold-synthetic.yaml", FIX / "map-synthetic.yaml", FIX / "page-snapshot"
KEY = "sbtest-eval-key"


@pytest.fixture
def fake():
    with FakeWeKnora() as fk:
        fk.state.kbs.add("kb-test")
        fk.add_key(KEY, {"ingest", "retrieve"}, {"kb-test"})
        yield fk


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus(load_snapshot_dir(SNAP), load_snapshot(MAP))


def env_for(fk: FakeWeKnora, **extra: str) -> dict[str, str]:
    return {"WEKNORA_TARGET": "test", "WEKNORA_BASE_URL": fk.base_url, "WEKNORA_API_KEY": KEY, "WEKNORA_KB_ID": "kb-test", **extra}


def run_cli(out: Path, env, *extra: str) -> int:
    return cr.main(["--gold", str(GOLD), "--map-snapshot", str(MAP), "--page-snapshot", str(SNAP), "--out", str(out),
                    "--weknora", "--weknora-index-timeout", "5", *extra], env)


def test_retriever_maps_hits_back_to_corpus_sources(fake, corpus):
    # 测试库里已有别的内容：不应混进本次运行的检索结果
    fake.state.docs.clear()
    wc = wp.WeKnoraCorpus(WeKnoraClient(fake.base_url, KEY), "kb-test", corpus, run_id="r0000001")
    texts = [c.text for c in corpus.chunks]
    c0 = next(c for c in corpus.chunks if texts.count(c.text) == 1 and len(c.text) >= 20)   # 正文唯一，排名不会并列
    WeKnoraClient(fake.base_url, KEY).create_manual("kb-test", "别的资料", c0.text)
    assert wc.ingest(index_timeout=1, poll=0.01, sleep=lambda _: None)
    assert len(wc.by_knowledge) == len(corpus.chunks)
    r = wp.WeKnoraRetriever(wc)
    pack = r.lookup(c0.text)
    assert pack.path == "weknora" and pack.segments
    assert pack.source_keys[0] == c0.source.key and set(pack.source_keys) <= {c.source.key for c in corpus.chunks}
    assert r.lookup(c0.text).source_keys == pack.source_keys        # 同一查询结果稳定
    assert r.unmapped == 0
    assert wc.cleanup() == 0
    assert [d.title for d in fake.state.docs.values()] == ["别的资料"] and not fake.state.tags


def test_cli_three_paths_and_cleanup(fake, corpus, tmp_path):
    assert run_cli(tmp_path, env_for(fake)) == 0
    rep = json.loads((tmp_path / "compare-retrieval.json").read_text(encoding="utf-8"))
    assert set(rep["summary"]) == {"page_first", "chunk", "weknora"}
    assert set(rep["questions"][0]) == {"id", "page_first", "chunk", "weknora"}
    assert rep["method"]["weknora"] == {"target": "test", "top_k": 5, "filter": "本次运行的标签",
                                        "ingested_chunks": len(corpus.chunks), "unmapped_hits": 0}
    assert set(rep["delta_weknora_minus_chunk"]) == {"accuracy", "consistency_mean", "context_tokens_mean_ratio"}
    assert cr.WEKNORA_NOTICE in rep["notices"]
    md = (tmp_path / "compare-retrieval.md").read_text(encoding="utf-8")
    assert md.startswith("# 对照评测：页面优先 vs 切块检索 vs WeKnora（流程样例）")
    assert "| 指标 | 页面优先 | 切块检索 | WeKnora |" in md and "- WeKnora − 切块：" in md
    blob = md + (tmp_path / "compare-retrieval.json").read_text(encoding="utf-8")
    for secret in (KEY, fake.base_url, "127.0.0.1", str(tmp_path)):
        assert secret not in blob
    for c in corpus.chunks:
        if len(c.text) >= 6:
            assert c.text not in blob
    assert not fake.state.docs and not fake.state.tags                  # 导入的文档与标签已删除


def test_cli_keep_leaves_ingested_docs(fake, corpus, tmp_path):
    assert run_cli(tmp_path, env_for(fake), "--weknora-keep") == 0
    assert len(fake.state.docs) == len(corpus.chunks)
    assert all(d.title.startswith(wp.TITLE_PREFIX) for d in fake.state.docs.values())


def test_cli_refuses_without_test_target(fake, tmp_path, capsys):
    env = env_for(fake)
    del env["WEKNORA_TARGET"]
    assert run_cli(tmp_path, env) == 2
    assert "WEKNORA_TARGET=test" in capsys.readouterr().err and not fake.state.docs
    env = env_for(fake)
    del env["WEKNORA_KB_ID"]
    assert run_cli(tmp_path, env) == 2 and not fake.state.docs


def test_cli_reports_weknora_failure_and_still_cleans_up(fake, tmp_path, capsys):
    env = env_for(fake, WEKNORA_API_KEY="sbtest-wrong-key")
    assert run_cli(tmp_path, env) == 2
    err = capsys.readouterr().err
    assert "WeKnora 调用失败" in err and "sbtest-wrong-key" not in err and not fake.state.docs
