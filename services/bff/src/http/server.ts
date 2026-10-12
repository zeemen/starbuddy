// 统一壳的 HTTP 入口（spec 第 3 节）：node:http，一张路由表（routes.ts），一条固定顺序的中间件链：
//   请求 ID → 匹配路由（表外 404）→ 读请求体 → 按 IP 限流 → 解析会话 → 按 auth 拦截 → CSRF → 按号码 / 账号 / 会话 /（账号、学生）限流
//   → withAccount(会话账号) → handler → 统一错误与安全头 → 访问日志
// 与 spec 第 3 节的顺序差异：限流拆成两段——按 IP 的在解析会话之前；按号码、按账号的要先有请求体或会话，放在 CSRF 之后
// （记入 specs/unified-shell-sprint4/lessons.md）。
// 身份只来自会话令牌（请求头 x-sb-session，由 apps/web 从 HttpOnly cookie 转交）经 platform.auth_session_resolve 解析；
// 请求参数、请求体、请求头里的 account_id 一律不读。响应一律 no-store、nosniff，不发任何 CORS 头。
// 请求 ID 只进访问日志，不回响应头：否则“无权”与“不存在”的 404 响应头就不能逐字节相同。
import { randomUUID } from 'node:crypto';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { withAccount } from '../metering/db.ts';
import { P } from './problems.ts';
import { activeRoutes, compile, templateShapes, type Ctx, type Query, type Reply, type Route, type Session, type ShellDeps } from './routes.ts';
import { csrfFor, E164, logHash, rateKey, safeEqual, SESSION_TOKEN } from './secrets.ts';

const MAX_BODY = 16 * 1024;
export const SESSION_HEADER = 'x-sb-session';
export const CSRF_HEADER = 'x-csrf-token';
export const CLIENT_IP_HEADER = 'x-sb-client-ip';

class Stop extends Error {
  readonly reply: Reply;
  constructor(body: string) {
    super('stop');
    this.reply = { status: (JSON.parse(body) as { status: number }).status, body };
  }
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const type = (req.headers['content-type'] ?? '').split(';')[0]!.trim().toLowerCase();
  if (type !== 'application/json') throw new Stop(P.UNSUPPORTED);
  if (Number(req.headers['content-length'] ?? 0) > MAX_BODY) throw new Stop(P.TOO_LARGE);
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY) throw new Stop(P.TOO_LARGE);
    chunks.push(chunk as Buffer);
  }
  try {
    const v = JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown;
    if (v === null || typeof v !== 'object' || Array.isArray(v)) throw new Error('not an object');
    return v as Record<string, unknown>;
  } catch {
    throw new Stop(P.INVALID);
  }
}

const header = (req: IncomingMessage, name: string): string | null => {
  const v = req.headers[name];
  return typeof v === 'string' ? v : null;
};

/**
 * 统一壳路由的请求处理：路径在路由表里（含“形状”对上、参数不合法的）就处理并返回 true；否则返回 false，
 * 由 createShellServer 回 404（表外一律 404）。
 */
