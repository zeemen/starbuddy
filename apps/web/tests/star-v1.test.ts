// H5 星图 v1 前端（specs/star-map-v1-h5/spec.md 分 PR 第 3 条）的单测与静态检查（不需要浏览器）：
//   - 开关 starmap_v1 / starmap_dashed：默认全关；只在 dev / test 可开；只开虚线不开 v1 一律关；
//   - 显示整理（v1-view.ts）：英语与不认识的学科不显示；虚线开关关时待验证星按中性底星；没有 claim_id 的星按中性；
//     陈述没有 claim_id 或文字不显示；一页超过 120 颗星按领域折叠（120 颗不折叠）；
//   - 文案在统一文案文件里，并进入全量文案表（禁语、禁用推断词表、比较词表由 tests/shell.test.ts 逐条扫描）；
//   - 静态：v1 组件不写中文、不发请求（能力视图侧栏没有数据请求）；关于学生的文字（陈述、星的状态词、下一次验证）只渲染在
//     带 data-claim-id 的元素里；v1 只在开关打开时被调用，v0 的数据源与渲染组件不变。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import type { StarMapV1, StarV1, StatementV1, SubjectV1 } from '../src/lib/star-map-v1.ts';
import { fetchStarMapV1, starMapV1Path } from '../src/lib/star-map-v1.ts';
import { ALL_SHELL_COPY, STAR_DISPLAY_V1, STAR_MAP_V1 } from '../src/shell/copy.ts';
import { STAR_MAP_FEATURES, starMapEnabled, starMapFeatures } from '../src/star/features.ts';
import { FOLD_THRESHOLD, viewMap, viewStar } from '../src/star/v1-view.ts';

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const C = '5e000000-0000-4000-8000-0000000000f1';

const neutral = (i: number): StarV1 => ({ node_id: `MATH.SYN.T1.N${String(i).padStart(3, '0')}`, name: `（合成）${i}`, display: 'neutral' });
function mapWith(counts: Record<string, number>, extra: StarV1[] = []): StarMapV1 {
  return {
    student_id: '5e000000-0000-4000-8000-0000000000c1', generated_at: '2026-10-02T00:00:00Z', skill_view: { status: 'coming_soon' },
    skies: Object.entries(counts).map(([subject, n]) => ({
      subject: subject as SubjectV1, status: 'ok' as const, map_version: '0.9.0',  // 夹具故意含接口外的编码（英语、aya），核对运行时过滤
      domains: [{ domain_id: 'MATH.SYN', name: '（合成）领域', topics: [{ topic_id: 'MATH.SYN.T1', name: null,
        stars: [...Array.from({ length: n }, (_, i) => neutral(i)), ...(subject === 'math' ? extra : [])] }] }],
    })),
  };
}

test('开关：默认全关；只在 dev / test 可开；只开虚线不开 v1 时一律关；staging、pilot、生产一律关', () => {
  assert.deepEqual([...starMapFeatures({})], []);
  assert.deepEqual([...starMapFeatures({ STARBUDDY_ENV: 'test' })], []);
  assert.deepEqual([...starMapFeatures({ STARBUDDY_ENV: 'test', STARBUDDY_STARMAP: 'starmap_v1' })], ['starmap_v1']);
  assert.deepEqual([...starMapFeatures({ STARBUDDY_ENV: 'dev', STARBUDDY_STARMAP: ' starmap_v1, starmap_dashed ,bogus' })], ['starmap_v1', 'starmap_dashed']);
  assert.deepEqual([...starMapFeatures({ STARBUDDY_ENV: 'test', STARBUDDY_STARMAP: 'starmap_dashed' })], []);
  for (const env of ['', 'staging', 'pilot', 'production']) {
    assert.deepEqual([...starMapFeatures({ STARBUDDY_ENV: env, STARBUDDY_STARMAP: STAR_MAP_FEATURES.join(',') })], [], env);
  }
  assert.equal(starMapEnabled('starmap_v1', {}), false);
  assert.equal(starMapV1Path('x'), '/bff/v0/students/x/star-map/v1');
});

