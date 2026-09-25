"""日志与错误详情里不出现学生作答原文和答案（spec 验收；同阿鸦 leak.test.ts 的做法）。"""
from __future__ import annotations

import json
import logging

from conftest import grade_body
from test_ingest import _tamper

SECRET = "學生作答原文-SYNTHETIC-51ab"
ALLOWED_KEYS = {"method", "status", "code", "request_id", "duration_ms", "replayed", "error_class"}


async def test_logs_hold_only_whitelisted_fields(api, student, fake_adapter, caplog):
    caplog.set_level(logging.INFO, logger="starbuddy.learning")
    bodies = [grade_body(student, text=SECRET)]
    bad_rid = grade_body(student, text=SECRET)
    bad_rid["context"]["request_id"] = SECRET  # 不是 UUID：不得进日志
    bodies.append(bad_rid)
    for body in bodies:
        await api.post("/internal/v0/grade", json=body)
    for kind in ("extra_field", "db_constraint", "dangling_id"):
        fake_adapter.mutate = _tamper(kind)
        r = await api.post("/internal/v0/grade", json=grade_body(student, text=SECRET))
        assert SECRET not in r.text
    fake_adapter.mutate = None
    fake_adapter.status, fake_adapter.problem = 404, {"code": "ITEM_NOT_FOUND", "detail": SECRET}
    r = await api.post("/internal/v0/grade", json=grade_body(student, text=SECRET))
    assert SECRET not in r.text
    records = [json.loads(rec.getMessage()) for rec in caplog.records if rec.name == "starbuddy.learning"]
    assert len(records) >= 6
    for rec in records:
        assert set(rec) <= ALLOWED_KEYS, rec
    assert SECRET not in caplog.text
