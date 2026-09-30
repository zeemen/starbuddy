"""测试共用工具：合成掌握行、合成策略、调用方凭据与上下文、进程内应用（lifespan + httpx ASGITransport）。全部合成数据。"""
from __future__ import annotations

import copy
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Mapping

import httpx

from insight.claims import MasteryRow, TemplateInfo
from insight.policy import CONFIG_DIR, Policy, parse_policy

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
STUDENT = "c1000000-0000-4000-8000-0000000000c1"
NODE_A, NODE_B, NODE_C = "MATH.SYN.EQ.LINEAR", "MATH.SYN.EQ.APPLY", "MATH.SYN.EQ.TRANSPOSE"
CREDS = {"bff": "synthetic-bff-to-insight-credential-0001", "orchestrator": "synthetic-orchestrator-to-insight-cred-01"}
BASE = "http://127.0.0.1:8791"


def policy_doc() -> dict[str, Any]:
    return json.loads((CONFIG_DIR / "claims.synthetic.json").read_text(encoding="utf-8"))


def make_policy(signed: bool = False, **overrides: Any) -> Policy:
    """signed=True 时去掉 synthetic 并填签字人（只在测试里模拟“已签字”的配置；数值仍是合成占位）"""
    doc = copy.deepcopy(policy_doc())
    if signed:
        doc.update(synthetic=False, signed_by="test-signer", signed_at="2026-09-30")
    for k, v in overrides.items():
        doc[k] = v
    return parse_policy(doc, env="test")


def templates(policy: Policy, days: int = 60) -> dict[str, TemplateInfo]:
    out = {}
    for cat, ref in policy.templates.items():
        vis = ("agent",) if cat == "metric" else ("agent", "student", "guardian")
        out[cat] = TemplateInfo(ref.key, ref.version, vis, timedelta(days=days))
    return out


def eid(i: int) -> str:
    return f"e1000000-0000-4000-8000-{i:012d}"


def bid(i: int) -> str:
    return f"6d000000-0000-4000-8000-{i:012d}"


def mrow(basis: int = 1, node: str = NODE_A, level: str = "apply", gate: str = "execute", conf: float = 0.5,
         evidence: tuple[int, ...] = (1, 2), at: datetime = T0, retest: datetime | None = None, subject: str = "aya",
         student: str = STUDENT) -> MasteryRow:
    return MasteryRow(basis_id=bid(basis), student_id=student, subject=subject, node_id=node, cognitive_level=level, gate=gate,
                      p_mastery=0.6, confidence=conf, evidence_ids=tuple(eid(i) for i in evidence),
                      algorithm_version="kge-mastery@0.1.0.syn", computed_at=at, next_retest_at=retest)


_ID_COUNTER = [0]


def ids(prefix: str = "c1a00000-0000-4000-8000-"):
    """新判断的 ID（进程内全局递增，不会与之前生成的重复）"""
    while True:
        _ID_COUNTER[0] += 1
        yield f"{prefix}{_ID_COUNTER[0]:012d}"


# ---------- HTTP ----------
def auth(caller: str) -> dict[str, str]:
    return {"authorization": f"Bearer {CREDS[caller]}"}


def agent_ctx(student: str = STUDENT, **extra: Any) -> dict[str, Any]:
    return {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": "orchestrator", "data_class": "L2",
            "student_ref": student, "audience": "agent", **extra}


def front_ctx(audience: str, actor_ref: str, student: str = STUDENT, link_id: str | None = None, **extra: Any) -> dict[str, Any]:
    actor: dict[str, Any] = {"role": audience, "actor_ref": actor_ref}
    if link_id:
        actor["link_id"] = link_id
    return {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": "bff", "data_class": "L2",
            "student_ref": student, "audience": audience, "actor": actor, **extra}


class StaticVerifier:
    """测试用的授权核对：合成的（actor_ref, 受众, 学生）白名单。缺省实现在生产代码里一律不通过"""

    def __init__(self, allowed: set[tuple[str, str, str]]) -> None:
        self.allowed = allowed

    async def verify(self, actor: Mapping[str, Any], audience: str, student: str) -> bool:
        return (actor.get("actor_ref"), audience, student) in self.allowed


def settings(**kw: Any):
    from insight.service.config import Settings
    return Settings(env="test", database_url="postgresql://unused", tokens=dict(CREDS), **kw)


@asynccontextmanager
async def running_app(db, *, role: str = "insight_app", policy: Policy | None = None, verifier: Any = None,
                      **settings_kw: Any) -> AsyncIterator[httpx.AsyncClient]:
    from insight.service.app import create_app
    from insight.store import create_pool
    pool = await create_pool(**db.connect_kwargs(role))
    app = create_app(settings(**settings_kw), pool=pool, policy=policy, verifier=verifier)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                client.app = app  # type: ignore[attr-defined]
                yield client
    finally:
        await pool.close()


async def call(client: httpx.AsyncClient, tool: str, body: Any, caller: str | None = "orchestrator") -> httpx.Response:
    return await client.post(f"/v0/{tool}", json=body, headers=auth(caller) if caller else {})
