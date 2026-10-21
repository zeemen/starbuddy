"""审查修正的护栏测试：输出目录（安全 L3）、库护栏（安全 L5）、价格表代号（安全 L4）、额度解析行数与画像引用（契约 L1）、
报告未计入项（契约 L2）、Makefile 参数护栏（安全 L2）、计量驱动桩与 BFF meteredCall 同口径（契约 H1）。"""
from __future__ import annotations

import copy
import os
import re
import subprocess
from pathlib import Path

import pytest

from loadtest import outdir
from loadtest.cost import CONFIG, build, load_quotas, load_yaml, render_markdown, validate
from loadtest.seed import SETUP_SQL, assert_local_test_db

ROOT = Path(__file__).resolve().parents[3]


# ---------------- 输出目录 ----------------
def test_default_out_is_fresh_private_tempdir():
    p = outdir.prepare(None, "starbuddy-guard-test")
    try:
        assert p.is_dir() and not p.is_symlink() and (p.stat().st_mode & 0o777) == 0o700
        assert p.name.startswith("starbuddy-guard-test.")
    finally:
        p.rmdir()


def test_out_dir_symlink_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError):
        outdir.prepare(link, "x")
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(ValueError):
        outdir.prepare(f, "x")


def test_write_does_not_follow_symlink(tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "baseline.md").symlink_to(target)
    with pytest.raises(OSError):
        outdir.write_text(tmp_path / "out" / "baseline.md", "overwrite")
    assert target.read_text() == "keep"


# ---------------- 库护栏 ----------------
def test_setup_sql_requires_test_db_and_unix_socket():
    assert "current_database() !~ '^starbuddy_test'" in SETUP_SQL
    assert "inet_server_addr() IS NOT NULL" in SETUP_SQL


async def test_seed_refuses_non_local_db():
    class Pool:
        def __init__(self, ok):
            self.ok = ok
            self.sql = ""

        async def fetchval(self, sql):
            self.sql = sql
            return self.ok

    good, bad = Pool(True), Pool(False)
    await assert_local_test_db(good)
    assert "inet_server_addr() IS NULL" in good.sql and "starbuddy_test" in good.sql
    with pytest.raises(RuntimeError):
        await assert_local_test_db(bad)


# ---------------- 价格表代号、额度解析、画像引用 ----------------
def _inputs():
    return load_yaml(CONFIG / "prices.example.yaml"), load_yaml(CONFIG / "cost_profile.yaml"), load_quotas("v12-pilot@1")


@pytest.mark.parametrize("vendor,model", [("acme-llm", "模型代号甲"), ("供应商代号甲", "gpt-9-2026-01-01"),
                                          ("供应商代号甲", "model:x/y@1"), ("供应商代号甲", "")])
@pytest.mark.parametrize("example", [True, False])
def test_prices_must_use_synthetic_codes(vendor, model, example):
    prices, profile, quotas = _inputs()
    prices = copy.deepcopy(prices)
    prices["example"] = example
    prices["per_million_tokens"]["T2"].update(vendor=vendor, model=model)
    with pytest.raises(ValueError):
        validate(prices, profile, quotas)


def test_signed_prices_with_codes_are_accepted_and_labelled():
    prices, profile, quotas = _inputs()
    prices = copy.deepcopy(prices)
    prices["example"] = False
    r = build([], prices, profile, quotas)
    assert r["example_prices"] is False
    assert "签字价" in render_markdown(r)


def test_profile_resource_or_task_must_exist():
    prices, profile, quotas = _inputs()
    bad = copy.deepcopy(profile)
    bad["subjects"]["aya"]["activities"].append({"resource": "no_such_resource", "task": "T1", "calls_per_unit": 1})
    with pytest.raises(ValueError, match="no_such_resource"):
        validate(prices, bad, quotas)
    bad = copy.deepcopy(profile)
    bad["subjects"]["aya"]["activities"].append({"resource": "ai_active_time", "task": "T7", "calls_per_unit": 1})
    with pytest.raises(ValueError, match="T7"):
        validate(prices, bad, quotas)


def _sql(rows: str) -> str:
    return ("INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)\n"
            f"SELECT 'x@1', v.tier::platform.plan_tier, v.res, v.qty, v.fair\n  FROM (VALUES\n{rows}\n  ) AS v(tier, res, qty, fair);\n")


