// 前端单测（不需要浏览器）：布局纯函数、文案与摘要、开发身份桩、BFF 地址限制。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SYNTHETIC_ACCOUNTS } from '../src/dev/accounts.ts';
import { devAccountFromCookie, devAuthEnabled, safeNext } from '../src/lib/dev-auth.ts';
import { skySummaryLabel, statusText, subjectName } from '../src/lib/labels.ts';
import { bffBaseUrl, fetchStarMap, isUuid } from '../src/lib/star-map.ts';
import { columnsForWidth, orderStars, placeStars, squareColumns, STAR_PITCH } from '../src/star/layout.ts';
import { strokePx } from '../src/star/tokens.ts';

const stars = (ids: string[]) => ids.map((id) => ({ skill: `aya:${id}` }));

test('布局：按 local_id 排序，与输入顺序无关；同一输入同一布局', () => {
  const a = placeStars(stars(['K-3', 'K-1', 'M-2', 'C-9']), 2);
  const b = placeStars(stars(['C-9', 'M-2', 'K-1', 'K-3']), 2);
  assert.deepEqual(a, b);
  assert.deepEqual(a.map((p) => p.item.skill), ['aya:C-9', 'aya:K-1', 'aya:K-3', 'aya:M-2']);
  assert.deepEqual(a.map((p) => [p.col, p.row]), [[0, 0], [1, 0], [0, 1], [1, 1]]);
  assert.deepEqual([a[3]!.x, a[3]!.y], [STAR_PITCH * 1.5, STAR_PITCH * 1.5]);
});

test('布局：阿鸦 959 颗星宽屏时 31 × 31（744px 见方）；窄容器按宽度折行', () => {
  assert.equal(squareColumns(959), 31);
  assert.equal(squareColumns(959) * STAR_PITCH, 744);
  const placed = placeStars(stars(Array.from({ length: 959 }, (_, i) => `N-${String(i).padStart(4, '0')}`)), 31);
  assert.equal(Math.max(...placed.map((p) => p.row)) + 1, 31);
  assert.equal(columnsForWidth(360 - 32), 13);  // 手机宽度 360px，左右各 16px 边距
  assert.equal(columnsForWidth(10), 1);
  assert.equal(orderStars([]).length, 0);
});

test('状态文字与星空摘要：只描述本人，不含比较', () => {
  assert.equal(statusText({ display: 'unlit', retained: false }), '未亮');
  assert.equal(statusText({ display: 'half', retained: false }), '半亮');
  assert.equal(statusText({ display: 'lit', retained: false }), '亮');
  assert.equal(statusText({ display: 'lit', retained: true }), '亮·保持');
  assert.equal(subjectName('aya'), '数学');
  assert.equal(subjectName('zhixing'), '道法');
  assert.equal(skySummaryLabel({ subject: 'aya', summary: { lit: 12, half: 30, unlit: 917 } }),
    '数学星空：亮 12 颗，半亮 30 颗，未亮 917 颗');
});

test('描边：按图 2b 比例缩放，下限 1.5px', () => {
  assert.equal(strokePx(16), 1.5);
  assert.equal(strokePx(48), 1.5);
  assert.ok(strokePx(84) === 2);
});

test('开发身份桩：开关不开、不是 UUID、不在合成账号列表里，都不算登录', () => {
  const on = { STARBUDDY_DEV_AUTH: '1', STARBUDDY_ENV: 'test' };
  const acc = SYNTHETIC_ACCOUNTS[0]!.account_id;
  assert.equal(devAuthEnabled({}), false);
  assert.equal(devAuthEnabled({ STARBUDDY_DEV_AUTH: 'true', STARBUDDY_ENV: 'dev' }), false);
  // 第二个条件：只在 dev / test 环境（审核台 PR 3 安全审查 #2）
  assert.equal(devAuthEnabled({ STARBUDDY_DEV_AUTH: '1' }), false);
  assert.equal(devAuthEnabled({ STARBUDDY_DEV_AUTH: '1', STARBUDDY_ENV: 'production' }), false);
  assert.equal(devAuthEnabled({ STARBUDDY_DEV_AUTH: '1', STARBUDDY_ENV: 'dev' }), true);
  assert.equal(devAccountFromCookie(acc, {}), null);
  assert.equal(devAccountFromCookie(acc, on), acc);
  assert.equal(devAccountFromCookie('not-a-uuid', on), null);
  assert.equal(devAccountFromCookie('5a000000-0000-4000-8000-00000000ffff', on), null);
  assert.equal(devAccountFromCookie(undefined, on), null);
});

test('登录后只能跳回星图页', () => {
  assert.equal(safeNext('/star-map/5a000000-0000-4000-8000-0000000000c1'), '/star-map/5a000000-0000-4000-8000-0000000000c1');
  for (const bad of ['https://evil.example/', '//evil.example', '/star-map/x/../../admin', '/dev/session?x', null, '']) {
    assert.equal(safeNext(bad), '/dev/login', String(bad));
  }
});

test('BFF 地址只能是本机 loopback 的 http', () => {
  assert.equal(bffBaseUrl({}), 'http://127.0.0.1:8787');
  assert.equal(bffBaseUrl({ STARBUDDY_BFF_URL: 'http://localhost:9000/x' }), 'http://localhost:9000');
  for (const bad of ['https://127.0.0.1:8787', 'http://10.0.0.5:8787', 'http://bff.example.com']) {
    assert.throws(() => bffBaseUrl({ STARBUDDY_BFF_URL: bad }), bad);
  }
});

test('没有登录或学生 ID 不是 UUID 时不请求 BFF', async () => {
  const orig = globalThis.fetch;
  let called = 0;
  globalThis.fetch = (async () => { called += 1; throw new Error('should not fetch'); }) as typeof fetch;
  try {
    assert.deepEqual(await fetchStarMap('5a000000-0000-4000-8000-0000000000c1', null), { kind: 'unauthenticated' });
    assert.deepEqual(await fetchStarMap('../../admin', SYNTHETIC_ACCOUNTS[0]!.account_id), { kind: 'not-found' });
    assert.equal(called, 0);
    assert.ok(isUuid('5a000000-0000-4000-8000-0000000000c1'));
  } finally {
    globalThis.fetch = orig;
  }
});
