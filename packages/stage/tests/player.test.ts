// 播放状态机：idle → playing → paused → ended，逐步前进、后退、跳转；纯函数（不改入参、同一输入同一输出）。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { initialPlayer, step, type PlayerEvent, type PlayerState } from '../src/index.ts';

const S = (status: PlayerState['status'], cursor: number, total = 3): PlayerState => ({ status, cursor, total });
const run = (s: PlayerState, ...events: PlayerEvent[]) => events.reduce(step, s);

test('初始状态与参数检查', () => {
  assert.deepEqual(initialPlayer(3), S('idle', 0));
  for (const bad of [0, -1, 1.5, Number.NaN]) assert.throws(() => initialPlayer(bad), RangeError);
});

test('转移表', () => {
  const cases: [PlayerState, PlayerEvent, PlayerState][] = [
    [S('idle', 0), { type: 'play' }, S('playing', 1)],
    [S('idle', 0, 1), { type: 'play' }, S('ended', 1, 1)],
    [S('playing', 1), { type: 'pause' }, S('paused', 1)],
    [S('paused', 1), { type: 'play' }, S('playing', 1)],
    [S('ended', 3), { type: 'play' }, S('ended', 3)],
    [S('idle', 0), { type: 'pause' }, S('idle', 0)],
    [S('paused', 2), { type: 'pause' }, S('paused', 2)],
    [S('playing', 1), { type: 'tick' }, S('playing', 2)],
    [S('playing', 2), { type: 'tick' }, S('ended', 3)],
    [S('paused', 1), { type: 'tick' }, S('paused', 1)],
    [S('idle', 0), { type: 'tick' }, S('idle', 0)],
    [S('ended', 3), { type: 'tick' }, S('ended', 3)],
    [S('idle', 0), { type: 'next' }, S('paused', 1)],
    [S('paused', 1), { type: 'next' }, S('paused', 2)],
    [S('playing', 1), { type: 'next' }, S('playing', 2)],
    [S('paused', 2), { type: 'next' }, S('ended', 3)],
    [S('ended', 3), { type: 'next' }, S('ended', 3)],
    [S('ended', 3), { type: 'prev' }, S('paused', 2)],
    [S('playing', 2), { type: 'prev' }, S('playing', 1)],
    [S('paused', 1), { type: 'prev' }, S('idle', 0)],
    [S('idle', 0), { type: 'prev' }, S('idle', 0)],
    [S('paused', 1), { type: 'seek', step: 2 }, S('paused', 2)],
    [S('playing', 1), { type: 'seek', step: 2 }, S('playing', 2)],
    [S('idle', 0), { type: 'seek', step: 2 }, S('paused', 2)],
    [S('ended', 3), { type: 'seek', step: 1 }, S('paused', 1)],
    [S('paused', 1), { type: 'seek', step: 3 }, S('ended', 3)],
    [S('playing', 2), { type: 'seek', step: 0 }, S('idle', 0)],
    [S('paused', 1), { type: 'seek', step: 4 }, S('paused', 1)],
    [S('paused', 1), { type: 'seek', step: -1 }, S('paused', 1)],
    [S('paused', 1), { type: 'seek', step: 1.5 }, S('paused', 1)],
    [S('paused', 1), { type: 'seek', step: Number.NaN }, S('paused', 1)],
    [S('ended', 3), { type: 'reset' }, S('idle', 0)],
    [S('playing', 2), { type: 'reset' }, S('idle', 0)],
  ];
  for (const [from, e, to] of cases) {
    const frozen = Object.freeze({ ...from });
    assert.deepEqual(step(frozen, e), to, `${JSON.stringify(from)} + ${JSON.stringify(e)}`);
  }
});

test('完整播放：play 后每次 tick 前进一步，走完即 ended；之后后退、跳转、重来', () => {
  let s = initialPlayer(4);
  s = step(s, { type: 'play' });
  const seen = [s.cursor];
  while (s.status === 'playing') { s = step(s, { type: 'tick' }); seen.push(s.cursor); }
  assert.deepEqual(seen, [1, 2, 3, 4]);
  assert.equal(s.status, 'ended');
  assert.deepEqual(run(s, { type: 'prev' }, { type: 'prev' }), S('paused', 2, 4));
  assert.deepEqual(run(s, { type: 'seek', step: 0 }, { type: 'play' }), S('playing', 1, 4));
  assert.deepEqual(run(s, { type: 'reset' }), S('idle', 0, 4));
});

test('不变式：任意事件序列下 cursor 在 0..total，状态与 cursor 一致（确定性伪随机序列）', () => {
  let seed = 20261013;
  const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
  const kinds: PlayerEvent['type'][] = ['play', 'pause', 'tick', 'next', 'prev', 'seek', 'reset'];
  for (let round = 0; round < 200; round++) {
    const total = 1 + Math.floor(rnd() * 6);
    let s = initialPlayer(total);
    for (let i = 0; i < 50; i++) {
      const k = kinds[Math.floor(rnd() * kinds.length)]!;
      const e: PlayerEvent = k === 'seek' ? { type: 'seek', step: Math.floor(rnd() * (total + 3)) - 1 } : { type: k } as PlayerEvent;
      const before = JSON.stringify(s);
      const next = step(s, e);
      assert.equal(JSON.stringify(s), before, '不改入参');
      assert.deepEqual(step(s, e), next, '同一输入同一输出');
      s = next;
      assert.ok(Number.isInteger(s.cursor) && s.cursor >= 0 && s.cursor <= total);
      assert.equal(s.total, total);
      if (s.cursor === 0) assert.equal(s.status, 'idle');
      if (s.cursor === total) assert.equal(s.status, 'ended');
      if (s.status === 'idle') assert.equal(s.cursor, 0);
      if (s.status === 'ended') assert.equal(s.cursor, total);
    }
  }
});
