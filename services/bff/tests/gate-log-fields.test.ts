// 闸门的日志字段与定时汇总（specs/gate-log-fields）：
//   - 访问日志的键（ctx.note / note / 闸门 observe 的两参数写法）都必须在日志白名单里：生产走 format / emit，不在白名单的整项丢掉；
//     闸门的 gate、gate_subject、gate_queue_ms、gate_reason 以前就是这样在生产上丢的（集成测试换了日志函数，不经白名单）；
//   - 真实 format 输出里有这四个字段；
//   - 闸门打开时按间隔写 event: subject_gate_stats，counts 里只有各科计数，经白名单后原样保留。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createSubjectGate, GATE_STATS_INTERVAL_MS, loadConcurrencyConfig, startGateStatsLog, type StatsTimer } from '../src/gate/subject-gate.ts';
import { format, HASHED_FIELDS, LOG_FIELDS } from '../src/observability/log.ts';

const BFF = fileURLToPath(new URL('..', import.meta.url));
const files = (d: string): string[] => readdirSync(d).flatMap((n) => {
  const p = join(d, n);
  return statSync(p).isDirectory() ? files(p) : /\.ts$/.test(n) ? [p] : [];
});

test('访问日志的键都在白名单里（两参数的 note 与闸门 observe）', () => {
  const keys = new Map<string, string>();
  for (const f of files(join(BFF, 'src'))) {
    const t = readFileSync(f, 'utf8');
    for (const m of t.matchAll(/\b(?:note|observe)\??\.?\(\s*'([a-z_]+)'\s*,/g)) keys.set(m[1]!, relative(BFF, f));
  }
  for (const k of ['gate', 'gate_subject', 'gate_queue_ms', 'gate_reason', 'route_group', 'outcome']) assert.ok(keys.has(k), `扫描到了 ${k}`);
  const missing = [...keys].filter(([k]) => !LOG_FIELDS.has(k) && !HASHED_FIELDS.has(k)).map(([k, f]) => `${k}（${f}）`);
  assert.deepEqual(missing, [], '这些键在生产日志里会被整项丢掉：加进 LOG_FIELDS（并同步 Python 三份 obs.py）');
});

test('真实 format 输出里有闸门的四个字段', () => {
  const line = JSON.parse(format('info', { event: 'http_request', route: '/bff/v0/x', status: 503, gate: 'rejected', gate_subject: 'aya',
                                          gate_queue_ms: 'lt100', gate_reason: 'full' })) as Record<string, unknown>;
  assert.equal(line['gate'], 'rejected');
  assert.equal(line['gate_subject'], 'aya');
  assert.equal(line['gate_queue_ms'], 'lt100');
  assert.equal(line['gate_reason'], 'full');
});

function fakeTimer() {
  const timers: { fn: () => void; ms: number; stopped: boolean }[] = [];
  const timer: StatsTimer = {
    every: (fn, ms) => { const t = { fn, ms, stopped: false }; timers.push(t); return t; },
    stop: (t) => { (t as { stopped: boolean }).stopped = true; },
  };
  return { timers, timer };
}

test('定时汇总：每 60 秒一行 subject_gate_stats，counts 只有各科计数，经白名单后原样保留；停止后不再写', async () => {
  assert.equal(GATE_STATS_INTERVAL_MS, 60_000);
  const gate = createSubjectGate(loadConcurrencyConfig());
  const g = await gate.acquire('aya', { student: '59000000-0000-4000-8000-0000000000c1' });
  assert.ok(g.ok);
  const lines: Record<string, unknown>[] = [];
  const { timers, timer } = fakeTimer();
  const stop = startGateStatsLog(gate, (r) => lines.push(JSON.parse(format('info', r)) as Record<string, unknown>), undefined, timer);
  assert.equal(timers.length, 1);
  assert.equal(timers[0]!.ms, 60_000);
  assert.equal(lines.length, 0, '启动时不立即写');
  timers[0]!.fn();
  assert.equal(lines.length, 1);
  const line = lines[0]!;
  assert.equal(line['event'], 'subject_gate_stats');
  const counts = line['counts'] as Record<string, Record<string, number>>;
  assert.deepEqual(Object.keys(counts).sort(), ['aya', 'wenli', 'zhixing']);
  assert.equal(counts['aya']!['inflight'], 1);
  assert.equal(counts['aya']!['admitted'], 1);
  for (const lane of Object.values(counts)) {
    assert.deepEqual(Object.keys(lane).sort(), ['admitted', 'inflight', 'queued', 'rejected_aborted', 'rejected_full', 'rejected_own_limit',
                                                 'rejected_timeout', 'waiting'], '八项都在（rejected_per_student 改名后不被当成学生标识丢掉）');
    for (const v of Object.values(lane)) assert.equal(typeof v, 'number');
  }
  assert.doesNotMatch(JSON.stringify(line), /59000000|student/, '没有学生信息');
  stop();
  assert.equal(timers[0]!.stopped, true);
  if (g.ok) g.release();
});
