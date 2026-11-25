// “指着元素提问”（specs/stage-point-to-ask 第二节第 1–4、6 条）：提问对象校验（只能指已经出现的东西）、元素来自哪一步、再讲一遍跳到哪、
// 白板可提问模式（缺省关闭时输出不变；点击区、读屏标签、选中虚线框、最小尺寸）。全部合成数据。
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';
import {
  askableAt, boardAt, DSL_VERSION, originsAt, QUESTION_INTENTS, replayCursor, STAGE_LAYOUTS, validateStageDsl, validateStageQuestion,
  type StageAnchor, type ValidatedStageDsl,
} from '../src/index.ts';
import type { AskableProps } from '../src/Whiteboard.tsx';
import { narrated, sample } from './fixtures.ts';

register('./tsx-loader.mjs', import.meta.url);
const { renderToStaticMarkup } = await import('react-dom/server');
const { createElement } = await import('react');
const { createWhiteboard, ASK_MIN_STAGE_UNITS } = await import('../src/Whiteboard.tsx');
const Whiteboard = createWhiteboard(createElement);

const valid = (raw: unknown): ValidatedStageDsl => {
  const r = validateStageDsl(raw);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  return r.dsl;
};
const DSL = valid(sample());        // s1 t1 | s2 f1 l1 | s3 f2 h1 | s4 f3 b1 | s5 清 f2，e1 l2 t2
const NAR = valid(narrated());      // 槽位 fig
const q = (anchor: unknown, cursor = 3, intent = 'why_step', extra: Record<string, unknown> = {}) =>
  ({ dsl_version: DSL_VERSION, cursor, anchor, intent, ...extra });
const codes = (dsl: ValidatedStageDsl, raw: unknown) => {
  const r = validateStageQuestion(dsl, raw);
  return r.ok ? [] : r.errors.map((e) => e.code);
};

test('合格的提问：元素、槽位、已播放的步；结果冻结、不保留调用方引用', () => {
  for (const intent of QUESTION_INTENTS) assert.deepEqual(codes(DSL, q({ kind: 'element', id: 'f1' }, 3, intent)), []);
  assert.deepEqual(codes(NAR, q({ kind: 'slot', id: 'fig' }, 1)), []);
  assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's3' }, 3)), []);
  const raw = q({ kind: 'element', id: 'f1' });
  const r = validateStageQuestion(DSL, raw);
  assert.ok(r.ok);
  assert.ok(Object.isFrozen(r.question) && Object.isFrozen(r.question.anchor));
  assert.notEqual(r.question, raw);
  assert.notEqual(r.question.anchor, raw.anchor);
  assert.deepEqual(r.question, { dsl_version: DSL_VERSION, cursor: 3, anchor: { kind: 'element', id: 'f1' }, intent: 'why_step' });
});

test('只能指已经出现的东西：未来的元素、未来的步、已擦掉的元素一律 not_visible', () => {
  assert.deepEqual(codes(DSL, q({ kind: 'element', id: 'f3' }, 3)), ['not_visible']);     // 第 4 步才写
  assert.deepEqual(codes(DSL, q({ kind: 'element', id: 'f2' }, 5)), ['not_visible']);     // 第 5 步擦掉
  assert.deepEqual(codes(DSL, q({ kind: 'element', id: 'f2' }, 4)), []);
  assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's4' }, 3)), ['not_visible']);        // 还没播放到
  assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's3' }, 2)), ['not_visible']);
});

test('高亮框、不存在的元素、未声明的槽位、不存在的步：unknown_anchor', () => {
  assert.deepEqual(codes(DSL, q({ kind: 'element', id: 'h1' }, 3)), ['unknown_anchor']);
  assert.deepEqual(codes(DSL, q({ kind: 'element', id: 'zz' }, 3)), ['unknown_anchor']);
  assert.deepEqual(codes(DSL, q({ kind: 'slot', id: 'fig' }, 3)), ['unknown_anchor']);    // 这份 DSL 没有槽位
  assert.deepEqual(codes(NAR, q({ kind: 'slot', id: 'f1' }, 3)), ['unknown_anchor']);     // 元素 id 不是槽位
  assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's9' }, 3)), ['unknown_anchor']);
  for (const a of [null, 'f1', [], { kind: 'node', id: 'f1' }, { kind: 'element', id: 'F1' }, { kind: 'element' }, { kind: 'element', id: 3 }]) {
    assert.deepEqual(codes(DSL, q(a, 3)), ['unknown_anchor'], JSON.stringify(a));
  }
});

