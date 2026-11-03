// H5 星图 v1 的组件级渲染测试（不需要浏览器）：用 tests/tsx-loader.mjs 加载 .tsx，以 react-dom/server 渲染 StarMapV1View，
// 喂带星数说明与迁移摘要的合成夹具，核对：
//   - 置信度说明只在家长端出现（data-confidence），学生端没有（BFF 两端都下发 confidence_band）；
//   - 陈述文字、星的状态词（STAR_DISPLAY_V1）、下一次验证只出现在带 data-claim-id 的元素内；其余文本节点都是壳文案、
//     节点名（data-node-name）或数值（data-value）；
//   - 中性底星没有 data-claim-id，读屏文字是“暂无可展示的结论”。
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';
import type { StarMapV1 } from '../src/lib/star-map-v1.ts';
import { ALL_SHELL_COPY, CONFIDENCE_BAND, STAR_DISPLAY_V1, STAR_MAP_V1 } from '../src/shell/copy.ts';
import { viewMap } from '../src/star/v1-view.ts';

register('./tsx-loader.mjs', import.meta.url);
const { renderToStaticMarkup } = await import('react-dom/server');
const { createElement } = await import('react');
const { StarMapV1View } = await import('../src/star/StarMapV1.tsx');

const SUM = '5e000000-0000-4000-8000-0000000003a1';
const TRA = '5e000000-0000-4000-8000-0000000003a2';
const LIT = '5e000000-0000-4000-8000-0000000003a3';
const DAS = '5e000000-0000-4000-8000-0000000003a4';
const MAP: StarMapV1 = {
  student_id: '5e000000-0000-4000-8000-0000000000c1', generated_at: '2026-10-02T00:00:00Z', skill_view: { status: 'coming_soon' },
  skies: [
    { subject: 'math', status: 'ok', map_version: '0.9.0',
      summary: { claim_id: SUM, text: '（合成陈述）星数说明', confidence_band: 'moderate' },
      transfer_summary: { claim_id: TRA, text: '（合成陈述）迁移摘要', confidence_band: 'few_evidence' },
      domains: [{ domain_id: 'MATH.SYN', name: '（合成）领域', topics: [{ topic_id: 'MATH.SYN.T1', name: '（合成）主题', stars: [
        { node_id: 'MATH.SYN.T1.A', name: '（合成）甲', display: 'lit', retained: true, claim_id: LIT },
        { node_id: 'MATH.SYN.T1.B', name: '（合成）乙', display: 'neutral' },
        { node_id: 'MATH.SYN.T1.C', name: '（合成）丙', display: 'dashed', claim_id: DAS, next_check: { due_on: '2026-11-03' } },
      ] }] }] },
    { subject: 'politics', status: 'building' },
  ],
};

const render = (audience: 'student' | 'guardian') => renderToStaticMarkup(
  createElement(StarMapV1View, { view: viewMap(MAP, { dashed: true }), audience, studentId: MAP.student_id }));

