"""页面快照热切换（specs/kge06-weekly-revision 范围 4；技术债 T30“换快照须重启”）。

封测期修订按周发布，每周换一次页面快照不应重启知识服务。做法：
- 快照目录仍不可变（knowledge.compiler.snapshot：换提交就换目录）。部署方把“当前快照目录”的绝对路径写进一个指针文件
  （KNOWLEDGE_PAGE_SNAPSHOT_POINTER），换快照时先导出新目录、再改指针文件（先写同目录临时文件再 rename 覆盖）、再给进程发 SIGHUP；
  指针文件与它所在目录不能对属组或其他用户可写，路径不能含 `..`；配置了 KNOWLEDGE_PAGE_SNAPSHOT_ROOT 时目标解析后必须在根目录里；
- 重新载入在线程里做（不占事件循环），与启动时同一套逐页核对（knowledge.service.pages.load_snapshot_dir：manifest 逐页 sha256、
  头部一致、页面体检、无符号链接、无组可写）；manifest.json 只读一次（O_NOFOLLOW + fstat），验签与解析用同一份字节；
- 全部通过才切换：一次引用赋值（ToolCore.pages），正在处理的请求已在开头取走旧快照，不会读到半新半旧；
- 任何一步失败：保留旧快照继续服务，失败计数加一，日志只记失败原因（不记路径与内容）；多次 SIGHUP 串行处理（锁）；
  没配置指针文件时 SIGHUP 只记日志；关停时等进行中的 reload 做完。

manifest 签名（HMAC）只留接口（T30 / T31：签名格式与密钥管理待人确认）：
- 密钥从环境变量 KNOWLEDGE_SNAPSHOT_HMAC_KEY 读（≥ 32 个字符）；未配置时按现状不验签，每次载入记一条“未验签”日志并计数；
- 配置了密钥时按占位格式验签：快照目录里的 manifest.json.hmac-sha256（一行 64 位小写十六进制，HMAC-SHA256(密钥, manifest.json 原始字节)），
  缺文件或不符即拒绝载入（fail closed）。导出器（knowledge.compiler.snapshot）还不写签名文件：启用前要由人定签名格式、密钥保管与轮换。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .pages import PageSnapshot, SnapshotLoadError, load_snapshot_dir, read_safe

log = logging.getLogger("starbuddy.knowledge.snapshot")

HMAC_KEY_ENV = "KNOWLEDGE_SNAPSHOT_HMAC_KEY"
SIGNATURE_FILE = "manifest.json.hmac-sha256"
MIN_KEY_CHARS = 32
MAX_POINTER_BYTES = 4096
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ManifestVerifier(Protocol):
    """manifest 签名核对的接口：不合格抛 SnapshotLoadError。name 进计数与日志（unverified / hmac-sha256）"""
    name: str

    def verify(self, root: Path, manifest_raw: bytes) -> None: ...


class UnsignedVerifier:
    """未配置密钥：按现状不验签，只记录（每次载入一条日志）"""
    name = "unverified"

    def verify(self, root: Path, manifest_raw: bytes) -> None:
        log.warning("page snapshot manifest not signature-checked (%s not configured)", HMAC_KEY_ENV)


class HmacVerifier:
    """占位格式：manifest.json.hmac-sha256 = HMAC-SHA256(密钥, manifest.json 原始字节) 的十六进制。常量时间比较"""
    name = "hmac-sha256"

    def __init__(self, key: str) -> None:
        if len(key) < MIN_KEY_CHARS:
            raise RuntimeError(f"{HMAC_KEY_ENV} 少于 {MIN_KEY_CHARS} 个字符")
        self._key = key.encode("utf-8")

    def __repr__(self) -> str:  # 不让密钥进日志或异常
        return "HmacVerifier(<key>)"

    def verify(self, root: Path, manifest_raw: bytes) -> None:
        sig = read_safe(root / SIGNATURE_FILE, "manifest 签名文件读不了")
        text = sig.decode("ascii", errors="replace").strip()
        if not _HEX64.fullmatch(text):
            raise SnapshotLoadError("manifest 签名文件格式不对")
        want = hmac.new(self._key, manifest_raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want.encode("ascii"), text.encode("ascii")):
            raise SnapshotLoadError("manifest 签名不符")


def verifier_for(key: str | None) -> ManifestVerifier:
    return HmacVerifier(key) if key else UnsignedVerifier()


def read_pointer(path: Path) -> Path:
    """指针文件：一行快照目录的绝对路径，不能含 `..` 段。指针文件本身不能是符号链接、不能对属组或其他用户可写
    （O_NOFOLLOW 打开后对描述符核对）；它所在的目录也不能对属组或其他用户可写（否则别人能把它换掉）。
    部署方改指针时先写同目录的临时文件、再 rename 覆盖（原子替换，读的一方不会读到半行）。"""
    try:
        parent = os.stat(path.parent)
    except OSError:
        raise SnapshotLoadError("快照指针文件读不了") from None
    if parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SnapshotLoadError("快照指针文件所在目录对属组或其他用户可写")
    raw = read_safe(path, "快照指针文件读不了")
    if len(raw) > MAX_POINTER_BYTES or b"\0" in raw:
        raise SnapshotLoadError("快照指针文件格式不对")
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise SnapshotLoadError("快照指针文件格式不对") from None
    if not text or "\n" in text or not os.path.isabs(text):
        raise SnapshotLoadError("快照指针文件必须是一行绝对路径")
    if ".." in Path(text).parts:
        raise SnapshotLoadError("快照指针文件里的路径不能含 ..")
    return Path(text)


def within_root(target: Path, root: Path | None) -> Path:
    """配置了快照根目录（KNOWLEDGE_PAGE_SNAPSHOT_ROOT）时，目标目录解析符号链接后必须在根目录里面（不能等于根本身）"""
    if root is None:
        return target
    real, base = os.path.realpath(target), os.path.realpath(root)
    if real == base or os.path.commonpath([real, base]) != base:
        raise SnapshotLoadError("快照目录不在配置的快照根目录里")
    return target


@dataclass
class SwapStats:
    """载入计数（进程内；由运维从日志与 app.state 读，不对外暴露接口）"""
    verification: str
    loads: int = 0                 # 成功载入（含启动时的第一次）
    failures: int = 0              # 载入失败、保留旧快照的次数
    unverified_loads: int = 0      # 未验签的成功载入
    last_error: str | None = None  # 最近一次失败的原因（SnapshotLoadError 的说明，不含路径与内容）
    commit: str | None = None      # 当前快照的提交号
    history: list[str] = field(default_factory=list)  # 成功切换过的提交号（最近 20 个）


class SnapshotSwapper:
    """持有 ToolCore，原子替换它的页面快照。reload 串行执行；失败不动旧快照"""

    def __init__(self, core: Any, verifier: ManifestVerifier, pointer: Path | None = None, lexicon: Any = None,
                 snapshot_root: Path | None = None) -> None:
        self.core = core
        self.verifier = verifier
        self.pointer = pointer
        self.lexicon = lexicon
        self.snapshot_root = snapshot_root  # KNOWLEDGE_PAGE_SNAPSHOT_ROOT：配置时快照目录必须在它里面
        self.stats = SwapStats(verification=verifier.name)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def load(self, root: str | Path) -> PageSnapshot:
        """同步载入并核对（启动时直接调用，失败即拒绝启动；运行中由 reload 放进线程里调用）。
        manifest.json 只读一次（O_NOFOLLOW + fstat）：验签、解析与之后的核对都用这同一份字节"""
        d = within_root(Path(root), self.snapshot_root)
        manifest_raw = read_safe(d / "manifest.json", "页面快照缺少 manifest.json")
        self.verifier.verify(d, manifest_raw)
        return load_snapshot_dir(d, lexicon=self.lexicon, manifest_raw=manifest_raw)

    def install(self, snap: PageSnapshot) -> None:
        self.core.pages = snap  # 一次引用赋值：请求在开头取走快照引用，切换前后各自一致
        # PLT-19：缓存开着时换快照代号并清掉 page.get 条目。清空是正确性所必需的一层：同一个 commit 的两份快照内容可能不同
        # （对账扣页 held_pages 不同），只靠 commit 入键会串；另两层是键里的快照代号、入缓存前核对快照没被换掉（tools.py）。
        # 与上一行同在一段同步代码里，请求开头取到的快照与代号总是对得上
        cache = getattr(self.core, "cache", None)
        if cache is not None:
            cache.new_snapshot()
        self.stats.loads += 1
        self.stats.unverified_loads += self.verifier.name == "unverified"
        self.stats.commit = snap.commit
        self.stats.history = (self.stats.history + [snap.commit])[-20:]

    def load_initial(self, root: str | Path | None) -> PageSnapshot | None:
        """启动：给了目录就载入（失败抛出，服务拒绝启动）；否则读指针文件；都没有返回 None（page.get 回 501）"""
        target = Path(root) if root is not None else (read_pointer(self.pointer) if self.pointer is not None else None)
        if target is None:
            return None
        snap = self.load(target)
        self.install(snap)
        log.info("page snapshot loaded commit=%s pages=%d verification=%s", snap.short_commit, len(snap.pages), self.verifier.name)
        return snap

    async def reload(self, root: str | Path | None = None) -> bool:
        """载入 root（缺省读指针文件）并切换。返回是否切换成功；失败时旧快照照常服务"""
        async with self._lock:
            try:
                if root is not None:
                    target = Path(root)
                elif self.pointer is not None:
                    target = read_pointer(self.pointer)
                else:
                    raise SnapshotLoadError("没有配置快照指针文件，不能热切换")
                snap = await asyncio.to_thread(self.load, target)
            except SnapshotLoadError as e:
                self.stats.failures += 1
                self.stats.last_error = str(e)
                log.error("page snapshot reload failed, keeping commit=%s (failures=%d): %s",
                          (self.stats.commit or "-")[:12], self.stats.failures, e)
                return False
            except Exception as e:  # noqa: BLE001 任何意外都不能让旧快照停止服务；不记异常消息（可能带出路径）
                self.stats.failures += 1
                self.stats.last_error = f"unexpected {type(e).__name__}"[:64]
                log.error("page snapshot reload failed unexpectedly, keeping commit=%s (failures=%d)",
                          (self.stats.commit or "-")[:12], self.stats.failures)
                return False
            self.install(snap)
            log.info("page snapshot swapped commit=%s pages=%d verification=%s", snap.short_commit, len(snap.pages), self.verifier.name)
            return True

    def request_reload(self) -> None:
        """SIGHUP 处理器：排一个 reload 任务（不在信号处理器里做 IO）。没有配置指针文件时只记日志（SIGHUP 不再按缺省动作结束进程）"""
        if self.pointer is None:
            log.warning("SIGHUP ignored: KNOWLEDGE_PAGE_SNAPSHOT_POINTER not configured, page snapshot unchanged")
            return
        t = asyncio.get_running_loop().create_task(self.reload())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """关停时等进行中的 reload 做完（不留半截的线程任务；结果照常记计数）"""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