test('版本、步数、意图、字段白名单', () => {
  assert.deepEqual(codes(DSL, q({ kind: 'element', id: 'f1' }, 3, 'why_step', { dsl_version: '0.1.0' })), ['bad_version']);
  for (const c of [0, 6, 2.5, '3', null, -1]) assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's1' }, c as number)), ['bad_cursor'], String(c));
  for (const i of ['why', '', null, 'WHY_STEP', 'free text']) assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's1' }, 3, i as string)), ['bad_intent']);
  assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's1' }, 3, 'why_step', { text: '（合成）自由文字' })), ['unknown_field']);
  assert.deepEqual(codes(DSL, q({ kind: 'step', id: 's1', note: 'x' }, 3)), ['unknown_field']);
  for (const raw of [null, [], 'q', 3, Object.create(null)]) assert.deepEqual(codes(DSL, raw), ['not_object']);
  assert.throws(() => validateStageQuestion(sample() as unknown as ValidatedStageDsl, q({ kind: 'step', id: 's1' })), TypeError);
});

test('提问对象不含学生数据：只有 id、步数与编码', () => {
  const r = validateStageQuestion(NAR, q({ kind: 'slot', id: 'fig' }, 2, 'what_is'));
  assert.ok(r.ok);
  assert.deepEqual(Object.keys(r.question).sort(), ['anchor', 'cursor', 'dsl_version', 'intent']);
  assert.deepEqual(Object.keys(r.question.anchor).sort(), ['id', 'kind']);
});

test('元素来自哪一步：originsAt 与白板一致；擦掉后不在；清屏后再写记新的步', () => {
  assert.deepEqual([...originsAt(DSL, 3)], [['t1', 0], ['f1', 1], ['l1', 1], ['f2', 2]]);
  assert.deepEqual([...originsAt(DSL, 5)], [['t1', 0], ['f1', 1], ['l1', 1], ['f3', 3], ['b1', 3], ['e1', 4], ['l2', 4], ['t2', 4]]);
  for (let k = 0; k <= 5; k++) {
    const onBoard = boardAt(DSL, k).elements.filter((e) => e.kind !== 'highlight').map((e) => e.id);
    assert.deepEqual([...originsAt(DSL, k).keys()], onBoard, `cursor ${k}`);
  }
  const cleared = valid({ ...sample(), steps: [
    { id: 's1', actions: [{ type: 'write_text', id: 'a', x: 10, y: 40, text: '（合成）甲' }] },
    { id: 's2', actions: [{ type: 'clear' }, { type: 'write_text', id: 'b', x: 10, y: 40, text: '（合成）乙' }] },
  ] });
  assert.deepEqual([...originsAt(cleared, 2)], [['b', 1]]);
  assert.deepEqual([...originsAt(cleared, 1)], [['a', 0]]);
});

test('askableAt：此刻能指的锚点', () => {
  assert.deepEqual(askableAt(DSL, 0), []);
  assert.deepEqual(askableAt(DSL, 2).map((a) => `${a.kind}:${a.id}`), ['element:t1', 'element:f1', 'element:l1', 'step:s1', 'step:s2']);
  assert.deepEqual(askableAt(NAR, 1).map((a) => `${a.kind}:${a.id}`), ['slot:fig', 'step:s1']);
  for (let k = 1; k <= 5; k++) for (const a of askableAt(DSL, k)) assert.deepEqual(codes(DSL, q(a, k)), [], `${k} ${a.id}`);
});

test('再讲一遍：跳回元素写上去的那一步的开头；整一步跳回那一步；槽位重放当前这一步', () => {
  const at = (anchor: StageAnchor, cursor: number) => {
    const r = validateStageQuestion(NAR.steps.length && anchor.kind === 'slot' ? NAR : DSL, q(anchor, cursor, 'again'));
    assert.ok(r.ok);
    return replayCursor(anchor.kind === 'slot' ? NAR : DSL, r.question);
  };
  assert.equal(at({ kind: 'element', id: 'f1' }, 4), 1);   // 第 2 步写的 → seek 到 1，再 play 就执行第 2 步
  assert.equal(at({ kind: 'element', id: 't1' }, 5), 0);
  assert.equal(at({ kind: 'element', id: 't2' }, 5), 4);
  assert.equal(at({ kind: 'step', id: 's3' }, 5), 2);
  assert.equal(at({ kind: 'slot', id: 'fig' }, 3), 2);
});

// ---- 白板可提问模式 ----

const label = (a: StageAnchor, step: number | null, index: number) => `（合成标签）${a.kind}|${step ?? '-'}|${index}`;
const render = (dsl: ValidatedStageDsl, cursor: number, askable?: AskableProps, layout: 'desktop' | 'mobile' = 'desktop') =>
  renderToStaticMarkup(createElement(Whiteboard, { dsl, cursor, layout, ...(askable ? { askable } : {}) }));
const ask = (selected: StageAnchor | null = null, onSelect = (_: StageAnchor) => {}): AskableProps => ({ selected, onSelect, label });

