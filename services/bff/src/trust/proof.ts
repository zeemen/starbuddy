// 信任界面的带密钥摘要与“近期登录”（SIE-03 PR③，安全审查 L1、H1）。
//   - trustMac：用专用密钥 keys.trust（BFF_TRUST_SECRET）派生删除确认凭据、异议幂等键；与限流键、CSRF 令牌的密钥分开；
//   - RecentLogins：家长经验证码登录成功的时刻（键是会话令牌的带密钥摘要，不存令牌）。删除的二次确认要求本会话在 10 分钟内登录过，
//     否则要求重新登录。0029 的 auth_session_resolve 不返回签发时间、BFF 也读不到会话表，所以记在进程内：
//     重启或换实例后一律视为“不是近期登录”（从严）；落库需要会话表加列或新函数（迁移，必停，lessons PR③）。
import { createHmac } from 'node:crypto';
import type { Keys } from '../http/config.ts';

export const trustMac = (keys: Keys, label: string, value: string): string =>
  createHmac('sha256', keys.trust).update(`starbuddy-trust:${label}:${value}`).digest('hex');

/** 删除确认要求的登录新鲜度 */
export const REAUTH_WINDOW_MS = 10 * 60_000;

export class RecentLogins {
  readonly #at = new Map<string, number>();
  readonly #max: number;
  constructor(max = 10_000) {
    this.#max = max;
  }

  record(keys: Keys, sessionToken: string, now: number): void {
    for (const [k, t] of this.#at) if (t + REAUTH_WINDOW_MS <= now) this.#at.delete(k);
    if (this.#at.size >= this.#max) this.#at.delete(this.#at.keys().next().value!);
    this.#at.set(trustMac(keys, 'login', sessionToken), now);
  }

  fresh(keys: Keys, sessionToken: string, now: number): boolean {
    const t = this.#at.get(trustMac(keys, 'login', sessionToken));
    return t !== undefined && now - t < REAUTH_WINDOW_MS && now >= t;
  }
}

const stores = new WeakMap<object, RecentLogins>();
/** 每个 BFF 实例（ShellDeps）一份 */
export function recentLogins(deps: object): RecentLogins {
  let s = stores.get(deps);
  if (!s) {
    s = new RecentLogins();
    stores.set(deps, s);
  }
  return s;
}
