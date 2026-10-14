// 学生壳会话状态 cookie 的签名与读写（SIE-05 PR2，安全审查阻断的处理）：只在服务端用（/s/{id}/presence 路由、退出登录的 server action）。
// cookie 带 HMAC-SHA256 签名，签名覆盖：学生、开始与最近活动时间、入口类别、开始事件是否已记下、开始与结束事件的幂等键、账号类型、
// 当前会话令牌的哈希（令牌本身不进 cookie）。验签失败（被改过、换了会话或账号、密钥变了）一律当作没有 cookie：不接着、不补记。
// 密钥：服务端环境变量 STARBUDDY_PRESENCE_HMAC_KEY（至少 32 个字符，只在服务端读，不进浏览器）；
//   dev / test 没设时在进程内随机生成（重启后旧 cookie 验签失败、当作没有，可接受）；staging、pilot、生产没设时拒绝写入（presenceKey 返回 null）。
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import type { BffSession } from '../lib/bff.ts';
import { postEventQuietly } from '../lib/events.ts';
import { alive, endOf, isEntry, MAX_AGE_SEC, type Presence } from './presence.ts';

type Env = Record<string, string | undefined>;

export const PRESENCE_KEY_ENV = 'STARBUDDY_PRESENCE_HMAC_KEY';
const MIN_KEY_LENGTH = 32;
const isLocalEnv = (env: Env): boolean => ['dev', 'test'].includes(env['STARBUDDY_ENV'] ?? '');

let localKey: Buffer | null = null;
/** 签名密钥；正式环境没配（或太短）时返回 null，调用方据此拒绝写入 */
export function presenceKey(env: Env = process.env): Buffer | null {
  const v = env[PRESENCE_KEY_ENV];
  if (typeof v === 'string' && v.length >= MIN_KEY_LENGTH) return Buffer.from(v, 'utf8');
  if (!isLocalEnv(env)) return null;
  localKey ??= randomBytes(32);
  return localKey;
}

/** 与会话 cookie 同样的属性（src/lib/session-cookie.ts）：HttpOnly、SameSite=Lax、Path=/；正式环境 __Host- 前缀与 Secure */
export const presenceCookieName = (env: Env = process.env): string => (isLocalEnv(env) ? 'sb_presence' : '__Host-sb_presence');
export const presenceCookieOptions = (env: Env = process.env) =>
  ({ httpOnly: true, sameSite: 'lax', path: '/', secure: !isLocalEnv(env), maxAge: MAX_AGE_SEC }) as const;

/** 签名的上下文：密钥、账号类型（只签学生本人）、当前会话令牌（只取哈希参与签名） */
export interface SignContext { key: Buffer; accountKind: 'student'; token: string }

const VERSION = 'v1';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SEC = /^[0-9]{1,12}$/;
const MAC = /^[A-Za-z0-9_-]{43}$/;

const fields = (p: Presence): string[] =>
  [VERSION, p.student, String(p.start), String(p.last), p.entry, p.started ? '1' : '0', p.startKey, p.endKey];
const tokenHash = (token: string): string => createHash('sha256').update(token, 'utf8').digest('base64url');
const mac = (f: string[], ctx: SignContext): string =>
  createHmac('sha256', ctx.key).update([...f, ctx.accountKind, tokenHash(ctx.token)].join('|'), 'utf8').digest('base64url');

export function signPresence(p: Presence, ctx: SignContext): string {
  const f = fields(p);
  return `${f.join('.')}.${mac(f, ctx)}`;
}

/** 验签并解析；格式不对、签名不对一律返回 null（当作没有 cookie） */
export function verifyPresence(v: string | undefined | null, ctx: SignContext): Presence | null {
  if (typeof v !== 'string' || v.length > 256) return null;
  const parts = v.split('.');
  if (parts.length !== 9) return null;
  const [ver, student, s, l, entry, started, startKey, endKey, sig] = parts as [string, string, string, string, string, string, string, string, string];
  if (ver !== VERSION || !UUID.test(student) || !SEC.test(s) || !SEC.test(l) || !isEntry(entry) || (started !== '0' && started !== '1')
      || !UUID_V4.test(startKey) || !UUID_V4.test(endKey) || !MAC.test(sig)) return null;
  const want = Buffer.from(mac(parts.slice(0, 8), ctx), 'utf8');
  const got = Buffer.from(sig, 'utf8');
  if (want.length !== got.length || !timingSafeEqual(want, got)) return null;
  const p: Presence = { student, start: Number(s), last: Number(l), entry, started: started === '1', startKey, endKey };
  return p.last < p.start ? null : p;
}

/**
 * 退出学生模式、退出登录时（src/app/family/actions.ts 的 logoutAction，在撤销会话之前）：本会话签过的会话状态能结束就先补记 session_end
 * （还在的会话按现在结束，已超时的按最近活动结束），然后删掉 cookie（属性与写入时一致）。家长会话验签必然失败，只删 cookie、不写。
 */
export async function endPresenceOnLogout(s: BffSession): Promise<void> {
  const { cookies } = await import('next/headers');
  const jar = await cookies();
  const name = presenceCookieName();
  const raw = jar.get(name)?.value;
  if (raw === undefined) return;
  const key = presenceKey();
  const p = key ? verifyPresence(raw, { key, accountKind: 'student', token: s.token }) : null;
  if (p) {
    const now = Math.floor(Date.now() / 1000);
    const last = p.last;
    const end = endOf(p, p.student, now, alive(p, p.student, now) ? now : last);
    if (end) await postEventQuietly(s, p.student, { kind: 'session_end', payload: { duration_sec: end.durationSec } }, end.key, new Date(end.at * 1000));
  }
  jar.set(name, '', { ...presenceCookieOptions(), maxAge: 0 });
}
