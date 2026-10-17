"""命令行（在 services/learning/.venv 下，PYTHONPATH 含 tools/loadtest）：

  python -m loadtest plan     --stages small|full|'10:120,50:120' [--seed N]        只打印计划摘要与请求序列 digest（不起栈）
  python -m loadtest baseline --stages small [--out DIR] [--seed N] [--fault fake-a=unavailable] [--median-ms 300 --p90-ms 900]
                              [--subject-gate] [--with-practice]   分学科并发闸门与再练、复测场景（PLT-16）
  python -m loadtest cost     --records FILE [--out DIR] [--prices FILE] [--profile FILE]
  python -m loadtest dashboard --usage FILE --records FILE [--out DIR] [--prices FILE] [--profile FILE] [--config FILE]
                              成本仪表盘（PLT-17）：消耗聚合行 + 调用记录 → cost-dashboard.json / .md（只出聚合；本步只收合成样例）
  python -m loadtest dashboard-synth [--out DIR] [--seed N]   固定种子的合成队列（usage.csv、call-records.jsonl）

baseline 只在本机一次性栈上跑（规则 9）；预发压测见 RUNBOOK.md，需人批准。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .arrivals import PROFILES, Profile, describe, digest, parse_stages, plan, requests, slot_counts
from .cost import CONFIG
from .cost import run as run_cost
from .faults import MODES

MAX_SAFE = 20  # 开发期规模上限：超过需显式 --i-know（完整分阶段只在人手动传参时跑）


def _fault(text: str) -> tuple[str, dict]:
    vendor, _, mode = text.partition("=")
    mode, _, ratio = mode.partition(":")
    if mode not in MODES:
        raise argparse.ArgumentTypeError(f"fault mode must be one of {MODES}")
    spec: dict = {"mode": mode}
    if ratio:
        spec["error_ratio" if mode == "error_ratio" else "extra_latency_ms"] = float(ratio)
    return vendor, spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loadtest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "baseline"):
        p = sub.add_parser(name)
        p.add_argument("--stages", default="small", help=f"预置档 {sorted(PROFILES)} 或 '并发:秒,…'")
        p.add_argument("--seed", type=int, default=20260930)
        p.add_argument("--mean-session-s", type=float, default=6.0)
        p.add_argument("--think-s", type=float, default=0.2)
        p.add_argument("--with-practice", action="store_true",
                       help="学生旅程另走再练与复测的列表、取题（BFF 与学习服务各自打开合成的再练、复测；不开时请求序列不变）")
    b = sub.choices["baseline"]
    b.add_argument("--subject-gate", action="store_true",
                   help="BFF 打开分学科并发闸门（STARBUDDY_SUBJECT_GATE，config/concurrency.json 的合成默认值）；配合 --fault fake-a=slow:800 观察排队")
    b.add_argument("--gate-config", default=None,
                   help="与 config/concurrency.json 同格式的一份闸门配置（调小 max_inflight 更容易看到排队）；须同时给 --subject-gate")
    b.add_argument("--out", default=None, help="不给则写到新建的临时目录（mkdtemp）；给了则不能是符号链接、须属于当前用户")
    b.add_argument("--fault", action="append", type=_fault, default=[], help="例如 fake-a=unavailable、fake-a=error_ratio:0.3")
    b.add_argument("--median-ms", type=float, default=300.0)
    b.add_argument("--p90-ms", type=float, default=900.0)
    b.add_argument("--i-know", action="store_true", help="允许并发超过 20（完整分阶段）；只在本机")
    b.add_argument("--scale-note", default=None)
    b.add_argument("--retry-scale", type=float, default=1.0, help="BFF meteredCall 退避时长的比例（1 = 1/2/4/8 s）")
    c = sub.add_parser("cost")
    c.add_argument("--records", required=True)
    c.add_argument("--out", default=None, help="同 baseline --out")
    c.add_argument("--prices", default=str(CONFIG / "prices.example.yaml"))
    c.add_argument("--profile", default=str(CONFIG / "cost_profile.yaml"))
    d = sub.add_parser("dashboard")
    d.add_argument("--usage", required=True, help="消耗聚合行（cost-dashboard-usage/1，synthetic=true）")
    d.add_argument("--records", required=True, help="调用记录 JSON Lines（call-records 格式）")
    d.add_argument("--out", default=None, help="不存在或为空的目录（文件 0600 新建、不覆盖）；不给则新建临时目录，非合成输入必须给")
    d.add_argument("--prices", default=str(CONFIG / "prices.example.yaml"))
    d.add_argument("--profile", default=str(CONFIG / "cost_profile.yaml"))
    d.add_argument("--config", default=str(CONFIG / "cost_dashboard.yaml"))
    ds = sub.add_parser("dashboard-synth")
    ds.add_argument("--out", default=None, help="同 baseline --out")
    ds.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    if args.cmd == "dashboard":
        from .dashboard import InputRefused, OutputRefused, run_dashboard

        try:
            r = run_dashboard(Path(args.usage), Path(args.records), Path(args.prices), Path(args.profile), Path(args.config),
                              Path(args.out) if args.out else None)
        except InputRefused as e:
            print(f"cost-dashboard: 输入被拒绝：{e}", file=sys.stderr)
            return 2
        except OutputRefused as e:
            print(f"cost-dashboard: 不写出：{e}", file=sys.stderr)
            return 2
        print(json.dumps({"out": r["out"], "banner": r["banner"], "cohort": r["cohort"]}, ensure_ascii=False))
        return 0
    if args.cmd == "dashboard-synth":
        from . import dashboard_synth

        out = dashboard_synth.run(Path(args.out) if args.out else None,
                                  dashboard_synth.SEED if args.seed is None else args.seed)
        print(json.dumps({"out": str(out), "synthetic": True}, ensure_ascii=False))
        return 0
    if args.cmd == "cost":
        r = run_cost(Path(args.records), Path(args.prices), Path(args.profile), Path(args.out) if args.out else None)
        print(json.dumps({"out": r["out"], "rows": len(r["table"]), "example_prices": r["example_prices"]}, ensure_ascii=False))
        return 0

    profile = Profile(seed=args.seed, stages=parse_stages(args.stages), mean_session_s=args.mean_session_s, think_mean_s=args.think_s,
                      practice=args.with_practice)
    if args.cmd == "plan":
        arr = plan(profile)
        print(json.dumps({"profile": describe(profile), "arrivals": len(arr), "slots": slot_counts(arr),
                          "digest": digest(requests(profile, arr))}, ensure_ascii=False, indent=1))
        return 0
    if args.gate_config is not None and (not args.subject_gate or not Path(args.gate_config).is_file()):
        print("--gate-config 须同时给 --subject-gate，且是存在的文件", file=sys.stderr)
        return 2
    if max(s.concurrency for s in profile.stages) > MAX_SAFE and not args.i_know:
        print(f"并发超过 {MAX_SAFE}：完整分阶段请显式加 --i-know（只在本机一次性栈）", file=sys.stderr)
        return 2
    from .baseline import run_baseline
    from .stack import missing_prerequisites

    why = missing_prerequisites()
    if why:
        print(f"loadtest: {why}", file=sys.stderr)
        return 3
    r = asyncio.run(run_baseline(profile, Path(args.out) if args.out else None, provider_median_ms=args.median_ms, provider_p90_ms=args.p90_ms,
                                 faults=dict(args.fault), scale_note=args.scale_note,
                                 retry_scale=args.retry_scale, subject_gate=args.subject_gate,
                                 gate_config=Path(args.gate_config).resolve() if args.gate_config else None))
    ok = r["metering"]["zero_leak"] and r["plan_digest"] == r["executed_digest"]
    print(json.dumps({"out": r["out"], "zero_leak": r["metering"]["zero_leak"], "sequence_match": r["plan_digest"] == r["executed_digest"],
                      "requests": sum(d["count"] for d in r["endpoints"].values())}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
