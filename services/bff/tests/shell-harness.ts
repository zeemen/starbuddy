// 集成测试用的真实会话（specs/unified-shell-sprint4 H2：开发身份桩删除后，星图、建档、事件、诊断的测试一律走真实会话）。
// 起一个 createShellServer（与生产同一个入口、同一条中间件链），会话只经 HTTP 登录流程产生：
//   家长：POST /auth/otp/request → 从短信捕获桩读验证码 → POST /auth/otp/verify；
//   学生：家长登录后 POST /students/{id}/student-session（打开学生模式；需要的同意记录由超级用户补写合成记录）。
// 限流阈值放宽（测试一次要登录很多账号；限流本身在 auth.integration.test.ts 按 config/auth.json 原值测）。
// 全部合成：号码用未分配的国家码 +999（规则 8）。
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type pg from 'pg';
import { loadAuthConfig, randomKeys, type AuthConfig, type Keys } from '../src/http/config.ts';
import type { ShellDeps } from '../src/http/routes.ts';
import { createShellServer } from '../src/http/server.ts';
import { CaptureSmsSender } from '../src/http/sms.ts';

export const ORIGIN = 'http://shell.test';

export interface Sess { token: string; csrf: string }

export interface ShellHarness {
  base: string;
  server: Server;
  keys: Keys;
  /** 这个服务用的阈值配置（同一个对象：测试可以临时改某个桶的阈值来测限流） */
  config: AuthConfig;
  sms: CaptureSmsSender;
  /** 某个账号的真实会话（同一账号复用；学生会话每次都经家长打开学生模式） */
  sessionFor: (accountId: string) => Promise<Sess>;
  /** 作废缓存（账号状态变了、登出之后） */
  forget: (accountId: string) => void;
  close: () => Promise<void>;
}

export interface CallOpts { headers?: Record<string, string>; contentType?: string; rawBody?: string; origin?: string | null; csrf?: string | null }

/** 以会话身份调 BFF：带 x-sb-session；改变状态的请求另带 Origin 与 x-csrf-token（与 apps/web 转交的一样） */
export async function callAs(base: string, method: string, path: string, sess: Sess | null, body?: unknown, o: CallOpts = {})
  : Promise<{ status: number; headers: Headers; text: string; body: any }> {
  const headers: Record<string, string> = { ...(o.headers ?? {}) };
  if (sess) headers['x-sb-session'] = sess.token;
  if (method !== 'GET') {
    const origin = o.origin === undefined ? ORIGIN : o.origin;
    if (origin !== null) headers['origin'] = origin;
    const csrf = o.csrf === undefined ? sess?.csrf : o.csrf;
    if (csrf) headers['x-csrf-token'] = csrf;
  }
  const payload = o.rawBody ?? (body === undefined ? undefined : JSON.stringify(body));
  if (payload !== undefined) headers['content-type'] = o.contentType ?? 'application/json';
  const r = await fetch(`${base}${path.startsWith('/bff') ? path : `/bff/v0${path}`}`, { method, headers, body: payload });
  const text = await r.text();
  let json: any = null;
  try { json = JSON.parse(text); } catch { /* 非 JSON */ }
  return { status: r.status, headers: r.headers, text, body: json };
}

let phoneSeq = 0;

