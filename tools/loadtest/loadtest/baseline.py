"""一次压测：计划 → 本机一次性栈 → 合成家庭 → （可选）故障注入 → 负载 → 零泄漏核对 → 报告与调用记录（只含元数据）。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from . import dbstats, meter, outdir
from .arrivals import Profile, describe, digest, plan, requests, slot_counts
from .driver import Runner, SmsInbox
from .metrics import by_endpoint, by_stage, percentile
from .report import BANNER, render_markdown, to_json
from .seed import family, seed_families, skew_sql
from .stack import call_records, local_stack

EXPIRY_SKEW = "3 hours"  # 大于最长的预留有效期（诊断、批改 10 分钟，分钟类 30 分钟；mock_exam 150 分钟不在压测里）
# 报告里的供应商一律用合成代号（不写路由表里的供应商 ID 与模型标识）
VENDOR_CODES = {"fake-a": "代号A（境内，主）", "fake-b": "代号B（境内，备）", "fake-x": "代号X（模拟境外）"}


def coded(d: dict) -> dict:
    return {VENDOR_CODES.get(k, "代号?"): v for k, v in d.items()}


POOL_NOTE = ("学习服务 asyncpg 池 max 10（learning/app.py）、BFF pg 池 max 10（services/bff/src/main.ts）；"
             "批改经计量驱动桩运行 BFF meteredCall，另用 bff_app 池 max 20；计量旅程另用 bff_app 池 max 20")


async def run_baseline(profile: Profile, out: Path | None, *, provider_median_ms: float = 300.0, provider_p90_ms: float = 900.0,
                       faults: dict[str, dict] | None = None, scale_note: str | None = None, retry_scale: float = 1.0) -> dict:
    arrivals = plan(profile)
    planned = requests(profile, arrivals)
    slots = slot_counts(arrivals)
    fams = {k: [family(profile.seed, k, i) for i in range(n)] for k, n in slots.items()}
    from e2e_cohort.replay import load_zhixing_items

    zx_points = {k: v["points"] for k, v in load_zhixing_items().items()}
    async with local_stack(seed_value=profile.seed, students=slots["student"], provider_median_ms=provider_median_ms,
                           provider_p90_ms=provider_p90_ms, retry_scale=retry_scale) as st:
        seeded = await seed_families(st.admin, st.bff_pool, [f for fs in fams.values() for f in fs])
        for vendor, spec in (faults or {}).items():
            await st.set_fault(vendor, spec)
        inbox = SmsInbox(st.sms_inbox)
        inbox.start()
        runner = Runner(profile, arrivals, fams, st.bff_url, st.shell_origin, inbox, st.bff_pool, zx_points, st.harness_url)
        await dbstats.reset(st.admin, st.stat_statements)
        sampler = dbstats.ActivitySampler(st.admin)
        sampler.start()
        try:
            timing = await runner.run()
        finally:
            activity = await sampler.stop()
            await inbox.stop()
        top = await dbstats.top_queries(st.admin, st.stat_statements)
        await st.admin.execute(skew_sql(EXPIRY_SKEW))
        expired = await meter.expire_all(st.scheduler)
        leak = await meter.leak_report(st.admin, [f.student for fs in fams.values() for f in fs])
        provider = await st.provider_stats()
        records = call_records(st.learning_log)

    rec = runner.recorder
    wall = timing["wall_seconds"]
    report = {
        "banner": BANNER, "scale_note": scale_note, "profile": describe(profile),
        "arrivals": len(arrivals), "arrivals_by_journey": dict(Counter(a.kind for a in arrivals)), "seeded": seeded,
        "plan_digest": digest(planned), "executed_digest": digest(runner.issued),
        "wall_seconds": wall, "queue_wait_p95_ms": percentile(rec.queue_wait_ms, 0.95),
        "stages": by_stage(rec), "endpoints": by_endpoint(rec, wall), "skipped": dict(sorted(rec.skipped.items())),
        "provider": {"median_ms": provider_median_ms, "p90_ms": provider_p90_ms, "counts": coded(provider.get("counts", {}))},
        "faults": coded(faults or {}), "retry_scale": retry_scale,
        "db": {"activity": activity, "top_queries": top, "stat_statements": st.stat_statements, "pool_note": POOL_NOTE},
        "metering": {**leak, "skew": EXPIRY_SKEW, "expired_by_job": expired},
        "model_calls": {
            "total": len(records),
            "by_subject_task": dict(sorted(Counter(f"{r['subject']}/{r['task']}" for r in records).items())),
            "by_outcome": dict(sorted(Counter(r["outcome"] for r in records).items())),
        },
    }
    out = outdir.prepare(out, "starbuddy-loadtest")
    outdir.write_text(out / "baseline.json", to_json(report))
    outdir.write_text(out / "baseline.md", render_markdown(report))
    outdir.write_text(out / "call-records.jsonl", "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records))
    report["out"] = str(out)
    return report
