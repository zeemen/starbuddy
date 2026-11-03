"""PLT-17 成本仪表盘第一步（specs/cost-dashboard-plt17）：插值分位数与粗化、分摊两口径与手算一致；人数扰动与门槛余量、样本不足、
P90 尾部人数；每人一个点；未归因份额；示例价与合成标注由数据决定；输入守卫（同 kge-calibrate）；出口不含伪名、0600 / O_EXCL 写出；
差分（逐个删一人重跑）还原不了个人；合成队列固定种子；逐字节一致（含与仓库样例一致）。全程合成数据，不连库。"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from loadtest import dashboard as D
from loadtest import dashboard_synth as Y
from loadtest.cost import CONFIG, load_quotas, load_yaml

TOOL = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("LOADTEST_REPO") or TOOL.parents[1])
SAMPLE = TOOL / "sample"
# 仓库样例 cost-dashboard.json 的 sha256（样例过期时按 Makefile 注释重生 cost-dashboard.md，并更新这里）
SAMPLE_JSON_SHA256 = "8266e34b74ade54284171004ad73c62596f28c8a3841cd80e8fe7d8179291dcc"

PRICES = {"example": True, "currency": "CNY",
          "per_million_tokens": {"T1": {"vendor": "代号甲", "model": "代号甲-1", "input": 2.0, "output": 8.0},
                                 "T2": {"vendor": "代号甲", "model": "代号甲-2", "input": 4.0, "output": 16.0},
                                 "T9": {"vendor": "代号乙", "model": "代号乙-1", "input": 0.8, "output": 2.0}},
          "voice_per_minute": {"vendor": "代号丙", "model": "代号丙-1", "price": 0.06}}
PROFILE = {"quota_config": "v12-pilot@1",
           "fallback_tokens": {"T1": {"input": 9999, "output": 9999}, "T2": {"input": 9999, "output": 9999},
                               "T9": {"input": 600, "output": 20}},
           "subjects": {
               "aya": {"label": "数学", "activities": [
                   {"resource": "mistake_analysis", "task": None, "calls_per_unit": 0, "note": "验证器判分，不调模型"}]},
               "zhixing": {"label": "道法", "activities": [
                   {"resource": "mistake_analysis", "task": "T2", "calls_per_unit": 0.5},
                   {"resource": "diagnosis_full", "task": None, "calls_per_unit": 0}]},
               "echo": {"label": "英语", "shelved": True, "activities": []}}}
_Q = {"mistake_analysis": 70, "ai_active_time": 600, "voice_realtime": 60, "diagnosis_full": 3}
QUOTAS = {t: dict(_Q) for t in ("basic", "plus", "pro", "max")}
DCFG = {"min_group": 20, "round_to": 10, "unattributed": {"label": "未归因", "activities": [
    {"resource": "ai_active_time", "task": "T1", "calls_per_unit": 1.0},
    {"resource": "ai_active_time", "task": "T9", "calls_per_unit": 1.0},
    {"resource": "voice_realtime", "task": "voice", "calls_per_unit": 1.0}]}}
_R = [
    {"subject": "zhixing", "task": "T2", "outcome": "ok", "input_tokens": 1000, "output_tokens": 200, "attempts": 1, "fallback": False},
    {"subject": "zhixing", "task": "T2", "outcome": "ok", "input_tokens": 2000, "output_tokens": 400, "attempts": 2, "fallback": True},
    {"subject": "zhixing", "task": "T2", "outcome": "unavailable", "input_tokens": 0, "output_tokens": 0, "attempts": 3, "fallback": False},
    {"subject": "aya", "task": "T1", "outcome": "ok", "input_tokens": 1000, "output_tokens": 100},
    {"subject": "zhixing", "task": "T1", "outcome": "ok", "input_tokens": 2000, "output_tokens": 300},
]
RECORDS = _R * 10    # 每个任务 ≥ 20 次调用；均值与占比同 _R
SUBJECTS = tuple(PROFILE["subjects"])


def pid(i: int) -> str:
    return "syn-testcase" + "".join(chr(97 + (i // 26 ** k) % 26) for k in (2, 1, 0))


def usage_text(rows, synthetic="true", qc="v12-pilot@1", header="pseudonym,month,tier,subject,resource,quantity") -> str:
    return (f"# format=cost-dashboard-usage/1 synthetic={synthetic} quota_config={qc}\n{header}\n"
            + "".join(",".join(map(str, r)) + "\n" for r in rows))


def cohort_rows():
    """基本档 50 人：道法批改 i 次、讲解 7i 分钟、语音 i 分钟；前 30 人另有数学批改 2 次。Plus 档 5 人（样本不足）。"""
    rows = []
    for i in range(1, 51):
        rows += [(pid(i), "2026-09", "basic", "zhixing", "mistake_analysis", i),
                 (pid(i), "2026-09", "basic", "unattributed", "ai_active_time", 7 * i),
                 (pid(i), "2026-09", "basic", "unattributed", "voice_realtime", i)]
        if i <= 30:
            rows.append((pid(i), "2026-09", "basic", "aya", "mistake_analysis", 2))
    for i in range(60, 65):
        rows += [(pid(i), "2026-09", "plus", "zhixing", "mistake_analysis", 5),
                 (pid(i), "2026-09", "plus", "unattributed", "ai_active_time", 100)]
    return rows


def usage_of(rows=None) -> D.Usage:
    return D.parse_usage(usage_text(cohort_rows() if rows is None else rows), QUOTAS, "v12-pilot@1", SUBJECTS)


def build(rows=None, records=RECORDS, prices=PRICES, dcfg=DCFG):
    return D.build_dashboard(usage_of(rows), records, prices, PROFILE, QUOTAS, dcfg)


def cons(r, tier, subject, resource):
    return next(c for c in r["consumption"] if (c["tier"], c["subject"], c["resource"]) == (tier, subject, resource))


def n_shown(members) -> int | None:
    n = len(set(members)) + D.noise(D.set_key(members))
    return None if n < 22 else n // 10 * 10


BASIC = [pid(i) for i in range(1, 51)]


# ---------------- 插值、粗化与手算 ----------------
def test_interp_quantile_and_coarsen():
    assert D.interp_quantile([float(i) for i in range(1, 51)], 0.5) == 25.5
    assert D.interp_quantile([float(i) for i in range(1, 51)], 0.9) == pytest.approx(45.1)
    assert D.interp_quantile([3.0, 1.0, 2.0], 0.5) == 2.0 and D.interp_quantile([1.0, 2.0], 0.5) == 1.5
    assert D.coarsen(2.25, 0.1) == 2.3 and D.coarsen(2.249, 0.1) == 2.2 and D.coarsen(8.5, 1.0) == 9.0, "半数进位"
    assert D.coarsen(0.3, 0.05) == 0.3 and D.coarsen(27.5, 3.5) == 28.0
    assert D.quota_step(70) == 3.5 and D.quota_step(3) == 1.0 and D.quota_step(0) == 1.0


def test_tier_cost_p50_p90_and_unattributed_share_by_hand():
    r = build()
    # 单次成本：道法 T2 取分科均值 (1500×4 + 300×16)/1e6 = 0.0108；未归因 T1 取不分科均值 (1500×2 + 200×8)/1e6 = 0.0046；
    # T9 无记录回退 (600×0.8 + 20×2)/1e6 = 0.00052；第 i 人：道法 i×0.5×0.0108 = 0.0054i，未归因 7i×0.00512 + i×0.06 = 0.09584i
    basic = next(t for t in r["tier_cost"] if t["tier"] == "basic")
    assert basic["status"] == "ok" and basic["n_students"] == n_shown(BASIC)
    assert basic["p50"] == pytest.approx(D.coarsen(0.10124 * 25.5, 0.1)) == 2.6
    assert basic["p90"] == pytest.approx(D.coarsen(0.10124 * 45.1, 0.1)) == 4.6
    assert basic["unattributed_share"] == 0.95          # 0.09584 / 0.10124 = 0.9467 → 5% 一档
    plus = next(t for t in r["tier_cost"] if t["tier"] == "plus")
    assert plus == {"tier": "plus", "status": D.INSUFFICIENT}, "不足门槛只写样本不足，不给任何数"


def test_consumption_and_utilization_by_hand():
    r = build()
    z = cons(r, "basic", "zhixing", "mistake_analysis")      # 1…50：P50 25.5、P90 45.1；一档 = 70 × 5% = 3.5
    assert z["consumption"] == {"status": "ok", "n_students": n_shown(BASIC), "p50": 24.5, "p90": 45.5}
    assert z["utilization"]["p50"] == 0.35 and z["utilization"]["p90"] == 0.65
    a = cons(r, "basic", D.ALL, "mistake_analysis")           # 前 30 人 i + 2：P50 27.5 → 28、P90 45.1 → 45.5
    assert (a["consumption"]["p50"], a["consumption"]["p90"]) == (28.0, 45.5)
    aya = cons(r, "basic", "aya", "mistake_analysis")
    assert aya["consumption"]["p50"] == 3.5 and aya["consumption"]["p90"] == D.INSUFFICIENT, "全是 2：P90 以上 0 人"
    un = cons(r, "basic", D.UNATTR, "ai_active_time")         # 7i：P50 178.5 → 180、P90 315.7 → 330（一档 30）
    assert (un["consumption"]["p50"], un["consumption"]["p90"]) == (180.0, 330.0)
    assert un["utilization"]["p50"] == 0.3
    assert cons(r, "plus", "zhixing", "mistake_analysis")["consumption"] == {"status": D.INSUFFICIENT}
    assert cons(r, "plus", "zhixing", "mistake_analysis")["utilization"] == {"status": D.INSUFFICIENT}


def test_p90_needs_five_above():
    pts = [(pid(i), float(i)) for i in range(1, 51)]      # h = 44.1：P90 = 45.1，以上 46…50 有 5 人
    pts_small = [(pid(i), float(i)) for i in range(1, 46)]  # h = 39.6：P90 = 40.6，以上 41…45 有 5 人
    pts_tiny = [(pid(i), float(i)) for i in range(1, 41)]   # h = 35.1：P90 = 36.1，以上只有 4 人
    for ps, want in ((pts, 45.0), (pts_small, 41.0), (pts_tiny, D.INSUFFICIENT)):
        g = D.group(ps, 1.0, DCFG)
        assert g["status"] == "ok" and g["p90"] == want


def test_allocation_two_methods_by_hand():
    r = build()
    sh = r["allocation"]["shares"]
    # 成功调用：数学 1 次（1100 token），道法 3 次（1200 + 2400 + 2300 = 5900 token）；占比粗化到 1%
    assert sh["calls"] == {"aya": 0.25, "zhixing": 0.75, "echo": 0.0}
    assert sh["tokens"] == {"aya": 0.16, "zhixing": 0.84, "echo": 0.0}
    row = {(x["tier"], x["subject"]): x for x in r["allocation"]["rows"]}
    aya = row[("basic", "aya")]
    assert aya["by_calls"]["p50"] == D.coarsen(0.09584 * 0.25 * 15.5, 0.1) == 0.4
    assert aya["by_calls"]["p90"] == D.INSUFFICIENT, "30 人：P90 以上只有 3 人"
    assert aya["by_tokens"]["p50"] == D.coarsen(0.09584 * 1100 / 7000 * 15.5, 0.1) == 0.2
    z = row[("basic", "zhixing")]
    # 前 30 人在读两科：道法 0.0054i + 0.09584i × 份额；后 20 人只读道法，未归因全归道法
    assert z["by_calls"]["p50"] == D.coarsen((0.0054 + 0.09584 * 0.75) * 25.5, 0.1) == 2.0
    assert z["by_tokens"]["p50"] == D.coarsen((0.0054 + 0.09584 * 5900 / 7000) * 25.5, 0.1) == 2.2
    assert z["by_calls"]["p90"] == D.coarsen(0.10124 * 45.1, 0.1) == 4.6
    assert row[("plus", "zhixing")]["by_calls"] == {"status": D.INSUFFICIENT}
    assert r["allocation"]["unallocated_share"] == {"calls": 0.0, "tokens": 0.0}
    assert r["allocation"]["note"] == "D5 待产品确认"


def test_allocation_conserves_cost_and_reports_unallocated():
    """分摊后各科之和 = 该生总成本；在读科目在调用记录里占比为 0 时未归因成本记为“未能分摊”"""
    recs = [x for x in RECORDS if x["subject"] != "aya"]
    rows = [(pid(i), "2026-09", "basic", "aya", "mistake_analysis", 1) for i in range(40)]
    rows += [(pid(i), "2026-09", "basic", "unattributed", "ai_active_time", 10) for i in range(40)]
    r = build(rows, records=recs)
    assert r["allocation"]["unallocated_share"] == {"calls": 1.0, "tokens": 1.0}
    aya = next(x for x in r["allocation"]["rows"] if (x["tier"], x["subject"]) == ("basic", "aya"))
    assert aya["by_calls"]["p50"] == 0.0, "数学在调用记录里占比为 0：不凭空分摊"
    P = D.prepare(usage_of(), RECORDS, PRICES, PROFILE, QUOTAS, DCFG)
    for c in P.per_student.values():
        for m in ("calls", "tokens"):
            assert sum(c["alloc"][m].values()) == pytest.approx(c["total"])


def test_tasks_multi_attempt_fallback_and_cost_by_hand():
    r = build()
    t = {x["task"]: x for x in r["tasks"]}
    assert t["T2"]["calls"] == 30 and t["T2"]["ok"] == 20 and t["T2"]["failure_share"] == 0.33, "占比到 1%"
    assert t["T2"]["multi_attempt_share"] == 0.67 and t["T2"]["fallback_share"] == 0.33
    assert t["T2"]["cost_per_ok_call"] == pytest.approx(0.0108) and t["T2"]["cost_per_call"] == pytest.approx(0.0072)
    assert t["T1"]["multi_attempt_share"] is None and t["T1"]["fallback_share"] is None, "记录里没有字段时不猜"
    assert t["T1"]["cost_per_ok_call"] == pytest.approx(0.0046)
    o = r["tasks_overall"]
    assert (o["calls"], o["ok"], o["multi_attempt_share"]) == (50, 40, 0.67)
    md = D.render_dashboard_md(r)
    assert "记录不可得" in md and "多次尝试占比" in md and "与失败占比重叠" in md and "重试占比" not in md


def test_call_aggregates_need_twenty_calls_and_round_counts():
    few = _R * 3   # T2 9 次、T1 6 次
    r = build(records=few)
    assert all(t["status"] == D.INSUFFICIENT and "cost_per_call" not in t for t in r["tasks"])
    r2 = build(records=_R * 5 + _R[:2])    # T2 17 次，T1 10 次，合计 27 次
    assert r2["tasks_overall"]["calls"] == 20 and r2["tasks_overall"]["status"] == "ok"
    recs = RECORDS + [{"subject": None, "task": "T3", "outcome": "ok", "input_tokens": 100, "output_tokens": 10}] * 23
    t3 = next(x for x in build(records=recs)["tasks"] if x["task"] == "T3")
    assert t3["priced"] is False and t3["calls"] == 20 and "cost_per_call" not in t3
    assert "无价格" in D.render_dashboard_md(build(records=recs))


def test_unpriced_resources_are_listed_not_guessed():
    assert {"subject": "aya", "resource": "mistake_analysis", "reason": "验证器判分，不调模型"} in build()["unpriced"]


# ---------------- 扰动、门槛、每人一个点 ----------------
def test_noisy_threshold_keeps_margin():
    """扰动后 ≥ 22 才公开（真实 ≥ 20）；真实 20、21 人可能公开，真实 22、23 人也可能不公开；真实 < 20 永不公开"""
    hits = set()
    for off in range(0, 6000, 53):
        for n in (18, 19, 20, 21, 22, 23):
            members = [pid(off + i) for i in range(n)]
            nz = D.noise(D.set_key(members))
            got = D.noisy_count(members, DCFG)
            assert got == (None if n + nz < 22 else (n + nz) // 10 * 10)
            if n < 20:
                assert got is None
            hits.add((n, got is None))
    assert {(20, False), (21, False), (22, True), (23, True)} <= hits


def test_noise_is_shared_by_member_set_and_tracks_members():
    a = [pid(i) for i in range(30)]
    assert D.set_key(a) == D.set_key(list(reversed(a)) + a[:3]), "同一成员集合同一表键（各组共用一份扰动）"
    assert D.set_key(a[1:]) != D.set_key(a)
    vals = [D.noise(D.set_key([pid(i), pid(i + 1)])) for i in range(2000)]
    assert set(vals) == {-2, -1, 0, 1, 2} and abs(sum(vals) / len(vals)) < 0.1


def test_group_counts_students_not_rows_and_month_average():
    rows = [(pid(i % 25), f"2026-0{1 + i // 25}", "basic", "zhixing", "mistake_analysis", 10 * (1 + i // 25)) for i in range(100)]
    u = usage_of(rows)
    assert len(u.students) == 25 and u.months == ("2026-01", "2026-02", "2026-03", "2026-04")
    assert u.students[pid(3)][1]["zhixing"]["mistake_analysis"] == 25.0, "四个月 10、20、30、40 取月均"
    few = [(pid(i % 10), f"2026-0{1 + i // 10}", "basic", "zhixing", "mistake_analysis", 3) for i in range(40)]
    assert all(c["consumption"]["status"] == D.INSUFFICIENT for c in build(few)["consumption"]), "10 人 × 4 个月仍是 10 人"


def test_tier_change_across_months_is_refused():
    rows = [(pid(1), "2026-09", "basic", "zhixing", "mistake_analysis", 3), (pid(1), "2026-10", "plus", "zhixing", "mistake_analysis", 3)]
    with pytest.raises(D.InputRefused, match="档位不一致"):
        usage_of(rows)


def test_whole_cohort_below_min_is_insufficient_everywhere():
    rows = [(pid(i), "2026-09", "basic", "zhixing", "mistake_analysis", i + 1) for i in range(19)]
    r = build(rows)
    assert r["cohort"]["n_students"] == D.INSUFFICIENT
    assert all(c["consumption"]["status"] == D.INSUFFICIENT for c in r["consumption"])
    assert all(t["status"] == D.INSUFFICIENT for t in r["tier_cost"])


# ---------------- 标注与输出 ----------------
def test_banner_is_decided_by_data():
    r = build()
    assert r["banner"] == "合成，不是试点结论；价格为示例价" and r["example_prices"] is True
    md = D.render_dashboard_md(r)
    first = md.split("\n\n")[1]
    assert "合成，不是试点结论；价格为示例价" in first and "档位为导出时档位，期内升降档未拆分" in first, "首段标注"
    assert "与自然月不对齐" in first
    no_ver = build(prices={**PRICES, "example": False})
    assert no_ver["banner"].endswith("价格为示例价"), "没有签字版本号一律标示例价"
    signed = build(prices={**PRICES, "example": False, "signoff_version": "prices-v1.0"})
    assert signed["banner"] == "合成，不是试点结论；价格为签字价（prices-v1.0）" and signed["example_prices"] is False
    bad = build(prices={**PRICES, "example": False, "signoff_version": "签字"})
    assert bad["banner"].endswith("示例价")
    assert D.banner(False, PRICES).startswith("试点数据")
    assert "D5 待产品确认" in md


def test_outputs_have_no_pseudonyms_and_exit_check_blocks():
    files = D.render_files(build())
    for text in files.values():
        assert "syn-" not in text and "pil-" not in text
    with pytest.raises(RuntimeError):
        D.assert_no_pseudonyms("x pil-abcdefghijkl y")


def test_write_outputs_is_exclusive_and_private(tmp_path):
    out = tmp_path / "out"
    D.write_outputs({"a.md": "x"}, out)
    assert stat.S_IMODE((out / "a.md").stat().st_mode) == 0o600
    with pytest.raises(D.OutputRefused, match="非空"):
        D.write_outputs({"b.md": "x"}, out)
    f = tmp_path / "file"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(D.OutputRefused):
        D.write_outputs({"a.md": "x"}, f)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "empty", target_is_directory=True)
    with pytest.raises(D.OutputRefused, match="符号链接"):
        D.write_outputs({"a.md": "x"}, link)


def test_non_synthetic_input_requires_out(monkeypatch, tmp_path):
    real = D.load_inputs

    def fake(*a):
        inputs = list(real(*a))
        inputs[0] = dataclasses.replace(inputs[0], synthetic=False)
        return tuple(inputs)

    monkeypatch.setattr(D, "load_inputs", fake)
    (tmp_path / "sys-tmp").mkdir()
    monkeypatch.setattr(D.tempfile, "tempdir", str(tmp_path / "sys-tmp"))   # 变异放行时也不在系统临时目录留下文件
    for name, text in Y.render().items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    with pytest.raises(D.OutputRefused, match="--out"):
        D.run_dashboard(tmp_path / "usage.csv", tmp_path / "call-records.jsonl", CONFIG / "prices.example.yaml",
                        CONFIG / "cost_profile.yaml", D.DASH_CONFIG, None)
    assert not any(p.name.startswith("cost-dashboard") for p in tmp_path.iterdir())
    assert not any((tmp_path / "sys-tmp").iterdir()), "拒绝时不建临时输出目录"


def test_dash_config_guards_and_consistency_with_cost_profile():
    dcfg = D.check_dash_config(load_yaml(D.DASH_CONFIG))
    for bad in ({**DCFG, "min_group": 19}, {**DCFG, "round_to": 5}, {**DCFG, "round_to": 30, "min_group": 20},
                {**DCFG, "extra": 1}, {**DCFG, "min_group": True}):
        with pytest.raises(D.InputRefused):
            D.check_dash_config(bad)
    profile = load_yaml(CONFIG / "cost_profile.yaml")
    prices = load_yaml(CONFIG / "prices.example.yaml")
    quotas = load_quotas(profile["quota_config"])
    un = dcfg["unattributed"]["activities"]
    resources = set(next(iter(quotas.values())))
    covered = {a["resource"] for a in un} | {a["resource"] for s in profile["subjects"].values() for a in s["activities"]}
    assert covered == resources, "额度配置里的每种资源都要在画像或未归因计价里有口径（哪怕是“不计价”）"
    for a in un:
        assert a["resource"] in resources
        if a["task"] and a["task"] != "voice":
            assert a["task"] in prices["per_million_tokens"] and a["task"] in profile["fallback_tokens"]
    for s, cfg in profile["subjects"].items():
        if cfg.get("shelved"):
            continue
        for res in ("ai_active_time", "voice_realtime"):
            mine = sorted((a["task"], a["calls_per_unit"]) for a in cfg["activities"] if a["resource"] == res)
            theirs = sorted((a["task"], a["calls_per_unit"]) for a in un if a["resource"] == res)
            assert mine == theirs, f"未归因的 {res} 计价须与画像里各科的假设一致（{s}）"
    assert dcfg["unattributed"]["label"] not in {c["label"] for c in profile["subjects"].values()}


# ---------------- 输入守卫 ----------------
GOOD = [(pid(1), "2026-09", "basic", "zhixing", "mistake_analysis", 3)]


@pytest.mark.parametrize("text,why", [
    (usage_text(GOOD, synthetic="false"), "合成样例"),
    (usage_text([("pil-testcaseab",) + GOOD[0][1:]]), "syn- 开头"),
    (usage_text(GOOD, header="pseudonym,month,tier,subject,resource,student_name"), "个人信息字段"),
    (usage_text(GOOD, header="pseudonym,month,tier,subject,resource,qty"), "不认识"),
    (usage_text(GOOD, header="pseudonym,month,tier,subject,resource,学校"), "个人信息字段"),
    (usage_text(GOOD, header="pseudonym,month,tier,subject,resource"), "六列"),
    (usage_text([GOOD[0][:5] + ("13812345678",)]), "个人信息"),
    (usage_text([GOOD[0][:3] + ("数学",) + GOOD[0][4:]]), "个人信息"),
    (usage_text([GOOD[0][:5] + ("a@b.cn",)]), "个人信息"),
    (usage_text([("syn-12345",) + GOOD[0][1:]]), "伪名格式"),
    (usage_text([GOOD[0][:1] + ("2026-13",) + GOOD[0][2:]]), "月份"),
    (usage_text([GOOD[0][:2] + ("gold",) + GOOD[0][3:]]), "档位"),
    (usage_text([GOOD[0][:3] + ("math",) + GOOD[0][4:]]), "科目"),
    (usage_text([GOOD[0][:4] + ("tokens",) + GOOD[0][5:]]), "资源"),
    (usage_text([GOOD[0][:5] + ("-1",)]), "非负整数"),
    (usage_text([GOOD[0][:5] + ("1.5",)]), "非负整数"),
    (usage_text(GOOD + GOOD), "重复"),
    (usage_text(GOOD + [GOOD[0][:2] + ("plus", "aya", "mistake_analysis", 1)]), "档位不一致"),
    (usage_text(GOOD, qc="ch8-hypothesis@1"), "额度配置"),
    (usage_text([]), "没有数据行"),
    ("pseudonym,month,tier,subject,resource,quantity\n", "元数据行"),
    ("# format=other/1 synthetic=true quota_config=v12-pilot@1\n", "format"),
    ("# format=cost-dashboard-usage/1 synthetic=true quota_config=v12-pilot@1 school=x\n", "不认识"),
])
def test_usage_guards(text, why):
    with pytest.raises(D.InputRefused) as e:
        D.parse_usage(text, QUOTAS, "v12-pilot@1", SUBJECTS)
    assert why in str(e.value)
    assert "testcase" not in str(e.value) and "13812345678" not in str(e.value), "错误信息不回显输入内容"


R0 = {"subject": "aya", "task": "T1", "outcome": "ok", "input_tokens": 1, "output_tokens": 1}


@pytest.mark.parametrize("rec,why", [
    ({**R0, "model_version": "x"}, "模型标识"),
    ({**R0, "reported_model": "x"}, "模型标识"),
    ({**R0, "vendor": "x"}, "模型标识"),
    ({**R0, "usage_ref": "x"}, "关联摘要"),
    ({**R0, "idempotency_ref": "x"}, "关联摘要"),
    ({**R0, "student_name": "x"}, "个人信息"),
    ({**R0, "pseudonym": "syn-abcdefghijk"}, "个人信息"),
    ({**R0, "note": "x"}, "不认识"),
    ({k: v for k, v in R0.items() if k != "outcome"}, "缺字段"),
    ({**R0, "task": "chat"}, "任务编码"),
    ({**R0, "outcome": "maybe"}, "结果"),
    ({**R0, "input_tokens": -1}, "token"),
    ({**R0, "input_tokens": True}, "token"),
    ({**R0, "output_tokens": 1.5}, "token"),
    ({**R0, "attempts": -1}, "尝试次数"),
    ({**R0, "fallback": 1}, "fallback"),
    ({**R0, "subject": "math"}, "科目"),
    ({**R0, "tier": "gold"}, "保留字段"),
    ({**R0, "data_class": "13812345678"}, "个人信息"),
    ({**R0, "data_class": "secret"}, "数据级别"),
])
def test_record_guards(rec, why):
    with pytest.raises(D.InputRefused) as e:
        D.parse_records(json.dumps(rec, ensure_ascii=False) + "\n", SUBJECTS)
    assert why in str(e.value)


def test_record_guards_reject_non_objects_and_accept_loadtest_sample():
    for text in ("[1]\n", "not json\n"):
        with pytest.raises(D.InputRefused):
            D.parse_records(text, SUBJECTS)
    recs = D.load_call_records(SAMPLE / "call-records.jsonl", ("aya", "wenli", "zhixing", "echo"))
    assert len(recs) == 20 and all("attempts" in r and "fallback" not in r for r in recs), "沿用 loadtest 的 call-records 格式（无回退字段）"


def test_read_limited_only_regular_files(tmp_path):
    with pytest.raises(D.InputRefused, match="普通文件"):
        D.read_limited(tmp_path, 10**6, "消耗表")
    f = tmp_path / "a.csv"
    f.write_text("x" * 11, encoding="utf-8")
    with pytest.raises(D.InputRefused):
        D.read_limited(f, 10, "消耗表")
    assert D.read_limited(f, 11, "消耗表") == b"x" * 11


def test_guard_patterns_are_verbatim_kge_calibrate():
    path = str(REPO / "tools/kge-calibrate")
    sys.path.insert(0, path)
    try:
        kin = importlib.import_module("kge_calibrate.inputs")
    finally:
        sys.path.remove(path)
    assert D.FORBIDDEN_COLUMN.pattern == kin.FORBIDDEN_COLUMN.pattern and D.FORBIDDEN_COLUMN.flags == kin.FORBIDDEN_COLUMN.flags
    assert D.PSEUDONYM.pattern == kin.PSEUDONYM.pattern and D.SYNTHETIC_PREFIX == kin.SYNTHETIC_PREFIX
    assert [p.pattern for p in D.PERSONAL] == [p.pattern for p in kin.PERSONAL]


# ---------------- 合成队列 ----------------
@pytest.fixture(scope="module")
def synthetic():
    files = Y.render()
    profile = load_yaml(CONFIG / "cost_profile.yaml")
    quotas = load_quotas(profile["quota_config"])
    subjects = tuple(profile["subjects"])
    usage = D.parse_usage(files["usage.csv"], quotas, profile["quota_config"], subjects)
    records = D.parse_records(files["call-records.jsonl"], subjects)
    prices = load_yaml(CONFIG / "prices.example.yaml")
    dcfg = D.check_dash_config(load_yaml(D.DASH_CONFIG))
    args = (usage, records, prices, profile, quotas, dcfg)
    return {"files": files, "usage": usage, "records": records, "r": D.build_dashboard(*args), "P": D.prepare(*args),
            "args": args, "profile": profile, "prices": prices, "dcfg": dcfg}


def test_synth_is_fixed_seed_and_synthetic(synthetic):
    assert Y.render() == synthetic["files"]
    assert Y.render(Y.SEED + 1) != synthetic["files"]
    assert synthetic["files"]["usage.csv"].startswith("# format=cost-dashboard-usage/1 synthetic=true ")
    assert all(p.startswith("syn-") for p in synthetic["usage"].pseudonyms)
    assert not re.search(r"model|vendor|fake-", synthetic["files"]["call-records.jsonl"]), "调用记录不含模型标识"
    tiers: dict[str, int] = {}
    for t, _ in synthetic["usage"].students.values():
        tiers[t] = tiers.get(t, 0) + 1
    assert tiers["max"] < 20 <= min(tiers[t] for t in ("basic", "plus", "pro")), "Max 档故意不足 20 人"
    assert all(n % 10 not in (0, 9) for n in tiers.values()), "各档人数不恰在取整边界上"
    assert all(D.UNATTR in use for _, use in synthetic["usage"].students.values()), "每名学生都有未归因份额"
    rs = synthetic["records"]
    assert any(r["attempts"] > 1 for r in rs) and any(r["fallback"] for r in rs) and any(r["subject"] is None for r in rs)


def _interp(v, q):
    v = sorted(v)
    h = (len(v) - 1) * q
    lo = int(h)
    return v[lo] + (h - lo) * (v[min(lo + 1, len(v) - 1)] - v[lo])


def test_synthetic_tier_cost_matches_independent_hand_calc(synthetic):
    """不经 monthly_cost / lookup_tokens，按画像口径直接从记录重算每名学生的月成本，与仪表盘的 P50 / P90 一致"""
    recs, prices, profile = synthetic["records"], synthetic["prices"], synthetic["profile"]

    def mean_cost(pred, task):
        ok = [r for r in recs if r["outcome"] == "ok" and r["task"] == task and pred(r)]
        i, o = sum(r["input_tokens"] for r in ok) / len(ok), sum(r["output_tokens"] for r in ok) / len(ok)
        p = prices["per_million_tokens"][task]
        return (i * p["input"] + o * p["output"]) / 1e6

    t2 = mean_cost(lambda r: r["subject"] == "zhixing", "T2")
    t4 = mean_cost(lambda r: r["subject"] == "wenli", "T4")
    teach = mean_cost(lambda r: True, "T1") + mean_cost(lambda r: True, "T9")
    voice = prices["voice_per_minute"]["price"]
    per_tier: dict[str, list[tuple[str, float]]] = {}
    for p, (tier, use) in synthetic["usage"].students.items():
        c = use.get("zhixing", {}).get("mistake_analysis", 0) * 0.5 * t2 + use.get("wenli", {}).get("mistake_analysis", 0) * 0.3 * t4
        un = use.get(D.UNATTR, {})
        c += un.get("ai_active_time", 0) * teach + un.get("voice_realtime", 0) * voice
        per_tier.setdefault(tier, []).append((p, c))
    for row in synthetic["r"]["tier_cost"]:
        pts = per_tier[row["tier"]]
        shown = n_shown([p for p, _ in pts])
        if shown is None:
            assert row["status"] == D.INSUFFICIENT
            continue
        v = [c for _, c in pts]
        assert row["n_students"] == shown and abs(row["n_students"] - len(v)) <= 12
        assert row["p50"] == pytest.approx(math.floor(_interp(v, 0.5) / 0.1 + 0.5) * 0.1, abs=1e-9)
        p90 = _interp(v, 0.9)
        if sum(1 for x in v if x > p90 + 1e-12) >= 5:
            assert row["p90"] == pytest.approx(math.floor(p90 / 0.1 + 0.5) * 0.1, abs=1e-9)
        else:
            assert row["p90"] == D.INSUFFICIENT


def test_synthetic_consumption_matches_direct_sort(synthetic):
    vals = [use.get(D.UNATTR, {}).get("ai_active_time", 0.0) for t, use in synthetic["usage"].students.values() if t == "basic"]
    c = cons(synthetic["r"], "basic", D.ALL, "ai_active_time")["consumption"]
    assert c["p50"] == math.floor(_interp(vals, 0.5) / 30 + 0.5) * 30 and c["p90"] == math.floor(_interp(vals, 0.9) / 30 + 0.5) * 30


def test_perturbation_is_on_in_the_report(synthetic):
    """扰动确实开着：报表里每个组的公开人数都是“真实人数 + 成员集合的扰动”取整；至少三个成员集合的扰动不为 0"""
    nonzero = set()
    for key, (pts, step) in synthetic["P"].points.items():
        members = frozenset(p for p, _ in pts)
        g = D.group(pts, step, synthetic["dcfg"])
        assert (g["status"] == "ok") == (n_shown(members) is not None)
        if g["status"] == "ok":
            assert g["n_students"] == n_shown(members)
        if D.noise(D.set_key(members)) != 0:
            nonzero.add(members)
    assert len(nonzero) >= 3


def test_published_quantiles_are_not_any_students_exact_value(synthetic):
    """公开的成本分位数（插值 + 粗化）不等于任何一名学生的精确原始值"""
    checked = 0
    for key, (pts, step) in synthetic["P"].points.items():
        if key[0] not in ("tier_cost", "alloc_calls", "alloc_tokens"):
            continue
        g = D.group(pts, step, synthetic["dcfg"])
        if g["status"] != "ok":
            continue
        exact = [v for _, v in pts]
        for q in ("p50", "p90"):
            if isinstance(g[q], float):
                checked += 1
                assert all(abs(g[q] - v) > 1e-9 for v in exact), (key, q)
    assert checked >= 10


# ---------------- 差分：逐个删一人重跑 ----------------
NOISE_VALUES = [v for v, _ in D.NOISE_TABLE]


def _shown(n: int, e: int) -> int | None:
    m = n + e
    return None if m < 22 else m // 10 * 10


def n_candidates(g0: dict, g1: dict) -> set[int]:
    """与“删前 / 删后”两次公开人数都相容的真实人数（攻击者知道扰动分布与取整规则、不知道表键）"""
    s0 = g0.get("n_students") if g0["status"] == "ok" else None
    s1 = g1.get("n_students") if g1["status"] == "ok" else None
    return {n0 for n0 in range(1, 400)
            if any(_shown(n0, e) == s0 for e in NOISE_VALUES) and any(_shown(n0 - 1, e) == s1 for e in NOISE_VALUES)}


def _alternatives(key: tuple, v: float, others: list[float], step: float, quota: float) -> list[float]:
    if key[0] == "consumption":
        deltas = [1, -1, 2, -2, 3, -3]
    elif key[0] == "utilization":
        deltas = [k / quota for k in (1, -1, 2, -2, 3, -3)]
    else:
        deltas = [step / 4, -step / 4, step / 2, -step / 2]
    return [v + d for d in deltas] + sorted(set(others))


def not_pinned(key: tuple, pts: list, p: str, step: float, dcfg: dict, quota: float) -> bool:
    """知道其他所有人精确值的攻击者也定不了 p 的精确值：存在另一个取值，公开结果完全相同"""
    g0 = D.group(pts, step, dcfg)
    v = dict(pts)[p]
    for v2 in _alternatives(key, v, [x for q, x in pts if q != p], step, quota):
        if v2 < 0 or abs(v2 - v) < 1e-9:
            continue
        if D.group([(q, v2 if q == p else x) for q, x in pts], step, dcfg) == g0:
            return True
    return False


def _without(u: D.Usage, p: str) -> D.Usage:
    return dataclasses.replace(u, students={k: v for k, v in u.students.items() if k != p})


def test_differential_deletion_cannot_recover_individual(synthetic):
    """安全 B1：逐个删一人重跑（每档取 3 人，Max 取 2 人）。不含此人的组输出不变；含此人的组：真实人数与两次输出相容的可能至少两种
    （门槛翻转不能唯一定出人数），且此人的精确值定不下来（知道其余所有人也不行）"""
    usage, records, prices, profile, quotas, dcfg = synthetic["args"]
    P0 = synthetic["P"]
    victims = []
    for tier, k in (("basic", 3), ("plus", 3), ("pro", 3), ("max", 2)):
        victims += sorted(p for p, (t, _) in usage.students.items() if t == tier)[:k]
    flips = member_groups = 0
    for p in victims:
        P1 = D.prepare(_without(usage, p), records, prices, profile, quotas, dcfg)
        for key, (pts, step) in P0.points.items():
            g0 = D.group(pts, step, dcfg)
            pts1 = P1.points.get(key, ([], step))[0]
            g1 = D.group(pts1, step, dcfg) if pts1 else {"status": D.INSUFFICIENT}
            if p not in dict(pts):
                assert g0 == g1, (p, key)
                continue
            member_groups += 1
            assert len(n_candidates(g0, g1)) >= 2, (key, g0, g1)
            flips += (g0["status"] == "ok") != (g1["status"] == "ok")
            if g0["status"] == "ok":
                quota = quotas[key[1]][key[3]] if key[0] in ("consumption", "utilization") else 0.0
                assert not_pinned(key, pts, p, step, dcfg, quota), (p, key)
    assert member_groups >= 100


def test_differential_without_noise_would_pin_counts():
    """对照：没有扰动时，门槛翻转（20 → 19）能唯一定出真实人数——说明上面的断言不是空转"""
    g0 = {"status": "ok", "n_students": 20}
    g1 = {"status": D.INSUFFICIENT}
    no_noise = {n0 for n0 in range(1, 400) if (None if n0 < 20 else n0 // 10 * 10) == 20 and n0 - 1 < 20}
    assert no_noise == {20}
    assert len(n_candidates(g0, g1)) >= 2


# ---------------- 逐字节一致 ----------------
def test_synthetic_dashboard_is_byte_reproducible(synthetic, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        for name, text in synthetic["files"].items():
            (d / name).write_text(text, encoding="utf-8")
    outs = [D.run_dashboard(d / "usage.csv", d / "call-records.jsonl", CONFIG / "prices.example.yaml", CONFIG / "cost_profile.yaml",
                            D.DASH_CONFIG, d / "out") for d in (a, b)]
    for name in ("cost-dashboard.json", "cost-dashboard.md"):
        assert (a / "out" / name).read_bytes() == (b / "out" / name).read_bytes()
    assert outs[0]["banner"] == "合成，不是试点结论；价格为示例价"


def test_dashboard_matches_committed_sample(synthetic):
    files = D.render_files(synthetic["r"])
    assert files["cost-dashboard.md"] == (SAMPLE / "cost-dashboard.md").read_text(encoding="utf-8"), \
        "样例过期：按 Makefile 中 cost-dashboard 的注释重生 sample/cost-dashboard.md"
    assert hashlib.sha256(files["cost-dashboard.json"].encode("utf-8")).hexdigest() == SAMPLE_JSON_SHA256
