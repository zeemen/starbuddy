// 月度计划的两条路由（specs/family-goals-monthly-plan/step3-spec.md 范围 1，PLT-14 第 3 步；接口定义 openapi/monthly-plan.v0.yaml；
// 快照由洞察服务生成，迁移 0054 草稿的两个视图函数只授 BFF）。挂在开关 monthly_plan 上（src/family/monthly-plan.ts），缺省关：关时不注册，
// 与表外路径同一个 404。不计量（自动月报不计次）。
//   GET /students/{id}/monthly-plan（只给家长）：路由 auth = session，学生会话在任何查询之前 404（server.ts 的 guardianOnly404，
//     与“不存在”逐字节相同，学生端不知道有这个接口）；先按本人会话确认是有效绑定家长（platform.is_linked_guardian）且档案未删除，
//     别家、已解除绑定、不存在、档案已删除同一个 404。本月（上海时区，按数据库时钟）没有快照时调洞察服务生成（actor = 当前家长账号，
//     洞察服务库内再核），再经 insight.monthly_plan_guardian_view 读；洞察服务统一的“不可用”回答（含没有权益）→ entitled: false，
//     不带任何别的信息。洞察服务不可用、模板库或词表读不到、模板版本对不上 → 503。生成中途解除绑定（二次读取不通过）→ 404。
//     生成的滥用面（审查意见 1）：
//       - 按家长账号计的生成桶 monthly_plan_generate.account.hour（config/auth.json，缺省每小时 20 次）：只在真要调洞察服务时计数，
//         超出按不可用处理（503），不调洞察服务；
//       - 进程内负缓存：洞察服务对（学生、家长、月份）回过“不可用”后 5 分钟内不再问，直接 entitled: false（不计生成桶）。
//   GET /students/{id}/my-plan（只给学生本人，auth = student）：家长会话 403（在任何查询之前）；兄妹、另一户、不存在 404。
//     只经 insight.monthly_plan_student_view 读学习计划一段；本月没有快照时不触发生成，返回 plan: null（生成只由家长端或调度器触发）。
// 渲染与词表核对在 src/family/monthly-plan.ts：成品句子过禁用推断词表与“名次 / 概率 / 承诺”词表，命中的那一条不出；
// 日志只记路由模板、学生 ID 的哈希（server.ts）、生成结果、丢弃条数与其中命中词表的条数，不记计划内容。
import type { Pool } from 'pg';
import { withAccount } from '../metering/db.ts';
import { loadShellConfig, type ShellConfig } from '../shell/config.ts';
import {
  cleanWith, loadTemplates, newTally, renderGuardianPlan, renderStudentView, type RenderCtx, type Tally, type TemplateLibrary,
} from '../family/monthly-plan.ts';
import { lexicon, noPromiseLexicon } from '../trust/lexicon.ts';
import { P } from './problems.ts';
import type { Ctx, Handler, Reply, Route, ShellDeps } from './routes.ts';
import { rateKey } from './secrets.ts';

const json = (status: number, v: unknown): Reply => ({ status, body: JSON.stringify(v) });
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const UNAVAILABLE = problem(P.TRUST_UNAVAILABLE);

let defaultConfig: ShellConfig | null = null;
const shellConfig = (deps: ShellDeps): ShellConfig => deps.shell?.config ?? (defaultConfig ??= loadShellConfig());

/** 按家长账号计的生成桶（config/auth.json） */
export const GENERATE_BUCKET = 'monthly_plan_generate.account.hour';
/** 洞察服务“不可用”回答的进程内负缓存时长（审查意见 1 的默认值；测试可经 deps.monthlyPlan.negativeTtlMs 改） */
export const NEGATIVE_TTL_MS = 5 * 60_000;
/** 负缓存（键：学生、家长、月份）：按 deps.monthlyPlan 对象分开，测试里各个服务互不影响；只存到期时刻，不存任何内容 */
const negative = new WeakMap<object, Map<string, number>>();
const negativeOf = (deps: ShellDeps): Map<string, number> => {
  const owner = deps.monthlyPlan ?? deps;
  let m = negative.get(owner);
  if (!m) {
    m = new Map();
    negative.set(owner, m);
  }
  return m;
};
const nowMs = (deps: ShellDeps): number => (deps.now ? deps.now() : new Date()).getTime();

