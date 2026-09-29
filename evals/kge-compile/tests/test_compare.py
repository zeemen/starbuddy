"""对照评测脚本：指标与手算一致、确定性输出、报告只含汇总与题目 ID、样例报告与夹具是最新的。全部是合成数据。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

import compare_retrieval as cr  # 先导入：它把 services/knowledge 加进 sys.path
from knowledge.atoms import load_snapshot
from knowledge.retrieval.chunks import Source
from knowledge.retrieval.pipeline import ContextPack, Corpus, Segment
from knowledge.service.pages import load_snapshot_dir

HERE = Path(__file__).resolve().parents[1]
FIX = HERE / "fixtures"
GOLD = FIX / "gold-synthetic.yaml"
MAP = FIX / "map-synthetic.yaml"
SNAP = FIX / "page-snapshot"
SAMPLE = HERE / "reports" / "sample-synthetic"
LINEAR = "MATH.SYN.EQ.LINEAR"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus(load_snapshot_dir(SNAP), load_snapshot(MAP))


def run_cli(out: Path, *extra: str) -> int:
    return cr.main(["--gold", str(GOLD), "--map-snapshot", str(MAP), "--page-snapshot", str(SNAP), "--out", str(out), *extra])


# ---------- 夹具与样例报告 ----------
def test_fixture_snapshot_up_to_date():
    import sys
    sys.path.insert(0, str(FIX))
    import make_fixture  # fixtures/ 不在 pythonpath：按路径导入
    assert make_fixture.OUT == SNAP
    built = make_fixture.build()
    on_disk = {str(p.relative_to(SNAP)): p.read_bytes() for p in SNAP.rglob("*") if p.is_file()}
    assert built == on_disk, "对照评测的页面快照夹具过期：cd evals/kge-compile/fixtures && python make_fixture.py"


def test_fixtures_are_marked_synthetic():
    gold = yaml.safe_load(GOLD.read_text(encoding="utf-8"))
    assert gold["synthetic"] is True and "合成" in GOLD.read_text(encoding="utf-8").split("\n", 1)[0]
    assert json.loads((SNAP / "manifest.json").read_text(encoding="utf-8"))["synthetic"] is True
    assert all(q["node_id"].startswith("MATH.SYN.") for q in gold["questions"])


def test_sample_report_is_current_and_deterministic(tmp_path):
    assert run_cli(tmp_path / "a") == 0 and run_cli(tmp_path / "b") == 0
    for name in ("compare-retrieval.md", "compare-retrieval.json"):
        a = (tmp_path / "a" / name).read_bytes()
        assert a == (tmp_path / "b" / name).read_bytes()
        assert a == (SAMPLE / name).read_bytes(), f"样例报告过期：重跑 README 里的命令更新 {name}"
    md = (SAMPLE / "compare-retrieval.md").read_text(encoding="utf-8")
    assert md.splitlines()[2] == f"> {cr.SAMPLE_NOTICE}" and "流程样例，不是评测结论" in md


def test_report_contains_only_ids_and_numbers(tmp_path, corpus):
    assert run_cli(tmp_path) == 0
    blob = (tmp_path / "compare-retrieval.md").read_text(encoding="utf-8") + \
        (tmp_path / "compare-retrieval.json").read_text(encoding="utf-8")
    gold = cr.load_gold(GOLD)
    for q in gold.questions:
        assert q.id in blob
        assert q.question not in blob
        for p in q.paraphrases:
            assert p not in blob
        for pt in q.answer_points:
            assert pt not in blob, "答案要点不进报告"
    for c in corpus.chunks:  # 上下文原文不进报告
        if len(c.text) >= 6:
            assert c.text not in blob
    assert str(tmp_path) not in blob and str(HERE) not in blob  # 不含本机路径
    rep = json.loads((tmp_path / "compare-retrieval.json").read_text(encoding="utf-8"))
    assert set(rep["questions"][0]) == {"id", "page_first", "chunk"}


# ---------- 指标：与手算一致 ----------
def test_token_estimate_and_jaccard_by_hand():
    assert cr.estimate_tokens("ab12 一元+x") == 5  # ab12、一、元、+、x
    assert cr.estimate_tokens("  ") == 0 and cr.estimate_tokens("（x²）") == 4  # （、x、²、）
    assert cr.jaccard(set(), set()) == 1 and cr.jaccard({"a"}, {"b"}) == 0
    assert cr.jaccard({"a", "b"}, {"b", "c"}) == cr.Fraction(1, 3)


def test_judges_by_hand():
    pts = ["等量 关系", "未知数", "检验"]
    ctx = "找出等量关系，设未知数。"
    assert cr.all_points(ctx, pts) == cr.Judgement(False, 2, 3)  # 去空白后“等量关系”算覆盖
    assert cr.majority(ctx, pts) == cr.Judgement(True, 2, 3)
    assert cr.majority("未知数", pts).correct is False  # 1 / 3 不过半
    assert cr.all_points("ＡＢ，检验", ["ab"]).correct is True  # NFKC + 小写
    cr.register_judge("synthetic_any", lambda c, p: cr.Judgement(True, len(p), len(p)))
    with pytest.raises(ValueError):
        cr.register_judge("synthetic_any", cr.all_points)
    with pytest.raises(ValueError):
        cr.register_judge("Bad Name", cr.all_points)


def _pack(path: str, keys: list[str], text: str, node_id: str | None = None, fallback: str | None = None) -> ContextPack:
    segs = tuple(Segment(text if i == 0 else "", Source("atom", atom_ref=k), "chunk") for i, k in enumerate(keys))
    return ContextPack(path, node_id, 1.0, segs, fallback)


class Scripted:
    """按问题文本依次返回预设的上下文包（第 n 次调用取第 n 个，超出取最后一个）"""

    def __init__(self, script: dict[str, list[ContextPack]]) -> None:
        self.script, self.calls = script, defaultdict(int)

    def lookup(self, text: str, **_: object) -> ContextPack:
        seq = self.script[text]
        i = self.calls[text]
        self.calls[text] += 1
        return seq[min(i, len(seq) - 1)]


def _hand_gold(tmp_path: Path) -> cr.Gold:
    p = tmp_path / "gold.yaml"
    p.write_text(yaml.safe_dump({"format": "kge-gold-qa/1", "synthetic": True, "questions": [
        {"id": "H-B", "question": "问题乙", "answer_points": ["丁"]},
        {"id": "H-A", "question": "问题甲", "paraphrases": ["改写甲"], "answer_points": ["甲乙", "丙"], "node_id": LINEAR},
    ]}, allow_unicode=True), encoding="utf-8")
    return cr.load_gold(p)


def test_evaluate_summary_by_hand(tmp_path, corpus):
    """页面优先：
      H-A：取回 3 次（主问题 2 次 + 改写 1 次），来源 {s1,s2}、{s1}、{s1,s2,s3}；
           两两 Jaccard = 1/2、2/3、1/3，平均 1/2。主问题上下文“甲乙 丙”+ 空段（段间以空行连接）：要点 2/2 正确；token 3、字符 4 + 2 = 6；定位命中。
      H-B：两次都是空包 → 一致性 1；要点 0/1 不正确；token 0；兜底 no_page。
      汇总：正确率 1/2 = 0.5；要点覆盖率 2/3 = 0.6667；一致性 (1/2 + 1)/2 = 0.75；token 平均 1.5、最大 3；字符平均 3；定位命中率 1/1。
    切块：每次都是 {c1}、“丁 ab12”：H-A 要点 0/2，H-B 1/1 → 正确率 0.5；覆盖率 1/3 = 0.3333；一致性 1；token 2；定位命中率 0/1。"""
    gold = _hand_gold(tmp_path)
    pf = Scripted({"问题甲": [_pack("page_first", ["s1", "s2"], "甲乙 丙", LINEAR), _pack("page_first", ["s1"], "甲乙", LINEAR)],
                   "改写甲": [_pack("page_first", ["s1", "s2", "s3"], "x")],
                   "问题乙": [_pack("page_first", [], "", None, "no_page")]})
    chunk_pack = _pack("chunk", ["c1"], "丁 ab12")
    ch = Scripted({"问题甲": [chunk_pack], "改写甲": [chunk_pack], "问题乙": [chunk_pack]})
    rep = cr.evaluate(gold, corpus, retrievers={"page_first": pf, "chunk": ch})
    s = rep["summary"]
    assert s["page_first"] == {"questions": 2, "correct": 1, "accuracy": 0.5, "point_coverage": 0.6667, "consistency_mean": 0.75,
                               "context_tokens_mean": 1.5, "context_tokens_max": 3, "context_chars_mean": 3.0, "node_hit_rate": 1.0,
                               "fallback": {"no_node": 0, "low_confidence": 0, "no_page": 1, "page_low_coverage": 0}}
    assert s["chunk"] == {"questions": 2, "correct": 1, "accuracy": 0.5, "point_coverage": 0.3333, "consistency_mean": 1.0,
                          "context_tokens_mean": 2.0, "context_tokens_max": 2, "context_chars_mean": 6.0, "node_hit_rate": 0.0}
    assert [r["id"] for r in rep["questions"]] == ["H-A", "H-B"]  # 按题目 ID 排序
    assert rep["questions"][0]["page_first"]["consistency"] == 0.5
    assert rep["delta_page_first_minus_chunk"] == {"accuracy": 0.0, "consistency_mean": -0.25, "context_tokens_mean_ratio": 0.75}
    assert pf.calls == {"问题甲": 2, "改写甲": 1, "问题乙": 2}  # 主问题重复 runs = 2 次
    three = cr.evaluate(gold, corpus, runs=3, retrievers={"page_first": Scripted(pf.script), "chunk": Scripted(ch.script)})
    # runs = 3：H-A 来源 {s1,s2}、{s1}、{s1}、{s1,s2,s3}：两两 1/2、1/2、2/3、1、1/3、1/3，平均 10/18 = 0.5556
    assert three["questions"][0]["page_first"]["consistency"] == 0.5556


def test_notices_for_real_and_small_gold(tmp_path, corpus):
    gold = _hand_gold(tmp_path)
    real = cr.Gold(False, gold.questions, gold.digest)
    rep = cr.evaluate(real, corpus)
    assert cr.SAMPLE_NOTICE not in rep["notices"] and cr.REAL_NOTICE in rep["notices"]
    assert any("少于 50" in n for n in rep["notices"])
    many = cr.Gold(False, tuple(cr.Question(f"R-{i:03d}", "问题", ("x",)) for i in range(50)), gold.digest)
    assert cr.evaluate(many, corpus)["notices"] == [cr.REAL_NOTICE]


@pytest.mark.parametrize("doc, msg", [
    ({"format": "x"}, "format"),
    ({"format": "kge-gold-qa/1", "questions": [{"id": "Q1", "question": "q", "answer_points": ["a"]}]}, "synthetic"),
    ({"format": "kge-gold-qa/1", "synthetic": True, "questions": []}, "非空"),
    ({"format": "kge-gold-qa/1", "synthetic": True, "questions": [{"id": "Q1", "question": "绝密问题原文", "answer_points": []}]}, "Q1"),
    ({"format": "kge-gold-qa/1", "synthetic": True, "questions": [{"id": "Q1", "question": "q", "answer_points": ["a"]},
                                                                  {"id": "Q1", "question": "q", "answer_points": ["a"]}]}, "重复"),
    ({"format": "kge-gold-qa/1", "synthetic": True, "questions": [{"id": "Q1", "question": "q", "answer_points": ["a"],
                                                                   "node_id": "not-a-node"}]}, "node_id"),
    ({"format": "kge-gold-qa/1", "synthetic": True, "questions": [{"id": "Q1", "question": "q", "answer_points": ["a"],
                                                                   "student_id": "s"}]}, "字段"),
])
def test_gold_validation(tmp_path, doc, msg):
    p = tmp_path / "g.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    with pytest.raises(cr.GoldError, match=msg) as e:
        cr.load_gold(p)
    assert "绝密问题原文" not in str(e.value)


def test_cli_errors(tmp_path):
    assert run_cli(tmp_path, "--runs", "1") == 2
    assert run_cli(tmp_path, "--judge", "nope") == 2
    assert cr.main(["--gold", str(tmp_path / "none.yaml"), "--map-snapshot", str(MAP), "--out", str(tmp_path)]) == 2
