// 白板状态折叠：前 cursor 步的结果只取决于 DSL 与步数（后退、跳转都是从头重算）；擦除连同高亮；讲解权；高亮带夹在画布内。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { bbox, boardAt, HIGHLIGHT_PAD, validateStageDsl, type StageDsl } from '../src/index.ts';
import { minimal, sample } from './fixtures.ts';

const valid = (v: unknown): StageDsl => {
  const r = validateStageDsl(v);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  return r.dsl;
};
const ids = (dsl: StageDsl, cursor: number) => boardAt(dsl, cursor).elements.map((e) => e.id);

test('逐步折叠样例', () => {
  const dsl = valid(sample());
  assert.deepEqual(boardAt(dsl, 0), { cursor: 0, elements: [], speaker: 'none', latest: [] });
  assert.deepEqual(ids(dsl, 1), ['t1']);
  assert.equal(boardAt(dsl, 1).speaker, 'teacher');
  assert.deepEqual(ids(dsl, 2), ['t1', 'f1', 'l1']);
  assert.deepEqual(boardAt(dsl, 2).latest, ['f1', 'l1']);
  assert.deepEqual(ids(dsl, 3), ['t1', 'f1', 'l1', 'f2', 'h1']);
  assert.deepEqual(boardAt(dsl, 3).latest, ['f2'], '高亮不算新写上的内容');
  assert.equal(boardAt(dsl, 4).speaker, 'buddy');
  // 第 5 步擦掉 f2：指向它的高亮 h1 一起消失；讲解权交还“无人”
  assert.deepEqual(ids(dsl, 5), ['t1', 'f1', 'l1', 'f3', 'b1', 'e1', 'l2', 't2']);
  assert.equal(boardAt(dsl, 5).speaker, 'none');
  // 公式按纯文本排版
  const f3 = boardAt(dsl, 5).elements.find((e) => e.id === 'f3');
  assert.deepEqual(f3, { kind: 'text', id: 'f3', x: 40, y: 300, text: 'x = (4)/(2) = 2', fontSize: 24, formula: true });
});

test('cursor 夹在 0..步数；同一步数结果相同（后退 = 重算）', () => {
  const dsl = valid(sample());
  assert.deepEqual(boardAt(dsl, -3), boardAt(dsl, 0));
  assert.deepEqual(boardAt(dsl, 99), boardAt(dsl, 5));
  assert.equal(boardAt(dsl, 2.7).cursor, 2);
  for (let k = 0; k <= 5; k++) assert.deepEqual(boardAt(dsl, k), boardAt(valid(sample()), k));
});

test('清空白板；高亮带按目标外接框加留白并夹在画布内', () => {
  const dsl = valid({ ...minimal(), steps: [
    { id: 's1', actions: [{ type: 'draw_shape', id: 'r', shape: 'rect', x: 0, y: 0, w: 100, h: 50 }, { type: 'highlight', id: 'h', target: 'r' }] },
    { id: 's2', actions: [{ type: 'write_text', id: 't', x: 100, y: 100, text: 'ab', size: 'sm' }, { type: 'highlight', id: 'h2', target: 't' }] },
    { id: 's3', actions: [{ type: 'clear' }, { type: 'write_text', id: 'u', x: 10, y: 40, text: '甲' }] },
  ] });
  const b1 = boardAt(dsl, 1).elements.find((e) => e.id === 'h')!;
  assert.deepEqual(bbox(b1), { x: 0, y: 0, w: 100 + HIGHLIGHT_PAD, h: 50 + HIGHLIGHT_PAD });
  const b2 = boardAt(dsl, 2).elements.find((e) => e.id === 'h2')!;
  // 文字外接框：x=100, y=100-24, w=ceil(2*0.6*24)=29, h=30
  assert.deepEqual(bbox(b2), { x: 100 - HIGHLIGHT_PAD, y: 76 - HIGHLIGHT_PAD, w: 29 + 2 * HIGHLIGHT_PAD, h: 30 + 2 * HIGHLIGHT_PAD });
  assert.deepEqual(ids(dsl, 3), ['u']);
  assert.deepEqual(boardAt(dsl, 3).latest, ['u']);
});
