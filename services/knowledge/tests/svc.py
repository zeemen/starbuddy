"""知识服务测试的共用工具：调用方凭据、上下文、进程内应用（lifespan + httpx ASGITransport）。全部合成数据。"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

from knowledge.service.app import create_app
from knowledge.service.config import Settings
from knowledge.service.pages import PageSnapshot
from knowledge.service.reader import create_reader_pool
from pgkge import APP_ROLE

CREDS = {"bff": "synthetic-bff-to-knowledge-credential-001",
         "orchestrator": "synthetic-orchestrator-to-knowledge-0001",
         "learning": "synthetic-learning-to-knowledge-cred-0001"}
BASE = "http://127.0.0.1:8790"


def settings(**kw: Any) -> Settings:
    return Settings(env="test", database_url="postgresql://unused", tokens=dict(CREDS), **kw)


def auth(caller: str) -> dict[str, str]:
    return {"authorization": f"Bearer {CREDS[caller]}"}


def ctx(caller: str = "orchestrator", data_class: str = "L1", **extra: Any) -> dict[str, Any]:
    return {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": caller, "data_class": data_class, **extra}


@asynccontextmanager
async def running_app(db, *, pages: PageSnapshot | None = None, role: str = APP_ROLE, weknora: Any = None,
                      **settings_kw: Any) -> AsyncIterator[httpx.AsyncClient]:
    pool = await create_reader_pool(**db.connect_kwargs(role))
    app = create_app(settings(**settings_kw), pool=pool, pages=pages, weknora=weknora)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                client.app = app  # type: ignore[attr-defined]
                yield client
    finally:
        await pool.close()


async def call(client: httpx.AsyncClient, tool: str, body: Any, caller: str | None = "orchestrator") -> httpx.Response:
    headers = auth(caller) if caller else {}
    return await client.post(f"/v0/{tool}", json=body, headers=headers)


# ---------- 合成页面：从库里的已发布快照编出知识点页（叙述段为合成文本，中性教研口吻） ----------
NARRATIVE = {
    "key_points": "解方程时先观察方程的结构，再决定变形的顺序。[@node:{nid}]\n\n每一步变形都要有依据。[@node:{nid}]",
    "pitfalls": "变形时漏掉某一项，是这一部分常见的问题。[@node:{nid}]",
    "frequent_questions": "- 说明某一步变形的依据。[@node:{nid}]\n- 判断变形前后是否等价。[@node:{nid}]",
}


def build_pages(snapshot, node_ids: list[str], known: set[str] | None = None) -> dict[str, str]:
    """返回 {相对路径: 页面文本}（相对路径形如 pages/kp/<节点 ID>.md）"""
    from knowledge.compiler.rebuild import assemble
    from knowledge.pages.format import KP_SECTIONS, SCHEMA_VERSION, Page, Section, serialize

    known = set(node_ids) if known is None else known
    out = {}
    for nid in node_ids:
        narrative = {s.id: Section(s.id, "narrative", f"## {s.title}\n\n" + NARRATIVE[s.id].format(nid=nid))
                     for s in KP_SECTIONS if s.kind == "narrative"}
        skeleton = Page({"page_id": nid, "page_type": "kp", "subject": snapshot.subject, "node_id": nid,
                         "map_version": snapshot.map_version, "schema_version": SCHEMA_VERSION, "page_version": "1.0.0",
                         "risk_level": "low", "rebuild_kind": "render",
                         "locked_sections": [s.id for s in KP_SECTIONS if s.kind == "locked"], "atom_refs": []}, "", ())
        out[f"pages/kp/{nid}.md"] = serialize(assemble(skeleton, snapshot, narrative, known))
    return out


def write_snapshot_dir(root, pages: dict[str, str], commit: str = "c" * 40, extra_files: dict[str, str] | None = None):
    """不经 Git 直接写一个快照目录（契约一致性测试用；端到端走 knowledge.compiler.snapshot）。extra_files 不进 manifest"""
    import hashlib
    import json
    from pathlib import Path

    from knowledge.pages.format import parse
    from knowledge.service.pages import FORMAT

    root = Path(root)
    entries = []
    for rel, text in sorted(pages.items()):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
        h = parse(text).header
        entries.append({"page_id": h["page_id"], "path": rel, "page_version": str(h["page_version"]),
                        "map_version": str(h["map_version"]), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    for rel, text in (extra_files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"format": FORMAT, "commit": commit, "pages": entries}), encoding="utf-8")
    return root