/** 渲染所需：两张词表与模板库（任何一样读不到 → null，调用方 503，fail closed）。测试可经 deps.monthlyPlan 注入 */
function renderCtx(ctx: Ctx): RenderCtx | null {
  const m = ctx.deps.monthlyPlan;
  try {
    const clean = cleanWith((m?.lexicon ?? lexicon)(), (m?.noPromise ?? noPromiseLexicon)());
    const lib: TemplateLibrary = m?.templates ? m.templates() : loadTemplates(ctx.deps.env, clean);
    ctx.note('template_fingerprint', lib.fingerprint.slice(0, 16));
    return { lib, clean, external: shellConfig(ctx.deps).externalTemplates };
  } catch {
    ctx.note('monthly_plan', 'templates_or_lexicon_unavailable');
    return null;
  }
}

/** 本月（上海时区月份的第一天）：ts 是一个 timestamptz 的 SQL 表达式；路由里用数据库时钟 now()，与洞察服务同一个时钟 */
export const monthExpr = (ts: string): string => `to_char(date_trunc('month', ${ts} AT TIME ZONE 'Asia/Shanghai'), 'YYYY-MM-DD')`;
const MONTH_SQL = monthExpr('now()');

interface GuardianRead { month: string; view: unknown }
/** 家长：有效绑定且档案未删除才读视图（否则 null → 404）；月份按数据库时钟（与洞察服务同一个时钟） */
async function guardianRead(pool: Pool, account: string, student: string): Promise<GuardianRead | null> {
  return withAccount(pool, account, async (c) => {
    await c.query('SET TRANSACTION READ ONLY');
    const { rows: [a] } = await c.query<{ linked: boolean; live: boolean | null; month: string }>(
      `SELECT platform.is_linked_guardian($1::uuid) AS linked,
              (SELECT s.erased_at IS NULL FROM platform.student_profile s WHERE s.id = $1::uuid) AS live, ${MONTH_SQL} AS month`, [student]);
    if (!a || a.linked !== true || a.live !== true) return null;
    const { rows: [v] } = await c.query<{ v: unknown }>('SELECT insight.monthly_plan_guardian_view($1::uuid, $2::date) AS v', [student, a.month]);
    return { month: a.month, view: v?.v ?? null };
  });
}

/** 生成桶（按家长账号）：与路由限流同一个库函数与键派生；超出返回 false */
async function generateAllowed(deps: ShellDeps, account: string): Promise<boolean> {
  const rule = deps.config.rate_limits[GENERATE_BUCKET];
  if (!rule) throw new Error(`rate limit ${GENERATE_BUCKET} missing from config`);
  const { rows } = await deps.pool.query<{ ok: boolean }>('SELECT platform.auth_rate_hit($1, $2, $3, $4) AS ok',
    [GENERATE_BUCKET, rateKey(deps.keys, 'account', account), rule.window_seconds, rule.limit]);
  return rows[0]?.ok === true;
}

const monthOf = (day: string): string => day.slice(0, 7);

function noteTally(ctx: Ctx, t: Tally): void {
  if (t.dropped > 0) ctx.note('dropped_statements', String(t.dropped));
  if (t.lexicon_hits > 0) ctx.note('lexicon_hits', String(t.lexicon_hits));
}

