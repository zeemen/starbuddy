// 再练票据的封装（specs/practice-loop-stage1/lessons.md 待人确认 8“票据是否只留在 BFF”：选“以不透明方式下发”）。
// 学习服务签发的票据（pt1.<载荷>.<签名>）载荷里有学生内部 ID、题目引用、节点等，前端不该读到；BFF 不存状态（多实例也能用），
// 所以把它连同 BFF 自己要核对的几项一起用 AES-256-GCM 加密后下发，前端拿到的只是不透明的 sbp1.<base64url>：
//   - 密钥由 BFF_AUTH_SECRET 派生（keys.practice），与 CSRF、限流、日志各用途分开；
//   - 附加数据（AAD）是会话所认的学生 ID：换一个学生（兄妹、另一户）拿去用，解不开，与伪造同一个 TICKET_INVALID；
//   - 里面带学习服务票据的过期时间、学科、条目、题型与选项键，BFF 在计次之前就能拦下过期、换学科、作答形状不对的请求（不扣次）；
//   - 里面带 BFF 封装时生成的 per-ticket 幂等键（UUIDv4，PR 3 审查 H2）：作答计次（meter_practice）与转学习服务都用它，
//     前端不再自带幂等键。一张票据最多扣一次：同一张票据重试是同一个键（不再计次），换作答是学习服务的 IDEMPOTENCY_CONFLICT（409，不再扣）。
// 密钥与轮换：票据里没有密钥标识（kid），只认当前的 keys.practice；轮换 BFF_AUTH_SECRET（或改派生标签）会让所有已下发、
// 未作答的票据解不开（422 TICKET_INVALID，重新取题即可；本地拒绝不计次）。已计过次、结果未知的那次作答也就不能再凭旧票据找回结果
// （学习服务那边仍按原键落库或封存），所以轮换宜避开学生在线的时段。
// 学习服务仍按自己的票据验签与重新确认条目（BFF 的核对只是“不扣冤枉次”，不替代学习服务的校验）。
import { createCipheriv, createDecipheriv, randomBytes, randomUUID } from 'node:crypto';
import { isUuidV4 } from '../metering/uuid.ts';

export const SEALED_PREFIX = 'sbp1.';
/** 复测与验证题（KGE-05，src/retest/）的票据前缀：同一把密钥，前缀与附加数据（AAD）都不同，两种票据互相解不开 */
export const RETEST_SEALED_PREFIX = 'sbr1.';
/** 票据用途：practice（错题再练，缺省）/ retest（复测与验证题） */
export type TicketDomain = 'practice' | 'retest';
const prefixOf = (domain: TicketDomain) => (domain === 'retest' ? RETEST_SEALED_PREFIX : SEALED_PREFIX);
/** 学习服务票据上限 1024 字符（practice-internal.v0.yaml）；加上 BFF 核对项、幂等键与加密开销，下发的票据不超过这个长度 */
export const MAX_SEALED = 2400;
const IV_LEN = 12;
const TAG_LEN = 16;
const B64URL = /^[A-Za-z0-9_-]+$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const OPTION_KEY = /^[A-Z]$/;

export type ItemFormat = 'single' | 'multi' | 'fill';

export interface TicketContent {
  /** 学习服务签发的原票据（只转回学习服务） */
  ticket: string;
  /** 学习服务的学科码（aya / zhixing / wenli） */
  subject: string;
  entryId: string;
  /** 学习服务票据的过期时间（RFC 3339） */
  expiresAt: string;
  format: ItemFormat;
  /** 选择题的选项键（填空题为空）：BFF 在计次前按它核对作答形状 */
  optionKeys: string[];
}

/** 解开后的票据：多一个封装时生成的幂等键（小写 UUIDv4） */
export interface OpenedTicket extends TicketContent {
  idempotencyKey: string;
}

const aad = (student: string, domain: TicketDomain = 'practice') =>
  Buffer.from(domain === 'retest' ? `starbuddy-retest-ticket:v1:${student}` : `starbuddy-practice-ticket:v1:${student}`, 'utf8');

/** 封装；幂等键在这里生成（每张票据一个，调用方不能指定）。domain 缺省 practice（错题再练，行为不变） */
export function sealTicket(key: Buffer, student: string, t: TicketContent, domain: TicketDomain = 'practice'): string {
  const iv = randomBytes(IV_LEN);
  const c = createCipheriv('aes-256-gcm', key, iv);
  c.setAAD(aad(student, domain));
  const plain = JSON.stringify({ t: t.ticket, s: t.subject, e: t.entryId, x: t.expiresAt, f: t.format, k: t.optionKeys, i: randomUUID() });
  const ct = Buffer.concat([c.update(plain, 'utf8'), c.final()]);
  return prefixOf(domain) + Buffer.concat([iv, ct, c.getAuthTag()]).toString('base64url');
}

/** 解开并逐项核对；伪造、篡改、换学生、换用途（再练与复测）、格式不对一律 null（调用方回同一个 TICKET_INVALID） */
export function openTicket(key: Buffer, student: string, sealed: string, domain: TicketDomain = 'practice'): OpenedTicket | null {
  const prefix = prefixOf(domain);
  if (typeof sealed !== 'string' || sealed.length > MAX_SEALED || !sealed.startsWith(prefix)) return null;
  const body = sealed.slice(prefix.length);
  if (!B64URL.test(body)) return null;
  const raw = Buffer.from(body, 'base64url');
  if (raw.length <= IV_LEN + TAG_LEN) return null;
  let plain: string;
  try {
    const d = createDecipheriv('aes-256-gcm', key, raw.subarray(0, IV_LEN));
    d.setAAD(aad(student, domain));
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
  const { t, s, e, x, f, k, i } = v;
  if (typeof t !== 'string' || t === '' || t.length > 1024 || typeof s !== 'string' || typeof e !== 'string' || !UUID.test(e)
      || typeof x !== 'string' || !Number.isFinite(Date.parse(x)) || (f !== 'single' && f !== 'multi' && f !== 'fill')
      || !Array.isArray(k) || !k.every((o) => typeof o === 'string' && OPTION_KEY.test(o)) || typeof i !== 'string' || !isUuidV4(i)) {
    return null;
  }
  return { ticket: t, subject: s, entryId: e, expiresAt: x, format: f, optionKeys: k as string[], idempotencyKey: i };
}
