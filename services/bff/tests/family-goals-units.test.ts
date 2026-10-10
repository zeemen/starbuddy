// 家长诉求的纯函数单测（PLT-14 第 1 步）：开关解析、请求体校验、补充说明规范化与禁用推断词表过滤。不连库。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FAMILY_GOALS_FEATURE, parseFamilyGoalsFeature } from '../src/family/features.ts';
import { HIDDEN_CHARS, normalizeNote, parseGoalInput } from '../src/family/goals.ts';
import { lexicon } from '../src/trust/lexicon.ts';

const goal = (over: Record<string, unknown> = {}) => ({ focus_subjects: [], target_scores: [], school_id: null, note: null, ...over });

test('开关：缺省关；只认 family_goals；只在 dev / test 可开，其余环境设了就拒绝启动', () => {
  assert.equal(parseFamilyGoalsFeature('dev', undefined), false);
  assert.equal(parseFamilyGoalsFeature('production', undefined), false);
  assert.equal(parseFamilyGoalsFeature('staging', ' '), false);
  assert.equal(parseFamilyGoalsFeature('test', FAMILY_GOALS_FEATURE), true);
  assert.equal(parseFamilyGoalsFeature('dev', ' family_goals '), true);
  assert.throws(() => parseFamilyGoalsFeature('dev', 'family_goals,monthly_plan'));
  for (const env of ['staging', 'pilot', 'production'] as const) assert.throws(() => parseFamilyGoalsFeature(env, FAMILY_GOALS_FEATURE), env);
});

test('请求体：合法的最小与完整形状', () => {
  assert.deepEqual(parseGoalInput(goal()), { kind: 'ok', value: { focus_subjects: [], target_scores: [], school_id: null, note: null } });
  const full = parseGoalInput(goal({ focus_subjects: ['aya', 'wenli'], target_scores: [{ subject: 'aya', full_score: 120, target_score: 0 },
    { subject: 'wenli', full_score: 150, target_score: 150 }], school_id: '6f530000-0000-4000-8000-0000000005c1', note: '合成说明' }));
  assert.equal(full.kind, 'ok');
});

test('请求体：形状不合一律 invalid', () => {
  const bad: unknown[] = [
    null, {}, { focus_subjects: [] }, { ...goal(), extra: true },
    goal({ focus_subjects: 'aya' }), goal({ focus_subjects: ['aya', 'wenli', 'echo'] }), goal({ focus_subjects: ['aya', 'aya'] }),
    goal({ focus_subjects: ['math'] }), goal({ focus_subjects: [null] }),
    goal({ target_scores: {} }), goal({ target_scores: [null] }), goal({ target_scores: [[]] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 100 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: 90, rank: 1 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 0, target_score: 0 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 301, target_score: 1 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: -1 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: 100.1 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: 95.25 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: '90' }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: Number.NaN, target_score: 1 }] }),
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: 90 }, { subject: 'aya', full_score: 120, target_score: 90 }] }),
    goal({ school_id: 'x' }), goal({ school_id: 1 }), goal({ school_id: '6F530000-0000-4000-8000-0000000005C1' }),
    goal({ note: 1 }), goal({ note: 'x'.repeat(201) }), goal({ note: '行\n分' }), goal({ note: '零宽​' }), goal({ note: '方向‮' }),
    goal({ note: '软­' }), goal({ note: '﻿前缀' }), goal({ note: '行 分' }),
  ];
  for (const b of bad) assert.equal(parseGoalInput(b as Record<string, unknown> | null).kind, 'invalid', JSON.stringify(b));
});

test('补充说明：首尾空白去掉，空串当作不填；200 字按码位计', () => {
  assert.equal(normalizeNote('  合成  '), '合成');
  assert.equal(normalizeNote('　合成　'), '合成');
  assert.equal(normalizeNote('   '), null);
  assert.equal(normalizeNote(undefined), null);
  assert.equal(normalizeNote('𠀀'.repeat(200)), '𠀀'.repeat(200), '扩展区汉字按一个字计');
  assert.equal(normalizeNote('𠀀'.repeat(201)), undefined);
  assert.ok(HIDDEN_CHARS.test('⁠') && HIDDEN_CHARS.test('\u0085') && !HIDDEN_CHARS.test('合成 说明'));
});

test('补充说明命中禁用推断词表：forbidden_text（每类的禁用例句都拦下，允许例句不误伤）', () => {
  const lex = lexicon();
  for (const c of lex.categories) {
    for (const ex of c.forbiddenExamples) {
      const r = parseGoalInput(goal({ note: ex.slice(0, 200) }), () => lex);
      assert.ok(r.kind === 'forbidden_text' || r.kind === 'invalid', `${c.id}：${ex}`);
    }
  }
  for (const ex of lex.allowedExamples) assert.equal(parseGoalInput(goal({ note: ex }), () => lex).kind, 'ok', ex);
  // 规范化之后再匹配：全角、零宽之外的写法变体
  assert.equal(parseGoalInput(goal({ note: '孩子有點懶惰' }), () => lex).kind, 'forbidden_text', '繁体写法同样拦下');
  // 词表只在有补充说明时读：没有说明的请求不碰词表；有说明而词表读不到时抛错（路由回 503，不存）
  const broken = () => { throw new Error('词表读不到'); };
  assert.equal(parseGoalInput(goal(), broken).kind, 'ok');
  assert.throws(() => parseGoalInput(goal({ note: '合成' }), broken));
});
