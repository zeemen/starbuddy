// 统一壳 v0 路由表（specs/unified-shell-sprint4/spec.md 第 3 节）：H1 的认证、会话、/me、建档、同意、打开学生模式，
// 加 H2 迁进来的星图、建档问卷、行为事件与探针、数学诊断（feature-routes.ts）。
// 分发只查这张表；表外路径一律 404（BFF 只有 server.ts 的 createShellServer 一个入口）。
// 每条路由声明：方法、路径模板、auth（public / session / guardian / student）、csrf（改变状态的一律开）、限流、是否读请求体、handler。
// handler 拿到的账号只来自会话（ctx.session），请求参数、请求体、请求头里的 account_id 一律不读（第二轮 N3）。
// 路径里的 student_id 只作查询条件，授权交给数据库（RLS 与 SECURITY DEFINER 函数）；无权与不存在同一个 404。
import type { Pool, PoolClient, QueryResultRow } from 'pg';
import type { DiagnosisDeps } from '../diagnosis/service.ts';
import type { ShellConfig } from '../shell/config.ts';
import type { InsightClient } from '../shell/insight-client.ts';
import type { PlanClient } from '../shell/plan-client.ts';
import type { RestStore } from '../shell/rest.ts';
import type { AuthConfig, Keys, RunEnv } from './config.ts';
import { FEATURE_ROUTES } from './feature-routes.ts';
import { SHELL_ROUTES } from './shell-routes.ts';
import { P } from './problems.ts';
import { codeMac, csrfFor, E164, newCode } from './secrets.ts';
import type { SmsSender } from './sms.ts';

export type AuthLevel = 'public' | 'session' | 'guardian' | 'student';
/** none：不改变状态；origin：只核对 Origin（登录前没有会话）；session：Origin + 与会话绑定的 CSRF 令牌 */
export type CsrfMode = 'none' | 'origin' | 'session';
export type RateDim = 'ip' | 'phone' | 'account' | 'session';
export interface RateLimit { bucket: string; dim: RateDim }

export interface Session { token: string; accountId: string; kind: 'guardian' | 'student'; origin: string }

export interface ShellDeps {
  pool: Pool;
  config: AuthConfig;
  keys: Keys;
  env: RunEnv;
  sms: SmsSender;
  /** 壳的来源（例如 https://app.example.cn），改变状态的请求 Origin 必须恰好等于它 */
  shellOrigin: string;
  /** 为 true 时客户端 IP 取 apps/web 转交的 x-sb-client-ip（BFF 只对 apps/web 开放时才能打开） */
  trustClientIpHeader?: boolean;
  /** 访问日志；只收到请求 ID、路由模板、状态码、耗时、账号类型、student_id 的 HMAC */
  log?: (line: Record<string, unknown>) => void;
  /** 数学自适应诊断：学习服务客户端等（不配时诊断路由一律 503） */
  diagnosis?: Omit<DiagnosisDeps, 'pool'>;
  /** 星图的生成时间、连续使用提醒的时钟（测试注入） */
  now?: () => Date;
  /** 统一壳 H4（shell-routes.ts）：显示规则（缺省读 config/shell.json）、学习服务的今日计划、洞察服务的 insight.explain、
   *  连续使用提醒的存储（缺省进程内）。不配 plan 时每一科“今天暂无安排”；不配 insight 时不显示任何判断陈述 */
  shell?: { config?: ShellConfig; plan?: PlanClient; insight?: InsightClient; rest?: RestStore };
}

export type Query = <R extends QueryResultRow = QueryResultRow>(sql: string, params?: unknown[]) => Promise<{ rows: R[] }>;

export interface Ctx {
  deps: ShellDeps;
  params: Record<string, string>;
  body: Record<string, unknown> | null;
  session: Session | null;
  /** 会话路由：本次请求的事务（app.account_id 已设为会话账号）；公开路由：连接池 */
  q: Query;
  /** 请求头里原样带来的会话令牌（格式合法才有）：登录时用来作废本设备旧会话 */
  presentedToken: string | null;
  /** handler 可以记日志的额外字段（只放不含个人信息的枚举值） */
  note: (k: string, v: string) => void;
}