export async function startShell(pool: pg.Pool, su: pg.Pool,
                                 extra: Partial<Pick<ShellDeps, 'log' | 'diagnosis' | 'now' | 'shell' | 'trust' | 'starMap' | 'practice' | 'retest'
                                                     | 'familyGoals' | 'monthlyPlan' | 'subjectGate'>> = {})
  : Promise<ShellHarness> {
  const base0 = loadAuthConfig();
  const config: AuthConfig = { ...base0, rate_limits: Object.fromEntries(Object.entries(base0.rate_limits)
    .map(([k, v]) => [k, { ...v, limit: v.limit * 10_000 }])) };
  const keys = randomKeys();
  const sms = new CaptureSmsSender('test');
  const server = createShellServer({ pool, config, keys, env: 'test', sms, shellOrigin: ORIGIN, ...extra });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const cache = new Map<string, Sess>();

  const phoneOf = async (guardian: string): Promise<string> => {
    const { rows } = await su.query<{ phone_e164: string }>('SELECT phone_e164 FROM platform.account_contact WHERE account_id = $1', [guardian]);
    if (rows[0]) return rows[0].phone_e164;
    // 合成号码：+99977 + 进程号与序号，保证同一测试库里不重复（同号多个家长账号会被拒绝登录）
    const phone = `+99977${String(process.pid % 10000).padStart(4, '0')}${String(++phoneSeq).padStart(4, '0')}`;
    await su.query('INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES ($1, $2, now())', [guardian, phone]);
    return phone;
  };

  const loginGuardian = async (guardian: string): Promise<Sess> => {
    const phone = await phoneOf(guardian);
    const req = await callAs(base, 'POST', '/auth/otp/request', null, { phone });
    if (req.status !== 202) throw new Error(`otp request ${req.status}`);
    const code = sms.latest(phone);
    if (!code) throw new Error('no code captured');
    const v = await callAs(base, 'POST', '/auth/otp/verify', null, { phone, code });
    if (v.status !== 200) throw new Error(`otp verify ${v.status} ${v.text}`);
    return { token: v.body.session_token, csrf: v.body.csrf_token };
  };

  const openStudent = async (studentAccount: string): Promise<Sess> => {
    const { rows } = await su.query<{ student: string; guardian: string | null }>(
      `SELECT s.id AS student,
              (SELECT l.guardian_account_id FROM platform.guardian_student_link l JOIN platform.account g ON g.id = l.guardian_account_id
                WHERE l.student_id = s.id AND l.status = 'active' AND g.status = 'active' ORDER BY l.created_at LIMIT 1) AS guardian
         FROM platform.student_profile s WHERE s.account_id = $1`, [studentAccount]);
    const r = rows[0];
    if (!r || !r.guardian) throw new Error('test fixture: student needs an active linked guardian to open student mode');
    // 打开学生模式需要的同意记录（合成版本号，只在 test 环境可用）
    await su.query(`INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                    SELECT $1, $2, t, replace(t, '_', '-') || '@synthetic-0', 'granted'
                      FROM unnest(ARRAY['privacy_policy', 'guardian_consent_under14']) t`, [r.student, r.guardian]);
    const g = await loginGuardian(r.guardian);
    const o = await callAs(base, 'POST', `/students/${r.student}/student-session`, g);
    if (o.status !== 200) throw new Error(`student-session ${o.status} ${o.text}`);
    return { token: o.body.session_token, csrf: o.body.csrf_token };
  };

  const sessionFor = async (accountId: string): Promise<Sess> => {
    const hit = cache.get(accountId);
    if (hit) return hit;
    const { rows } = await su.query<{ kind: string }>('SELECT kind::text FROM platform.account WHERE id = $1', [accountId]);
    const kind = rows[0]?.kind;
    if (kind !== 'guardian' && kind !== 'student') throw new Error(`test fixture: account ${accountId} is ${kind ?? 'missing'}`);
    const s = kind === 'guardian' ? await loginGuardian(accountId) : await openStudent(accountId);
    cache.set(accountId, s);
    return s;
  };

  return {
    base, server, keys, config, sms, sessionFor,
    forget: (a) => { cache.delete(a); },
    close: async () => {
      server.closeAllConnections();
      await new Promise<void>((r) => server.close(() => r()));
    },
  };
}

/**
 * 与原来测试里的 fetch 包装同形：account 为空时不带会话；改变状态的请求带 Origin 与该会话的 CSRF 令牌；
 * body 是字符串时原样发送（测试非法 JSON）。
 */
export async function fetchAs(shell: ShellHarness, method: string, u: string, account?: string, body?: unknown,
                              headers: Record<string, string> = {}): Promise<Response> {
  const h: Record<string, string> = {};
  if (account) {
    const s = await shell.sessionFor(account);
    h['x-sb-session'] = s.token;
    if (method !== 'GET') h['x-csrf-token'] = s.csrf;
  }
  if (method !== 'GET') h['origin'] = ORIGIN;
  if (body !== undefined) h['content-type'] = 'application/json';
  return fetch(u, { method, headers: { ...h, ...headers },
                    body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body) });
}