test('星：画了的星必须带 claim_id；虚线只在开关开时画；不认识的 display 按中性；“保持”只跟亮星', () => {
  const base = { node_id: 'MATH.SYN.T1.A', name: 'n' };
  assert.deepEqual(viewStar({ ...base, display: 'lit', retained: true, claim_id: C }, false),
                   { kind: 'painted', ...base, display: 'lit', retained: true, claim_id: C });
  assert.equal(viewStar({ ...base, display: 'half', retained: true, claim_id: C }, false).kind, 'painted');
  assert.equal((viewStar({ ...base, display: 'half', retained: true, claim_id: C }, false) as { retained: boolean }).retained, false);
  assert.deepEqual(viewStar({ ...base, display: 'lit', retained: false, claim_id: '' }, false), { kind: 'neutral', ...base });
  assert.deepEqual(viewStar({ ...base, display: 'lit', retained: false } as unknown as StarV1, true), { kind: 'neutral', ...base });
  const dashed: StarV1 = { ...base, display: 'dashed', claim_id: C, next_check: { due_on: '2026-11-03' } };
  assert.deepEqual(viewStar(dashed, false), { kind: 'neutral', ...base });
  assert.deepEqual(viewStar(dashed, true), { kind: 'dashed', ...base, claim_id: C, due_on: '2026-11-03' });
  assert.deepEqual(viewStar({ ...dashed, next_check: null }, true), { kind: 'dashed', ...base, claim_id: C, due_on: null });
  assert.deepEqual(viewStar({ ...base, display: 'bright' } as unknown as StarV1, true), { kind: 'neutral', ...base });
  // 中性星不带任何星态、判断 ID
  assert.deepEqual(Object.keys(viewStar({ ...base, display: 'neutral' }, true)).sort(), ['kind', 'name', 'node_id']);
});

test('学科：只显示数学、语文、道法（固定顺序），英语与不认识的编码不显示；地图建设中照样显示', () => {
  const m = mapWith({ politics: 1, english: 3, math: 2, aya: 4 });
  m.skies.push({ subject: 'chinese' as SubjectV1, status: 'building' });
  const v = viewMap(m, { dashed: false });
  assert.deepEqual(v.skies.map((s) => [s.subject, s.status]), [['math', 'ok'], ['chinese', 'building'], ['politics', 'ok']]);
  assert.equal(v.count, 3);
});

test(`折叠：一页超过 ${FOLD_THRESHOLD} 颗星时按领域折叠，正好 ${FOLD_THRESHOLD} 颗不折叠；计数跨学科相加，不计地图建设中`, () => {
  assert.equal(FOLD_THRESHOLD, 120);
  assert.equal(viewMap(mapWith({ math: 120 }), { dashed: false }).folded, false);
  assert.equal(viewMap(mapWith({ math: 121 }), { dashed: false }).folded, true);
  assert.equal(viewMap(mapWith({ math: 119, chinese: 2 }), { dashed: false }).folded, true);
  assert.equal(viewMap(mapWith({ math: 119, english: 50 }), { dashed: false }).folded, false);
  const v = viewMap(mapWith({ math: 3 }), { dashed: false });
  assert.equal(v.skies[0]!.status === 'ok' && v.skies[0]!.domains[0]!.count, 3);
});

test('陈述：只在 BFF 给了 claim_id 与文字时显示；置信度说明随陈述原样带上', () => {
  const m = mapWith({ math: 1 });
  const sky = m.skies[0] as Extract<StarMapV1['skies'][number], { status: 'ok' }>;
  sky.summary = { claim_id: C, text: '（合成）陈述', confidence_band: 'moderate' };
  sky.transfer_summary = { claim_id: '', text: '（合成）没有判断 ID', confidence_band: 'strong' };
  let v = viewMap(m, { dashed: false }).skies[0]!;
  assert.ok(v.status === 'ok');
  assert.deepEqual(v.summary, { claim_id: C, text: '（合成）陈述', confidence_band: 'moderate' });
  assert.equal(v.transfer, null);
  sky.summary = { claim_id: C, text: '  ' } as StatementV1;
  v = viewMap(m, { dashed: false }).skies[0]!;
  assert.ok(v.status === 'ok' && v.summary === null);
});