const guardianPlan: Handler = async (ctx) => {
  // 第二道（第一道在 server.ts，请求体、CSRF、限流之前）：学生会话在任何查询之前 404
  if (ctx.session!.kind !== 'guardian') return problem(P.NOT_FOUND);
  ctx.note('route_group', 'monthly_plan');
  const { deps } = ctx;
  const account = ctx.session!.accountId;
  const student = ctx.params['student_id']!;
  const first = await guardianRead(deps.pool, account, student);
  if (!first) return problem(P.NOT_FOUND);
  const rc = renderCtx(ctx);
  if (!rc) return UNAVAILABLE;
  let read = first;
  let entitled = true;
  if (first.view === null) {
    // 本月没有快照：调洞察服务生成（同学生同月幂等；已有直接返回、不写新行）。只在已确认有效绑定家长之后调
    const month = monthOf(first.month);
    const cache = negativeOf(deps);
    const key = `${student}:${account}:${month}`;
    const until = cache.get(key);
    if (until !== undefined && until > nowMs(deps)) {
      ctx.note('generate', 'not_available_cached');  // 负缓存：不调洞察服务、不计生成桶
      return json(200, { student_id: student, month, entitled: false, plan: null });
    }
    const client = deps.monthlyPlan?.client;
    if (!client) {
      ctx.note('generate', 'not_configured');
      return UNAVAILABLE;
    }
    if (!(await generateAllowed(deps, account))) {
      ctx.note('generate', 'rate_limited');  // 生成桶用完：按不可用处理，不调洞察服务
      return UNAVAILABLE;
    }
    const out = await client.generate(student, month, account).catch(() => 'unavailable' as const);
    ctx.note('generate', out);
    if (out === 'not_available') cache.set(key, nowMs(deps) + (deps.monthlyPlan?.negativeTtlMs ?? NEGATIVE_TTL_MS));
    const again = await guardianRead(deps.pool, account, student);
    if (!again) return problem(P.NOT_FOUND);  // 其间解除了绑定或档案被删除
    read = again;
    if (again.view === null) {
      if (out === 'not_available') entitled = false;
      else return UNAVAILABLE;
    }
  }
  if (!entitled) return json(200, { student_id: student, month: monthOf(read.month), entitled: false, plan: null });
  const v = read.view as Record<string, unknown> | null;
  if (!v || typeof v !== 'object' || v['template_version'] !== rc.lib.version) {
    ctx.note('monthly_plan', 'template_version_mismatch');
    return UNAVAILABLE;
  }
  const tally = newTally();
  const plan = renderGuardianPlan(v, rc, tally);
  noteTally(ctx, tally);
  if (!plan) {
    ctx.note('monthly_plan', 'invalid_shape');
    return UNAVAILABLE;
  }
  return json(200, { student_id: student, month: monthOf(read.month), entitled: true, plan });
};

const studentPlan: Handler = async (ctx) => {
  ctx.note('route_group', 'monthly_plan');
  const student = ctx.params['student_id']!;
  const r = await withAccount(ctx.deps.pool, ctx.session!.accountId, async (c) => {
    await c.query('SET TRANSACTION READ ONLY');
    const { rows: [a] } = await c.query<{ self: boolean; month: string }>(
      `SELECT platform.is_self_student($1::uuid) AS self, ${MONTH_SQL} AS month`, [student]);
    if (!a || a.self !== true) return null;
    const { rows: [v] } = await c.query<{ v: unknown }>('SELECT insight.monthly_plan_student_view($1::uuid, $2::date) AS v', [student, a.month]);
    return { month: a.month, view: v?.v ?? null };
  });
  if (!r) return problem(P.NOT_FOUND);
  // 本月没有快照：不触发生成（生成只由家长端或调度器触发），空状态
  if (r.view === null) return json(200, { student_id: student, month: monthOf(r.month), plan: null });
  const rc = renderCtx(ctx);
  if (!rc) return UNAVAILABLE;
  const tally = newTally();
  // 学生视图只有 month、generated_at、student_plan（已知缺口：不带模板版本，只能按模板键核对，待迁移补上）
  const plan = renderStudentView(r.view, rc, tally);
  noteTally(ctx, tally);
  if (!plan) {
    ctx.note('monthly_plan', 'invalid_shape');
    return UNAVAILABLE;
  }
  return json(200, { student_id: student, month: monthOf(r.month), plan });
};

const RL = [{ bucket: 'monthly_plan.session.minute', dim: 'session' }] as const;

export const MONTHLY_PLAN_ROUTES: readonly Route[] = [
  { method: 'GET', path: '/students/{student_id}/monthly-plan', auth: 'session', guardianOnly404: true, csrf: 'none', body: false, ownTx: true,
    handler: guardianPlan, rateLimits: RL },
  { method: 'GET', path: '/students/{student_id}/my-plan', auth: 'student', csrf: 'none', body: false, ownTx: true, handler: studentPlan, rateLimits: RL },
];
