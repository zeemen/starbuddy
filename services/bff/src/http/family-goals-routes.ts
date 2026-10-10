// 家长诉求的家长端路由（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14；接口定义 openapi/family-goals.v0.yaml；迁移 0053）。
// 挂在功能开关 family_goals 上（src/family/features.ts），默认关闭：关闭时不注册，与表外路径同一个 404。
// 只给家长：路由 auth = session，学生会话在任何查询之前一律 404（与“不存在”同一个响应，学生端不知道有这个接口，spec 口径 1）；
// 家长的授权全在库里（按 guardian_student_link 核对当前账号）：别家、已解除绑定、不存在、档案已删除同一个 404。
// 补充说明经禁用推断词表过滤（docs/design/sie/forbidden-inference.v0.yaml），命中 422 TEXT_NOT_ACCEPTED，不存；词表读不到 503，不存。
// 不计量（诉求收集不是消耗类请求）。限流：读按会话、写按（家长，孩子）一对与按家长账号（config/auth.json）。
import { getGoals, parseGoalInput, submitGoals, type Outcome } from '../family/goals.ts';
import { lexicon, type Lexicon } from '../trust/lexicon.ts';
import { P } from './problems.ts';
import type { Ctx, Handler, Reply, Route } from './routes.ts';

const json = (status: number, v: unknown): Reply => ({ status, body: JSON.stringify(v) });
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });

const reply = <T>(o: Outcome<T>): Reply =>
  o.kind === 'ok' ? json(200, o.value) : o.kind === 'not_found' ? problem(P.NOT_FOUND) : o.kind === 'invalid' ? problem(P.INVALID)
    : o.kind === 'conflict' ? problem(P.CONFLICT) : o.kind === 'too_many' ? problem(P.WRITE_CAP)
      : o.kind === 'school_not_open' ? problem(P.SCHOOL_NOT_OPEN) : problem(P.GOAL_GRADE_NOT_SUPPORTED);

/** 学生会话：server.ts 已在请求体错误、CSRF、限流之前回 404；这里是第二道（任何查询之前 404，不读请求体内容、不碰库） */
const guardianOnly = (h: Handler): Handler => async (ctx: Ctx) => {
  if (ctx.session!.kind !== 'guardian') return problem(P.NOT_FOUND);
  ctx.note('route_group', 'family_goals');  // 日志只记路由模板与学生 ID 的哈希（server.ts），不记诉求内容
  return h(ctx);
};

const read = guardianOnly(async (ctx) => reply(await getGoals(ctx.deps.pool, ctx.session!.accountId, ctx.params['student_id']!)));

/** 写入 handler；loadLexicon 只为测试注入“词表读不到”（缺省读 docs/design/sie/forbidden-inference.v0.yaml） */
export const familyGoalsWrite = (loadLexicon: () => Lexicon = lexicon): Handler => guardianOnly(async (ctx) => {
  let parsed;
  try {
    parsed = parseGoalInput(ctx.body, loadLexicon);
  } catch {
    return problem(P.TRUST_UNAVAILABLE);  // 禁用推断词表读不到：fail closed，不存（不碰库）
  }
  if (parsed.kind === 'invalid') return problem(P.INVALID);
  if (parsed.kind === 'forbidden_text') return problem(P.TEXT_NOT_ACCEPTED);
  return reply(await submitGoals(ctx.deps.pool, ctx.session!.accountId, ctx.params['student_id']!, parsed.value));
});
const write = familyGoalsWrite();

/** 结果到响应的映射（测试逐项核对：年级不在 7–9 → 409、写入上限 → 429 等） */
export const familyGoalsReply = reply;

const PATH = '/students/{student_id}/family-goals';

export const FAMILY_GOALS_ROUTES: readonly Route[] = [
  { method: 'GET', path: PATH, auth: 'session', guardianOnly404: true, csrf: 'none', body: false, ownTx: true, handler: read,
    rateLimits: [{ bucket: 'family_goals.session.minute', dim: 'session' }] },
  { method: 'PUT', path: PATH, auth: 'session', guardianOnly404: true, csrf: 'session', body: true, ownTx: true, handler: write,
    rateLimits: [{ bucket: 'family_goals_write.link.hour', dim: 'link' }, { bucket: 'family_goals_write.account.hour', dim: 'account' }] },
];
