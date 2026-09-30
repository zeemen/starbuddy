"""报告：样例是最新的、确定性、只含汇总与编号（不含原文、音频引用、供应商名、本机路径）、盲测编号、拒绝写 gold/、
score 与 sample 一致、假供应商行为与指标方向一致。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_blind
from voiceblind import blind, fakes
from voiceblind.manifest import load
from voiceblind.text import zh_units
from voiceblind.report import MD_NAME, JSON_NAME, SAMPLE_NOTICE, ReportPathError, evaluate, write_report
from voiceblind.vendor import (AsrOutput, AsrRequest, AsrVendor, PronunciationVendor, VendorOutputError, load_results, run_asr,
                               run_pron)

HERE = Path(__file__).resolve().parents[1]
FIX = HERE / "fixtures" / "manifest-synthetic.yaml"
SAMPLE = HERE / "reports" / "sample-synthetic"
CODES = blind.assign_codes(list(fakes.FAKE_IDS), run_blind.SAMPLE_BLIND_SEED)


def sample(out: Path, *extra: str) -> int:
    return run_blind.main(["sample", "--manifest", str(FIX), "--out", str(out), *extra])


def rows(rep: dict, section: str, code: str) -> dict[str, dict]:
    return {r["group"]: r for r in rep[section][code]}


@pytest.fixture(scope="module")
def rep(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("rep")
    assert sample(d) == 0
    return json.loads((d / JSON_NAME).read_text(encoding="utf-8"))


# ---------- 样例与确定性 ----------
def test_sample_report_is_current_and_deterministic(tmp_path):
    assert sample(tmp_path / "a") == 0 and sample(tmp_path / "b") == 0
    for name in (MD_NAME, JSON_NAME):
        a = (tmp_path / "a" / name).read_bytes()
        assert a == (tmp_path / "b" / name).read_bytes()
        assert a == (SAMPLE / name).read_bytes(), f"样例报告过期：按 README 重跑 sample 更新 {name}"
    md = (SAMPLE / MD_NAME).read_text(encoding="utf-8")
    assert md.splitlines()[2] == f"> {SAMPLE_NOTICE}" and "流程样例，不是评测结论" in md
    assert json.loads((SAMPLE / JSON_NAME).read_text(encoding="utf-8"))["notices"][0] == SAMPLE_NOTICE


def test_real_manifest_has_no_sample_notice():
    m = load(FIX)
    real = type(m)(False, m.samples, m.digest)
    r = evaluate(real, {}, {}, resamples=100)
    assert SAMPLE_NOTICE not in r["notices"] and not r["synthetic"]


def test_report_contains_only_numbers_ids_and_labels(tmp_path):
    res = tmp_path / "results"
    assert sample(tmp_path / "out", "--results", str(res)) == 0
    blob = (tmp_path / "out" / MD_NAME).read_text(encoding="utf-8") + (tmp_path / "out" / JSON_NAME).read_text(encoding="utf-8")
    m = load(FIX)
    asr, _ = load_results(res)
    for s in m.samples:
        assert s.id in blob
        if s.reference:
            assert s.reference not in blob, "参考文本不进报告"
        assert s.audio.ref not in blob and "synthetic://" not in blob, "音频引用不进报告"
    hyps = {o.text for per in asr.values() for o in per.values() if len(o.text) >= 4}
    assert hyps and not any(h in blob for h in hyps), "转写原文不进报告"
    for vid in fakes.FAKE_IDS:
        assert vid not in blob, "供应商名不进报告（只用盲测编号）"
    assert "fake" not in blob.lower()
    assert str(tmp_path) not in blob and str(HERE) not in blob
    js = json.loads((tmp_path / "out" / JSON_NAME).read_text(encoding="utf-8"))
    assert set(js) == {"format", "framework", "synthetic", "notices", "inputs", "config", "blind", "asr", "latency", "pron",
                       "samples", "small_groups"}
    assert set(js["samples"][0]) == {"id", "lang", "accent", "noise", "task", "expert"}
    assert js["blind"]["codes"] == ["VA", "VB", "VC"] and js["blind"]["key_sha256"] == blind.key_digest(CODES)


def test_score_from_results_matches_sample(tmp_path):
    res = tmp_path / "results"
    assert sample(tmp_path / "a", "--results", str(res)) == 0
    assert sorted(p.name for p in res.iterdir()) == ["VA.asr.jsonl", "VA.pron.jsonl", "VB.asr.jsonl", "VB.pron.jsonl",
                                                     "VC.asr.jsonl", "VC.pron.jsonl"]
    assert run_blind.main(["score", "--manifest", str(FIX), "--results", str(res), "--out", str(tmp_path / "b"),
                           "--key-sha256", blind.key_digest(CODES)]) == 0
    for name in (MD_NAME, JSON_NAME):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_normalization_options_change_config_and_numbers(tmp_path, rep):
    assert sample(tmp_path, "--zh-keep-punct", "--en-case-sensitive") == 0
    r2 = json.loads((tmp_path / JSON_NAME).read_text(encoding="utf-8"))
    assert r2["config"]["zh_norm"]["strip_punct"] is False and r2["config"]["en_norm"]["lowercase"] is False
    acc = CODES["fake-accurate"]
    assert rows(rep, "asr", acc)["zh"]["rate"]["value"] == 0.0 and rows(rep, "asr", acc)["en"]["rate"]["value"] == 0.0
    # 准确的一家只改了标点、全半角与大小写：放开规范化后错误率上升
    assert rows(r2, "asr", acc)["zh"]["rate"]["value"] > 0 and rows(r2, "asr", acc)["en"]["rate"]["value"] > 0


# ---------- 假供应商行为 → 指标方向 ----------
def test_fake_vendor_profiles_show_up_in_metrics(rep):
    acc, bias, rnd = (CODES[v] for v in fakes.FAKE_IDS)
    a, b, r = rows(rep, "asr", acc), rows(rep, "asr", bias), rows(rep, "asr", rnd)
    for g in a:
        assert a[g]["rate"]["value"] == 0.0 and a[g]["missing"] == 0
    for lang in ("zh", "en"):
        assert b[f"{lang}/accent=standard"]["rate"]["value"] == 0.0
        assert b[f"{lang}/accent=nanjing"]["rate"]["value"] > 0.25
        assert r[f"{lang}/accent=standard"]["rate"]["value"] > 0
    # 空参考（静音）样本上的插入计入中文合计
    assert b["zh"]["ins"] == 0 and r["zh"]["ins"] > 0
    pa, pb, pr = rows(rep, "pron", acc), rows(rep, "pron", bias), rows(rep, "pron", rnd)
    assert pa["zh"]["phone_accuracy"]["value"] == 1.0 and pa["en"]["error_recall"]["value"] == 1.0
    assert pa["en"]["sentence_pearson"]["value"] > 0.95
    assert pb["en/accent=standard"]["phone_accuracy"]["value"] == 1.0
    assert pb["en/accent=nanjing"]["phone_accuracy"]["value"] < 1.0
    assert pb["en/accent=nanjing"]["error_precision"]["value"] < 1.0
    assert pr["en"]["phone_accuracy"]["value"] < 1.0
    for code in (acc, bias, rnd):
        for row in rep["latency"][code]:
            for k in ("rtf_p50", "rtf_p95", "first_token_s_p50", "first_token_s_p95"):
                ci = row[k]
                assert ci["ci_low"] <= ci["value"] <= ci["ci_high"]
            assert row["rtf_p50"]["value"] <= row["rtf_p95"]["value"]


def test_every_interval_brackets_its_point(rep):
    def walk(x):
        if isinstance(x, dict):
            if {"value", "ci_low", "ci_high"} <= set(x) and x["ci_low"] is not None:
                assert x["ci_low"] <= x["value"] + 1e-9 and x["value"] <= x["ci_high"] + 1e-9
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(rep)


def test_missing_results_count_as_deletions():
    m = load(FIX)
    zh = [s for s in m.samples if s.lang == "zh"]
    only = {zh[0].id: AsrOutput(zh[0].id, zh[0].reference, 0.1, 0.2)}
    r = evaluate(m, {"VA": only}, {}, resamples=100)
    row = rows(r, "asr", "VA")["zh"]
    units = row["units"]
    assert row["missing"] == len(zh) - 1 and row["del"] == units - len(zh_units(zh[0].reference))
    with pytest.raises(ValueError):
        evaluate(m, {"VA": {"NOT-IN-MANIFEST": AsrOutput("x", "", 0, 0)}}, {}, resamples=100)


# ---------- 盲测 ----------
class Recorder(AsrVendor):
    vendor_id = "recorder"

    def __init__(self) -> None:
        self.reqs: list[AsrRequest] = []

    def transcribe(self, req: AsrRequest) -> AsrOutput:
        self.reqs.append(req)
        return AsrOutput(req.item_id, "", 0.1, 0.2)


def test_vendor_requests_are_blind():
    m = load(FIX)
    v = Recorder()
    out = run_asr(v, m, seed="s", code="VA")
    assert set(out) == {s.id for s in m.samples}
    sids = {s.id for s in m.samples}
    for req in v.reqs:
        assert set(vars(req)) == {"item_id", "audio", "language"}, "ASR 请求不含参考文本与标签"
        assert req.item_id.startswith("I-") and not any(sid in req.item_id for sid in sids)
    order_a = [r.item_id for r in v.reqs]
    v2 = Recorder()
    run_asr(v2, m, seed="s", code="VB")
    assert sorted(order_a) == sorted(r.item_id for r in v2.reqs), "每家同一批音频"
    assert order_a != [r.item_id for r in v2.reqs], "每家提交顺序各自随机"


def test_codes_are_seeded_and_order_independent():
    ids = ["x-one", "x-two", "x-three"]
    c = blind.assign_codes(ids, "k")
    assert c == blind.assign_codes(list(reversed(ids)), "k")
    assert sorted(c.values()) == ["VA", "VB", "VC"]
    assert any(blind.assign_codes(ids, f"k{i}") != c for i in range(5))
    with pytest.raises(ValueError):
        blind.assign_codes(["a", "a"], "k")


def test_interfaces_are_abstract_and_outputs_checked():
    with pytest.raises(TypeError):
        AsrVendor()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        PronunciationVendor()  # type: ignore[abstract]

    class Bad(Recorder):
        def transcribe(self, req):
            return AsrOutput("wrong-id", "", 0.1, 0.2)

    class Slow(Recorder):
        def transcribe(self, req):
            return AsrOutput(req.item_id, "", 0.5, 0.2)

    m = load(FIX)
    for cls in (Bad, Slow):
        with pytest.raises(VendorOutputError):
            run_asr(cls(), m, seed="s", code="VA")
    asr_v, pron_v = fakes.build(m, "s")
    assert set(run_pron(pron_v[0], m, seed="s", code="VA")) == {s.id for s in m.samples if s.expert is not None}


# ---------- 输出位置 ----------
@pytest.mark.parametrize("sub", ["gold", "gold/x", "a/GOLD/b"])
def test_refuses_to_write_into_gold(tmp_path, sub):
    target = tmp_path / sub
    with pytest.raises(ReportPathError):
        write_report({}, target)
    assert sample(target) == 2
    assert not target.exists()
    assert sample(tmp_path / "ok", "--results", str(tmp_path / "gold" / "r")) == 2
    assert not (tmp_path / "gold" / "r").exists()


def test_real_results_inside_repo_refused(tmp_path, monkeypatch):
    real = tmp_path / "m.yaml"
    txt = FIX.read_text(encoding="utf-8").replace("synthetic: true", "synthetic: false").replace("synthetic://", "store://")
    real.write_text(txt, encoding="utf-8")
    res = tmp_path / "results"
    assert sample(tmp_path / "s", "--results", str(res)) == 0  # 结果文件本身合规：只因位置被拒
    assert run_blind.main(["score", "--manifest", str(real), "--results", str(res), "--out", str(tmp_path / "ok")]) == 0
    monkeypatch.setattr(run_blind, "ROOT", tmp_path)
    assert run_blind.main(["score", "--manifest", str(real), "--results", str(res), "--out", str(tmp_path / "o")]) == 2
    assert run_blind.main(["sample", "--manifest", str(real), "--out", str(tmp_path / "o")]) == 2
    assert not (tmp_path / "o").exists()


def test_bad_results_rejected(tmp_path):
    (tmp_path / "vendorx.asr.jsonl").write_text("{}\n", encoding="utf-8")
    assert run_blind.main(["score", "--manifest", str(FIX), "--results", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
    (tmp_path / "vendorx.asr.jsonl").unlink()
    (tmp_path / "VA.asr.jsonl").write_text('{"sample_id": "ZH-001", "text": "x", "first_token_s": 2, "total_s": 1}\n',
                                          encoding="utf-8")
    assert run_blind.main(["score", "--manifest", str(FIX), "--results", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
