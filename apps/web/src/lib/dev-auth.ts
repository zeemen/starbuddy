// 开发身份桩（spec 待人确认 #3；4-H 用真实会话替换并删除本文件）。
// 只有 STARBUDDY_DEV_AUTH=1 且 STARBUDDY_ENV 为 dev / test 时存在（两个条件，与审核台后端对齐，审核台 PR 3 安全审查 #2）：
// /dev/login 只列合成账号，选中后写一个 httpOnly cookie，服务端组件读它，作为 x-dev-account-id 转给 BFF。
// BFF 那边另有“只认 loopback”的开关，两边都要开才生效。
import { SYNTHETIC_ACCOUNTS } from '../dev/accounts.ts';
import { isUuid } from './star-map.ts';

export const DEV_COOKIE = 'sb_dev_account';
/** 开发 cookie 的有效期（秒）：8 小时，不做长期登录 */
export const DEV_COOKIE_MAX_AGE = 8 * 60 * 60;

type Env = Record<string, string | undefined>;

export const devAuthEnabled = (env: Env = process.env): boolean =>
  env['STARBUDDY_DEV_AUTH'] === '1' && ['dev', 'test'].includes(env['STARBUDDY_ENV'] ?? '');

/**
 * 请求的 Host 必须是本机地址：挡 DNS 重绑定（恶意网页把自己的域名解析到 127.0.0.1，浏览器会当成同源，
 * Next 的 server action 同源校验也会放行，审核台 PR 3 安全审查 #1）。
 */
export function isLoopbackHost(host: string | null | undefined): boolean {
  if (!host) return false;
  const name = host.startsWith('[') ? host.slice(0, host.indexOf(']') + 1) : host.split(':')[0];
  return ['127.0.0.1', 'localhost', '[::1]'].includes(name ?? '');
}

/** cookie 里的账号只有在开关打开、是合法 UUID、并且在合成账号列表里时才算数 */
export function devAccountFromCookie(value: string | undefined, env: Env = process.env): string | null {
  if (!devAuthEnabled(env) || !value || !isUuid(value)) return null;
  return SYNTHETIC_ACCOUNTS.some((a) => a.account_id === value) ? value : null;
}

/** 登录后只能跳回星图页、建档页或探针、诊断演示页（只在开发、测试构建里存在），不能被带去任意地址 */
export function safeNext(next: string | null): string {
  return next && /^\/(star-map|onboarding|dev\/probe|dev\/diagnosis)\/[0-9a-f-]{36}$/.test(next) ? next : '/dev/login';
}
