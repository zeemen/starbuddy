"""离线全流程（spec 设计要点 5）：用例 → 1-F 模型路由的 test 环境（本地假供应商）→ 回复 → 评分。

只验证流程能跑通：假供应商回固定文本，分数本身没有意义。不接任何真实模型、不需要密钥、不联网。
调用走 T1（教学对话）、数据级别 L0（合成用例）；prompt 用占位名 diandian-placeholder@0.0.0，不起草人设 Prompt（必停项）。
用法（在 evals/persona 目录下）：python diandian/run.py [--runs 1] [--out 目录]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "services" / "learning"))
sys.path.insert(0, str(HERE.parent))

from learning.model_router import (DEFAULT_PATH, DataClass, FakeProvider, MemorySink, ModelRouter, ProviderReply, RouteRequest,  # noqa: E402
                                   Task, Usage, load)
from scorer.cases import holdout_ids, load_cases  # noqa: E402
from scorer.judge import load_lexicon  # noqa: E402
from scorer.report import score, to_markdown, write  # noqa: E402

PLACEHOLDER_PROMPT = ("diandian-placeholder", "0.0.0")
PLACEHOLDER_SYSTEM = "（占位：点点人设 Prompt 尚未起草，属必停项）"
SUBJECT_CODES = {"aya", "wenli", "zhixing", "echo"}


async def generate(cases: list[dict], runs: int) -> tuple[list[dict], MemorySink]:
    cfg = load(DEFAULT_PATH, "test")

    async def placeholder(target, messages, key):
        return ProviderReply(output="（假供应商占位回复）", usage=Usage(1, 1), reported_model=target.api_name)

    sink = MemorySink()
    router = ModelRouter(cfg, {vid: FakeProvider(v, placeholder) for vid, v in cfg.vendors.items()}, sink=sink)
    out: list[dict] = []
    for case in cases:
        subject = (case.get("context") or {}).get("subject")
        for run in range(1, runs + 1):
            messages = [{"role": "system", "content": PLACEHOLDER_SYSTEM}]
            turns, model_version = [], None
            for text in case["turns"]:
                messages.append({"role": "user", "content": text})
                res = await router.route(RouteRequest(
                    task=Task.T1, data_class=DataClass.L0, subject=subject if subject in SUBJECT_CODES else None,
                    prompt_template=PLACEHOLDER_PROMPT[0], prompt_version=PLACEHOLDER_PROMPT[1], messages=tuple(messages),
                    usage_id=f"eval-{uuid.uuid4()}", idempotency_key=f"eval-{uuid.uuid4()}"))
                messages.append({"role": "assistant", "content": res.output})
                turns.append({"text": res.output, "fields": {}})
                model_version = res.model_version
            out.append({"case_id": case["case_id"], "run": run, "model_version": model_version,
                        "prompt_version": f"prompt:{PLACEHOLDER_PROMPT[0]}@{PLACEHOLDER_PROMPT[1]}", "turns": turns})
    return out, sink


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cases = load_cases(HERE / "cases")
    responses, sink = asyncio.run(generate(cases, a.runs))
    out_dir = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="diandian-eval."))
    out_dir.mkdir(parents=True, exist_ok=True)
    # 回复只写到仓库外的临时目录（spec 待人确认 #5）；报告不含原文
    with open(out_dir / "responses.jsonl", "w", encoding="utf-8") as f:
        for r in responses:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rep = score(cases, responses, load_lexicon(HERE / "lexicon" / "forbidden.yaml"), holdout=set(holdout_ids(cases)),
                gold_present=(HERE / "gold").is_dir())
    write(rep, out_dir)
    calls = len(sink.records)
    ok = rep["runs"] == len(cases) * a.runs and calls == sum(len(c["turns"]) for c in cases) * a.runs \
        and all(r.outcome == "ok" for r in sink.records)
    print(to_markdown(rep).splitlines()[0])
    print(f"eval-persona: 离线全流程{'通过' if ok else '失败'}（{len(cases)} 条用例 × {a.runs} 次，经模型路由 test 环境 {calls} 次调用；"
          f"假供应商回复，分数无意义；输出在 {out_dir}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
