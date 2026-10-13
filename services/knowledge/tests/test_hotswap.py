"""KGE-06 范围 4：页面快照热切换（hotswap.py）与 manifest 签名接口占位。全部合成数据（规则 8）。

- 不连库：载入核对、篡改快照载入失败并计数、旧快照保留、HMAC 接口（未配置不验签并记录；配置后按占位格式验签、缺签名或不符即拒绝）、
  指针文件的格式与权限、载入期间 manifest 被换、配置项；
- 库上（一次性 Postgres）：热切换期间并发 page.get 全部成功且页面与提交号一致；请求进行中切换，响应仍用开头取到的快照；
  SIGHUP 经指针文件热切换。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import signal
import time
from pathlib import Path

import pytest

from conftest import make_page_snapshot
from knowledge.service.config import Settings
from knowledge.service.hotswap import (SIGNATURE_FILE, HmacVerifier, SnapshotSwapper, UnsignedVerifier, read_pointer,
                                       verifier_for)
from knowledge.service.pages import SnapshotLoadError

KEY = "synthetic-manifest-signing-key-for-tests-0001"
A, B, C = "a" * 40, "b" * 40, "c" * 40


class Core:
    """只有 pages 属性的 ToolCore 替身"""
    pages = None


def snap_dir(root: Path, commit: str) -> Path:
    return make_page_snapshot(root, commit=commit)


def tamper(d: Path) -> Path:
    """改一页正文而不改 manifest（哈希不符）"""
    page = sorted((d / "pages" / "kp").glob("*.md"))[0]
    page.write_text(page.read_text(encoding="utf-8") + "\n被改过的一行。\n", encoding="utf-8")
    return d


def sign(d: Path, key: str = KEY) -> Path:
    mac = hmac.new(key.encode(), (d / "manifest.json").read_bytes(), hashlib.sha256).hexdigest()
    (d / SIGNATURE_FILE).write_text(mac + "\n", encoding="ascii")
    return d


@pytest.mark.anyio
async def test_reload_swaps_and_tampered_snapshot_keeps_old_and_counts(tmp_path, lexicon, caplog):
    core = Core()
    sw = SnapshotSwapper(core, UnsignedVerifier(), lexicon=lexicon)
    caplog.set_level(logging.INFO)
    first = sw.load_initial(snap_dir(tmp_path / "a", A))
    assert core.pages is first and first.commit == A and sw.stats.loads == 1
    assert await sw.reload(snap_dir(tmp_path / "b", B)) is True
    assert core.pages.commit == B and sw.stats.loads == 2 and sw.stats.failures == 0 and sw.stats.history == [A, B]
    kept = core.pages
    # 篡改：哈希不符 → 载入失败，旧快照照常服务，失败计数加一
    assert await sw.reload(tamper(snap_dir(tmp_path / "c", C))) is False
    assert core.pages is kept and sw.stats.failures == 1 and sw.stats.commit == B
    assert "哈希" in (sw.stats.last_error or "")
    # 不存在的目录、缺 manifest 同样失败计数
    assert await sw.reload(tmp_path / "nope") is False
    assert sw.stats.failures == 2 and core.pages is kept
    # 未配置密钥：每次成功载入记一条“未验签”
    assert sw.stats.verification == "unverified" and sw.stats.unverified_loads == 2
    assert caplog.text.count("not signature-checked") >= 2
    assert str(tmp_path) not in caplog.text  # 日志不带路径


@pytest.mark.anyio
async def test_startup_load_fails_closed(tmp_path, lexicon):
    sw = SnapshotSwapper(Core(), UnsignedVerifier(), lexicon=lexicon)
    with pytest.raises(SnapshotLoadError):
        sw.load_initial(tamper(snap_dir(tmp_path / "a", A)))
    assert sw.load_initial(None) is None  # 什么都没配：page.get 回 501（现状）


@pytest.mark.anyio
async def test_hmac_interface(tmp_path, lexicon):
    with pytest.raises(RuntimeError):
        HmacVerifier("short")
    v = verifier_for(KEY)
    assert v.name == "hmac-sha256" and KEY not in repr(v) and isinstance(verifier_for(None), UnsignedVerifier)
    core = Core()
    sw = SnapshotSwapper(core, v, lexicon=lexicon)
    good = sw.load_initial(sign(snap_dir(tmp_path / "a", A)))
    assert good.commit == A and sw.stats.unverified_loads == 0
    # 缺签名文件
    assert await sw.reload(snap_dir(tmp_path / "b", B)) is False
    # 别的密钥签的
    assert await sw.reload(sign(snap_dir(tmp_path / "c", C), "another-synthetic-key-that-is-long-enough-0002")) is False
    # 签名后又改了 manifest
    d = sign(snap_dir(tmp_path / "d", B))
    doc = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    doc["pages"] = doc["pages"][:1]
    (d / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
    assert await sw.reload(d) is False
    # 签名文件格式不对
    e = sign(snap_dir(tmp_path / "e", B))
    (e / SIGNATURE_FILE).write_text("not-hex", encoding="ascii")
    assert await sw.reload(e) is False
    assert sw.stats.failures == 4 and core.pages is good
    assert await sw.reload(sign(snap_dir(tmp_path / "f", B))) is True and core.pages.commit == B


class SwapAfterVerify:
    """验签通过之后、解析之前，有人把磁盘上的 manifest 换成另一份（提交号 B）：载入的必须仍是验过的那一份字节"""
    name = "hmac-sha256"

    def verify(self, root: Path, manifest_raw: bytes) -> None:
        assert json.loads(manifest_raw)["commit"] == A
        m = root / "manifest.json"
        m.write_text(m.read_text(encoding="utf-8").replace(A, B), encoding="utf-8")


@pytest.mark.anyio
async def test_manifest_read_once_verified_bytes_are_the_loaded_bytes(tmp_path, lexicon):
    core = Core()
    sw = SnapshotSwapper(core, SwapAfterVerify(), lexicon=lexicon)
    assert await sw.reload(snap_dir(tmp_path / "a", A)) is True
    assert core.pages.commit == A  # 不是磁盘上后来的 B
    # load_snapshot_dir 收到 manifest_raw 时只用它，不再读磁盘
    from knowledge.service.pages import load_snapshot_dir
    d = snap_dir(tmp_path / "b", A)
    raw = (d / "manifest.json").read_bytes().replace(A.encode(), C.encode())
    assert load_snapshot_dir(d, lexicon=lexicon, manifest_raw=raw).commit == C
    # manifest 是符号链接：O_NOFOLLOW 打开即失败
    e = snap_dir(tmp_path / "e", B)
    real_m = tmp_path / "manifest-real.json"
    real_m.write_bytes((e / "manifest.json").read_bytes())
    (e / "manifest.json").unlink()
    (e / "manifest.json").symlink_to(real_m)
    assert await sw.reload(e) is False and "符号链接" in (sw.stats.last_error or "") and core.pages.commit == A


@pytest.mark.anyio
async def test_snapshot_root_constraint(tmp_path, lexicon):
    root = tmp_path / "snaps"
    root.mkdir()
    inside, outside = snap_dir(root / "a", A), snap_dir(tmp_path / "out", B)
    core = Core()
    sw = SnapshotSwapper(core, UnsignedVerifier(), lexicon=lexicon, snapshot_root=root)
    assert await sw.reload(inside) is True
    assert await sw.reload(outside) is False and "根目录" in (sw.stats.last_error or "")
    # 根目录里的符号链接指到外面：按解析后的路径判（链接在上一级目录，快照目录本身不是链接，lstat 查不出来）
    outer = tmp_path / "outer"
    snap_dir(outer / "s", B)
    (root / "sneaky").symlink_to(outer)
    assert await sw.reload(root / "sneaky" / "s") is False and "根目录" in (sw.stats.last_error or "")
    assert await sw.reload(root) is False  # 根目录本身不算
    assert core.pages.commit == A and sw.stats.failures == 3


@pytest.mark.anyio
async def test_pointer_file(tmp_path, lexicon):
    a, b = snap_dir(tmp_path / "a", A), snap_dir(tmp_path / "b", B)
    ptr = tmp_path / "current"
    ptr.write_text(f"{a}\n", encoding="utf-8")
    assert read_pointer(ptr) == a
    core = Core()
    sw = SnapshotSwapper(core, UnsignedVerifier(), pointer=ptr, lexicon=lexicon)
    assert sw.load_initial(None).commit == A
    ptr.write_text(str(b), encoding="utf-8")
    assert await sw.reload() is True and core.pages.commit == B
    # 指针文件不合格：相对路径、多行、空、组可写、符号链接 → 失败计数，旧快照保留
    for bad in ("relative/dir", f"{a}\n{b}", "", f"{a}/../b"):
        ptr.write_text(bad, encoding="utf-8")
        with pytest.raises(SnapshotLoadError):
            read_pointer(ptr)
        assert await sw.reload() is False
    ptr.write_text(str(a), encoding="utf-8")
    os.chmod(ptr, 0o664)
    with pytest.raises(SnapshotLoadError):
        read_pointer(ptr)
    os.chmod(ptr, 0o644)
    link = tmp_path / "link"
    link.symlink_to(ptr)
    with pytest.raises(SnapshotLoadError):
        read_pointer(link)
    # 指针文件所在目录对属组或其他用户可写：拒绝（别人能把指针换掉）
    for mode in (0o775, 0o757):
        os.chmod(tmp_path, mode)
        try:
            with pytest.raises(SnapshotLoadError, match="所在目录"):
                read_pointer(ptr)
        finally:
            os.chmod(tmp_path, 0o700)
    assert sw.stats.failures == 4 and core.pages.commit == B
    # 没有指针文件时 reload() 不能热切换
    assert await SnapshotSwapper(Core(), UnsignedVerifier(), lexicon=lexicon).reload() is False


def test_settings_pointer_and_key():
    base = {"KNOWLEDGE_ENV": "test", "KNOWLEDGE_DATABASE_URL": "postgresql://unused", "KNOWLEDGE_TOKEN_BFF": "x" * 32}
    s = Settings.from_env({**base, "KNOWLEDGE_PAGE_SNAPSHOT_POINTER": "/srv/kge/current", "KNOWLEDGE_SNAPSHOT_HMAC_KEY": KEY})
    assert s.page_snapshot_pointer == Path("/srv/kge/current") and s.snapshot_hmac_key == KEY and KEY not in repr(s)
    assert Settings.from_env(base).snapshot_hmac_key is None
    assert Settings.from_env({**base, "KNOWLEDGE_PAGE_SNAPSHOT_ROOT": "/srv/kge"}).page_snapshot_root == Path("/srv/kge")
    for bad in ({"KNOWLEDGE_PAGE_SNAPSHOT_POINTER": "/srv/p", "KNOWLEDGE_PAGE_SNAPSHOT": "/srv/s"},
                {"KNOWLEDGE_PAGE_SNAPSHOT_ROOT": "relative/root"},
                {"KNOWLEDGE_PAGE_SNAPSHOT_POINTER": "relative/p"}, {"KNOWLEDGE_SNAPSHOT_HMAC_KEY": "short"}):
        with pytest.raises(RuntimeError):
            Settings.from_env({**base, **bad})


# ---------- 库上：并发与请求进行中的切换 ----------
@pytest.fixture(scope="module")
def db_snap(kgedb):
    from knowledge.service.reader import DbAtomSource
    from pgkge import APP_ROLE
    return DbAtomSource(kgedb.dsn(APP_ROLE), "aya").load()


def db_pages_dir(root: Path, snapshot, commit: str) -> Path:
    from pgkge import LINEAR, TRANSPOSE
    from svc import build_pages, write_snapshot_dir
    return write_snapshot_dir(root, build_pages(snapshot, [LINEAR, TRANSPOSE]), commit=commit)


def page_body(page_id: str = "math.syn.eq.linear") -> dict:
    from svc import ctx
    return {"context": ctx("orchestrator"), "page_id": page_id}


def commit_of(resp_body: dict) -> str:
    return resp_body["source_version"].rsplit("kge-pages@", 1)[1]


@pytest.mark.anyio
async def test_concurrent_requests_during_swaps_never_fail(kgedb, db_snap, tmp_path):
    from svc import call, running_app
    a, b = db_pages_dir(tmp_path / "a", db_snap, A), db_pages_dir(tmp_path / "b", db_snap, B)
    bad = tamper(db_pages_dir(tmp_path / "c", db_snap, C))
    async with running_app(kgedb) as client:
        sw: SnapshotSwapper = client.app.state.knowledge["snapshot"]  # type: ignore[attr-defined]
        assert await sw.reload(a) is True

        async def requests() -> list:
            return await asyncio.gather(*[call(client, "page.get", page_body()) for _ in range(40)])

        async def swaps() -> list[bool]:
            out = []
            for target in (b, bad, a, bad, b):
                out.append(await sw.reload(target))
                await asyncio.sleep(0)
            return out
        rs, flips = await asyncio.gather(requests(), swaps())
        assert flips == [True, False, True, False, True]
        assert all(r.status_code == 200 for r in rs), [r.status_code for r in rs if r.status_code != 200]
        assert {commit_of(r.json()) for r in rs} <= {A[:12], B[:12]}
        assert sw.stats.failures == 2 and sw.stats.commit == B
        r = await call(client, "page.get", page_body())
        assert r.status_code == 200 and commit_of(r.json()) == B[:12]


@pytest.mark.anyio
async def test_swap_in_the_middle_of_a_request_uses_the_snapshot_taken_at_start(kgedb, db_snap, tmp_path):
    from svc import call, running_app
    a, b = db_pages_dir(tmp_path / "a", db_snap, A), db_pages_dir(tmp_path / "b", db_snap, B)
    async with running_app(kgedb) as client:
        state = client.app.state.knowledge  # type: ignore[attr-defined]
        sw: SnapshotSwapper = state["snapshot"]
        assert await sw.reload(a) is True
        reader = state["core"].reader
        real_node = reader.node
        swapped: list[bool] = []

        async def node_then_swap(*args, **kw):
            if not swapped:
                swapped.append(await sw.reload(b))  # 请求读库的途中换了快照
            return await real_node(*args, **kw)
        reader.node = node_then_swap
        try:
            r = await call(client, "page.get", page_body())
        finally:
            reader.node = real_node
        assert swapped == [True] and r.status_code == 200
        assert commit_of(r.json()) == A[:12]  # 页面与提交号都来自请求开头的那一份
        r = await call(client, "page.get", page_body())
        assert commit_of(r.json()) == B[:12]


@pytest.mark.anyio
async def test_sighup_reloads_from_pointer(kgedb, db_snap, tmp_path):
    from svc import call, running_app
    a, b = db_pages_dir(tmp_path / "a", db_snap, A), db_pages_dir(tmp_path / "b", db_snap, B)
    bad = tamper(db_pages_dir(tmp_path / "c", db_snap, C))
    ptr = tmp_path / "current"
    ptr.write_text(str(a), encoding="utf-8")
    # 兜底处理器：应用没装上 SIGHUP 时，信号不能按缺省动作结束测试进程（让断言来报）
    previous = signal.signal(signal.SIGHUP, lambda *_: None)
    try:
        await _sighup_flow(kgedb, ptr, a, b, bad)
    finally:
        signal.signal(signal.SIGHUP, previous)


async def _sighup_flow(kgedb, ptr: Path, a: Path, b: Path, bad: Path) -> None:
    from svc import call, running_app
    async with running_app(kgedb, page_snapshot_pointer=ptr) as client:
        sw: SnapshotSwapper = client.app.state.knowledge["snapshot"]  # type: ignore[attr-defined]
        assert sw.stats.commit == A
        for target, want_loads, want_failures, want_commit in ((b, 2, 0, B), (bad, 2, 1, B)):
            ptr.write_text(str(target), encoding="utf-8")
            os.kill(os.getpid(), signal.SIGHUP)
            for _ in range(200):
                if sw.stats.loads == want_loads and sw.stats.failures == want_failures:
                    break
                await asyncio.sleep(0.02)
            assert (sw.stats.loads, sw.stats.failures, sw.stats.commit) == (want_loads, want_failures, want_commit)
            r = await call(client, "page.get", page_body())
            assert r.status_code == 200 and commit_of(r.json()) == want_commit[:12]
        # 关停时等进行中的 reload 做完：排一次切换就立刻退出。这次载入人为放慢（线程里睡 0.3 秒），
        # 不等就关停时一定还没做完，测试不靠时序碰运气（原先载入太快时“不等”也能偶然通过）
        ptr.write_text(str(a), encoding="utf-8")
        load = sw.load

        def slow_load(root):
            time.sleep(0.3)
            return load(root)

        sw.load = slow_load  # type: ignore[method-assign]
        sw.request_reload()
    assert sw.stats.loads == 3 and sw.stats.commit == A and not sw._tasks


@pytest.mark.anyio
async def test_sighup_without_pointer_only_logs(kgedb, db_snap, tmp_path, caplog):
    from svc import call, running_app
    a = db_pages_dir(tmp_path / "a", db_snap, A)
    previous = signal.signal(signal.SIGHUP, lambda *_: None)  # 兜底：应用没装处理器时不让信号结束测试进程
    caplog.set_level(logging.WARNING)
    try:
        async with running_app(kgedb, page_snapshot=a) as client:
            sw: SnapshotSwapper = client.app.state.knowledge["snapshot"]  # type: ignore[attr-defined]
            os.kill(os.getpid(), signal.SIGHUP)
            for _ in range(100):
                if "SIGHUP ignored" in caplog.text:
                    break
                await asyncio.sleep(0.02)
            assert "SIGHUP ignored" in caplog.text
            assert (sw.stats.loads, sw.stats.failures, sw.stats.commit) == (1, 0, A)
            r = await call(client, "page.get", page_body())
            assert r.status_code == 200 and commit_of(r.json()) == A[:12]
    finally:
        signal.signal(signal.SIGHUP, previous)
