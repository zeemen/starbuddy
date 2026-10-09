// 复测与验证题的学生端路由（specs/retest-delivery-kge05/spec.md 范围第 4 条；接口定义 openapi/retest.v0.yaml）。
// 挂在功能开关 retest 上（src/retest/features.ts，STARBUDDY_RETEST_UI），默认关闭：关闭时不注册，与表外路径同一个 404。
// 只给学生本人（auth = student）：家长会话在任何查询之前 403；兄妹、另一户、不存在 404。
// 限流：与错题再练共用每会话桶 practice.session.minute（spec“宵禁、限流同再练”；两类合计计数，不另加桶，不改 config/auth.json）。
// 计量只在作答（src/retest/service.ts，复用错题再练的题库自练计次）。
import { INVALID, parseRetestAnswer, RETEST_UNAVAILABLE, retestAnswer, retestNext, retestQueue, type RetestDeps } from '../retest/service.ts';
import type { Ctx, Handler, Reply, Route } from './routes.ts';

type Op = (deps: RetestDeps, ctx: Ctx, account: string, student: string, subject: string) => Promise<Reply>;

const retest = (op: Op): Handler => async (ctx) => {
  const r = ctx.deps.retest;
  if (!r?.learning) return RETEST_UNAVAILABLE;
  ctx.note('route_group', 'retest');  // 日志只记路由模板与学生 ID 的哈希（server.ts），不记票据、作答与题面
  const deps: RetestDeps = {
    pool: ctx.deps.pool, learning: r.learning, key: ctx.deps.keys.practice,
    now: () => (ctx.deps.now ? ctx.deps.now() : new Date()).getTime(),
    ...(r.sleep ? { sleep: r.sleep } : {}), ...(r.retryDelaysMs ? { retryDelaysMs: r.retryDelaysMs } : {}), ...(r.note ? { note: r.note } : {}),
    ...(r.retryBudgetMs !== undefined ? { retryBudgetMs: r.retryBudgetMs } : {}), ...(r.monotonic ? { monotonic: r.monotonic } : {}),
  };
  return op(deps, ctx, ctx.session!.accountId, ctx.params['student_id']!, ctx.params['subject']!);
};

const queue = retest((d, _ctx, account, student, subject) => retestQueue(d, account, student, subject));
const next = retest((d, ctx, account, student, subject) => retestNext(d, account, student, subject, ctx.params['entry_id']!));
const answer = retest(async (d, ctx, account, student, subject) => {
  const input = parseRetestAnswer(ctx.body!);
  return input ? retestAnswer(d, account, student, subject, input) : INVALID;
});

const S = '/students/{student_id}/retest/{subject}';
const RL = [{ bucket: 'practice.session.minute', dim: 'session' }] as const;

export const RETEST_ROUTES: readonly Route[] = [
  { method: 'GET', path: S, auth: 'student', csrf: 'none', body: false, ownTx: true, handler: queue, rateLimits: RL },
  { method: 'POST', path: `${S}/entries/{entry_id}/next`, auth: 'student', csrf: 'session', body: false, ownTx: true, handler: next, rateLimits: RL },
  { method: 'POST', path: `${S}/answer`, auth: 'student', csrf: 'session', body: true, ownTx: true, handler: answer, rateLimits: RL },
];
