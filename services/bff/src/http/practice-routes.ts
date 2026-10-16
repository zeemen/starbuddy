// 错题再练的学生端路由（specs/practice-loop-stage1/spec.md 第 5 节，PR 3；接口定义 openapi/practice.v0.yaml）。
// 挂在功能开关 practice 上（src/practice/features.ts），默认关闭：关闭时不注册，与表外路径同一个 404。
// 只给学生本人（auth = student）：家长会话在任何查询之前 403（家长看不到再练列表，spec #10）；兄妹、另一户、不存在 404。
// 学科码放路径（{subject}，与选科、档案页同一套前端学科码）而不是查询参数：BFF 的路由表只按路径分发，查询参数不交给 handler。
// 限流：三条路由按 spec 第 5 节的每会话桶 practice.session.minute（config/auth.json）。
// 计量只在作答（src/practice/service.ts；H6 的 METER_CALLERS 已登记）。
import { practiceAnswer, practiceNext, practiceQueue, parseAnswer, INVALID, PRACTICE_UNAVAILABLE, type PracticeDeps } from '../practice/service.ts';
import type { Ctx, Handler, Reply, Route } from './routes.ts';

type Op = (deps: PracticeDeps, ctx: Ctx, account: string, student: string, subject: string) => Promise<Reply>;

const practice = (op: Op): Handler => async (ctx) => {
  const p = ctx.deps.practice;
  if (!p?.learning) return PRACTICE_UNAVAILABLE;
  ctx.note('route_group', 'practice');  // 日志只记路由模板与学生 ID 的哈希（server.ts），不记票据、作答与题面
  const deps: PracticeDeps = {
    pool: ctx.deps.pool, learning: p.learning, key: ctx.deps.keys.practice,
    now: () => (ctx.deps.now ? ctx.deps.now() : new Date()).getTime(),
    ...(p.sleep ? { sleep: p.sleep } : {}), ...(p.retryDelaysMs ? { retryDelaysMs: p.retryDelaysMs } : {}), ...(p.note ? { note: p.note } : {}),
    ...(p.retryBudgetMs !== undefined ? { retryBudgetMs: p.retryBudgetMs } : {}), ...(p.monotonic ? { monotonic: p.monotonic } : {}),
    // 分学科并发闸门（PLT-16，开关关时不传，服务直接执行原来的代码）
    ...(ctx.deps.subjectGate ? { gate: ctx.deps.subjectGate, observe: ctx.note, signal: ctx.signal } : {}),
  };
  return op(deps, ctx, ctx.session!.accountId, ctx.params['student_id']!, ctx.params['subject']!);
};

const queue = practice((d, _ctx, account, student, subject) => practiceQueue(d, account, student, subject));
const next = practice((d, ctx, account, student, subject) => practiceNext(d, account, student, subject, ctx.params['entry_id']!));
const answer = practice(async (d, ctx, account, student, subject) => {
  const input = parseAnswer(ctx.body!);
  return input ? practiceAnswer(d, account, student, subject, input) : INVALID;
});

const S = '/students/{student_id}/practice/{subject}';
const RL = [{ bucket: 'practice.session.minute', dim: 'session' }] as const;

export const PRACTICE_ROUTES: readonly Route[] = [
  { method: 'GET', path: S, auth: 'student', csrf: 'none', body: false, ownTx: true, handler: queue, rateLimits: RL },
  { method: 'POST', path: `${S}/entries/{entry_id}/next`, auth: 'student', csrf: 'session', body: false, ownTx: true, handler: next, rateLimits: RL },
  { method: 'POST', path: `${S}/answer`, auth: 'student', csrf: 'session', body: true, ownTx: true, handler: answer, rateLimits: RL },
];
