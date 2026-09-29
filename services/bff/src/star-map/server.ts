// 星图的最小 HTTP 层（spec 第 1 节）：node:http，不引入框架（与 adapters/aya/src/server.ts 同一做法）。
//   GET /bff/v0/students/{student_id}/star-map
// 建档（SIE-01，specs/sie-01-onboarding/spec.md）同一个服务、同一套身份：
//   GET  /bff/v0/students/{student_id}/onboarding             按调用者身份裁剪的已填内容与进度
//   PUT  /bff/v0/students/{student_id}/onboarding/answers     {field_id, value}（学生写自述、家长写家长观察，由库判断）
//   POST /bff/v0/students/{student_id}/onboarding/exams       考试记录
//   PUT  /bff/v0/students/{student_id}/onboarding/textbooks   {subject, textbook_version|null}
//   POST /bff/v0/students/{student_id}/onboarding/progress    {step, status}
//   写入 200 {"status":"ok"}；取值不合要求 422；未授权与不存在同样 404；请求体上限 16 KB。
//   200 成功；401 未登录；404 未授权与不存在（响应体逐字节相同）；Cache-Control: no-store。
// 行为事件与探针（冲刺 2-L，specs/behavior-events-sprint2/spec.md）同一个服务、同一套身份；只写不读，不消耗额度：
//   POST /bff/v0/students/{student_id}/events                     {kind, payload, occurred_at, idempotency_key}
//   POST /bff/v0/students/{student_id}/probes                     {subject, item_id, prediction, asked_at, idempotency_key}
//   POST /bff/v0/students/{student_id}/probes/{probe_id}/attempt  {attempt_id}
//   首次写入 201 {"id"} 与响应头 idempotent-replayed: false；同一幂等键重放 200 {"id": 原 ID} 与 idempotent-replayed: true
//   （与 learning-internal 契约同一约定）；关联作答 200 {"status":"ok"}；401 未登录；403 不是学生本人（家长、其他学生、
//   学生不存在一律同一个 403）；409 IDEMPOTENCY_KEY_REUSED 同一幂等键内容不同（换新键再发，原样重试不会成功）、
//   409 PROBE_ALREADY_LINKED 探针已关联别的作答；422 未知键、自由文本、取值不合要求（不回显输入）；
//   429 24 小时写入上限（0025：事件 2000、探针 500）。
// 数学自适应诊断（SIE-02a，specs/sie-02a-math-diagnosis/spec.md）同一个服务、同一套身份，只认学生本人：
//   POST /bff/v0/students/{student_id}/diagnosis/sessions                          开始 / 续做 {subject, idempotency_key}
//   GET  /bff/v0/students/{student_id}/diagnosis/sessions/{session_id}             当前题（只有题面、序号、题型与进度）
//   POST /bff/v0/students/{student_id}/diagnosis/sessions/{session_id}/probe       {seq, prediction, asked_at, idempotency_key}
//   POST /bff/v0/students/{student_id}/diagnosis/sessions/{session_id}/answer      {seq, answer, work?, elapsed_sec, idempotency_key}
//   POST /bff/v0/students/{student_id}/diagnosis/sessions/{session_id}/pause       {seq?, step_elapsed_sec?, idempotency_key}
//   POST /bff/v0/students/{student_id}/diagnosis/sessions/{session_id}/abandon     {idempotency_key}
//   401 未登录；403 不是学生本人（家长、其他学生、不存在同一个 403）；422 请求体不合要求（不回显）；402 额度不可用；
//   409 步骤已推进或幂等键冲突；503 学习服务不可用（同一请求重试）；没配学习服务时 503 DIAGNOSIS_UNAVAILABLE。接口定义 openapi/diagnosis.v0.yaml。
//
// 身份来源是开发桩（待人确认 #3，4-H 用真实会话替换）：只有同时满足
//   - 创建服务时 devAuth 为真（main.ts 只在 STARBUDDY_DEV_AUTH=1 时传 true）；
//   - 服务实际监听的地址是 loopback；
//   - 请求来自 loopback，并且没有转发头（经过代理的请求也来自 loopback）；
// 才读请求头 x-dev-account-id。任一条件不满足，这个头一律忽略，请求按未登录处理（401）。
import { createHash } from 'node:crypto';
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { Pool } from 'pg';
import { getStarMap, isUuid } from './query.ts';
import { getOnboarding, recordStep, setTextbook, submitAnswer, submitExam, type ExamInput, type Outcome } from '../onboarding/query.ts';
import { linkProbeAttempt, parseAttempt, parseEvent, parseProbe, recordEvent, recordProbe, type Recorded, type WriteOutcome } from '../events/query.ts';
import {
  abandonSession, currentStep, parseAbandon, parseAnswer, parsePause, parseProbe as parseDiagnosisProbe, parseStart, pauseSession,
  startDiagnosis, submitAnswer as submitDiagnosisAnswer, submitProbe, type DiagnosisDeps, type Reply,
} from '../diagnosis/service.ts';

