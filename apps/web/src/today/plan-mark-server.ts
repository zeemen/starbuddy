// 今日计划“完成”“先跳过”的票据与幂等键（PLT-15 PR2，审查阻断的处理，方案 a；只在服务端用）。
// 问题：幂等键只按（任务, 动作）固定时，同一页面里“完成 → 跳过 → 再完成”的第三次是第一次的重放，库里最后一条是跳过、界面却显示已记下。
// 做法：
//   - 页面渲染时为每个任务的按钮签一张一次性票据：随机 nonce + HMAC-SHA256（覆盖用途与版本、学生、任务 ID、nonce、当前会话令牌的哈希；
//     令牌本身不进票据）。密钥与会话状态 cookie 同一套管理（STARBUDDY_PRESENCE_HMAC_KEY，src/behavior/presence-server.ts 的 presenceKey：
//     dev / test 没设时进程内随机，正式环境没设时返回 null，页面就不显示按钮）；签名输入以 plan-mark 开头，与会话 cookie 的签名分开；
//   - 幂等键 = H(任务 ID, 动作, nonce)：同一张票据内重复点击、失败重试都是同一个键（库里重放，或时间不同回 409，前端算已记下）；
//   - 每次记下后 server action 返回新票据，前端换上：同一页面内“完成 → 跳过 → 完成”三次各是新键，都入库，最后一条与界面一致；
//   - 刷新页面拿到新票据，再点会多记一行；统计口径是“同一任务以最后一次为准”（services/insight/insight/plan_tasks.py），结果不变。
// 形状：幂等键是 UUIDv4（BFF 只收 v4 形状的幂等键）：SHA-256 的前 16 字节，按 RFC 4122 改写版本与变体位。
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import type { PlanAction } from './plan-mark.ts';

const NAMESPACE = 'starbuddy:plan-mark:v1';
const NONCE = /^[A-Za-z0-9_-]{22}$/;
const MAC = /^[A-Za-z0-9_-]{43}$/;
const MAX_TICKET = 80;

/** 签票据的上下文：密钥（presenceKey）、当前会话令牌（只取哈希参与签名）、学生 */
export interface TicketContext { key: Buffer; token: string; student: string }

const tokenHash = (token: string): string => createHash('sha256').update(token, 'utf8').digest('base64url');
const mac = (ctx: TicketContext, task: string, nonce: string): string =>
  createHmac('sha256', ctx.key).update(['plan-mark', 'v1', ctx.student, task, nonce, tokenHash(ctx.token)].join('|'), 'utf8').digest('base64url');

/** 为一个任务签一张新票据（每次调用 nonce 都不同） */
export function planMarkTicket(ctx: TicketContext, task: string): string {
  const nonce = randomBytes(16).toString('base64url');
  return `${nonce}.${mac(ctx, task, nonce)}`;
}

/** 验票据：格式不对、签名不对（换了会话、学生、任务或密钥）一律返回 null；对了返回 nonce */
export function verifyPlanMarkTicket(ticket: unknown, ctx: TicketContext, task: string): string | null {
  if (typeof ticket !== 'string' || ticket.length > MAX_TICKET) return null;
  const parts = ticket.split('.');
  if (parts.length !== 2) return null;
  const [nonce, sig] = parts as [string, string];
  if (!NONCE.test(nonce) || !MAC.test(sig)) return null;
  const want = Buffer.from(mac(ctx, task, nonce), 'utf8');
  const got = Buffer.from(sig, 'utf8');
  return want.length === got.length && timingSafeEqual(want, got) ? nonce : null;
}

/** 幂等键 = H(任务 ID, 动作, nonce)：同一张票据内确定性，换票据即换键 */
export function planMarkKey(taskId: string, action: PlanAction, nonce: string): string {
  const b = createHash('sha256').update(`${NAMESPACE}:${action}:${taskId}:${nonce}`, 'utf8').digest().subarray(0, 16);
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const h = b.toString('hex');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
