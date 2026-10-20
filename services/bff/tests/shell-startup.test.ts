// BFF 启动检查（spec 第 2、17 节）：生产配置下没有短信供应商、密钥缺失或太短、环境不认识，都拒绝启动（在连数据库之前）。
// 用合成的随机十六进制串当密钥，只在子进程环境里，不落盘。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';

const MAIN = new URL('../src/main.ts', import.meta.url).pathname;
const hex = () => randomBytes(32).toString('hex');

function start(vars: Record<string, string>): { status: number | null; stderr: string } {
  const r = spawnSync(process.execPath, ['--no-warnings', MAIN], {
    env: { PATH: process.env['PATH'] ?? '', BFF_DATABASE_URL: 'postgresql://nobody@/none?host=/nonexistent', ...vars },
    encoding: 'utf8', timeout: 20_000,
  });
  return { status: r.status, stderr: r.stderr };
}

test('生产、pilot、staging：密钥与来源都配齐也拒绝启动（没有短信供应商，捕获桩不可用；staging 没配白名单固定码同样拒绝）', () => {
  for (const env of ['production', 'pilot', 'staging']) {
    const r = start({ STARBUDDY_ENV: env, BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(), BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
    assert.equal(r.status, 2, env);
    assert.match(r.stderr, /短信供应商/, env);
  }
});

// ---------------------------------------------------------------- LT-01 staging（决定记录第 1、6、10 条）
const tmp = mkdtempSync(join(tmpdir(), 'bff-staging-'));
const OTP_FILE = join(tmp, 'otp.json');
writeFileSync(OTP_FILE, JSON.stringify({ entries: [] }), { mode: 0o600 });
after(() => rmSync(tmp, { recursive: true, force: true }));
const stagingVars = (extra: Record<string, string> = {}) => ({
  STARBUDDY_ENV: 'staging', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(), BFF_SHELL_ORIGIN: 'https://shell.example.invalid',
  BFF_STAGING_OTP_FILE: OTP_FILE, BFF_STAGING_OTP_AUDIT_FILE: join(tmp, `audit-${randomBytes(4).toString('hex')}.jsonl`), ...extra });
const CONFIG_ERR = /STARBUDDY_|BFF_HOST|BFF_CONSENT|短信|OTP|BFF_AUTH_CONFIG|BFF_SHELL_CONFIG|镜像环境标记/;

test('staging：配了白名单固定码与上线测试开关能过启动校验（之后因连不上数据库才退出）', () => {
  const r = start(stagingVars({ STARBUDDY_TRUST_UI: 'why,dispute,reconcile,weekly,data_rights,report', STARBUDDY_STARMAP: 'starmap_v1',
                                STARBUDDY_PRACTICE: 'practice', STARBUDDY_RETEST_UI: 'retest', STARBUDDY_TODAY_ORCH: 'today_orch',
                                BFF_HOST: '172.28.0.30', STARBUDDY_IMAGE_ENV: 'staging',
                                BFF_CONSENT_VERSIONS: 'privacy_policy=privacy@2026-10-v1,guardian_consent_under14=guardian-consent@2026-10-v1' }));
  assert.equal(r.status, 2);
  assert.doesNotMatch(r.stderr, CONFIG_ERR, r.stderr);
});

test('staging 反例：开发开关、范围外开关、坏的监听地址、合成同意版本、坏的 OTP 文件都拒绝启动', () => {
  const cases: [Record<string, string>, RegExp][] = [
    [{ STARBUDDY_TRUST_UI: 'profile' }, /STARBUDDY_TRUST_UI/],
    [{ STARBUDDY_STARMAP: 'starmap_v1,starmap_dashed' }, /STARBUDDY_STARMAP/],
    [{ STARBUDDY_MONTHLY_PLAN: 'monthly_plan' }, /STARBUDDY_MONTHLY_PLAN/],
    [{ STARBUDDY_FAMILY_GOALS: 'family_goals' }, /STARBUDDY_FAMILY_GOALS/],
    [{ BFF_AUTH_CONFIG: '/nonexistent/auth.json' }, /BFF_AUTH_CONFIG/],
    [{ BFF_SMS_CAPTURE_FILE: join(tmp, 'sms') }, /BFF_SMS_CAPTURE_FILE/],
    [{ BFF_HOST: '0.0.0.0' }, /BFF_HOST/],
    [{ BFF_HOST: '8.8.8.8' }, /BFF_HOST/],
    [{ BFF_CONSENT_VERSIONS: 'privacy_policy=privacy@synthetic-v1' }, /BFF_CONSENT_VERSIONS/],
    [{ BFF_STAGING_OTP_FILE: join(tmp, 'missing.json') }, /OTP/],
    [{ STARBUDDY_IMAGE_ENV: 'test' }, /STARBUDDY_IMAGE_ENV/],
  ];
  for (const [extra, re] of cases) {
    const r = start(stagingVars(extra));
    assert.equal(r.status, 2, JSON.stringify(extra));
    assert.match(r.stderr, re, JSON.stringify(extra));
  }
});

test('dev / test 与 pilot、生产不变：BFF_HOST、BFF_CONSENT_VERSIONS、BFF_STAGING_OTP_FILE 只在 staging', () => {
  const localCases: Record<string, string>[] = [{ BFF_HOST: '172.28.0.30' }, { BFF_CONSENT_VERSIONS: 'privacy_policy=privacy@2026-10-v1' },
                                                { BFF_STAGING_OTP_FILE: OTP_FILE }];
  for (const extra of localCases) {
    const r = start({ STARBUDDY_ENV: 'test', ...extra });
    assert.equal(r.status, 2, JSON.stringify(extra));
    assert.match(r.stderr, /BFF_HOST|BFF_CONSENT_VERSIONS|BFF_STAGING_OTP/, JSON.stringify(extra));
  }
  for (const env of ['pilot', 'production']) {
    // 开关在短信发送方之前解析的三个：错误信息就是开关的；信任界面开关在其后解析（单测 trust-units 核对 pilot 拒绝）
    const featureCases: Record<string, string>[] = [{ STARBUDDY_PRACTICE: 'practice' }, { STARBUDDY_RETEST_UI: 'retest' }, { STARBUDDY_TODAY_ORCH: 'today_orch' }];
    for (const extra of featureCases) {
      const r = start({ STARBUDDY_ENV: env, BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(),
                        BFF_SHELL_ORIGIN: 'https://shell.example.invalid', ...extra });
      assert.equal(r.status, 2, env);
      assert.match(r.stderr, /STARBUDDY_/, env);
    }
  }
});

test('生产：密钥缺失或太短、来源不是 https 都拒绝启动；环境不认识也拒绝', () => {
  let r = start({ STARBUDDY_ENV: 'production', BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /未设置/);
  r = start({ STARBUDDY_ENV: 'production', BFF_LOG_HMAC_KEY: 'ab'.repeat(8), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(), BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /至少 32 字节/);
  r = start({ STARBUDDY_ENV: 'production', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(), BFF_SHELL_ORIGIN: 'http://shell.example.invalid' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /https/);
  // 信任界面专用密钥（SIE-03 PR③）：与其他密钥同一规则
  r = start({ STARBUDDY_ENV: 'production', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /BFF_TRUST_SECRET 未设置/);
  r = start({ STARBUDDY_ENV: 'production', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: 'ab'.repeat(8),
              BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /BFF_TRUST_SECRET 必须是至少 32 字节/);
  r = start({ STARBUDDY_ENV: 'prod' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /STARBUDDY_ENV/);
  r = start({});
  assert.equal(r.status, 2);
  assert.match(r.stderr, /STARBUDDY_ENV/);
});

test('阈值配置只能在 dev / test 换：staging、pilot、生产设了 BFF_AUTH_CONFIG 就拒绝启动', () => {
  for (const env of ['production', 'pilot', 'staging']) {
    const r = start({ STARBUDDY_ENV: env, BFF_AUTH_CONFIG: '/nonexistent/auth.json', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(),
                      BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
    assert.equal(r.status, 2, env);
    assert.match(r.stderr, /BFF_AUTH_CONFIG/, env);
  }
});

test('H5 星图 v1 开关只在 dev / test / staging（LT-01）：pilot、生产设了 STARBUDDY_STARMAP 就拒绝启动；test 下不认识的值也拒绝', () => {
  for (const env of ['production', 'pilot']) {
    const r = start({ STARBUDDY_ENV: env, STARBUDDY_STARMAP: 'starmap_v1', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(),
                      BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
    assert.equal(r.status, 2, env);
    assert.match(r.stderr, /STARBUDDY_STARMAP/, env);
  }
  const r = start({ STARBUDDY_ENV: 'test', STARBUDDY_STARMAP: 'starmap_v2' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /STARBUDDY_STARMAP/);
});

test('家长诉求开关只在 dev / test（PLT-14）：staging、pilot、生产设了 STARBUDDY_FAMILY_GOALS 就拒绝启动；test 下不认识的值也拒绝', () => {
  for (const env of ['production', 'pilot', 'staging']) {
    const r = start({ STARBUDDY_ENV: env, STARBUDDY_FAMILY_GOALS: 'family_goals', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(),
                      BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
    assert.equal(r.status, 2, env);
    assert.match(r.stderr, /STARBUDDY_FAMILY_GOALS/, env);
  }
  const r = start({ STARBUDDY_ENV: 'test', STARBUDDY_FAMILY_GOALS: 'monthly_plan' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /STARBUDDY_FAMILY_GOALS/);
  // 合法取值能过开关解析（之后因为连不上数据库才退出，错误信息不是开关的）
  const ok = start({ STARBUDDY_ENV: 'test', STARBUDDY_FAMILY_GOALS: 'family_goals' });
  assert.doesNotMatch(ok.stderr, /STARBUDDY_FAMILY_GOALS/);
});

test('月度计划开关只在 dev / test（PLT-14 第 3 步）：staging、pilot、生产设了 STARBUDDY_MONTHLY_PLAN 就拒绝启动；test 下不认识的值也拒绝；打开时模板库与词表启动即读', () => {
  for (const env of ['production', 'pilot', 'staging']) {
    const r = start({ STARBUDDY_ENV: env, STARBUDDY_MONTHLY_PLAN: 'monthly_plan', BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(),
                      BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
    assert.equal(r.status, 2, env);
    assert.match(r.stderr, /STARBUDDY_MONTHLY_PLAN/, env);
  }
  const r = start({ STARBUDDY_ENV: 'test', STARBUDDY_MONTHLY_PLAN: 'family_goals' });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /STARBUDDY_MONTHLY_PLAN/);
  // 合法取值能过开关解析、模板库与两张词表读得到（之后因为连不上数据库才退出，错误信息不是开关的）
  const ok = start({ STARBUDDY_ENV: 'test', STARBUDDY_MONTHLY_PLAN: 'monthly_plan' });
  assert.doesNotMatch(ok.stderr, /STARBUDDY_MONTHLY_PLAN/);
});