const ROUTE = /^\/bff\/v0\/students\/([^/]+)\/star-map$/;
const ONBOARDING = /^\/bff\/v0\/students\/([^/]+)\/onboarding(?:\/(answers|exams|textbooks|progress))?$/;
// 行为事件与探针：events、probes、probes/{id}/attempt
const EVENTS = /^\/bff\/v0\/students\/([^/]+)\/(events|probes)(?:\/([^/]+)\/(attempt))?$/;
// 数学自适应诊断：sessions、sessions/{id}、sessions/{id}/(probe|answer|pause|abandon)
const DIAGNOSIS = /^\/bff\/v0\/students\/([^/]+)\/diagnosis\/sessions(?:\/([^/]+)(?:\/(probe|answer|pause|abandon))?)?$/;
const MAX_BODY = 16 * 1024;
const LOOPBACK = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);
const FORWARDED_HEADERS = ['forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip'];

const problem = (status: number, code: string, title: string) =>
  JSON.stringify({ type: `/problems/${code.toLowerCase()}`, title, status, code });
// 预先序列化：未授权与不存在共用同一份字节
const NOT_FOUND = problem(404, 'NOT_FOUND', 'student not found');
const UNAUTHENTICATED = problem(401, 'UNAUTHENTICATED', 'sign-in required');
const METHOD_NOT_ALLOWED = problem(405, 'METHOD_NOT_ALLOWED', 'method not allowed');
const INTERNAL = problem(500, 'INTERNAL', 'internal error');
const INVALID = problem(422, 'INVALID', 'invalid request');
const TOO_LARGE = problem(413, 'TOO_LARGE', 'request body too large');
const UNSUPPORTED = problem(415, 'UNSUPPORTED_MEDIA_TYPE', 'content-type must be application/json');
const CONFLICT = problem(409, 'CONFLICT', 'concurrent update, retry');
const TOO_MANY = problem(429, 'TOO_MANY', 'too many writes');
// 行为事件与探针的 409 各有自己的错误码：不复用 CONFLICT 的“retry”文案，同一请求原样重试永远不会成功
const KEY_REUSED = problem(409, 'IDEMPOTENCY_KEY_REUSED', 'idempotency key reused with different content');
const PROBE_LINKED = problem(409, 'PROBE_ALREADY_LINKED', 'probe already linked to a different attempt');
const FORBIDDEN = problem(403, 'FORBIDDEN', 'not allowed');
const DIAGNOSIS_UNAVAILABLE = problem(503, 'DIAGNOSIS_UNAVAILABLE', 'diagnosis is not available');
const OK = JSON.stringify({ status: 'ok' });

class BodyError extends Error {
  readonly status: 413 | 415 | 422;
  constructor(status: 413 | 415 | 422) { super('body'); this.status = status; }
}

