// 计量驱动桩（契约审查 H1）：在本机一次性栈里直接运行 BFF 的 meteredCall（services/bff/src/metering/meter.ts），
// 让压测与降级测试的批改走 BFF 真实的计量语义：预留 → 学习服务（结果按 classifyResponse 归类）→ 确定成功结算 /
// 确定失败释放 / 结果未知按 1、2、4、8 s 退避同键重试（预算为预留剩余时间的一半），仍未知则不结算不释放；
// 同键重试遇到 existing 时按 state 处理（released 直接返回、expired 只查不执行）；请求指纹用 BFF 的 meteringFingerprint，
// 内容指纹用 BFF 的 itemFingerprint。不修改 BFF 代码，只 import。
//
// 与 BFF 的唯一差别（传输层，报告与 lessons 注明）：BFF 的 httpLearningClient 发 /internal/v0/{method}；本栈的适配器按契约 1.0.0
// 启动（同 tools/e2e-cohort），所以这里把同一个请求体包成 v1 信封 {request, ingest} 发 /internal/v1/{method}，响应仍由 BFF 的
// classifyResponse 归类；重放用 BFF 的 httpLearningReplay 原样调用。
//
// 只监听 127.0.0.1；地址与令牌只经环境变量传入（HARNESS_DATABASE_URL、HARNESS_LEARNING_URL、HARNESS_LEARNING_TOKEN、PORT）。
// HARNESS_RETRY_SCALE：退避时长按比例缩短（压测与测试用，口径不变）；HARNESS_TIMEOUT_MS：学习服务请求超时（BFF 为 15 000）。
// 计量时钟：meteredCall 的 now 与数据库 platform.meter_now() 对齐：调用方（以管理员连接读计量时钟）经 POST /clock-sync
// 传入 {meter_now_ms}；BFF 角色没有 meter_now 的执行权限，这里也不去要。
import http from 'node:http';
import { createRequire } from 'node:module';
import { itemFingerprint } from '../../../services/bff/src/metering/content-fingerprint.ts';
import { classifyResponse, httpLearningReplay, type LearningCall } from '../../../services/bff/src/metering/learning-client.ts';
import { meteredCall, type MeteredResource } from '../../../services/bff/src/metering/meter.ts';

const require = createRequire(new URL('../../../services/bff/package.json', import.meta.url));
const pg = require('pg') as typeof import('pg');

const env = (k: string): string => {
  const v = process.env[k];
  if (!v) {
    console.error(`meter-harness: 缺少 ${k}`);
    process.exit(2);
  }
  return v;
};
const learningUrl = env('HARNESS_LEARNING_URL').replace(/\/$/, '');
const token = env('HARNESS_LEARNING_TOKEN');
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(learningUrl)) {
  console.error('meter-harness: 学习服务地址只能是本机回环');
  process.exit(2);
}
const pool = new pg.Pool({ connectionString: env('HARNESS_DATABASE_URL'), max: Number(process.env['HARNESS_POOL_MAX'] ?? 20) });
const scale = Number(process.env['HARNESS_RETRY_SCALE'] ?? 1);
const timeoutMs = Number(process.env['HARNESS_TIMEOUT_MS'] ?? 15_000);
const delays = [1000, 2000, 4000, 8000].map((d) => Math.round(d * scale));  // 与 meter.ts DEFAULT_DELAYS 同口径

let offset = 0;

const replay = httpLearningReplay({ baseUrl: learningUrl, token });

function learningFor(counter: { calls: number }): LearningCall {
  return async (method, body) => {
    counter.calls += 1;
    let res: Response;
    try {
      res = await fetch(`${learningUrl}/internal/v1/${method}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify({ request: body, ingest: { channel: 'keypad' } }),
        signal: AbortSignal.timeout(timeoutMs),
        redirect: 'error',
      });
    } catch (err) {
      return { kind: 'unknown', reason: err instanceof Error ? err.name : 'fetch failed' };
    }
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      if (res.ok) return { kind: 'unknown', reason: 'unreadable success body' };
    }
    return classifyResponse(res.status, parsed, res.headers.get('idempotent-replayed') === 'true');
  };
}

interface CallInput {
  accountId: string;
  studentId: string;
  resourceType: MeteredResource;
  idempotencyKey: string;
  body: Record<string, unknown>;
  itemRef?: string;
}

async function readJson(req: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const c of req) {
    size += (c as Buffer).length;
    if (size > 1 << 20) throw new Error('too large');
    chunks.push(c as Buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

const server = http.createServer(async (req, res) => {
  const send = (status: number, obj: unknown) => {
    const raw = JSON.stringify(obj);
    res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(raw) });
    res.end(raw);
  };
  try {
    if (req.method === 'POST' && req.url === '/clock-sync') {
      const b = (await readJson(req)) as { meter_now_ms?: unknown };
      if (typeof b.meter_now_ms !== 'number' || !Number.isFinite(b.meter_now_ms)) return send(400, { error: 'meter_now_ms required' });
      offset = b.meter_now_ms - Date.now();
      return send(200, { offset_ms: Math.round(offset) });
    }
    if (req.method !== 'POST' || req.url !== '/metered-call') return send(404, { error: 'not found' });
    const input = (await readJson(req)) as CallInput;
    const counter = { calls: 0 };
    const t0 = Date.now();
    const result = await meteredCall({
      pool, learning: learningFor(counter), replay, retryDelaysMs: delays, now: () => Date.now() + offset,
    }, {
      accountId: input.accountId, studentId: input.studentId, resourceType: input.resourceType,
      idempotencyKey: input.idempotencyKey, body: input.body,
      ...(input.itemRef ? { contentFingerprint: itemFingerprint(input.itemRef) } : {}),
    });
    return send(200, { result, learning_calls: counter.calls, elapsed_ms: Date.now() - t0 });
  } catch (err) {
    // 只回类别，不回异常消息（可能带出请求内容）
    return send(500, { error: err instanceof TypeError ? 'invalid_input' : 'harness_error', class: err instanceof Error ? err.name : 'unknown' });
  }
});

server.listen(Number(env('PORT')), '127.0.0.1', () => console.log(JSON.stringify({ event: 'listening' })));
const stop = () => server.close(() => void pool.end());
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
