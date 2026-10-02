"""合成联调的完整一轮：生成队列 → 起栈 → 建档 → 回放（诊断、批改、retest_task 驱动的复测）→ 断言 → 整轮重跑 → 断言零新增。

pytest（services/learning/tests/test_e2e_cohort.py）与命令行（python -m e2e_cohort）共用这里；返回的 Report 只含 ID、计数与编码。
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import GENERATOR_VERSION
from . import checks as C
from .cohort import Cohort, generate
from . import mastery as mastery_setup
from .replay import CallRecord, RetestTaskScheduler, RunLog, load_zhixing_items, replay_cohort, rerun, seed_accounts
from .scripted import ScriptedModel
from pgharness import LOGIN_ROLE

from .starmap import PAGE_VERSION as STARMAP_PAGE_VERSION
from .stack import ADAPTERS, ConnectionLog, adapter_db_drivers, running_stack

ROOT = Path(__file__).resolve().parents[3]
DB_ENV_MARKERS = ("DATABASE", "PGHOST", "PGUSER", "PGPASSWORD", "POSTGRES", "DSN")


@dataclass
class Report:
    cohort: Cohort
    checks: list[C.Check]
    events: list[dict]
    nodes: list[dict]
    retests: list[dict]
    metadata: dict
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failed(self) -> list[C.Check]:
        return [c for c in self.checks if not c.passed]


def population(cohort: Cohort) -> C.Population:
    return C.Population([C.Member(s.student_id, s.account_id, s.household_id, (s.guardian_id,)) for s in cohort.students],
                        [cohort.unlinked_guardian_id])


def expected_evidence(log: RunLog) -> dict:
    out: dict[tuple[str, str], set[str]] = {}
    for c in log.calls:
        if c.status == 200:
            out.setdefault((c.student_id, c.subject), set()).update(c.summary.get("evidence_ids", []))
    return out


def check_calls(log: RunLog) -> C.Check:
    bad = [{"label": c.label, "step": c.step_id, "status": c.status, "code": c.summary.get("code")} for c in log.calls if c.status != 200]
    by = {}
    for c in log.calls:
        k = f"{c.subject}/{c.method}/{c.purpose}"
        by[k] = by.get(k, 0) + 1
    return C.Check("all_calls_succeeded", not bad and bool(log.calls),
                   {"calls": len(log.calls), "by_subject_method_purpose": dict(sorted(by.items())), "failed": bad})


def check_adapters_isolated(procs) -> C.Check:
    """适配器进程拿不到任何数据库连接信息，也不持有模型供应商配置（规则 1、ADR 04）；package.json 里没有数据库驱动"""
    leaks = sorted(f"{s}:{k}" for s, p in procs.items() for k in p.env
                   if any(m in k.upper() for m in DB_ENV_MARKERS) or k.startswith("MODEL_") or "API_KEY" in k)
    drivers = {s: d for s, d in adapter_db_drivers().items() if d}
    return C.Check("adapters_have_no_db_or_vendor_config", not leaks and not drivers,
                   {"adapters": sorted(procs), "env_leaks": leaks, "db_drivers_in_package_json": drivers})


async def check_db_clients(conn, connlog: ConnectionLog, allowed: tuple[str, ...]) -> C.Check:
    """联调期间连过库的只有学习服务登录角色与管理员（连接日志 + 结束时的 pg_stat_activity）"""
    async with conn.transaction(readonly=True):
        active = {r[0] for r in await conn.fetch(
            """SELECT DISTINCT usename::text FROM pg_stat_activity
                WHERE datname = current_database() AND backend_type = 'client backend' AND usename IS NOT NULL""")}
    logged = connlog.users()
    extra = sorted((active | logged) - set(allowed))
    return C.Check("db_clients_only_learning_and_admin", not extra and bool(logged), {
        "allowed": sorted(allowed), "logged_users": sorted(logged), "active_users": sorted(active), "unexpected": extra})


async def check_retest(conn, cohort: Cohort, log: RunLog, scheduler: RetestTaskScheduler, explained: dict) -> C.Check:
    """复测由 platform.retest_task 驱动（KGE-04 第二步）：每条执行的复测都对应一条到期的复测轮任务、经 grade 契约接口 200；
    到期的复测轮任务全部执行（或因没有可选的题计入 no_item）；有复测证据（delayed_retest）的学生恰是执行过复测的学生。
    合成配置下到不了复测（没有任务）时 0 次复测也是通过，explained 给出原因（不编造复测）。"""
    async with conn.transaction(readonly=True):
        rows = await conn.fetch(
            """SELECT student_id::text AS sid, count(DISTINCT evidence_id) AS n FROM platform.evidence_event
                WHERE student_id = ANY($1::uuid[]) AND evidence_kind = 'delayed_retest' GROUP BY 1""",
            [s.student_id for s in cohort.students])
        open_rounds = await conn.fetchval(
            """SELECT count(*) FROM platform.retest_task WHERE student_id = ANY($1::uuid[]) AND reason IN ('scheduled', 'early')""",
            [s.student_id for s in cohort.students])
    got = {r["sid"] for r in rows}
    ran = {r["student_id"] for r in log.retests}
    bad = [r for r in log.retests if r["scheduler"] != "retest_task" or r["status"] != 200 or not r["source_step"].startswith("retest_task:")]
    interrupted = [s.label for s in cohort.students if s.pattern == "interrupted" and s.student_id in got]
    passed = not bad and got == ran and not interrupted and (bool(log.retests) or open_rounds == 0)
    detail = {"scheduler": "retest_task", "reached": bool(log.retests), "reached_count": len(log.retests),
              "note": "KGE-04 第二步真实调度：grade 落库后同步重算掌握、同一事务建复测轮任务；联调把时钟推进到 due_at 后执行，"
                      "执行经同一 grade 契约接口，ingest.purpose = retest",
              "retest_calls": len(log.retests), "retest_round_tasks": int(open_rounds), "no_item": scheduler.no_item,
              "students_with_retest_evidence": len(got), "evidence_without_task": sorted(got - ran), "failed_or_unmarked": len(bad),
              "interrupted_with_retest": interrupted, "mastery": explained}
    if not log.retests:
        ex = explained.get("excluded_reasons", {})
        detail["not_reached"] = {
            "retest_round_tasks": int(open_rounds), "node_key_records": explained.get("node_key_records"),
            "evidence_counted": explained.get("evidence_counted"), "top_excluded_reason": max(ex, key=ex.get) if ex else None,
            "note": "没有复测轮任务就不执行复测（不编造）；原因见 mastery.excluded_reasons（例如内容未准入 → 判分待复核 → pending_review 不计入）"}
    return C.Check("retest_via_retest_task", passed, detail)


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _openapi_version(name: str) -> str:
    text = (ROOT / "packages/contracts/openapi" / name).read_text(encoding="utf-8")
    m = re.search(r"^info:\n(?:  .*\n)*?  version: ([^\s]+)", text, re.M)
    return m.group(1) if m else "unknown"


def _pack_version(subject: str) -> str:
    import json
    p = json.loads((ADAPTERS / subject / "content/pack.json").read_text(encoding="utf-8"))
    return f"{p['pack_name']}@{p['pack_version']}"


def metadata(cohort: Cohort, procs) -> dict:
    return {
        "notice": "合成联调，不是内测结论",
        "commit": _git("rev-parse", "HEAD"),
        "worktree_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "contracts": {"plugin-api": _openapi_version("plugin-api.v1.0.yaml"),
                      "learning-internal": _openapi_version("learning-internal.v1.yaml"),
                      "request_contract_version": "1.0.0"},
        "adapters": {s: {"adapter_version": p.capabilities.get("adapter_version"),
                         "capabilities_contract_version": p.capabilities.get("contract_version"),
                         "content_pack": _pack_version(s)} for s, p in sorted(procs.items())},
        "cohort": {"generator": GENERATOR_VERSION, "seed": cohort.seed, "students": len(cohort.students),
                   "digest": hashlib.sha256(cohort.to_json().encode()).hexdigest()},
        "retest_scheduler": "retest_task（KGE-04 第二步真实调度；联调合成掌握配置：数学 synthetic-test 未签字、rung0 间隔 0，知行与文砺不配置）",
        "model_route": "test 环境假供应商（合成判定），不是任何真实模型",
        "english": "未纳入（开口成章适配器已搁置，specs/adapter-echo-sprint3/lessons.md）",
        # 与是否截图无关的固定内容（样例逐字节比对）；截图的状态与对账在 starmap/starmap.json，失败时 write_pack 另加 status = failed
        "starmap_screenshot": {"record": "starmap/starmap.json", "page_version": STARMAP_PAGE_VERSION,
                               "provenance": "starmap/provenance.md",
                               "how": "make gate0-evidence GATE0_STARMAP=1 时生成（status = captured / failed）；未带时不截图、不删已有截图"},
    }


async def run_synthetic(pg, *, register: bool = True) -> Report:
    zx_items = load_zhixing_items()
    cohort = generate({k: v["points"] for k, v in zx_items.items()})
    model = ScriptedModel(zx_items)
    pop = population(cohort)
    async with running_stack(pg, model, register=register) as st:
        await seed_accounts(st.admin, cohort)
        scheduler = RetestTaskScheduler(st.app_pool)
        log = await replay_cohort(st.api, cohort, model, scheduler)
        explained = await mastery_setup.explanation(st.app_pool, [s.student_id for s in cohort.students], mastery_setup.config())
        results = [check_calls(log), check_adapters_isolated(st.adapters)]
        async with st.admin.acquire() as conn:
            results.append(await C.check_coexistence(conn, pop, expected_evidence(log)))
            results.append(await C.check_no_private_copies(conn, pop))
            results.append(await C.check_guardian_links_backed(conn, pop))
            results.append(C.check_grade_envelopes(st.transport.records))
            results.append(await C.check_grade_rows(conn, pop))
            results.append(await check_retest(conn, cohort, log, scheduler, explained))
            results.append(await C.check_isolation(conn, pop))
            before = await C.snapshot_counts(conn, pop)
            calls_before = st.transport.calls
            again = await rerun(st.api, log)
            after = await C.snapshot_counts(conn, pop)
            keys_ok = all(c.usage_id and c.idempotency_key for c in log.calls)
            results.append(C.check_rerun(before, after, replayed=sum(1 for c in again if c.replayed and c.status == 200),
                                         total=len(again), adapter_calls=st.transport.calls - calls_before,
                                         first_run_keys_ok=keys_ok))
            same = all(a.summary.get("evidence_ids") == b.summary.get("evidence_ids") for a, b in zip(log.calls, again))
            results.append(C.Check("rerun_same_result", same, {"compared": len(again)}))
            results.append(await check_db_clients(conn, st.connlog, (LOGIN_ROLE, "postgres")))
            events = await C.event_counts(conn, pop)
            nodes = await C.node_summary(conn, pop)
        meta = metadata(cohort, st.adapters)
    return Report(cohort, results, events, nodes, log.retests, meta, log.calls)
