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

const ROUTE = /^\/bff\/v0\/students\/([^/]+)\/star-map$/;
const ONBOARDING = /^\/bff\/v0\/students\/([^/]+)\/onboarding(?:\/(answers|exams|textbooks|progress))?$/;
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
const OK = JSON.stringify({ status: 'ok' });

class BodyError extends Error {
  readonly status: 413 | 422;
  constructor(status: 413 | 422) { super('body'); this.status = status; }
}

/** 读 JSON 请求体：超过 16 KB 413，不是 JSON 对象 422（不回显内容） */
async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
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

export interface StarMapServerOptions {
  pool: Pool;
  devAuth: boolean;
  /** 访问日志；只收到路由、状态码、耗时与 student_id 的哈希，不含星图内容 */
  log?: (line: Record<string, unknown>) => void;
  now?: () => Date;
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
    const send = (status: number, body: string) => {
      record['status'] = status;
      if (res.headersSent || res.destroyed) return;
      res.writeHead(status, {
        'content-type': status >= 400 ? 'application/problem+json' : 'application/json',
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
      });
      res.end(body);
    };
    try {
      const url = new URL(req.url ?? '/', 'http://bff.invalid');
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
        record['student'] = hashId(studentId);
        const account = devAccount(req, server, opts.devAuth);
        if (!account) return send(401, UNAUTHENTICATED);
        const reply = (o: Outcome<unknown>) =>
          o.kind === 'not_found' ? send(404, NOT_FOUND) : o.kind === 'invalid' ? send(422, INVALID) : send(200, o.value === null ? OK : JSON.stringify(o.value));
        if (ob[2] === undefined) return reply(await getOnboarding(opts.pool, account, studentId));
        let body: Record<string, unknown>;
        try {
          body = await readJson(req);
        } catch (e) {
          if (e instanceof BodyError) return send(e.status, e.status === 413 ? TOO_LARGE : INVALID);
          throw e;
        }
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
          rank_band: str(body['rank_band']), scope_note: str(body['scope_note']), difficulty_note: str(body['difficulty_note']),
        };
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
      record['student'] = hashId(studentId);
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

