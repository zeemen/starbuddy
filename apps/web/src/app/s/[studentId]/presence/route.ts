// 学生壳的会话起止（SIE-05 PR2，specs/sie05-silent-metrics/spec.md 范围第 4 条）：壳的客户端部分（src/behavior/SessionBeacon.tsx）报
// ping（进入页面、有活动）/ touch（离开页面）/ end（无活动超时），这里按签名的 HttpOnly cookie 里的会话状态（src/behavior/presence.ts、
// presence-server.ts）决定要不要经 BFF 现有事件接口（POST /bff/v0/students/{id}/events）写 session_start、session_end。
// 用路由而不用 server action：server action 里改 cookie 会让 Next 重新渲染当前页与布局（多一轮 BFF 读取，作答页状态也可能受影响）。
//   - 只认 POST、application/json、请求体不超过 512 字节、同源（Origin 的主机等于本站；BFF 另核对 Origin）、带与 CSRF cookie 一致的令牌；
//   - 签名密钥没配（正式环境）时拒绝写入；在读写 cookie 之前先经 /me 确认当前会话是路径里的学生本人——家长会话不改 cookie、不写库；
//   - 一律回 204、不回任何内容：对学生静默，浏览器端也不重试。开始事件没记下时 cookie 里留着同一个幂等键，下一次 ping 用它重试；
//   - 授权只在 BFF 与数据库：只认学生本人（0025 的 is_self_student）。
import { randomUUID } from 'node:crypto';
import { cookies } from 'next/headers';
import type { NextRequest } from 'next/server';
import { decide, endOf, IDLE_SEC, isEntry, type Entry, type Presence } from '../../../../behavior/presence.ts';
import { presenceCookieName, presenceCookieOptions, presenceKey, signPresence, verifyPresence, type SignContext } from '../../../../behavior/presence-server.ts';
import type { BffSession } from '../../../../lib/bff.ts';
import { postEventQuietly } from '../../../../lib/events.ts';
import { actionSession, pageSession } from '../../../../lib/session.ts';
import { isUuid } from '../../../../lib/star-map.ts';

export const dynamic = 'force-dynamic';

const OPS = ['ping', 'touch', 'end'] as const;
type Op = (typeof OPS)[number];
const MAX_BODY = 512;

const done = (): Response => new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } });

function sameOrigin(req: NextRequest): boolean {
  const origin = req.headers.get('origin');
  const host = req.headers.get('x-forwarded-host') ?? req.headers.get('host');
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/** 先看请求头（类型、声明的长度）再读请求体；读出来仍超长同样拒绝 */
async function parse(req: NextRequest): Promise<{ csrf: string; op: Op; entry: Entry | null } | null> {
  const type = (req.headers.get('content-type') ?? '').split(';')[0]!.trim().toLowerCase();
  if (type !== 'application/json') return null;
  const declared = req.headers.get('content-length');
  if (declared !== null && !(/^[0-9]{1,6}$/.test(declared) && Number(declared) <= MAX_BODY)) return null;
  const text = await req.text();
  if (text.length > MAX_BODY) return null;
  let b: unknown;
  try { b = JSON.parse(text); } catch { return null; }
  if (b === null || typeof b !== 'object' || Array.isArray(b)) return null;
  const { csrf, op, entry } = b as Record<string, unknown>;
  if (typeof csrf !== 'string' || typeof op !== 'string' || !(OPS as readonly string[]).includes(op)) return null;
  return { csrf, op: op as Op, entry: isEntry(entry) ? entry : null };
}

const writeStart = (s: BffSession, p: Presence) =>
  postEventQuietly(s, p.student, { kind: 'session_start', payload: { entry: p.entry } }, p.startKey, new Date(p.start * 1000));

export async function POST(req: NextRequest, { params }: { params: Promise<{ studentId: string }> }): Promise<Response> {
  const { studentId } = await params;
  if (!isUuid(studentId) || !sameOrigin(req)) return done();
  const body = await parse(req);
  if (!body || (body.op === 'ping' && !body.entry)) return done();
  const key = presenceKey();
  if (!key) return done();  // 正式环境没配签名密钥：拒绝写入
  const s = await actionSession(body.csrf);
  if (!s) return done();
  // 只认学生本人：家长会话、别的学生到这里为止，不读不改 cookie、不写库
  const ps = await pageSession();
  if (!ps || ps.me.account_kind !== 'student' || ps.me.student_id !== studentId || ps.session.token !== s.token) return done();
  const ctx: SignContext = { key, accountKind: 'student', token: s.token };
  const jar = await cookies();
  const name = presenceCookieName();
  const opts = presenceCookieOptions();
  const prev = verifyPresence(jar.get(name)?.value, ctx);
  const now = Math.floor(Date.now() / 1000);
  const save = (p: Presence) => jar.set(name, signPresence(p, ctx), opts);

  if (body.op === 'end') {
    // 只结束确实已经 IDLE_SEC 没有活动的会话（同一学生开着的另一个页面还在报活动时不结束）
    if (prev && prev.student === studentId && now - prev.last >= IDLE_SEC - 60) {
      const end = endOf(prev, studentId, now);
      if (end) await postEventQuietly(s, studentId, { kind: 'session_end', payload: { duration_sec: end.durationSec } }, end.key, new Date(end.at * 1000));
      jar.set(name, '', { ...opts, maxAge: 0 });
    }
    return done();
  }

  const step = decide(prev, studentId, now);
  if (step.kind === 'continue') {
    save(step.next);
    return done();
  }
  if (step.kind === 'retry_start') {
    // 开始事件上次没记下：ping 时用原来的幂等键与发生时间再试一次（touch 只更新最近活动）
    save(body.op === 'ping' ? { ...step.next, started: await writeStart(s, step.next) } : step.next);
    return done();
  }
  if (body.op === 'touch' || !body.entry) return done();  // 离开页面时只更新还在的会话，不开始新会话
  if (step.end) {
    const e = step.end;
    await postEventQuietly(s, studentId, { kind: 'session_end', payload: { duration_sec: e.durationSec } }, e.key, new Date(e.at * 1000));
  }
  const next: Presence = { student: studentId, start: now, last: now, entry: body.entry, started: false, startKey: randomUUID(), endKey: randomUUID() };
  save({ ...next, started: await writeStart(s, next) });
  return done();
}
