"""命令行：python -m governance run …（在 evals/persona 目录下运行；参数见 governance/run.py）。"""
from __future__ import annotations

import argparse
import sys

from .common import CODE_ROOT
from .run import main_run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="governance")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", default=str(CODE_ROOT))
    r.add_argument("--registry", default="packages/personas/registry.yaml")
    r.add_argument("--role")
    r.add_argument("--base")
    r.add_argument("--strict", action="store_true")
    r.add_argument("--out")
    r.add_argument("--no-baseline", action="store_true")
    r.add_argument("--json-only")
    a = ap.parse_args(argv)
    return main_run(a)


if __name__ == "__main__":
    sys.exit(main())
