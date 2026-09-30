// 带密钥的摘要与常量时间比较（spec 第 2、16、17 节）。手机号、IP、账号、令牌都不以原文进日志或限流表。
import { createHmac, randomInt, timingSafeEqual } from 'node:crypto';
import type { Keys } from './config.ts';

const mac = (key: Buffer, label: string, value: string): Buffer => createHmac('sha256', key).update(`${label}\n${value}`).digest();

/** 日志里的 student_id：带密钥 HMAC 的前 16 位十六进制（换密钥后值变化） */
export const logHash = (keys: Keys, id: string): string => mac(keys.log, 'student', id).toString('hex').slice(0, 16);

/** 验证码的 MAC（与号码绑定：换号使用必然对不上）。库里只存它，验证码原文不进库 */
export const codeMac = (keys: Keys, phone: string, code: string): Buffer => mac(keys.otp, 'otp', `${phone}:${code}`);

/** 限流键：维度 + 取值的 HMAC（32 字节） */
export const rateKey = (keys: Keys, dim: string, value: string): Buffer => mac(keys.rate, `rate:${dim}`, value);

/** 与会话绑定的 CSRF 令牌（双重提交的服务端一半）：由会话令牌派生，不另存 */
export const csrfFor = (keys: Keys, sessionToken: string): string => mac(keys.csrf, 'csrf', sessionToken).toString('hex');

/** 常量时间比较（长度不同直接不等；长度本身不是秘密） */
export function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a, 'utf8');
  const y = Buffer.from(b, 'utf8');
  return x.length === y.length && timingSafeEqual(x, y);
}

/** 6 位数字验证码（CSPRNG） */
export const newCode = (): string => String(randomInt(0, 1_000_000)).padStart(6, '0');

export const SESSION_TOKEN = /^[0-9a-f]{64}$/;
export const E164 = /^\+[1-9][0-9]{6,14}$/;
