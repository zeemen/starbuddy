"""端到端（spec 验收第 1 条的 PR ② 部分）：库里已发布地图（DbAtomSource，只读角色）→ 编出合成页面 → 合成页面仓库（本地 git）
提交 → 导出只读快照（knowledge.compiler.snapshot：提交号 + manifest）→ 知识服务 page.get 按两种版次渲染出不同的教材原句；
未发布页、节点不在已发布版本的页回 404；个人层三个工具回 501。导出前体检有阻断就拒绝导出。全部合成数据，不访问远程。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from knowledge.compiler.snapshot import ExportBlocked, ExportError, export_snapshot
from knowledge.service.contract import PERSONAL_TOOLS, contract
from knowledge.service.pages import load_snapshot_dir
from knowledge.service.reader import DbAtomSource, KgeReader, create_reader_pool
from pgkge import (APP_ROLE, APPLY, DISPUTED_QUOTE, DRAFT_ONLY, DRAFT_QUOTE, LINEAR, QUOTE_A, QUOTE_B, TRANSPOSE,
                   UNVERIFIED_QUOTE)
from svc import build_pages, call, ctx, running_app, write_snapshot_dir
from test_service_contract import example_request

C = contract()
TODAY = dt.date(2026, 9, 29)
SERVICE = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="synthetic", GIT_AUTHOR_EMAIL="synthetic@example.invalid", GIT_COMMITTER_NAME="synthetic",
               GIT_COMMITTER_EMAIL="synthetic@example.invalid", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def commit_files(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def page_repo(kgedb, tmp_path_factory):
    """合成页面仓库：第一次提交 LINEAR、TRANSPOSE 两页 + 地图快照；第二次提交再加 APPLY（不导出 = 未发布）"""
    import asyncio

    async def doc() -> dict:
        pool = await create_reader_pool(**kgedb.connect_kwargs(APP_ROLE))
        try:
            return await KgeReader(pool).snapshot_doc("aya")
        finally:
            await pool.close()

    snap_doc = asyncio.run(doc())
    snap = DbAtomSource(kgedb.dsn(APP_ROLE), "aya").load()
    repo = tmp_path_factory.mktemp("pages-repo")
    git(repo, "init", "-q", "-b", "main")
    first = build_pages(snap, [LINEAR, TRANSPOSE])
    files = {**first, "snapshots/current.yaml": yaml.safe_dump(snap_doc, allow_unicode=True, sort_keys=True)}
    c1 = commit_files(repo, files, "合成：两页知识点页")
    # 新增页面时相邻页的 [[链接]] 随之渲染重建（PR ① lessons 偏差 6）
    c2 = commit_files(repo, build_pages(snap, [LINEAR, TRANSPOSE, APPLY]), "合成：新增一页（未导出）")
    return repo, c1, c2, snap


def test_export_snapshot_is_readonly_with_commit_and_manifest(page_repo, tmp_path):
    repo, c1, c2, _ = page_repo
    out = tmp_path / "snap-1"
    m = export_snapshot(repo, c1, out, today=TODAY)
    assert m["commit"] == c1 and len(c1) == 40 and m["format"] == "kge-page-snapshot/1" and m["map_snapshot"] == "0.9.0"
    assert [p["page_id"] for p in m["pages"]] == [LINEAR, TRANSPOSE]
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == m
    for p in m["pages"]:
        assert (out / p["path"]).read_text(encoding="utf-8") == git(repo, "show", f"{c1}:{p['path']}") + "\n"
        assert p["page_version"] == "1.0.0" and p["map_version"] == "0.9.0"
    for path in [out, *out.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o222 == 0, path  # 只读
    with pytest.raises(ExportError):
        export_snapshot(repo, c1, out, today=TODAY)  # 快照不可变：目标已存在就拒绝
    with pytest.raises(ExportError):
        export_snapshot(repo, "no-such-branch", tmp_path / "x", today=TODAY)
    with pytest.raises(ExportError):
        export_snapshot(repo, "--output=/tmp/x", tmp_path / "y", today=TODAY)
    snap2 = export_snapshot(repo, "main", tmp_path / "snap-2", today=TODAY)
    assert snap2["commit"] == c2 and len(snap2["pages"]) == 3


def test_export_refuses_blocking_lint(page_repo, tmp_path):
    repo, c1, _, _ = page_repo
    work = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(work)], check=True, capture_output=True,
                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    p = work / "pages" / "kp" / f"{LINEAR}.md"
    text = p.read_text(encoding="utf-8").replace("## 易错\n\n", "## 易错\n\n合成：联系电话 13912345678。[@node:MATH.SYN.EQ.LINEAR]\n\n", 1)
    bad = commit_files(work, {f"pages/kp/{LINEAR}.md": text}, "合成：写进个人信息")
    out = tmp_path / "blocked"
    with pytest.raises(ExportBlocked):
        export_snapshot(work, bad, out, today=TODAY)
    assert not out.exists() and [x.name for x in tmp_path.iterdir() if x.name.startswith(".blocked")] == []
    r = subprocess.run([sys.executable, "-m", "knowledge.compiler.snapshot", "--repo", str(work), "--commit", bad, "--out", str(out),
                        "--today", TODAY.isoformat()], capture_output=True, text=True, cwd=str(SERVICE))
    assert r.returncode == 1 and not out.exists()
    r = subprocess.run([sys.executable, "-m", "knowledge.compiler.snapshot", "--repo", str(work), "--commit", c1, "--out", str(out),
                        "--today", TODAY.isoformat()], capture_output=True, text=True, cwd=str(SERVICE))
    assert r.returncode == 0, r.stdout + r.stderr
    assert load_snapshot_dir(out).commit == c1


@pytest.mark.anyio
async def test_page_get_renders_by_edition_and_region(kgedb, page_repo, tmp_path):
    repo, c1, _, _ = page_repo
    out = tmp_path / "snap"
    manifest = export_snapshot(repo, c1, out, today=TODAY)
    pages = load_snapshot_dir(out)
    async with running_app(kgedb, pages=pages) as client:
        async def page(**kw):
            r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.linear", **kw})
            body = r.json()
            assert r.status_code == 200, body
            assert C.validate("PageGetResponse", body) == []
            return body

        a = await page(edition="syn-ed-a", region="syn-region")
        b = await page(edition="syn-ed-b", region="syn-region")
        none = await page()
        ma, mb = a["result"]["body_md"], b["result"]["body_md"]
        assert QUOTE_A in ma and QUOTE_B not in ma
        assert QUOTE_B in mb and QUOTE_A not in mb
        assert ma != mb
        md = none["result"]["body_md"]
        assert QUOTE_A not in md and QUOTE_B not in md and "{{quote:" not in md and "syn-ed-a" in md and "syn-ed-b" in md
        for body in (a, b, none):
            text = json.dumps(body, ensure_ascii=False)
            for forbidden in (UNVERIFIED_QUOTE, DISPUTED_QUOTE, DRAFT_QUOTE, "0.990"):
                assert forbidden not in text
            res = body["result"]
            assert res["page_id"] == "math.syn.eq.linear" and res["page_version"] == manifest["pages"][0]["page_version"]
            assert body["source_version"] == f"graph:math-map@0.9.0;graph:kge-pages@{c1[:12]}"
            assert body["model_version"] == "algo:kge-page-get@0.1.0" and body["evidence_ids"] == []
            assert f"node:{LINEAR}" in res["atom_refs"]
            assert "解方程时先观察方程的结构，再决定变形的顺序。" in res["body_md"]  # 叙述段逐字来自页面
            assert not res["body_md"].startswith("---")  # 不带页面头部
        assert "syn-other" not in ma and "syn-region 2025：权重 0.030" in ma
        assert "syn-other" in md  # 不给地区时列出全部地区
        assert sum(r.startswith("textbook_projection:") for r in a["result"]["atom_refs"]) == 1
        assert sum(r.startswith("textbook_projection:") for r in none["result"]["atom_refs"]) == 2
        c = await page(edition="syn-ed-c")  # 未核对版次：不渲染任何原句
        assert UNVERIFIED_QUOTE not in c["result"]["body_md"] and "（暂无）" in c["result"]["body_md"]

        # 未发布（仓库里有，但不在导出的快照里）
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.apply"})
        assert r.status_code == 404 and r.json()["code"] == "CONTENT_VERSION_NOT_FOUND"
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.no-such-page"})
        assert r.status_code == 404
        r = await call(client, "page.get", {"context": ctx(map_version="0.10.0"), "page_id": "math.syn.eq.linear"})
        assert r.status_code == 404
        # 个人层三个工具 501
        for tool in sorted(PERSONAL_TOOLS):
            r = await call(client, tool, example_request(tool), "orchestrator")
            assert r.status_code == 501 and r.json()["code"] == "METHOD_NOT_IMPLEMENTED"


@pytest.mark.anyio
async def test_page_for_node_outside_published_map_not_served(kgedb, page_repo, tmp_path):
    """快照里有页面，但它的节点只在草稿版本里（或已撤下）：不服务"""
    _, _, _, snap = page_repo
    pages = build_pages(snap, [LINEAR])
    text = pages[f"pages/kp/{LINEAR}.md"].replace(LINEAR, DRAFT_ONLY)
    d = write_snapshot_dir(tmp_path / "s", {f"pages/kp/{DRAFT_ONLY}.md": text, **pages},
                           extra_files={f"pages/kp/{APPLY}.md": pages[f"pages/kp/{LINEAR}.md"].replace(LINEAR, APPLY)})
    loaded = load_snapshot_dir(d)
    async with running_app(kgedb, pages=loaded) as client:
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.draft-only"})
        assert r.status_code == 404
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.apply"})  # 文件在目录里但 manifest 没列
        assert r.status_code == 404
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.linear"})
        assert r.status_code == 200


@pytest.mark.anyio
async def test_page_snapshot_config(kgedb, tmp_path):
    from knowledge.service.app import create_app
    from knowledge.service.pages import SnapshotLoadError
    from svc import settings
    async with running_app(kgedb) as client:  # 没配快照：契约规定的 501
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.linear"})
        assert r.status_code == 501 and r.json()["code"] == "METHOD_NOT_IMPLEMENTED"
    pool = await create_reader_pool(**kgedb.connect_kwargs(APP_ROLE))
    try:
        app = create_app(settings(page_snapshot=tmp_path / "missing"), pool=pool)
        with pytest.raises(SnapshotLoadError):  # 配了但目录不存在：拒绝启动
            async with app.router.lifespan_context(app):
                pass
    finally:
        await pool.close()
