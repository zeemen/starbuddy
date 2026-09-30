// BFF 启动检查（spec 第 2、17 节）：生产配置下没有短信供应商、密钥缺失或太短、环境不认识，都拒绝启动（在连数据库之前）。
// 用合成的随机十六进制串当密钥，只在子进程环境里，不落盘。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { test } from 'node:test';

const MAIN = new URL('../src/main.ts', import.meta.url).pathname;
const hex = () => randomBytes(32).toString('hex');

function start(vars: Record<string, string>): { status: number | null; stderr: string } {
  const r = spawnSync(process.execPath, ['--no-warnings', MAIN], {
    env: { PATH: process.env['PATH'] ?? '', BFF_DATABASE_URL: 'postgresql://nobody@/none?host=/nonexistent', ...vars },
    encoding: 'utf8', timeout: 20_000,
  });
  return { status: r.status, stderr: r.stderr };
}

test('生产、pilot、staging：密钥与来源都配齐也拒绝启动（没有短信供应商，捕获桩不可用）', () => {
  for (const env of ['production', 'pilot', 'staging']) {
    const r = start({ STARBUDDY_ENV: env, BFF_LOG_HMAC_KEY: hex(), BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(), BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
    assert.equal(r.status, 2, env);
    assert.match(r.stderr, /短信供应商/, env);
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
