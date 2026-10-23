// 统一壳 HTTP 层的配置（specs/unified-shell-sprint4/spec.md 第 2、17 节）。
//   - 阈值与时长：config/auth.json（测试读同一份）；
//   - 运行环境：STARBUDDY_ENV（dev / test / staging / pilot / production），不认识的取值拒绝启动；
//   - 密钥：BFF_LOG_HMAC_KEY（日志里 student_id 的带密钥 HMAC）与 BFF_AUTH_SECRET（验证码 MAC、限流键、CSRF 令牌的派生根），
//     BFF_TRUST_SECRET（信任界面：删除确认凭据、异议幂等键；SIE-03 PR③）
//     从环境变量读，十六进制、至少 32 字节，不进仓库；dev / test 不设时启动随机生成（3-H 待人确认 #15），其余环境不设或太短拒绝启动。
import { createHmac, randomBytes } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';

export type RunEnv = 'dev' | 'test' | 'staging' | 'pilot' | 'production';
const RUN_ENVS: readonly RunEnv[] = ['dev', 'test', 'staging', 'pilot', 'production'];
export const isLocalEnv = (e: RunEnv): boolean => e === 'dev' || e === 'test';
/** 上线测试开关矩阵（LT-01 决定记录第 1 条，deploy/flags/staging.yaml）里的功能可以打开的环境：dev / test，加 staging；
 *  pilot、生产仍一律拒绝。开发开关（BFF_AUTH_CONFIG 等）不用它，仍只认 isLocalEnv */
export const launchFeatureEnv = (e: RunEnv): boolean => isLocalEnv(e) || e === 'staging';

/** RFC 1918 的 IPv4 字面量（点分十进制、不带前导零） */
export function privateIpv4(host: string): boolean {
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return false;
  const parts = host.split('.');
  if (parts.some((p) => (p.length > 1 && p.startsWith('0')) || Number(p) > 255)) return false;
  const [a, b] = parts.map(Number) as [number, number];
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

/** 服务间地址（学习服务、洞察服务）：https、本机回环的 http；staging 另收内网 IPv4 字面量的 http（LT-01 决定记录第 1 条） */
export function serviceUrlOk(url: string, env: RunEnv): boolean {
  if (/^(https:\/\/|http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$))/.test(url)) return true;
  if (env !== 'staging') return false;
  const m = /^http:\/\/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d{1,5})?(\/|$)/.exec(url);
  return m !== null && privateIpv4(m[1]!);
}

/** 镜像固化的环境标记（STARBUDDY_IMAGE_ENV，LT-01 安全审查 S10）：设了就必须与运行环境一致（staging 镜像拒绝 dev / test 运行）；
 *  已知局限：运行时同名变量可以覆盖，只防误用 */
export function checkImageEnv(env: RunEnv, marked: string | undefined): void {
  if (marked !== undefined && marked !== env) throw new Error(`镜像环境标记是 ${marked}，运行环境却是 ${env}：拒绝启动（STARBUDDY_IMAGE_ENV）`);
}

/** staging 托管库的 CA（LT-03 决定记录（续）第 6 条）：固定路径，由部署以只读卷挂载（deploy/k8s/staging.yaml 的 rds-ca） */
export const RDS_CA_PATH = '/run/rds-ca/ca.pem';

/** 连库串的 TLS 核对（BFF_DATABASE_URL）：staging 必须 sslmode=verify-full、sslrootcert 恰好是固定 CA 路径且文件存在；
 *  其余环境不核对（dev / test 连本机一次性库；pilot / 生产在 parseRunEnv 之后另有拒绝）。不合格只回 false，不带连接串 */
export function dbTlsOk(env: RunEnv, url: string, caPath: string = RDS_CA_PATH, exists: (p: string) => boolean = existsSync): boolean {
  if (env !== 'staging') return true;
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.protocol !== 'postgres:' && u.protocol !== 'postgresql:') return false;
  const modes = u.searchParams.getAll('sslmode');
  const roots = u.searchParams.getAll('sslrootcert');
  return modes.length === 1 && modes[0] === 'verify-full' && roots.length === 1 && roots[0] === caPath && exists(caPath);
}

/** 监听地址（BFF_HOST）：不设 = 127.0.0.1；dev / test 设了只能是 127.0.0.1；staging 只能是 127.0.0.1 或内网 IPv4；pilot / 生产只能是 127.0.0.1 */
export function listenHost(raw: string | undefined, env: RunEnv): string {
  if (raw === undefined || raw === '' || raw === '127.0.0.1') return '127.0.0.1';
  if (env !== 'staging') throw new Error('BFF_HOST 只有 staging 能改（其余环境只监听 127.0.0.1）');
  if (!privateIpv4(raw)) throw new Error('BFF_HOST 在 staging 只能是 127.0.0.1 或内网 IPv4 地址（RFC 1918，不收通配地址与主机名）');
  return raw;
}

