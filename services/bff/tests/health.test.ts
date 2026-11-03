// LT-03 健康端点（specs/launch-lt03-cloud-staging 范围第 4 条；src/http/health.ts，接口定义 openapi/health.v0.yaml）。
// /healthz 存活（不访问依赖）、/readyz 就绪（连接池 SELECT 1，限时）；无需会话；响应只含状态、no-store、nosniff；
// 路径逐字相等、只认 GET；在统一壳路由表之前应答，表外其他路径照旧 404。连接池是假的，不连库；全部合成值。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { cachedReady, HEALTH_PATH, poolReady, READY_PATH, type ReadyClient } from '../src/http/health.ts';
import { loadAuthConfig } from '../src/http/config.ts';
import type { ShellDeps } from '../src/http/routes.ts';
import { createShellServer } from '../src/http/server.ts';

type Mode = 'ok' | 'fail' | 'hang' | 'wrong';
const fake = { mode: 'ok' as Mode, selects: 0, released: [] as Array<boolean | Error | undefined> };
const client: ReadyClient = {
  query: async (sql: string) => {
    if (sql !== 'SELECT 1 AS ok') throw new Error(`unexpected query ${sql}`);
    fake.selects += 1;
    if (fake.mode === 'fail') throw new Error('synthetic: connection lost 10.0.0.9 bff_app');
    if (fake.mode === 'hang') await new Promise((r) => setTimeout(r, 10_000).unref());
    return { rows: [{ ok: fake.mode === 'wrong' ? 0 : 1 }] };
  },
  release: (d) => { fake.released.push(d); },
};
const pool = { connect: async () => client };
const logs: unknown[] = [];
// 端到端用例关掉缓存（每次探测都真查），缓存与 single-flight 另有单元用例
const server = createShellServer({ pool, config: loadAuthConfig(), log: (l: unknown) => logs.push(l) } as unknown as ShellDeps, { readyCacheMs: 0 });
let base = '';
before(async () => {
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
after(() => server.close());

test('/healthz：200 {"status":"ok"}，不访问连接池、不要会话，no-store、nosniff', async () => {
  const before = fake.selects;
  fake.mode = 'fail';
  const r = await fetch(`${base}${HEALTH_PATH}`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(await r.text(), '{"status":"ok"}');
  assert.equal(fake.selects, before, '存活不访问依赖');
  fake.mode = 'ok';
});

test('/readyz：连接池可用 200 ready；失败、超时、结果不对一律 503 not_ready，不回显原因', async () => {
  let r = await fetch(`${base}${READY_PATH}`);
  assert.equal(r.status, 200);
  assert.equal(await r.text(), '{"status":"ready"}');
  for (const mode of ['fail', 'wrong', 'hang'] as const) {
    fake.mode = mode;
    const t0 = Date.now();
    r = await fetch(`${base}${READY_PATH}`);
    const text = await r.text();
    assert.equal(r.status, 503, mode);
    assert.equal(text, '{"status":"not_ready"}', mode);
    assert.ok(Date.now() - t0 < 5_000, `${mode}：限时返回`);
  }
  fake.mode = 'ok';
  assert.equal((await fetch(`${base}${READY_PATH}`)).status, 200);
});

test('只认 GET、路径逐字相等；别的路径照旧是统一壳的 404；健康端点不记访问日志', async () => {
  const n = logs.length;
  for (const p of [HEALTH_PATH, READY_PATH]) {
    const r = await fetch(`${base}${p}`, { method: 'POST' });
    assert.equal(r.status, 405, p);
    assert.equal(r.headers.get('allow'), 'GET');
  }
  for (const p of ['/healthz/', '/readyz?x=1', '/health', '/bff/v0/healthz']) {
    const r = await fetch(`${base}${p}`);
    assert.equal(r.status, 404, p);
    assert.equal(r.headers.get('content-type'), 'application/problem+json', p);
  }
  await fetch(`${base}${HEALTH_PATH}`);
  await fetch(`${base}${READY_PATH}`);
  assert.equal(logs.length, n, '健康端点与表外 404 都不进访问日志');
});

test('poolReady 单元：超时按时限、异常与非 1 都是 false；成功的连接还回池，超时与出错的连接销毁（查询随之丢弃）', async () => {
  const mk = (query: ReadyClient['query']) => {
    const rel: Array<boolean | Error | undefined> = [];
    return { rel, pool: { connect: async () => ({ query, release: (d?: boolean | Error) => { rel.push(d); } }) } };
  };
  let p = mk(async () => ({ rows: [{ ok: 1 }] }));
  assert.equal(await poolReady(p.pool), true);
  assert.deepEqual(p.rel, [false], '成功：还回池');
  p = mk(async () => ({ rows: [] }));
  assert.equal(await poolReady(p.pool), false);
  p = mk(() => Promise.reject(new Error('x')));
  assert.equal(await poolReady(p.pool), false);
  assert.deepEqual(p.rel, [true], '出错：销毁连接');
  p = mk(() => { throw new Error('sync'); });
  assert.equal(await poolReady(p.pool), false);
  assert.equal(await poolReady({ connect: () => Promise.reject(new Error('pool exhausted')) }), false);
  p = mk(() => new Promise(() => {}));
  const t0 = Date.now();
  assert.equal(await poolReady(p.pool, 50), false);
  assert.ok(Date.now() - t0 < 1_000);
  assert.deepEqual(p.rel, [true], '超时：连接销毁、只释放一次（查询不留在池里继续占用）');
  // 借连接本身超时：之后借到的连接立刻销毁
  let give: (c: ReadyClient) => void = () => {};
  const rel: Array<boolean | Error | undefined> = [];
  const slow = { connect: () => new Promise<ReadyClient>((r) => { give = r; }) };
  assert.equal(await poolReady(slow, 30), false);
  give({ query: async () => ({ rows: [{ ok: 1 }] }), release: (d) => { rel.push(d); } });
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(rel, [true]);
});

test('cachedReady 单元：并发探测共用一次检查（single-flight），结果在缓存期内复用、过期后重查；检查抛错算未就绪', async () => {
  let calls = 0;
  let clock = 1_000;
  let finish: (v: boolean) => void = () => {};
  const ready = cachedReady(() => { calls += 1; return new Promise<boolean>((r) => { finish = r; }); }, 3_000, () => clock);
  const a = ready();
  const b = ready();
  assert.equal(calls, 1, '并发只跑一次');
  finish(true);
  assert.deepEqual(await Promise.all([a, b]), [true, true]);
  clock += 2_999;
  assert.equal(await ready(), true);
  assert.equal(calls, 1, '缓存期内不重查');
  clock += 1;
  const c = ready();
  assert.equal(calls, 2, '过期后重查');
  finish(false);
  assert.equal(await c, false);
  clock += 5_000;
  const boom = cachedReady(async () => { throw new Error('x'); }, 3_000, () => clock);
  assert.equal(await boom(), false);
  const src = readFileSync(new URL('../src/http/server.ts', import.meta.url), 'utf8');
  // LT-05：第四个参数把最近一次结果交给指标 starbuddy_ready
  assert.match(src, /const ready = cachedReady\(\(\) => poolReady\(deps\.pool\), opts\.readyCacheMs, Date\.now, \(ok\) => \{ lastReady = ok; \}\);/,
    '服务用带缓存的就绪检查');
});

test('健康端点在路由表之前应答，且不在统一壳路由表与 openapi 的壳接口定义里（health.v0.yaml 单独一份）', () => {
  const src = readFileSync(new URL('../src/http/server.ts', import.meta.url), 'utf8');
  assert.match(src, /handleHealth\(req, res, ready\)\.then\(\(done\) => \(done \? true : handle\(req, res\)\)\)/);
  assert.ok(!/handleMetrics\(req, res\)\)\)\.then/.test(src), 'LT-05：业务端口不服务 /metrics（只在单独的指标端口）');
  const routes = readFileSync(new URL('../src/http/routes.ts', import.meta.url), 'utf8');
  assert.ok(!/healthz|readyz/.test(routes));
  const spec = readFileSync(new URL('../openapi/health.v0.yaml', import.meta.url), 'utf8');
  for (const p of ['/healthz:', '/readyz:']) assert.ok(spec.includes(`  ${p}`), p);
  assert.match(spec, /x-sb-auth: public/);
});
