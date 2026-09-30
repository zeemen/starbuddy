"""批改请求（契约形状同 tools/e2e-cohort/e2e_cohort/replay.build_envelope）。只含合成作答。

grade_request：插件契约的 GradeRequest（经 BFF meteredCall 时由它补 student_ref、idempotency_key、usage_id）；
grade_body：学习服务 v1 信封 {request, ingest}（直连学习服务时用）。
"""
from __future__ import annotations

import uuid

from e2e_cohort.cohort import CONTENT_VERSION, RUBRIC


def grade_request(subject: str, student: str, item: str, text: str, attempt_id: str, occurred_at: str) -> dict:
    return {
        "context": {"request_id": str(uuid.uuid4()), "contract_version": "1.0.0", "subject": subject, "student_ref": student,
                    "data_class": "L2", "content_version": dict(CONTENT_VERSION[subject])},
        "attempt": {"attempt_id": attempt_id, "item_ref": f"{subject}:{item}", "occurred_at": occurred_at, "response": {"text": text},
                    "elapsed_sec": 60, "hint_level_used": 0},
        "rubric_version": RUBRIC[subject]}


def grade_body(subject: str, student: str, key: str, usage_id: str, item: str, text: str, attempt_id: str, occurred_at: str) -> dict:
    req = grade_request(subject, student, item, text, attempt_id, occurred_at)
    req["context"].update(usage_id=usage_id, idempotency_key=key)
    return {"request": req, "ingest": {"channel": "keypad"}}
