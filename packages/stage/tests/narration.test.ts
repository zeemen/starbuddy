// 讲解 DSL v0.2 的“说 / 画 / 指”（specs/stage-dsl-v0.2）：校验器的配对规则、point 与槽位、台词稿核对、字幕、白板上的指向与槽位渲染。
// 台词全是明显的合成占位，不是人设话术（规则 7）。全部是合成数据。
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';
import {
  boardAt, captionAt, isValidatedScript, lineProblem, SCRIPT_LIMITS, validateStageDsl, validateStagePair, type ValidatedStageDsl,
} from '../src/index.ts';
import { minimal, narrated, narratedScript } from './fixtures.ts';

register('./tsx-loader.mjs', import.meta.url);
const { renderToStaticMarkup } = await import('react-dom/server');
const { createElement } = await import('react');
const { createWhiteboard } = await import('../src/Whiteboard.tsx');
const Whiteboard = createWhiteboard(createElement);

const ok = (raw: unknown): ValidatedStageDsl => {
  const r = validateStageDsl(raw);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  return r.dsl;
};
const codes = (raw: unknown) => { const r = validateStageDsl(raw); return r.ok ? [] : r.errors.map((e) => `${e.path} ${e.code}`); };
const has = (raw: unknown, code: string, path: string) => assert.ok(codes(raw).includes(`${path} ${code}`), `${path} ${code} ∉ ${codes(raw).join('; ')}`);

test('合成样例合格：台词引用、槽位、两种指向都保留；narrated 原样输出', () => {
  const dsl = ok(narrated());
  assert.equal(dsl.narrated, true);
  assert.deepEqual(dsl.slots, [{ id: 'fig', kind: 'item_figure', x: 600, y: 40, w: 320, h: 200 }]);
  assert.deepEqual(dsl.steps.map((s) => s.say ?? null), ['l1', 'l2', 'l3', 'l4', null]);
  assert.deepEqual(dsl.steps[2]!.actions, [{ type: 'point', target: 'f1', mode: 'laser' }]);
  assert.ok(Object.isFrozen(dsl.slots));
});

test('“说 / 画”配对：有台词的讲解里，带书写或作图的步骤必须有台词；只说不写的步骤可以没有动作', () => {
  for (const write of [{ type: 'write_text', id: 'x', x: 40, y: 300, text: '（合成）' }, { type: 'draw_line', id: 'x', x1: 40, y1: 300, x2: 200, y2: 300 },
    { type: 'draw_shape', id: 'x', shape: 'rect', x: 40, y: 260, w: 80, h: 40 }, { type: 'write_formula', id: 'x', x: 40, y: 300, latex: 'x = 1' }]) {
    const d = narrated();
    d.steps[4].actions.push(write);
    has(d, 'unpaired_board', '$.steps[4]');
  }
  const silent = narrated();
  silent.steps[4] = { id: 's5', actions: [{ type: 'highlight', id: 'h1', target: 'f1' }, { type: 'speaker', role: 'none' }] };
  ok(silent);  // 高亮、切换讲解权、擦除不算板书，可以不说
  const talk = narrated();
  talk.steps.splice(4, 0, { id: 's9', say: 'l9', actions: [] });
  ok(talk);
  const empty = narrated();
  empty.steps[4] = { id: 's5', actions: [] };
  has(empty, 'empty', '$.steps[4].actions');
});

test('无声板书（narrated: false）里不得出现台词与指向；台词编号格式与唯一', () => {
  const d = minimal();
  d.steps[0].say = 'l1';
  has(d, 'not_narrated', '$.steps[0].say');
  const p = minimal([{ type: 'write_text', id: 'a', x: 10, y: 40, text: '（合成）' }, { type: 'point', target: 'a', mode: 'laser' }]);
  has(p, 'not_narrated', '$.steps[0].actions[1]');
  const bad = narrated(); bad.steps[0].say = 'L1';
  has(bad, 'bad_id', '$.steps[0].say');
  const dup = narrated(); dup.steps[1].say = 'l1';
  has(dup, 'duplicate_id', '$.steps[1].say');
  const nt = narrated(); nt.narrated = 'yes';
  has(nt, 'bad_type', '$.narrated');
  const { narrated: _n, ...missing } = narrated();
  has(missing, 'missing_field', '$.narrated');
});

test('point：只能指现存的非高亮元素或声明的槽位；模式只有两种；一步最多一个；必须配台词', () => {
  const later = narrated(); later.steps[1].actions.unshift({ type: 'point', target: 'f2', mode: 'laser' });
  has(later, 'unknown_target', '$.steps[1].actions[0].target');  // f2 第 4 步才写
  const hl = narrated(); hl.steps[3].actions.push({ type: 'highlight', id: 'h1', target: 'f1' }, { type: 'point', target: 'h1', mode: 'laser' });
  has(hl, 'unknown_target', '$.steps[3].actions[3].target');
  const gone = narrated(); gone.steps[2].actions.unshift({ type: 'clear', targets: ['f1'] });
  has(gone, 'unknown_target', '$.steps[2].actions[1].target');
  const mode = narrated(); mode.steps[2].actions[0].mode = 'glow';
  has(mode, 'bad_enum', '$.steps[2].actions[0].mode');
  const two = narrated(); two.steps[2].actions.push({ type: 'point', target: 'fig', mode: 'spotlight' });
  has(two, 'too_many', '$.steps[2].actions');
  const mute = narrated(); delete mute.steps[2].say;
  has(mute, 'point_without_say', '$.steps[2]');
  const extra = narrated(); extra.steps[2].actions[0].color = 'red';
  has(extra, 'unknown_field', '$.steps[2].actions[0].color');
});

