"""命令行：python -m scorer <validate|holdout|score|selftest>（在 evals/persona 目录下运行）。

  validate            校验用例格式、条数、留出集、只含合成数据
  holdout [--write]   按固定种子重新抽留出集（--write 写回 holdout.txt）
  score --responses F [--out D]   对一批回复评分，写 report.json 与 report.md
  selftest            用 fixtures/selftest.yaml 的合成回复核对评分器判得对不对
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cases import auto_judgable_ratio, holdout_ids, load_cases, read_holdout, validate
from .judge import load_lexicon
from .report import score, to_markdown, write
from .selftest import run_selftest

BASE = Path(__file__).resolve().parents[1] / "diandian"
CASES, SCHEMA, HOLDOUT, LEXICON = BASE / "cases", BASE / "schema" / "case.schema.json", BASE / "holdout.txt", BASE / "lexicon" / "forbidden.yaml"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scorer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    h = sub.add_parser("holdout")
    h.add_argument("--write", action="store_true")
    s = sub.add_parser("score")
    s.add_argument("--responses", required=True)
    s.add_argument("--out")
    sub.add_parser("selftest")
    a = ap.parse_args(argv)

    if a.cmd == "validate":
        errors = validate(CASES, SCHEMA, HOLDOUT, extra_files=(BASE / "fixtures" / "selftest.yaml", LEXICON))
        for e in errors:
            print(f"  {e}")
        if errors:
            print(f"eval-persona: 用例校验失败（{len(errors)} 处）")
            return 1
        cases = load_cases(CASES)
        ratio = auto_judgable_ratio(cases)
        if ratio < 0.8:
            print(f"eval-persona: 核心用例可自动判定的检查点只有 {ratio:.0%}，低于 80%")
            return 1
        print(f"eval-persona: 用例校验通过（{len(cases)} 条，九类，留出集 {len(read_holdout(HOLDOUT))} 条，核心可自动判定 {ratio:.0%}）")
        return 0
    if a.cmd == "holdout":
        ids = holdout_ids(load_cases(CASES))
        text = "# 留出集（固定种子分层抽 30%，由 python -m scorer holdout --write 生成；修订 Prompt 时不看这些用例）\n" + "\n".join(ids) + "\n"
        if a.write:
            HOLDOUT.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    if a.cmd == "score":
        cases = load_cases(CASES)
        responses = [json.loads(line) for line in Path(a.responses).read_text(encoding="utf-8").splitlines() if line.strip()]
        rep = score(cases, responses, load_lexicon(LEXICON), holdout=set(read_holdout(HOLDOUT)), gold_present=(BASE / "gold").is_dir())
        if a.out:
            write(rep, Path(a.out))
        print(to_markdown(rep))
        return 0
    if a.cmd == "selftest":
        return run_selftest(CASES, LEXICON, BASE / "fixtures" / "selftest.yaml")
    return 2


if __name__ == "__main__":
    sys.exit(main())