export interface RateRule { window_seconds: number; limit: number }
export interface Lifetime { absolute_seconds: number; idle_seconds: number }
export interface AuthConfig {
  otp: { ttl_seconds: number; max_attempts: number };
  session: { guardian: Lifetime; student: Lifetime };
  rate_limits: Record<string, RateRule>;
}

export const AUTH_CONFIG_PATH = new URL('../../config/auth.json', import.meta.url);

const int = (v: unknown, lo: number, hi: number, what: string): number => {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < lo || v > hi) throw new Error(`auth.json：${what} 应为 ${lo}–${hi} 的整数`);
  return v;
};

/** 读并校验 config/auth.json（上下界与迁移 0029 的库函数一致，写错时启动就失败，而不是请求时才被库拒绝） */
export function loadAuthConfig(path: URL | string = AUTH_CONFIG_PATH): AuthConfig {
  const raw = JSON.parse(readFileSync(path, 'utf8')) as Record<string, any>;
  const life = (x: any, what: string): Lifetime => {
    const absolute = int(x?.absolute_seconds, 300, 30 * 86400, `${what}.absolute_seconds`);
    return { absolute_seconds: absolute, idle_seconds: int(x?.idle_seconds, 60, absolute, `${what}.idle_seconds`) };
  };
  const rate_limits: Record<string, RateRule> = {};
  for (const [k, v] of Object.entries((raw['rate_limits'] ?? {}) as Record<string, any>)) {
    if (!/^[a-z][a-z0-9_.]{1,47}$/.test(k)) throw new Error(`auth.json：限流桶名 ${k} 不合格式`);
    rate_limits[k] = { window_seconds: int(v?.window_seconds, 1, 7 * 86400, `${k}.window_seconds`), limit: int(v?.limit, 1, 1_000_000, `${k}.limit`) };
  }
  return {
    otp: { ttl_seconds: int(raw['otp']?.ttl_seconds, 60, 900, 'otp.ttl_seconds'), max_attempts: int(raw['otp']?.max_attempts, 1, 10, 'otp.max_attempts') },
    session: { guardian: life(raw['session']?.guardian, 'session.guardian'), student: life(raw['session']?.student, 'session.student') },
    rate_limits,
  };
}

export function parseRunEnv(v: string | undefined): RunEnv {
  if (!v || !(RUN_ENVS as readonly string[]).includes(v)) throw new Error(`STARBUDDY_ENV 必须是 ${RUN_ENVS.join(' / ')} 之一`);
  return v as RunEnv;
}

/** 派生出的各用途密钥（互不相同，一个泄露不影响别的用途的标签空间） */
export interface Keys {
  log: Buffer;
  otp: Buffer;
  rate: Buffer;
  csrf: Buffer;
  /** 信任界面专用（SIE-03 PR③）：删除的会话内确认凭据、转给洞察服务的异议幂等键；独立配置项 BFF_TRUST_SECRET，
   *  轮换它只让未确认的删除凭据失效（重新发起即可）、让异议重试换一个幂等键（库里同一人同一判断同一类的未结束异议仍只有一条） */
  trust: Buffer;
  /** 错题再练票据的封装密钥（AES-256-GCM，src/practice/ticket.ts），由 BFF_AUTH_SECRET 派生；轮换只让已下发、未作答的再练题失效（重新取题即可） */
  practice: Buffer;
}

function readKey(name: string, env: RunEnv, vars: Record<string, string | undefined>): Buffer {
  const v = vars[name];
  if (v === undefined || v === '') {
    if (isLocalEnv(env)) return randomBytes(32);
    throw new Error(`${name} 未设置（${env} 环境必须配置密钥）`);
  }
  if (!/^[0-9a-f]+$/i.test(v) || v.length % 2 !== 0 || v.length < 64) throw new Error(`${name} 必须是至少 32 字节的十六进制串`);
  return Buffer.from(v, 'hex');
}

export function loadKeys(env: RunEnv, vars: Record<string, string | undefined> = process.env): Keys {
  const log = readKey('BFF_LOG_HMAC_KEY', env, vars);
  const root = readKey('BFF_AUTH_SECRET', env, vars);
  const derive = (label: string) => createHmac('sha256', root).update(`starbuddy-bff:${label}`).digest();
  const trust = readKey('BFF_TRUST_SECRET', env, vars);
  return { log, otp: derive('otp'), rate: derive('rate'), csrf: derive('csrf'), trust, practice: derive('practice-ticket') };
}

/** 测试用：随机密钥 */
export const randomKeys = (): Keys => ({ log: randomBytes(32), otp: randomBytes(32), rate: randomBytes(32), csrf: randomBytes(32), trust: randomBytes(32),
                                         practice: randomBytes(32) });
