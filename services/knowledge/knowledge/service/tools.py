"""知识服务工具的唯一实现层：HTTP（http.py）与 MCP（mcp_server.py）都只调 ToolCore.call，校验与鉴权一处实现。

处理顺序：调用方准入（令牌决定 caller，按工具限定）→ 个人层工具回 501 → 契约版本 → 请求体 caller 与令牌一致 →
按契约严格校验请求（additionalProperties、枚举、长度）→ 执行 → 组统一信封 → 按契约校验响应，不合格回 500（不把
不合契约的数据交出去）。
日志只记 request_id（UUID 形状才记）、caller、工具名、状态码、错误码、耗时；不记请求体（node.resolve 的 text 可能来自学生）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

from ..nodeid import SUBJECT_PREFIX
from .contract import CONTRACT_VERSION, PERSONAL_TOOLS, Contract
from .errors import ServiceError, forbidden, internal, invalid, not_found
from .pages import PageSnapshot, render_page
from .reader import KgeReader, MapVersionRow, jsonb, node_record, projection_record, region_record, subject_of
from ..retrieval.resolver import CombinedResolver
from ..retrieval.tokenize import TOKENIZER_VERSION
from .resolve import LOW_CONFIDENCE, RESOLVER_VERSION, Resolver

log = logging.getLogger("starbuddy.knowledge")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
ALGO_VERSION = "0.1.0"
PUBLIC_CALLERS = frozenset({"bff", "orchestrator", "learning"})
# 契约允许的调用方：node.resolve 只给编排层（ResolveContext）；个人层没有 bff（StudentContext）
ALLOWED_CALLERS: dict[str, frozenset[str]] = {
    "node.resolve": frozenset({"orchestrator"}),
    "node.get": PUBLIC_CALLERS, "node.path": PUBLIC_CALLERS, "page.get": PUBLIC_CALLERS, "item.search": PUBLIC_CALLERS,
    "item.similar": PUBLIC_CALLERS, "rubric.get": PUBLIC_CALLERS,
    "evidence.write": frozenset({"orchestrator", "learning"}), "mastery.get": frozenset({"orchestrator", "learning"}),
    "mastery.explain": frozenset({"orchestrator", "learning"}),
}
PARTIAL_CREDIT = ("all_or_nothing", "proportional", "stepwise")
MAX_PROJECTIONS = 16


def graph_source(subject: str, v: MapVersionRow) -> str:
    return f"graph:{SUBJECT_PREFIX[subject].lower()}-map@{v.version}"


def tokenizer_source(version: str = TOKENIZER_VERSION) -> str:
    """node.resolve 的全文检索依赖分词器与停用词表：版本进 source_version（契约 SourceVersion 的 algo 类别，版本段只收
    [A-Za-z0-9._-]，所以把其他字符换成点）"""
    return "algo:kge-tokenize@" + re.sub(r"[^A-Za-z0-9._\-]", ".", version)[:32]


class ToolCore:
    def __init__(self, reader: KgeReader, contract: Contract, pages: PageSnapshot | None = None,
                 resolver: Resolver | None = None) -> None:
        self.reader = reader
        self.contract = contract
        self.pages = pages
        self.resolver = resolver or CombinedResolver()  # PR ③：规则 + 全文（jieba + BM25），经 Resolver 接口
        self.handlers: dict[str, Callable[[dict], Awaitable[tuple[dict, dict]]]] = {
            "node.resolve": self.node_resolve, "node.get": self.node_get, "node.path": self.node_path, "page.get": self.page_get,
            "item.search": self.item_search, "item.similar": self.item_similar, "rubric.get": self.rubric_get,
        }

    async def call(self, tool: str, body: Any, caller: str | None) -> tuple[int, dict]:
        """返回（HTTP 状态码，响应体）。caller 由调用方令牌决定（None = 未通过鉴权，调用方先回 401）"""
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
        if tool in PERSONAL_TOOLS:
            # 个人层由学习服务 / KGE-04 实现（ADR 01）；本服务不持有任何个人层表权限
            raise ServiceError(501, "METHOD_NOT_IMPLEMENTED", "personal-layer tool is not served by the knowledge service")
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
        result, meta = await self.handlers[tool](body)
        envelope = {"contract_version": CONTRACT_VERSION, "result": result, "evidence_ids": [],
                    "confidence": meta.get("confidence", 1), "source_version": meta["source_version"],
                    "model_version": meta.get("model_version", f"algo:kge-{tool.replace('.', '-')}@{ALGO_VERSION}"),
                    "human_review_required": bool(meta.get("review_reasons"))}
        if meta.get("review_reasons"):
            envelope["review_reasons"] = list(meta["review_reasons"])
        bad = self.contract.validate(spec.response_schema, envelope)
        if bad:
            log.error(json.dumps({"tool": tool, "response_contract_errors": bad[:20]}, ensure_ascii=False))
            raise internal()
        return envelope

    # ---------- 公共 ----------
    async def _version(self, subject: str, ctx: dict) -> MapVersionRow:
        v = await self.reader.published_version(subject)
        if v is None or ("map_version" in ctx and ctx["map_version"] != v.version):
            raise not_found()
        return v

    async def _node_version(self, node_id: str, ctx: dict) -> tuple[str, MapVersionRow]:
        subject = subject_of(node_id)
        if subject is None:
            raise not_found()
        return subject, await self._version(subject, ctx)

    @staticmethod
    def _item_ref(item_ref: str) -> tuple[str, str]:
        subject, _, item_id = item_ref.partition(":")
        return subject, item_id

    @staticmethod
    def _item_view(r: Any) -> dict:
        view: dict[str, Any] = {"item_ref": f"{r['subject']}:{r['item_id']}", "main_node_id": r["main_node_id"],
                                "license_state": r["license_state"]}
        if r["node_ids"]:
            view["node_ids"] = list(r["node_ids"])
        if r["cognitive_level"] is not None:
            view["cognitive_level"] = r["cognitive_level"]
        if r["prior_difficulty"] is not None:
            view["prior_difficulty"] = float(r["prior_difficulty"])
        if r["family_code"] is not None:
            view["family_code"] = r["family_code"]
        return view

    # ---------- 工具 ----------
    async def node_resolve(self, body: dict) -> tuple[dict, dict]:
        subject = body["subject"]
        v = await self._version(subject, body["context"])
        nodes = [dict(r) for r in await self.reader.nodes(v.id)]
        cands = self.resolver.resolve(body["text"], nodes, int(body.get("max_candidates", 5)))
        out = []
        for c in cands:
            item: dict[str, Any] = {"node_id": c.node_id, "level": c.level, "confidence": c.confidence}
            if c.kind:
                item["kind"] = c.kind
            out.append(item)
        top = cands[0].confidence if cands else 0.0
        meta = {"source_version": f"{graph_source(subject, v)};{tokenizer_source()}", "confidence": top,
                "model_version": getattr(self.resolver, "version", RESOLVER_VERSION)}
        if top < LOW_CONFIDENCE:
            meta["review_reasons"] = ["low_confidence"]
        return {"map_version": v.version, "candidates": out}, meta

    async def node_get(self, body: dict) -> tuple[dict, dict]:
        node_id = body["node_id"]
        subject, v = await self._node_version(node_id, body["context"])
        n = await self.reader.node(v.id, node_id)
        if n is None:
            raise not_found()
        view: dict[str, Any] = {"node_id": n["id"], "map_version": v.version, "level": n["level"], "name": n["name"],
                                "scope": n["scope"], "cognitive_levels": list(n["cognitive_levels"] or []),
                                "includes": list(n["includes"] or []), "excludes": list(n["excludes"] or [])}
        if n["kind"]:
            view["kind"] = n["kind"]
        if n["error_type"]:
            view["error_type"] = n["error_type"]
        tps = await self.reader.textbook_projections(v.id, node_id, body.get("edition"))
        rps = await self.reader.region_projections(v.id, node_id, body.get("region"))
        projections = [{"edition": r["edition"], "edition_year": int(r["edition_year"]), "volume": r["volume"],
                        "chapter": r["chapter"], "page_from": int(r["page_from"]), "page_to": int(r["page_to"]),
                        "quote_text": r["quote_text"], "quote_hash": r["quote_hash"]} for r in tps[:MAX_PROJECTIONS]]
        weights = []
        for r in rps[:MAX_PROJECTIONS]:
            w: dict[str, Any] = {"region": r["region"], "exam_year": int(r["exam_year"]), "weight": float(r["weight"])}
            if r["typical_slots"]:
                w["typical_slots"] = list(r["typical_slots"])
            weights.append(w)
        return {"node": view, "textbook_projections": projections, "region_weights": weights}, \
            {"source_version": graph_source(subject, v)}

    async def node_path(self, body: dict) -> tuple[dict, dict]:
        root, direction = body["node_id"], body["direction"]
        subject, v = await self._node_version(root, body["context"])
        visible = {r["id"] for r in await self.reader.nodes(v.id)}
        if root not in visible:
            raise not_found()
        steps: list[dict] = []
        seen_edges: set[tuple[str, str]] = set()
        reached = {root}
        frontier = [root]
        for depth in range(1, int(body.get("max_depth", 3)) + 1):
            if not frontier or len(steps) >= 500:
                break
            nxt: list[str] = []
            for f, t in await self.reader.prerequisite_step(v.id, frontier, direction):
                far = f if direction == "up" else t
                if (f, t) in seen_edges or far not in visible or len(steps) >= 500:
                    continue
                seen_edges.add((f, t))
                steps.append({"from_id": f, "to_id": t, "depth": depth})
                if far not in reached:
                    reached.add(far)
                    nxt.append(far)
            frontier = sorted(nxt)
        return {"root": root, "direction": direction, "steps": steps}, {"source_version": graph_source(subject, v)}

    async def page_get(self, body: dict) -> tuple[dict, dict]:
        if self.pages is None:
            raise ServiceError(501, "METHOD_NOT_IMPLEMENTED", "page snapshot is not configured")
        pp = self.pages.get(body["page_id"])
        if pp is None:  # 只有快照 manifest 列出的页面算已发布
            raise not_found()
        subject, v = await self._node_version(pp.node_id, body["context"])
        if pp.map_version != v.version:
            # 页面按旧地图版本编译：升版到重编合并之间暂不服务（继续服务旧页需让 source_version 反映编译版本，属契约变更，
            # 见 lessons）。与“对象不存在”同一个 404（title = code，不带 detail）
            raise not_found()
        n = await self.reader.node(v.id, pp.node_id)
        if n is None:  # 节点已撤下或不在已发布版本里：页面不再服务
            raise not_found()
        edition, region = body.get("edition"), body.get("region")
        projections = [projection_record(r) for r in await self.reader.textbook_projections(v.id, pp.node_id, edition)]
        regions = [region_record(r) for r in await self.reader.region_projections(v.id, pp.node_id, region)]
        body_md, refs = render_page(pp, subject=subject, map_version=v.version, standard_year=v.standard_year,
                                    node=node_record(n), projections=projections, regions=regions, edition=edition)
        result = {"page_id": pp.page_id, "page_version": pp.page_version, "body_md": body_md, "atom_refs": refs}
        return result, {"source_version": f"{graph_source(subject, v)};graph:kge-pages@{self.pages.short_commit}"}

    async def item_search(self, body: dict) -> tuple[dict, dict]:
        subject = body["subject"]
        node_id = body.get("node_id")
        if node_id is not None and subject_of(node_id) != subject:
            raise ServiceError(400, "SUBJECT_MISMATCH", "node_id does not belong to subject")
        dmin, dmax = body.get("difficulty_min"), body.get("difficulty_max")
        if dmin is not None and dmax is not None and dmin > dmax:
            raise invalid("difficulty_min is greater than difficulty_max")
        v = await self._version(subject, body["context"])
        rows = await self.reader.item_search(v.id, subject, node_id, body.get("cognitive_level"), dmin, dmax,
                                             body.get("family_code"), int(body.get("limit", 20)))
        return {"items": [self._item_view(r) for r in rows]}, {"source_version": graph_source(subject, v)}

    async def item_similar(self, body: dict) -> tuple[dict, dict]:
        subject, item_id = self._item_ref(body["item_ref"])
        v = await self._version(subject, body["context"])
        me = await self.reader.item(v.id, subject, item_id)
        if me is None:
            raise not_found("ITEM_NOT_FOUND")
        rows = await self.reader.item_similar(v.id, subject, item_id, me["family_id"], me["main_node_id"], int(body.get("limit", 5)))
        items = [{"item": self._item_view(r), "relation": "same_family" if r["same_family"] else "similar"} for r in rows]
        return {"items": items}, {"source_version": graph_source(subject, v)}

    async def rubric_get(self, body: dict) -> tuple[dict, dict]:
        subject, item_id = self._item_ref(body["item_ref"])
        v = await self._version(subject, body["context"])
        rows = await self.reader.scoring_points(v.id, subject, item_id, body["rubric_version"])
        if not rows:
            raise not_found("ITEM_NOT_FOUND")
        points = []
        for r in rows:
            p: dict[str, Any] = {"point_id": r["point_id"], "score": float(r["score"]), "node_ids": list(r["node_ids"]),
                                 "partial_credit": _partial_credit(jsonb(r["partial_credit_rule"]))}
            if r["subject_dimension"] is not None:
                p["subject_dimension"] = r["subject_dimension"]
            words = _keywords(jsonb(r["keyword_family"]))
            if words:
                p["keyword_family"] = words
            points.append(p)
        result = {"item_ref": body["item_ref"], "rubric_version": body["rubric_version"], "map_version": v.version, "points": points}
        return result, {"source_version": f"rubric:{body['rubric_version']};{graph_source(subject, v)}"}


class LossyMapping(ValueError):
    """库里的写法映射到契约会丢信息：fail closed（回 500，日志只记异常类名，不回显内容；lessons 待内容侧确认写法）"""


def _partial_credit(rule: Any) -> str:
    """kge.scoring_point.partial_credit_rule（jsonb）→ 契约枚举：空对象 = all_or_nothing；恰好 {"mode": <枚举值>}；
    有 mode 以外的键或其他写法一律报错（回 500），不静默丢掉规则里的其他条件"""
    if rule in ({}, None):
        return "all_or_nothing"
    if isinstance(rule, dict) and set(rule) == {"mode"} and rule["mode"] in PARTIAL_CREDIT:
        return str(rule["mode"])
    raise LossyMapping("partial_credit_rule")


def _keywords(value: Any) -> list[str]:
    """kge.scoring_point.keyword_family（jsonb）→ 字符串数组：只收字符串数组（去重保序）；空值 = 无。
    对象形式（按类别分组的关键词）映射到契约的扁平数组会丢掉分组，fail closed（回 500）"""
    if value in (None, [], {}):
        return []
    if not isinstance(value, list) or not all(isinstance(w, str) for w in value):
        raise LossyMapping("keyword_family")
    seen: dict[str, None] = {}
    for w in value:
        seen.setdefault(w, None)
    return list(seen)
