// HTTP 健康端点（LT-03 范围第 4 条，specs/launch-lt03-cloud-staging；接口定义 openapi/health.v0.yaml，不在统一壳路由表里）。
//   GET /healthz  存活：不访问任何依赖，200 {"status":"ok"}
//   GET /readyz   就绪：启动核对（运行环境、开关、密钥、同意版本、白名单 OTP、登录角色断言 assertBffRole、上游地址、staging 连库 TLS）
//                 已在 main.ts 通过才会起监听；这里再在 READY_TIMEOUT_MS 内从连接池借一条连接 SELECT 1：200 {"status":"ready"}，
//                 否则 503 {"status":"not_ready"}。超时就把这条连接销毁（release(true)），查询随连接作废，不留在池里继续占用
//                 （安全审查采纳建议：超时丢弃查询）。并发的探针共用同一次检查（single-flight），结果缓存 READY_CACHE_MS
// 无需会话、不限流、不记访问日志（探针每 10 秒一次）；响应只含状态（不带版本、库名、错误类别），一律 no-store、nosniff。
// 路径逐字相等（不收查询串、尾斜杠）；其他方法 405。路径在 /bff/v0 前缀之外，与统一壳路由表互不重叠。
import type { IncomingMessage, ServerResponse } from 'node:http';

export const HEALTH_PATH = '/healthz';
export const READY_PATH = '/readyz';
export const READY_TIMEOUT_MS = 2000;
export const READY_CACHE_MS = 3000;

export interface ReadyClient {
  query: (sql: string) => Promise<{ rows: Array<Record<string, unknown>> }>;
  release: (destroy?: boolean | Error) => void;
}
export interface Connectable { connect: () => Promise<ReadyClient> }

const HEADERS = { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } as const;

/** 连接池能在时限内借到连接并 SELECT 1；超时、抛错一律 false（不区分原因）。超时或出错的连接销毁、不还回池里 */
export async function poolReady(pool: Connectable, timeoutMs = READY_TIMEOUT_MS): Promise<boolean> {
  let timer: NodeJS.Timeout | undefined;
  let finished = false;
  let client: ReadyClient | undefined;
  let released = false;
  const release = (destroy: boolean) => {
    if (client === undefined || released) return;
    released = true;
    try { client.release(destroy); } catch { /* 释放失败不影响结果 */ }
  };
  const timeout = new Promise<boolean>((resolve) => {
    timer = setTimeout(() => { finished = true; release(true); resolve(false); }, timeoutMs);
  });
  const work = (async () => {
    client = await pool.connect();
    if (finished) { release(true); return false; }  // 借到连接时已超时：直接销毁
    try {
      const r = await client.query('SELECT 1 AS ok');
      release(false);
      return r.rows[0]?.['ok'] === 1;
    } catch {
      release(true);
      return false;
    }
  })().catch(() => false);
  try {
    return await Promise.race([work, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** 就绪检查的 single-flight + 短缓存：同一时刻只有一次检查在跑，结果在 ttlMs 内复用（探针并发、多副本同时探测时不放大到库） */
export function cachedReady(check: () => Promise<boolean>, ttlMs = READY_CACHE_MS, now: () => number = Date.now,
                            onResult?: (ok: boolean) => void): () => Promise<boolean> {
  let inflight: Promise<boolean> | undefined;
  let last: { at: number; ok: boolean } | undefined;
  return () => {
    if (last !== undefined && now() - last.at < ttlMs) return Promise.resolve(last.ok);
    if (inflight !== undefined) return inflight;
    inflight = check().catch(() => false).then((ok) => {
      last = { at: now(), ok };
      inflight = undefined;
      onResult?.(ok);  // LT-05：最近一次结果给指标 starbuddy_ready（采集时不另查库）
      return ok;
    });
    return inflight;
  };
}

/** 是健康端点就应答并返回 true；否则返回 false，交给统一壳的处理 */
export async function handleHealth(req: IncomingMessage, res: ServerResponse, ready: () => Promise<boolean>): Promise<boolean> {
  const raw = req.url ?? '';
  if (raw !== HEALTH_PATH && raw !== READY_PATH) return false;
  const send = (status: number, body: Record<string, string>, extra: Record<string, string> = {}) => {
    if (res.headersSent || res.destroyed) return;
    res.writeHead(status, { ...HEADERS, ...extra });
    res.end(JSON.stringify(body));
  };
  if (req.method !== 'GET') {
    send(405, { status: 'method_not_allowed' }, { allow: 'GET' });
    return true;
  }
  if (raw === HEALTH_PATH) {
    send(200, { status: 'ok' });
    return true;
  }
  let ok = false;
  try {
    ok = await ready();
  } catch {
    ok = false;
  }
  if (ok) send(200, { status: 'ready' });
  else send(503, { status: 'not_ready' });
  return true;
}
