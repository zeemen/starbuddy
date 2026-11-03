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
                        "--today", TODAY.isoformat(), "--no-hold-report"], capture_output=True, text=True, cwd=str(SERVICE))
    assert r.returncode == 1 and not out.exists()
    r = subprocess.run([sys.executable, "-m", "knowledge.compiler.snapshot", "--repo", str(work), "--commit", c1, "--out", str(out),
                        "--today", TODAY.isoformat(), "--no-hold-report"], capture_output=True, text=True, cwd=str(SERVICE))
    assert r.returncode == 0, r.stdout + r.stderr
    assert load_snapshot_dir(out).commit == c1


@pytest.mark.anyio
async def test_export_holds_reconcile_failed_pages(kgedb, page_repo, tmp_path):
    """页面对账失败被扣的页面（specs/release-gate-sprint4 第 6 节）：不发布本提交的内容，沿用上一份快照的原文与清单条目
    （带 source_commit，page.get 的 source_version 对回它原来的提交）；上一份没有就不导出"""
    repo, c1, c2, _ = page_repo
    prev = tmp_path / "prev"
    export_snapshot(repo, c1, prev, today=TODAY)
    out = tmp_path / "held"
    m = export_snapshot(repo, c2, out, today=TODAY, hold={LINEAR}, previous=prev)
    assert m["commit"] == c2 and m["held_pages"] == [LINEAR]
    by_id = {p["page_id"]: p for p in m["pages"]}
    assert set(by_id) == {LINEAR, TRANSPOSE, APPLY}
    assert by_id[LINEAR]["source_commit"] == c1 and "source_commit" not in by_id[TRANSPOSE]
    assert (out / by_id[LINEAR]["path"]).read_bytes() == (prev / by_id[LINEAR]["path"]).read_bytes()  # 原文是上一份的
    assert (out / by_id[TRANSPOSE]["path"]).read_text(encoding="utf-8") == git(repo, "show", f"{c2}:{by_id[TRANSPOSE]['path']}") + "\n"
    pages = load_snapshot_dir(out)
    assert pages.get("math.syn.eq.linear").source_commit == c1
    async with running_app(kgedb, pages=pages) as client:
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.linear"})
        assert r.status_code == 200 and r.json()["source_version"].endswith(f"graph:kge-pages@{c1[:12]}")
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.transpose"})
        assert r.status_code == 200 and r.json()["source_version"].endswith(f"graph:kge-pages@{c2[:12]}")
    # 上一份快照里没有的被扣页面（APPLY 是 c2 新增的）：不导出该页；相邻页按主分支的完整页面集合体检，照常导出
    m = export_snapshot(repo, c2, tmp_path / "noprev", today=TODAY, hold={APPLY}, previous=prev)
    assert sorted(p["page_id"] for p in m["pages"]) == sorted([LINEAR, TRANSPOSE]) and m["held_pages"] == [APPLY]
    assert all("source_commit" not in p for p in m["pages"])
    m = export_snapshot(repo, c2, tmp_path / "noprev2", today=TODAY, hold={APPLY})  # 不给上一份快照：同样只是不导出被扣页
    assert sorted(p["page_id"] for p in m["pages"]) == sorted([LINEAR, TRANSPOSE])
    assert [x.name for x in tmp_path.iterdir() if ".lint." in x.name] == []  # 体检用的临时目录已清理
    # 上一份快照被改过：拒绝沿用
    bad = tmp_path / "bad-prev"
    export_snapshot(repo, c1, bad, today=TODAY)
    os.chmod(bad, 0o755)
    os.chmod(bad / "manifest.json", 0o644)
    man = json.loads((bad / "manifest.json").read_text(encoding="utf-8"))
    man["pages"][0]["sha256"] = "0" * 64
    (bad / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    os.chmod(bad / "manifest.json", 0o444)
    os.chmod(bad, 0o555)
    with pytest.raises(ExportError, match="上一份快照不合格"):
        export_snapshot(repo, c2, tmp_path / "x", today=TODAY, hold={LINEAR}, previous=bad)
    # 载入时核对 source_commit 写法
    from knowledge.service.pages import SnapshotLoadError
    os.chmod(out, 0o755)
    os.chmod(out / "manifest.json", 0o644)
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    next(p for p in man["pages"] if p["page_id"] == LINEAR)["source_commit"] = "C1" * 20
    (out / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    os.chmod(out / "manifest.json", 0o444)
    os.chmod(out, 0o555)
    with pytest.raises(SnapshotLoadError, match="source_commit"):
        load_snapshot_dir(out)


def test_snapshot_cli_requires_fresh_hold_report(page_repo, tmp_path):
    repo, c1, c2, _ = page_repo
    prev = tmp_path / "prev"
    export_snapshot(repo, c1, prev, today=TODAY)
    report = tmp_path / "reconcile.json"
    now = dt.datetime.now(dt.timezone.utc)

    def run(out: str, *extra: str):
        return subprocess.run([sys.executable, "-m", "knowledge.compiler.snapshot", "--repo", str(repo), "--commit", c2,
                               "--out", str(tmp_path / out), "--today", TODAY.isoformat(), *extra],
                              capture_output=True, text=True, cwd=str(SERVICE))
    r = run("none")
    assert r.returncode == 2 and "--hold-report" in r.stderr and not (tmp_path / "none").exists()
    report.write_text(json.dumps({"format": "kge-pages-reconcile/1", "ok": False, "held_pages": [LINEAR],
                                  "exported_at": (now - dt.timedelta(hours=1)).isoformat()}), encoding="utf-8")
    r = run("held", "--hold-report", str(report), "--previous", str(prev))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "被扣 1 页" in r.stdout and json.loads((tmp_path / "held" / "manifest.json").read_text())["held_pages"] == [LINEAR]
    report.write_text(json.dumps({"format": "kge-pages-reconcile/1", "ok": False, "held_pages": [LINEAR],
                                  "exported_at": (now - dt.timedelta(hours=25)).isoformat()}), encoding="utf-8")
    r = run("stale", "--hold-report", str(report), "--previous", str(prev))
    assert r.returncode == 2 and "过期" in r.stdout and not (tmp_path / "stale").exists()
    r = run("stale48", "--hold-report", str(report), "--previous", str(prev), "--hold-max-age-hours", "48")
    assert r.returncode == 0, r.stdout + r.stderr


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


@pytest.mark.anyio
async def test_page_with_stale_map_version_not_served(kgedb, page_repo, tmp_path):
    """快照里页面的 map_version 与当前已发布版本不一致（升版到重编合并之间）：回 404 CONTENT_VERSION_NOT_FOUND，
    title = code，不带 detail；同一快照里版本一致的页照常服务"""
    _, _, _, snap = page_repo
    pages = build_pages(snap, [LINEAR, TRANSPOSE])
    rel = f"pages/kp/{TRANSPOSE}.md"
    assert "map_version: 0.9.0" in pages[rel]
    pages[rel] = pages[rel].replace("map_version: 0.9.0", "map_version: 0.8.0", 1)
    loaded = load_snapshot_dir(write_snapshot_dir(tmp_path / "s", pages))
    async with running_app(kgedb, pages=loaded) as client:
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.transpose"})
        body = r.json()
        assert r.status_code == 404 and body["code"] == "CONTENT_VERSION_NOT_FOUND"
        assert body["title"] == "CONTENT_VERSION_NOT_FOUND" and "detail" not in body
        assert C.problem_errors(body) == []
        r = await call(client, "page.get", {"context": ctx(), "page_id": "math.syn.eq.linear"})
        assert r.status_code == 200


def _clone(repo: Path, work: Path) -> None:
    subprocess.run(["git", "clone", "-q", str(repo), str(work)], check=True, capture_output=True,
                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})


def test_export_only_from_protected_branch(page_repo, tmp_path):
    """只导出受保护分支（缺省 main）上的提交：功能分支上的提交退出码 2；--branch 可指定；manifest 记分支名"""
    repo, c1, _, _ = page_repo
    work = tmp_path / "clone"
    _clone(repo, work)
    git(work, "checkout", "-q", "-b", "feature")
    p = work / "pages" / "kp" / f"{LINEAR}.md"
    text = p.read_text(encoding="utf-8").replace("每一步变形都要有依据。", "每一步变形都要写出依据。", 1)
    feature = commit_files(work, {f"pages/kp/{LINEAR}.md": text}, "合成：功能分支上的改动")
    with pytest.raises(ExportError, match="不在受保护分支"):
        export_snapshot(work, feature, tmp_path / "f1", today=TODAY)
    with pytest.raises(ExportError, match="不在受保护分支"):
        export_snapshot(work, "feature", tmp_path / "f2", today=TODAY)
    r = subprocess.run([sys.executable, "-m", "knowledge.compiler.snapshot", "--repo", str(work), "--commit", feature,
                        "--out", str(tmp_path / "f3"), "--today", TODAY.isoformat(), "--no-hold-report"],
                       capture_output=True, text=True, cwd=str(SERVICE))
    assert r.returncode == 2 and not (tmp_path / "f3").exists()
    m = export_snapshot(work, feature, tmp_path / "f4", today=TODAY, branch="feature")
    assert m["branch"] == "feature" and m["commit"] == git(work, "rev-parse", "feature")
    m = export_snapshot(work, c1, tmp_path / "f5", today=TODAY)  # main 上的旧提交：是 main 的祖先，可以导出
    assert m["branch"] == "main" and m["commit"] == c1
    for bad in ("--x", "../main", "main..feature", "a b", ""):
        with pytest.raises(ExportError):
            export_snapshot(work, c1, tmp_path / "bad", today=TODAY, branch=bad)


def test_export_requires_map_snapshot(page_repo, tmp_path):
    repo, _, _, _ = page_repo
    work = tmp_path / "clone"
    _clone(repo, work)
    git(work, "rm", "-q", "snapshots/current.yaml")
    git(work, "commit", "-q", "-m", "合成：删掉地图快照")
    out = tmp_path / "nosnap"
    with pytest.raises(ExportError, match="没有地图快照"):
        export_snapshot(work, "main", out, today=TODAY)
    r = subprocess.run([sys.executable, "-m", "knowledge.compiler.snapshot", "--repo", str(work), "--commit", "main", "--out", str(out),
                        "--today", TODAY.isoformat(), "--no-hold-report"], capture_output=True, text=True, cwd=str(SERVICE))
    assert r.returncode == 2 and not out.exists()


def test_export_ignores_repo_fsmonitor_and_uses_cat_file(page_repo, tmp_path):
    """仓库配置里的 fsmonitor 命令不执行（-c core.hooksPath=/dev/null -c core.fsmonitor=false）；读文件用 cat-file"""
    repo, c1, _, _ = page_repo
    work = tmp_path / "clone"
    _clone(repo, work)
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    git(work, "config", "core.fsmonitor", str(hook))
    m = export_snapshot(work, c1, tmp_path / "ok", today=TODAY)
    assert m["commit"] == c1 and not marker.exists()
    import inspect
    from knowledge.compiler import snapshot as snapmod
    src = inspect.getsource(snapmod)
    assert '"show"' not in src
    assert snapmod.GIT_SAFE == ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
    calls = []
    real = snapmod.subprocess.run

    def spy(cmd, *a, **kw):
        calls.append(list(cmd))
        return real(cmd, *a, **kw)
    snapmod.subprocess.run = spy
    try:
        export_snapshot(work, c1, tmp_path / "ok2", today=TODAY)
    finally:
        snapmod.subprocess.run = real
    git_calls = [c for c in calls if c[0] == "git"]
    assert git_calls and all(c[1:5] == list(snapmod.GIT_SAFE) for c in git_calls)
