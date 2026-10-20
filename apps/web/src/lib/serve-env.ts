// 前端容器启动前的环境核对（LT-01 安全审查 A1、S7、S10；scripts/serve.ts 调用，通过后才起 next start）：
//   - STARBUDDY_ENV 只收 dev / test / staging（pilot、生产与不设一律拒绝：前端的部署形态随 LT-03 定）；
//   - STARBUDDY_IMAGE_ENV（镜像固化的环境标记）设了就必须与 STARBUDDY_ENV 一致；
//   - WEB_HOST：不设 = 127.0.0.1；dev / test 只收 127.0.0.1；staging 只收 127.0.0.1 或 RFC 1918 内网 IPv4 字面量；
//   - PORT：1–65535，缺省 3000；
//   - staging：两份同意文书的版本都要配、都不能是合成版本（src/lib/consent-version.ts）；开发专用页面开关不能设。
// 纯函数，返回监听地址与端口；不合格抛错（信息不带取值）。
import { consentVersions } from './consent-version.ts';

type Env = Record<string, string | undefined>;

export function privateIpv4(host: string): boolean {
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return false;
  const parts = host.split('.');
  if (parts.some((p) => (p.length > 1 && p.startsWith('0')) || Number(p) > 255)) return false;
  const [a, b] = parts.map(Number) as [number, number];
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

export function checkServeEnv(env: Env): { host: string; port: number } {
  const run = env['STARBUDDY_ENV'] ?? '';
  if (!['dev', 'test', 'staging'].includes(run)) throw new Error('STARBUDDY_ENV 必须是 dev、test 或 staging');
  const marked = env['STARBUDDY_IMAGE_ENV'];
  if (marked !== undefined && marked !== run) throw new Error('镜像环境标记与 STARBUDDY_ENV 不一致（STARBUDDY_IMAGE_ENV）');
  const raw = env['WEB_HOST'];
  const host = raw === undefined || raw === '' ? '127.0.0.1' : raw;
  if (host !== '127.0.0.1') {
    if (run !== 'staging') throw new Error('WEB_HOST 只有 staging 能改（dev / test 只监听 127.0.0.1）');
    if (!privateIpv4(host)) throw new Error('WEB_HOST 在 staging 只能是 127.0.0.1 或内网 IPv4 地址（RFC 1918）');
  }
  const p = env['PORT'] ?? '3000';
  if (!/^[0-9]{1,5}$/.test(p) || Number(p) < 1 || Number(p) > 65535) throw new Error('PORT 必须是 1–65535 的整数');
  if (run === 'staging') {
    if (consentVersions(env) === null) {
      throw new Error('staging 须配置两份同意文书的真实版本（STARBUDDY_CONSENT_VERSION_PRIVACY、STARBUDDY_CONSENT_VERSION_GUARDIAN；不收合成版本）');
    }
    if (env['STARBUDDY_DEV_PAGES']) throw new Error('STARBUDDY_DEV_PAGES 是开发开关：staging 拒绝启动');
  }
  return { host, port: Number(p) };
}
