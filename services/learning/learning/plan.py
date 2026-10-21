"""今日计划（冲刺 4-H 统一壳，specs/unified-shell-sprint4/spec.md 第 6 节；契约 learning-internal.v1 `POST /internal/v1/plan`，待签字）。

非消耗类：不计量、不写任何表、计划不落库。对每一科：
  1. 读该生当前的掌握快照（platform.mastery_estimate 的当前行：节点键行经 skill_link 读时汇总为能力视图，旧能力点键行并读；
     next_retest_at 来自 KGE-04 的复测调度，规则 2）；
  2. 组装插件 API v1.0 的 PlanRequest，调该科适配器 /plan，按契约校验结果；
  3. 能力点经已发布地图版本的 kge.skill_link 换算成知识节点 ID（BFF 读不到 skill_link）；
  4. 只回平台摘要：任务类型、目标节点、优先级、预计分钟、rationale_code——不回 p_mastery、confidence、evidence_ids、learner_context。
某一科没配适配器、适配器 501 / 超时 / 结果不合契约：这一科 unavailable，不影响其他科。
日志只记学科数、各科状态与耗时，不记学生 ID 与任务内容。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
import httpx

from .adapter_client import call_adapter
from .config import Settings
from .contract import UnsupportedContractVersion, internal_contract, plugin_contract
from .errors import IngestError
from .mastery.store import skill_view

PLAN_CONTRACT = "1.0.0"
MAX_TASKS = 50
MAX_NODES_PER_TASK = 16


def validate_query(body: object) -> dict:
    """请求按契约严格校验（PlanQuery）；不合格 400，错误只报位置与关键字。"""
    errors = internal_contract("1.0.0").validate("PlanQuery", body)
    if errors:
        raise IngestError(400, "INVALID_REQUEST", "request does not match PlanQuery", detail="; ".join(errors[:5]))
    assert isinstance(body, dict)
    return body


async def _mastery(pool: asyncpg.Pool, student: str, subject: str) -> list[dict]:
    """掌握快照（插件 API 形状不变：仍按 skill_node 给，KGE-04 第二步待人确认 #4）：
    - 节点键的当前记录经当前已发布地图的 kge.skill_link 读时汇总成能力视图（mastery/store.py aggregate_skill_view；不写库、不点亮）；
    - 旧的 skill_local_id 行（只剩 3-H 合成种子，第二步待人确认 #10）继续读；
    - 同一能力点两种来源都有时以节点汇总为准（节点键行是唯一真相源，规则 2）。"""
    async with pool.acquire() as conn:
        legacy = await conn.fetch(
            """SELECT skill_local_id::text AS local_id, state::text AS state, p_mastery, next_retest_at
                 FROM platform.mastery_estimate
                WHERE student_id = $1::uuid AND subject = $2::platform.subject AND is_current
                  AND skill_local_id IS NOT NULL
                ORDER BY skill_local_id""", student, subject)
        view = await skill_view(conn, student, subject)
    merged: dict[str, dict[str, Any]] = {}
    for r in legacy:
        v: dict[str, Any] = {"state": r["state"]}
        if r["p_mastery"] is not None:
            v["p_mastery"] = float(r["p_mastery"])
        if r["next_retest_at"] is not None:
            v["next_retest_at"] = r["next_retest_at"]
        merged[r["local_id"]] = v
    merged.update(view)
    out = []
    for local_id in sorted(merged):
        v = merged[local_id]
        m: dict[str, Any] = {"skill_node": f"{subject}:{local_id}", "state": v["state"]}
        if v.get("p_mastery") is not None:
            m["p_mastery"] = float(v["p_mastery"])
        if v.get("next_retest_at") is not None:
            m["next_retest_at"] = v["next_retest_at"].isoformat()
        out.append(m)
    return out


async def _node_map(pool: asyncpg.Pool, subject: str, local_ids: list[str]) -> dict[str, list[str]]:
    """能力点 → 已发布地图版本里的有效知识节点（按权重从高到低）。没有已发布版本或没有对应时为空。"""
    if not local_ids:
        return {}
    rows = await pool.fetch(
        """SELECT l.local_id::text AS local_id, l.node_id::text AS node_id
             FROM kge.skill_link l
             JOIN kge.map_version v ON v.id = l.map_version_id
             JOIN kge.knowledge_node n ON n.map_version_id = l.map_version_id AND n.id = l.node_id
            WHERE v.status = 'published' AND v.subject = $1::platform.subject AND l.subject = $1::platform.subject
              AND n.status = 'active' AND l.local_id::text = ANY($2::text[])
            ORDER BY l.local_id, l.weight DESC, l.node_id""", subject, sorted(set(local_ids)))
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["local_id"], []).append(r["node_id"])
    return out


def _local(subject: str, skill_node: str) -> str | None:
    prefix = f"{subject}:"
    return skill_node[len(prefix):] if skill_node.startswith(prefix) else None


async def _one_subject(settings: Settings, pool: asyncpg.Pool, client: httpx.AsyncClient, student: str, subject: str,
                       budget: int | None) -> dict:
    unavailable = {"subject": subject, "status": "unavailable", "tasks": []}
    target = settings.adapters.get(subject)
    if target is None:
        return unavailable
    # 插件 plan 只按 v1.0 实现（specs/adapter-aya-methods-sprint4；v1.0 已签字）；/internal/v1/plan 属于 learning-internal v1
    #（2026-10-20 签字，LT-04），不再另设开关
    try:
        contract = plugin_contract(PLAN_CONTRACT)
    except UnsupportedContractVersion:
        return unavailable
    request: dict[str, Any] = {
        "context": {"request_id": str(uuid.uuid4()), "contract_version": PLAN_CONTRACT, "subject": subject,
                    "student_ref": student, "data_class": "L2", "idempotency_key": str(uuid.uuid4())},
        "mastery": await _mastery(pool, student, subject),
    }
    if budget is not None:
        request["constraints"] = {"time_budget_minutes": budget}
    if contract.validate("PlanRequest", request):
        return unavailable  # 平台自己组装的请求不合契约：宁可这一科不排，也不把不合格的请求发给适配器
    try:
        data = await call_adapter(client, target, "plan", request, settings.plan_timeout)
    except IngestError:
        return unavailable
    if data.get("contract_version") != PLAN_CONTRACT or contract.validate("PlanResponse", data):
        return unavailable
    tasks = data["result"]["tasks"]
    # 平台摘要的预计分钟上限是 600（契约 PlanDigestTask）：超出说明适配器结果不可信，整科按不可用处理，不截断成看似合理的值
    if any(int(t["estimated_minutes"]) > 600 for t in tasks):
        return unavailable
    locals_ = [l for t in tasks for l in (_local(subject, s) for s in t["skill_nodes"]) if l]
    nodes = await _node_map(pool, subject, locals_)
    out = []
    for t in tasks:
        ids: list[str] = []
        for s in t["skill_nodes"]:
            for n in nodes.get(_local(subject, s) or "", []):
                if n not in ids:
                    ids.append(n)
        # 摘要只取这几个字段：显式挑出来，不从适配器的任务对象里整体拷贝
        out.append({"kind": t["kind"], "target_node_ids": ids[:MAX_NODES_PER_TASK], "priority": t["priority"],
                    "estimated_minutes": int(t["estimated_minutes"]), "rationale_code": t["rationale_code"]})
    out.sort(key=lambda x: -x["priority"])  # 稳定排序：同优先级保持适配器给的顺序
    return {"subject": subject, "status": "ok", "tasks": out[:MAX_TASKS]}


async def plan_today(settings: Settings, pool: asyncpg.Pool, client: httpx.AsyncClient, body: object) -> tuple[dict, dict]:
    """返回（平台摘要, 日志字段）。各科并行；摘要在返回前按契约校验（不合格 500，不外露）。"""
    q = validate_query(body)
    student = q["student_ref"]
    subjects: list[str] = q["subjects"]
    results = await asyncio.gather(*(_one_subject(settings, pool, client, student, s, q.get("time_budget_minutes"))
                                     for s in subjects))
    digest = {"subjects": list(results)}
    if internal_contract("1.0.0").validate("PlanDigest", digest):
        raise IngestError(500, "INTERNAL", "plan digest failed validation", retryable=True)
    return digest, {"subjects": len(subjects), "subject_status": ",".join(r["status"] for r in results)}
