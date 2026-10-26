"""模型调用记录落库（specs/plt-model-call-log 范围第 2、5 条；决定记录第 1、2 条；迁移 0060）。

- to_row：字段映射（供应商 / 模型 / 版本、提示词模板与版本、是否切备、尝试状态、估算成本）与 0060 列约束同一口径的格式核对；
- 禁止字段静态扫描：写库的列恰好是 0060 的列级 INSERT 授权、没有学生 / 账号 / 原文 / 输出类的列；调用记录里的模型输出、提示词不会进行；
- TeeSink：单个落地出错不影响其他落地与路由结果；
- DbSink：write 不等库、队列满丢弃并计数、按批写、写库失败只计数（日志不带异常消息）、退出时尽力刷写（写不完的计丢弃）；
- 真实库：学习服务登录角色（只继承 starbuddy_learning）能按 0060 的授权写入，读不到；
- 启动：LEARNING_MODEL_CALL_LOG 解析；开关打开时 0060 不在或没有 INSERT 权限拒绝启动；三个计数接在 DbSink 上。
全部合成数据（规则 8）。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from learning import metrics
from learning.model_router import MemorySink, ModelRouter, ProviderError, ProviderReply, Usage, parse
from learning.model_router.ledger import (INSERT_SQL, MODEL_CALL_COLUMNS, CallRecord, DbSink, LogSink, TeeSink, digest,
                                          to_row)
from learning.model_router.types import Attempt, RouteUnavailable
from test_model_router import Clock, SECRET_OUTPUT, SECRET_PROMPT, doc_with, fakes, req, target, vendor

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "db/migrations/0060_platform__model_call_log.up.sql"
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
MV_A = "model:bailian/syn-model@2026-09-01"
MV_B = "model:ark/syn-backup@v2"


def rec(**over) -> CallRecord:
    base = dict(env="test", task="T2", data_class="L1", subject="aya", usage_ref=digest("syn-usage-1"),
                idempotency_ref=digest("syn-idem-1"), source_version="prompt:syn-grade@1.0", outcome="ok", model_version=MV_A,
                attempts=(Attempt("bailian", MV_A, "ok", 9),), input_tokens=10, output_tokens=5, elapsed_ms=12,
                estimated_cost_cny=0.0125)
    base.update(over)
    return CallRecord(**base)


def as_dict(row) -> dict:
    return dict(zip(MODEL_CALL_COLUMNS, row))


# ---------------------------------------------------------------- 字段映射与格式核对
def test_ok_record_maps_to_row():
    r = as_dict(to_row(rec(), NOW))
    assert r["occurred_at"] == NOW and (r["vendor"], r["model"], r["model_release"]) == ("bailian", "syn-model", "2026-09-01")
    assert (r["prompt_template"], r["prompt_version"], r["source_version"]) == ("syn-grade", "1.0", "prompt:syn-grade@1.0")
    assert r["attempt_count"] == 1 and r["attempt_statuses"] == ["ok"] and r["fallback"] is False
    assert r["estimated_cost_cny"] == Decimal("0.012500") and r["usage_ref"] == digest("syn-usage-1")


def test_failed_record_takes_last_attempt_target_and_marks_fallback():
    attempts = (Attempt("bailian", MV_A, "timeout", 5), Attempt("bailian", MV_A, "server", 5), Attempt("ark", MV_B, "server", 4))
    r = as_dict(to_row(rec(outcome="unavailable", model_version=None, attempts=attempts, input_tokens=0, output_tokens=0,
                           estimated_cost_cny=None), NOW))
    assert (r["vendor"], r["model"], r["model_release"], r["model_version"]) == ("ark", "syn-backup", "v2", None)
    assert r["attempt_count"] == 3 and r["attempt_statuses"] == ["timeout", "server", "server"] and r["fallback"] is True
    assert r["estimated_cost_cny"] is None


def test_retry_on_same_target_is_not_fallback():
    attempts = (Attempt("bailian", MV_A, "timeout", 5), Attempt("bailian", MV_A, "ok", 5))
    assert as_dict(to_row(rec(attempts=attempts), NOW))["fallback"] is False


def test_rejected_record_without_attempts_has_no_target_and_invalid_source():
    r = as_dict(to_row(rec(outcome="rejected", model_version=None, attempts=(), source_version="invalid", subject=None,
                           input_tokens=0, output_tokens=0, estimated_cost_cny=None), NOW))
    assert all(r[c] is None for c in ("vendor", "model", "model_release", "model_version", "prompt_template", "prompt_version", "subject"))
    assert r["attempt_count"] == 0 and r["attempt_statuses"] == [] and r["source_version"] == "invalid"


BAD = {
    "供应商不是编码": dict(attempts=(Attempt("bai lian", "model:bai lian/m@v1", "ok", 1),), model_version="model:bai lian/m@v1"),
    "usage_ref 是原值": dict(usage_ref="syn-usage-1"),
    "尝试状态是自由文本": dict(attempts=(Attempt("bailian", MV_A, "学生原话", 1),)),
    "结果不是固定编码": dict(outcome="weird"),
    "成功却没有 model_version": dict(model_version=None),
    "失败却带 model_version": dict(outcome="unavailable"),
    "未知学科": dict(subject="math"),
    "未知环境": dict(env="local"),
    "任务不是 T1–T9": dict(task="T10"),
    "数据级别不对": dict(data_class="L4"),
    "source_version 不合格": dict(source_version="prompt:Has Space@1"),
    "token 为负": dict(input_tokens=-1),
    "token 超出 int4": dict(output_tokens=2**31),
    "耗时不是整数": dict(elapsed_ms=1.5),
    "估算成本为负": dict(estimated_cost_cny=-0.1),
    "尝试超过 64 次": dict(attempts=tuple(Attempt("bailian", MV_A, "timeout", 1) for _ in range(65)), outcome="unavailable",
                       model_version=None),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_invalid_fields_are_refused_before_queueing(name):
    with pytest.raises(ValueError):
        to_row(rec(**BAD[name]), NOW)


def test_naive_timestamp_refused():
    with pytest.raises(ValueError):
        to_row(rec(), datetime(2026, 10, 26, 8, 0))


# ---------------------------------------------------------------- 禁止字段静态扫描
FORBIDDEN = re.compile(r"(student|account|household|guardian|learner|user|phone|email|message|content|answer|response|body|raw|"
                       r"prompt_text|^output$|reported_model|text)")


def test_columns_are_exactly_the_0060_insert_grant_and_carry_no_forbidden_fields():
    sql = MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"GRANT INSERT \(([^)]*)\)\s+ON platform\.model_call_log TO starbuddy_learning", sql)
    assert m, "0060 里找不到学习服务的列级 INSERT 授权"
    granted = [c.strip() for c in m.group(1).split(",")]
    assert list(MODEL_CALL_COLUMNS) == granted, "写库的列必须与 0060 的列级授权逐列一致（顺序也一致）"
    assert not [c for c in MODEL_CALL_COLUMNS if FORBIDDEN.search(c)]
    assert INSERT_SQL.startswith("INSERT INTO platform.model_call_log (") and "RETURNING" not in INSERT_SQL
    assert INSERT_SQL.count("$") == len(MODEL_CALL_COLUMNS)


def test_row_never_contains_prompt_output_or_reported_model():
    r = to_row(rec(reported_model="vendor-reported-" + SECRET_OUTPUT[:4], model_mismatch=True), NOW)
    flat = json.dumps([str(v) for v in r], ensure_ascii=False)
    assert "vendor-reported" not in flat and SECRET_OUTPUT[:4] not in flat
    assert len(r) == len(MODEL_CALL_COLUMNS)


def test_ledger_source_writes_no_request_or_reply_content():
    src = (ROOT / "services/learning/learning/model_router/ledger.py").read_text(encoding="utf-8")
    db_part = src[src.index("# ---------------------------------------------------------------- 数据库落地"):]
    for word in (r"messages", r"\.output\b", r"reported_model", r"usage_id", r"idempotency_key"):
        assert not re.search(word, db_part), f"数据库落地不应碰 {word}"


# ---------------------------------------------------------------- TeeSink
class Boom:
    def write(self, record):
        raise RuntimeError("synthetic sink failure " + SECRET_PROMPT)


def test_tee_isolates_failing_sink(caplog):
    mem1, mem2 = MemorySink(), MemorySink()
    with caplog.at_level(logging.WARNING, logger="starbuddy.model_router"):
        TeeSink(mem1, Boom(), mem2).write(rec())
    assert len(mem1.records) == 1 and len(mem2.records) == 1
    assert "ledger_sink_failed" in caplog.text and SECRET_PROMPT not in caplog.text


def router_with(sink, behaviors):
    cfg = parse(doc_with("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L2"], "primary": target("a")}}), "test")
    return ModelRouter(cfg, fakes(cfg, behaviors), sink=sink, clock=Clock())


async def test_routing_result_unchanged_when_db_sink_fails():
    """写库失败、落地抛异常都不影响路由结果（决定记录第 2 条：异常隔离）"""
    async def ok(t, m, k):
        return ProviderReply(output=SECRET_OUTPUT, usage=Usage(3, 5), reported_model=t.api_name)
    mem = MemorySink()
    failed: list[int] = []
    db = DbSink(FailingPool(), on_failed=failed.append, flush_seconds=0.01)
    db.start()
    router = router_with(TeeSink(mem, Boom(), db), {"a": ok})
    out = await router.route(req())
    assert out.output == SECRET_OUTPUT and len(mem.records) == 1
    await db.aclose(1.0)
    assert sum(failed) == 1


async def test_unregistered_prompt_template_is_recorded_as_invalid_but_result_unchanged():
    """审查建议：模板名不在提示词登记表里的，调用记录 source_version 记 invalid、模板两列为空；返回给调用方的 source_version 不变
    （插件契约字段与适配器看到的行为都不变）"""
    async def ok(t, m, k):
        return ProviderReply(output=SECRET_OUTPUT, usage=Usage(3, 5), reported_model=t.api_name)
    mem = MemorySink()
    out = await router_with(mem, {"a": ok}).route(req(prompt_template="tutor-aya-hint", prompt_version="1.0"))
    assert out.source_version == "prompt:tutor-aya-hint@1.0"
    assert mem.records[-1].source_version == "invalid"
    row = as_dict(to_row(mem.records[-1], NOW))
    assert row["prompt_template"] is None and row["prompt_version"] is None and row["source_version"] == "invalid"
    out = await router_with(mem, {"a": ok}).route(req(prompt_template="zhixing-m3-grade", prompt_version="0.2.0"))
    assert out.source_version == mem.records[-1].source_version == "prompt:zhixing-m3-grade@0.2.0"
    assert as_dict(to_row(mem.records[-1], NOW))["prompt_template"] == "zhixing-m3-grade"


def test_recorded_prompt_templates_match_prompt_registry():
    """白名单 = 提示词登记表 packages/personas/registry.yaml 里非人设 Prompt 的 prompt_id（人设 prompts 目前为空，有了也一并核对）
    + 冒烟脚本自己的 model-smoke；登记表新增 Prompt 而这里没同步就失败"""
    import yaml
    from learning.model_router.router import RECORDED_PROMPT_TEMPLATES
    reg = yaml.safe_load((ROOT / "packages/personas/registry.yaml").read_text(encoding="utf-8"))
    ids = {p["prompt_id"] for section in ("personas", "tasks") for e in (reg.get(section) or []) for p in (e.get("prompts") or [])
           if isinstance(p, dict) and "prompt_id" in p}
    assert ids and RECORDED_PROMPT_TEMPLATES == ids | {"model-smoke"}
    smoke = (ROOT / "services/learning/scripts/model_smoke.py").read_text(encoding="utf-8")
    assert 'PROMPT_TEMPLATE, PROMPT_VERSION = "model-smoke"' in smoke


async def test_failed_route_is_still_logged_and_queued():
    async def bad(t, m, k):
        raise ProviderError("client")  # 不重试：不等退避
    db = DbSink(RecordingPool())
    router = router_with(TeeSink(LogSink(), db), {"a": bad})
    with pytest.raises(RouteUnavailable):
        await router.route(req())
    assert db.pending == 1
    await db.aclose(1.0)


# ---------------------------------------------------------------- DbSink（假连接池）
class _Conn:
    def __init__(self, pool) -> None:
        self.pool = pool

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Tx()

    async def executemany(self, sql, rows):
        await self.pool.on_write(sql, list(rows))


class RecordingPool:
    def __init__(self, delay: float = 0.0) -> None:
        self.batches: list[list[tuple]] = []
        self.delay = delay

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                return _Conn(pool)

            async def __aexit__(self, *a):
                return False
        return _Acq()

    async def on_write(self, sql, rows):
        assert sql == INSERT_SQL
        if self.delay:
            await asyncio.sleep(self.delay)
        self.batches.append(rows)


class FailingPool(RecordingPool):
    async def on_write(self, sql, rows):
        raise RuntimeError('new row violates check constraint DETAIL: Failing row contains (' + SECRET_PROMPT + ')')


def counters():
    seen = {"written": [], "dropped": [], "failed": []}
    return seen, {f"on_{k}": v.append for k, v in seen.items()}


async def test_write_does_not_wait_for_database_and_queue_full_drops():
    seen, hooks = counters()
    db = DbSink(RecordingPool(delay=10), max_queue=3, **hooks)
    for i in range(5):
        db.write(rec(usage_ref=digest(f"u{i}")))
    assert db.pending == 3 and sum(seen["dropped"]) == 2


async def test_background_task_writes_in_batches():
    seen, hooks = counters()
    pool = RecordingPool()
    db = DbSink(pool, batch_size=2, flush_seconds=0.01, **hooks)
    db.start()
    for i in range(5):
        db.write(rec(usage_ref=digest(f"u{i}")))
    for _ in range(100):
        if sum(seen["written"]) == 5:
            break
        await asyncio.sleep(0.01)
    assert sum(seen["written"]) == 5 and all(len(b) <= 2 for b in pool.batches)
    assert [r[MODEL_CALL_COLUMNS.index("usage_ref")] for b in pool.batches for r in b] == [digest(f"u{i}") for i in range(5)]
    await db.aclose(1.0)
    assert sum(seen["dropped"]) == 0


async def test_write_failure_only_counts_and_logs_class(caplog):
    seen, hooks = counters()
    db = DbSink(FailingPool(), **hooks)
    db.write(rec())
    db.write(rec())
    with caplog.at_level(logging.WARNING, logger="starbuddy.model_router"):
        await db.flush()
    assert sum(seen["failed"]) == 2 and sum(seen["written"]) == 0 and db.pending == 0
    assert "model_call_log_write_failed" in caplog.text and "RuntimeError" in caplog.text
    assert SECRET_PROMPT not in caplog.text and "Failing row" not in caplog.text, "不记异常消息（约束报错会带出行内容）"


async def test_write_timeout_counts_failed():
    seen, hooks = counters()
    db = DbSink(RecordingPool(delay=5), write_timeout=0.05, **hooks)
    db.write(rec())
    await db.flush()
    assert sum(seen["failed"]) == 1


class HangingAcquirePool(RecordingPool):
    """取连接一直等（池满、库连不上）：时限必须包住 acquire，不能只限 executemany"""

    def acquire(self):
        class _Acq:
            async def __aenter__(self):
                await asyncio.sleep(30)

            async def __aexit__(self, *a):
                return False
        return _Acq()


async def test_acquire_timeout_counts_failed():
    seen, hooks = counters()
    db = DbSink(HangingAcquirePool(), write_timeout=0.05, **hooks)
    db.write(rec())
    db.write(rec())
    await asyncio.wait_for(db.flush(), 2)
    assert sum(seen["failed"]) == 2 and sum(seen["written"]) == 0 and db.pending == 0


async def test_invalid_record_counts_failed_and_is_not_queued():
    seen, hooks = counters()
    db = DbSink(RecordingPool(), **hooks)
    db.write(rec(usage_ref="not-a-digest"))
    assert db.pending == 0 and sum(seen["failed"]) == 1


async def test_close_flushes_pending_rows():
    seen, hooks = counters()
    pool = RecordingPool()
    db = DbSink(pool, flush_seconds=60, **hooks)
    db.start()
    db.write(rec())
    db.write(rec())
    await db.aclose(1.0)
    assert sum(seen["written"]) == 2 and sum(seen["dropped"]) == 0
    db.write(rec())  # 关闭后不再收
    assert sum(seen["dropped"]) == 1


async def test_close_timeout_counts_unwritten_as_dropped():
    seen, hooks = counters()
    db = DbSink(RecordingPool(delay=10), batch_size=2, flush_seconds=0.01, write_timeout=30, **hooks)
    db.start()
    for i in range(3):
        db.write(rec(usage_ref=digest(f"u{i}")))
    await asyncio.sleep(0.05)
    await db.aclose(0.05)
    assert sum(seen["dropped"]) == 3 and sum(seen["written"]) == 0 and db.pending == 0


def test_counter_hook_errors_do_not_escape():
    def boom(n):
        raise RuntimeError("synthetic metrics failure")
    db = DbSink(RecordingPool(), max_queue=1, on_dropped=boom, on_failed=boom)
    db.write(rec())
    db.write(rec())
    db.write(rec(usage_ref="bad"))


def test_db_sink_wires_three_counters():
    from test_obs import value
    w0, d0, f0 = (value(f"starbuddy_model_call_log_{k}_total") for k in ("written", "dropped", "failed"))
    db = metrics.db_sink(RecordingPool(), max_queue=1)
    db.write(rec())
    db.write(rec())
    db.write(rec(usage_ref="bad"))
    asyncio.run(db.flush())
    assert value("starbuddy_model_call_log_written_total") == w0 + 1
    assert value("starbuddy_model_call_log_dropped_total") == d0 + 1
    assert value("starbuddy_model_call_log_failed_total") == f0 + 1


def test_router_sink_includes_db_sink_only_when_given():
    assert len(metrics.router_sink().sinks) == 2
    db = DbSink(RecordingPool())
    assert metrics.router_sink(db).sinks[-1] is db


# 真实库的用例（学习服务登录角色按 0060 的授权写入、启动核对通过）在 tests/test_model_call_log_db.py（不进路由变异：每个变异都起库太慢）


# ---------------------------------------------------------------- 开关与启动
def test_settings_model_call_log():
    from conftest import CREDENTIAL
    from learning.config import Settings
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://unused"}
    assert Settings.from_env(base).model_call_log is False
    assert Settings.from_env({**base, "LEARNING_MODEL_CALL_LOG": "0"}).model_call_log is False
    assert Settings.from_env({**base, "LEARNING_MODEL_CALL_LOG": "1"}).model_call_log is True
    for bad in ("true", "yes", "2", " 1"):
        with pytest.raises(RuntimeError, match="LEARNING_MODEL_CALL_LOG"):
            Settings.from_env({**base, "LEARNING_MODEL_CALL_LOG": bad})


class StartupPool:
    """启动前置的假池：0016、0058 都在；model_call_log 的表与授权按参数"""

    def __init__(self, table: bool, grant: bool) -> None:
        self.table, self.grant = table, grant
        self.seen: list[str] = []
        self.closed = False

    async def fetchval(self, q, *a):
        self.seen.append(q)
        if "model_call_log" in q:
            return self.table if "to_regclass" in q else self.grant
        return True


@pytest.mark.parametrize("table,grant,match", [(False, True, "run migration 0060"), (True, False, "not insertable by this role")])
async def test_startup_refuses_when_switch_on_and_0060_not_ready(table, grant, match):
    from conftest import settings
    from learning.app import create_app
    pool = StartupPool(table, grant)
    app = create_app(settings(model_call_log=True), pool=pool)
    with pytest.raises(RuntimeError, match=match):
        async with app.router.lifespan_context(app):
            pass
    if table:
        assert any("has_column_privilege(current_user, 'platform.model_call_log'" in q for q in pool.seen)


STAGING_PLAIN_DB = "postgresql://syn@10.0.0.5:5432/syn"  # 不带 verify-full 与固定 CA


def test_self_built_call_log_pool_checks_db_tls_in_staging():
    """审查建议：注入了业务池、开了调用记录落库但没注入调用记录池时，学习服务会按 LEARNING_DATABASE_URL 自建调用记录池——
    staging 下同样先核对 verify-full + 固定 CA，在建任何连接之前拒绝"""
    from conftest import settings
    from learning.app import create_app
    with pytest.raises(RuntimeError, match="verify-full"):
        create_app(settings(env="staging", database_url=STAGING_PLAIN_DB, model_call_log=True), pool=StartupPool(True, True))


@pytest.mark.parametrize("model_call_log,inject", [(False, False), (True, True)])
def test_db_tls_not_rechecked_when_no_pool_is_self_built(model_call_log, inject):
    """业务池与调用记录池都由测试注入（或开关关着）时不自建任何池，不按连接串核对（与之前一致）"""
    from conftest import settings
    from learning.app import create_app
    try:
        create_app(settings(env="staging", database_url=STAGING_PLAIN_DB, model_call_log=model_call_log), pool=StartupPool(True, True),
                   call_log_pool=StartupPool(True, True) if inject else None)
    except RuntimeError as e:  # 别的 staging 前置可能拒绝；这里只看不是连库 TLS 核对
        assert "verify-full" not in str(e)


async def test_startup_does_not_check_0060_when_switch_off():
    from conftest import settings
    from learning.app import create_app
    pool = StartupPool(False, False)
    app = create_app(settings(), pool=pool)
    try:  # 假池可能走不完后面的启动；这里只看开关关着时根本不查 0060
        async with app.router.lifespan_context(app):
            pass
    except Exception as e:  # noqa: BLE001
        assert "0060" not in str(e)
    assert not any("model_call_log" in q for q in pool.seen)
