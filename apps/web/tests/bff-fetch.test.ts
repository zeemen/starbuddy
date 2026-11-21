// 内测前整改 P2：服务端调 BFF 的共享客户端都要有明确超时，BFF 断连、超时、读响应体中断时落到各自已有的失败结果，
// 不抛异常、不等底层超时。底层是 src/lib/bff.ts 的 bffFetch；这里逐个客户端换掉 globalThis.fetch 模拟三种故障，
// 另加一条静态检查：src 里服务端直接调 fetch 的文件，要么经 bffFetch，要么自己带 AbortSignal.timeout 并接住异常。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { BFF_TIMEOUT_MS, bffFetch } from '../src/lib/bff.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const S = '5a000000-0000-4000-8000-0000000000c1';
const P = '5a000000-0000-4000-8000-0000000000d1';
const session = { token: 'a'.repeat(64), csrf: 'b'.repeat(64) };

type Fault = 'refused' | 'timeout' | 'body';
/** 三种故障：拒绝连接（fetch 直接拒绝）、超时（一直不应答，直到 signal 中止）、读响应体中途断开 */
function faulty(kind: Fault, seen: { signal: AbortSignal | null }[]): typeof fetch {
  return (async (_u: string | URL | Request, init?: RequestInit) => {
    const signal = init?.signal ?? null;
    seen.push({ signal });
    if (kind === 'refused') throw new TypeError('fetch failed');
    if (kind === 'timeout') {
      return await new Promise<Response>((_, reject) => {
        // 没有 signal：真实环境里会一直挂到底层超时；这里 2 秒后报错，让“没带超时”成为明确的失败，而不是测试挂起
        if (!signal) { setTimeout(() => reject(new Error('请求没带 signal：会一直挂着')), 2_000); return; }
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    }
    const body = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('{"stud')); c.error(new TypeError('terminated')); } });
    return new Response(body, { status: 200, headers: { 'content-type': 'application/json' } });
  }) as typeof fetch;
}

/** 换掉 fetch 与 AbortSignal.timeout（把超时缩短到 30 毫秒），跑完还原 */
async function withFault<T>(kind: Fault, fn: () => Promise<T>): Promise<{ out: T; seen: { signal: AbortSignal | null }[]; asked: number[] }> {
  const saved = { fetch: globalThis.fetch, timeout: AbortSignal.timeout };
  const seen: { signal: AbortSignal | null }[] = [];
  const asked: number[] = [];
  globalThis.fetch = faulty(kind, seen);
  AbortSignal.timeout = (ms: number) => { asked.push(ms); return saved.timeout.call(AbortSignal, 30); };
  // AbortSignal.timeout 的计时器不占住事件循环（服务端进程里有监听的端口占着）；测试里另挂一个，免得进程提前结束
  const keep = setInterval(() => {}, 1_000);
  try {
    return { out: await fn(), seen, asked };
  } finally {
    clearInterval(keep);
    globalThis.fetch = saved.fetch;
    AbortSignal.timeout = saved.timeout;
  }
}

const FAULTS: Fault[] = ['refused', 'timeout', 'body'];

test('bffFetch：三种故障都返回 null（不抛出）；每次请求都带 signal，默认超时 10 秒', async () => {
  assert.equal(BFF_TIMEOUT_MS, 10_000);
  for (const kind of FAULTS) {
    const { out, seen, asked } = await withFault(kind, () => bffFetch('http://127.0.0.1:8787/bff/v0/me', { method: 'GET' }));
    assert.equal(out, null, kind);
    assert.equal(seen.length, 1);
    assert.ok(seen[0]!.signal instanceof AbortSignal, `${kind}：请求带了 signal`);
    assert.deepEqual(asked, [BFF_TIMEOUT_MS], `${kind}：超时按 BFF_TIMEOUT_MS`);
  }
  const saved = globalThis.fetch;
  globalThis.fetch = (async () => new Response('{"code":"X"}', { status: 409 })) as typeof fetch;
  try {
    assert.deepEqual(await bffFetch('http://127.0.0.1:8787/x', {}), { status: 409, ok: false, text: '{"code":"X"}' }, '正常应答原样带回状态与正文');
  } finally {
    globalThis.fetch = saved;
  }
});

test('bffFetch：BFF 地址配置错误不吞掉（地址由调用方先算好，bffBaseUrl 照常抛出）', async () => {
  const { bffBaseUrl } = await import('../src/lib/bff.ts');
  assert.throws(() => bffBaseUrl({ STARBUDDY_BFF_URL: 'http://203.0.113.9:8787', STARBUDDY_ENV: 'test' }));
});

test('登录与会话（shell-auth）：断连、超时、读体中断都回 503 INTERNAL，登录页与家长首页有这条提示', async () => {
  const auth = await import('../src/lib/shell-auth.ts');
  const copy = await import('../src/auth/copy.ts');
  for (const kind of FAULTS) {
    const { out, seen } = await withFault(kind, () => auth.whoAmI(session.token));
    assert.deepEqual(out, { ok: false, problem: { status: 503, code: 'INTERNAL' } }, kind);
    assert.ok(seen[0]!.signal instanceof AbortSignal);
  }
  assert.ok(copy.LOGIN_RESULT['INTERNAL'] && copy.FAMILY_RESULT['INTERNAL'], '登录、家长首页的结果表里有 INTERNAL 的提示');
});