export interface Reply { status: number; body: string; headers?: Record<string, string> }
export type Handler = (ctx: Ctx) => Promise<Reply>;

export interface Route {
  method: 'GET' | 'POST' | 'PUT';
  path: string;
  auth: AuthLevel;
  csrf: CsrfMode;
  rateLimits: readonly RateLimit[];
  body: boolean;
  /** handler 自己按会话账号开事务（复用的查询模块都在 withAccount 里执行）；中间件不另开事务，ctx.q 不可用 */
  ownTx?: boolean;
  handler: Handler;
}

const json = (status: number, v: unknown): Reply => ({ status, body: JSON.stringify(v) });
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const OK = json(200, { status: 'ok' });
const pgCode = (e: unknown): string => (e && typeof e === 'object' && 'code' in e ? String((e as { code: unknown }).code) : '');

/** 请求体只能有这些键（未知键 422，不静默忽略） */
const onlyKeys = (b: Record<string, unknown> | null, allowed: string[]): b is Record<string, unknown> =>
  b !== null && Object.keys(b).every((k) => allowed.includes(k));

// ---------------------------------------------------------------- 认证

const otpRequest: Handler = async (ctx) => {
  const b = ctx.body;
  if (!onlyKeys(b, ['phone']) || typeof b['phone'] !== 'string' || !E164.test(b['phone'])) return problem(P.INVALID);
  const phone = b['phone'];
  const code = newCode();
  const { config, keys } = ctx.deps;
  await ctx.q('SELECT platform.auth_otp_issue($1, $2, $3, $4)', [phone, codeMac(keys, phone, code), config.otp.ttl_seconds, config.otp.max_attempts]);
  try {
    await ctx.deps.sms.sendLoginCode(phone, code);
  } catch {
    ctx.note('sms', 'send_failed');  // 不告诉请求方：注册与否、发没发成功，响应都相同
  }
  return json(202, { status: 'accepted' });
};

const otpVerify: Handler = async (ctx) => {
  const b = ctx.body;
  if (!onlyKeys(b, ['phone', 'code'])) return problem(P.INVALID);
  const phone = b['phone'];
  const code = b['code'];
  // 号码或验证码格式不对：与码错同一个 401（不区分原因）
  if (typeof phone !== 'string' || !E164.test(phone) || typeof code !== 'string' || !/^[0-9]{6}$/.test(code)) return problem(P.LOGIN_FAILED);
  const life = ctx.deps.config.session.guardian;
  const { rows } = await ctx.q<{ outcome: string; session_token: string | null }>(
    'SELECT outcome, session_token FROM platform.auth_otp_login($1, $2, $3, $4, $5)',
    [phone, codeMac(ctx.deps.keys, phone, code), life.absolute_seconds, life.idle_seconds, ctx.presentedToken]);
  const r = rows[0];
  ctx.note('outcome', r?.outcome ?? 'none');
  if (r?.outcome === 'ok' && r.session_token) {
    return json(200, { account_kind: 'guardian', session_token: r.session_token, csrf_token: csrfFor(ctx.deps.keys, r.session_token) });
  }
  if (r?.outcome === 'contact_support') return problem(P.CONTACT_SUPPORT);
  return problem(P.LOGIN_FAILED);
};

const logout: Handler = async (ctx) => {
  await ctx.q('SELECT platform.auth_session_revoke($1)', [ctx.session!.token]);
  return OK;
};

const logoutAll: Handler = async (ctx) => {
  await ctx.q('SELECT platform.auth_session_revoke_all($1)', [ctx.session!.token]);
  return OK;
};

// ---------------------------------------------------------------- 账号

