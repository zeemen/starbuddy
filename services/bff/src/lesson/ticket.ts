// 讲评课票据的封装（与再练票据同一做法，src/practice/ticket.ts）：学习服务签发的讲评课票据（lt1.<载荷>.<签名>）载荷里有学生内部 ID、
// 会话 ID、讲评课引用、节点与题，前端不该读到；BFF 不存状态，所以把它连同 BFF 自己要核对的几项一起用 AES-256-GCM 加密后下发，
// 前端拿到的只是不透明的 sbl1.<base64url>：
//   - 密钥由 BFF_AUTH_SECRET 派生（keys.lesson，与再练、CSRF、限流各用途分开）；
//   - 附加数据（AAD）是会话所认的学生 ID：换一个学生（兄妹、另一户）拿去用，解不开，与伪造同一个 TICKET_INVALID；
//   - 里面带会话过期时间：过期后 BFF 本地就回 410，不调学习服务。
// 每一问的幂等键由前端带（同一问重试带同一个键），不放进票据：一张票据要问多次。
// 学习服务仍按自己的票据验签、按库里的会话放行（BFF 的核对只是“过期与换学生不出 BFF”）。
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';

export const LESSON_SEALED_PREFIX = 'sbl1.';
/** 学习服务票据上限 1024 字符（lesson-internal.v0.yaml）；加上会话 ID、过期时间与加密开销，下发的票据不超过这个长度 */
export const MAX_SEALED = 1700;
const IV_LEN = 12;
const TAG_LEN = 16;
const B64URL = /^[A-Za-z0-9_-]+$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export interface LessonTicketContent {
  /** 学习服务签发的原票据（只转回学习服务） */
  ticket: string;
  sessionId: string;
  /** 会话过期时间（RFC 3339，即那笔预留的过期时间） */
  expiresAt: string;
}

const aad = (student: string) => Buffer.from(`starbuddy-lesson-ticket:v1:${student}`, 'utf8');

export function sealLessonTicket(key: Buffer, student: string, t: LessonTicketContent): string {
  const iv = randomBytes(IV_LEN);
  const c = createCipheriv('aes-256-gcm', key, iv);
  c.setAAD(aad(student));
  const plain = JSON.stringify({ t: t.ticket, s: t.sessionId, x: t.expiresAt });
  const ct = Buffer.concat([c.update(plain, 'utf8'), c.final()]);
  return LESSON_SEALED_PREFIX + Buffer.concat([iv, ct, c.getAuthTag()]).toString('base64url');
}

/** 解开并逐项核对；伪造、篡改、换学生、换用途（再练与复测的票据）、格式不对一律 null（调用方回同一个 TICKET_INVALID） */
export function openLessonTicket(key: Buffer, student: string, sealed: unknown): LessonTicketContent | null {
  if (typeof sealed !== 'string' || sealed.length > MAX_SEALED || !sealed.startsWith(LESSON_SEALED_PREFIX)) return null;
  const body = sealed.slice(LESSON_SEALED_PREFIX.length);
  if (!B64URL.test(body)) return null;
  const raw = Buffer.from(body, 'base64url');
  if (raw.length <= IV_LEN + TAG_LEN) return null;
  let plain: string;
  try {
    const d = createDecipheriv('aes-256-gcm', key, raw.subarray(0, IV_LEN));
    d.setAAD(aad(student));
    d.setAuthTag(raw.subarray(raw.length - TAG_LEN));
    plain = Buffer.concat([d.update(raw.subarray(IV_LEN, raw.length - TAG_LEN)), d.final()]).toString('utf8');
  } catch {
    return null;
  }
  let v: Record<string, unknown>;
  try {
    v = JSON.parse(plain) as Record<string, unknown>;
  } catch {
    return null;
  }
  const { t, s, x } = v;
  if (typeof t !== 'string' || t === '' || t.length > 1024 || typeof s !== 'string' || !UUID.test(s)
      || typeof x !== 'string' || !Number.isFinite(Date.parse(x))) {
    return null;
  }
  return { ticket: t, sessionId: s, expiresAt: x };
}
