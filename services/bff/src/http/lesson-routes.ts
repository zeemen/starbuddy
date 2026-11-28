// 讲评课会话的学生端路由（specs/stage-lesson-session ③b-4b；接口定义 openapi/lesson.v0.yaml）。
// 挂在功能开关 lesson 上（src/lesson/features.ts），默认关闭：关闭时不注册，与表外路径同一个 404。
// 只给学生本人（auth = student）：家长会话在任何查询之前 403；兄妹、另一户、不存在 404。
// 限流：三条路由按每会话桶 lesson.session.minute（config/auth.json）。
// 计量只在打开（src/lesson/service.ts；H6 的 METER_CALLERS 已登记）；课内提问、目录不扣次。
// 目录（specs/lesson-catalog PR C）：学科码放路径（与再练同一做法：路由表只按路径分发，查询参数不交给 handler）；
// 只回编号、版本、标题与步数。多一段 catalog：免得 open、ask 也合 {subject} 的形状（GET /lessons/open 要与其他表内路径一样 405）。
import { askLesson, lessonCatalog, openLesson, parseAsk, parseOpen, INVALID, LESSON_UNAVAILABLE, type LessonDeps, type Reply as LessonReply } from '../lesson/service.ts';
import type { Ctx, Handler, Route } from './routes.ts';

type Op = (deps: LessonDeps, ctx: Ctx, account: string, student: string) => Promise<LessonReply>;

const lesson = (op: Op): Handler => async (ctx) => {
  const l = ctx.deps.lesson;
  const out = (r: LessonReply) => ({ status: r.status, body: JSON.stringify(r.body), ...(r.headers ? { headers: r.headers } : {}) });
  if (!l?.learning) return out(LESSON_UNAVAILABLE);
  ctx.note('route_group', 'lesson');  // 日志只记路由模板与学生 ID 的哈希（server.ts），不记票据、提问、讲解与回答
  const deps: LessonDeps = {
    pool: ctx.deps.pool, learning: l.learning, key: ctx.deps.keys.lesson,
    now: () => (ctx.deps.now ? ctx.deps.now() : new Date()).getTime(),
    ...(l.sleep ? { sleep: l.sleep } : {}), ...(l.retryDelaysMs ? { retryDelaysMs: l.retryDelaysMs } : {}), ...(l.note ? { note: l.note } : {}),
    ...(l.retryBudgetMs !== undefined ? { retryBudgetMs: l.retryBudgetMs } : {}), ...(l.monotonic ? { monotonic: l.monotonic } : {}),
  };
  return out(await op(deps, ctx, ctx.session!.accountId, ctx.params['student_id']!));
};

const open = lesson(async (d, ctx, account, student) => {
  const input = parseOpen(ctx.body!);
  return input ? openLesson(d, account, student, input) : INVALID;
});
const ask = lesson(async (d, ctx, account, student) => {
  const input = parseAsk(ctx.body!);
  return input ? askLesson(d, account, student, input) : INVALID;
});

const catalog = lesson((d, ctx, account, student) => lessonCatalog(d, account, student, ctx.params['subject']!));

const S = '/students/{student_id}/lessons';
const RL = [{ bucket: 'lesson.session.minute', dim: 'session' }] as const;

export const LESSON_ROUTES: readonly Route[] = [
  { method: 'POST', path: `${S}/open`, auth: 'student', csrf: 'session', body: true, ownTx: true, handler: open, rateLimits: RL },
  { method: 'POST', path: `${S}/ask`, auth: 'student', csrf: 'session', body: true, ownTx: true, handler: ask, rateLimits: RL },
  { method: 'GET', path: `${S}/catalog/{subject}`, auth: 'student', csrf: 'none', body: false, ownTx: true, handler: catalog, rateLimits: RL },
];
