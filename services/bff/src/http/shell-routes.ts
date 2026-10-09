// 统一壳 H4 的读路由与家长的两类写路由（specs/unified-shell-sprint4/spec.md 第 3、5、6、8、9、10 节）：
//   选科：GET 学科目录与选科状态（session）；POST 开通 / 暂停（guardian，只经 enroll_subject / pause_subject，BFF 不写 subject_enrollment）；
//   今日计划：GET（session）→ 学习服务 POST /internal/v1/plan → 各科适配器 /plan；推荐理由按判断显示（规则 11）；
//   额度与时长：GET（session），时长全部来自 platform.meter_guard_status（BFF 不重算单日上限、宵禁、已用分钟）；
//   时长设置：GET（session，学生只读）/ PUT（guardian，只经 set_time_guard：只能调低、只能收紧）；
//   连续使用提醒：GET / POST 确认休息（student）。
// 身份只来自会话；路径里的 student_id 只作查询条件，授权交给 RLS 与 SECURITY DEFINER 函数：无权与不存在同一个 404。
// 这里没有任何消耗类入口（不调 reserve / settle / meteredCall / meter_practice），也没有调用 insight.dispute / reconcile /
// export / erase 的路由（冲刺 5 的信任界面在 trust-routes.ts，挂在功能开关上、默认不注册；tests/shell-pages.integration.test.ts 断言）。
import type { Pool } from 'pg';
import { withAccount } from '../metering/db.ts';
import { loadShellConfig, type ShellConfig } from '../shell/config.ts';
import type { Audience } from '../shell/insight-client.ts';
import type { PlanSection, PlanSubject } from '../shell/plan-client.ts';
import { MemoryRestStore, type RestStore } from '../shell/rest.ts';
import { linkIdFor, nodeClaims, resolveStatements, templateRef, type ShownStatement, type StatementJob } from '../shell/statements.ts';
import { P } from './problems.ts';
import { isLocalEnv, type RunEnv } from './config.ts';
import type { Ctx, Handler, Query, Reply, Route, ShellDeps } from './routes.ts';
import { rateKey } from './secrets.ts';

const json = (status: number, v: unknown): Reply => ({ status, body: JSON.stringify(v) });
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const pgCode = (e: unknown): string => (e && typeof e === 'object' && 'code' in e ? String((e as { code: unknown }).code) : '');
const student = (ctx: Ctx): string => ctx.params['student_id']!;
const audienceOf = (ctx: Ctx): Audience => (ctx.session!.kind === 'student' ? 'student' : 'guardian');

let defaultConfig: ShellConfig | null = null;
const shellConfig = (deps: ShellDeps): ShellConfig => deps.shell?.config ?? (defaultConfig ??= loadShellConfig());
const restStores = new WeakMap<ShellDeps, RestStore>();
const restStore = (deps: ShellDeps): RestStore => {
  if (deps.shell?.rest) return deps.shell.rest;
  let s = restStores.get(deps);
  if (!s) {
    s = new MemoryRestStore(deps.config.session.student.absolute_seconds * 1000);
    restStores.set(deps, s);
  }
  return s;
};
/** BFF 的请求时钟（deps.now，测试注入）：推算当前年级（platform.current_grade）等按“此刻”取值的读点都用它，不用数据库的 now() */
export const asOf = (deps: ShellDeps): Date => (deps.now ? deps.now() : new Date());
const nowMs = (deps: ShellDeps): number => asOf(deps).getTime();

/**
 * 该会话能读到的学生档案（RLS：学生本人或已绑定家长）；读不到 = 无权或不存在，同一个 404。
 * 年级是推算值（迁移 0045，specs/policy-decisions-v0.9.1 第 1 节）：platform.current_grade(届别, 时刻)，时刻取 BFF 的请求时钟
 * （deps.now，测试注入），不读存下的建档年级；不在 7–9（未入学或已毕业）时为 null。
 */
async function profile(q: Query, id: string, at: Date): Promise<{ grade: number | null } | null> {
  const { rows } = await q<{ grade: number | null }>(
    'SELECT platform.current_grade(s.exam_cohort, $2::timestamptz) AS grade FROM platform.student_profile s WHERE s.id = $1', [id, at]);
  return rows[0] ?? null;
}

