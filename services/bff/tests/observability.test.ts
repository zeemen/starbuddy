// LT-05 可观测性（specs/launch-lt05-observability 范围第 1、2、7 条；决定记录（续）第 4–6 条）：src/observability/metrics.ts（自写的
// Prometheus 文本格式，不用 prom-client）、log.ts，server.ts 的指标端口与请求指标，metering/meter.ts 的计量计数。
// - 标签白名单：只收服务、路由模板、方法、状态码类别、学科、任务类型、供应商、结果；学科、任务类型、供应商、结果只收枚举（枚举外记 other），
//   路由只收模板形状（UUID、手机号、邮箱、长数字、原始路径一律拒绝）；指标名必须 starbuddy_ 开头；
// - 文本格式：HELP / TYPE、标签值转义（反斜杠、双引号、换行）、直方图累计桶（含 +Inf）、_sum、_count；
// - 指标端口（createMetricsServer）只服务 GET /metrics：免会话、no-store、文本格式；别的路径 404、别的方法 405；
//   业务端口上的 /metrics 是表外路径（404）；请求按路由模板计数，表外记 unmatched，不记原始路径；输出里只有白名单标签、没有 UUID；
// - 计量：预留 / 结算 / 释放（回滚）按结果编码计数，冲突（对外 409）另计；计数在事务提交之后；计数抛异常时计量结果不变；
// - 日志：缺省访问日志一行 JSON（ts、level、service）；顶层字段白名单（键名先驼峰归一）、集中脱敏（禁止字段丢掉，手机号（含分隔）/ 邮箱 /
//   身份证 / 令牌 / 连接串替换，正文 UUID 替换）；学生、账号只收带密钥哈希的 16 位十六进制；
// - 注册的指标与 deploy/observability/metrics-inventory.yaml 一致。
// 连接池是假的，不连库；全部合成值。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import type { Pool } from 'pg';
import { loadAuthConfig } from '../src/http/config.ts';
import type { ShellDeps } from '../src/http/routes.ts';
import { createMetricsServer, createShellServer } from '../src/http/server.ts';
import { release, reserve, settle } from '../src/metering/meter.ts';
import { promMetrics } from '../src/metering/metrics.ts';
import { forbiddenKey, format, HASHED_FIELDS, LOG_FIELDS, logFields, normalizeKey, REDACTED, scrub, scrubText, UUID_MARK } from '../src/observability/log.ts';
import {
  catalog, counter, ENUMS, escapeHelp, escapeLabel, formatValue, gauge, histogram, LABELS, labelValue, meterTesting, metricsPort, observeHttp,
  observeMeter, render, sampleValue,
} from '../src/observability/metrics.ts';

