// 预制答疑（specs/stage-point-to-ask-answer PR ②，台词稿 v0.3）：答疑的格式核对、answerFor 只从登记过的稿里取、台词稿的迁移入口、
// 台词稿 schema 与常量一致。答疑台词全是明显的合成占位，不是人设话术（规则 7）。全部是合成数据。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  ANSWER_FIELDS, ANSWER_INTENTS, answerFor, DSL_VERSION, isValidatedScript, migrateStageScript, QUESTION_INTENTS, SCRIPT_FIELDS, SCRIPT_LIMITS,
  SCRIPT_MIGRATIONS, SCRIPT_OPTIONAL, validateStageDsl, validateStagePair, type DslMigration, type ValidatedStageDsl,
} from '../src/index.ts';
import { narrated, narratedScript, sample } from './fixtures.ts';

const ok = (raw: unknown): ValidatedStageDsl => {
  const r = validateStageDsl(raw);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  return r.dsl;
};
const DSL = ok(narrated());   // s1 老师（l1）、s2 老师（l2）、s3 老师（l3）、s4 点点（l4）、s5 没人讲

/** 带两条答疑的合格稿：s2 问为什么由老师答，s4 问是什么由点点答 */
const withAnswers = (): Record<string, any> => {
  const s = narratedScript();
  s.lines.push({ id: 'a1', role: 'teacher', text: '（合成答疑）因为要把常数移到右边。' },
    { id: 'a2', role: 'buddy', text: '（合成答疑）这是两边同时减三。' });
  s.answers = [{ step: 's2', intent: 'why_step', line: 'a1' }, { step: 's4', intent: 'what_is', line: 'a2' }];
  return s;
};
const codes = (raw: unknown, dsl = DSL) => { const r = validateStagePair(dsl, raw); return r.ok ? [] : r.errors.map((e) => `${e.path} ${e.code}`); };
const has = (raw: unknown, want: string, dsl = DSL) => assert.ok(codes(raw, dsl).includes(want), `${want} ∉ ${codes(raw, dsl).join('; ')}`);

test('合格的答疑：登记、冻结；answerFor 按步骤与问法取，没有就是 null，“再讲一遍”不取', () => {
  const r = validateStagePair(DSL, withAnswers());
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.ok(isValidatedScript(r.script) && Object.isFrozen(r.script.answers) && Object.isFrozen(r.script.answers![0]));
  assert.deepEqual(answerFor(DSL, r.script, 's2', 'why_step'), { id: 'a1', role: 'teacher', text: '（合成答疑）因为要把常数移到右边。' });
  assert.equal(answerFor(DSL, r.script, 's4', 'what_is')?.id, 'a2');
  assert.equal(answerFor(DSL, r.script, 's2', 'what_is'), null);
  assert.equal(answerFor(DSL, r.script, 's9', 'why_step'), null);
  assert.equal(answerFor(DSL, r.script, 's2', 'again'), null);
  assert.equal(answerFor(DSL, withAnswers() as never, 's2', 'why_step'), null, '没登记过的稿不取');
  // 不带 answers 的稿照常合格，结果里也不凭空多出 answers
  const plain = validateStagePair(DSL, narratedScript());
  assert.ok(plain.ok && !('answers' in plain.script));
  const empty = validateStagePair(DSL, { ...narratedScript(), answers: [] });
  assert.ok(empty.ok && empty.script.answers?.length === 0);
});

test('答疑的问法：只有三种，“再讲一遍”不收', () => {
  assert.deepEqual([...ANSWER_INTENTS], QUESTION_INTENTS.filter((i) => i !== 'again'));
  for (const bad of ['again', 'why', '', 3]) {
    const s = withAnswers();
    s.answers[0].intent = bad;
    has(s, '$.answers[0].intent bad_enum');
  }
});

test('答疑的结构：步骤存在、字段白名单、同一步同一问法只有一句、一句只答一处、条数上限', () => {
  let s = withAnswers(); s.answers[0].step = 's9'; has(s, '$.answers[0].step unknown_target');
  s = withAnswers(); s.answers[0].note = '（合成）'; has(s, '$.answers[0].note unknown_field');
  s = withAnswers(); delete s.answers[0].line; has(s, '$.answers[0].line missing_field');
  s = withAnswers(); s.answers[0].line = 'A1'; has(s, '$.answers[0].line bad_id');
  s = withAnswers(); s.answers.push({ step: 's2', intent: 'why_step', line: 'a2' }); has(s, '$.answers[2] duplicate_id');
  s = withAnswers(); s.answers.push({ step: 's3', intent: 'where_from', line: 'a1' }); has(s, '$.answers[2].line duplicate_id');
  s = withAnswers(); s.answers = 'a1'; has(s, '$.answers bad_type');
  s = withAnswers(); s.answers = [null]; has(s, '$.answers[0] not_object');
  s = withAnswers();
  s.answers = Array.from({ length: SCRIPT_LIMITS.maxAnswers + 1 }, (_, i) => ({ step: 's2', intent: 'why_step', line: `x${i}` }));
  has(s, '$.answers too_many');
});

