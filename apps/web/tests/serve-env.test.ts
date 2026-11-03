// LT-01 前端启动包装（安全审查 A1、S7、S10）：WEB_HOST、运行环境、镜像环境标记、staging 的两份同意文书版本。全部合成值。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { consentDocumentVersion, consentVersions, isSyntheticVersion } from '../src/lib/consent-version.ts';
import { checkServeEnv, privateIpv4 } from '../src/lib/serve-env.ts';

const STAGING = { STARBUDDY_ENV: 'staging', STARBUDDY_CONSENT_VERSION_PRIVACY: '2026-10-v1', STARBUDDY_CONSENT_VERSION_GUARDIAN: '2026-10-v1' };

test('WEB_HOST：不设 = 127.0.0.1；dev / test 只收 127.0.0.1；staging 另收内网 IPv4 字面量', () => {
  for (const env of ['dev', 'test']) {
    assert.deepEqual(checkServeEnv({ STARBUDDY_ENV: env }), { host: '127.0.0.1', port: 3000 });
    assert.equal(checkServeEnv({ STARBUDDY_ENV: env, WEB_HOST: '127.0.0.1', PORT: '3100' }).port, 3100);
    for (const h of ['172.28.0.40', '0.0.0.0', 'localhost', '::']) assert.throws(() => checkServeEnv({ STARBUDDY_ENV: env, WEB_HOST: h }), /只有 staging/);
  }
  assert.equal(checkServeEnv({ ...STAGING, WEB_HOST: '172.28.0.40' }).host, '172.28.0.40');
  assert.equal(checkServeEnv(STAGING).host, '127.0.0.1');
  for (const h of ['0.0.0.0', '8.8.8.8', '172.32.0.1', 'web', '::', '010.0.0.1', '10.0.0.256']) {
    assert.throws(() => checkServeEnv({ ...STAGING, WEB_HOST: h }), /WEB_HOST/, h);
  }
});

test('运行环境：只收 dev / test / staging；镜像环境标记必须一致；端口合法', () => {
  for (const env of ['pilot', 'production', '', 'STAGING']) assert.throws(() => checkServeEnv({ ...STAGING, STARBUDDY_ENV: env }), /STARBUDDY_ENV/);
  assert.throws(() => checkServeEnv({}), /STARBUDDY_ENV/);
  assert.throws(() => checkServeEnv({ STARBUDDY_ENV: 'test', STARBUDDY_IMAGE_ENV: 'staging' }), /STARBUDDY_IMAGE_ENV/);
  assert.throws(() => checkServeEnv({ ...STAGING, STARBUDDY_IMAGE_ENV: 'test' }), /STARBUDDY_IMAGE_ENV/);
  checkServeEnv({ ...STAGING, STARBUDDY_IMAGE_ENV: 'staging' });
  for (const p of ['0', '65536', 'abc', '30 00']) assert.throws(() => checkServeEnv({ STARBUDDY_ENV: 'test', PORT: p }), /PORT/);
});

test('S7：staging 两份同意文书都要配真实版本（合成版本不区分大小写拒绝）；开发页面开关拒绝', () => {
  assert.throws(() => checkServeEnv({ STARBUDDY_ENV: 'staging' }), /同意文书/);
  assert.throws(() => checkServeEnv({ ...STAGING, STARBUDDY_CONSENT_VERSION_GUARDIAN: undefined }), /同意文书/);
  assert.throws(() => checkServeEnv({ ...STAGING, STARBUDDY_CONSENT_VERSION_PRIVACY: 'Synthetic-1' }), /同意文书/);
  assert.throws(() => checkServeEnv({ STARBUDDY_ENV: 'staging', STARBUDDY_CONSENT_VERSION: '2026-10-v1' }), /同意文书/);  // 只配合并的一个不算
  assert.throws(() => checkServeEnv({ ...STAGING, STARBUDDY_DEV_PAGES: '1' }), /STARBUDDY_DEV_PAGES/);
  assert.deepEqual(consentVersions(STAGING), ['privacy-policy@2026-10-v1', 'guardian-consent@2026-10-v1']);
  assert.equal(consentVersions({ ...STAGING, STARBUDDY_CONSENT_VERSION_PRIVACY: 'bad version!' }), null);
});

test('dev / test 的同意版本：分别配置优先，退回 STARBUDDY_CONSENT_VERSION，再退回合成版本', () => {
  assert.equal(consentDocumentVersion('privacy_policy', { STARBUDDY_ENV: 'test' }), 'privacy-policy@synthetic-0');
  assert.equal(consentDocumentVersion('privacy_policy', { STARBUDDY_ENV: 'dev', STARBUDDY_CONSENT_VERSION: 'v2' }), 'privacy-policy@v2');
  assert.equal(consentDocumentVersion('guardian_consent_under14', { STARBUDDY_ENV: 'dev', STARBUDDY_CONSENT_VERSION: 'v2',
                                                                    STARBUDDY_CONSENT_VERSION_GUARDIAN: 'g3' }), 'guardian-consent@g3');
  assert.ok(isSyntheticVersion('SYNTHETIC-0') && !isSyntheticVersion('2026-10-v1'));
  assert.ok(privateIpv4('192.168.0.1') && !privateIpv4('192.169.0.1'));
});
