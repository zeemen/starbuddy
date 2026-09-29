"""导出已发布页面快照（spec 待人确认 #7）：把页面仓库主分支的指定提交导出为只读快照目录，知识服务只读它。

    python -m knowledge.compiler.snapshot --repo <页面仓库检出目录> --commit <提交号或分支> --out <新快照目录> [--today YYYY-MM-DD]

- 只读 Git 对象（git rev-parse / ls-tree / show），不改工作区，不访问远程；
- 导出前对该提交的全部页面跑程序体检（该提交里有 snapshots/current.yaml 就带上地图快照），有阻断即拒绝导出；
- 输出：pages/kp/<节点 ID>.md（与该提交逐字节相同）+ manifest.json（格式、40 位提交号、每页 page_version、map_version、sha256）；
- 先写到同级临时目录再改名（不会留下半个快照）；文件 0444、目录 0555；目标已存在则拒绝（快照不可变，换提交就换目录）。
退出码：0 完成，1 体检阻断，2 参数、仓库或页面错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

import yaml

from ..atoms import SnapshotError, parse_snapshot
from ..lint import lint_dir
from ..nodeid import is_node_id
from ..pages.format import PageFormatError, parse

FORMAT = "kge-page-snapshot/1"
MAP_SNAPSHOT_PATH = "snapshots/current.yaml"


class ExportError(RuntimeError):
    pass


class ExportBlocked(RuntimeError):
    def __init__(self, message: str, report: object) -> None:
        super().__init__(message)
        self.report = report


def _git(repo: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # 不继承会改变仓库位置的环境变量
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)
    if r.returncode != 0:
        raise ExportError(f"git {args[0]} 失败")
    return r.stdout


def resolve_commit(repo: Path, rev: str) -> str:
    if rev.startswith("-"):
        raise ExportError("提交号不合法")
    sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").decode().strip()
    if len(sha) != 40:
        raise ExportError("提交号不合法")
    return sha


def export_snapshot(repo: str | Path, rev: str, out_dir: str | Path, *, today: dt.date | None = None,
                    exported_at: dt.datetime | None = None) -> dict:
    """返回 manifest。体检有阻断时抛 ExportBlocked，其他问题抛 ExportError"""
    repo, out = Path(repo), Path(out_dir)
    if out.exists():
        raise ExportError("输出目录已存在（快照不可变，换提交就换目录）")
    sha = resolve_commit(repo, rev)
    names = [n for n in _git(repo, "ls-tree", "-r", "-z", "--name-only", sha, "--", "pages/").decode("utf-8").split("\0") if n]
    files: dict[str, bytes] = {}
    for name in sorted(names):
        p = PurePosixPath(name)
        if p.suffix != ".md" or p.name.lower() == "readme.md":
            continue
        if len(p.parts) != 3 or p.parts[1] != "kp" or not is_node_id(p.stem):
            raise ExportError(f"页面路径不合规范：{name}")
        files[name] = _git(repo, "show", f"{sha}:{name}")
    map_snapshot = None
    if MAP_SNAPSHOT_PATH in _git(repo, "ls-tree", "-r", "-z", "--name-only", sha, "--", MAP_SNAPSHOT_PATH).decode("utf-8").split("\0"):
        try:
            map_snapshot = parse_snapshot(yaml.safe_load(_git(repo, "show", f"{sha}:{MAP_SNAPSHOT_PATH}").decode("utf-8")))
        except (SnapshotError, yaml.YAMLError, UnicodeDecodeError):
            raise ExportError("地图快照无法解析") from None

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        entries = []
        for name, raw in files.items():
            try:
                page = parse(raw.decode("utf-8"))
            except (PageFormatError, UnicodeDecodeError):
                raise ExportError(f"页面无法解析：{name}") from None
            nid = PurePosixPath(name).stem
            h = page.header
            if h.get("page_id") != nid or h.get("node_id") != nid or h.get("page_type") != "kp":
                raise ExportError(f"页面头部与文件名不一致：{name}")
            dst = tmp / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(raw)
            entries.append({"page_id": nid, "path": name, "page_version": str(h.get("page_version")),
                            "map_version": str(h.get("map_version")), "sha256": hashlib.sha256(raw).hexdigest()})
        report = lint_dir(tmp / "pages", snapshot=map_snapshot, today=today)
        if report.blocks:
            where = "、".join(sorted({f"{i.check_type}@{i.page_id}" for i in report.blocks}))
            raise ExportBlocked(f"提交 {sha[:12]} 的页面有 {len(report.blocks)} 个体检阻断（{where}），拒绝导出", report)
        manifest = {"format": FORMAT, "commit": sha,
                    "exported_at": (exported_at or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"),
                    "map_snapshot": map_snapshot.map_version if map_snapshot else None, "pages": entries}
        (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for dirpath, _dirs, fnames in os.walk(tmp, topdown=False):
            for f in fnames:
                os.chmod(Path(dirpath) / f, 0o444)
            os.chmod(dirpath, 0o555)
        os.rename(tmp, out)
        return manifest
    except BaseException:
        for dirpath, _dirs, _f in os.walk(tmp):
            os.chmod(dirpath, 0o755)
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.compiler.snapshot", description="导出已发布页面快照（只读 Git 对象）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--today", help="体检用的日期（YYYY-MM-DD，缺省今天）")
    a = ap.parse_args(argv)
    try:
        today = dt.date.fromisoformat(a.today) if a.today else None
        m = export_snapshot(a.repo, a.commit, a.out, today=today)
    except ExportBlocked as e:
        print(f"导出快照：{e}")
        return 1
    except (ExportError, ValueError) as e:
        print(f"导出快照失败：{e}")
        return 2
    print(f"导出快照：提交 {m['commit'][:12]}，{len(m['pages'])} 页 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