test('答疑台词：必须在稿里、不兼作字幕台词、角色等于这一步讲话的人（没人讲不行）', () => {
  let s = withAnswers(); s.lines = s.lines.filter((l: { id: string }) => l.id !== 'a1'); has(s, '$.answers[0].line unknown_line');
  s = withAnswers(); s.answers[0].line = 'l2'; has(s, '$.answers duplicate_id');
  s = withAnswers(); s.lines.find((l: { id: string }) => l.id === 'a1').role = 'buddy'; has(s, '$.lines[4].role bad_role');
  s = withAnswers(); s.answers[1].step = 's5'; has(s, '$.lines[5].role bad_role');
  // 没有被任何答疑引用的额外台词仍是多余台词
  s = withAnswers(); s.answers.pop(); has(s, '$.lines[5].id unused_line');
});

test('答疑台词与字幕台词同一套长度、句数与安全检查', () => {
  const line = (text: string) => { const s = withAnswers(); s.lines[4].text = text; return s; };
  has(line('（合成答疑）' + '长'.repeat(SCRIPT_LIMITS.maxSayChars)), '$.lines[4].text too_long');
  has(line('（合成）一。二。三。'), '$.lines[4].text too_many_sentences');
  has(line('（合成）见 https://example.invalid'), '$.lines[4].text link_or_script');
  has(line('（合成）<b>粗</b>'), '$.lines[4].text html');
  has(line('   '), '$.lines[4].text empty');
});

test('只有有台词的讲解（narrated）才能带答疑；无声板书带空列表可以', () => {
  const silent = ok(sample());
  has({ dsl_version: DSL_VERSION, lines: [{ id: 'a1', role: 'teacher', text: '（合成答疑）' }], answers: [{ step: 's1', intent: 'why_step', line: 'a1' }] },
    '$.answers not_narrated', silent);
  assert.ok(validateStagePair(silent, { dsl_version: DSL_VERSION, lines: [], answers: [] }).ok);
});

test('台词稿的迁移入口：0.2.0 的旧稿只升版本号、台词逐字不变；未来、未知、迁移失败一律拒收；不改输入', () => {
  const old: Record<string, any> = { ...narratedScript(), dsl_version: '0.2.0' };
  const before = JSON.stringify(old);
  const r = migrateStageScript(DSL, old);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.equal(r.from, '0.2.0');
  assert.equal(r.script.dsl_version, DSL_VERSION);
  assert.deepEqual(r.script.lines.map((l) => l.text), old.lines.map((l: { text: string }) => l.text));
  assert.ok(!('answers' in r.script));
  assert.equal(JSON.stringify(old), before);
  const cur = migrateStageScript(DSL, withAnswers());
  assert.ok(cur.ok && cur.from === DSL_VERSION && cur.script.answers?.length === 2);
  for (const [v, code] of [['0.3.1', 'future_version'], ['1.0.0', 'future_version'], ['0.1.0', 'unknown_version']] as const) {
    const x = migrateStageScript(DSL, { ...narratedScript(), dsl_version: v });
    assert.ok(!x.ok && x.errors[0]!.code === code && x.from === v, v);
  }
  // 改了台词的迁移、迁移后配对不过：migration_failed
  const changing: DslMigration[] = [{ from: '0.2.0', to: DSL_VERSION, migrate: (s) => ({ ...s, lines: (s['lines'] as { text: string }[]).map((l) => ({ ...l, text: `${l.text}改` })) }) }];
  const c = migrateStageScript(DSL, old, changing);
  assert.ok(!c.ok && c.errors[0]!.code === 'migration_failed');
  const broken = migrateStageScript(DSL, { ...old, lines: old.lines.slice(1) });
  assert.ok(!broken.ok && broken.errors[0]!.code === 'migration_failed');
  assert.deepEqual(SCRIPT_MIGRATIONS.map((m) => `${m.from}→${m.to}`), ['0.2.0→0.3.0']);
});

test('台词稿 schema 与常量一致：版本、字段白名单、答疑字段与问法、条数上限', () => {
  const schema = JSON.parse(readFileSync(new URL(`../schema/stage-script-${DSL_VERSION}.schema.json`, import.meta.url), 'utf8'));
  assert.equal(schema.properties.dsl_version.const, DSL_VERSION);
  assert.deepEqual([...schema.required].sort(), [...SCRIPT_FIELDS].sort());
  assert.deepEqual(Object.keys(schema.properties).sort(), [...SCRIPT_FIELDS, ...SCRIPT_OPTIONAL].sort());
  assert.equal(schema.properties.answers.maxItems, SCRIPT_LIMITS.maxAnswers);
  const a = schema.$defs.answer;
  assert.equal(a.additionalProperties, false);
  assert.deepEqual([...a.required].sort(), [...ANSWER_FIELDS].sort());
  assert.deepEqual(a.properties.intent.enum, [...ANSWER_INTENTS]);
  assert.equal(schema.properties.lines.maxItems, SCRIPT_LIMITS.maxLines);
});