test('文案：v1 的界面文案在统一文案文件里并进入全量文案表；状态词不进全量文案表（只能出现在 data-claim-id 内）', () => {
  assert.equal(STAR_MAP_V1.neutral, '暂无可展示的结论');
  assert.equal(STAR_MAP_V1.building, '地图建设中');
  assert.equal(STAR_MAP_V1.skillViewComing, '能力视图即将开放');
  assert.equal(STAR_MAP_V1.watermark, '原型 · 合成数据');  // spec #6：v1 仍保留水印
  for (const s of Object.values(STAR_MAP_V1)) assert.ok(ALL_SHELL_COPY.includes(s), s);
  // 状态词是关于学生的陈述的一部分：不算壳文案（端到端“文本节点要么是文案、要么在 data-claim-id 里”的核对因此会抓到放错地方的状态词）
  for (const s of Object.values(STAR_DISPLAY_V1)) assert.ok(!ALL_SHELL_COPY.includes(s), s);
  for (const s of Object.values(STAR_MAP_V1)) assert.ok(!/亮|暗|掌握|会了|不会/.test(s), `系统文案不含状态词：${s}`);
});

test('静态：v1 组件不写中文、不发请求；能力视图侧栏没有数据来源', () => {
  for (const p of ['src/star/StarMapV1.tsx', 'src/star/v1-view.ts', 'src/star/features.ts']) {
    assert.deepEqual(code(read(p)).match(/[一-鿿]+/g) ?? [], [], p);
    assert.ok(!/\bfetch\(|lib\/(bff|shell-api|trust-api|star-map)\.ts|XMLHttpRequest|EventSource|WebSocket/.test(code(read(p))), p);
  }
  const v1 = code(read('src/star/StarMapV1.tsx'));
  const aside = v1.slice(v1.indexOf('export function SkillViewAside'), v1.indexOf('export function StarMapV1View'));
  assert.match(aside, /STAR_MAP_V1\.skillViewComing/);
  assert.match(aside, /export function SkillViewAside\(\) \{/, '侧栏不接收任何数据');
});

test('静态：关于学生的文字只渲染在带 data-claim-id 的元素里（陈述、状态词、下一次验证）', () => {
  const v1 = code(read('src/star/StarMapV1.tsx'));
  // 陈述文字：只有一处，且就在 data-claim-id 元素里
  assert.deepEqual([...v1.matchAll(/\{s\.text\}/g)].length, 1);
  assert.match(v1, /<span data-claim-id=\{s\.claim_id\}>\{s\.text\}<\/span>/);
  // 状态词：只在画了的星的星位里（星位 <li> 带 data-claim-id）
  const painted = v1.slice(v1.indexOf("star.kind === 'painted'"), v1.indexOf("star.kind === 'dashed'"));
  assert.match(painted, /STAR_DISPLAY_V1/);
  assert.match(painted, /data-claim-id=\{star\.claim_id\}>/);
  assert.equal([...v1.matchAll(/STAR_DISPLAY_V1\./g)].length + [...v1.matchAll(/STAR_DISPLAY_V1\[/g)].length, 2);
  assert.ok(!v1.slice(v1.indexOf("star.kind === 'dashed'")).includes('STAR_DISPLAY_V1'), '虚线与中性星不带状态词');
  // 下一次验证：只在虚线星的星位与带 data-claim-id 的日期行里
  const uses = [...v1.matchAll(/<NextCheck /g)].map((m) => v1.slice(Math.max(0, m.index! - 400), m.index));
  assert.equal(uses.length, 2);
  for (const before of uses) assert.match(before, /data-claim-id=\{s(tar)?\.claim_id\}/);
  // 中性底星：没有 data-claim-id，读屏读 neutral
  const neutralSlot = v1.slice(v1.lastIndexOf('<li', v1.lastIndexOf('data-display="neutral"')), v1.indexOf('function NextCheck'));
  assert.ok(!neutralSlot.includes('data-claim-id'));
  assert.match(neutralSlot, /STAR_MAP_V1\.neutral/);
});

test('正式显示规则（services/bff/config/shell.json）不含合成模板（syn.*）；合成种子的模板注明“合成占位，永不进正式白名单”', () => {
  const cfg = JSON.parse(read('../../services/bff/config/shell.json')) as {
    external_claim_templates: string[]; rationale_templates: Record<string, string[]>;
    star_map: { mastery_templates: string[]; transfer_templates: string[] };
  };
  const all = [...cfg.external_claim_templates, ...Object.values(cfg.rationale_templates).flat(),
               ...cfg.star_map.mastery_templates, ...cfg.star_map.transfer_templates];
  assert.deepEqual(all.filter((t) => /^syn\./.test(t)), []);
  assert.ok(!/"syn\./.test(read('../../services/bff/config/shell.json')));
  const seed = read('../../db/seeds/dev/star-map-v1-synthetic.sql');
  assert.match(seed, /syn\.starmap\.node_mastery/);
  assert.match(seed, /合成占位，永不进正式白名单/);
});

test('前端开、BFF 关：v1 接口 404 → 页面按“没有找到”处理（与未授权、不存在同一个结果）；401 → 去登录', async () => {
  const real = globalThis.fetch;
  const seen: string[] = [];
  try {
    for (const [status, kind] of [[404, 'not-found'], [401, 'unauthenticated']] as const) {
      globalThis.fetch = (async (u: string | URL) => { seen.push(String(u)); return new Response('{}', { status }); }) as typeof fetch;
      assert.deepEqual(await fetchStarMapV1('5e000000-0000-4000-8000-0000000000c1', { token: 't', csrf: '' }), { kind });
    }
    globalThis.fetch = (async () => new Response('{}', { status: 500 })) as typeof fetch;
    await assert.rejects(fetchStarMapV1('5e000000-0000-4000-8000-0000000000c1', { token: 't', csrf: '' }));
  } finally {
    globalThis.fetch = real;
  }
  assert.ok(seen.every((u) => u.endsWith('/bff/v0/students/5e000000-0000-4000-8000-0000000000c1/star-map/v1')));
  assert.deepEqual(await fetchStarMapV1('not-a-uuid', { token: 't', csrf: '' }), { kind: 'not-found' });
  // 页面：not-found → notFound()（Next 的“没有找到”页），unauthenticated → 去登录
  const page = code(read('src/star/StarMapPage.tsx'));
  assert.match(page, /if \(v1\.kind === 'not-found'\) notFound\(\);/);
  assert.match(page, /if \(v1\.kind === 'unauthenticated'\) redirect\('\/login'\);/);
});

test('静态：v1 只在开关打开时被调用；v0 的数据源与渲染组件不变', () => {
  const page = code(read('src/star/StarMapPage.tsx'));
  const gate = page.indexOf("if (flags.has('starmap_v1')) {");
  assert.ok(gate > 0);
  const v0 = page.indexOf('const result = await fetchStarMap(studentId, ps.session);');
  assert.ok(v0 > gate);
  const branch = page.slice(gate, v0);
  assert.match(branch, /fetchStarMapV1\(studentId, ps\.session\)/);
  assert.match(branch, /return \(/);
  assert.equal([...page.matchAll(/fetchStarMapV1\(/g)].length, 1);
  assert.match(branch, /dashed: flags\.has\('starmap_dashed'\)/);
  // v0 组件不认识 v1 的类型：StarSky 只画 v0 的三态
  assert.ok(!/neutral|dashed|star-map-v1/.test(code(read('src/star/StarSky.tsx'))));
});