export function createShellHandler(deps: ShellDeps): (req: IncomingMessage, res: ServerResponse) => Promise<boolean> {
  // 信任界面、星图 v1、错题再练、复测、家长诉求（PLT-14，开关 family_goals）、月度计划（PLT-14 第 3 步，开关 monthly_plan）
  // 开关关闭的路由不注册（表外 404）
  const routes = activeRoutes(deps.trust?.features, deps.starMap?.features, deps.practice?.enabled === true, deps.retest?.enabled === true,
                              deps.familyGoals?.enabled === true, deps.monthlyPlan?.enabled === true);
  const table = compile(routes);
  const shapes = templateShapes(routes);
  const log = deps.log ?? ((line) => console.log(JSON.stringify(line)));

  const limit = async (bucket: string, dim: string, value: string): Promise<void> => {
    const rule = deps.config.rate_limits[bucket];
    if (!rule) throw new Error(`rate limit ${bucket} missing from config`);
    const { rows } = await deps.pool.query<{ ok: boolean }>('SELECT platform.auth_rate_hit($1, $2, $3, $4) AS ok',
      [bucket, rateKey(deps.keys, dim, value), rule.window_seconds, rule.limit]);
    if (!rows[0]?.ok) throw new Stop(P.TOO_MANY);  // 不论哪条规则、哪个维度超限，都是同一个 429（第二轮 O1）
  };

  return async (req, res) => {
    const url = new URL(req.url ?? '/', 'http://bff.invalid');
    const hit = table.map((c) => ({ c, m: c.re.exec(url.pathname) })).filter((x) => x.m !== null);
    const inTable = hit.length > 0 || shapes.some((s) => s.test(url.pathname));
    if (!inTable) return false;

    const started = Date.now();
    const requestId = randomUUID();
    const record: Record<string, unknown> = { request_id: requestId, route: 'unmatched' };
    const send = (r: Reply) => {
      record['status'] = r.status;
      if (res.headersSent || res.destroyed) return;
      res.writeHead(r.status, {
        'content-type': r.status >= 400 ? 'application/problem+json' : 'application/json',
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
        ...(r.headers ?? {}),
      });
      res.end(r.body);
    };
    try {
      // 形状对上但参数不是 UUID：与“不存在”同一个 404
      if (hit.length === 0) throw new Stop(P.NOT_FOUND);
      const found = hit.find((x) => x.c.route.method === req.method);
      if (!found) throw new Stop(P.METHOD_NOT_ALLOWED);
      const route: Route = found.c.route;
      record['route'] = `${route.method} ${route.path}`;
      const params: Record<string, string> = {};
      found.c.names.forEach((n, i) => { params[n] = found.m![i + 1]!; });
      if (params['student_id']) record['student'] = logHash(deps.keys, params['student_id']);

      // 请求体
      // 家长诉求与月度计划家长端（PLT-14）：学生会话要在任何其他检查之前得到与“不存在”逐字节相同的 404，所以请求体的错误先记下，核对完会话类型再抛
      const guardianOnly404 = route.guardianOnly404 === true;
      let body: Record<string, unknown> | null = null;
      let bodyError: unknown = null;
      if (route.body) {
        try {
          body = await readJson(req);
        } catch (e) {
          if (e instanceof Stop && e.reply.status !== 422) {
            res.setHeader('Connection', 'close');
            res.once('finish', () => req.destroy());
          }
          if (!guardianOnly404) throw e;
          bodyError = e;
        }
      }

      // 按 IP 限流（未登录也要挡）
      const ip = (deps.trustClientIpHeader ? header(req, CLIENT_IP_HEADER) : null) ?? req.socket.remoteAddress ?? 'unknown';
      for (const r of route.rateLimits) if (r.dim === 'ip') await limit(r.bucket, 'ip', ip);

      // 解析会话：只认 x-sb-session 里格式合法的令牌
      const raw = header(req, SESSION_HEADER);
      const presented = raw !== null && SESSION_TOKEN.test(raw) ? raw : null;
      let session: Session | null = null;
      if (route.auth !== 'public' && presented) {
        const { rows } = await deps.pool.query<{ account_id: string; account_kind: 'guardian' | 'student'; origin: string }>(
          'SELECT account_id, account_kind::text AS account_kind, origin FROM platform.auth_session_resolve($1)', [presented]);
        if (rows[0]) session = { token: presented, accountId: rows[0].account_id, kind: rows[0].account_kind, origin: rows[0].origin };
      }

      // 按 auth 拦截：未登录 401；账号类型不对 403（在任何数据库查询之前，与路径里的学生是谁无关）
      if (route.auth !== 'public') {
        if (!session) throw new Stop(P.UNAUTHENTICATED);
        record['account_kind'] = session.kind;
        if ((route.auth === 'guardian' && session.kind !== 'guardian') || (route.auth === 'student' && session.kind !== 'student')) {
          throw new Stop(P.FORBIDDEN);
        }
      }
      // 家长诉求：学生会话一律 404（在请求体错误、CSRF、限流之前；与“不存在”的响应逐字节相同，学生端不知道有这个接口）
      if (guardianOnly404 && session!.kind !== 'guardian') throw new Stop(P.NOT_FOUND);
      if (bodyError !== null) throw bodyError;

      // CSRF：改变状态的请求 Origin 必须恰好是壳的来源；有会话的还要带与会话绑定的令牌（双重提交，apps/web 从表单转交）
      if (route.csrf !== 'none') {
        const origin = header(req, 'origin');
        if (origin === null || !safeEqual(origin, deps.shellOrigin)) throw new Stop(P.CSRF);
        if (route.csrf === 'session') {
          const t = header(req, CSRF_HEADER);
          if (t === null || !safeEqual(t, csrfFor(deps.keys, session!.token))) throw new Stop(P.CSRF);
        }
      }

      // 按号码、按账号限流
      for (const r of route.rateLimits) {
        if (r.dim === 'phone') {
          const phone = body?.['phone'];
          if (typeof phone === 'string' && E164.test(phone)) await limit(r.bucket, 'phone', phone);
        } else if (r.dim === 'account') {
          await limit(r.bucket, 'account', session!.accountId);
        } else if (r.dim === 'session') {
          await limit(r.bucket, 'session', session!.token);  // 键是令牌的带密钥 HMAC（rateKey），不存令牌本身
        } else if (r.dim === 'link') {
          // （会话账号、路径里的学生）一对：在授权之前计数，按学生单独计数会让无权的家长替别人的孩子把次数用光
          await limit(r.bucket, 'link', `${session!.accountId}:${params['student_id'] ?? ''}`);
        }
      }

      const note = (k: string, v: string) => { record[k] = v; };
      let reply: Reply;
      if (session && route.ownTx) {
        // handler 自己按会话账号开事务（feature-routes.ts）；这里不给查询入口，防止在事务外查询
        const q: Query = () => Promise.reject(new Error('route manages its own transaction'));
        reply = await route.handler({ deps, params, body, session, q, presentedToken: presented, note });
      } else if (session) {
        const s = session;
        // 会话账号只在本事务内生效（set_config 第三个参数 true），连接池复用不串号（第二轮 N4）
        reply = await withAccount(deps.pool, s.accountId, (client) => {
          const q: Query = (sql, p) => client.query(sql, p);
          const ctx: Ctx = { deps, params, body, session: s, q, presentedToken: presented, note };
          return route.handler(ctx);
        });
      } else {
        const q: Query = (sql, p) => deps.pool.query(sql, p);
        reply = await route.handler({ deps, params, body, session: null, q, presentedToken: presented, note });
      }
      send(reply);
    } catch (e) {
      if (e instanceof Stop) {
        send(e.reply);
      } else {
        record['error_class'] = e instanceof Error ? e.constructor.name.slice(0, 48) : 'unknown';  // 不记消息，可能带出数据
        send({ status: 500, body: P.INTERNAL });
      }
    } finally {
      record['duration_ms'] = Date.now() - started;
      log(record);
    }
    return true;
  };
}

/** BFF 唯一的 HTTP 入口（src/main.ts）：表外路径一律 404 */
export function createShellServer(deps: ShellDeps): Server {
  const handle = createShellHandler(deps);
  const server = createServer((req, res) => {
    void handle(req, res).then((handled) => {
      if (handled) return;
      res.writeHead(404, { 'content-type': 'application/problem+json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' });
      res.end(P.NOT_FOUND);
    });
  });
  server.requestTimeout = 30_000;
  server.headersTimeout = 10_000;
  return server;
}
