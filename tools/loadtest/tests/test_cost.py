"""成本模型：给定合成调用记录与价格表，P50 / P90 与手算一致；敏感性正确；报告标注数据来源与示例价；不出现模型标识。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loadtest.cost import (CONFIG, SOURCE_ASSUMED, SOURCE_REPLAY, build, load_quotas, load_records, load_yaml, render_markdown,
                           weighted_quantile)

SAMPLE = Path(__file__).resolve().parents[1] / "sample"
PRICES = {"example": True, "currency": "CNY",
          "per_million_tokens": {"T1": {"vendor": "代号甲", "model": "代号甲-1", "input": 2.0, "output": 8.0},
                                 "T2": {"vendor": "代号甲", "model": "代号甲-2", "input": 4.0, "output": 16.0},
                                 "T9": {"vendor": "代号乙", "model": "代号乙-1", "input": 0.8, "output": 2.0}},
          "voice_per_minute": {"vendor": "代号丙", "model": "代号丙-1", "price": 0.06}}
PROFILE = {"quota_config": "v12-pilot@1", "utilization": {"levels": [0.3, 0.6, 1.0], "weights": [1, 1, 1]},
           "fallback_tokens": {"T1": {"input": 1800, "output": 250}, "T2": {"input": 9999, "output": 9999},
                               "T9": {"input": 600, "output": 20}},
           "subjects": {
               "zhixing": {"label": "道法", "activities": [
                   {"resource": "mistake_analysis", "task": "T2", "calls_per_unit": 0.5},
                   {"resource": "diagnosis_full", "task": None, "calls_per_unit": 0},
                   {"resource": "ai_active_time", "task": "T1", "calls_per_unit": 1.0},
                   {"resource": "ai_active_time", "task": "T9", "calls_per_unit": 1.0},
                   {"resource": "voice_realtime", "task": "voice", "calls_per_unit": 1.0}]},
               "echo": {"label": "英语", "shelved": True, "activities": []}}}
QUOTAS = {"basic": {"mistake_analysis": 60, "ai_active_time": 600, "voice_realtime": 60, "diagnosis_full": 3},
          "plus": {"mistake_analysis": 150, "ai_active_time": 1200, "voice_realtime": 120, "diagnosis_full": 5},
          "pro": {"mistake_analysis": 400, "ai_active_time": 2400, "voice_realtime": 300, "diagnosis_full": 10},
          "max": {"mistake_analysis": 800, "ai_active_time": 3960, "voice_realtime": 600, "diagnosis_full": 20}}
RECORDS = [
    {"subject": "zhixing", "task": "T2", "outcome": "ok", "input_tokens": 1000, "output_tokens": 200},
    {"subject": "zhixing", "task": "T2", "outcome": "ok", "input_tokens": 2000, "output_tokens": 400},
    {"subject": "zhixing", "task": "T2", "outcome": "unavailable", "input_tokens": 0, "output_tokens": 0},  # 失败调用不进均值
    {"subject": "wenli", "task": "T2", "outcome": "ok", "input_tokens": 50000, "output_tokens": 50000},  # 别的科目不串
]


def row(r, subject, tier):
    return next(x for x in r["table"] if x["subject"] == subject and x.get("tier") == tier)


def test_monthly_p50_p90_match_hand_calculation():
    r = build(RECORDS, PRICES, PROFILE, QUOTAS)
    # 手算（基本档）：单次成本 T2 = (1500×4 + 300×16)/1e6 = 0.0108（记录均值）；T1 = (1800×2 + 250×8)/1e6 = 0.0056；
    # T9 = (600×0.8 + 20×2)/1e6 = 0.00052；每月 = 60u×0.5×0.0108 + 600u×0.0056 + 600u×0.00052 + 60u×0.06 = 7.596u
    b = row(r, "zhixing", "basic")
    assert b["by_level"] == pytest.approx({"0.3": 7.596 * 0.3, "0.6": 7.596 * 0.6, "1": 7.596})
    assert b["p50"] == pytest.approx(4.5576) and b["p90"] == pytest.approx(7.596), "三档等权：P50 = 60% 档，P90 = 100% 档"
    # Max 档：800u×0.5×0.0108 + 3960u×(0.0056+0.00052) + 600u×0.06 = 4.32u + 24.2352u + 36u = 64.5552u
    m = row(r, "zhixing", "max")
    assert m["p90"] == pytest.approx(64.5552) and m["p50"] == pytest.approx(64.5552 * 0.6)


def test_sensitivity_by_hand():
    r = build(RECORDS, PRICES, PROFILE, QUOTAS)
    s = {x["scenario"]: x for x in r["sensitivity"] if x["subject"] == "zhixing" and x["tier"] == "basic"}
    assert s["price_x0.5"]["p90"] == pytest.approx(7.596 * 0.5) and s["price_x1.5"]["p50"] == pytest.approx(4.5576 * 1.5)
    # token ×1.5 只放大 token 部分（3.996u），语音 3.6u 不变
    assert s["tokens_x1.5"]["p90"] == pytest.approx(3.996 * 1.5 + 3.6)
    assert s["tokens_x1.5"]["p50"] == pytest.approx((3.996 * 1.5 + 3.6) * 0.6)


def test_token_sources_are_labelled():
    r = build(RECORDS, PRICES, PROFILE, QUOTAS)
    src = {(s["subject"], s["task"]): s for s in r["token_sources"]}
    assert src[("zhixing", "T2")]["source"] == SOURCE_REPLAY and src[("zhixing", "T2")]["calls_observed"] == 2
    assert src[("zhixing", "T2")]["input_tokens"] == 1500
    assert src[("zhixing", "T1")]["source"] == SOURCE_ASSUMED and src[("zhixing", "T1")]["calls_observed"] == 0
    # 没有调用记录时 T2 回退到可配置分布
    r2 = build([], PRICES, PROFILE, QUOTAS)
    assert {s["source"] for s in r2["token_sources"]} == {SOURCE_ASSUMED}
    assert row(r2, "zhixing", "basic")["p90"] > row(r, "zhixing", "basic")["p90"]


def test_english_row_is_shelved_and_report_is_labelled():
    r = build(RECORDS, PRICES, PROFILE, QUOTAS)
    echo = [x for x in r["table"] if x["subject"] == "echo"]
    assert echo == [{"subject": "echo", "label": "英语", "status": "搁置，无数据"}]
    md = render_markdown(r)
    assert "合成估算" in md and "示例价" in md and "搁置，无数据" in md
    assert "可配置分布（假设）" in md and "合成回放" in md


def test_weighted_quantile():
    assert weighted_quantile([3.0, 1.0, 2.0], [1, 1, 1], 0.5) == 2.0
    assert weighted_quantile([1.0, 2.0, 3.0], [1, 1, 1], 0.9) == 3.0
    assert weighted_quantile([1.0, 2.0, 3.0], [8, 1, 1], 0.5) == 1.0 and weighted_quantile([1.0, 2.0, 3.0], [8, 1, 1], 0.9) == 2.0
    assert weighted_quantile([1.0, 2.0], [1, 1], 0.5) == 1.0


def test_quotas_are_read_from_migration_0015():
    q = load_quotas("v12-pilot@1")
    assert set(q) == {"basic", "plus", "pro", "max"}
    assert all(len(v) == 10 for v in q.values())
    assert q["basic"]["ai_active_time"] == 600 and q["max"]["ai_active_time"] == 3960 and q["pro"]["mistake_analysis"] == 400
    assert q["basic"]["voice_realtime"] == 60 and q["max"]["diagnosis_full"] == 20
    with pytest.raises(ValueError):
        load_quotas("no-such-config@9")


def test_repo_configs_cover_every_task_and_are_example_prices():
    prices = load_yaml(CONFIG / "prices.example.yaml")
    profile = load_yaml(CONFIG / "cost_profile.yaml")
    assert prices["example"] is True
    tasks = {a["task"] for s in profile["subjects"].values() for a in s["activities"] if a["task"] and a["task"] != "voice"}
    assert tasks <= set(prices["per_million_tokens"]) and tasks <= set(profile["fallback_tokens"])
    assert profile["subjects"]["echo"]["shelved"] is True
    assert set(profile["subjects"]) == {"aya", "wenli", "zhixing", "echo"}


MODEL_ID = re.compile(r"(model:[a-z0-9-]+/|fake-[abx]\b|fake-(chat|reasoner|guard|batch))")


@pytest.mark.parametrize("name", ["cost-report.md", "cost-report.json", "baseline.md", "baseline.json", "call-records.jsonl"])
def test_committed_samples_have_no_model_identifiers_and_are_labelled(name):
    text = (SAMPLE / name).read_text(encoding="utf-8")
    assert not MODEL_ID.search(text), "产物不写模型标识（报告中的供应商与模型一律用合成代号）"
    if name.startswith("cost-report"):
        assert "合成估算" in text and "示例价" in text or '"example_prices": true' in text
    if name.startswith("baseline"):
        assert "本机基线，不代表生产容量" in text and "小规模" in text


def test_sample_cost_report_regenerates_from_sample_records():
    """样例成本报告由样例调用记录 + 仓库价格表与画像生成（确定性）"""
    r = build(load_records(SAMPLE / "call-records.jsonl"), load_yaml(CONFIG / "prices.example.yaml"),
              load_yaml(CONFIG / "cost_profile.yaml"), load_quotas("v12-pilot@1"))
    committed = json.loads((SAMPLE / "cost-report.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(r, ensure_ascii=False)) == committed, "样例过期：cd tools/loadtest && python -m loadtest cost --records sample/call-records.jsonl --out sample 重生"
    assert render_markdown(r) == (SAMPLE / "cost-report.md").read_text(encoding="utf-8")
