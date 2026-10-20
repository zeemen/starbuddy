// 短信发送接口与捕获桩（spec 第 2 节、待人确认 #3）。
// 4-H 不接任何真实短信供应商（必停项，随 ADR 10 选境内厂商另起 PR）：只有捕获桩，验证码写进只有测试能读的收件箱。
//   - 捕获桩只在 STARBUDDY_ENV 为 dev / test 时能建；其余环境 createSmsSender 抛错，BFF 拒绝启动；
//   - 捕获桩只收未分配国家码 +999 的合成号码（CLAUDE.md 规则 8），别的号码一律拒收（永远不会“发给”真实号码，也不落盘）；
//   - 收件箱：内存（集成测试直接读）；设了 BFF_SMS_CAPTURE_FILE 时另追加一行 JSON 到这个文件（端到端测试从这里读码）。
import { appendFileSync } from 'node:fs';
import { isLocalEnv, type RunEnv } from './config.ts';
import { NoDeliverySender } from './staging-otp.ts';

export interface SmsSender {
  /** 发登录验证码。失败时抛错；调用方不把失败告诉请求方（响应与成功相同，第一轮 C3） */
  sendLoginCode(phone: string, code: string): Promise<void>;
}

export interface CapturedSms { phone: string; code: string; at: string }

export const SYNTHETIC_PHONE = /^\+999[0-9]{4,12}$/;

export class CaptureSmsSender implements SmsSender {
  readonly inbox: CapturedSms[] = [];
  readonly #file: string | undefined;

  constructor(env: RunEnv, file?: string) {
    if (!isLocalEnv(env)) throw new Error('短信捕获桩只能在 dev / test 环境使用');
    this.#file = file;
  }

  async sendLoginCode(phone: string, code: string): Promise<void> {
    if (!SYNTHETIC_PHONE.test(phone)) throw new Error('capture stub only accepts synthetic +999 numbers');
    const m: CapturedSms = { phone, code, at: new Date().toISOString() };
    this.inbox.push(m);
    if (this.#file) appendFileSync(this.#file, JSON.stringify(m) + '\n', { mode: 0o600 });
  }

  /** 某个号码最近一条验证码（测试用） */
  latest(phone: string): string | null {
    for (let i = this.inbox.length - 1; i >= 0; i--) if (this.inbox[i]!.phone === phone) return this.inbox[i]!.code;
    return null;
  }
}

/** 按环境选发送方：dev / test 用捕获桩；staging 只在配了白名单固定码（BFF_STAGING_OTP_FILE，LT-01 决定记录第 6 条）时
 *  用“不投递”的发送方（固定码由管理员线下下发，src/http/staging-otp.ts）；pilot、生产没有供应商可选，拒绝启动 */
export function createSmsSender(env: RunEnv, vars: Record<string, string | undefined> = process.env): SmsSender {
  if (isLocalEnv(env)) {
    if (vars['BFF_STAGING_OTP_FILE'] || vars['BFF_STAGING_OTP_AUDIT_FILE']) throw new Error('BFF_STAGING_OTP_FILE / _AUDIT_FILE 只能在 staging 设置');
    return new CaptureSmsSender(env, vars['BFF_SMS_CAPTURE_FILE'] || undefined);
  }
  if (vars['BFF_SMS_CAPTURE_FILE']) throw new Error('BFF_SMS_CAPTURE_FILE 只能在 dev / test 环境设置');
  if (env === 'staging') {
    if (!vars['BFF_STAGING_OTP_FILE'] || !vars['BFF_STAGING_OTP_AUDIT_FILE']) {
      throw new Error('staging 没有短信供应商：须配置白名单固定码 BFF_STAGING_OTP_FILE 与审计文件 BFF_STAGING_OTP_AUDIT_FILE');
    }
    return new NoDeliverySender();
  }
  if (vars['BFF_STAGING_OTP_FILE'] || vars['BFF_STAGING_OTP_AUDIT_FILE']) throw new Error('BFF_STAGING_OTP_FILE / _AUDIT_FILE 只能在 staging 设置');
  throw new Error(`${env} 环境没有配置短信供应商（4-H 只有捕获桩；接入供应商是必停项）`);
}