/** 读 JSON 请求体：Content-Type 不是 application/json 415，超过 16 KB 413，不是 JSON 对象 422（不回显内容） */
async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const type = (req.headers['content-type'] ?? '').split(';')[0]!.trim().toLowerCase();
  if (type !== 'application/json') throw new BodyError(415);
  const declared = Number(req.headers['content-length'] ?? 0);
  if (declared > MAX_BODY) throw new BodyError(413);
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY) throw new BodyError(413);
    chunks.push(chunk as Buffer);
  }
  try {
    const v = JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown;
    if (v === null || typeof v !== 'object' || Array.isArray(v)) throw new Error('not an object');
    return v as Record<string, unknown>;
  } catch {
    throw new BodyError(422);
  }
}

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
/** 可选字符串：缺省或 null 为 null；其他非字符串类型为 undefined（调用方回 422，不静默当成 null） */
const optStr = (v: unknown): string | null | undefined => (v === undefined || v === null ? null : typeof v === 'string' ? v : undefined);

export interface StarMapServerOptions {
  pool: Pool;
  devAuth: boolean;
  /** 访问日志；只收到路由、状态码、耗时与 student_id 的哈希，不含星图内容 */
  log?: (line: Record<string, unknown>) => void;
  now?: () => Date;
  /** 数学自适应诊断：学习服务客户端等（不配时诊断接口一律 503） */
  diagnosis?: Omit<DiagnosisDeps, 'pool'>;
}

/** 日志里的 student_id 只记哈希前 16 位 */
export const hashId = (id: string): string => createHash('sha256').update(id).digest('hex').slice(0, 16);

function devAccount(req: IncomingMessage, server: Server, devAuth: boolean): string | null {
  if (!devAuth) return null;
  const addr = server.address() as AddressInfo | null;
  if (!addr || !LOOPBACK.has(addr.address)) return null;
  if (!LOOPBACK.has(req.socket.remoteAddress ?? '')) return null;
  // 经过代理（同机反向代理、sidecar）转来的请求也来自 loopback：带转发头时一律不认身份头（安全审查 S1）
  if (FORWARDED_HEADERS.some((h) => req.headers[h] !== undefined)) return null;
  const v = req.headers['x-dev-account-id'];
  return typeof v === 'string' && isUuid(v) ? v : null;
}

