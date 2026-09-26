// 开发身份桩（spec 待人确认 #3；4-H 用真实会话替换并删除本文件）。
// 只有 STARBUDDY_DEV_AUTH=1 时存在：/dev/login 只列合成账号，选中后写一个 httpOnly cookie，
// 服务端组件读它，作为 x-dev-account-id 转给 BFF。BFF 那边另有“只认 loopback”的开关，两边都要开才生效。
import { SYNTHETIC_ACCOUNTS } from '../dev/accounts.ts';
import { isUuid } from './star-map.ts';

export const DEV_COOKIE = 'sb_dev_account';

export const devAuthEnabled = (env: Record<string, string | undefined> = process.env): boolean =>
  env['STARBUDDY_DEV_AUTH'] === '1';

/** cookie 里的账号只有在开关打开、是合法 UUID、并且在合成账号列表里时才算数 */
export function devAccountFromCookie(value: string | undefined, env: Record<string, string | undefined> = process.env): string | null {
  if (!devAuthEnabled(env) || !value || !isUuid(value)) return null;
  return SYNTHETIC_ACCOUNTS.some((a) => a.account_id === value) ? value : null;
}

/** 登录后只能跳回星图页，不能被带去任意地址 */
export function safeNext(next: string | null): string {
  return next && /^\/star-map\/[0-9a-f-]{36}$/.test(next) ? next : '/dev/login';
}
