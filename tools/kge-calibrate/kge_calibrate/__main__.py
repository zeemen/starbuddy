"""命令行（纯标准库；Makefile 用洞察服务的 venv 跑测试，运行本身任何 Python 3.11+ 都可以）：

    python -m kge_calibrate synth --out <目录> [--seed N] [--size N]
        生成合成输入：map-snapshot.json、mastery.csv、truth.json、attempts.csv、errors.csv、pitfall-codes.json
    python -m kge_calibrate run --map <map-snapshot.json> --mastery <mastery.csv> --out <目录> [--config config.json]
                                [--attempts attempts.csv] [--errors errors.csv] [--pitfall-codes pitfall-codes.json]
                                [--taxonomy docs/mapping/error-taxonomy.md]
        生成 calibration-report.json 与 worklist.md（只含聚合数；本步只接受 synthetic=true 的输入）。
        第二步的输入都可缺省：没有 --errors 不出易错候选与难归类，没有 --attempts 不出拆分候选；
        --pitfall-codes 是教研给的易错点编码对照（公共层，临时口径，以 kge 的 error_type 为准），缺省时关联了易错点的节点一律记“易错点对照缺失”。
    python -m kge_calibrate codes --subject aya [--taxonomy …]
        打印错因分类表里状态“明确”的学科编码（PostgreSQL 数组字面量），供导出函数过滤待定与敏感编码

不连库、不联网、不调用模型；只写输出目录（必须不存在、为空，或只含本工具同名文件）。退出码：0 成功，2 输入或配置被拒绝。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import CONFIG_PATH, ConfigError, load_config
from .inputs import SUBJECT_PREFIX, InputRefused, load_map, load_mastery
from .report import OutputRefused, run, write_files
from .responses import Step2, load_attempts, load_errors, load_pitfall_codes
from .synth import SEED, SIZE, render_inputs
from .taxonomy import TAXONOMY_PATH, load_taxonomy


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
    ru.add_argument("--attempts", type=Path)
    ru.add_argument("--errors", type=Path)
    ru.add_argument("--pitfall-codes", dest="pitfall_codes", type=Path)
    ru.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    co = sub.add_parser("codes", help="打印错因分类表里状态“明确”的学科编码（导出函数的 p_usable_codes；公共层）")
    co.add_argument("--subject", required=True, choices=sorted(SUBJECT_PREFIX))
    co.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "codes":
            print("{" + ",".join(load_taxonomy(args.taxonomy).subject_codes(args.subject)) + "}")
            return 0
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
        tax = load_taxonomy(args.taxonomy) if (args.errors or args.pitfall_codes) else None
        step2 = Step2(tax, load_attempts(args.attempts, mp) if args.attempts else None,
                      load_errors(args.errors, mp, tax, cfg.pitfall.min_confidence) if args.errors and tax else None,
                      load_pitfall_codes(args.pitfall_codes, mp, tax) if args.pitfall_codes and tax else None)
        written = write_files(run(mp, mastery, cfg, step2), args.out)
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
