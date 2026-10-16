// 3-H 起陆续加的读写接口挂到统一壳路由表上（specs/unified-shell-sprint4/spec.md 第 4、7.1 节，H2）：
// 星图（3-H）、建档（SIE-01）、行为事件与探针（2-L）、数学自适应诊断（SIE-02a）。原来它们在 src/star-map/server.ts 里按正则分发、
// 身份来自开发身份桩；现在与认证路由共用一条中间件链（会话 → auth 拦截 → CSRF → 访问日志），身份只来自会话。
// 接口形状与错误码不变（openapi/star-map、onboarding、events、diagnosis.v0.yaml），差别只有：
//   - 身份：请求头 x-sb-session（apps/web 从 HttpOnly cookie 转交），开发身份桩已删除；
//   - 改变状态的请求另要 Origin 等于壳的来源、带与会话绑定的 x-csrf-token（与认证路由相同）；
//   - 只认学生本人的接口（事件、探针、诊断）auth = student：家长会话在任何查询之前就 403，与原来“不是本人”同一个 403；
//   - 路径参数不是 UUID 时与“不存在”同一个 404（原来事件、诊断是 403）。
// 这些 handler 复用原有的查询模块，各自在 withAccount 里开事务（ownTx），不占用中间件的事务。
import { getStarMap } from '../star-map/query.ts';
import { getOnboarding, recordStep, setTextbook, submitAnswer, submitExam, type ExamInput, type Outcome } from '../onboarding/query.ts';
import { linkProbeAttempt, parseAttempt, parseEvent, parseProbe, recordEvent, recordProbe, type Recorded, type WriteOutcome } from '../events/query.ts';
import {
  abandonSession, currentStep, parseAbandon, parseAnswer, parsePause, parseProbe as parseDiagnosisProbe, parseStart, pauseSession,
  startDiagnosis, submitAnswer as submitDiagnosisAnswer, submitProbe, type DiagnosisDeps, type Reply as DiagnosisReply,
} from '../diagnosis/service.ts';
import { P } from './problems.ts';
import type { Ctx, Handler, Reply, Route } from './routes.ts';

const json = (status: number, v: unknown, headers?: Record<string, string>): Reply => ({ status, body: JSON.stringify(v), ...(headers ? { headers } : {}) });
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const OK = json(200, { status: 'ok' });
const account = (ctx: Ctx): string => ctx.session!.accountId;
const student = (ctx: Ctx): string => ctx.params['student_id']!;
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
/** 可选字符串：缺省或 null 为 null；其他非字符串类型为 undefined（调用方回 422，不静默当成 null） */
const optStr = (v: unknown): string | null | undefined => (v === undefined || v === null ? null : typeof v === 'string' ? v : undefined);

// ---------------------------------------------------------------- 星图（3-H）

const starMap: Handler = async (ctx) => {
  const map = await getStarMap(ctx.deps.pool, account(ctx), student(ctx), ctx.deps.now);
  return map ? json(200, map) : problem(P.NOT_FOUND);
};

// ---------------------------------------------------------------- 建档（SIE-01）

const onboardingReply = (o: Outcome<unknown>): Reply =>
  o.kind === 'not_found' ? problem(P.NOT_FOUND) : o.kind === 'invalid' ? problem(P.INVALID)
    : o.kind === 'conflict' ? problem(P.CONFLICT) : o.kind === 'too_many' ? problem(P.WRITE_CAP)
      : o.value === null ? OK : json(200, o.value);

const onboardingGet: Handler = async (ctx) => onboardingReply(await getOnboarding(ctx.deps.pool, account(ctx), student(ctx)));

const onboardingAnswer: Handler = async (ctx) => {
  const field = str(ctx.body!['field_id']);
  if (!field) return problem(P.INVALID);
  return onboardingReply(await submitAnswer(ctx.deps.pool, account(ctx), student(ctx), field, ctx.body!['value']));
};

