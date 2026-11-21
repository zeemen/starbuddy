// 作答被拒后按 retry-after 冷却（specs/retry-after-cooldown）：
//   - retry-after 只认 1–300 的整数秒；冷却有头用头、封顶 30 秒，没有用 2 秒；
//   - 两个 BFF 客户端把 retry-after 带进失败返回，两个 server action 只在 rate_limited 时把它带回组件；
//   - 再练、复测的作答组件：提交按钮冷却时禁用（data-cooldown），冷却中的提交直接返回，换题时清掉冷却，不自动重发。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { COOLDOWN_DEFAULT_SEC, COOLDOWN_MAX_SEC, cooldownMs, parseRetryAfter } from '../src/lib/retry-after.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

test('parseRetryAfter：只认 1–300 的整数秒', () => {
  for (const [v, want] of [['1', 1], ['2', 2], [' 30 ', 30], ['300', 300]] as const) assert.equal(parseRetryAfter(v), want, v);
  for (const v of [null, undefined, '', '0', '301', '999', '-1', '1.5', '2e1', 'abc', '0x10', '1000',
                   'Wed, 21 Oct 2026 07:28:00 GMT', '1, 2']) {
    assert.equal(parseRetryAfter(v), null, String(v));
  }
});

test('cooldownMs：有 retry-after 用它（封顶 30 秒），没有用 2 秒', () => {
  assert.equal(COOLDOWN_DEFAULT_SEC, 2);
  assert.equal(COOLDOWN_MAX_SEC, 30);
  assert.equal(cooldownMs(1), 1000);
  assert.equal(cooldownMs(2), 2000);
  assert.equal(cooldownMs(30), 30_000);
  assert.equal(cooldownMs(300), 30_000, '封顶');
  for (const v of [null, undefined, 0, -3, 1.5, Number.NaN]) assert.equal(cooldownMs(v), 2000, String(v));
});

test('两个 BFF 客户端：失败返回带上 retry-after（经 parseRetryAfter）', () => {
  for (const f of ['src/lib/practice-api.ts', 'src/lib/retest-api.ts']) {
    const t = read(f);
    assert.match(t, /const retryAfterSec = parseRetryAfter\(r\.headers\.get\('retry-after'\)\);/, f);
    assert.ok(t.includes("...(retryAfterSec === null ? {} : { retryAfterSec })"), `${f}：没有合口径的头时不带这个字段`);
    assert.equal((t.match(/headers\.get\(/g) ?? []).length, 1, `${f}：只读 retry-after 这一个响应头`);
  }
});

test('两个 server action：只在 rate_limited 时把 retryAfterSec 带回组件', () => {
  for (const [f, fn] of [['src/practice/actions.ts', 'uiError'], ['src/retest/actions.ts', 'retestUiError']] as const) {
    const t = read(f);
    assert.match(t, new RegExp(`const e = ${fn}\\(r, 'answer'\\);\\n\\s+return e === 'rate_limited' \\? \\{ kind: 'error', error: e, retryAfterSec: r\\.retryAfterSec \\?\\? null \\} : err\\(e\\);`), f);
    assert.equal((t.match(/retryAfterSec/g) ?? []).length, 4, `${f}：注释与类型各一处、返回一处（两次），别处不带`);
  }
});

test('作答组件：被限流后冷却，冷却中禁用提交且提交直接返回，换题清掉冷却，冷却结束不自动重发', () => {
  for (const [f, id] of [['src/practice/PracticeItem.tsx', 'practice-submit'], ['src/retest/RetestItem.tsx', 'retest-submit']] as const) {
    const t = read(f);
    assert.match(t, /const \{ cooling, start: startCooldown, clear: clearCooldown \} = useCooldown\(\);/, f);
    assert.match(t, /if \(r\.error === 'rate_limited'\) startCooldown\(cooldownMs\(r\.retryAfterSec\)\);/, f);
    assert.match(t, /if \(!ready \|\| busy \|\| cooling\) return;/, `${f}：冷却中的提交直接返回`);
    assert.ok(t.includes(`disabled={busy || cooling} data-cooldown={cooling ? 'on' : 'off'} data-testid="${id}"`), `${f}：按钮冷却时禁用`);
    assert.match(t, /setChoice\(\[\]\);\n\s+clearCooldown\(\);/, `${f}：换题时清掉冷却`);
    assert.equal((t.match(/startCooldown\(/g) ?? []).length, 1, `${f}：只有限流时开始冷却`);
  }
  const hook = read('src/practice/use-cooldown.ts');
  assert.match(hook, /useEffect\(\(\) => \(\) => \{ if \(timer\.current !== null\) clearTimeout\(timer\.current\); \}, \[\]\);/, '卸载时清掉定时器');
  assert.doesNotMatch(hook, /submit|Action\(/, '冷却结束只恢复按钮，不提交');
});
