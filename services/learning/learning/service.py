"""写入流程：幂等检查 → 调适配器 → 平台核对 → 一个事务落库（spec 范围第 2–6 条）。

冲刺 1 只有这一步，幂等表里的状态（pending / succeeded / failed）就是这一步的状态；
完整的编排状态机（ADR 03）在后续冲刺。
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import time
import uuid

import asyncpg
import httpx

from . import store
from .adapter_client import call_adapter
from .checks import collect_refs, local_id, parse_time, verify
from .config import Settings
from .attributes import Attributes, derive, v0_attributes
from .contract import UnsupportedContractVersion, internal_contract, plugin_contract
from .errors import IngestError
from .fingerprint import request_fingerprint, sha256_hex
from .mastery.recompute import MasteryEngine, after_ingest

log = logging.getLogger("starbuddy.learning.mastery")

METHODS = ("grade", "diagnose")
BUSY_POLL_SECONDS = 0.1
BUSY_MAX_POLL_SECONDS = 2.0


class IngestService:
    def __init__(self, settings: Settings, pool: asyncpg.Pool, client: httpx.AsyncClient,
                 mastery: MasteryEngine | None = None) -> None:
        self.settings = settings
        self.pool = pool
        self.client = client
        self.mastery = mastery  # 掌握重算（KGE-04 第二步）；None = 不写掌握记录

    async def _recompute_mastery(self, ctx: dict, summary: dict) -> None:
        """落库事务已提交：另起事务同步重算这次作答涉及的单元（第一步待人确认 #6）。
        失败只记计数与单元 ID（不记学生与证据内容，第二步待人确认 #8），不影响已落库的证据与接口返回；之后由对账补上。"""
        if self.mastery is None:
            return
        try:
            # 时间预算（审查意见 1）：同步重算不能拖住接口；超时取消（未提交的单元事务回滚），记 failed，交给对账
            await asyncio.wait_for(
                after_ingest(self.mastery, ctx["student_ref"], ctx["subject"], list(summary.get("attempt_ids") or [])),
                self.settings.mastery_sync_budget_seconds)
        except asyncio.TimeoutError:
            self.mastery.stats["failed"] += 1
            self.mastery.stats["sync_timeout"] += 1
            log.warning(json.dumps({"event": "mastery_after_ingest_timeout", "subject": ctx["subject"]}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            self.mastery.stats["failed"] += 1
            log.warning(json.dumps({"event": "mastery_after_ingest_failed", "subject": ctx["subject"],
                                    "error_class": type(e).__name__[:48]}, ensure_ascii=False))

    # ---------- 请求校验（调适配器之前） ----------
    def _validate_request(self, method: str, body: object, version: str) -> dict:
        schema = "GradeRequest" if method == "grade" else "DiagnoseRequest"
        # 迁移期两个契约版本并存：路径决定版本（/internal/v0 → 0.9.0，/internal/v1 → 1.0.0），请求声明的版本必须一致
        declared = body.get("context", {}).get("contract_version") if isinstance(body, dict) and isinstance(body.get("context"), dict) else None
        if declared != version:
            raise IngestError(400, "INVALID_REQUEST", "context.contract_version does not match this endpoint")
        try:
            contract = plugin_contract(version)
        except UnsupportedContractVersion:
            raise IngestError(400, "INVALID_REQUEST", "contract version is not enabled on this service") from None
        errors = contract.validate(schema, body)
        if errors:
            raise IngestError(400, "INVALID_REQUEST", "request does not match the contract", detail="; ".join(errors[:10]))
        assert isinstance(body, dict)
        subject = body["context"]["subject"]
        if subject not in self.settings.adapters:
            raise IngestError(400, "SUBJECT_NOT_SUPPORTED", "no adapter is configured for this subject")
        if method == "diagnose" and (body["mode"] not in ("intake", "gap_test") or not body.get("attempts")):
            raise IngestError(422, "INVALID_REQUEST", "sprint 1 supports synchronous diagnose with attempts only")
        attempts = [body["attempt"]] if method == "grade" else body["attempts"]
        try:
            keys = [(a["item_ref"], parse_time(a["occurred_at"])) for a in attempts]
        except ValueError:
            raise IngestError(400, "INVALID_REQUEST", "attempt occurred_at is not an RFC 3339 date-time with time zone") from None
        if len(set(keys)) != len(keys):
            raise IngestError(422, "INVALID_REQUEST", "attempts must differ in item_ref or occurred_at")
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
    async def replay(self, method: str, key: str, student_id: str, usage_id: str) -> store.Replay:
        return await store.replay(self.pool, method=method, key=key, student_id=student_id, usage_id=usage_id)

    def _unwrap(self, method: str, body: object, version: str) -> tuple[object, dict | None]:
        """v1 的请求体是信封 {request, ingest}（learning-internal.v1，specs/evidence-attributes-sprint1）；v0 就是插件请求本身。"""
        if version == "0.9.0":
            return body, None
        if not isinstance(body, dict) or set(body) != {"request", "ingest"}:
            raise IngestError(400, "INVALID_REQUEST", "v1 request body must be an envelope {request, ingest}")
        errors = internal_contract("1.0.0").validate("IngestContext", body["ingest"])
        if method == "grade" and isinstance(body["ingest"], dict) and "evidence_level" in body["ingest"]:
            errors.append("/ingest/evidence_level not allowed for grade (use request.evidence_level)")
        if errors:
            raise IngestError(400, "INVALID_REQUEST", "ingest does not match the contract", detail="; ".join(errors[:10]))
        return body["request"], body["ingest"]

    @staticmethod
    def _fingerprint(method: str, request: dict, ingest: dict | None) -> tuple[str, Attributes]:
        fp = request_fingerprint(method, request)
        attrs: Attributes = v0_attributes(method, request)
        if ingest is not None:
            attrs = derive(method, request, ingest)
            # 指纹含推导后的平台上下文：同 key 换输入通道、用途或等级算冲突；语义相同的不同写法得到同一指纹（契约审查建议 7）
            fp = sha256_hex({"request": fp, "ingest": {"channel": attrs.channel, "purpose": attrs.purpose, "level": attrs.evidence_level}})
        return fp, attrs

    def envelope_fingerprint(self, method: str, body: object, version: str) -> str:
        """与 ingest 落库的请求指纹同一算法（供诊断会话比对“同一步是不是同一内容”，不调适配器、不落库）。"""
        inner, ingest = self._unwrap(method, body, version)
        assert isinstance(inner, dict)
        return self._fingerprint(method, inner, ingest)[0]

    async def ingest(self, method: str, body: object, version: str = "0.9.0") -> tuple[dict, bool]:
        """返回 (摘要, 是否为重放)。"""
        inner, ingest = self._unwrap(method, body, version)
        request = self._validate_request(method, inner, version)
        ctx = request["context"]
        key = ctx["idempotency_key"]
        fp, attrs = self._fingerprint(method, request, ingest)
        if ingest is not None:
            attrs = Attributes(attrs.channel, attrs.evidence_level, attrs.purpose,
                               await store.map_version_for(self.pool, ctx["subject"], ctx.get("content_version")))
        attempts = [request["attempt"]] if method == "grade" else request["attempts"]
        proposed_ids = [a.get("attempt_id") or str(uuid.uuid4()) for a in attempts]

        # 请求里的题目必须已登记（spec 待人确认 #8：只检查、不自动登记）
        items, _ = await store.registered(self.pool, ctx["subject"], {a["item_ref"] for a in attempts}, set())
        if {local_id(a["item_ref"]) for a in attempts} - set(items):
            raise IngestError(422, "CONTENT_NOT_REGISTERED", "item_ref is not registered on the platform")

        # 既有证据必须真在库里、属于这个学生和学科；准入状态以平台为准（契约守护阻断 1）
        prior_ids = [p["evidence_id"] for p in request.get("prior_evidence", [])]
        prior = await store.prior_evidence_admission(self.pool, ctx["student_ref"], ctx["subject"], prior_ids)
        if len(prior) != len(set(prior_ids)):
            raise IngestError(422, "INVALID_REQUEST", "prior_evidence contains ids that are not this student's recorded evidence")

        # 等同 key 的另一请求：指数退避加抖动，最多等一个租约；再等不到就返回 503 BUSY，让 BFF 稍后用同一个 key 重试
        #（安全审查 S5：每 100ms 取一次连接会占满连接池）。接管分支同样受这个期限约束（契约守护阻断 2）。
        deadline = time.monotonic() + self.settings.lease_seconds + 5
        backoff = BUSY_POLL_SECONDS
        while True:
            c = await store.claim(
                self.pool, method=method, key=key, subject=ctx["subject"], student_id=ctx["student_ref"],
                fingerprint=fp, attempt_ids=proposed_ids, lease_seconds=self.settings.lease_seconds,
                usage_id=ctx.get("usage_id"),
            )
            if c.kind == "sealed":
                # v0 契约的错误码枚举里没有 IDEMPOTENCY_KEY_SEALED：v0 路径用已有的 IDEMPOTENCY_CONFLICT（同为 409、不可重试）
                if version == "0.9.0":
                    raise IngestError(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was sealed by a replay lookup; use a new key")
                raise IngestError(409, "IDEMPOTENCY_KEY_SEALED", "idempotency_key was sealed by a replay lookup; use a new key")
            if c.kind == "conflict":
                raise IngestError(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was used with a different request body")
            if c.kind == "done":
                return c.summary or {}, True
            if time.monotonic() > deadline:
                # 包括接管分支：租约配置过短时，两个同 key 请求不会无限轮流接管（契约守护阻断 2）
                if c.kind == "takeover":
                    await store.mark_failed(self.pool, method=method, key=key, lease_token=c.lease_token, error_code="BUSY")
                raise IngestError(503, "BUSY", "another request with this key is still running", retryable=True)
            if c.kind == "busy":
                await asyncio.sleep(backoff * (0.5 + random.random()))
                backoff = min(backoff * 2, BUSY_MAX_POLL_SECONDS)
                continue
            try:
                summary = await self._run(method, request, key, c, prior, attrs, versioned=ingest is not None)
            except store.LeaseLost:
                continue  # 别人接管并可能已完成：回到幂等检查拿它的结果
            except IngestError as e:
                await store.mark_failed(self.pool, method=method, key=key, lease_token=c.lease_token, error_code=e.code)
                raise
            except Exception:
                await store.mark_failed(self.pool, method=method, key=key, lease_token=c.lease_token, error_code="INTERNAL")
                raise
            # 只有这次真正落库才重算；幂等重放（上面 done 分支）不触发，漏掉的由对账补
            await self._recompute_mastery(ctx, summary)
            return summary, False

    async def _run(self, method: str, request: dict, key: str, c: store.Claim, prior: dict[str, str], attrs: Attributes,
                   *, versioned: bool) -> dict:
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
        verified = verify(method, forward, response, registered_items=reg_items, registered_nodes=reg_nodes,
                          prior_admission=prior)
        # 键盘作答不会有识别转写：出现了说明通道或结果不实（安全审查建议 3）
        if attrs.channel == "keypad" and any(e["kind"] in ("ocr_transcription", "asr_transcription") for e in verified.evidence):
            raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter result rejected by platform checks",
                              detail="recognition evidence on keypad channel")
        base = self._summary_base(method, forward, response, verified, c.attempt_ids)
        if versioned:  # v0 摘要没有这两个字段（learning-internal.v0 不改）
            base.update(evidence_level=attrs.evidence_level, learner_model_updated=attrs.learner_model_updated)
        if not attrs.learner_model_updated:
            # 没有落库的错因与观察不出现在摘要里：否则就是没有依据的关于学生的陈述（规则 11；契约审查建议 4）
            base["error_categories"] = []
            if method == "diagnose":
                base["observation_count"] = 0
        return await store.persist(
            self.pool, method=method, key=key, lease_token=c.lease_token, request=forward, response=response,
            verified=verified, attempt_ids=c.attempt_ids, summary_base=base, attrs=attrs,
        )