const onboardingTextbook: Handler = async (ctx) => {
  const b = ctx.body!;
  const subject = str(b['subject']);
  const version = b['textbook_version'] === null ? null : str(b['textbook_version']);
  if (!subject || (version === null && b['textbook_version'] !== null)) return problem(P.INVALID);
  return onboardingReply(await setTextbook(ctx.deps.pool, account(ctx), student(ctx), subject, version));
};

const onboardingProgress: Handler = async (ctx) => {
  const step = str(ctx.body!['step']);
  const status = str(ctx.body!['status']);
  if (!step || !status) return problem(P.INVALID);
  return onboardingReply(await recordStep(ctx.deps.pool, account(ctx), student(ctx), step, status));
};

const onboardingExam: Handler = async (ctx) => {
  const body = ctx.body!;
  const e: ExamInput = {
    subject: str(body['subject']) ?? '', exam_type: str(body['exam_type']) ?? '', exam_date: str(body['exam_date']) ?? '',
    full_score: typeof body['full_score'] === 'number' ? body['full_score'] : NaN,
    raw_score: typeof body['raw_score'] === 'number' ? body['raw_score'] : NaN,
    rank_band: optStr(body['rank_band']), scope_note: optStr(body['scope_note']), difficulty_note: optStr(body['difficulty_note']),
  };
  if (e.rank_band === undefined || e.scope_note === undefined || e.difficulty_note === undefined) return problem(P.INVALID);
  if (!e.subject || !e.exam_type || !/^\d{4}-\d{2}-\d{2}$/.test(e.exam_date) || !Number.isFinite(e.full_score) || !Number.isFinite(e.raw_score)) {
    return problem(P.INVALID);
  }
  return onboardingReply(await submitExam(ctx.deps.pool, account(ctx), student(ctx), e));
};

// ---------------------------------------------------------------- 行为事件与探针（2-L）：只写不读，只认学生本人

/** 首次写入 201；同一幂等键重放 200 与原 ID，响应头标明是重放（库里没有新写任何行） */
const created = (r: Recorded): Reply => json(r.replayed ? 200 : 201, { id: r.id }, { 'idempotent-replayed': r.replayed ? 'true' : 'false' });
const writeReply = <T>(o: WriteOutcome<T>, conflict: string, ok: (v: T) => Reply): Reply =>
  o.kind === 'forbidden' ? problem(P.FORBIDDEN) : o.kind === 'invalid' ? problem(P.INVALID)
    : o.kind === 'conflict' ? problem(conflict) : o.kind === 'too_many' ? problem(P.WRITE_CAP) : ok(o.value);

const events: Handler = async (ctx) => {
  const e = parseEvent(ctx.body!);
  if (!e) return problem(P.INVALID);
  return writeReply(await recordEvent(ctx.deps.pool, account(ctx), student(ctx), e), P.KEY_REUSED, created);
};

const probes: Handler = async (ctx) => {
  const p = parseProbe(ctx.body!);
  if (!p) return problem(P.INVALID);
  return writeReply(await recordProbe(ctx.deps.pool, account(ctx), student(ctx), p), P.KEY_REUSED, created);
};

const probeAttempt: Handler = async (ctx) => {
  const attempt = parseAttempt(ctx.body!);
  if (!attempt) return problem(P.INVALID);
  return writeReply(await linkProbeAttempt(ctx.deps.pool, account(ctx), student(ctx), ctx.params['probe_id']!, attempt),
                    P.PROBE_LINKED, () => OK);
};

// ---------------------------------------------------------------- 数学自适应诊断（SIE-02a）：只认学生本人

type DiagnosisOp = (deps: DiagnosisDeps, ctx: Ctx) => Promise<DiagnosisReply | null>;
const diagnosis = (op: DiagnosisOp): Handler => async (ctx) => {
  if (!ctx.deps.diagnosis) return problem(P.DIAGNOSIS_UNAVAILABLE);
  ctx.note('route_group', 'diagnosis');  // 诊断：日志只记哈希，不记作答
  // 分学科并发闸门（PLT-16，开关关时不传，服务直接执行原来的代码）
  const gate = ctx.deps.subjectGate ? { gate: ctx.deps.subjectGate, observe: ctx.note, signal: ctx.signal } : {};
  const r = await op({ ...ctx.deps.diagnosis, pool: ctx.deps.pool, ...gate }, ctx);
  return r === null ? problem(P.INVALID) : { status: r.status, body: JSON.stringify(r.body), ...(r.headers ? { headers: r.headers } : {}) };
};
const session = (ctx: Ctx): string => ctx.params['session_id']!;

