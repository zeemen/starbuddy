"""影子评审比对：与手算一致、确定性、分歧清单三类、输入校验、不写 gold/、样例报告是最新的且标注“流程样例”。全部合成数据。"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

import compare_shadow as cs

HERE = Path(__file__).resolve().parents[1]
REPORT = HERE / "fixtures" / "report-synthetic.json"
GOLD = HERE / "fixtures" / "gold-synthetic.yaml"
SAMPLE = HERE / "reports" / "sample-synthetic"
A1, A2 = "00000000-0000-4000-8000-00000000a001", "00000000-0000-4000-8000-00000000a002"


def side(items, synthetic=True, reviewers=("syn-teacher-01",)):
    return cs.Side(synthetic, dict(items), reviewers)


def test_hand_calculation_on_fixtures():
    res = cs.compare(cs.load_report(REPORT), cs.load_gold(GOLD))
    # 报告 6 项、教师 6 项；可比 5 项（A2 的 APPLY/understand 教师没判），一致 4 项（A1 的 APPLY/apply 不同）
    assert (res["report_items"], res["teacher_items"], res["comparable"], res["agree"]) == (6, 6, 5, 4)
    assert Fraction(*res["agreement"]) == Fraction(4, 5) and res["agreement_pct"] == "80.0%"
    assert Fraction(*res["coverage"]) == Fraction(5, 6) and res["coverage_pct"] == "83.3%"
    assert res["by_kind"] == {"state_mismatch": 1, "not_judged": 1, "not_in_report": 1}
    assert (res["students"], res["reviewers"], res["sample"]) == (3, 2, True)
    kinds = {(d["student_ref"], d["node_id"], d["level"]): d["kind"] for d in res["disagreements"]}
    assert kinds == {(A1, "MATH.SYN.EQ.APPLY", "apply"): "state_mismatch", (A2, "MATH.SYN.EQ.APPLY", "understand"): "not_judged",
                     (A2, "MATH.SYN.EXPR.SQUARE_DIFF", "apply"): "not_in_report"}


def test_small_cases():
    k = (A1, "MATH.SYN.EQ.LINEAR", "apply")
    res = cs.compare(side({k: "stable"}), side({k: "stable"}))
    assert res["agreement_pct"] == "100.0%" and res["disagreements"] == []
    res = cs.compare(side({}), side({k: "stable"}))
    assert res["agreement"] is None and res["agreement_pct"] == "不可计算" and res["coverage_pct"] == "不可计算"
    res = cs.compare(side({k: "stable"}, synthetic=False), side({k: "execute"}, synthetic=False))
    assert res["agreement_pct"] == "0.0%" and not res["sample"]
    assert cs._pct(Fraction(2, 3)) == "66.7%" and cs._pct(Fraction(1, 8)) == "12.5%" and cs._pct(Fraction(1, 2000)) == "0.1%"


def test_markdown_labels_sample_and_real_runs_are_not_labelled():
    md = cs.to_markdown(cs.compare(cs.load_report(REPORT), cs.load_gold(GOLD)))
    assert "流程样例" in md and "合成、非金标" in md
    k = (A1, "MATH.SYN.EQ.LINEAR", "apply")
    assert "流程样例" not in cs.to_markdown(cs.compare(side({k: "stable"}, False), side({k: "stable"}, False)))


def test_deterministic_and_sample_is_current(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert cs.main(["--report", str(REPORT), "--gold", str(GOLD), "--out", str(a)]) == 0
    assert cs.main(["--report", str(REPORT), "--gold", str(GOLD), "--out", str(b)]) == 0
    for name in ("compare-shadow.json", "compare-shadow.md"):
        assert (a / name).read_bytes() == (b / name).read_bytes() == (SAMPLE / name).read_bytes()
    text = (a / "compare-shadow.json").read_text(encoding="utf-8") + (a / "compare-shadow.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "syn-teacher" not in text  # 不含本机路径；评审人只计数


def test_refuses_to_write_under_gold(tmp_path):
    assert cs.main(["--report", str(REPORT), "--gold", str(GOLD), "--out", str(tmp_path / "gold" / "out")]) == 2
    assert not (tmp_path / "gold").exists()


def test_default_gold_is_read_only_path_and_missing_is_an_error(tmp_path):
    assert cs.main(["--report", str(REPORT), "--out", str(tmp_path / "x")]) == 2  # 真实金标 gold/ 还没有：报错，不当作通过


@pytest.mark.parametrize("patch", [
    lambda d: d.update(format="x"),
    lambda d: d.pop("synthetic"),
    lambda d: d["students"][0].update(student_ref="张三"),
    lambda d: d["students"][0].update(name="合成"),
    lambda d: d["students"][0]["judgments"][0].update(state="mastered"),
    lambda d: d["students"][0]["judgments"][0].update(level="deep"),
    lambda d: d["students"][0]["judgments"][0].update(note="自由文本"),
    lambda d: d["students"][0]["judgments"].append(dict(d["students"][0]["judgments"][0])),
])
def test_report_input_validation(tmp_path, patch):
    doc = json.loads(REPORT.read_text(encoding="utf-8"))
    patch(doc)
    p = tmp_path / "r.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(cs.InputError):
        cs.load_report(p)


def test_gold_requires_reviewer_code(tmp_path):
    import yaml
    doc = yaml.safe_load(GOLD.read_text(encoding="utf-8"))
    doc["students"][0]["reviewer"] = "王老师"
    p = tmp_path / "g.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    with pytest.raises(cs.InputError):
        cs.load_gold(p)
