"""命令行：python -m gate1_evidence evidence --out <目录> [--seed N]

在洞察服务目录下用洞察服务的 venv 运行（Makefile 的 gate1-evidence 目标）：不连库、不联网，只写输出目录。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .cohort import SEED
from .config import ConfigError
from .evidence import PackRefused, build, render, write_pack

ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gate1_evidence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evidence", help="生成合成 Gate 1 证据包")
    ev.add_argument("--out", required=True, type=Path)
    ev.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    try:
        pack = build(args.seed)
    except ConfigError as e:
        print(f"gate1-evidence: 配置不合法：{e}")
        return 2
    meta = {"commit": _git("rev-parse", "HEAD"), "worktree_dirty": _git("status", "--porcelain", "--untracked-files=no") != ""}
    try:
        written = write_pack(render(pack, meta), args.out)
    except PackRefused as e:
        print(f"gate1-evidence: 拒绝写入：{e}")
        return 2
    print(f"gate1-evidence: 已写入 {args.out}（{len(written)} 个文件；合成，不是试点结论）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
