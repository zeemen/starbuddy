// 同意文本版本（LT-01 安全审查 S6、S7）：两份文书分别配置版本——隐私政策 STARBUDDY_CONSENT_VERSION_PRIVACY、
// 监护人同意书 STARBUDDY_CONSENT_VERSION_GUARDIAN。dev / test 没配时退回 STARBUDDY_CONSENT_VERSION，再退回合成版本 synthetic-0；
// staging 两份都必须配、且都不能是合成版本（synthetic-*，不区分大小写），否则启动拒绝（src/lib/serve-env.ts）、写入前也拒绝。
// BFF 在 staging 只收由人登记的版本（BFF_CONSENT_VERSIONS），两边由部署环境各自配置、必须一致。只在服务端读。
type Env = Record<string, string | undefined>;

export const CONSENT_DOCS = { privacy_policy: 'privacy-policy', guardian_consent_under14: 'guardian-consent' } as const;
export type ConsentType = keyof typeof CONSENT_DOCS;
const DOC_VAR: Record<ConsentType, string> = { privacy_policy: 'STARBUDDY_CONSENT_VERSION_PRIVACY', guardian_consent_under14: 'STARBUDDY_CONSENT_VERSION_GUARDIAN' };
const VERSION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;

export const isSyntheticVersion = (v: string): boolean => v.toLowerCase().startsWith('synthetic-');

/** 某份文书要记的完整版本号（文书名@版本）；staging 没配或是合成版本返回 null（调用方不写入） */
export function consentDocumentVersion(type: ConsentType, env: Env = process.env): string | null {
  const run = env['STARBUDDY_ENV'] ?? '';
  const own = env[DOC_VAR[type]];
  if (run === 'staging') {
    if (!own || !VERSION.test(own) || isSyntheticVersion(own)) return null;
    return `${CONSENT_DOCS[type]}@${own}`;
  }
  const v = own ?? env['STARBUDDY_CONSENT_VERSION'] ?? 'synthetic-0';
  return `${CONSENT_DOCS[type]}@${VERSION.test(v) ? v : 'synthetic-0'}`;
}

/** 两份文书的版本都可用时返回二者，否则 null（写入前一起确认，免得只记了一份） */
export function consentVersions(env: Env = process.env): [string, string] | null {
  const a = consentDocumentVersion('privacy_policy', env);
  const b = consentDocumentVersion('guardian_consent_under14', env);
  return a && b ? [a, b] : null;
}

export const CONSENT_VERSION_VARS = Object.values(DOC_VAR);
