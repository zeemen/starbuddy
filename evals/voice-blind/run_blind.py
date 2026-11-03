"""语音盲测评测入口（冲刺 4-I，specs/voice-blind-test-sprint4/plan.md 执行手册）。

两个子命令：
- sample：用三家合成假供应商跑合成清单，生成流程样例报告（只接受 synthetic: true 的清单）；
- score：读执行环境里各家的结果文件（<编号>.asr.jsonl / <编号>.pron.jsonl），生成报告。
  非合成清单的结果文件含转写原文，必须放在仓库外（境内执行环境），评测完按方案删除。

    python evals/voice-blind/run_blind.py sample --manifest evals/voice-blind/fixtures/manifest-synthetic.yaml \
        --out evals/voice-blind/reports/sample-synthetic
    python evals/voice-blind/run_blind.py score --manifest <清单.yaml> --results <结果目录> --out <报告目录> \
        [--key-sha256 <编号表 sha256>] [--seed voice-blind/1] [--zh-keep-punct] [--zh-no-nfkc] [--zh-latin-as-word] \
        [--en-case-sensitive] [--en-keep-punct] [--en-drop-apostrophe] [--en-no-split-hyphen]
退出码：0 生成成功；2 参数或输入错误（消息只含字段名与编号，不含文本）。
不联网、不读环境变量与密钥、不读音频。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from voiceblind import blind, fakes  # noqa: E402
from voiceblind.manifest import ManifestError, load  # noqa: E402
from voiceblind.report import DEFAULT_SEED, ReportPathError, evaluate, write_report  # noqa: E402
from voiceblind.stats import RESAMPLES  # noqa: E402
from voiceblind.text import EnNorm, ZhNorm  # noqa: E402
from voiceblind.vendor import VendorOutputError, load_results, run_asr, run_pron, write_results  # noqa: E402

SAMPLE_BLIND_SEED = "sample-synthetic"


def _inside(p: Path, root: Path) -> bool:
    p, root = p.resolve(), root.resolve()
    return p == root or root in p.parents


def _norms(a: argparse.Namespace) -> tuple[ZhNorm, EnNorm]:
    zh = ZhNorm(nfkc=not a.zh_no_nfkc, strip_punct=not a.zh_keep_punct, latin_as_word=a.zh_latin_as_word)
    en = EnNorm(lowercase=not a.en_case_sensitive, strip_punct=not a.en_keep_punct, keep_apostrophe=not a.en_drop_apostrophe,
                split_hyphen=not a.en_no_split_hyphen)
    return zh, en


def cmd_sample(a: argparse.Namespace) -> Path:
    m = load(a.manifest)
    if not m.synthetic:
        raise ManifestError("sample 只接受合成清单（synthetic: true）")
    asr_vendors, pron_vendors = fakes.build(m, a.blind_seed)
    codes = blind.assign_codes([v.vendor_id for v in asr_vendors], a.blind_seed)
    asr = {codes[v.vendor_id]: run_asr(v, m, seed=a.blind_seed, code=codes[v.vendor_id]) for v in asr_vendors}
    pron = {codes[v.vendor_id]: run_pron(v, m, seed=a.blind_seed, code=codes[v.vendor_id]) for v in pron_vendors}
    if a.results:
        rd = Path(a.results)
        if "gold" in [p.lower() for p in rd.resolve().parts]:
            raise ReportPathError("产物不得写进 gold/ 目录")
        for c in sorted(asr):
            write_results(rd, c, asr[c], pron.get(c))
    zh, en = _norms(a)
    rep = evaluate(m, asr, pron, zh=zh, en=en, seed=a.seed, resamples=a.resamples, blind_key_sha256=blind.key_digest(codes))
    md, _ = write_report(rep, a.out)
    return md


def cmd_score(a: argparse.Namespace) -> Path:
    m = load(a.manifest)
    rd = Path(a.results)
    if not m.synthetic and _inside(rd, ROOT):
        raise ManifestError("非合成清单的结果文件含转写原文，必须放在仓库外的境内执行环境里")
    asr, pron = load_results(rd)
    if not asr and not pron:
        raise VendorOutputError("结果目录里没有 <编号>.asr.jsonl / <编号>.pron.jsonl")
    zh, en = _norms(a)
    rep = evaluate(m, asr, pron, zh=zh, en=en, seed=a.seed, resamples=a.resamples, blind_key_sha256=a.key_sha256)
    md, _ = write_report(rep, a.out)
    return md


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="语音盲测评测（冲刺 4-I）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sample", "score"):
        p = sub.add_parser(name)
        p.add_argument("--manifest", required=True, help="输入清单 YAML（format: voice-blind-manifest/1）")
        p.add_argument("--out", required=True, help="报告输出目录（不得在 gold/ 下）")
        p.add_argument("--seed", default=DEFAULT_SEED, help="自助法种子（写进报告）")
        p.add_argument("--resamples", type=int, default=RESAMPLES, help="自助法重抽次数")
        p.add_argument("--zh-no-nfkc", action="store_true", help="中文不做全半角规范化")
        p.add_argument("--zh-keep-punct", action="store_true", help="中文标点计入 CER")
        p.add_argument("--zh-latin-as-word", action="store_true", help="中文里的拉丁字母、数字串按一个单位计")
        p.add_argument("--en-case-sensitive", action="store_true", help="英文区分大小写")
        p.add_argument("--en-keep-punct", action="store_true", help="英文保留标点")
        p.add_argument("--en-drop-apostrophe", action="store_true", help="英文去掉撇号（don't → dont）")
        p.add_argument("--en-no-split-hyphen", action="store_true", help="英文连字符不拆词")
        if name == "sample":
            p.add_argument("--blind-seed", default=SAMPLE_BLIND_SEED, help="编号种子（合成样例用固定值）")
            p.add_argument("--results", help="可选：把假供应商的结果文件写到这个目录（演示 score 的输入格式）")
        else:
            p.add_argument("--results", required=True, help="结果目录：<编号>.asr.jsonl、<编号>.pron.jsonl")
            p.add_argument("--key-sha256", help="编号表的 sha256（执行人提供，写进报告便于揭盲核对）")
    a = ap.parse_args(argv)
    if a.resamples < 100:
        print("语音盲测无法运行：--resamples 至少 100", file=sys.stderr)
        return 2
    try:
        md = cmd_sample(a) if a.cmd == "sample" else cmd_score(a)
    except (ManifestError, VendorOutputError, ReportPathError, OSError, ValueError) as e:
        print(f"语音盲测无法运行：{e}", file=sys.stderr)
        return 2
    print(f"已生成：{md.name}、{md.with_suffix('.json').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