test('不开可提问模式：没有点击区、没有选中框（与快照同一输出，见 render.test.ts）', () => {
  for (let k = 0; k <= 5; k++) {
    const html = render(DSL, k);
    assert.ok(!/data-ask|role="button"|tabindex/.test(html), `cursor ${k}`);
  }
});

test('开启时：每个白板元素（高亮框除外）与槽位一个点击区，可聚焦、有读屏标签，不念 DSL 文字', () => {
  const html = render(DSL, 3, ask());
  const hits = [...html.matchAll(/data-ask="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(hits, ['element:t1', 'element:f1', 'element:l1', 'element:f2']);
  assert.equal((html.match(/role="button"/g) ?? []).length, 4);
  assert.equal((html.match(/tabindex="0"/g) ?? []).length, 4);
  assert.match(html, /aria-label="（合成标签）element\|2\|2"/);           // f1：第 2 步写的、白板第 2 项
  const labels = [...html.matchAll(/aria-label="([^"]+)"/g)].map((m) => m[1]!);
  for (const l of labels.filter((x) => x.startsWith('（合成标签）'))) assert.ok(!/解方程|2x/.test(l), l);
  assert.ok(!html.includes('data-ask-selected'));
  assert.equal(render(DSL, 0, ask()).includes('data-ask'), false, '空白板没有可指的东西');
  const nar = render(NAR, 1, ask());
  assert.deepEqual([...nar.matchAll(/data-ask="([^"]+)"/g)].map((m) => m[1]), ['slot:fig']);
});

test('选中：虚线细框（与老师的实线粗框、小三角只用形状与线宽区分），aria-pressed 跟随', () => {
  const html = render(NAR, 3, ask({ kind: 'element', id: 'f1' }));
  const sel = html.match(/<rect[^>]*data-ask-selected="element:f1"[^>]*>/)?.[0] ?? '';
  assert.ok(sel, html);
  assert.match(sel, /stroke-dasharray="4 4"/);
  assert.match(sel, /stroke-width="2"/);
  assert.match(sel, /fill="none"/);
  assert.match(sel, /aria-hidden="true"/);
  assert.match(html, /data-point="laser"/);                    // 老师的激光笔照常
  assert.match(html, /data-ask="element:f1"[^>]*aria-pressed="true"|aria-pressed="true"[^>]*data-ask="element:f1"/);
  assert.equal((html.match(/aria-pressed="true"/g) ?? []).length, 1);
  // 选中的东西此刻不在白板上（如已擦掉）：不画选中框
  assert.ok(!render(DSL, 5, ask({ kind: 'element', id: 'f2' })).includes('data-ask-selected'));
});

test('点击区不小于最小边长（舞台单位），且在画布内', () => {
  for (const [dsl, cursor, layout] of [[DSL, 5, 'desktop'], [DSL, 5, 'mobile'], [NAR, 4, 'mobile']] as const) {
    const wb = STAGE_LAYOUTS[layout].whiteboard;
    const scale = Math.min(wb.w / dsl.canvas.width, wb.h / dsl.canvas.height);
    const html = render(dsl, cursor, ask(), layout);
    const rects = [...html.matchAll(/<rect[^>]*data-ask="[^"]+"[^>]*>/g)].map((m) => m[0]);
    assert.ok(rects.length > 0);
    for (const r of rects) {
      const n = (k: string) => Number(r.match(new RegExp(` ${k}="([0-9.]+)"`))![1]);
      assert.ok(n('width') * scale >= ASK_MIN_STAGE_UNITS - 0.01 && n('height') * scale >= ASK_MIN_STAGE_UNITS - 0.01, `${layout} ${r}`);
      assert.ok(n('x') >= 0 && n('y') >= 0 && n('x') + n('width') <= dsl.canvas.width + 0.01 && n('y') + n('height') <= dsl.canvas.height + 0.01, r);
    }
  }
});

test('点击与回车、空格都选中同一个锚点；其他键不选', () => {
  const got: StageAnchor[] = [];
  const tree = (Whiteboard({ dsl: DSL, cursor: 2, askable: ask(null, (a) => got.push(a)) }) as unknown) as { props: { children: unknown[] } };
  // 在元素树里找点击区（不依赖 DOM）
  const found: { props: Record<string, any> }[] = [];
  const walk = (n: any): void => {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (n.props?.['data-ask']) found.push(n);
    walk(n.props?.children);
  };
  walk(tree);
  assert.deepEqual(found.map((n) => n.props['data-ask']), ['element:t1', 'element:f1', 'element:l1']);
  const f1 = found[1]!.props;
  f1.onClick();
  let prevented = 0;
  for (const key of ['Enter', ' ', 'a', 'Escape', 'Tab']) f1.onKeyDown({ key, preventDefault: () => { prevented++; } });
  assert.deepEqual(got.map((a) => `${a.kind}:${a.id}`), ['element:f1', 'element:f1', 'element:f1']);
  assert.equal(prevented, 2);
});