// ---------------------------------------------------------------- 选科（第 5 节）

/** 学科目录的显示顺序（英语搁置，planned 不列） */
const ORDER = ['math', 'chinese', 'politics'];

interface CatalogRow { code: string; product: string; status: 'beta' | 'live'; is_core: boolean; enrollment: 'active' | 'paused' | null; has_content: boolean }

/**
 * 学科目录按当前年级筛（subject_catalog.grades）。年级为 null（已毕业或未入学）时没有可新选的学科，
 * 但已开通、已暂停的学科照样列出（家长要能看到并暂停它们；specs/policy-decisions-v0.9.1 PR3 #10）
 */
async function catalogFor(q: Query, id: string, grade: number | null): Promise<CatalogRow[]> {
  const { rows } = await q<CatalogRow>(
    `SELECT c.code, c.product::text AS product, c.status, c.is_core, e.status::text AS enrollment,
            EXISTS (SELECT 1 FROM platform.curriculum_version v WHERE v.subject = c.product AND v.status = 'published') AS has_content
       FROM platform.subject_catalog c
       LEFT JOIN platform.subject_enrollment e ON e.student_id = $1 AND e.subject = c.product
      WHERE c.status <> 'planned' AND c.product IS NOT NULL
        AND ($2::smallint = ANY (c.grades) OR ($2::smallint IS NULL AND e.status IS NOT NULL))
      ORDER BY array_position($3::text[], c.code) NULLS LAST, c.code`, [id, grade, ORDER]);
  return rows;
}

const enrollments: Handler = async (ctx) => {
  const id = student(ctx);
  const p = await profile(ctx.q, id, asOf(ctx.deps));
  if (!p) return problem(P.NOT_FOUND);
  // 口径说明只做展示：当前主订阅（与 meter_main_subscription 同一取法：有效、非附加、计量时钟落在周期内；时钟取 meter_guard_status
  // 的 as_of，BFF 没有 meter_now 的执行权）配置里的 core_subjects / elective_slots。能不能选只由 enroll_subject 判断（长期由库函数预判，lessons）
  const g = await guardStatus(ctx.q, id);
  if (!g) return problem(P.NOT_FOUND);
  const { rows: subs } = await ctx.q<{ source: string; core: string | null; elective: string | null; max_active: string | null }>(
    `SELECT s.source,
            (SELECT f.value_code FROM platform.plan_feature f WHERE f.config_version = s.config_version AND f.tier = s.tier AND f.feature_code = 'core_subjects') AS core,
            (SELECT f.value_code FROM platform.plan_feature f WHERE f.config_version = s.config_version AND f.tier = s.tier AND f.feature_code = 'elective_slots') AS elective,
            (SELECT f.value_code FROM platform.plan_feature f WHERE f.config_version = s.config_version AND f.tier = s.tier AND f.feature_code = 'max_active_subjects') AS max_active
       FROM platform.subscription s
      WHERE s.student_id = $1 AND s.status IN ('active', 'grace') AND s.source <> 'addon'
        AND $2::timestamptz >= s.period_start AND $2::timestamptz < s.period_end
      ORDER BY s.period_start DESC LIMIT 1`, [id, g.as_of]);
  const sub = subs[0] ?? null;
  const rows = await catalogFor(ctx.q, id, p.grade);
  return json(200, {
    student_id: id,
    // 当前年级不在 7–9 时为假：列表里只有已开通、已暂停的学科，只能暂停，新选回 409 GRADE_OUT_OF_RANGE
    can_enroll: p.grade !== null,
    rules: sub ? { core_subjects: sub.core, elective_slots: sub.elective, max_active_subjects: sub.max_active } : null,
    subjects: rows.map((r) => ({
      subject: r.code, is_core: r.is_core, catalog_status: r.status,
      // 只用于显示“暂未开放”标签（beta 只对试点订阅开放、没有已发布内容版本）；不据此隐藏开通按钮，结果以 enroll_subject 的 outcome 为准
      open: r.has_content && (r.status === 'live' || sub?.source === 'pilot_virtual'),
      enrollment: r.enrollment,
    })),
  });
};

