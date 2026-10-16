"""命令行（纯标准库；Makefile 用洞察服务的 venv 跑测试，运行本身任何 Python 3.11+ 都可以）：

    python -m kge_calibrate synth --out <目录> [--seed N] [--size N]
        生成合成输入：map-snapshot.json、mastery.csv、truth.json
    python -m kge_calibrate run --map <map-snapshot.json> --mastery <mastery.csv> --out <目录> [--config config.json]
        生成 calibration-report.json 与 worklist.md（只含聚合数；本步只接受 synthetic=true 的掌握状态表）

不连库、不联网、不调用模型；只写输出目录（必须不存在、为空，或只含本工具同名文件）。退出码：0 成功，2 输入或配置被拒绝。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import CONFIG_PATH, ConfigError, load_config
from .inputs import InputRefused, load_map, load_mastery
from .report import OutputRefused, run, write_files
from .synth import SEED, SIZE, render_inputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kge_calibrate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sy = sub.add_parser("synth", help="生成合成输入（地图快照、掌握状态表、埋入结构）")
    sy.add_argument("--out", required=True, type=Path)
    sy.add_argument("--seed", type=int, default=SEED)
    sy.add_argument("--size", type=int, default=SIZE)
    ru = sub.add_parser("run", help="前置边检验与隐藏依赖候选")
    ru.add_argument("--map", required=True, type=Path)
    ru.add_argument("--mastery", required=True, type=Path)
    ru.add_argument("--out", required=True, type=Path)
    ru.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "synth":
            if not 1 <= args.size <= 100_000:
                print("kge-calibrate: --size 必须在 1–100000 之间")
                return 2
            written = write_files(render_inputs(args.seed, args.size), args.out)
            print(f"kge-calibrate: 已写入合成输入 {args.out}（{len(written)} 个文件；合成，不是试点数据）")
            return 0
        cfg = load_config(args.config)
        mp = load_map(args.map)
        mastery = load_mastery(args.mastery, mp, cfg.mastery)
        written = write_files(run(mp, mastery, cfg), args.out)
    except ConfigError as e:
        print(f"kge-calibrate: 配置不合法：{e}")
        return 2
    except InputRefused as e:
        print(f"kge-calibrate: 拒绝输入：{e}")
        return 2
    except OutputRefused as e:
        print(f"kge-calibrate: 拒绝写出：{e}")
        return 2
    except OSError as e:
        print(f"kge-calibrate: 读写失败（{type(e).__name__}）")
        return 2
    print(f"kge-calibrate: 已写入 {args.out}（{len(written)} 个文件；只含聚合数，结论都是候选）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
