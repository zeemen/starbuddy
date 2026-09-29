"""点点跨模型一致性对照运行器与首轮差异报告的测试（冲刺 2-I，specs/diandian-consistency-r1-sprint2/spec.md 验收标准）。

三家假候选（总是合规 / 转介类稳定失败 / 固定种子随机失败）各跑 3 次，断言通过率、按类通过率、差值、区间、不稳定比例、
红线单列与手算一致；报告确定、不含回复原文；仓库内 --out、未登记或境外供应商、非 test 环境缺人设 Prompt 都被拒绝；默认不跑留出集。
全部是 test 环境的进程内假供应商，不联网、不需要密钥。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parents[1]
BASE = HERE / "diandian"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BASE))

import compare  # noqa: E402
import fake_candidates as fakes  # noqa: E402
from scorer.cases import CORE, load_cases, read_holdout  # noqa: E402
from scorer.compare_report import SAMPLE_BANNER, paired_bootstrap  # noqa: E402
from scorer.judge import judge_run, load_lexicon  # noqa: E402

SAMPLE = BASE / "candidates" / "sample-fake.yaml"
COMMITTED = HERE / "reports" / "r1-sample-fake"
LEX = load_lexicon(BASE / "lexicon" / "forbidden.yaml")
HOLDOUT = set(read_holdout(BASE / "holdout.txt"))
DEV = [c for c in load_cases(BASE / "cases") if c["case_id"] not in HOLDOUT]
BY_ID = {c["case_id"]: c for c in DEV}
RUNS = 3
RANDOM_RATE, RANDOM_SEED = 0.2, "r1-sample"


def expected_fail(cand: str, case: dict, run: int) -> bool:
    """假候选的设计（与夹具独立写一遍）：手算期望值用"""
    if cand == "fake-referral-fail":
        return case["category"] == "referral"
    if cand == "fake-random":
        return random.Random(f"{RANDOM_SEED}:{case['case_id']}:{run}").random() < RANDOM_RATE
    return False


def rate(passed: int, runs: int) -> float:
    return round(passed / runs, 4)


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    out = tmp_path_factory.mktemp("cmp-out")
    rep_dir = tmp_path_factory.mktemp("cmp-rep")
    seen: list = []
    rep = compare.compare(SAMPLE, runs=RUNS, env="test", out_dir=out, report_dir=rep_dir, seen=seen)
    return {"rep": rep, "out": out, "dir": rep_dir, "seen": seen, "cands": {c["id"]: c for c in rep["candidates"]}}


def test_probes_hit_their_rules():
    rules = {r: LEX.patterns[r]["regex"] for r in LEX.patterns if "regex" in LEX.patterns[r]}
    for name, probe in fakes.VIOLATION_PROBES.items():
        if name.startswith("forbidden:"):
            cls = name.split(":")[1]
            assert any(rx.search(probe) for _, c, rx in LEX.forbidden if c == cls), name
        elif name == "star_sentence":
            p = LEX.patterns["star_sentence"]
            assert p["mention"].search(probe) and not p["allowed"].search(probe)
        elif name == "unearned_lit":
            assert LEX.patterns["lit_claim"]["regex"].search(probe)
        else:
            assert rules[name].search(probe), name
    for name, probe in fakes.MATCH_PROBES.items():
        assert rules[name].search(probe), name


def test_fake_replies_do_what_they_are_designed_to():
    """夹具自检：合规回复每条用例都通过；失败回复只让计划违反的那一个检查点失败"""
    for case in DEV:
        ok = [fakes.reply_for(case, t, False) for t in range(len(case["turns"]))]
        assert all(v.verdict != "fail" for v in judge_run(case, {"turns": ok}, LEX)), case["case_id"]
        bad = [fakes.reply_for(case, t, True) for t in range(len(case["turns"]))]
        failed = [v.check_id for v in judge_run(case, {"turns": bad}, LEX) if v.verdict == "fail"]
        assert failed == [fakes.planned_violation(case)["id"]], (case["case_id"], failed)


def test_default_skips_holdout_and_runs_every_dev_case(sample):
    seen = sample["seen"]
    assert seen and not {c for _, c, _, _ in seen} & HOLDOUT, "默认不跑留出集（规则 14）"
    assert sample["rep"]["cases"] == len(DEV) == 120 - len(HOLDOUT) and sample["rep"]["holdout_excluded"] == len(HOLDOUT)
    turns = sum(len(c["turns"]) for c in DEV)
    for cid, c in sample["cands"].items():
        assert c["calls"] == turns * RUNS and c["call_errors"] == 0 and c["model_mismatch"] == 0, cid
        assert len({(k, x, r, t) for k, x, r, t in seen if k == cid}) == turns * RUNS
        assert c["model_versions"] == [c["model_version"]], "每个候选只经自己的路由，不回落到别家"


def _expected_summary(cand: str) -> dict:
    fails = {(c["case_id"], r) for c in DEV for r in range(1, RUNS + 1) if expected_fail(cand, c, r)}

    def agg(cases):
        n = len(cases) * RUNS
        return rate(n - sum((c["case_id"], r) in fails for c in cases for r in range(1, RUNS + 1)), n)

    unstable = [c for c in DEV if 0 < sum((c["case_id"], r) in fails for r in range(1, RUNS + 1)) < RUNS]
    red = sorted((cid, r, fakes.planned_violation(BY_ID[cid])["id"]) for cid, r in fails
                 if fakes.planned_violation(BY_ID[cid]).get("redline"))
    return {"overall": agg(DEV), "core": agg([c for c in DEV if c["core"]]),
            "by_category": {cat: agg([c for c in DEV if c["category"] == cat]) for cat in sorted({c["category"] for c in DEV})},
            "unstable": rate(len(unstable), len(DEV)), "redline": red, "fails": fails}


@pytest.mark.parametrize("cand", ["fake-compliant", "fake-referral-fail", "fake-random"])
def test_candidate_rates_match_hand_calculation(sample, cand):
    got, want = sample["cands"][cand], _expected_summary(cand)
    assert got["overall"]["rate"] == want["overall"] and got["core"]["rate"] == want["core"]
    assert {k: v["rate"] for k, v in got["by_category"].items()} == want["by_category"]
    assert got["unstable"]["ratio"] == want["unstable"]
    assert sorted((v["case_id"], v["run"], v["check"]) for v in got["redline"]["items"]) == want["redline"], "红线单列"
    assert got["redline"]["count"] == len(want["redline"])


def test_three_designs_look_as_designed(sample):
    c = sample["cands"]
    assert c["fake-compliant"]["overall"]["rate"] == 1.0 and c["fake-compliant"]["redline"]["count"] == 0
    ref = [x for x in DEV if x["category"] == "referral"]
    neg = [x for x in ref if x["polarity"] == "negative"]
    assert c["fake-referral-fail"]["by_category"]["referral"]["rate"] == 0.0
    assert c["fake-referral-fail"]["overall"]["rate"] == rate(len(DEV) - len(ref), len(DEV))
    assert c["fake-referral-fail"]["redline"]["count"] == len(neg) * RUNS, "转介漏报是红线，逐次单列"
    assert c["fake-referral-fail"]["unstable"]["unstable"] == 0, "稳定失败不算不稳定"
    assert 0 < c["fake-random"]["unstable"]["ratio"] < 1


def test_per_run_detail_outside_repo_has_text_but_report_does_not(sample):
    raw = (sample["out"] / "fake-random" / "run-1" / "responses.jsonl").read_text(encoding="utf-8")
    assert fakes.REPLY_MARKER in raw, "回复原文只写到仓库外"
    text = "".join((sample["dir"] / n).read_text(encoding="utf-8") for n in ("report.json", "report.md"))
    for s in [fakes.REPLY_MARKER, *fakes.VIOLATION_PROBES.values(), *fakes.MATCH_PROBES.values(), fakes.PLACEHOLDER_SYSTEM]:
        assert s not in text, s
    assert '"hits"' not in text and '"start"' not in text, "报告不带命中位置"


def test_pair_differences_and_intervals(sample):
    pairs = {(p["a"], p["b"]): p for p in sample["rep"]["pairs"]}
    assert list(pairs) == [("fake-compliant", "fake-referral-fail"), ("fake-compliant", "fake-random"), ("fake-referral-fail", "fake-random")]
    s = {k: _expected_summary(k) for k in ("fake-compliant", "fake-referral-fail", "fake-random")}
    for (a, b), p in pairs.items():
        assert p["overall"]["diff"] == round(s[a]["overall"] - s[b]["overall"], 4), (a, b)
        assert p["core"]["diff"] == round(s[a]["core"] - s[b]["core"], 4), (a, b)
        for cat, d in p["by_category"].items():
            assert d == round(s[a]["by_category"][cat] - s[b]["by_category"][cat], 4), (a, b, cat)
        for scope in ("overall", "core"):
            x = p[scope]
            assert x["ci_low"] <= x["diff"] <= x["ci_high"], (a, b, scope)
    # 合规 − 转介失败：用例级差值只有 0 与 1 两种，区间应接近二项分布的正态近似
    p = pairs[("fake-compliant", "fake-referral-fail")]["overall"]
    k = len([x for x in DEV if x["category"] == "referral"])
    q, n = k / len(DEV), len(DEV)
    half = 1.96 * (q * (1 - q) / n) ** 0.5
    assert abs(p["ci_low"] - (q - half)) < 0.03 and abs(p["ci_high"] - (q + half)) < 0.03
    assert pairs[("fake-compliant", "fake-referral-fail")]["by_category"]["referral"] == 1.0


def test_paired_bootstrap_hand_examples():
    assert paired_bootstrap([1, 1, 0], [1, 1, 0]) == {"diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "cases": 3}
    # 两条用例、差值 (1, 0)：重抽均值取 0、0.5、1 的概率为 1/4、1/2、1/4 → 2.5% 分位 0，97.5% 分位 1
    r = paired_bootstrap([1, 0], [0, 0])
    assert (r["diff"], r["ci_low"], r["ci_high"]) == (0.5, 0.0, 1.0)
    # 四条用例、差值 (1, 0, 0, 0)：均值 ~ Binomial(4, 1/4)/4，累计 0.316、0.738、0.949、0.996 → 区间 [0, 0.75]
    r = paired_bootstrap([1, 0, 0, 0], [0, 0, 0, 0])
    assert (r["diff"], r["ci_low"], r["ci_high"]) == (0.25, 0.0, 0.75)
    # 配对：两边同时变化的用例不贡献差值
    r = paired_bootstrap([1, 0, 1, 0], [1, 0, 1, 0])
    assert r["ci_low"] == r["ci_high"] == 0.0
    r2 = paired_bootstrap([0, 1, 0, 1], [1, 0, 1, 0])
    assert r2["diff"] == 0.0 and r2["ci_low"] < 0 < r2["ci_high"]
    assert paired_bootstrap([], []) is None


def test_only_one_failed_lists_ids_and_checks(sample):
    ids = ["fake-compliant", "fake-referral-fail", "fake-random"]
    want = []
    for c in sorted(DEV, key=lambda x: x["case_id"]):
        failing = [k for k in ids if any(expected_fail(k, c, r) for r in range(1, RUNS + 1))]
        if len(failing) == 1:
            want.append((c["case_id"], failing[0], [fakes.planned_violation(c)["id"]]))
    got = [(x["case_id"], x["candidate"], x["checks"]) for x in sample["rep"]["only_one_failed"]]
    assert got == want
    assert all(set(x) == {"case_id", "candidate", "category", "checks", "failed_runs"} for x in sample["rep"]["only_one_failed"])


def test_criterion_3i_is_reported_not_concluded(sample):
    crit = sample["rep"]["criterion_3i"]
    core = {c: sample["cands"][c]["core"]["rate"] for c in sample["cands"]}
    assert crit["threshold_pp"] == 5.0 and crit["conclusion"] is None
    assert crit["max_abs_core_diff_pp"] == round(max(abs(p["core"]["diff"]) for p in sample["rep"]["pairs"]) * 100, 4)
    assert abs(crit["max_abs_core_diff_pp"] / 100 - (max(core.values()) - min(core.values()))) < 1e-6
    md = (sample["dir"] / "report.md").read_text(encoding="utf-8")
    assert "只报告、不下结论" in md and "不满足" not in md and "通过验收" not in md


def test_sample_header_says_not_a_model_evaluation(sample):
    md = (sample["dir"] / "report.md").read_text(encoding="utf-8")
    assert sample["rep"]["sample"] is True and sample["rep"]["banner"] == SAMPLE_BANNER
    assert SAMPLE_BANNER in md.splitlines()[2]


def test_report_is_deterministic_and_committed_sample_is_current(sample, tmp_path):
    again = tmp_path / "rep"
    compare.compare(SAMPLE, runs=RUNS, env="test", out_dir=tmp_path / "out", report_dir=again)
    for n in ("report.json", "report.md"):
        assert (again / n).read_bytes() == (sample["dir"] / n).read_bytes(), f"{n} 不确定"
        assert (COMMITTED / n).read_bytes() == (again / n).read_bytes(), f"reports/r1-sample-fake/{n} 需要重新生成"


def _cands(tmp_path: Path, items: list[dict], name: str = "cands") -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "candidates": items}, allow_unicode=True), encoding="utf-8")
    return p


def test_refuses_output_inside_repo(tmp_path, capsys):
    assert compare.main(["--candidates", str(SAMPLE), "--out", str(BASE / "gold")]) == 2
    assert not (BASE / "gold").exists()
    assert compare.main(["--candidates", str(SAMPLE), "--out", str(tmp_path / "o"), "--report-dir", str(BASE / "cases" / "x")]) == 2
    assert compare.main(["--candidates", str(SAMPLE), "--out", str(tmp_path / "o"), "--report-dir", str(HERE / "reports")]) == 2
    assert not (BASE / "cases" / "x").exists()
    assert "仓库内" in capsys.readouterr().out


@pytest.mark.parametrize("item,needle", [
    ({"id": "x", "vendor": "no-such-vendor", "model": "fake-chat"}, "未在模型路由配置"),
    ({"id": "x", "vendor": "fake-x", "model": "fake-batch"}, "境外"),
    ({"id": "x", "vendor": "fake-a", "model": "unregistered-model"}, "未在 test 环境的路由里登记"),
    ({"id": "x", "vendor": "fake-a", "model": "fake-chat", "api_key": "k"}, "未知键"),
    ({"id": "X!", "vendor": "fake-a", "model": "fake-chat"}, "id"),
    ({"id": "x", "vendor": "fake-a", "model": "fake-chat", "fake_profile": "fail_category:nope"}, "类别"),
])
def test_refuses_unregistered_or_foreign_candidates(tmp_path, capsys, item, needle):
    assert compare.main(["--candidates", str(_cands(tmp_path, [item])), "--out", str(tmp_path / "o")]) == 2
    assert needle in capsys.readouterr().out


def test_non_test_env_requires_persona_prompt_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(compare, "PROMPT_PATH", tmp_path / "missing" / "diandian-v0.md")
    real = _cands(tmp_path, [{"id": "a", "vendor": "fake-a", "model": "fake-chat"}])
    for env in ("dev", "staging", "pilot", "prod"):
        assert compare.main(["--candidates", str(real), "--env", env, "--out", str(tmp_path / "o")]) == 2
        assert "人设 Prompt" in capsys.readouterr().out
    assert not (tmp_path / "o").exists()


def test_non_test_env_with_prompt_still_needs_registered_vendor_and_env_credentials(tmp_path, capsys, monkeypatch):
    prompt = tmp_path / "diandian-v0.md"
    prompt.write_text("测试占位，不是人设 Prompt", encoding="utf-8")  # 测试夹具：只用来让“文件存在”成立
    monkeypatch.setattr(compare, "PROMPT_PATH", prompt)
    monkeypatch.delenv("MODEL_FAKE_A_URL", raising=False)
    real = _cands(tmp_path, [{"id": "a", "vendor": "fake-a", "model": "fake-chat"}])
    assert compare.main(["--candidates", str(real), "--env", "staging", "--out", str(tmp_path / "o")]) == 2
    assert "未在模型路由配置的 staging 环境登记" in capsys.readouterr().out
    faked = _cands(tmp_path, [{"id": "a", "vendor": "fake-a", "model": "fake-chat", "fake_profile": "compliant"}], "faked")
    assert compare.main(["--candidates", str(faked), "--env", "dev", "--out", str(tmp_path / "o")]) == 2
    assert "只在 test 环境有效" in capsys.readouterr().out
    # dev 环境：运行器不持有凭据，地址与 Key 只从执行环境读取；没有就拒绝
    assert compare.main(["--candidates", str(real), "--env", "dev", "--out", str(tmp_path / "o")]) == 2
    assert "执行环境" in capsys.readouterr().out


def test_candidate_list_is_only_registered_domestic_fakes():
    doc = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    assert {c["vendor"] for c in doc["candidates"]} <= {"fake-a", "fake-b"}
    assert all(c["fake_profile"] for c in doc["candidates"])


def test_categories_cover_core_marking(sample):
    assert set(sample["rep"]["categories"]) == {c["category"] for c in DEV}
    assert sample["rep"]["core_cases"] == sum(c["category"] in CORE for c in DEV)


def test_committed_sample_report_has_no_raw_text():
    text = "".join((COMMITTED / n).read_text(encoding="utf-8") for n in ("report.json", "report.md"))
    assert SAMPLE_BANNER in text and fakes.REPLY_MARKER not in text
    assert json.loads((COMMITTED / "report.json").read_text(encoding="utf-8"))["sample"] is True
