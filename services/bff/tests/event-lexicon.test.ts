// 行为事件与探针的编码过禁用推断词表（冲刺 2-L，spec 设计要点 4；CLAUDE.md 规则 12）：
//   - 事件类型、payload 键名、entry 取值、探针预测取值从 openapi/events.v0.yaml 抽出，用 docs/design/sie/sie_lexicon.py 的
//     匹配规则扫描（scripts/check_event_lexicon.py，Python：词表与匹配规则只有 Python 一份实现，这里不重写）；
//   - 抽出的编码与代码里的白名单（src/events/query.ts）逐一对照：OpenAPI 与代码一致，扫 OpenAPI 就等于扫了代码实际收的东西；
//   - 反例：塞了 exit_when_anxious 等合成编码的副本必须被发现，证明检查在工作。
// 解释器用 services/learning/.venv/bin/python（与 make learning-test 同一个，带 PyYAML）；没有时退到 python3。
// 缺 PyYAML（退出码 3）时非严格模式跳过，CONTRACT_STRICT=1 时失败。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { EVENT_KINDS, PREDICTIONS, SESSION_ENTRIES, validPayload } from '../src/events/query.ts';

const BFF = new URL('..', import.meta.url).pathname;
const ROOT = join(BFF, '..', '..');
const SCRIPT = join(BFF, 'scripts', 'check_event_lexicon.py');
const OPENAPI = join(BFF, 'openapi', 'events.v0.yaml');
const VENV = join(ROOT, 'services', 'learning', '.venv', 'bin', 'python');
const PY = existsSync(VENV) ? VENV : 'python3';

const run = (...args: string[]) => {
  const r = spawnSync(PY, [SCRIPT, ...args], { encoding: 'utf8' });
  return { rc: r.status ?? -1, out: `${r.stdout}${r.stderr}` };
};
const probe = run('--self-test');
const SKIP = probe.rc === 3 && process.env['CONTRACT_STRICT'] !== '1' ? '缺少 PyYAML（services/learning/.venv 没装）' : false;

test('词表检查脚本的反例自检：每组塞入的合成编码都被发现', { skip: SKIP }, () => {
  assert.equal(probe.rc, 0, probe.out);
  assert.match(probe.out, /自检通过/);
});

test('events.v0.yaml 的事件类型、payload 键名、entry 与预测取值不命中禁用推断词表，且与代码白名单一致', { skip: SKIP }, () => {
  const r = run('--json');
  assert.equal(r.rc, 0, r.out);
  const words = JSON.parse(r.out.trim().split('\n').at(-1)!) as Record<string, string[]>;
  const sorted = (xs: readonly string[]) => [...xs].sort();
  assert.deepEqual(words['kinds'], sorted(EVENT_KINDS), '事件类型与 src/events/query.ts 一致');
  assert.deepEqual(words['entries'], sorted(SESSION_ENTRIES), 'entry 取值一致');
  assert.deepEqual(words['predictions'], sorted(PREDICTIONS), '预测取值一致');
  assert.deepEqual(words['payload_keys'], ['duration_sec', 'entry', 'plan_task_id']);
  // payload 键名与代码白名单一致：用这些键拼出的合成 payload 代码都收，多一个键就不收
  assert.ok(validPayload('session_start', { entry: 'today' }));
  assert.ok(validPayload('session_end', { duration_sec: 60 }));
  assert.ok(validPayload('plan_done', { plan_task_id: '5a000000-0000-4000-8000-000000000001' }));
  assert.ok(!validPayload('session_start', { entry: 'today', extra: 'x' }));
});

test('反例：往 OpenAPI 副本里加一个会命中词表的事件类型、payload 键与 entry，检查失败并指出命中项', { skip: SKIP }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'event-lexicon-'));
  try {
    const bad = readFileSync(OPENAPI, 'utf8')
      .replace('kind: { const: session_start }', 'kind: { type: string, enum: [session_start, exit_when_anxious] }')
      .replace('required: [duration_sec]\n              properties:\n',
        'required: [duration_sec]\n              properties:\n                mood_frustrated: { type: string }\n')
      .replace('enum: [today, star_map, subject, onboarding, other]', 'enum: [today, star_map, subject, onboarding, other, gifted_track]');
    assert.notEqual(bad, readFileSync(OPENAPI, 'utf8'));
    const path = join(dir, 'events.v0.yaml');
    writeFileSync(path, bad);
    const r = run('--openapi', path);
    assert.equal(r.rc, 1, r.out);
    for (const w of ['exit_when_anxious', 'mood_frustrated', 'gifted_track']) assert.ok(r.out.includes(w), `没有指出 ${w}：${r.out}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