@pytest.mark.parametrize("rows,why", [
    ("('basic', 'a', 1, false), ('plus', 'a', 1, false), ('pro', 'a', 1, false), ('max', 'a', 1.5, false)", "行数"),
    ("('basic', 'a', 1, false), ('basic', 'a', 2, false), ('plus', 'a', 1, false), ('pro', 'a', 1, false), ('max', 'a', 1, false)", "重复"),
    ("('basic', 'a', 1, false), ('plus', 'a', 1, false), ('pro', 'a', 1, false)", "集合"),
    ("('basic', 'a', 1, false), ('plus', 'b', 1, false), ('pro', 'a', 1, false), ('max', 'a', 1, false)", "集合"),
])
def test_quota_parse_checks_row_count_and_shape(tmp_path, rows, why):
    f = tmp_path / "m.sql"
    f.write_text(_sql(rows), encoding="utf-8")
    with pytest.raises(ValueError, match=why):
        load_quotas("x@1", f)


def test_quota_parse_accepts_well_formed(tmp_path):
    f = tmp_path / "m.sql"
    f.write_text(_sql("('basic', 'a', 1, false), ('plus', 'a', 2, false), ('pro', 'a', 3, false), ('max', 'a', 4, true)"),
                 encoding="utf-8")
    assert load_quotas("x@1", f) == {"basic": {"a": 1.0}, "plus": {"a": 2.0}, "pro": {"a": 3.0}, "max": {"a": 4.0}}


def test_report_mentions_unbilled_failed_attempts():
    prices, profile, quotas = _inputs()
    assert "失败尝试" in render_markdown(build([], prices, profile, quotas))


# ---------------- Makefile 护栏 ----------------
@pytest.mark.parametrize("args", [
    ["LOADTEST_ARGS=--fault fake-a=unavailable; touch /tmp/pwned-lt"], ["LOADTEST_ARGS=--out /etc"], ["LOADTEST_ARGS=$$(id)"],
    ["LOADTEST_ARGS=--scale-note x"], ["LOADTEST_STAGES=small;id"], ["LOADTEST_STAGES="], ["LOADTEST_OUT=/tmp/a b"],
    ["LOADTEST_OUT=/tmp/a'b"],
])
def test_makefile_rejects_unsafe_parameters(args):
    r = subprocess.run(["make", "--no-print-directory", "loadtest-baseline", *args], cwd=ROOT, capture_output=True, text=True,
                       timeout=60, env={**os.environ, "TMPDIR": "/nonexistent-guard"})
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "loadtest-baseline:" in r.stdout and not Path("/tmp/pwned-lt").exists()


@pytest.mark.parametrize("args", [["COST_OUT=/tmp/a b"], ["COST_OUT=/tmp/a'b"], ["COST_RECORDS=/tmp/x y.jsonl"]])
def test_makefile_cost_report_rejects_unsafe_paths(args):
    r = subprocess.run(["make", "--no-print-directory", "cost-report", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "cost-report:" in r.stdout, (r.stdout, r.stderr)


# ---------------- 计量驱动桩与 BFF 同口径 ----------------
def test_harness_runs_bff_meteredcall_with_same_policy():
    src = (ROOT / "tools/loadtest/node/meter-harness.mts").read_text(encoding="utf-8")
    meter_ts = (ROOT / "services/bff/src/metering/meter.ts").read_text(encoding="utf-8")
    # 直接 import BFF 的实现（不复制）：meteredCall（预留、退避、结算 / 释放、existing 按 state、过期后 replay、meteringFingerprint）、
    # httpLearningClient（LT-04 起 BFF 写入的传输层：/internal/v1 信封，内含 classifyResponse 结果归类）、httpLearningReplay（重放）、
    # itemFingerprint（内容指纹）
    for name, mod in (("meteredCall", "meter.ts"), ("httpLearningClient", "learning-client.ts"), ("httpLearningReplay", "learning-client.ts"),
                      ("itemFingerprint", "content-fingerprint.ts")):
        assert re.search(rf"import \{{[^}}]*\b{name}\b[^}}]*\}} from '\.\./\.\./\.\./services/bff/src/metering/{re.escape(mod)}'", src), name
    assert "meteringFingerprint(method, body)" in meter_ts, "请求指纹由 meteredCall 内部按 BFF 口径计算"
    bff_delays = re.search(r"const DEFAULT_DELAYS = \[([0-9, ]+)\]", meter_ts).group(1)
    harness_delays = re.search(r"const delays = \[([0-9, ]+)\]", src).group(1)
    assert bff_delays.replace(" ", "") == harness_delays.replace(" ", ""), "退避序列与 BFF 相同（只按比例缩短）"
    assert "fetch(" not in src and "return learning(method, body)" in src, "不自己发写入请求：传输层与 BFF 同一份"
    assert "127\\.0\\.0\\.1" in src and "'127.0.0.1'" in src, "只连本机学习服务、只监听本机"
