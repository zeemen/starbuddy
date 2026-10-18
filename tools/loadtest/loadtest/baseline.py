"""一次压测：计划 → 本机一次性栈 → 合成家庭 → （可选）故障注入 → 负载 → 零泄漏核对 → 报告与调用记录（只含元数据）。"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from . import dbstats, meter, outdir, phases
from . import slo as slo_mod
from .arrivals import Profile, describe, digest, plan, requests, slot_counts
from .driver import ModelBudget, Runner, SmsInbox
from .metrics import by_endpoint, by_stage, count_log, demote_unverified, gate_summary, path_summary, percentile

MONTHLY_ROUTE = "GET /students/{student_id}/monthly-plan"


def verify_monthly_expected(rec, log_lines) -> int:
    """审查 S3：家长月度计划的 503 UNAVAILABLE 只有在 BFF 访问日志里核对到 generate=not_configured 时才算预期；核对不到的改记为错误"""
    n = count_log(log_lines, MONTHLY_ROUTE, "generate", "not_configured", 503)
    return demote_unverified(rec, "parent.monthly_plan", "503 UNAVAILABLE", n, "503 UNAVAILABLE（未核对到 not_configured）")
from .report import BANNER, PEAK_BANNER, render_markdown, to_json
from .seed import family, seed_families, skew_sql
from .stack import call_records, local_stack


class StageMarks:
    """PLT-20：阶段边界各取一次 pg_stat_statements 快照与 BFF 访问日志大小（开始时一次、每个阶段结束一次），
    压测结束时补齐没到的边界（最后一个会话早于阶段结束时）。只在内存里，结束后只出汇总。"""

    def __init__(self, admin, stat_enabled: bool, bff_log: Path, durations: list[float]) -> None:
        self.admin, self.stat, self.log, self.durations = admin, stat_enabled, bff_log, durations
        self.snaps: list[dict] = []
        self.offsets: list[int] = []
        self.t0 = 0.0
        self._task: asyncio.Task | None = None

    async def _mark(self) -> None:
        self.snaps.append(await dbstats.snapshot_statements(self.admin, self.stat))
        self.offsets.append(self.log.stat().st_size if self.log.exists() else 0)

    async def start(self) -> None:
        self.t0 = time.monotonic()
        await self._mark()
        self._task = asyncio.create_task(self._loop())

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def stage_now(self) -> int:
        return phases.stage_at(self.elapsed(), self.durations)

    async def _loop(self) -> None:
        for end in phases.boundaries(self.durations)[:-1]:
            await asyncio.sleep(max(0.0, end - self.elapsed()))
            await self._mark()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        while len(self.snaps) < len(self.durations) + 1:
            await self._mark()

EXPIRY_SKEW = "3 hours"  # 大于最长的预留有效期（诊断、批改 10 分钟，分钟类 30 分钟；mock_exam 150 分钟不在压测里）
# 报告里的供应商一律用合成代号（不写路由表里的供应商 ID 与模型标识）
VENDOR_CODES = {"fake-a": "代号A（境内，主）", "fake-b": "代号B（境内，备）", "fake-x": "代号X（模拟境外）"}


def coded(d: dict) -> dict:
    return {VENDOR_CODES.get(k, "代号?"): v for k, v in d.items()}


POOL_NOTE = ("学习服务 asyncpg 池 max 10（learning/app.py）、BFF pg 池 max 10（services/bff/src/main.ts）；"
             "批改经计量驱动桩运行 BFF meteredCall，另用 bff_app 池 max 20；计量旅程另用 bff_app 池 max 20")


async def run_baseline(profile: Profile, out: Path | None, *, provider_median_ms: float = 300.0, provider_p90_ms: float = 900.0,
                       faults: dict[str, dict] | None = None, scale_note: str | None = None, retry_scale: float = 1.0,
                       subject_gate: bool = False, gate_config: Path | None = None, slo: slo_mod.SloConfig | None = None,
                       slo_strict: bool = False, peak_note: str | None = None, max_model_calls: int | None = None) -> dict:
    """peak_note 不为空 = --profile peak2x：报告首段标“本机合成，不是容量结论”，另给各阶段表，文件名 peak2x.md / peak2x.json"""
    arrivals = plan(profile)
    planned = requests(profile, arrivals)
    slots = slot_counts(arrivals)
    fams = {k: [family(profile.seed, k, i) for i in range(n)] for k, n in slots.items()}
    from e2e_cohort.replay import load_zhixing_items

    zx_points = {k: v["points"] for k, v in load_zhixing_items().items()}
    async with local_stack(seed_value=profile.seed, students=slots["student"], provider_median_ms=provider_median_ms,
                           provider_p90_ms=provider_p90_ms, retry_scale=retry_scale, subject_gate=subject_gate,
                           practice=profile.practice, gate_config=gate_config, journeys=profile.journeys) as st:
        seeded = await seed_families(st.admin, st.bff_pool, [f for fs in fams.values() for f in fs])
        for vendor, spec in (faults or {}).items():
            await st.set_fault(vendor, spec)
        inbox = SmsInbox(st.sms_inbox)
        inbox.start()
        runner = Runner(profile, arrivals, fams, st.bff_url, st.shell_origin, inbox, st.bff_pool, zx_points, st.harness_url,
                        budget=ModelBudget(max_model_calls))
        await dbstats.reset(st.admin, st.stat_statements)
        marks = StageMarks(st.admin, st.stat_statements, st.bff_log, [s.duration_s for s in profile.stages])
        await marks.start()
        sampler = dbstats.ActivitySampler(st.admin, stage_of=marks.stage_now)
        sampler.start()
        try:
            timing = await runner.run()
        finally:
            activity = await sampler.stop()
            await marks.stop()
            await inbox.stop()
        top = await dbstats.top_queries(st.admin, st.stat_statements)
        log_bytes = st.bff_log.read_bytes() if st.bff_log.exists() else b""
        await st.admin.execute(skew_sql(EXPIRY_SKEW))
        expired = await meter.expire_all(st.scheduler)
        leak = await meter.leak_report(st.admin, [f.student for fs in fams.values() for f in fs])
        provider = await st.provider_stats()
        records = call_records(st.learning_log)
        # 分学科并发闸门（PLT-16）：只取访问日志里 gate_* 枚举字段的汇总（临时目录随栈删除）
        gate = gate_summary(log_bytes.decode("utf-8", errors="replace").splitlines())

    rec = runner.recorder
    demoted = verify_monthly_expected(rec, log_bytes.decode("utf-8", errors="replace").splitlines())
    wall = timing["wall_seconds"]
    stage_rows = by_stage(rec)
    detail = phases.assemble(stage_rows, [s.label for s in profile.stages], [s.concurrency for s in profile.stages],
                             activity.get("by_stage"), marks.snaps if st.stat_statements else None,
                             phases.split_log(log_bytes, marks.offsets) if subject_gate else None)
    slo_result = slo_mod.evaluate(slo, rec.samples) if slo is not None else None
    report = {
        "banner": BANNER, "scale_note": scale_note, "profile": describe(profile),
        "arrivals": len(arrivals), "arrivals_by_journey": dict(Counter(a.kind for a in arrivals)), "seeded": seeded,
        "plan_digest": digest(planned), "executed_digest": digest(runner.issued),
        "wall_seconds": wall, "queue_wait_p95_ms": percentile(rec.queue_wait_ms, 0.95),
        "stages": stage_rows, "stage_detail": detail, "endpoints": by_endpoint(rec, wall), "skipped": dict(sorted(rec.skipped.items())),
        "peak2x": {"note": peak_note} if peak_note else None,
        "extras_check": {"paths": path_summary(rec), "notes": {f"{s}:{w}": n for (s, w), n in sorted(rec.notes.items())},
                         "monthly_plan_demoted": demoted} if profile.journeys else None,
        "model_budget": runner.budget.summary(),
        "slo": None if slo_result is None else {**slo_result, "strict": slo_strict},
        "provider": {"median_ms": provider_median_ms, "p90_ms": provider_p90_ms, "counts": coded(provider.get("counts", {}))},
        "faults": coded(faults or {}), "retry_scale": retry_scale,
        "subject_gate": {"enabled": subject_gate, "custom_config": gate_config is not None, "by_subject": gate},
        "db": {"activity": activity, "top_queries": top, "stat_statements": st.stat_statements, "pool_note": POOL_NOTE},
        "metering": {**leak, "skew": EXPIRY_SKEW, "expired_by_job": expired},
        "model_calls": {
            "total": len(records),
            "by_subject_task": dict(sorted(Counter(f"{r['subject']}/{r['task']}" for r in records).items())),
            "by_outcome": dict(sorted(Counter(r["outcome"] for r in records).items())),
        },
    }
    if peak_note:
        report["banner"] = PEAK_BANNER
    out = outdir.prepare(out, "starbuddy-loadtest")
    name = "peak2x" if peak_note else "baseline"
    outdir.write_text(out / f"{name}.json", to_json(report))
    outdir.write_text(out / f"{name}.md", render_markdown(report))
    outdir.write_text(out / "call-records.jsonl", "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records))
    report["out"] = str(out)
    return report
