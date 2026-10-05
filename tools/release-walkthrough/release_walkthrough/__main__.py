"""命令行（用 services/learning 的 venv 运行；需要本机 postgresql-16 服务端程序、node 与 adapters/aya 的 npm ci）：

  python -m release_walkthrough run [--out DIR] [--check-sample DIR | --write-sample DIR]

- --out：原样产物目录，须是新目录或空目录（已存在且非空即退出码 2，一步也不走；缺省新建临时目录）。写入：walkthrough.json（每步摘要）、version-record.json / .csv（版本记录导出）、
  admission-decisions.json（平台导出的阿鸦准入决定文件）、admission-export-report.json、pack.rebuilt.json（重建后的内容包，
  约 3.5 万行，不入库）、c9-report.json、pack-rebuild-summary.json、admission-verify.json（逐题不比平台宽）；
  normalized/ 下是规范化后的同名文件（normalize.py：库里生成的 UUID、时间、摘要换成占位符）。
- --check-sample DIR：规范化产物与 DIR 下的样例逐字节比对，不同即退出码 1（make 目标默认比对仓库样例 tools/release-walkthrough/sample）。
- --write-sample DIR：把规范化产物写到 DIR（重生样例：make -C tools/release-walkthrough sample）。
退出码：0 走完且（若比对）一致；1 样例不一致；2 前提不满足或某一步失败。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .walk import NORMALIZED, main_run, normalize_dir


def out_not_usable(out: Path) -> str | None:
    """产物目录须是不存在的新目录或空目录：已存在且非空（或不是目录）返回原因。"""
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        return "已存在且不是目录"
    if out.is_dir() and any(out.iterdir()):
        return "已存在且非空"
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release_walkthrough")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="一次性本地库上走完数学地图版本 + 内容版本发布、准入变更与导出")
    r.add_argument("--out", help="产物目录（缺省新建临时目录）")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--check-sample", help="与该目录下的规范化样例比对")
    g.add_argument("--write-sample", help="把规范化产物写到该目录（重生样例）")
    a = p.parse_args(argv)
    if a.out:
        why = out_not_usable(Path(a.out))
        if why:
            print(f"release-walkthrough: 产物目录 {a.out} {why}（请指定新目录或空目录，免得新旧产物混在一起）", file=sys.stderr)
            return 2
    out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="release-walkthrough."))
    try:
        rc = main_run(out)
    except Exception as e:  # 任何一步失败：给出步骤里的说明，不回显库里的数据
        print(f"release-walkthrough: 失败（{e.__class__.__name__}：{str(e)[:600]}）", file=sys.stderr)
        return 2
    if rc:
        return rc
    norm = normalize_dir(out)
    (out / "normalized").mkdir(exist_ok=True)
    for name, text in norm.items():
        (out / "normalized" / name).write_text(text, encoding="utf-8")
    print(f"release-walkthrough: 走完，产物在 {out}", file=sys.stderr)
    if a.write_sample:
        d = Path(a.write_sample)
        d.mkdir(parents=True, exist_ok=True)
        for name, text in norm.items():
            (d / name).write_text(text, encoding="utf-8")
        print(f"release-walkthrough: 样例已写到 {d}", file=sys.stderr)
    if a.check_sample:
        d = Path(a.check_sample)
        bad = [n for n in NORMALIZED if not (d / n).exists() or (d / n).read_text(encoding="utf-8") != norm[n]]
        if bad:
            print(f"release-walkthrough: 与样例不一致：{', '.join(bad)}（对比 {out}/normalized 与 {d}）", file=sys.stderr)
            return 1
        print("release-walkthrough: 与样例一致", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