const me: Handler = async (ctx) => {
  const s = ctx.session!;
  if (s.kind === 'student') {
    const { rows } = await ctx.q<{ id: string }>('SELECT s.id FROM platform.student_profile s WHERE s.account_id = platform.current_account_id()');
    return json(200, { account_kind: 'student', student_id: rows[0]?.id ?? null });
  }
  // 家长：已绑定孩子的档案 ID 与年级（档案没有姓名，界面按“孩子 1 · 八年级”区分，待人确认 #12）
  const { rows } = await ctx.q<{ student_id: string; grade: number }>(
    `SELECT l.student_id, s.grade FROM platform.guardian_student_link l
       JOIN platform.student_profile s ON s.id = l.student_id
      WHERE l.status = 'active' AND l.guardian_account_id = platform.current_account_id()
      ORDER BY l.created_at, l.student_id`);
  return json(200, { account_kind: 'guardian', children: rows.map((r) => ({ student_id: r.student_id, grade: r.grade })) });
};

// ---------------------------------------------------------------- 家长：建档、同意、打开学生模式

const REGION = /^[a-z][a-z0-9-]{1,31}$/;
const TEXTBOOK = /^[a-z0-9][a-z0-9-]{1,31}$/;
const YEAR_MONTH = /^(19|20)[0-9]{2}-(0[1-9]|1[0-2])$/;

const createStudent: Handler = async (ctx) => {
  const b = ctx.body;
  if (!onlyKeys(b, ['grade', 'region', 'textbook_version', 'birth_year_month'])) return problem(P.INVALID);
  const grade = b['grade'];
  const region = b['region'];
  const textbook = b['textbook_version'] ?? null;
  const birth = b['birth_year_month'] ?? null;
  if (typeof grade !== 'number' || !Number.isInteger(grade) || grade < 7 || grade > 9 || typeof region !== 'string' || !REGION.test(region)
      || (textbook !== null && (typeof textbook !== 'string' || !TEXTBOOK.test(textbook)))
      || (birth !== null && (typeof birth !== 'string' || !YEAR_MONTH.test(birth)))) {
    return problem(P.INVALID);
  }
  try {
    // 第二个孩子建在自己创建的家庭里；还没有家庭时由函数新建
    const { rows } = await ctx.q<{ id: string }>(
      `SELECT platform.create_student(
         (SELECT h.id FROM platform.household h WHERE h.created_by_account = platform.current_account_id() ORDER BY h.created_at, h.id LIMIT 1),
         $1::smallint, $2, $3, $4::date) AS id`, [grade, region, textbook, birth === null ? null : `${birth as string}-01`]);
    return json(201, { student_id: rows[0]!.id });
  } catch (e) {
    if (/^2[23]/.test(pgCode(e))) return problem(P.INVALID);
    throw e;
  }
};

