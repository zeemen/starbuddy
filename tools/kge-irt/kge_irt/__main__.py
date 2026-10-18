"""命令行（纯标准库；Makefile 用洞察服务的 venv 跑测试，运行本身任何 Python 3.11+ 都可以）：

    python -m kge_irt synth --out <目录> [--seed N] [--size N]
        生成合成输入：items.json、responses.csv、baseline.csv（合成，不是试点数据）
    python -m kge_irt run --items <items.json> --responses <responses.csv> --baseline <baseline.csv> --out <目录> [--config config.json]
        生成 irt-report.json 与 irt-report.md（只含题目级与节点级聚合；本步只接受 synthetic=true 的输入）

不连库、不联网、不调用模型；只写输出目录（必须不存在或为空，文件 0600 独占新建）。退出码：0 成功，2 输入、配置或输出被拒绝。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import analyze
from .config import CONFIG_PATH, ConfigError, load_config
from .inputs import InputRefused, load_baseline, load_items, load_responses
from .report import OutputRefused, run, write_files
from .synth import SEED, SIZE, render_inputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kge_irt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sy = sub.add_parser("synth", help="生成合成输入（题目快照、作答表、掌握估计基线）")
    sy.add_argument("--out", required=True, type=Path)
    sy.add_argument("--seed", type=int, default=SEED)
    sy.add_argument("--size", type=int, default=SIZE)
    ru = sub.add_parser("run", help="拟合 1PL / 2PL 并做时间切分评估")
    ru.add_argument("--items", required=True, type=Path)
    ru.add_argument("--responses", required=True, type=Path)
    ru.add_argument("--baseline", required=True, type=Path)
    ru.add_argument("--out", required=True, type=Path)
    ru.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "synth":
            if not 50 <= args.size <= 20_000:
                print("kge-irt: --size 必须在 50–20000 之间")
                return 2
            cfg = load_config()
            written = write_files(render_inputs(args.seed, args.size, cfg.synthetic_fit_fraction, cfg), args.out)
            print(f"kge-irt: 已写入合成输入 {args.out}（{len(written)} 个文件；合成，不是试点数据）")
            return 0
        cfg = load_config(args.config)
        iv = load_items(args.items)
        resp = load_responses(args.responses, iv, cfg.min_fit_fraction, cfg.max_fit_fraction)
        base = load_baseline(args.baseline, iv, resp)
        files = run(iv, resp, analyze(iv, resp, base, cfg), cfg, base.pseudonyms)
        written = write_files(files, args.out)
    except ConfigError as e:
        print(f"kge-irt: 配置不合法：{e}")
        return 2
    except InputRefused as e:
        print(f"kge-irt: 拒绝输入：{e}")
        return 2
    except OutputRefused as e:
        print(f"kge-irt: 拒绝写出：{e}")
        return 2
    except OSError as e:
        print(f"kge-irt: 读写失败（{type(e).__name__}）")
        return 2
    print(f"kge-irt: 已写入 {args.out}（{len(written)} 个文件；只含聚合数，是评估报告、不是替换依据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