export function createStarMapServer(opts: StarMapServerOptions): Server {
  const log = opts.log ?? ((line) => console.log(JSON.stringify(line)));
  const server: Server = createServer(async (req, res) => {
    const started = Date.now();
    const record: Record<string, unknown> = { route: 'unknown' };
    const send = (status: number, body: string, extra: Record<string, string> = {}) => {
      record['status'] = status;
      if (res.headersSent || res.destroyed) return;
      res.writeHead(status, {
        'content-type': status >= 400 ? 'application/problem+json' : 'application/json',
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
        ...extra,
      });
      res.end(body);
    };
    try {
      const url = new URL(req.url ?? '/', 'http://bff.invalid');
      /** 读请求体；出错时已回好错误码，返回 null */
      const readBody = async (): Promise<Record<string, unknown> | null> => {
        try {
          return await readJson(req);
        } catch (e) {
          if (!(e instanceof BodyError)) throw e;
          // 超限或类型不对时不再读剩下的请求体：回完就断开连接
          if (e.status !== 422) res.setHeader('Connection', 'close');
          send(e.status, e.status === 413 ? TOO_LARGE : e.status === 415 ? UNSUPPORTED : INVALID);
          if (e.status !== 422) res.once('finish', () => req.destroy());
          return null;
        }
      };
      const dg = DIAGNOSIS.exec(url.pathname);
      if (dg) {
        const sub = dg[3] ?? (dg[2] ? 'current' : 'start');
        record['route'] = `diagnosis/${sub}`;
        if (req.method !== (sub === 'current' ? 'GET' : 'POST')) return send(405, METHOD_NOT_ALLOWED);
        let studentId: string;
        let sessionId: string;
        try {
          studentId = decodeURIComponent(dg[1]!);
          sessionId = dg[2] === undefined ? '' : decodeURIComponent(dg[2]);
        } catch {
          return send(403, FORBIDDEN);
        }
        record['student'] = hashId(studentId); // 诊断：日志只记哈希，不记作答
        const account = devAccount(req, server, opts.devAuth);
        if (!account) return send(401, UNAUTHENTICATED);
        if (!opts.diagnosis) return send(503, DIAGNOSIS_UNAVAILABLE);
        const deps: DiagnosisDeps = { ...opts.diagnosis, pool: opts.pool };
        const out = (r: Reply) => send(r.status, JSON.stringify(r.body), r.headers ?? {});
        if (sub === 'current') return out(await currentStep(deps, account, studentId, sessionId));
        const b = await readBody();
        if (!b) return;
        if (sub === 'start') {
          const input = parseStart(b);
          return input ? out(await startDiagnosis(deps, account, studentId, input)) : send(422, INVALID);
        }
        if (sub === 'probe') {
          const input = parseDiagnosisProbe(b);
          return input ? out(await submitProbe(deps, account, studentId, sessionId, input)) : send(422, INVALID);
        }
        if (sub === 'answer') {
          const input = parseAnswer(b);
          return input ? out(await submitDiagnosisAnswer(deps, account, studentId, sessionId, input)) : send(422, INVALID);
        }
        if (sub === 'pause') {
          const input = parsePause(b);
          return input ? out(await pauseSession(deps, account, studentId, sessionId, input)) : send(422, INVALID);
        }
        return parseAbandon(b) ? out(await abandonSession(deps, account, studentId, sessionId)) : send(422, INVALID);
      }
      const ev = EVENTS.exec(url.pathname);
      if (ev) {
        const sub = ev[2] === 'events' ? 'events' : ev[4] ? 'probes/attempt' : 'probes';
        record['route'] = sub;
        if (req.method !== 'POST') return send(405, METHOD_NOT_ALLOWED);
        let studentId: string;
        let probeId: string | null;
        try {
          studentId = decodeURIComponent(ev[1]!);
          probeId = ev[3] === undefined ? null : decodeURIComponent(ev[3]);
        } catch {
          return send(403, FORBIDDEN);
        }
        if (ev[2] === 'events' && probeId !== null) return send(404, NOT_FOUND);
        record['student'] = hashId(studentId); // 行为事件与探针：日志只记哈希，不记 payload
        const account = devAccount(req, server, opts.devAuth);
        if (!account) return send(401, UNAUTHENTICATED);
        const b = await readBody();
        if (!b) return;
        const conflict = sub === 'probes/attempt' ? PROBE_LINKED : KEY_REUSED;
        const reply = <T>(o: WriteOutcome<T>, ok: (v: T) => [number, string, Record<string, string>?]) =>
          o.kind === 'forbidden' ? send(403, FORBIDDEN) : o.kind === 'invalid' ? send(422, INVALID)
            : o.kind === 'conflict' ? send(409, conflict) : o.kind === 'too_many' ? send(429, TOO_MANY) : send(...ok(o.value));
        // 首次写入 201；同一幂等键重放 200 与原 ID，响应头标明是重放（库里没有新写任何行）
        const created = (r: Recorded): [number, string, Record<string, string>] =>
          [r.replayed ? 200 : 201, JSON.stringify({ id: r.id }), { 'idempotent-replayed': r.replayed ? 'true' : 'false' }];
        if (sub === 'events') {
          const e = parseEvent(b);
          if (!e) return send(422, INVALID);
          return reply(await recordEvent(opts.pool, account, studentId, e), created);
        }
        if (sub === 'probes') {
          const p = parseProbe(b);
          if (!p) return send(422, INVALID);
          return reply(await recordProbe(opts.pool, account, studentId, p), created);
        }
        const attempt = parseAttempt(b);
        if (!attempt) return send(422, INVALID);
        return reply(await linkProbeAttempt(opts.pool, account, studentId, probeId!, attempt), () => [200, OK]);
      }
      const ob = ONBOARDING.exec(url.pathname);
      if (ob) {
        record['route'] = `onboarding${ob[2] ? `/${ob[2]}` : ''}`;
        const expected = ob[2] === undefined ? 'GET' : ob[2] === 'answers' || ob[2] === 'textbooks' ? 'PUT' : 'POST';
        if (req.method !== expected) return send(405, METHOD_NOT_ALLOWED);
        let studentId: string;
        try {
          studentId = decodeURIComponent(ob[1]!);
        } catch {
          return send(404, NOT_FOUND);
        }
        record['student'] = hashId(studentId); // 建档：日志只记哈希
        const account = devAccount(req, server, opts.devAuth);
        if (!account) return send(401, UNAUTHENTICATED);
        const reply = (o: Outcome<unknown>) =>
          o.kind === 'not_found' ? send(404, NOT_FOUND) : o.kind === 'invalid' ? send(422, INVALID)
            : o.kind === 'conflict' ? send(409, CONFLICT) : o.kind === 'too_many' ? send(429, TOO_MANY)
              : send(200, o.value === null ? OK : JSON.stringify(o.value));
        if (ob[2] === undefined) return reply(await getOnboarding(opts.pool, account, studentId));
        const body = await readBody();
        if (!body) return;
        if (ob[2] === 'answers') {
          const field = str(body['field_id']);
          if (!field) return send(422, INVALID);
          return reply(await submitAnswer(opts.pool, account, studentId, field, body['value']));
        }
        if (ob[2] === 'textbooks') {
          const subject = str(body['subject']);
          const version = body['textbook_version'] === null ? null : str(body['textbook_version']);
          if (!subject || (version === null && body['textbook_version'] !== null)) return send(422, INVALID);
          return reply(await setTextbook(opts.pool, account, studentId, subject, version));
        }
        if (ob[2] === 'progress') {
          const step = str(body['step']);
          const status = str(body['status']);
          if (!step || !status) return send(422, INVALID);
          return reply(await recordStep(opts.pool, account, studentId, step, status));
        }
        const e: ExamInput = {
          subject: str(body['subject']) ?? '', exam_type: str(body['exam_type']) ?? '', exam_date: str(body['exam_date']) ?? '',
          full_score: typeof body['full_score'] === 'number' ? body['full_score'] : NaN,
          raw_score: typeof body['raw_score'] === 'number' ? body['raw_score'] : NaN,
          rank_band: optStr(body['rank_band']), scope_note: optStr(body['scope_note']), difficulty_note: optStr(body['difficulty_note']),
        };
        if (e.rank_band === undefined || e.scope_note === undefined || e.difficulty_note === undefined) return send(422, INVALID);
        if (!e.subject || !e.exam_type || !/^\d{4}-\d{2}-\d{2}$/.test(e.exam_date) || !Number.isFinite(e.full_score) || !Number.isFinite(e.raw_score)) {
          return send(422, INVALID);
        }
        return reply(await submitExam(opts.pool, account, studentId, e));
      }
      const m = ROUTE.exec(url.pathname);
      if (!m) return send(404, NOT_FOUND);
      record['route'] = 'star-map';
      if (req.method !== 'GET') return send(405, METHOD_NOT_ALLOWED);
      let studentId: string;
      try {
        studentId = decodeURIComponent(m[1]!);
      } catch {
        return send(404, NOT_FOUND);
      }
      record['student'] = hashId(studentId); // 星图：日志只记哈希
      const account = devAccount(req, server, opts.devAuth);
      if (!account) return send(401, UNAUTHENTICATED);
      const map = await getStarMap(opts.pool, account, studentId, opts.now);
      if (!map) return send(404, NOT_FOUND);
      send(200, JSON.stringify(map));
    } catch (e) {
      record['error_class'] = e instanceof Error ? e.constructor.name.slice(0, 48) : 'unknown';  // 不记消息，可能带出数据
      send(500, INTERNAL);
    } finally {
      record['duration_ms'] = Date.now() - started;
      log(record);
    }
  });
  server.requestTimeout = 30_000;
  server.headersTimeout = 10_000;
  return server;
}

