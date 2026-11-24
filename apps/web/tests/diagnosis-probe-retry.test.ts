// 诊断探针提交失败的处理（specs/diagnosis-probe-retry）：失败分类、下一步、server action 与组件接线。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { nextAfterProbe } from '../src/diagnosis/probe-retry.ts';
import { retryableFailure } from '../src/lib/retry-after.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

test('可重试：闸门被拒 503 SUBJECT_BUSY、限流 429、断连 / 超时（503 无错误码）；其他不重试', () => {
  for (const f of [{ status: 503, code: 'SUBJECT_BUSY' }, { status: 429, code: 'TOO_MANY_REQUESTS' }, { status: 429, code: null }, { status: 503, code: null }]) {
    assert.equal(retryableFailure(f), true, JSON.stringify(f));
  }
  for (const f of [{ status: 400, code: 'INVALID_REQUEST' }, { status: 403, code: 'FORBIDDEN' }, { status: 404, code: 'NOT_FOUND' },
                   { status: 409, code: 'SESSION_INCONSISTENT' }, { status: 402, code: 'QUOTA_UNAVAILABLE' }, { status: 503, code: 'UPSTREAM_UNAVAILABLE' },
                   { status: 500, code: 'INTERNAL' }]) {
    assert.equal(retryableFailure(f), false, JSON.stringify(f));
  }
});

test('下一步：记上 → recorded；宵禁 → curfew；可重试且这一步没重试过 → cooldown；第二次或不可重试 → skip', () => {
  assert.equal(nextAfterProbe({ ok: true }, false), 'recorded');
  assert.equal(nextAfterProbe({ ok: false, curfew: true }, false), 'curfew');
  assert.equal(nextAfterProbe({ ok: false, curfew: true, retryable: true }, false), 'curfew', '宵禁优先');
  assert.equal(nextAfterProbe({ ok: false, retryable: true, retryAfterSec: 2 }, false), 'cooldown');
  assert.equal(nextAfterProbe({ ok: false, retryable: true, retryAfterSec: 2 }, true), 'skip', '每一步只多给一次机会');
  assert.equal(nextAfterProbe({ ok: false }, false), 'skip');
  assert.equal(nextAfterProbe({ ok: true }, true), 'recorded', '重试后记上');
});

test('server action：只在可重试的失败时带 retryable 与 retryAfterSec；宵禁照旧', () => {
  const t = read('src/app/dev/diagnosis/actions.ts');
  const fn = t.slice(t.indexOf('export async function probeAction('), t.indexOf('export async function answerAction('));
  assert.match(fn, /if \(r\.ok\) return \{ ok: true \};/);
  assert.match(fn, /if \(isCurfew\(r\)\) return \{ ok: false, curfew: true \};/);
  assert.match(fn, /return retryableFailure\(r\) \? \{ ok: false, curfew: false, retryable: true, retryAfterSec: r\.retryAfterSec \?\? null \} : \{ ok: false, curfew: false \};/);
  const lib = read('src/lib/diagnosis.ts');
  assert.match(lib, /const retryAfterSec = parseRetryAfter\(r\.headers\.get\('retry-after'\)\);/);
});

test('组件接线：冷却时探针留在原处（不设跳过、显示已有文案、按 retry-after 冷却）；跳过清掉提示；按钮冷却时禁用', () => {
  const d = read('src/diagnosis/Diagnosis.tsx');
  const fn = d.slice(d.indexOf('const onPredict = async'), d.indexOf('const onSkip = '));
  assert.match(fn, /const next = nextAfterProbe\(r, retriedProbe\.current === seq\);/);
  const cool = fn.slice(fn.indexOf("if (next === 'cooldown') {"), fn.indexOf("if (next === 'skip')"));
  assert.match(cool, /retriedProbe\.current = seq;/);
  assert.match(cool, /setMessage\(C\.notSaved\);/, '沿用已有文案，不新增');
  assert.match(cool, /startProbeCooldown\(cooldownMs\(r\.retryAfterSec\)\);/);
  assert.match(cool, /return;/, '不进入作答、不自动重发');
  assert.doesNotMatch(cool, /skippedProbe|api\.probe/, '冷却时既不记跳过，也不重发');
  assert.match(fn, /if \(next === 'skip'\) skippedProbe\.current = seq;/);
  assert.match(d, /<ConfidenceProbe onAnswer=\{onPredict\} onSkip=\{onSkip\} cooling=\{probeCooling\} \/>/);
  const skip = d.slice(d.indexOf('const onSkip = '), d.indexOf('const submit = '));
  assert.match(skip, /setMessage\(''\);/);
  const p = read('src/probe/ConfidenceProbe.tsx');
  assert.match(p, /if \(busy \|\| cooling\) return;/);
  assert.equal((p.match(/disabled=\{busy \|\| cooling\}/g) ?? []).length, 2, '三个选项（同一行渲染）与跳过都在冷却时禁用');
});