test('今日计划、选科等（shell-api）：断连、超时、读体中断都回 503 INTERNAL，选科结果表有这条提示', async () => {
  const api = await import('../src/lib/shell-api.ts');
  const copy = await import('../src/shell/copy.ts');
  for (const kind of FAULTS) {
    const calls: (() => Promise<unknown>)[] = [() => api.fetchToday(session, S), () => api.fetchEnrollments(session, S), () => api.fetchBalance(session, S)];
    for (const call of calls) {
      const { out, seen } = await withFault(kind, call);
      assert.deepEqual(out, { ok: false, status: 503, code: 'INTERNAL' }, kind);
      assert.ok(seen[0]!.signal instanceof AbortSignal);
    }
  }
  assert.ok(copy.ENROLL_RESULT['INTERNAL'], '选科结果表里有 INTERNAL 的提示');
});

// 写请求（导出兑换等）要取浏览器请求头（next/headers），单测里起不来；读写共用同一个 call / bffFetch，只测读
test('信任界面（trust-api）：断连、超时、读体中断都回 503（页面显示“暂时无法获取”）', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], ui: process.env['STARBUDDY_TRUST_UI'] };
  process.env['STARBUDDY_ENV'] = 'test';
  process.env['STARBUDDY_TRUST_UI'] = 'why,dispute,reconcile,data_rights';
  try {
    const api = await import('../src/lib/trust-api.ts');
    for (const kind of FAULTS) {
      assert.deepEqual((await withFault(kind, () => api.fetchDisputes(session, S))).out, { ok: false, status: 503, code: 'UNAVAILABLE' }, kind);
      assert.deepEqual((await withFault(kind, () => api.fetchPrediction(session, S, P))).out, { ok: false, status: 503, code: 'UNAVAILABLE' }, kind);
    }
  } finally {
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.ui === undefined) delete process.env['STARBUDDY_TRUST_UI']; else process.env['STARBUDDY_TRUST_UI'] = saved.ui;
  }
});

test('星图（v0 与 v1）：断连、超时、读体中断与 5xx 同一个分支（抛错进错误页），不挂起', async () => {
  const v0 = await import('../src/lib/star-map.ts');
  const v1 = await import('../src/lib/star-map-v1.ts');
  for (const kind of FAULTS) {
    await assert.rejects(withFault(kind, () => v0.fetchStarMap(S, session)), /star map request failed/, kind);
    await assert.rejects(withFault(kind, () => v1.fetchStarMapV1(S, session)), /star map v1 request failed/, kind);
  }
});

test('建档（onboarding）：断连、超时、读体中断都回 { ok: false }（原来超时有、异常没接住）', async () => {
  const api = await import('../src/lib/onboarding.ts');
  for (const kind of FAULTS) {
    assert.deepEqual((await withFault(kind, () => api.getOnboarding(session, S))).out, { ok: false, status: 503 }, kind);
  }
});

test('静态：服务端直接调 fetch 的文件，要么就是 bff.ts，要么自己带 AbortSignal.timeout 并 try/catch 接住异常', () => {
  const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : p.endsWith('.ts') ? [p] : [];
  });
  // 浏览器端（.tsx 组件、Service Worker）调的是本前端自己的地址，不在这条检查里
  // events.ts：post 带超时、故意让异常抛给调用方，真实页面只经 postEventQuietly（接住并计为 network，tests/behavior.test.ts 钉着）
  const SKIP = new Set(['src/lib/bff.ts', 'src/pwa/sw-source.ts', 'src/lib/events.ts']);
  const bad: string[] = [];
  let checked = 0;
  for (const p of files(join(WEB, 'src'))) {
    const rel = relative(WEB, p).split('\\').join('/');
    const text = readFileSync(p, 'utf8');
    if (SKIP.has(rel) || !/(^|[^.\w])fetch\(/m.test(text)) continue;
    checked += 1;
    if (!text.includes('AbortSignal.timeout(') || !/\btry\s*\{[\s\S]*?\bfetch\(/.test(text)) bad.push(rel);
  }
  assert.ok(checked >= 5, `检查到的直接调 fetch 的文件太少（${checked}），扫描写错了`);
  assert.deepEqual(bad, [], '这些文件直接调 fetch 却没有明确超时或没有接住异常；改用 src/lib/bff.ts 的 bffFetch');
});

test('导出包下载单独给 60 秒（凭据一兑换就作废，不能按通用的 10 秒在读响应体时超时；PR 28 合并前安全审查第 4 条）', async () => {
  const src = readFileSync(join(WEB, 'src/lib/trust-api.ts'), 'utf8');
  assert.match(src, /export const EXPORT_DOWNLOAD_TIMEOUT_MS = 60_000;/);
  const fn = src.slice(src.indexOf('export async function downloadExport('));
  assert.match(fn.slice(0, fn.indexOf('\n}\n')), /\}, EXPORT_DOWNLOAD_TIMEOUT_MS\);/, 'downloadExport 把时限传给 bffFetch');
  assert.ok(60_000 > BFF_TIMEOUT_MS);
});
