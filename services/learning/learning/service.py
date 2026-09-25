"""写入流程：幂等检查 → 调适配器 → 平台核对 → 一个事务落库（spec 范围第 2–6 条）。

冲刺 1 只有这一步，幂等表里的状态（pending / succeeded / failed）就是这一步的状态；
完整的编排状态机（ADR 03）在后续冲刺。
"""
from __future__ import annotations

import asyncio
import copy
import time
import uuid

import asyncpg
import httpx

from . import store
from .adapter_client import call_adapter
from .checks import collect_refs, local_id, parse_time, verify
from .config import Settings
from .contract import plugin_contract
from .errors import IngestError
from .fingerprint import request_fingerprint

METHODS = ("grade", "diagnose")
BUSY_POLL_SECONDS = 0.1


class IngestService:
    def __init__(self, settings: Settings, pool: asyncpg.Pool, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.pool = pool
        self.client = client

    # ---------- 请求校验（调适配器之前） ----------
    def _validate_request(self, method: str, body: object) -> dict:
        schema = "GradeRequest" if method == "grade" else "DiagnoseRequest"
        errors = plugin_contract().validate(schema, body)
        if errors:
            raise IngestError(400, "INVALID_REQUEST", "request does not match the contract", detail="; ".join(errors[:10]))
        assert isinstance(body, dict)
        subject = body["context"]["subject"]
        if subject not in self.settings.adapters:
            raise IngestError(400, "SUBJECT_NOT_SUPPORTED", "no adapter is configured for this subject")
        if method == "diagnose":
            if body["mode"] not in ("intake", "gap_test") or not body.get("attempts"):
                raise IngestError(422, "INVALID_REQUEST", "sprint 1 supports synchronous diagnose with attempts only")
            keys = [(a["item_ref"], parse_time(a["occurred_at"])) for a in body["attempts"]]
            if len(set(keys)) != len(keys):
                raise IngestError(422, "INVALID_REQUEST", "attempts must differ in item_ref or occurred_at")
        attempts = [body["attempt"]] if method == "grade" else body["attempts"]
        for a in attempts:
            if a["item_ref"].split(":", 1)[0] != subject:
                raise IngestError(400, "INVALID_REQUEST", "attempt item_ref is outside the request subject")
        return body

    def _summary_base(self, method: str, request: dict, response: dict, verified, attempt_ids: list[str]) -> dict:
        """返回给 BFF 的摘要（spec 待人确认 #6）：只有 ID 与编码，不含作答、答案、维度分。"""
        r = response["result"]
        cats = sorted({a["cross_subject_category"] for a in r.get("error_attributions", [])})
        s = {
            "method": method,
            "subject": request["context"]["subject"],
            "attempt_ids": attempt_ids,
            "evidence_ids": [e["evidence_id"] for e in verified.evidence],
            "semantics": verified.semantics,
            "human_review_required": verified.human_review_required,
            "error_categories": cats,
        }
        if method == "grade":
            s["verdict"] = r["verdict"]
        else:
            s["observation_count"] = len(r.get("observations", []))
        return s

    # ---------- 主流程 ----------
    async def ingest(self, method: str, body: object) -> tuple[dict, bool]:
        """返回 (摘要, 是否为重放)。"""
        request = self._validate_request(method, body)
        ctx = request["context"]
        key = ctx["idempotency_key"]
        fp = request_fingerprint(method, request)
        attempts = [request["attempt"]] if method == "grade" else request["attempts"]
        proposed_ids = [a.get("attempt_id") or str(uuid.uuid4()) for a in attempts]

        # 请求里的题目必须已登记（spec 待人确认 #8：只检查、不自动登记）
        items, _ = await store.registered(self.pool, ctx["subject"], {a["item_ref"] for a in attempts}, set())
        if {local_id(a["item_ref"]) for a in attempts} - set(items):
            raise IngestError(422, "CONTENT_NOT_REGISTERED", "item_ref is not registered on the platform")

        deadline = time.monotonic() + 2 * self.settings.lease_seconds + 5
        while True:
            c = await store.claim(
                self.pool, method=method, key=key, subject=ctx["subject"], student_id=ctx["student_ref"],
                fingerprint=fp, attempt_ids=proposed_ids, lease_seconds=self.settings.lease_seconds,
            )
            if c.kind == "conflict":
                raise IngestError(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was used with a different request body")
            if c.kind == "done":
                return c.summary or {}, True
            if c.kind == "busy":
                if time.monotonic() > deadline:
                    raise IngestError(503, "BUSY", "another request with this key is still running", retryable=True)
                await asyncio.sleep(BUSY_POLL_SECONDS)
                continue
            try:
                summary = await self._run(method, request, key, c)
                return summary, False
            except store.LeaseLost:
                continue  # 别人接管并可能已完成：回到幂等检查拿它的结果
            except IngestError as e:
                await store.mark_failed(self.pool, method=method, key=key, lease_token=c.lease_token, error_code=e.code)
                raise
            except Exception:
                await store.mark_failed(self.pool, method=method, key=key, lease_token=c.lease_token, error_code="INTERNAL")
                raise

    async def _run(self, method: str, request: dict, key: str, c: store.Claim) -> dict:
        ctx = request["context"]
        # 转发给适配器：带上平台的 attempt_id（重试时沿用，适配器派生的证据 ID 因此不变）
        forward = copy.deepcopy(request)
        targets = [forward["attempt"]] if method == "grade" else forward["attempts"]
        for a, attempt_id in zip(targets, c.attempt_ids):
            a["attempt_id"] = attempt_id
        timeout = self.settings.grade_timeout if method == "grade" else self.settings.diagnose_timeout
        response = await call_adapter(self.client, self.settings.adapters[ctx["subject"]], method, forward, timeout)

        items, nodes = collect_refs(response)
        reg_items, reg_nodes = await store.registered(self.pool, ctx["subject"], items, nodes)
        verified = verify(method, forward, response, registered_items=reg_items, registered_nodes=reg_nodes)
        base = self._summary_base(method, forward, response, verified, c.attempt_ids)
        return await store.persist(
            self.pool, method=method, key=key, lease_token=c.lease_token, request=forward, response=response,
            verified=verified, attempt_ids=c.attempt_ids, summary_base=base,
        )
