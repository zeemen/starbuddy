// 诊断作答提交被拒后的冷却（specs/diagnosis-answer-cooldown）：server action 的返回与组件接线。
// 失败分类（retryableFailure）的用例在 tests/diagnosis-probe-retry.test.ts，探针与作答共用。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

test('server action：宵禁、届别变化照旧；只在可重试的失败时带 retryable 与 retryAfterSec', () => {
  const t = read('src/app/dev/diagnosis/actions.ts');
  const fn = t.slice(t.indexOf('export async function answerAction('), t.indexOf('export async function pauseAction('));
  assert.match(fn, /Promise<AnswerOutcome>/);
  const ok = fn.indexOf('if (r.ok) return { ok: true };');
  const curfew = fn.indexOf('if (isCurfew(r)) return { ok: false, curfew: true };');
  const restart = fn.indexOf('if (restart) return { ok: false, curfew: false, restart };');
  const retry = fn.indexOf('return retryableFailure(r) ? { ok: false, curfew: false, retryable: true, retryAfterSec: r.retryAfterSec ?? null } : { ok: false, curfew: false };');
  assert.ok(ok > 0 && curfew > ok && restart > curfew && retry > restart, '先成功，再宵禁、届别变化，最后才看可不可重试');
  assert.match(t, /import \{ retryableFailure \} from '\.\.\/\.\.\/\.\.\/lib\/retry-after\.ts';/, '与探针共用同一个分类');
});

test('组件接线：可重试才冷却（按 retry-after），沿用已有文案、不重发、不清作答；冷却中不提交；换题清冷却', () => {
  const d = read('src/diagnosis/Diagnosis.tsx');
  assert.match(d, /answer\(sessionId: string, seq: number, answer: Answer, work: string \| null, elapsedSec: number\): Promise<AnswerOutcome>;/);
  assert.match(d, /const \{ cooling: answerCooling, start: startAnswerCooldown, clear: clearAnswerCooldown \} = useCooldown\(\);/);
  const fn = d.slice(d.indexOf('const submit = async'), d.indexOf('const pause = async'));
  assert.match(fn, /if \(!session \|\| !view\?\.step \|\| busy \|\| answerCooling\) return;/, '冷却中按 Enter 提交也不发请求');
  const fail = fn.slice(fn.indexOf('if (!r.ok) {'), fn.indexOf("setMessage('');\n      await load(session);"));
  assert.match(fail, /if \(r\.retryable === true\) startAnswerCooldown\(cooldownMs\(r\.retryAfterSec\)\);/);
  assert.match(fail, /return setMessage\(C\.notSaved\);/, '沿用已有文案，不新增');
  assert.doesNotMatch(fail, /api\.answer|setText|setWork|setChoice/, '不自动重发，作答与过程原样留着');
  assert.ok(fn.indexOf('if (r.curfew) return toCurfew();') < fn.indexOf('if (!r.ok) {'), '宵禁先于冷却');
  assert.ok(fn.indexOf('if (r.restart) return toRestart(r.restart);') < fn.indexOf('if (!r.ok) {'), '届别变化先于冷却');
  const show = d.slice(d.indexOf('const show = useCallback('), d.indexOf('const toCurfew = '));
  assert.match(show, /clearAnswerCooldown\(\);/);
  assert.match(show, /\}, \[clearAnswerCooldown\]\);/);
  assert.match(d, /<button type="submit" disabled=\{busy \|\| answerCooling\} data-cooldown=\{answerCooling \? 'on' : 'off'\}>\{C\.submit\}<\/button>/);
  assert.doesNotMatch(d.slice(d.indexOf("className=\"diagnosis-pause\"")), /answerCooling/, '冷却中仍可暂停');
});
