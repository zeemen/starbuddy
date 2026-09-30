// 三态映射单测（spec 验收“三态映射”）：mastery_state 六个取值 + 无记录，共七种输入，逐一断言。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { toDisplay, VERIFY_BELOW, type MasteryState } from '../src/star-map/display-state.ts';
import { randomKeys } from '../src/http/config.ts';
import { logHash } from '../src/http/secrets.ts';
import { isUuid } from '../src/star-map/query.ts';

test('七种输入各自的显示', () => {
  const cases: [MasteryState | null, string, boolean][] = [
    [null, 'unlit', false],
    ['unseen', 'unlit', false],
    ['understand', 'unlit', false],
    ['execute', 'half', false],
    ['stable', 'half', false],
    ['transfer', 'lit', false],
    ['retain', 'lit', true],
  ];
  for (const [state, display, retained] of cases) {
    const d = toDisplay(state, 0.9);
    assert.equal(d.display, display, String(state));
    assert.equal(d.retained, retained, String(state));
    assert.equal(d.needs_verification, false, String(state));
  }
});

test('待验证只看 confidence，不改变三态；无记录不标待验证', () => {
  assert.deepEqual(toDisplay('transfer', VERIFY_BELOW - 0.01), { display: 'lit', retained: false, needs_verification: true });
  assert.deepEqual(toDisplay('transfer', VERIFY_BELOW), { display: 'lit', retained: false, needs_verification: false });
  assert.deepEqual(toDisplay('stable', 0.3), { display: 'half', retained: false, needs_verification: true });
  assert.deepEqual(toDisplay(null, null), { display: 'unlit', retained: false, needs_verification: false });
});

test('未知的 state 直接报错，不静默当成未亮', () => {
  assert.throws(() => toDisplay('mastered' as MasteryState, 0.9));
});

test('日志里的 student_id 只记哈希；UUID 校验', () => {
  const id = '5a000000-0000-4000-8000-0000000000c1';
  const keys = randomKeys();
  assert.equal(logHash(keys, id).length, 16);
  assert.ok(!logHash(keys, id).includes('5a000000'));
  assert.notEqual(logHash(keys, id), logHash(randomKeys(), id), '带密钥：换密钥值变化');
  assert.ok(isUuid(id));
  assert.ok(!isUuid(id.toUpperCase()));  // 只认小写（与库里输出一致）
  assert.ok(!isUuid("5a000000-0000-4000-8000-0000000000c1' OR 1=1"));
});
