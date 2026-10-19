"""负载驱动（spec 设计要点 1）：按 arrivals.plan 的到达时刻起旅程，每阶段会话并发不超过该阶段的并发数；只记耗时与状态类别。

- 学生、家长旅程经 BFF（真实会话：短信捕获桩的验证码登录、x-sb-session、x-csrf-token、Origin）；
- 批改：壳里还没有批改路由；驱动器经计量驱动桩（node/meter-harness.mts）直接运行 BFF 的 meteredCall：预留 → 学习服务 →
  确定成功结算 / 确定失败释放 / 结果未知同键退避重试，仍未知则不结算不释放（留给过期任务）；
- 计量旅程直接调计量函数（与 BFF 同一组函数、同一数据库角色）。
请求序列（到达、步骤、幂等键）逐条记下，压测后与计划比对（arrivals.digest）。
PLT-16：429 限流与 503 SUBJECT_BUSY（分学科并发闸门被拒）单列（仍算错误）；--with-practice 时学生旅程另走再练与复测的列表、取题。
PLT-20：--journeys 打开的可选场景接在学生 / 家长旅程末尾（请求体见 bodies.py）；本栈缺依赖时的预期结果按（状态码, 问题码）记为 expected。
PLT-21：写路径接上时（Runner.write_path）再练 / 复测的派生条目与派生票据不再算预期；写路径未接上时同 PLT-20。
PLT-22：写路径接上时取题没拿到真票据就不发作答（记 notes[(步骤, upstream_missing)]，该步算跳过，不进写路径一致性）。
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

import httpx

from e2e_cohort.cohort import BASE_DAY

from . import bodies, harness, meter
from .answers import Answers, for_student
from .arrivals import Arrival, Profile, sid, step_key
from .envelopes import grade_request
from .metrics import Recorder
from .scenarios import EXTRAS, TODAY_ORCH, PlannedRequest
from .seed import Family

PREFIX = "/bff/v0"
UPSTREAM_MISSING = "upstream_missing"   # PLT-22：取题没拿到真票据、未发作答（写路径接上时）


class StepFailed(Exception):
    """前置步骤失败：旅程其余步骤记为跳过"""


class SmsInbox:
    """短信捕获桩的收件箱（BFF 追加 JSON 行 {phone, code, at}）：后台增量读取，按号码取请求之后的最新验证码"""

    def __init__(self, path) -> None:
        self.path = path
        self.offset = 0
        self.codes: dict[str, list[tuple[float, str]]] = {}
        self._task: asyncio.Task | None = None

    def _read(self) -> None:
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        for line in data[: end + 1].splitlines():
            try:
                m = json.loads(line)
                at = _parse_ts(m["at"])
                self.codes.setdefault(m["phone"], []).append((at, m["code"]))
            except (ValueError, KeyError):
                continue

    async def _loop(self) -> None:
        while True:
            self._read()
            await asyncio.sleep(0.02)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def code_for(self, phone: str, after: float, timeout: float = 10.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            hits = [c for at, c in self.codes.get(phone, []) if at >= after]
            if hits:
                return hits[-1]
            await asyncio.sleep(0.02)
        raise StepFailed("no otp code")


def resume_check(status: int, got: str, original: str) -> str:
    """续做核对：200 且同一会话 → resumed_same；否则 resumed_new（新开了会话，会另预留额度）"""
    return "resumed_same" if status == 200 and got == original else "resumed_new"


def _json_or_none(r: httpx.Response | None) -> object:
    """2xx 的 JSON 响应体，其余为 None（只在内存里用来取票据、任务 ID，不记录）"""
    if r is None or not 200 <= r.status_code < 300:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _parse_ts(v) -> float:
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e12 else 1)
    from datetime import datetime
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def classify(status: int, code: str | None = None) -> str:
    if 200 <= status < 300:
        return "ok"
    if status == 402:
        return "rejected"  # 额度不足、宵禁等业务拒绝（不算错误，单列）
    # PLT-16：限流（429）与分学科并发闸门被拒（503 SUBJECT_BUSY）单列；仍按错误计（错误率口径不变），报表另给两列
    if status == 429:
        return "throttled"
    if status == 503 and code == "SUBJECT_BUSY":
        return "busy"
    return "http_4xx" if status < 500 else "http_5xx"


def problem_code(r: httpx.Response, statuses: frozenset[int] = frozenset({503})) -> str | None:
    """statuses 里的状态码（缺省只看 503）读问题码（只取 code 一个枚举值，不留响应内容）"""
    if r.status_code not in statuses:
        return None
    try:
        v = r.json()
    except ValueError:
        return None
    return v.get("code") if isinstance(v, dict) and isinstance(v.get("code"), str) else None


EXTRA_STEPS = frozenset(s for _, steps in EXTRAS.values() for s in steps)
MODEL_SUBJECTS = frozenset({"zhixing", "wenli"})  # 批改走模型的两科（数学是验证器）


@dataclass
class ModelBudget:
    """PLT-20 审查 S4：驱动器侧按模型调用次数的硬上限（--max-model-calls）。按计量驱动桩回报的学习服务调用次数累计
    （道法、语文批改各至少一次模型调用，重试另计）；到上限后不再发任何走模型的请求，之后到达的旅程也不再开始。None = 不设上限。"""
    limit: int | None = None
    used: int = 0
    stopped: bool = False

    def allows(self) -> bool:
        if self.limit is not None and self.used >= self.limit:
            self.stopped = True
        return not self.stopped

    def spend(self, n: int) -> None:
        self.used += max(0, n)
        if self.limit is not None and self.used >= self.limit:
            self.stopped = True

    def summary(self) -> dict:
        return {"limit": self.limit, "used": self.used, "stopped": self.stopped}


def outcome_of(r: httpx.Response, expect: frozenset[int] = frozenset(),
               expect_codes: frozenset[tuple[int, str]] = frozenset()) -> tuple[str, str]:
    """响应 → （类别, 报表里的状态标签）。expect：按状态码视为成功（PLT-16 的派生条目 404）；
    expect_codes（PLT-20）：按（状态码, 问题码）记为“预期”（本栈缺依赖时的预期结果，不算错误、单列）。
    给了 expect_codes 时标签带问题码（只取 code 枚举值）；否则同以前（只有 SUBJECT_BUSY 带码）。"""
    statuses = frozenset({503}) | frozenset(s for s, _ in expect_codes)
    code = problem_code(r, statuses)
    if r.status_code in expect:
        outcome = "ok"
    elif code is not None and (r.status_code, code) in expect_codes:
        outcome = "expected"
    else:
        outcome = classify(r.status_code, code)
    label = f"{r.status_code} {code}" if code is not None and (code == "SUBJECT_BUSY" or expect_codes) else str(r.status_code)
    return outcome, label


@dataclass
class Session:
    token: str
    csrf: str


@dataclass
class Runner:
    profile: Profile
    arrivals: list[Arrival]
    families: dict[str, list[Family]]
    bff_url: str
    shell_origin: str
    inbox: SmsInbox
    bff_pool: object  # asyncpg.Pool（bff_app 登录角色）
    zx_points: dict[str, list[dict]]
    harness_url: str = ""
    recorder: Recorder = field(default_factory=Recorder)
    issued: list[PlannedRequest] = field(default_factory=list)
    budget: ModelBudget = field(default_factory=ModelBudget)
    # PLT-21：本栈写路径可用（真知识服务、合成地图与题、到期条目）：再练 / 复测的取题与作答不再有“派生路径的预期”，
    # 取题与作答都按真路径计（派生的照发、按错误计、路径记 derived，真路径占比应为 100%）
    write_path: bool = False
    _start: float = 0.0

    # ---------------- 基础 ----------------
    def now(self) -> float:
        return time.monotonic() - self._start

    async def timed(self, stage: int, step: str, fn: Callable[[], Awaitable[httpx.Response]],
                    expect: frozenset[int] = frozenset(), expect_codes: frozenset[tuple[int, str]] = frozenset()) -> httpx.Response | None:
        t = time.monotonic()
        try:
            r = await fn()
        except httpx.TransportError as e:
            self.recorder.add(stage, step, (time.monotonic() - t) * 1000, "transport", type(e).__name__)
            return None
        outcome, label = outcome_of(r, expect, expect_codes)
        self.recorder.add(stage, step, (time.monotonic() - t) * 1000, outcome, label)
        return r

    async def timed_db(self, stage: int, step: str, fn: Callable[[], Awaitable[dict]], ok: set[str]) -> dict | None:
        t = time.monotonic()
        try:
            out = await fn()
        except Exception as e:  # noqa: BLE001 数据库异常只记类别
            self.recorder.add(stage, step, (time.monotonic() - t) * 1000, "http_5xx", f"db:{type(e).__name__}")
            return None
        outcome = out.get("outcome", "?")
        cls = "ok" if outcome in ok else ("rejected" if outcome in ("insufficient", "curfew", "curfew_soon", "daily_cap_reached",
                                                                    "retained_no_ai", "no_entitlement") else "http_4xx")
        self.recorder.add(stage, step, (time.monotonic() - t) * 1000, cls, f"outcome:{outcome}")
        return out

    def headers(self, s: Session | None, write: bool) -> dict:
        h = {"origin": self.shell_origin}
        if write:
            h["content-type"] = "application/json"
        if s:
            h["x-sb-session"] = s.token
            if write:
                h["x-csrf-token"] = s.csrf
        return h

    # ---------------- 共用步骤 ----------------
    async def login(self, a: Arrival, f: Family, run) -> Session:
        after = time.time() - 1.0
        r = await run("login.otp_request", lambda: self.bff.post(f"{PREFIX}/auth/otp/request", json={"phone": f.phone},
                                                                  headers=self.headers(None, True)))
        if r is None or r.status_code != 202:
            raise StepFailed("otp_request")
        code = await self.inbox.code_for(f.phone, after)
        r = await run("login.otp_verify", lambda: self.bff.post(f"{PREFIX}/auth/otp/verify", json={"phone": f.phone, "code": code},
                                                                 headers=self.headers(None, True)))
        if r is None or r.status_code != 200:
            raise StepFailed("otp_verify")
        b = r.json()
        return Session(b["session_token"], b["csrf_token"])

    async def get(self, run, step: str, path: str, s: Session) -> httpx.Response:
        r = await run(step, lambda: self.bff.get(PREFIX + path, headers=self.headers(s, False)))
        if r is None or r.status_code >= 300:
            raise StepFailed(step)
        return r

    # ---------------- 旅程 ----------------
    async def student(self, a: Arrival, run) -> None:
        f = self.families["student"][a.slot]
        ans = for_student(self.profile.seed, a.slot, self.zx_points)
        g = await self.login(a, f, run)
        r = await run("login.student_session", lambda: self.bff.post(f"{PREFIX}/students/{f.student}/student-session",
                                                                      headers=self.headers(g, True)))
        if r is None or r.status_code != 200:
            raise StepFailed("student_session")
        b = r.json()
        s = Session(b["session_token"], b["csrf_token"])
        today_step = TODAY_ORCH if "plan_events" in self.profile.journeys else "today"
        today = await self.get(run, today_step, f"/students/{f.student}/today", s)
        base = f"/students/{f.student}/diagnosis/sessions"
        r = await run("diagnose.start", lambda: self.bff.post(PREFIX + base, json={"subject": "aya", "idempotency_key": self.key(a, "diagnose.start")},
                                                               headers=self.headers(s, True)))
        if r is None or r.status_code >= 300:
            raise StepFailed("diagnose.start")
        session_id = r.json()["session_id"]
        view = (await self.get(run, "diagnose.current", f"{base}/{session_id}", s)).json()
        step = view.get("step") or {}
        item = step.get("item") or {}
        opts = item.get("options") or []
        answer = {"choice": [opts[0]["key"]]} if item.get("format") in ("single", "multi") and opts else {"text": "1"}
        r = await run("diagnose.answer", lambda: self.bff.post(
            f"{PREFIX}{base}/{session_id}/answer",
            json={"seq": int(step.get("seq") or 1), "answer": answer, "elapsed_sec": 30, "idempotency_key": self.key(a, "diagnose.answer")},
            headers=self.headers(s, True)))
        if r is None or r.status_code >= 300:
            raise StepFailed("diagnose.answer")
        r = await run("diagnose.pause", lambda: self.bff.post(f"{PREFIX}{base}/{session_id}/pause",
                                                               json={"idempotency_key": self.key(a, "diagnose.pause")},
                                                               headers=self.headers(s, True)))
        if r is None or r.status_code >= 300:
            raise StepFailed("diagnose.pause")
        for subject in ("aya", "zhixing", "wenli"):
            try:
                await self.grade(a, f, ans, subject)
            except StepFailed:
                pass  # 一科批改失败（例如模型主备全失效）：学生照常继续下一科与星图
        await self.get(run, "star_map", f"/students/{f.student}/star-map", s)
        nexts: dict[str, object] = {}
        if self.profile.practice:
            nexts = await self.practice_steps(a, f, s, run)
        await self.student_extras(a, f, s, run, today, session_id, nexts)

    async def practice_steps(self, a: Arrival, f: Family, s: Session, run) -> dict[str, object]:
        """PLT-16（--with-practice）：数学的再练与复测，各一次列表、一次取题（取题经 BFF 的分学科闸门）。
        列表里没有到期条目时用（种子、到达、步骤）派生的条目 ID 取题：请求照样经过闸门；写路径未接上时记为预期结果（404），
        写路径接上时（PLT-21，self.write_path）不设预期、按错误计，并记真 / 派生路径；
        四步总是都发出（请求序列可复现）。返回两次取题的响应体（PLT-20 作答场景从中取票据；不进报告）。"""
        out: dict[str, object] = {}
        for kind in ("practice", "retest"):
            base = f"/students/{f.student}/{kind}/math"
            r = await run(f"{kind}.queue", lambda: self.bff.get(PREFIX + base, headers=self.headers(s, False)))
            due = None
            if r is not None and r.status_code == 200:
                entries = r.json().get("entries") or []
                due = next((e.get("entry_id") for e in entries if isinstance(e, dict) and e.get("status") == "due"), None)
            eid = due or sid("entry", self.profile.seed, a.index, kind)
            r = await run(f"{kind}.next", lambda: self.bff.post(f"{PREFIX}{base}/entries/{eid}/next", headers=self.headers(s, True)),
                          frozenset() if due or self.write_path else frozenset({404}),
                          path=("real" if due else "derived") if self.write_path else None)
            out[kind] = _json_or_none(r)
        return out

    async def student_extras(self, a: Arrival, f: Family, s: Session, run, today: httpx.Response, session_id: str,
                             nexts: dict[str, object]) -> None:
        """PLT-20 学生旅程的可选场景（按 scenarios.EXTRAS 顺序；开了的场景每一步都发出，请求序列可复现）"""
        on = set(self.profile.journeys)
        stu = f"/students/{f.student}"
        if "practice_answer" in on:
            for kind in ("practice", "retest"):
                nb = nexts.get(kind)
                if self.write_path and bodies.ready_ticket(kind, nb) is None:
                    # PLT-22 审查：写路径接上时取题没拿到真票据（上游非 2xx 或没有可作答的题）就不再用派生票据去作答：
                    # 单列“上游未取到（未发作答）”，这一步记为跳过，不计入写路径一致性
                    self.recorder.note(f"{kind}.answer", UPSTREAM_MISSING)
                    continue
                body, codes = bodies.answer_request(kind, nb, self.profile.seed, a.index, expect_derived=not self.write_path)
                await run(f"{kind}.answer", lambda: self.bff.post(f"{PREFIX}{stu}/{kind}/math/answer", json=body,
                                                                  headers=self.headers(s, True)), expect_codes=codes,
                          path="real" if bodies.ready_ticket(kind, nb) is not None else "derived")
        if "plan_events" in on:
            derived = {st: sid("plan-task", self.profile.seed, a.index, st) for st in ("plan.done", "plan.skip")}
            keys = {st: self.key(a, st) for st in ("plan.done", "plan.skip")}
            for step, body, codes in bodies.plan_events(_json_or_none(today), derived, keys, bodies.rfc3339()):
                await run(step, lambda: self.bff.post(f"{PREFIX}{stu}/events", json=body, headers=self.headers(s, True)),
                          expect_codes=codes)
        # 只读场景失败不拦住后面的场景（失败照记）
        for name, path in (("my_plan", f"{stu}/my-plan"), ("star_map_v1", f"{stu}/star-map/v1")):
            if name in on:
                await run(name, lambda: self.bff.get(PREFIX + path, headers=self.headers(s, False)))
        if "diagnose_more" in on:
            base = f"{stu}/diagnosis/sessions"
            # 续做：同一学科已有暂停的会话时 BFF 不另预留，学习服务把它恢复为进行中（与开始同一路由、新幂等键）
            r = await run("diagnose.resume", lambda: self.bff.post(PREFIX + base, json={"subject": "aya",
                                                                                           "idempotency_key": self.key(a, "diagnose.resume")},
                                                                    headers=self.headers(s, True)))
            if r is None or r.status_code >= 300:
                raise StepFailed("diagnose.resume")
            got = _json_or_none(r)
            sid_ = got.get("session_id") if isinstance(got, dict) and isinstance(got.get("session_id"), str) else session_id
            # 审查 S1：续做应回 200 且是原会话；回了新会话（201 或不同 ID，例如原会话已结束或过期）单记 resumed_new，照样把新会话放弃掉
            self.recorder.note("diagnose.resume", resume_check(r.status_code, sid_, session_id))
            view = (await self.get(run, "diagnose.recheck", f"{base}/{sid_}", s)).json()
            body, codes = bodies.probe_body(view, self.key(a, "diagnose.probe"), bodies.rfc3339())
            await run("diagnose.probe", lambda: self.bff.post(f"{PREFIX}{base}/{sid_}/probe", json=body, headers=self.headers(s, True)),
                      expect_codes=codes)
            await run("diagnose.abandon", lambda: self.bff.post(f"{PREFIX}{base}/{sid_}/abandon",
                                                                json=bodies.abandon_body(self.key(a, "diagnose.abandon")),
                                                                headers=self.headers(s, True)))

    async def parent_extras(self, a: Arrival, f: Family, g: Session, run) -> None:
        """PLT-20 家长旅程的可选场景。月度计划（本月无快照时要调洞察服务生成，本栈不起洞察服务）回 503 UNAVAILABLE，先记为预期，
        压测后按 BFF 访问日志的 generate=not_configured 核对；预测：BFF 未接预测客户端，一律 503，只测拒绝路径。
        周报、档案页：本栈没有已生成的周报与档案页，回 200（report / page 为空）。"""
        on = set(self.profile.journeys)
        stu = f"/students/{f.student}"
        reads = (("monthly_plan", "parent.monthly_plan", f"{stu}/monthly-plan", bodies.EXPECT_NO_INSIGHT),
                 ("weekly_report", "parent.weekly_report", f"{stu}/weekly-report", frozenset()),
                 ("profile_pages", "parent.profile_page", f"{stu}/profile-pages/math", frozenset()),
                 ("prediction", "parent.prediction", f"{stu}/exams/{sid('exam', self.profile.seed, a.index)}/prediction",
                  bodies.EXPECT_PREDICTION_UNWIRED))
        for name, step, path, codes in reads:
            if name in on:
                await run(step, lambda: self.bff.get(PREFIX + path, headers=self.headers(g, False)), expect_codes=codes)

    async def parent(self, a: Arrival, run) -> None:
        f = self.families["parent"][a.slot]
        g = await self.login(a, f, run)
        await self.get(run, "parent.children", "/me", g)
        await self.get(run, "parent.child_star_map", f"/students/{f.student}/star-map", g)
        await self.get(run, "parent.child_balance", f"/students/{f.student}/balance", g)
        await self.get(run, "parent.child_enrollments", f"/students/{f.student}/enrollments", g)
        await self.parent_extras(a, f, g, run)

    async def metering(self, a: Arrival, run_db) -> None:
        f = self.families["meter"][a.slot]
        acct, stu = f.student_account, f.student
        k1, k2 = self.key(a, "meter.reserve_pair"), self.key(a, "meter.reserve")
        fp1 = meter.fingerprint(stu, "ai_active_time", k1)
        pair = await asyncio.gather(*(run_db("meter.reserve_pair", lambda: meter.reserve(
            self.bff_pool, acct, stu, "ai_active_time", k1, fp1, 5, None, "teach"), {"reserved", "existing"}) for _ in range(2)))
        usage = next((p["usage_id"] for p in pair if p and p.get("usage_id")), None)
        if usage is None:
            raise StepFailed("meter.reserve_pair")
        if not await run_db("meter.settle", lambda: meter.settle(self.bff_pool, acct, usage, 3), {"settled"}):
            raise StepFailed("meter.settle")
        await run_db("meter.settle_repeat", lambda: meter.settle(self.bff_pool, acct, usage, 3), {"settled"})
        r = await run_db("meter.reserve", lambda: meter.reserve(self.bff_pool, acct, stu, "ai_active_time", k2,
                                                                meter.fingerprint(stu, "ai_active_time", k2), 2, None, "teach"),
                         {"reserved"})
        if not r or not r.get("usage_id"):
            raise StepFailed("meter.reserve")
        await run_db("meter.release", lambda: meter.release(self.bff_pool, acct, r["usage_id"], "client_cancelled"), {"released"})

    # ---------------- 批改（经计量驱动桩运行 BFF 的 meteredCall） ----------------
    async def grade(self, a: Arrival, f: Family, ans: Answers, subject: str) -> None:
        step = f"grade.{subject}"
        if subject in MODEL_SUBJECTS and not self.budget.allows():
            raise StepFailed("model budget reached")  # 到上限：不再发任何走模型的请求（这一步记为跳过）
        key = self.key(a, step)
        item, text = {"aya": (ans.aya_item, ans.aya_text), "zhixing": (ans.zhixing_item, ans.zhixing_text),
                      "wenli": (ans.wenli_item, ans.wenli_text)}[subject]
        at = (BASE_DAY + timedelta(days=5, seconds=a.index)).isoformat()
        body = grade_request(subject, f.student, item, text, sid("attempt", self.profile.seed, a.index, subject), at)
        self.issued.append(PlannedRequest(a.index, self.profile.steps(a.kind).index(step), step, key))
        t = time.monotonic()
        try:
            out = await harness.metered_grade(self.harness, self.harness_url, account=f.student_account, student=f.student, key=key,
                                              body=body, item_ref=f"{subject}:{item}")
        except httpx.HTTPError as e:
            self.recorder.add(a.stage, step, (time.monotonic() - t) * 1000, "transport", type(e).__name__)
            raise StepFailed(step) from None
        status, calls = out["result"]["status"], out["learning_calls"]
        if subject in MODEL_SUBJECTS:
            self.budget.spend(int(calls))
        code = status if calls <= 1 else f"{status}（学习服务 {calls} 次）"
        self.recorder.add(a.stage, step, (time.monotonic() - t) * 1000, harness.classify(status), code)
        if status != "ok":
            raise StepFailed(step)

    def key(self, a: Arrival, step: str) -> str:
        return step_key(self.profile.seed, a, step, self.profile.run_id)  # type: ignore[return-value]

    # ---------------- 调度 ----------------
    async def journey(self, a: Arrival, sems: list[asyncio.Semaphore]) -> None:
        await asyncio.sleep(max(0.0, a.at_s - self.now()))
        t = time.monotonic()
        async with sems[a.stage]:
            self.recorder.queue_wait_ms.append((time.monotonic() - t) * 1000)
            steps = self.profile.steps(a.kind)
            done: set[str] = set()
            if self.budget.stopped:
                # 模型调用到了 --max-model-calls：之后到达的旅程一律不发（全部记为跳过，请求序列不一致 → 退出码 1）
                for step in steps:
                    self.recorder.skip(step)
                return

            async def think(step: str) -> None:
                i = steps.index(step)
                if i > 0 and a.think_s[i] > 0:
                    await asyncio.sleep(a.think_s[i])

            async def run(step: str, fn, expect: frozenset[int] = frozenset(), expect_codes: frozenset[tuple[int, str]] = frozenset(),
                          path: str | None = None):
                await think(step)
                if step not in done:
                    done.add(step)
                    self.issued.append(PlannedRequest(a.index, steps.index(step), step, self.key(a, step)))
                r = await self.timed(a.stage, step, fn, expect, expect_codes)
                if path is not None or step in EXTRA_STEPS:
                    # 可选场景：给了预期码 = 派生 / 预期路径（必须是预期结果），没给 = 真 ID / 真票据（必须 2xx）；不记内容。
                    # PLT-21：写路径可用时调用方直接给 path（派生路径不再有预期码，照样记为 derived）
                    oc = "transport" if r is None else outcome_of(r, expect, expect_codes)[0]
                    self.recorder.path(step, path or ("derived" if expect_codes else "real"), oc)
                return r

            async def run_db(step: str, fn, ok: set[str]):
                if step not in done:
                    await think(step)
                    done.add(step)
                    self.issued.append(PlannedRequest(a.index, steps.index(step), step, self.key(a, step)))
                return await self.timed_db(a.stage, step, fn, ok)

            try:
                if a.kind == "student":
                    await self.student(a, run)
                elif a.kind == "parent":
                    await self.parent(a, run)
                else:
                    await self.metering(a, run_db)
            except StepFailed:
                pass
            except Exception:  # noqa: BLE001 驱动器自身的异常：记一次并继续（不带内容）
                self.recorder.add(a.stage, "driver.exception", 0.0, "http_5xx", "driver")
            issued = {r.step for r in self.issued if r.arrival == a.index}
            for step in steps:
                if step not in issued:
                    self.recorder.skip(step)

    async def run(self) -> dict:
        limits = httpx.Limits(max_connections=1000, max_keepalive_connections=200)
        async with httpx.AsyncClient(base_url=self.bff_url, timeout=60, limits=limits, trust_env=False) as bff, \
                httpx.AsyncClient(timeout=600, limits=limits, trust_env=False) as hc:
            self.bff, self.harness = bff, hc
            sems = [asyncio.Semaphore(s.concurrency) for s in self.profile.stages]
            self._start = time.monotonic()
            t0 = 0.0
            for i, s in enumerate(self.profile.stages):
                self.recorder.stage_windows[i] = [t0, t0 + s.duration_s]
                t0 += s.duration_s
            await asyncio.gather(*(self.journey(a, sems) for a in self.arrivals))
            wall = self.now()
            last = max(self.recorder.stage_windows)
            self.recorder.stage_windows[last][1] = max(self.recorder.stage_windows[last][1], wall)
        return {"wall_seconds": round(wall, 2)}