const UUID = '5f0e0000-0000-4000-8000-00000000abcd';
const PHONE = '13812345678';
const pool = { connect: async () => ({ query: async () => ({ rows: [{ ok: 1 }] }), release: () => {} }) };
const server = createShellServer({ pool, config: loadAuthConfig() } as unknown as ShellDeps, { readyCacheMs: 0 });
const metricsServer = createMetricsServer();
let base = '';
let mbase = '';
before(async () => {
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  await new Promise<void>((r) => metricsServer.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  mbase = `http://127.0.0.1:${(metricsServer.address() as AddressInfo).port}`;
});
after(() => { server.close(); metricsServer.close(); });

function labelNames(text: string): Set<string> {
  const out = new Set<string>();
  for (const line of text.split('\n')) {
    if (!line || line.startsWith('#')) continue;
    for (const m of line.split(' ')[0]!.matchAll(/([a-zA-Z_][a-zA-Z0-9_]*)="/g)) out.add(m[1]!);
  }
  return out;
}

// ---------------------------------------------------------------- 标签白名单与枚举
test('标签白名单恰好是约定的八个；取值只收低基数编码', () => {
  assert.deepEqual([...LABELS].sort(), ['method', 'outcome', 'provider', 'route', 'service', 'status_class', 'subject', 'task']);
  for (const [n, v] of [['service', 'bff'], ['route', '/bff/v0/students/{student_id}/today'], ['route', 'unmatched'], ['method', 'POST'],
    ['status_class', '4xx'], ['outcome', 'dedup_in_progress'], ['subject', 'aya'], ['task', 'T1'], ['provider', 'bailian']] as const) {
    assert.equal(labelValue(n, v), v);
  }
  const bad: Array<[string, unknown]> = [['student_id', 'x'], ['account', 'x'], ['student_id', '/a'], ['le', 'unmatched'],
    ['route', `/bff/v0/students/${UUID}/today`],
    ['route', '/bff/v0/x/123456'], ['route', 'bff/v0'], ['route', '/x/kid@example.invalid'], ['service', 'learning'], ['method', 'BREW'],
    ['status_class', '404'], ['service', undefined]];
  for (const [n, v] of bad) assert.throws(() => labelValue(n, v), `${n}=${String(v)}`);
});

test('枚举标签（学科、任务类型、供应商、结果）：枚举外的任何取值一律记 other', () => {
  const outside: Array<[string, unknown]> = [['subject', UUID], ['outcome', UUID.replaceAll('-', '')], ['task', PHONE],
    ['provider', 'a@b.example.invalid'], ['outcome', 'x'.repeat(49)], ['outcome', undefined], ['subject', `s${PHONE}`], ['outcome', `a${UUID}`],
    ['subject', 'math'], ['task', 'T10'], ['provider', 'openai'], ['outcome', 'OK'], ['subject', '']];
  for (const [n, v] of outside) assert.equal(labelValue(n, v), 'other', `${n}=${String(v)}`);
  for (const [name, values] of Object.entries(ENUMS)) {
    assert.ok(values.has('other') && values.size <= 64, name);
    for (const v of values) assert.match(v, /^[A-Za-z][A-Za-z0-9_-]{0,47}$/, `${name}=${v}`);
  }
});

test('登记指标：名字必须 starbuddy_ 开头、标签必须在白名单里、不能重复登记；写入时标签对不上就拒绝', () => {
  assert.throws(() => counter('http_requests_total', 'x', ['service']));
  assert.throws(() => counter('starbuddy_bad_label_total', 'x', ['student_id' as never]));
  assert.throws(() => counter('starbuddy_http_requests_total', 'x', ['service']), /重复/);
  const c = counter('starbuddy_test_only_total', 'x', ['outcome', 'route']);
  assert.throws(() => c.inc({ outcome: 'ok', route: `/x/${UUID}` }));
  assert.throws(() => c.inc({ outcome: 'ok' }));
  assert.throws(() => c.inc({ outcome: 'ok', route: '/x', subject: 'aya' }));
  assert.throws(() => c.inc({ outcome: 'ok', route: '/x' }, -1));
  c.inc({ outcome: UUID, route: '/x' });
  assert.equal(sampleValue('starbuddy_test_only_total', { outcome: 'other', route: '/x' }), 1);
});

// ---------------------------------------------------------------- 文本格式（自写，决定记录（续）第 4 条）
test('文本格式：HELP 转义反斜杠与换行，标签值转义反斜杠、双引号与换行；数值格式', () => {
  assert.equal(escapeHelp('a\\b\nc"d'), 'a\\\\b\\nc"d');
  assert.equal(escapeLabel('a\\b\nc"d'), 'a\\\\b\\nc\\"d');
  assert.equal(formatValue(Infinity), '+Inf');
  assert.equal(formatValue(-Infinity), '-Inf');
  assert.equal(formatValue(NaN), 'NaN');
  assert.equal(formatValue(0.25), '0.25');
  gauge('starbuddy_test_only_escape', '帮助\\文本\n第二行', ['route'], () => [[{ route: '/a' }, 1]]);
  const text = render();
  assert.ok(text.includes('# HELP starbuddy_test_only_escape 帮助\\\\文本\\n第二行\n'));
  assert.ok(text.includes('# TYPE starbuddy_test_only_escape gauge\nstarbuddy_test_only_escape{route="/a"} 1\n'));
  // 仪表读出不合格的标签值（会需要转义的原始值）：这一轮整族不报，不带出原始值
  gauge('starbuddy_test_only_bad_gauge', 'x', ['route'], () => [[{ route: '/a"b' }, 1]]);
  assert.ok(!render().includes('/a"b') && !render().includes('/a\\"b'));
  assert.ok(text.endsWith('\n'));
});

test('文本格式：直方图输出累计桶（含 +Inf）、_sum、_count；计数器、仪表各一行；每个族 HELP / TYPE 各一行', () => {
  const h = histogram('starbuddy_test_only_seconds', '测试直方图', ['route'], [0.1, 1]);
  h.observe({ route: '/h' }, 0.05);
  h.observe({ route: '/h' }, 0.5);
  h.observe({ route: '/h' }, 5);
  const text = render();
  const block = text.split('\n').filter((l) => l.includes('starbuddy_test_only_seconds'));
  assert.deepEqual(block, [
    '# HELP starbuddy_test_only_seconds 测试直方图',
    '# TYPE starbuddy_test_only_seconds histogram',
    'starbuddy_test_only_seconds_bucket{route="/h",le="0.1"} 1',
    'starbuddy_test_only_seconds_bucket{route="/h",le="1"} 2',
    'starbuddy_test_only_seconds_bucket{route="/h",le="+Inf"} 3',
    'starbuddy_test_only_seconds_sum{route="/h"} 5.55',
    'starbuddy_test_only_seconds_count{route="/h"} 3',
  ]);
  assert.equal(sampleValue('starbuddy_test_only_seconds', { route: '/h' }), 3);
  assert.equal(sampleValue('starbuddy_test_only_seconds', { route: '/h' }, '_sum'), 5.55);
  for (const name of catalog()) {
    assert.equal(text.split('\n').filter((l) => l.startsWith(`# HELP ${name} `)).length, 1, name);
    assert.equal(text.split('\n').filter((l) => l.startsWith(`# TYPE ${name} `)).length, 1, name);
  }
  // 每个样本行都是 名字{标签} 数值
  for (const line of text.split('\n')) {
    if (!line || line.startsWith('#')) continue;
    assert.match(line, /^starbuddy_[a-z0-9_]+(\{[a-z_]+="(?:[^"\\]|\\.)*"(,[a-z_]+="(?:[^"\\]|\\.)*")*\})? (-?[0-9.e+-]+|\+Inf|-Inf|NaN)$/, line);
  }
});

test('observeHttp：写指标出任何错都只丢这一条', () => {
  observeHttp(`/x/${UUID}`, 'GET', 200, 1);  // 路由不合格：不抛
  observeHttp('/x', undefined, 200, Number.NaN);  // 耗时不合格：不抛
  assert.ok(!render().includes(UUID));
});

// ---------------------------------------------------------------- 指标端口与请求指标
test('指标端口：只服务 GET /metrics（免会话、no-store、文本格式）；别的路径 404、别的方法 405', async () => {
  let r = await fetch(`${mbase}/metrics`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  assert.match(r.headers.get('content-type') ?? '', /^text\/plain; version=0\.0\.4/);
  assert.match(await r.text(), /starbuddy_http_requests_total/);
  for (const m of ['POST', 'PUT', 'DELETE']) {
    r = await fetch(`${mbase}/metrics`, { method: m });
    assert.equal(r.status, 405, m);
    assert.equal(r.headers.get('allow'), 'GET');
    await r.text();
  }
  for (const p of ['/metrics/', '/metrics?x=1', '/bff/v0/metrics', '/healthz', '/readyz', '/bff/v0/me', '/', '/Metrics']) {
    r = await fetch(`${mbase}${p}`);
    assert.equal(r.status, 404, p);
    assert.equal(await r.text(), '{"status":"not_found"}', p);
  }
});

test('指标端口配置：staging 缺省 9787、dev / test 不设不起；规范整数、不与业务端口相同', () => {
  assert.equal(metricsPort(undefined, 'staging', 8787), 9787);
  assert.equal(metricsPort('', 'staging', 8787), 9787);
  assert.equal(metricsPort(undefined, 'dev', 8787), null);
  assert.equal(metricsPort(undefined, 'test', 8787), null);
  assert.equal(metricsPort('9999', 'test', 8787), 9999);
  for (const bad of ['8787', '0', '65536', '09787', '97x', '-1', ' 9787']) {
    assert.throws(() => metricsPort(bad, 'staging', 8787), /BFF_METRICS_PORT/, bad);
  }
  const main = readFileSync(new URL('../src/main.ts', import.meta.url), 'utf8');
  assert.match(main, /mport = metricsPort\(process\.env\['BFF_METRICS_PORT'\], shellDeps\.env, port\);/);
  assert.match(main, /metricsServer\?\.listen\(mport!, listenAddr,/, '指标监听与业务监听同一地址');
});

test('业务端口不服务 /metrics：表外路径 404（与别的表外路径相同），不出指标内容', async () => {
  for (const m of ['GET', 'POST']) {
    const r = await fetch(`${base}/metrics`, { method: m });
    assert.equal(r.status, 404, m);
    assert.equal(r.headers.get('content-type'), 'application/problem+json');
    assert.ok(!(await r.text()).includes('starbuddy_'));
  }
});

test('请求按路由模板计数；表外路径记 unmatched；输出只有白名单标签、没有原始路径与 UUID', async () => {
  const me = { service: 'bff', route: '/bff/v0/me', method: 'GET', status_class: '4xx' };
  const un = { service: 'bff', route: 'unmatched', method: 'GET', status_class: '4xx' };
  const me0 = sampleValue('starbuddy_http_requests_total', me);
  const un0 = sampleValue('starbuddy_http_requests_total', un);
  let r = await fetch(`${base}/bff/v0/me`);
  assert.equal(r.status, 401);
  await r.text();
  r = await fetch(`${base}/nowhere/${UUID}/${PHONE}`);
  assert.equal(r.status, 404);
  await r.text();
  assert.equal(sampleValue('starbuddy_http_requests_total', me), me0 + 1);
  assert.equal(sampleValue('starbuddy_http_requests_total', un), un0 + 1);
  // 带路径参数的路由：标签是模板，不是带 UUID 的原始路径
  const byRoute = (route: string) => ['2xx', '4xx', '5xx'].reduce((a, sc) =>
    a + sampleValue('starbuddy_http_requests_total', { service: 'bff', route, method: 'POST', status_class: sc }), 0);
  const tpl = '/bff/v0/students/{student_id}/consents';
  const t0 = byRoute(tpl);
  r = await fetch(`${base}/bff/v0/students/${UUID}/consents`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
  await r.text();
  assert.equal(byRoute(tpl), t0 + 1);
  const text = await (await fetch(`${mbase}/metrics`)).text();
  assert.ok(!text.includes(UUID) && !text.includes(PHONE) && !text.includes('/nowhere'));
  assert.ok(!/route="\/(metrics|healthz|readyz)"/.test(text), '/metrics 与健康端点不计');
  for (const n of labelNames(text)) assert.ok((LABELS as readonly string[]).includes(n) || n === 'le', n);
});

test('就绪指标取最近一次 /readyz 的结果', async () => {
  await (await fetch(`${base}/readyz`)).text();
  assert.equal(sampleValue('starbuddy_ready', { service: 'bff' }), 1);
});

// ---------------------------------------------------------------- 计量计数
function fakePool(outcome: string, log: string[] = []): Pool {
  return { connect: async () => ({
    query: async (sql: string) => {
      log.push(sql.startsWith('SELECT platform.meter_') ? 'meter' : sql.split(' ')[0]!);
      return sql.includes('platform.meter_') ? { rows: [{ r: { outcome, usage_id: UUID, state: 'reserved' } }] } : { rows: [] };
    },
    release: () => {},
  }) } as unknown as Pool;
}
const input = { studentId: UUID, resourceType: 'diagnosis_full' as const, idempotencyKey: UUID, requestFingerprint: 'f', quantity: 1,
                sourceMethod: 'diagnose' as const };

test('预留 / 结算 / 释放（回滚）按结果编码计数；冲突（409）另计', async () => {
  const v = (name: string, outcome: string) => sampleValue(name, { outcome });
  const r0 = v('starbuddy_meter_reserve_total', 'reserved');
  const c0 = v('starbuddy_meter_conflict_total', 'reserve_conflict');
  const s0 = v('starbuddy_meter_settle_total', 'settled');
  const l0 = v('starbuddy_meter_release_total', 'released');
  const sc0 = v('starbuddy_meter_conflict_total', 'settle_conflict');
  await reserve(fakePool('reserved'), UUID, input);
  await reserve(fakePool('conflict'), UUID, input);
  await settle(fakePool('settled'), UUID, UUID, 1);
  await settle(fakePool('conflict'), UUID, UUID, 1);
  await release(fakePool('released'), UUID, UUID, 'upstream_rejected');
  assert.equal(v('starbuddy_meter_reserve_total', 'reserved'), r0 + 1);
  assert.equal(v('starbuddy_meter_conflict_total', 'reserve_conflict'), c0 + 1);
  assert.equal(v('starbuddy_meter_settle_total', 'settled'), s0 + 1);
  assert.equal(v('starbuddy_meter_conflict_total', 'settle_conflict'), sc0 + 1);
  assert.equal(v('starbuddy_meter_release_total', 'released'), l0 + 1);
  const d0 = v('starbuddy_meter_conflict_total', 'reserve_dedup_in_progress');
  observeMeter('reserve', 'dedup_in_progress');
  const o0 = v('starbuddy_meter_reserve_total', 'other');
  observeMeter('reserve', UUID);  // 枚举外的结果记 other，不进标签
  assert.equal(v('starbuddy_meter_conflict_total', 'reserve_dedup_in_progress'), d0 + 1);
  assert.equal(v('starbuddy_meter_reserve_total', 'other'), o0 + 1);
  assert.ok(!render().includes(UUID));
});

test('计量计数在事务提交（COMMIT）之后', async () => {
  const seen: Array<[string, string[]]> = [];
  for (const [step, call] of [
    ['reserve', (log: string[]) => reserve(fakePool('reserved', log), UUID, input)],
    ['settle', (log: string[]) => settle(fakePool('settled', log), UUID, UUID, 1)],
    ['release', (log: string[]) => release(fakePool('released', log), UUID, UUID, 'upstream_rejected')],
  ] as const) {
    const log: string[] = [];
    meterTesting.onObserve = (s) => { seen.push([s, [...log]]); };
    try {
      await call(log);
    } finally {
      delete meterTesting.onObserve;
    }
    assert.deepEqual(seen.at(-1), [step, ['BEGIN', 'SELECT', 'meter', 'COMMIT']], `${step}：计数时事务已提交`);
  }
});

test('计数抛异常时计量结果不变（预留、结算、释放与计量事件）', async () => {
  const before = await Promise.all([reserve(fakePool('reserved'), UUID, input), settle(fakePool('conflict'), UUID, UUID, 1),
    release(fakePool('released'), UUID, UUID, 'upstream_rejected')]);
  const r0 = sampleValue('starbuddy_meter_reserve_total', { outcome: 'reserved' });
  const e0 = sampleValue('starbuddy_meter_events_total', { outcome: 'result_unknown' });
  meterTesting.fail = true;
  try {
    const during = await Promise.all([reserve(fakePool('reserved'), UUID, input), settle(fakePool('conflict'), UUID, UUID, 1),
      release(fakePool('released'), UUID, UUID, 'upstream_rejected')]);
    assert.deepEqual(during, before, '结果与计数正常时逐项相同');
    promMetrics.increment('result_unknown');  // 计量事件计数出错同样不抛
  } finally {
    meterTesting.fail = false;
  }
  assert.equal(sampleValue('starbuddy_meter_reserve_total', { outcome: 'reserved' }), r0, '这一次计数丢掉');
  assert.equal(sampleValue('starbuddy_meter_events_total', { outcome: 'result_unknown' }), e0);
  promMetrics.increment('result_unknown');
  assert.equal(sampleValue('starbuddy_meter_events_total', { outcome: 'result_unknown' }), e0 + 1);
});

// ---------------------------------------------------------------- 日志：缺省访问日志、字段白名单与集中脱敏
test('缺省访问日志：一行 JSON（ts、level、service、request_id、route、status、duration_ms）', async () => {
  const lines: string[] = [];
  const orig = console.log;
  console.log = (line: string) => { lines.push(line); };
  try {
    const r = await fetch(`${base}/bff/v0/me`);
    await r.text();
  } finally {
    console.log = orig;
  }
  const d = JSON.parse(lines.at(-1)!) as Record<string, unknown>;
  assert.equal(d['level'], 'info');
  assert.equal(d['service'], 'bff');
  assert.match(String(d['ts']), /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
  assert.equal(d['route'], 'GET /me');
  assert.equal(d['status'], 401);
  assert.match(String(d['request_id']), /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/, 'request_id 原样保留');
  assert.equal(typeof d['duration_ms'], 'number');
});

// 安全审查 B 给出的样例：不在白名单里（或是禁止字段）的键整项丢掉；值里的手机号（含空格 / 连字符分隔）与 UUID 也替换
const REVIEW_SAMPLES: Record<string, unknown> = {
  studentId: UUID, student_name: '小明', message: '我不会做这道题', raw_text: '原文', ocr_text: '拍照识别的原文', question_text: '题面原文',
  photo_url: 'https://x.example.invalid/p.jpg', audio: 'base64...', guardianPhone: PHONE, sessionToken: 's'.repeat(40), session: 'abc',
  nickname: '小星', unknown_field: '随便什么',
};

test('字段白名单：审查样例整项丢掉；键名先驼峰归一；正文 UUID 与带分隔的手机号替换；学生、账号只收 16 位十六进制', () => {
  const out = logFields({ ...REVIEW_SAMPLES, student: '小明', event: 'probe', status: 200, durationMs: 7, requestId: UUID,
    detail: `call 138 1234 5678 / 138-1234-5678 for ${UUID}` });
  assert.deepEqual(Object.keys(out).sort(), ['detail', 'duration_ms', 'event', 'request_id', 'status']);
  assert.equal(out['duration_ms'], 7);
  assert.equal(out['request_id'], UUID);
  assert.equal(out['detail'], `call ${REDACTED} / ${REDACTED} for ${UUID_MARK}`);
  const text = JSON.stringify(out);
  for (const needle of ['小明', '不会做', '原文', 'p.jpg', 'base64', PHONE, '1234 5678', '小星', '随便']) assert.ok(!text.includes(needle), needle);
  assert.deepEqual(logFields({ student: 'ab12cd34ef56ab78', account: '0123456789abcdef' }), { student: 'ab12cd34ef56ab78', account: '0123456789abcdef' });
  assert.deepEqual(logFields({ student: UUID, account: 'kid@example.invalid', Student: 'AB12CD34EF56AB78' }), {});
  assert.deepEqual(logFields({ request_id: `call ${PHONE}` }), { request_id: `call ${REDACTED}` }, '不是 UUID 的 request_id 按文本脱敏');
  for (const [k, want] of [['studentId', 'student_id'], ['StudentName', 'student_name'], ['durationMs', 'duration_ms'], ['x-api-key', 'x_api_key']]) {
    assert.equal(normalizeKey(k!), want);
  }
});

test('白名单与 Python 同一份；白名单里没有禁止字段；学生、账号只作为哈希字段出现', () => {
  for (const k of LOG_FIELDS) {
    assert.ok(!forbiddenKey(k), k);
    assert.equal(normalizeKey(k), k);
  }
  assert.deepEqual([...HASHED_FIELDS].sort(), ['account', 'student']);
  for (const k of HASHED_FIELDS) assert.ok(!LOG_FIELDS.has(k) && forbiddenKey(k), k);
  const py = readFileSync(new URL('../../learning/learning/obs.py', import.meta.url), 'utf8');
  const block = py.split('LOG_FIELDS = frozenset(ENVELOPE + (')[1]!.split('))')[0]!;
  const env = py.split('ENVELOPE = (')[1]!.split(')')[0]!;
  const pyFields = new Set([...`${env}\n${block}`.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]!));
  assert.deepEqual([...LOG_FIELDS].sort(), [...pyFields].sort());
});

test('脱敏：禁止字段丢掉、模式替换、超长截断；元数据保留', () => {
  const rec = { status: 409, route: 'GET /me', usage_ref: 'ab'.repeat(8),
    answer: '作答原文', text: 'x', content: 'x', phone: PHONE, guardian_phone: PHONE, email: 'a@b.example.invalid', token: 't',
    session_token: 't', password: 'p', authorization: 'Bearer x', student_id: UUID, account_id: UUID, x_api_key: 'k', messages: [],
    note: `call ${PHONE} or kid@example.invalid, dsn postgresql://u@10.0.0.5/db, Bearer abc.def`,
    detail: '一段说明 '.repeat(200), nested: { stem: '题面', ok: 1, studentName: 'x', deeper: [{ email: 'x' }, { reason: 'ok' }] } };
  const out = scrub(rec) as Record<string, unknown>;
  for (const k of ['answer', 'text', 'content', 'phone', 'guardian_phone', 'email', 'token', 'session_token', 'password', 'authorization',
    'student_id', 'account_id', 'x_api_key', 'messages']) {
    assert.ok(!(k in out), k);
  }
  assert.equal(out['status'], 409);
  assert.equal(out['usage_ref'], 'ab'.repeat(8));
  const note = String(out['note']);
  assert.ok(!note.includes(PHONE) && !note.includes('kid@') && !note.includes('10.0.0.5') && !note.includes('abc.def') && note.includes(REDACTED));
  assert.ok(String(out['detail']).endsWith('…[truncated]'));
  assert.deepEqual(out['nested'], { ok: 1, deeper: [{}, { reason: 'ok' }] });
  for (const raw of ['id 11010520120101123X', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl', 'sk-AbCdEfGhIjKlMnOpQrStUv',
    'LTAI5tSyntheticSynthetic0', `token ${'a1'.repeat(32)}`, `+86 ${PHONE}`, '138 1234 5678', '138-1234-5678', `+86-${PHONE}`]) {
    assert.ok(scrubText(raw).includes(REDACTED), raw);
  }
  assert.equal(scrubText(`for ${UUID}`), `for ${UUID_MARK}`);
  assert.equal(scrubText('in_process_state_requires_single_instance'), 'in_process_state_requires_single_instance', '只有字母和下划线的长编码不是密钥');
  assert.ok(forbiddenKey('answer') && forbiddenKey('Guardian_Phone') && forbiddenKey('studentId') && forbiddenKey('session') && !forbiddenKey('input_tokens'));
  const line = JSON.parse(format('warn', { event: 'x', ts: 'spoofed', level: 'info', phone: PHONE, ...REVIEW_SAMPLES }, () => new Date(0))) as Record<string, unknown>;
  assert.deepEqual(line, { ts: '1970-01-01T00:00:00.000Z', level: 'warn', service: 'bff', event: 'x' }, '记录里的同名字段不覆盖信封；审查样例不出现');
});

test('源码里的访问日志备注（ctx.note）与 record 字段都在白名单里（学生、账号只作为哈希字段）', () => {
  const src = ['src/http/server.ts', 'src/http/routes.ts', 'src/http/trust-routes.ts', 'src/http/feature-routes.ts', 'src/http/shell-routes.ts',
    'src/http/practice-routes.ts', 'src/http/retest-routes.ts', 'src/http/family-goals-routes.ts', 'src/http/monthly-plan-routes.ts',
    'src/http/star-map-v1-routes.ts'].map((p) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')).join('\n');
  const keys = [...src.matchAll(/note\('([a-z_]+)'/g), ...src.matchAll(/record\['([a-z_]+)'\]/g)].map((m) => m[1]!);
  assert.ok(keys.length > 10);
  assert.deepEqual(keys.filter((k) => !LOG_FIELDS.has(k) && !HASHED_FIELDS.has(k)), [], '日志字段要先登记进 LOG_FIELDS（与 Python 同一份）');
  assert.deepEqual(keys.filter((k) => !HASHED_FIELDS.has(k) && forbiddenKey(k)), []);
});

test('注册的指标与 deploy/observability/metrics-inventory.yaml 一致', () => {
  const inv = readFileSync(new URL('../../../deploy/observability/metrics-inventory.yaml', import.meta.url), 'utf8');
  const block = inv.split('\n  bff:\n')[1]!.split('\nexternal:')[0]!;
  const listed = [...block.matchAll(/^ {4}- (starbuddy_[a-z0-9_]+)$/gm)].map((m) => m[1]!).sort();
  const mine = catalog().filter((n) => !n.startsWith('starbuddy_test_only_'));
  assert.deepEqual(mine, listed);
});