export async function productOf(q: Query, code: string): Promise<string | null> {
  const { rows } = await q<{ product: string }>(
    `SELECT product::text AS product FROM platform.subject_catalog WHERE code = $1 AND status <> 'planned' AND product IS NOT NULL`, [code]);
  return rows[0]?.product ?? null;
}

const ENROLL_KEYS = ['outcome', 'slot', 'limit', 'max_active_subjects'];
const subjectOp = (fn: 'enroll_subject' | 'pause_subject'): Handler => async (ctx) => {
  const product = await productOf(ctx.q, ctx.params['subject']!);
  if (!product) return problem(P.NOT_FOUND);  // 英语（planned）与不认识的学科码：与“不存在”相同
  if (fn === 'enroll_subject') {
    // 当前年级（届别按请求时钟推算）不在 7–9：不能新选，只能暂停（PR3 #10）。读不到档案 = 无权或不存在，同一个 404
    const p = await profile(ctx.q, student(ctx), asOf(ctx.deps));
    if (!p) return problem(P.NOT_FOUND);
    if (p.grade === null) return problem(P.GRADE_OUT_OF_RANGE);
  }
  try {
    const { rows } = await ctx.q<{ r: Record<string, unknown> }>(`SELECT platform.${fn}($1, $2::platform.subject) AS r`, [student(ctx), product]);
    const r = rows[0]!.r;
    ctx.note('outcome', String(r['outcome']));
    return json(200, Object.fromEntries(ENROLL_KEYS.filter((k) => k in r).map((k) => [k, r[k]])));
  } catch (e) {
    if (pgCode(e) === '42501') return problem(P.NOT_FOUND);
    // 库层兜底（迁移 0051）：enroll_subject 按届别与计量时钟再核一次当前年级，不在 7–9 时 55000 GRADE_OUT_OF_RANGE。
    // 与上面的前置检查同一个 409（BFF 请求时钟与库的计量时钟在 9 月 1 日前后可能差几秒，或绕过前置检查直接走到库函数）
    if (fn === 'enroll_subject' && isGradeOutOfRange(e)) return problem(P.GRADE_OUT_OF_RANGE);
    throw e;
  }
};
const isGradeOutOfRange = (e: unknown): boolean =>
  pgCode(e) === '55000' && e instanceof Error && e.message === 'GRADE_OUT_OF_RANGE';

// ---------------------------------------------------------------- 今日计划（第 6 节）

const KIND_ORDER = ['retest', 'diagnose', 'practice', 'review', 'lesson', 'mock_exam'];

type Rationale = { kind: 'content'; code: string } | ({ kind: 'claim' } & ShownStatement) | null;
interface OutTask {
  kind: string; node_id: string | null; node_name: string | null; priority: number; estimated_minutes: number; rationale: Rationale;
  /** 内部用：这条任务在陈述任务表里的下标（响应前删掉） */
  job?: number | null;
}