const VOID = new Set(['br', 'hr', 'img', 'input', 'meta', 'link']);
/** 极简的标记遍历：每个文本节点 + 它所在元素链上是否有 data-claim-id / data-value / data-node-name */
function textNodes(html: string): { text: string; claim: boolean; value: boolean; name: boolean }[] {
  const stack: { claim: boolean; value: boolean; name: boolean }[] = [];
  const out: { text: string; claim: boolean; value: boolean; name: boolean }[] = [];
  const re = /<\/?([a-zA-Z][\w:-]*)([^>]*?)(\/?)>|([^<]+)/g;
  for (let m = re.exec(html); m; m = re.exec(html)) {
    const top = stack.at(-1) ?? { claim: false, value: false, name: false };
    if (m[4] !== undefined) {
      const t = m[4].replace(/&[a-z#0-9]+;/g, ' ').trim();
      if (t) out.push({ text: t, ...top });
    } else if (m[0].startsWith('</')) {
      stack.pop();
    } else if (m[3] !== '/' && !VOID.has(m[1]!)) {
      const a = m[2] ?? '';
      stack.push({ claim: top.claim || /\sdata-claim-id="/.test(a), value: top.value || /\sdata-value="/.test(a),
                   name: top.name || /\sdata-node-name="/.test(a) });
    }
  }
  return out;
}

test('置信度说明只在家长端显示；学生端没有 data-confidence', () => {
  const g = render('guardian');
  const s = render('student');
  assert.equal([...g.matchAll(/data-confidence="/g)].length, 2);
  assert.match(g, /data-confidence="moderate">/);
  assert.ok(g.includes(CONFIDENCE_BAND['moderate']!) && g.includes(CONFIDENCE_BAND['few_evidence']!));
  assert.ok(!s.includes('data-confidence'));
  for (const band of Object.values(CONFIDENCE_BAND)) assert.ok(!s.includes(band), band);
  // 两端的陈述都在
  for (const html of [g, s]) {
    assert.match(html, new RegExp(`<span data-claim-id="${SUM}">（合成陈述）星数说明</span>`));
    assert.match(html, new RegExp(`<span data-claim-id="${TRA}">（合成陈述）迁移摘要</span>`));
  }
});

test('关于学生的文字（陈述、状态词、下一次验证）只在 data-claim-id 内；其余文本是壳文案、节点名或数值', () => {
  const statusWords = new Set(Object.values(STAR_DISPLAY_V1));
  for (const audience of ['student', 'guardian'] as const) {
    const nodes = textNodes(render(audience));
    const outside = nodes.filter((n) => !n.claim);
    // 陈述、状态词、下一次验证都不在 data-claim-id 之外
    for (const n of outside) {
      for (const t of n.text.split(STAR_MAP_V1.sep)) assert.ok(!statusWords.has(t.trim()), `${audience}：状态词出现在 data-claim-id 之外：${n.text}`);
      assert.ok(!n.text.includes('（合成陈述）'), `${audience}：陈述出现在 data-claim-id 之外`);
      assert.ok(!n.text.includes(STAR_MAP_V1.nextCheck), `${audience}：下一次验证出现在 data-claim-id 之外`);
    }
    // 其余文本：壳文案、节点名或数值
    const copy = new Set(ALL_SHELL_COPY);
    // （静态渲染会把相邻的文本合并成一个节点，例如分隔符“：”与“暂无可展示的结论”：按分隔符拆开再核对）
    const parts = outside.filter((n) => !n.value && !n.name && !copy.has(n.text))
      .flatMap((n) => n.text.split(STAR_MAP_V1.sep).map((t) => t.trim()).filter(Boolean));
    assert.deepEqual(parts.filter((t) => !copy.has(t)), [], audience);
    // 状态词确实渲染了（在 data-claim-id 内）
    assert.ok(nodes.some((n) => n.claim && n.text.split(STAR_MAP_V1.sep).map((t) => t.trim()).includes(STAR_DISPLAY_V1.retained)));
  }
});

test('星位：画了的星与虚线带 data-claim-id；中性底星没有，读屏读“暂无可展示的结论”；地图建设中与侧栏占位', () => {
  const html = render('student');
  assert.match(html, new RegExp(`data-node-id="MATH.SYN.T1.A" data-display="lit" data-retained="true" data-claim-id="${LIT}"`));
  assert.match(html, new RegExp(`data-node-id="MATH.SYN.T1.C" data-display="dashed" data-claim-id="${DAS}"`));
  assert.match(html, /<li class="star-slot" data-node-id="MATH.SYN.T1.B" data-display="neutral">/);
  assert.match(html, new RegExp(`（合成）乙</span>${STAR_MAP_V1.sep}${STAR_MAP_V1.neutral}</span>`));
  assert.match(html, /data-value="date">2026-11-03</);
  assert.match(html, new RegExp(`data-testid="building-politics">${STAR_MAP_V1.building}<`));
  assert.ok(html.includes(STAR_MAP_V1.skillViewComing));
  // 虚线开关关：同一颗星按中性画，不带 claim_id
  const off = renderToStaticMarkup(createElement(StarMapV1View, { view: viewMap(MAP, { dashed: false }), audience: 'student', studentId: MAP.student_id }));
  assert.match(off, /data-node-id="MATH.SYN.T1.C" data-display="neutral">/);
  assert.ok(!off.includes(DAS));
});
