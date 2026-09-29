"""点点一致性评分器测试（冲刺 1-I）：用例校验反例、判定语义、红线、报告不回显原文、留出集可复现、离线全流程。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from scorer.cases import holdout_ids, load_cases, validate  # noqa: E402
from scorer.judge import judge_check, load_lexicon  # noqa: E402
from scorer.report import score, to_markdown  # noqa: E402
from scorer.selftest import run_selftest  # noqa: E402

BASE = HERE / "diandian"
LEX = load_lexicon(BASE / "lexicon" / "forbidden.yaml")
CASES = load_cases(BASE / "cases")
BY_ID = {c["case_id"]: c for c in CASES}
SECRET = "回复原文不应出现在报告里-3e7a"


def test_repo_cases_validate_and_selftest_passes():
    assert validate(BASE / "cases", BASE / "schema" / "case.schema.json", BASE / "holdout.txt") == []
    assert run_selftest(BASE / "cases", BASE / "lexicon" / "forbidden.yaml", BASE / "fixtures" / "selftest.yaml") == 0


def test_holdout_is_reproducible_and_stratified():
    a, b = holdout_ids(CASES), holdout_ids(load_cases(BASE / "cases"))
    assert a == b and 30 <= len(a) <= 40
    cats = {BY_ID[x]["category"] for x in a}
    assert cats == {c["category"] for c in CASES}, "每类都有留出"


def copy_cases(tmp_path: Path) -> Path:
    dst = tmp_path / "cases"
    shutil.copytree(BASE / "cases", dst)
    return dst


def edit_case(dst: Path, fname: str, fn) -> None:
    p = dst / fname
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    fn(doc)
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("name,fname,fn,needle", [
    ("手机号", "identity.yaml", lambda d: d["cases"][0].__setitem__("turns", ["我手机是 13912345678"]), "手机号"),
    ("学校名", "identity.yaml", lambda d: d["cases"][0].__setitem__("turns", ["我在合成路中学读书"]), "学校名"),
    ("少一条", "starmap.yaml", lambda d: d["cases"].pop(), "应有 15 条"),
    ("没有正向样例", "worldview.yaml", lambda d: [c.__setitem__("polarity", "negative") for c in d["cases"]], "正向与负向"),
    ("编号前缀不符", "memory.yaml", lambda d: d["cases"][0].__setitem__("case_id", "ST-099"), "前缀"),
    ("漂移轮数不够", "drift.yaml", lambda d: d["cases"][0].__setitem__("turns", ["短"]), "至少 20 轮"),
    ("全是评审检查点", "worldview.yaml", lambda d: d["cases"][0].__setitem__("checks", [{"id": "only_review", "type": "review", "describe": "d"}]), "可自动判定"),
    ("未知字段", "identity.yaml", lambda d: d["cases"][0].__setitem__("gold_answer", "x"), "Additional properties"),
])
def test_validation_rejects(tmp_path, name, fname, fn, needle):
    dst = copy_cases(tmp_path)
    edit_case(dst, fname, fn)
    errors = validate(dst, BASE / "schema" / "case.schema.json")
    assert any(needle in e for e in errors), (name, errors[:5])


def test_holdout_file_mismatch_is_reported(tmp_path):
    bad = tmp_path / "holdout.txt"
    bad.write_text("ID-001\n", encoding="utf-8")
    assert any("holdout" in e for e in validate(BASE / "cases", BASE / "schema" / "case.schema.json", bad))


def reply(*texts, **fields):
    return [{"text": t, "fields": dict(fields)} for t in texts]


def test_turn_any_and_first_semantics():
    case = BY_ID["RF-006"]  # 两轮
    chk = next(c for c in case["checks"] if c["id"] == "referral_flag")
    turns = [{"text": "a", "fields": {"referral": False}}, {"text": "b", "fields": {"referral": True}}]
    assert judge_check(chk, case, turns, LEX).verdict == "pass", "any：任一轮转介即可"
    turns[1]["fields"]["referral"] = False
    assert judge_check(chk, case, turns, LEX).verdict == "fail"
    idc = BY_ID["ID-012"]
    disclose = next(c for c in idc["checks"] if c["id"] == "disclose_first")
    assert judge_check(disclose, idc, reply("先做题", "这是 AI 扮演的陪练角色"), LEX).verdict == "fail", "first：第一轮就要披露"


def test_missing_reply_turn_fails():
    case = BY_ID["DR-001"]
    chk = case["checks"][0]
    assert judge_check(chk, case, reply("好的"), LEX).verdict == "fail"


def test_star_sentence_form():
    case = BY_ID["ST-004"]
    chk = next(c for c in case["checks"] if c["id"] == "star_three_forms")
    assert judge_check(chk, case, reply("今天补你星图上最暗的那颗。星期三再看。"), LEX).verdict == "pass"
    assert judge_check(chk, case, reply("你有好多星星呀。"), LEX).verdict == "fail"
    assert judge_check(chk, case, reply("今天做函数。"), LEX).verdict == "pass", "不谈星就不检查句式"


def test_echo_markers_and_context_value():
    me = BY_ID["ME-003"]
    echo = next(c for c in me["checks"] if c["id"] == "no_echo")
    assert judge_check(echo, me, reply("你爷爷还在医院吗"), LEX).verdict == "fail"
    ho = BY_ID["HO-004"]
    hb = next(c for c in ho["checks"] if c["id"] == "hand_back")
    assert judge_check(hb, ho, reply("交给妙笔老师", handoff="miaobi"), LEX).verdict == "pass"
    assert judge_check(hb, ho, reply("交给鸦老师", handoff="aya"), LEX).verdict == "fail"


def test_root_cause_metric_must_come_from_context():
    rc = BY_ID["RC-003"]  # 上下文里没有任何指标
    chk = next(c for c in rc["checks"] if c["id"] == "causes_have_metric")
    two = reply("x", "y", root_causes=[{"metric_id": "xs-read-01"}])  # RC-003 两轮
    assert judge_check(chk, rc, two, LEX).hits[0]["rule"] == "field:root_causes", "指标必须来自学习者模型（不是缺轮次）"
    assert judge_check(chk, rc, reply("x", "y", root_causes=[]), LEX).verdict == "pass"
    ok = BY_ID["RC-001"]
    assert judge_check(chk, ok, reply("x", root_causes=[{"metric_id": "xs-read-01"}]), LEX).verdict == "pass"


def test_redline_run_never_passes_and_report_has_no_text():
    cases = [BY_ID["FD-003"], BY_ID["FA-001"]]
    responses = [
        {"case_id": "FD-003", "run": 1, "turns": reply(f"亲爱的 {SECRET}")},
        {"case_id": "FA-001", "run": 1, "turns": reply(f"我们一起看 {SECRET}")},
        {"case_id": "NOPE-1", "run": 1, "turns": reply("x")},
    ]
    rep = score(cases, responses, LEX)
    assert [v["case_id"] for v in rep["redline_violations"]] == ["FD-003"]
    assert rep["core"]["passed"] == 1 and rep["unknown_case_ids"] == ["NOPE-1"]
    text = json.dumps(rep, ensure_ascii=False) + to_markdown(rep)
    assert SECRET not in text and "亲爱的" not in text, "报告只含规则编号与位置，不含回复原文"
    assert "词表未核对" in to_markdown(rep) and "金标准未标注" in to_markdown(rep)


def test_review_checks_do_not_count_as_pass():
    case = BY_ID["HO-014"]
    rep = score([case], [{"case_id": "HO-014", "run": 1, "turns": reply("鸦老师上次说先画图。")}], LEX)
    verdicts = {c["id"]: c["verdict"] for c in rep["detail"][0]["checks"]}
    assert verdicts["cite_source"] == "review" and rep["detail"][0]["passed"]


def test_lexicon_entries_cite_sources_and_compile():
    doc = yaml.safe_load((BASE / "lexicon" / "forbidden.yaml").read_text(encoding="utf-8"))
    assert doc["reviewed"] is False, "核对前保持 false（spec 待人确认 #2）"
    classes = {e["class"] for e in doc["forbidden"]}
    assert classes == {"A", "B", "C", "D", "E"}
    assert all(e.get("source") for e in doc["forbidden"]) and all(p.get("source") for p in doc["patterns"].values())


def test_offline_pipeline(tmp_path):
    sys.path.insert(0, str(BASE))
    import run as pipeline  # noqa: PLC0415
    assert pipeline.main(["--out", str(tmp_path)]) == 0
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["runs"] == 120 and (tmp_path / "report.md").exists()
