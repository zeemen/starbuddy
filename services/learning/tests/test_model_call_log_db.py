"""模型调用记录落库的真实库用例（specs/plt-model-call-log 范围第 5 条；迁移 0060）：学习服务登录角色（只继承 starbuddy_learning）
经 DbSink 按 0060 的列级授权与 RLS 写入（成功、切备失败、被拒三种形状），自己读不到；LEARNING_MODEL_CALL_LOG 的启动核对在真实库上通过。
一次性本地库、合成数据（规则 8）。纯逻辑的用例在 tests/test_model_call_log.py。"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from learning.model_router.ledger import DbSink, digest
from learning.model_router.types import Attempt
from test_model_call_log import MV_A, MV_B, counters, rec


async def test_db_sink_writes_real_rows_with_learning_role(pools):
    app_pool, admin = pools
    tag = uuid.uuid4().hex[:8]
    refs = [digest(f"syn-real-{tag}-{i}") for i in range(3)]
    seen, hooks = counters()
    db = DbSink(app_pool, **hooks)
    db.write(rec(usage_ref=refs[0]))
    db.write(rec(usage_ref=refs[1], outcome="unavailable", model_version=None, estimated_cost_cny=None, input_tokens=0, output_tokens=0,
                 attempts=(Attempt("bailian", MV_A, "timeout", 5), Attempt("ark", MV_B, "server", 4))))
    db.write(rec(usage_ref=refs[2], outcome="rejected", model_version=None, attempts=(), source_version="invalid", subject=None,
                 estimated_cost_cny=None, input_tokens=0, output_tokens=0))
    await db.flush()
    assert sum(seen["written"]) == 3 and not seen["failed"]
    rows = await admin.fetch("SELECT usage_ref, outcome, vendor, model, fallback, attempt_count, prompt_template, estimated_cost_cny"
                             " FROM platform.model_call_log WHERE usage_ref = ANY($1::text[]) ORDER BY outcome", refs)
    assert [(r["outcome"], r["vendor"], r["model"], r["fallback"], r["attempt_count"]) for r in rows] == [
        ("ok", "bailian", "syn-model", False, 1), ("rejected", None, None, False, 0), ("unavailable", "ark", "syn-backup", True, 2)]
    assert rows[0]["prompt_template"] == "syn-grade" and rows[0]["estimated_cost_cny"] == Decimal("0.012500")
    with pytest.raises(Exception, match="permission denied"):
        await app_pool.fetchval("SELECT count(*) FROM platform.model_call_log")


async def test_startup_check_passes_on_real_database(pools):
    from learning.app import check_call_log
    app_pool, _ = pools
    await check_call_log(app_pool)
