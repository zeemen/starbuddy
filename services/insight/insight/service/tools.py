"""洞察服务工具的唯一实现层（HTTP 只调 ToolCore.call）。

处理顺序：调用方准入（令牌决定 caller，按工具限定为契约允许的调用方）→ 未实现的五个工具回 501 → 契约版本 →
请求体 caller 与令牌一致 → 按契约严格校验请求 → insight.record 回 501（见下）→ 执行 → 组统一信封 →
按契约校验响应，不合格回 500（不把不合契约的数据交出去）。

- insight.claims、insight.context 只给编排层（受众 agent）；insight.explain 给 BFF（学生本人 / 家长）与编排层。
- 前端受众（student / guardian）只看到可见范围含该受众的非终态、未到期、模板已审核的判断；看不到的判断与“不存在”
  同一个 404（不暴露存在性）；中圈指标（metric）对前端一律不存在。
- caller = bff 时按 actor 与 student_ref 再核对一次授权（契约“调用方、受众与代表谁”）。洞察服务没有读绑定关系的权限
  （0010 / 0028 都没有授），核对由注入的 ActorVerifier 完成；缺省实现一律不通过（fail closed，按 404 处理），
  需要人确认授权函数的迁移草稿（lessons 待人确认）。
- insight.record：契约校验后回 501——洞察服务没有可用的写入路径（0022 / 0025 的写入函数只授 BFF，且只认学生本人会话），
  不新增来源（spec 设计要点 1；lessons）。
- 日志只记 request_id（UUID 形状才记）、caller、工具名、状态码、错误码、耗时；不记请求体里的 student_ref、actor、value。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

import yaml

from ..claims import NODE_ID
from ..metrics import METRIC_DEFS
from ..policy import Policy
from ..render import render_statement
from ..shared import ROOT, Forbidden
from ..store import VisibleClaim
from .contract import CONTRACT_VERSION, NOT_IMPLEMENTED, Contract
from .errors import ServiceError, forbidden, internal, invalid, not_found, not_implemented

log = logging.getLogger("starbuddy.insight")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
ALGO_VERSION = "0.1.0"
ALLOWED_CALLERS: dict[str, frozenset[str]] = {
    "insight.context": frozenset({"orchestrator"}), "insight.claims": frozenset({"orchestrator"}),
    "insight.explain": frozenset({"bff", "orchestrator"}), "insight.record": frozenset({"bff", "orchestrator"}),
    "insight.dispute": frozenset({"bff", "orchestrator"}), "insight.predict": frozenset({"orchestrator"}),
    "insight.reconcile": frozenset({"orchestrator"}), "insight.export": frozenset({"bff"}), "insight.erase": frozenset({"bff"}),
}
FIELD_DICTIONARY = ROOT / "docs" / "design" / "sie" / "field-dictionary.v0.9.yaml"
NODE_FIELD = "inner.g3.node_mastery"
VERSIONED_ALGO = re.compile(r"^algo:([a-z0-9][a-z0-9\-]{0,47})@([A-Za-z0-9][A-Za-z0-9._\-]{0,31})$")
EVIDENCE_SOURCE = {"objective": "objective", "model_inference": "inferred"}  # platform.evidence_source_type → 契约 SourceType
MAX_CONTEXT_EVIDENCE = 50


class ClaimReader(Protocol):
    async def visible_claims(self, student: str, audience: str, *, subject: str | None = None, target_type: str | None = None,
                             statuses: Any = None, limit: int = 200, claim_id: str | None = None) -> list[VisibleClaim]: ...

    async def evidence(self, student: str, ids: Any) -> list[Mapping[str, Any]]: ...


class ActorVerifier(Protocol):
    async def verify(self, actor: Mapping[str, Any], audience: str, student: str) -> bool: ...


class UnavailableVerifier:
    """缺省：洞察服务没有核对绑定关系的数据库权限，一律不通过（BFF 代表学生 / 家长的请求按 404 处理）"""

    async def verify(self, actor: Mapping[str, Any], audience: str, student: str) -> bool:  # noqa: ARG002
        return False


def load_field_dictionary(path: Path = FIELD_DICTIONARY) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {f["id"]: {"source_type": tuple(f.get("source_type") or ()), "visibility": tuple(f.get("visibility") or ())}
            for f in doc["fields"]}


class ToolCore:
    def __init__(self, store: ClaimReader, contract: Contract, policy: Policy, forbidden: Forbidden,
                 verifier: ActorVerifier | None = None, fields: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.store = store
        self.contract = contract
        self.policy = policy
        self.forbidden = forbidden
        self.verifier = verifier or UnavailableVerifier()
        self.fields = fields if fields is not None else load_field_dictionary()
        self.handlers: dict[str, Callable[[dict, str], Awaitable[tuple[dict, dict]]]] = {
            "insight.claims": self.claims, "insight.context": self.context, "insight.explain": self.explain,
        }

    async def call(self, tool: str, body: Any, caller: str | None) -> tuple[int, dict]:
        started = time.monotonic()
        rid = None
        record: dict[str, Any] = {"tool": tool, "caller": caller}
        try:
            if isinstance(body, dict) and isinstance(body.get("context"), dict):
                cand = body["context"].get("request_id")
                rid = cand if isinstance(cand, str) and UUID_RE.fullmatch(cand) else None
            out = await self._call(tool, body, caller)
            record["status"] = 200
            return 200, out
        except ServiceError as e:
            record.update(status=e.status, code=e.code)
            return e.status, e.problem(rid)
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return 500, internal().problem(rid)
        finally:
            if rid:
                record["request_id"] = rid
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    async def _call(self, tool: str, body: Any, caller: str | None) -> dict:
        spec = self.contract.tools.get(tool)
        if spec is None:
            raise not_found()
        if caller not in ALLOWED_CALLERS[tool]:
            raise forbidden("caller is not allowed to use this tool")
        if tool in NOT_IMPLEMENTED:
            raise not_implemented("tool is not implemented in this version")
        if not isinstance(body, dict) or not isinstance(body.get("context"), dict):
            raise invalid("request body must be an object with context")
        ctx = body["context"]
        if "contract_version" in ctx and ctx["contract_version"] != CONTRACT_VERSION:
            raise ServiceError(400, "CONTRACT_VERSION_UNSUPPORTED", "contract version is not supported")
        if ctx.get("caller") != caller:
            raise forbidden("context.caller does not match the service token")
        errors = self.contract.validate(spec.request_schema, body)
        if errors:
            raise invalid("request does not conform to the contract", "; ".join(errors)[:500])
        if tool == "insight.record":
            raise not_implemented("no write path for this event in the insight service yet")
        result, meta = await self.handlers[tool](body, caller)
        envelope: dict[str, Any] = {"contract_version": CONTRACT_VERSION, "result": result,
                                    "evidence_ids": list(meta.get("evidence_ids", [])), "confidence": meta.get("confidence", 1.0),
                                    "source_version": meta["source_version"], "model_version": meta["model_version"],
                                    "human_review_required": False}
        bad = self.contract.validate(spec.response_schema, envelope)
        if bad:
            log.error(json.dumps({"tool": tool, "response_contract_errors": bad[:20]}, ensure_ascii=False))
            raise internal()
        return envelope

    # ---------- 公共 ----------
    def _source(self, tool: str) -> str:
        return f"algo:sie-{tool}@{ALGO_VERSION};algo:sie-claims-policy@{self.policy.config_version}"

    @staticmethod
    def _union(lists: Any) -> list[str]:
        seen: dict[str, None] = {}
        for ids in lists:
            for e in ids:
                seen.setdefault(e, None)
        return sorted(seen)

    def _field_ok(self, field_id: str, source_type: str, audience: str) -> bool:
        f = self.fields.get(field_id)
        return f is not None and source_type in f["source_type"] and audience in f["visibility"]

    # ---------- insight.claims ----------
    async def claims(self, body: dict, caller: str) -> tuple[dict, dict]:  # noqa: ARG002
        ctx = body["context"]
        rows = await self.store.visible_claims(ctx["student_ref"], "agent", subject=body.get("subject"),
                                               target_type=body.get("target_type"), statuses=body.get("statuses"),
                                               limit=int(body.get("limit", 50)))
        out = []
        for v in rows:
            c = v.row
            s: dict[str, Any] = {"claim_id": c.id, "target_type": c.target_type, "target_id": c.target_id,
                                 "template_key": c.template_key, "template_version": c.template_version, "status": c.status,
                                 "confidence": c.confidence, "evidence_ids": list(c.evidence_ids)[:200],
                                 "expires_at": c.expires_at.isoformat(), "source_type": c.source_type}
            if c.subject is not None:
                s["subject"] = c.subject
            if c.model_version is not None:
                s["model_version"] = c.model_version
            out.append(s)
        meta = {"evidence_ids": self._union(s["evidence_ids"] for s in out),
                "confidence": min((s["confidence"] for s in out), default=1.0),
                "source_version": self._source("claims"), "model_version": f"algo:sie-claims@{ALGO_VERSION}"}
        return {"claims": out}, meta

    # ---------- insight.context ----------
    async def context(self, body: dict, caller: str) -> tuple[dict, dict]:  # noqa: ARG002
        ctx = body["context"]
        budget = body.get("token_budget")
        cap = 200 if budget is None else max(1, min(200, int(budget) // 25))
        rows = await self.store.visible_claims(ctx["student_ref"], "agent", subject=body.get("subject"), limit=200)
        entries: list[dict[str, Any]] = []
        for v in rows:
            for e in self._entries(v):
                if self._field_ok(e["field_id"], e["source_type"], "agent"):
                    entries.append(e)
        entries = entries[:cap]
        meta = {"evidence_ids": self._union(e.get("evidence_ids", []) for e in entries),
                "confidence": min((c.row.confidence for c in rows if any(e.get("claim_id") == c.row.id for e in entries)),
                                  default=1.0),
                "source_version": self._source("context"), "model_version": f"algo:sie-context@{ALGO_VERSION}"}
        return {"task": body["task"], "entries": entries, "statements": []}, meta

    def _entries(self, v: VisibleClaim) -> list[dict[str, Any]]:
        c = v.row
        cat = self.policy.category_of(c.template_key)
        ev = list(c.evidence_ids)[-MAX_CONTEXT_EVIDENCE:]
        base = {"source_type": c.source_type, "claim_id": c.id, "evidence_ids": ev}
        if cat in ("node_mastery", "node_transfer") and c.target_type == "node" and isinstance(c.value.get("gate"), str):
            return [{"field_id": NODE_FIELD, "value": c.value["gate"], **base}]
        if cat == "metric" and c.target_type == "metric":
            key = c.target_id.split(".", 1)[0]
            d = METRIC_DEFS.get(key)
            val = c.value.get("v")
            if d is None or not isinstance(val, (int, float)) or isinstance(val, bool):
                return []
            out = [{"field_id": d[0], "value": val, **base}]
            if key == "self_assessment_calibration" and isinstance(c.value.get("dir"), str):
                out.append({"field_id": d[0], "value": c.value["dir"], **base})
            return out
        return []

    # ---------- insight.explain ----------
    async def explain(self, body: dict, caller: str) -> tuple[dict, dict]:
        ctx = body["context"]
        audience, student = ctx["audience"], ctx["student_ref"]
        if caller == "bff" and not await self.verifier.verify(ctx.get("actor") or {}, audience, student):
            raise not_found()  # 无权访问与不存在同一个 404
        rows = await self.store.visible_claims(student, audience, claim_id=body["claim_id"], limit=1)
        if not rows:
            raise not_found()
        v = rows[0]
        c = v.row
        band = self.policy.confidence_band(c.confidence)
        statement = render_statement(v.for_render(), band, self.forbidden)
        if statement is None:
            raise not_found()  # 没有可渲染的结论（agent 专用模板或占位变量缺失）：不输出任何账本外的陈述
        chain = []
        for r in await self.store.evidence(student, list(c.evidence_ids)):
            item: dict[str, Any] = {"evidence_id": str(r["evidence_id"]), "occurred_at": r["occurred_at"].isoformat(),
                                    "source_type": EVIDENCE_SOURCE.get(r["source_type"], "objective")}
            if r["evidence_level"] in ("A", "B"):
                item["evidence_level"] = r["evidence_level"]
            if c.target_type == "node" and NODE_ID.fullmatch(c.target_id):
                item["node_id"] = c.target_id
            chain.append(item)
        result = {"statement": statement, "evidence_chain": chain, "confidence_band": band, "expires_at": c.expires_at.isoformat()}
        source = self._source("explain")
        m = VERSIONED_ALGO.fullmatch(c.model_version or "")
        if m:
            source += f";algo:{m.group(1)}@{m.group(2)}"  # 依据推断行的算法版本
        meta = {"evidence_ids": list(dict.fromkeys(c.evidence_ids)), "confidence": c.confidence, "source_version": source,
                "model_version": c.model_version or f"algo:sie-explain@{ALGO_VERSION}"}
        return result, meta