const today: Handler = async (ctx) => {
  const { deps } = ctx;
  const cfg = shellConfig(deps);
  const id = student(ctx);
  const s = ctx.session!;
  const audience = audienceOf(ctx);
  // 一、在读学科（英语搁置、planned 不排）；当前年级不在 7–9（未入学或已毕业）时不排任何学科（PR3 #10，与选科只能暂停一致）
  const first = await withAccount(deps.pool, s.accountId, async (c) => {
    const q: Query = (sql, p) => c.query(sql, p);
    const p = await profile(q, id, asOf(deps));
    if (!p) return null;
    if (p.grade === null) return { subjects: [] as { code: string; product: PlanSubject }[], linkId: undefined };
    const { rows } = await q<{ code: string; product: PlanSubject }>(
      `SELECT c.code, c.product::text AS product FROM platform.subject_enrollment e
         JOIN platform.subject_catalog c ON c.product = e.subject
        WHERE e.student_id = $1 AND e.status = 'active' AND c.status <> 'planned' AND c.product IN ('aya', 'wenli', 'zhixing')
        ORDER BY array_position($2::text[], c.code) NULLS LAST, c.code`, [id, ORDER]);
    return { subjects: rows, linkId: audience === 'guardian' ? await linkIdFor(q, id) : undefined };
  });
  if (!first) return problem(P.NOT_FOUND);
  // 二、学习服务排计划（事务外调用，不占着连接等 HTTP）；学习服务不可用时每一科都“今天暂无安排”
  const asked = first.subjects.map((r) => r.product);
  const sections: PlanSection[] = asked.length === 0 ? []
    : (await deps.shell?.plan?.plan(id, asked)) ?? asked.map((subject) => ({ subject, status: 'unavailable' as const, tasks: [] }));
  const nodeIds = [...new Set(sections.flatMap((x) => x.tasks.flatMap((t) => t.target_node_ids.slice(0, 1))))];
  const claimNodes = [...new Set(sections.flatMap((x) => x.tasks.filter((t) => cfg.studentRationale.has(t.rationale_code))
    .flatMap((t) => t.target_node_ids.slice(0, 1))))];
  // 三、节点名与判断（RLS 下读，受众由会话决定）
  const { names, claims } = await withAccount(deps.pool, s.accountId, async (c) => {
    const q: Query = (sql, p) => c.query(sql, p);
    const { rows } = nodeIds.length === 0 ? { rows: [] as { id: string; name: string }[] } : await q<{ id: string; name: string }>(
      `SELECT n.id, n.name FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id
        WHERE v.status = 'published' AND n.status = 'active' AND n.id = ANY ($1::text[])`, [nodeIds]);
    return { names: new Map(rows.map((r) => [r.id, r.name])), claims: await nodeClaims(q, cfg, id, audience, claimNodes) };
  });
  const codeOf = new Map(first.subjects.map((r) => [r.product, r.code]));
  const jobs: StatementJob[] = [];
  const out: { subject: string; status: string; tasks: OutTask[] }[] = [];
  for (const sec of sections) {
    const tasks: OutTask[] = [];
    const sorted = [...sec.tasks].sort((a, b) => b.priority - a.priority || KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
    for (const t of sorted) {
      const node = t.target_node_ids[0] ?? null;
      let rationale: Rationale = null;
      if (cfg.contentRationale.has(t.rationale_code)) {
        rationale = { kind: 'content', code: t.rationale_code };  // 内容类：固定短句，不带 claim_id
      } else if (node && cfg.studentRationale.has(t.rationale_code)) {
        // 关于学生类：只有目标节点上有可见、成立或假设、白名单内、且登记为这个编码可用模板的判断时才显示它的陈述；
        // 映射表外的编码不显示理由
        const allowed = cfg.rationaleTemplates.get(t.rationale_code);
        const candidates = (claims.get(node) ?? []).filter((c) => allowed?.has(templateRef(c.template_key, c.template_version)));
        if (candidates.length > 0) jobs.push({ key: jobs.length, claims: candidates });
        tasks.push({ kind: t.kind, node_id: node, node_name: names.get(node) ?? null, priority: t.priority,
                     estimated_minutes: t.estimated_minutes, rationale, job: candidates.length > 0 ? jobs.length - 1 : null });
        continue;
      }
      tasks.push({ kind: t.kind, node_id: node, node_name: node ? names.get(node) ?? null : null, priority: t.priority,
                   estimated_minutes: t.estimated_minutes, rationale, job: null });
    }
    out.push({ subject: codeOf.get(sec.subject) ?? sec.subject, status: sec.status, tasks });
  }
  // 四、陈述：并行取（并发上限、整页总时限、每页条数上限、每会话限频）；时限内没回来的句子不显示
  const shown = await resolveStatements(deps, cfg, deps.shell?.insight, s.token,
    { student: id, audience, actor: s.accountId, ...(first.linkId ? { linkId: first.linkId } : {}) }, jobs);
  for (const sec of out) {
    for (const t of sec.tasks) {
      const st = t.job === null || t.job === undefined ? undefined : shown.get(t.job);
      if (st) t.rationale = { kind: 'claim', ...st };
      delete t.job;
    }
  }
  return json(200, { student_id: id, subjects: out });
};

// ---------------------------------------------------------------- 额度与时长（第 9 节）

interface Guard {
  state: 'trialing' | 'active' | 'grace' | 'retained' | 'none';
  trial_ends_at: string | null; plan_daily_cap: number; daily_cap: number; minutes_used_today: number; minutes_remaining_today: number;
  curfew_start: string; curfew_end: string; in_curfew: boolean; minutes_until_curfew: number; practice_remaining_today: number | null; as_of: string;
}

async function guardStatus(q: Query, id: string): Promise<Guard | null> {
  try {
    const { rows } = await q<{ g: Guard }>('SELECT platform.meter_guard_status($1::uuid) AS g', [id]);
    return rows[0]?.g ?? null;
  } catch (e) {
    if (pgCode(e) === '42501') return null;
    throw e;
  }
}

/** 上海时区的自然日差（试用还剩 N 天，spec 第 9 节）：只是把库里给的两个时间换成日期相减，不涉及计量规则 */
export function shanghaiDaysBetween(fromIso: string, toIso: string): number {
  const day = (iso: string) => Math.floor((Date.parse(iso) + 8 * 3600_000) / 86_400_000);
  return day(toIso) - day(fromIso);
}

const balance: Handler = async (ctx) => {
  const id = student(ctx);
  const g = await guardStatus(ctx.q, id);
  if (!g) return problem(P.NOT_FOUND);
  const cfg = shellConfig(ctx.deps);
  const { rows } = await ctx.q<{ resource_type: string; unit: string; source: string; period_end: Date; remaining: string }>(
    `SELECT resource_type, unit, source, period_end, remaining FROM platform.entitlement_balance
      WHERE student_id = $1 ORDER BY resource_type, source, period_end`, [id]);
  const byType = new Map<string, { resource_type: string; unit: string; remaining: number; period_end: string; sources: { source: string; remaining: number; period_end: string }[] }>();
  for (const r of rows) {
    const end = r.period_end.toISOString();
    const cur = byType.get(r.resource_type) ?? { resource_type: r.resource_type, unit: r.unit, remaining: 0, period_end: end, sources: [] };
    cur.remaining += Number(r.remaining);
    if (end < cur.period_end) cur.period_end = end;
    cur.sources.push({ source: r.source, remaining: Number(r.remaining), period_end: end });
    byType.set(r.resource_type, cur);
  }
  const guardian = ctx.session!.kind === 'guardian';
  const serving = g.state === 'trialing' || g.state === 'active' || g.state === 'grace';
  return json(200, {
    student_id: id,
    state: g.state,
    as_of: g.as_of,
    trial_ends_at: g.state === 'trialing' ? g.trial_ends_at : null,
    trial_days_left: g.state === 'trialing' && g.trial_ends_at ? Math.max(0, shanghaiDaysBetween(g.as_of, g.trial_ends_at)) : null,
    practice_remaining_today: g.practice_remaining_today,
    // 留存态每天自练次数：meter_guard_status 不返回，BFF 从 config/shell.json 单处读（与 0015 一致；lessons 待人确认）
    practice_daily_limit: cfg.meterDefaults.retainedDailyPractice,
    time: {
      plan_daily_cap: g.plan_daily_cap, daily_cap: g.daily_cap, minutes_used_today: g.minutes_used_today,
      minutes_remaining_today: g.minutes_remaining_today, curfew_start: g.curfew_start, curfew_end: g.curfew_end,
      in_curfew: g.in_curfew, minutes_until_curfew: g.minutes_until_curfew,
      // 显示规则（不是计量规则）：阈值在 config/shell.json；数值全部来自 meter_guard_status
      curfew_near: !g.in_curfew && g.minutes_until_curfew > 0 && g.minutes_until_curfew <= cfg.curfewNearMinutes,
      daily_cap_reached: serving && g.minutes_remaining_today <= 0,
    },
    // 学生端：同一资源合并成一行；分来源明细只给家长（spec 第 9 节）
    balances: [...byType.values()].map((b) => (guardian ? b : { resource_type: b.resource_type, unit: b.unit, remaining: b.remaining, period_end: b.period_end })),
  });
};

// ---------------------------------------------------------------- 时长设置（第 9 节）

const timeGuardGet: Handler = async (ctx) => {
  const id = student(ctx);
  const g = await guardStatus(ctx.q, id);
  if (!g) return problem(P.NOT_FOUND);
  // 设置人（updated_by）不给 BFF 读（0015）：学生端看不到是哪位家长改的
  const { rows } = await ctx.q<{ daily_cap_minutes: number | null; curfew_start: string | null; curfew_end: string | null }>(
    `SELECT daily_cap_minutes, to_char(curfew_start, 'HH24:MI') AS curfew_start, to_char(curfew_end, 'HH24:MI') AS curfew_end
       FROM platform.student_time_guard WHERE student_id = $1`, [id]);
  const set = rows[0] ?? { daily_cap_minutes: null, curfew_start: null, curfew_end: null };
  return json(200, {
    student_id: id,
    daily_cap_minutes: set.daily_cap_minutes, plan_daily_cap: g.plan_daily_cap, effective_daily_cap: g.daily_cap,
    custom_curfew: set.curfew_start !== null, curfew_start: g.curfew_start, curfew_end: g.curfew_end,
    // 默认宵禁：meter_guard_status 不返回，BFF 从 config/shell.json 单处读（与 0015 一致；lessons 待人确认）
    default_curfew: { start: shellConfig(ctx.deps).meterDefaults.curfewStart, end: shellConfig(ctx.deps).meterDefaults.curfewEnd },
  });
};

const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const timeGuardPut: Handler = async (ctx) => {
  const b = ctx.body;
  if (!b || Object.keys(b).sort().join(',') !== 'curfew_end,curfew_start,daily_cap_minutes') return problem(P.INVALID);
  const cap = b['daily_cap_minutes'];
  const start = b['curfew_start'];
  const end = b['curfew_end'];
  if ((cap !== null && (typeof cap !== 'number' || !Number.isInteger(cap) || cap < 0 || cap > 1440))
      || (start !== null && (typeof start !== 'string' || !HHMM.test(start))) || (end !== null && (typeof end !== 'string' || !HHMM.test(end)))
      || (start === null) !== (end === null) || (start !== null && start === end)) {
    return problem(P.INVALID);
  }
  try {
    const { rows } = await ctx.q<{ r: Record<string, unknown> }>('SELECT platform.set_time_guard($1, $2, $3::time, $4::time) AS r',
      [student(ctx), cap, start, end]);
    const r = rows[0]!.r;
    ctx.note('outcome', String(r['outcome']));
    return json(200, Object.fromEntries(['outcome', 'daily_cap', 'plan_cap'].filter((k) => k in r).map((k) => [k, r[k]])));
  } catch (e) {
    if (pgCode(e) === '42501') return problem(P.NOT_FOUND);
    if (pgCode(e) === '22023') return problem(P.INVALID);
    throw e;
  }
};

// ---------------------------------------------------------------- 届别更正（specs/policy-decisions-v0.9.1 第 1 节，PR2）

/**
 * 家长更正孩子的中考届别：只经 platform.correct_exam_cohort（迁移 0045，SECURITY DEFINER）。授权全在库里：
 * 经 BFF 调用时必须是该学生的有效绑定家长（guardian_student_link），来源记 guardian_report；每次只能 ±1，
 * 更正后当前年级必须在 7–9，写一条 platform.exam_cohort_change。BFF 不比对旧值、不算步长。
 * 请求体只收更正后的届别（绝对值：重复提交同一个值第二次因步长为 0 被拒，不会连改两次）。
 * 错误：未绑定、学生不存在、已执行删除一律同一个 404；步长不是 ±1、更正后不在 7–9 → 422。
 * 频控：每个（家长账号、学生）每天 3 次（cohort_correction.link.day），另加每个家长账号每天 10 次的总量上限
 * （cohort_correction.account.day，挡换着学生 ID 试探；config/auth.json）。没有前端入口（spec“本任务不做前端入口”）。
 */
const cohortCorrect: Handler = async (ctx) => {
  const b = ctx.body;
  if (!b || Object.keys(b).join(',') !== 'exam_cohort') return problem(P.INVALID);
  const cohort = b['exam_cohort'];
  if (typeof cohort !== 'number' || !Number.isInteger(cohort) || cohort < 2015 || cohort > 2100) return problem(P.INVALID);
  const id = student(ctx);
  try {
    const { rows } = await ctx.q<{ exam_cohort: number; grade: number | null }>(
      `SELECT platform.correct_exam_cohort($1, $2::smallint) AS exam_cohort, platform.current_grade($2::smallint, $3::timestamptz) AS grade`,
      [id, cohort, asOf(ctx.deps)]);
    const r = rows[0]!;
    ctx.note('outcome', 'corrected');
    return json(200, { student_id: id, exam_cohort: r.exam_cohort, grade: r.grade });
  } catch (e) {
    const code = pgCode(e);
    if (code === '42501' || code === 'P0002') return problem(P.NOT_FOUND);
    if (code === '22023' || code === '23514') return problem(P.INVALID);
    throw e;
  }
};

// ---------------------------------------------------------------- 连续使用提醒（第 10 节）

const restKey = (ctx: Ctx): string => rateKey(ctx.deps.keys, 'rest', ctx.session!.token).toString('hex');
/** 进程内计时只在 dev / test，或明确声明单实例部署（rest_reminder.single_instance）时启用；pilot 前须把计时落到会话表（lessons H4） */
export const restEnabled = (env: RunEnv, cfg: ShellConfig['rest']): boolean => isLocalEnv(env) || cfg.singleInstance;

const rest = (reset: boolean): Handler => async (ctx) => {
  if (!(await profile(ctx.q, student(ctx), asOf(ctx.deps)))) return problem(P.NOT_FOUND);  // 学生会话只读得到自己
  const cfg = shellConfig(ctx.deps).rest;
  if (!restEnabled(ctx.deps.env, cfg)) {
    return json(200, { enabled: false, show: false, continuous_minutes: 0, threshold_minutes: cfg.continuousMinutes, suggest_break_minutes: cfg.breakMinutes });
  }
  const store = restStore(ctx.deps);
  const view = reset ? store.reset(restKey(ctx), nowMs(ctx.deps), cfg) : store.touch(restKey(ctx), nowMs(ctx.deps), cfg);
  return json(200, { enabled: true, ...view });
};

// ---------------------------------------------------------------- 路由

const read = (path: string, auth: Route['auth'], handler: Handler, ownTx = false): Route =>
  ({ method: 'GET', path, auth, csrf: 'none', body: false, handler, rateLimits: [], ...(ownTx ? { ownTx } : {}) });

const S = '/students/{student_id}';

export const SHELL_ROUTES: readonly Route[] = [
  read(`${S}/enrollments`, 'session', enrollments),
  { method: 'POST', path: `${S}/enrollments/{subject}`, auth: 'guardian', csrf: 'session', body: false, handler: subjectOp('enroll_subject'), rateLimits: [] },
  { method: 'POST', path: `${S}/enrollments/{subject}/pause`, auth: 'guardian', csrf: 'session', body: false, handler: subjectOp('pause_subject'), rateLimits: [] },
  { ...read(`${S}/today`, 'session', today, true), rateLimits: [{ bucket: 'today.session.minute', dim: 'session' }] },
  read(`${S}/balance`, 'session', balance),
  read(`${S}/time-guard`, 'session', timeGuardGet),
  { method: 'PUT', path: `${S}/time-guard`, auth: 'guardian', csrf: 'session', body: true, handler: timeGuardPut,
    rateLimits: [{ bucket: 'time_guard.account.hour', dim: 'account' }] },
  { method: 'POST', path: `${S}/exam-cohort`, auth: 'guardian', csrf: 'session', body: true, handler: cohortCorrect,
    rateLimits: [{ bucket: 'cohort_correction.link.day', dim: 'link' }, { bucket: 'cohort_correction.account.day', dim: 'account' }] },
  read(`${S}/rest-reminder`, 'student', rest(false)),
  { method: 'POST', path: `${S}/rest-reminder/ack`, auth: 'student', csrf: 'session', body: false, handler: rest(true), rateLimits: [] },
];

export type { Pool };
