// SVG 白板的服务端渲染（react-dom/server，不需要浏览器）：快照、无外链、讲解权 1.25 线、只渲染校验过的 DSL、文字被转义、
// 源码不碰任何存储与网络（不持久化）。快照在 tests/snapshots/，改渲染时连同快照一起评审（失败信息里有实际输出）。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { register } from 'node:module';
import { test } from 'node:test';
import { lineUnit, STAGE_LAYOUTS, validateStageDsl, type LayoutName, type ValidatedStageDsl } from '../src/index.ts';
import { sample } from './fixtures.ts';

register('./tsx-loader.mjs', import.meta.url);
const { renderToStaticMarkup } = await import('react-dom/server');
const { createElement } = await import('react');
const { createWhiteboard } = await import('../src/Whiteboard.tsx');
const Whiteboard = createWhiteboard(createElement);

const r = validateStageDsl(sample());
assert.ok(r.ok);
const DSL = r.dsl;
const render = (cursor: number, layout: LayoutName = 'desktop', dsl: ValidatedStageDsl = DSL) =>
  renderToStaticMarkup(createElement(Whiteboard, { dsl, cursor, layout }));

/** 渲染结果里不得出现的东西：链接、外部资源、脚本、内嵌 HTML、内联样式 */
const FORBIDDEN_MARKUP = /https?:|\/\/|href|<a[\s>]|<script|<image|<img|<iframe|<foreignObject|<use[\s>]|<style|\sstyle=|url\(|xlink|javascript:|\son[a-z]+=/i;

const SNAPSHOTS: [name: string, cursor: number, layout: LayoutName][] = [
  ['desktop-5', 5, 'desktop'], ['tablet-3', 3, 'tablet'], ['mobile-2', 2, 'mobile'], ['desktop-0', 0, 'desktop'],
];

test('SSR 快照', () => {
  for (const [name, cursor, layout] of SNAPSHOTS) {
    const expected = readFileSync(new URL(`./snapshots/${name}.svg`, import.meta.url), 'utf8').trimEnd();
    const actual = render(cursor, layout);
    assert.equal(actual, expected, `快照 ${name} 不一致；实际输出：\n${actual}`);
  }
  // 快照目录里没有多余的旧快照
  assert.deepEqual(readdirSync(new URL('./snapshots/', import.meta.url)).sort(), SNAPSHOTS.map(([n]) => `${n}.svg`).sort());
});

test('渲染结果没有外链、脚本、内联样式；文字被转义', () => {
  for (const layout of ['desktop', 'tablet', 'mobile'] as const) {
    for (let k = 0; k <= DSL.steps.length; k++) {
      const html = render(k, layout);
      assert.ok(!FORBIDDEN_MARKUP.test(html), `${layout} 第 ${k} 步：${html.match(FORBIDDEN_MARKUP)?.[0]}`);
      assert.equal(render(k, layout), html, '同一输入同一输出');
    }
  }
  const end = render(5);
  assert.ok(end.includes('（合成）x &lt; 3 &amp; y &gt; 1'));
});

test('只渲染校验器产出的 DSL', () => {
  const copy = JSON.parse(JSON.stringify(DSL)) as ValidatedStageDsl;
  assert.equal(render(5, 'desktop', copy), '');
  assert.equal(render(5, 'desktop', { ...DSL } as ValidatedStageDsl), '');
});

test('白板占舞台高度 ≥ 65%；讲话者主轮廓 1.25 线，其余 1 线；点点只在出场时有占位框', () => {
  for (const layout of ['desktop', 'tablet', 'mobile'] as const) {
    const L = STAGE_LAYOUTS[layout];
    const html = render(1, layout);
    assert.match(html, new RegExp(`viewBox="0 0 ${L.stage.width} ${L.stage.height}"`));
    const wb = html.match(/data-stage-part="whiteboard"[^>]*height="([\d.]+)"/);
    assert.ok(wb && Number(wb[1]) / L.stage.height >= 0.65, layout);
    const width = (role: string, h: string) => Number(h.match(new RegExp(`data-anchor="${role}" data-speaking="(?:true|false)"><rect[^>]*data-outline-width="([\\d.]+)"`))![1]);
    // 第 1 步老师讲，第 4 步点点讲，第 5 步无人讲
    assert.match(html, /data-anchor="teacher" data-speaking="true"/);
    assert.ok(Math.abs(width('teacher', html) - 1.25 * lineUnit(L.teacher)) < 1e-3);
    assert.ok(Math.abs(width('buddy', html) - lineUnit(L.buddy)) < 1e-3);
    const h4 = render(4, layout);
    assert.ok(Math.abs(width('buddy', h4) - 1.25 * lineUnit(L.buddy)) < 1e-3);
    assert.ok(Math.abs(width('teacher', h4) - lineUnit(L.teacher)) < 1e-3);
    const h5 = render(5, layout);
    assert.ok(!h5.includes('data-speaking="true"'));
  }
  const only = validateStageDsl({ ...sample(), roles: ['teacher'], steps: [{ id: 's1', actions: [{ type: 'speaker', role: 'teacher' }] }] });
  assert.ok(only.ok);
  const html = render(1, 'desktop', only.dsl);
  assert.ok(html.includes('data-anchor="teacher"') && !html.includes('data-anchor="buddy"'));
  // 讲解权不靠颜色：讲与不讲的占位框只差线宽与字重
  assert.ok(!/fill="(?!none|currentColor)/.test(render(4)) && !/stroke="(?!currentColor)/.test(render(4)));
});

test('元素树是纯函数：同一 DSL 与步数得到同一棵树；未校验返回 null', async () => {
  const { renderWhiteboard } = await import('../src/Whiteboard.tsx');
  const h = (type: string, props: Record<string, unknown> | null, ...children: unknown[]) => ({ type, props, children });
  assert.deepEqual(renderWhiteboard(h, { dsl: DSL, cursor: 3 }), renderWhiteboard(h, { dsl: DSL, cursor: 3 }));
  assert.equal(renderWhiteboard(h, { dsl: { ...DSL } as ValidatedStageDsl, cursor: 3 }), null);
});

test('源码不碰存储与网络（不持久化）、不引外部资源', () => {
  const dir = new URL('../src/', import.meta.url);
  for (const f of readdirSync(dir)) {
    const src = readFileSync(new URL(f, dir), 'utf8');
    for (const bad of ['localStorage', 'sessionStorage', 'indexedDB', 'document.cookie', 'fetch(', 'XMLHttpRequest', 'WebSocket', 'sendBeacon',
      'EventSource', 'dangerouslySetInnerHTML', 'innerHTML', 'eval(', 'new Function', 'import(']) {
      assert.ok(!src.includes(bad), `${f} 含 ${bad}`);
    }
    for (const m of src.matchAll(/from '([^']+)'/g)) {
      assert.ok(m[1]!.startsWith('./'), `${f} 引用了 ${m[1]}（本包源码只允许相对路径；react 由调用方注入）`);
    }
  }
});