const CONSENT_TYPES = ['privacy_policy', 'guardian_consent_under14', 'voice_processing', 'pilot_participation'];
const VERSIONED = /^[a-z0-9][a-z0-9-]{0,47}@[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;
/** 合成的同意文本版本号（synthetic-*）只在 dev / test / staging 可用；pilot 与生产在合规交付前不能记同意，也就打不开学生模式（待人确认 #13） */
export const syntheticConsentAllowed = (env: RunEnv): boolean => env === 'dev' || env === 'test' || env === 'staging';

const recordConsent: Handler = async (ctx) => {
  const b = ctx.body;
  if (!onlyKeys(b, ['consent_type', 'document_version', 'decision'])) return problem(P.INVALID);
  const type = b['consent_type'];
  const version = b['document_version'];
  const decision = b['decision'];
  if (typeof type !== 'string' || !CONSENT_TYPES.includes(type) || typeof version !== 'string' || !VERSIONED.test(version)
      || (decision !== 'granted' && decision !== 'revoked')) {
    return problem(P.INVALID);
  }
  if (version.split('@')[1]!.startsWith('synthetic-') && !syntheticConsentAllowed(ctx.deps.env)) return problem(P.INVALID);
  try {
    await ctx.q(
      `INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
       VALUES ($1, platform.current_account_id(), $2, $3, $4)`, [ctx.params['student_id'], type, version, decision]);
  } catch (e) {
    // RLS 拒绝（未绑定、已解除绑定）与学生不存在：同一个 404
    if (pgCode(e) === '42501' || pgCode(e) === '23503') return problem(P.NOT_FOUND);
    throw e;
  }
  return json(201, { status: 'recorded' });
};

const openStudentMode: Handler = async (ctx) => {
  const student = ctx.params['student_id']!;
  const life = ctx.deps.config.session.student;
  try {
    const { rows } = await ctx.q<{ token: string }>('SELECT platform.auth_issue_student_session($1, $2, $3, $4) AS token',
      [student, ctx.session!.token, life.absolute_seconds, life.idle_seconds]);
    const token = rows[0]!.token;
    return json(200, { account_kind: 'student', student_id: student, session_token: token, csrf_token: csrfFor(ctx.deps.keys, token) });
  } catch (e) {
    if (pgCode(e) === '42501') return problem(P.NOT_FOUND);  // 未绑定、已解除、学生不存在或停用：同一个 404
    if (pgCode(e) === '55000') return problem(P.CONSENT_REQUIRED);  // 只有已绑定的家长能走到这一步
    throw e;
  }
};

// ---------------------------------------------------------------- 路由表

export const PREFIX = '/bff/v0';

export const ROUTES: readonly Route[] = [
  { method: 'POST', path: '/auth/otp/request', auth: 'public', csrf: 'origin', body: true, handler: otpRequest,
    rateLimits: [{ bucket: 'otp_request.ip.hour', dim: 'ip' }, { bucket: 'otp_request.phone.minute', dim: 'phone' },
                 { bucket: 'otp_request.phone.day', dim: 'phone' }] },
  { method: 'POST', path: '/auth/otp/verify', auth: 'public', csrf: 'origin', body: true, handler: otpVerify,
    rateLimits: [{ bucket: 'otp_verify.ip.hour', dim: 'ip' }, { bucket: 'otp_verify.phone.quarter', dim: 'phone' }] },
  { method: 'POST', path: '/auth/logout', auth: 'session', csrf: 'session', body: false, handler: logout, rateLimits: [] },
  { method: 'POST', path: '/auth/logout-all', auth: 'session', csrf: 'session', body: false, handler: logoutAll, rateLimits: [] },
  { method: 'GET', path: '/me', auth: 'session', csrf: 'none', body: false, handler: me, rateLimits: [] },
  { method: 'POST', path: '/students', auth: 'guardian', csrf: 'session', body: true, handler: createStudent,
    rateLimits: [{ bucket: 'student_create.account.day', dim: 'account' }] },
  { method: 'POST', path: '/students/{student_id}/consents', auth: 'guardian', csrf: 'session', body: true, handler: recordConsent, rateLimits: [] },
  { method: 'POST', path: '/students/{student_id}/student-session', auth: 'guardian', csrf: 'session', body: false, handler: openStudentMode,
    rateLimits: [{ bucket: 'student_session.account.hour', dim: 'account' }] },
  ...FEATURE_ROUTES,
  ...SHELL_ROUTES,
];

/** 路由模板编译成正则：路径参数（student_id、session_id、probe_id）一律只匹配小写 UUID；学科码 {subject} 只匹配小写字母（H4） */
export interface Compiled { route: Route; re: RegExp; names: string[] }
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const PARAM: Record<string, string> = { subject: '[a-z]{2,16}' };

export function compile(routes: readonly Route[] = ROUTES): Compiled[] {
  return routes.map((route) => {
    const names: string[] = [];
    const src = (PREFIX + route.path).split('/').map((seg) => {
      const m = /^\{([a-z_]+)\}$/.exec(seg);
      if (!m) return seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      names.push(m[1]!);
      return `(${PARAM[m[1]!] ?? UUID})`;
    }).join('/');
    return { route, re: new RegExp(`^${src}$`), names };
  });
}

/** 路径是否落在路由表的某个模板“形状”上（参数位置接受任意段）：用来区分“表内路径但参数不合法（404）”与“表外路径” */
export function templateShapes(routes: readonly Route[] = ROUTES): RegExp[] {
  return routes.map((r) => new RegExp('^' + (PREFIX + r.path).split('/').map((seg) =>
    /^\{[a-z_]+\}$/.test(seg) ? '[^/]+' : seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('/') + '$'));
}

export type { PoolClient };
