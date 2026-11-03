// LT-03 健康端点（specs/launch-lt03-cloud-staging 范围第 4 条）：/healthz 存活、/readyz 就绪（按启动包装同一套核对）。
// 直接调路由模块的 GET（不起 Next）；另做静态核对：两条路由不读会话与 cookie、不调 BFF、只回状态。全部合成值。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { GET as healthz } from '../src/app/healthz/route.ts';
import { GET as readyz } from '../src/app/readyz/route.ts';

const STAGING = { STARBUDDY_ENV: 'staging', STARBUDDY_CONSENT_VERSION_PRIVACY: '2026-10-v1', STARBUDDY_CONSENT_VERSION_GUARDIAN: '2026-10-v1' };

async function withEnv<T>(vars: Record<string, string>, fn: () => Promise<T>): Promise<T> {
  const keys = ['STARBUDDY_ENV', 'STARBUDDY_IMAGE_ENV', 'WEB_HOST', 'PORT', 'STARBUDDY_CONSENT_VERSION_PRIVACY', 'STARBUDDY_CONSENT_VERSION_GUARDIAN',
    'STARBUDDY_DEV_PAGES'];
  const saved = Object.fromEntries(keys.map((k) => [k, process.env[k]]));
  for (const k of keys) delete process.env[k];
  Object.assign(process.env, vars);
  try {
    return await fn();
  } finally {
    for (const k of keys) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  }
}

test('/healthz：200，只回 {"status":"ok"}，no-store；与环境无关', async () => {
  await withEnv({}, async () => {
    const r = healthz();
    assert.equal(r.status, 200);
    assert.equal(r.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await r.json(), { status: 'ok' });
  });
});

test('/readyz：启动核对通过时 200 ready；不通过时 503 not_ready，不回显原因', async () => {
  for (const env of [{ STARBUDDY_ENV: 'test' }, { ...STAGING, WEB_HOST: '172.28.0.40' }]) {
    await withEnv(env, async () => {
      const r = readyz();
      assert.equal(r.status, 200, JSON.stringify(env));
      assert.equal(r.headers.get('cache-control'), 'no-store');
      assert.deepEqual(await r.json(), { status: 'ready' });
    });
  }
  for (const env of [{}, { STARBUDDY_ENV: 'pilot' }, { STARBUDDY_ENV: 'staging' }, { ...STAGING, WEB_HOST: '8.8.8.8' },
    { ...STAGING, STARBUDDY_IMAGE_ENV: 'test' }, { ...STAGING, STARBUDDY_DEV_PAGES: '1' }]) {
    await withEnv(env as Record<string, string>, async () => {
      const r = readyz();
      assert.equal(r.status, 503, JSON.stringify(env));
      const text = await r.text();
      assert.deepEqual(JSON.parse(text), { status: 'not_ready' });
      assert.ok(!/STARBUDDY|WEB_HOST|同意|staging/.test(text), '不回显核对失败的原因');
    });
  }
});

test('两条健康路由只回状态：不读会话与 cookie、不调 BFF、不带版本信息', () => {
  for (const f of ['healthz', 'readyz']) {
    const src = readFileSync(new URL(`../src/app/${f}/route.ts`, import.meta.url), 'utf8');
    const code = src.split('\n').filter((l) => !l.trimStart().startsWith('//')).join('\n');
    assert.ok(!/cookies|headers\(\)|lib\/bff|fetch\(|session|version|BUILD_ID/i.test(code), `${f}：只回状态`);
    assert.match(code, /export const dynamic = 'force-dynamic'/);
    assert.match(code, /'cache-control': 'no-store'/);
    assert.ok(!/export (async )?function (POST|PUT|PATCH|DELETE)/.test(code), `${f}：只认 GET`);
  }
});