const diagnosisStart = diagnosis(async (d, ctx) => {
  const input = parseStart(ctx.body!);
  return input ? startDiagnosis(d, account(ctx), student(ctx), input) : null;
});
const diagnosisCurrent = diagnosis((d, ctx) => currentStep(d, account(ctx), student(ctx), session(ctx)));
const diagnosisProbe = diagnosis(async (d, ctx) => {
  const input = parseDiagnosisProbe(ctx.body!);
  return input ? submitProbe(d, account(ctx), student(ctx), session(ctx), input) : null;
});
const diagnosisAnswer = diagnosis(async (d, ctx) => {
  const input = parseAnswer(ctx.body!);
  return input ? submitDiagnosisAnswer(d, account(ctx), student(ctx), session(ctx), input) : null;
});
const diagnosisPause = diagnosis(async (d, ctx) => {
  const input = parsePause(ctx.body!);
  return input ? pauseSession(d, account(ctx), student(ctx), session(ctx), input) : null;
});
const diagnosisAbandon = diagnosis(async (d, ctx) => (parseAbandon(ctx.body!) ? abandonSession(d, account(ctx), student(ctx), session(ctx)) : null));

// ---------------------------------------------------------------- 路由

const read = (path: string, auth: Route['auth'], handler: Handler): Route =>
  ({ method: 'GET', path, auth, csrf: 'none', body: false, ownTx: true, handler, rateLimits: [] });
const write = (method: 'POST' | 'PUT', path: string, auth: Route['auth'], handler: Handler): Route =>
  ({ method, path, auth, csrf: 'session', body: true, ownTx: true, handler, rateLimits: [] });

const DIAG = '/students/{student_id}/diagnosis/sessions';
/** 诊断六条路由的每会话桶（PLT-16 审查：照 practice.session.minute 的写法；阈值在 config/auth.json，待人确认） */
const DIAG_RL = [{ bucket: 'diagnosis.session.minute', dim: 'session' }] as const;

export const FEATURE_ROUTES: readonly Route[] = [
  read('/students/{student_id}/star-map', 'session', starMap),
  read('/students/{student_id}/onboarding', 'session', onboardingGet),
  write('PUT', '/students/{student_id}/onboarding/answers', 'session', onboardingAnswer),
  write('POST', '/students/{student_id}/onboarding/exams', 'session', onboardingExam),
  write('PUT', '/students/{student_id}/onboarding/textbooks', 'session', onboardingTextbook),
  write('POST', '/students/{student_id}/onboarding/progress', 'session', onboardingProgress),
  write('POST', '/students/{student_id}/events', 'student', events),
  write('POST', '/students/{student_id}/probes', 'student', probes),
  write('POST', '/students/{student_id}/probes/{probe_id}/attempt', 'student', probeAttempt),
  { ...write('POST', DIAG, 'student', diagnosisStart), rateLimits: DIAG_RL },
  { ...read(`${DIAG}/{session_id}`, 'student', diagnosisCurrent), rateLimits: DIAG_RL },
  { ...write('POST', `${DIAG}/{session_id}/probe`, 'student', diagnosisProbe), rateLimits: DIAG_RL },
  { ...write('POST', `${DIAG}/{session_id}/answer`, 'student', diagnosisAnswer), rateLimits: DIAG_RL },
  { ...write('POST', `${DIAG}/{session_id}/pause`, 'student', diagnosisPause), rateLimits: DIAG_RL },
  { ...write('POST', `${DIAG}/{session_id}/abandon`, 'student', diagnosisAbandon), rateLimits: DIAG_RL },
];