test('槽位：字段白名单、类型、在画布内、与元素 id 不重复；不能被高亮或擦除', () => {
  const k = narrated(); k.slots[0].kind = 'photo';
  has(k, 'bad_enum', '$.slots[0].kind');
  const out = narrated(); out.slots[0].x = 800;
  has(out, 'out_of_canvas', '$.slots[0]');
  const dup = narrated(); dup.slots[0].id = 'f1';
  has(dup, 'duplicate_id', '$.steps[1].actions[0].id');
  const many = narrated(); many.slots = Array.from({ length: 5 }, (_, i) => ({ id: `g${i}`, kind: 'item_figure', x: 0, y: 0, w: 10, h: 10 }));
  has(many, 'too_many', '$.slots');
  const url = narrated(); url.slots[0].src = 'https://example.invalid/a.png';
  has(url, 'unknown_field', '$.slots[0].src');
  const hl = narrated(); hl.steps[4].actions.unshift({ type: 'highlight', id: 'h1', target: 'fig' });
  has(hl, 'unknown_target', '$.steps[4].actions[0].target');
  const cl = narrated(); cl.steps[4].actions.unshift({ type: 'clear', targets: ['fig'] });
  has(cl, 'unknown_target', '$.steps[4].actions[0].targets[0]');
  const none = narrated(); none.slots = [];
  has(none, 'empty', '$.slots');
});

test('台词稿：合格时冻结并登记；字幕按步取', () => {
  const dsl = ok(narrated());
  const r = validateStagePair(dsl, narratedScript());
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.ok(isValidatedScript(r.script) && Object.isFrozen(r.script.lines));
  assert.equal(captionAt(dsl, r.script, 0), null);
  assert.deepEqual(captionAt(dsl, r.script, 1), { id: 'l1', role: 'teacher', text: '（合成台词）先看题图。' });
  assert.equal(captionAt(dsl, r.script, 4)!.role, 'buddy');
  assert.equal(captionAt(dsl, r.script, 5), null, '第 5 步没有台词');
  assert.equal(captionAt(dsl, narratedScript() as never, 1), null, '没校验过的稿不出字幕');
});

test('台词稿：缺台词、多余台词、角色与讲解权不符、超长、超过两句、含链接、版本不对一律拒收', () => {
  const dsl = ok(narrated());
  const fails = (mut: (s: Record<string, any>) => void, code: string) => {
    const s = narratedScript(); mut(s);
    const r = validateStagePair(dsl, s);
    assert.ok(!r.ok && r.errors.some((e) => e.code === code), `${code} ∉ ${JSON.stringify(!r.ok && r.errors)}`);
  };
  fails((s) => s.lines.pop(), 'unknown_line');
  fails((s) => s.lines.push({ id: 'l9', role: 'teacher', text: '（合成台词）多余。' }), 'unused_line');
  fails((s) => { s.lines[0].role = 'buddy'; }, 'bad_role');                  // 第 1 步讲解权在老师
  fails((s) => { s.lines[3].role = 'teacher'; }, 'bad_role');                // 第 4 步讲解权在点点
  fails((s) => { s.lines[0].text = '合'.repeat(SCRIPT_LIMITS.maxSayChars + 1); }, 'too_long');
  fails((s) => { s.lines[0].text = '一。二。三。'; }, 'too_many_sentences');
  fails((s) => { s.lines[0].text = '看 https://example.invalid'; }, 'link_or_script');
  fails((s) => { s.lines[0].text = '<b>x</b>'; }, 'html');
  fails((s) => { s.lines[0].voice = 'x'; }, 'unknown_field');
  fails((s) => { s.dsl_version = '0.1.0'; }, 'bad_version');
  fails((s) => { s.lines[1].id = 'l1'; }, 'duplicate_id');
  assert.equal(lineProblem('（合成台词）好。对吗？'), null, '两句可以');
  const raw = validateStagePair(narrated() as never, narratedScript());
  assert.ok(!raw.ok, '没校验过的 DSL 不配对');
});

test('讲解权：没人讲的时候说话不行（台词角色必须等于当时的讲解权）', () => {
  const d = narrated(); d.steps[0].actions = [{ type: 'point', target: 'fig', mode: 'spotlight' }];  // 第 1 步没切到老师
  const r = validateStagePair(ok(d), narratedScript());
  assert.ok(!r.ok && r.errors.some((e) => e.path === '$.lines[0].role' && e.code === 'bad_role'));
});

test('白板：指向只在当步显示（聚光是粗描边框、激光笔是小三角），槽位是空框；无外链、无内联样式', () => {
  const dsl = ok(narrated());
  assert.equal(boardAt(dsl, 1).point?.mode, 'spotlight');
  assert.equal(boardAt(dsl, 2).point, null, '下一步自动收起');
  assert.deepEqual(boardAt(dsl, 3).point?.target, 'f1');
  const html = (cursor: number) => renderToStaticMarkup(createElement(Whiteboard, { dsl, cursor, layout: 'desktop' }));
  const s1 = html(1);
  assert.match(s1, /<rect[^>]*data-slot="fig"[^>]*data-slot-kind="item_figure"/);
  assert.match(s1, /<rect[^>]*data-point="spotlight"[^>]*data-target="fig"[^>]*stroke-width="6"/);
  const s3 = html(3);
  assert.match(s3, /<polygon[^>]*data-point="laser"[^>]*data-target="f1"/);
  assert.doesNotMatch(html(2), /data-point=/);
  for (const out of [s1, s3]) assert.doesNotMatch(out, /https?:|href|\sstyle=|<image|<foreignObject|<script/i);
});
