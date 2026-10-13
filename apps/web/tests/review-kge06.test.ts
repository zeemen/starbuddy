// KGE-06 封测周修订支持（specs/kge06-weekly-revision）的审核台前端单测：开关默认关、只在 dev / test 能开；页面在读会话之前判断开关；
// 工作单的查询串与游标校验；结果提示齐全；功能名与后端一致。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { REVIEW_FEATURES, reviewFeatureEnabled, reviewFeatures } from '../src/lib/review-features.ts';
import { BUMP_LABEL, LAYER_LABEL, ROLE_LABEL, TAG_RESULT_LABEL, isConfidence, isTagDecision, worklistQuery } from '../src/lib/review.ts';

const src = (p: string) => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');

test('开关：缺省关；只在 dev / test 能开（别的环境设了即报错）；不认识的名字报错（与后端一致）', () => {
  assert.equal(reviewFeatures({}).size, 0);
  assert.equal(reviewFeatures({ STARBUDDY_ENV: 'test' }).size, 0);
  assert.equal(reviewFeatures({ STARBUDDY_ENV: 'production', STARBUDDY_REVIEW_FEATURES: ' , ' }).size, 0);  // 空 = 全关
  for (const env of ['staging', 'pilot', 'production', '']) {
    assert.throws(() => reviewFeatures({ STARBUDDY_ENV: env, STARBUDDY_REVIEW_FEATURES: 'tag_worklist' }), /dev \/ test/, env);
  }
  assert.throws(() => reviewFeatures({ STARBUDDY_REVIEW_FEATURES: 'tag_worklist' }));  // 没有环境 = 正式环境
  for (const bad of ['tag-worklist', 'tag_worklist,everything', 'TAG_WORKLIST']) {
    assert.throws(() => reviewFeatures({ STARBUDDY_ENV: 'dev', STARBUDDY_REVIEW_FEATURES: bad }), /不认识/, bad);
  }
  const on = reviewFeatures({ STARBUDDY_ENV: 'dev', STARBUDDY_REVIEW_FEATURES: ' tag_worklist ,revision_preflight' });
  assert.deepEqual([...on].sort(), ['revision_preflight', 'tag_worklist']);
  assert.ok(reviewFeatureEnabled('revision_preflight', { STARBUDDY_ENV: 'test', STARBUDDY_REVIEW_FEATURES: 'revision_preflight' }));
  assert.ok(!reviewFeatureEnabled('tag_worklist', { STARBUDDY_ENV: 'test', STARBUDDY_REVIEW_FEATURES: 'revision_preflight' }));
});

test('功能名与后端 REVIEW_CONSOLE_FEATURES 一致', () => {
  const py = readFileSync(new URL('../../../services/learning/review_console/config.py', import.meta.url), 'utf8');
  const m = py.match(/^FEATURES = \(([^)]*)\)/m);
  assert.ok(m);
  const backend = [...m[1]!.matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
  assert.deepEqual(backend, [...REVIEW_FEATURES]);
});

test('两个页面与 server action 在读会话、调后端之前判断开关；导航入口随开关', () => {
  for (const [p, f] of [['app/review/tag-candidates/page.tsx', 'tag_worklist'], ['app/review/map-versions/[id]/preflight/page.tsx', 'revision_preflight']] as const) {
    const s = src(p);
    const gate = s.indexOf(`if (!reviewFeatureEnabled('${f}')) notFound();`);
    assert.ok(gate > 0, p);
    assert.ok(gate < s.indexOf('await currentSession()'), p);
  }
  const actions = src('app/review/actions.ts');
  const body = actions.slice(actions.indexOf('export async function decideTagAction'));
  assert.ok(body.indexOf("reviewFeatureEnabled('tag_worklist')") < body.indexOf('await staff()'));
  assert.match(src('app/review/layout.tsx'), /tagWorklist \? <a href="\/review\/tag-candidates">/);
  assert.match(src('app/review/map-versions/[id]/page.tsx'), /isDraft && reviewFeatureEnabled\('revision_preflight'\)/);
});

test('工作单：查询串只拼校验过的值；游标两项齐了才带；置信度与处理方式校验', () => {
  assert.equal(worklistQuery({ subject: 'aya' }), 'subject=aya&limit=50');
  assert.equal(worklistQuery({ subject: 'zhixing', after_confidence: '0.200' }), 'subject=zhixing&limit=50');
  assert.equal(worklistQuery({ subject: 'aya', map_version_id: '6b060000-0000-4000-8000-0000000001c1', after_confidence: '0.2', after_id: '6b060000-0000-4000-8000-000000000301' }),
    'subject=aya&limit=50&map_version_id=6b060000-0000-4000-8000-0000000001c1&after_confidence=0.2&after_id=6b060000-0000-4000-8000-000000000301');
  for (const ok of ['0', '0.2', '0.200', '1', '1.000']) assert.ok(isConfidence(ok), ok);
  for (const bad of ['1.5', '-0.1', '0.2000', 'NaN', '', '.5', '0.2&x=1']) assert.ok(!isConfidence(bad), bad);
  assert.ok(isTagDecision('accept') && isTagDecision('reject') && isTagDecision('relabel') && !isTagDecision('approve'));
});

test('结果提示与标签齐全；ActionForm 把 written、recorded 当成功', () => {
  for (const code of ['written', 'recorded', 'RULE_VIOLATION', 'FORBIDDEN', 'NOT_FOUND', 'INVALID_PARAMETER', 'UNAUTHENTICATED', 'INTERNAL']) {
    assert.ok(Object.hasOwn(TAG_RESULT_LABEL, code), code);
  }
  assert.deepEqual(Object.keys(ROLE_LABEL).sort(), ['main', 'secondary']);
  assert.deepEqual(Object.keys(LAYER_LABEL).sort(), ['model', 'rule', 'vector']);
  assert.deepEqual(Object.keys(BUMP_LABEL).sort(), ['initial', 'minor', 'patch']);
  const form = src('app/review/ActionForm.tsx');
  for (const code of ['written', 'recorded']) assert.ok(form.includes(`'${code}'`), code);
});

test('预检页：库判为结构性而逐条理由为空时提示“以库判定为准”', () => {
  const s = src('app/review/map-versions/[id]/preflight/page.tsx');
  assert.match(s, /p\.structural_reasons\.length === 0 \? \(\s*p\.structural \? \(/);
  assert.ok(s.includes('以库判定为准') && s.includes('preflight-reasons-db-only'));
});

test('工作单页面不显示任何学生数据字段（只有公共层内容）', () => {
  const s = src('app/review/tag-candidates/page.tsx');
  for (const bad of ['student', 'answer', 'response_text']) assert.ok(!s.includes(bad), bad);
});
