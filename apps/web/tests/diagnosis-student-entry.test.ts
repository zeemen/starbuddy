// 学生端数学诊断入口与正式做题页（specs/diagnosis-student-entry）的单测与静态检查：
//   - 开关 STARBUDDY_DIAGNOSIS_UI：默认关；dev / test / staging 可开；pilot、生产与没设一律关；与再练、复测的开关互不影响；学科只有数学；
//   - 页面先过开关（读会话之前）、学科不是数学 404、只认学生本人；入口组件关时返回 null；只有数学科目页挂入口，家长端不引用；
//   - 正式 server action 先过开关再读会话，经 src/diagnosis/ops.ts 调 BFF；不引用开发演示页的任何文件，演示页的关卡不受开关影响；
//   - 入口与返回文案是 {{TODO_COPY:diagnosis.*}} 占位（人写人审），已并入诊断文案的禁语扫描。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { allDiagnosisCopy, DIAGNOSIS_ENTRY_COPY } from '../src/diagnosis/copy.ts';
import { DIAGNOSIS_SUBJECTS, diagnosisEnabled, isDiagnosisSubject } from '../src/diagnosis/features.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const src = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

test('开关：默认关；dev / test / staging 可开；pilot、生产与没设一律关；与再练、复测互不影响；学科只有数学', () => {
  assert.equal(diagnosisEnabled({}), false);
  assert.equal(diagnosisEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(diagnosisEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_PRACTICE: 'practice', STARBUDDY_RETEST_UI: 'retest' }), false);
  assert.equal(diagnosisEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_DIAGNOSIS_UI: 'diagnosis' }), true);
  assert.equal(diagnosisEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_DIAGNOSIS_UI: ' diagnosis ' }), true);
  assert.equal(diagnosisEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_DIAGNOSIS_UI: 'practice' }), false);
  assert.equal(diagnosisEnabled({ STARBUDDY_ENV: 'staging', STARBUDDY_DIAGNOSIS_UI: 'diagnosis' }), true);  // LT-01
  for (const env of ['pilot', 'production', '']) assert.equal(diagnosisEnabled({ STARBUDDY_ENV: env, STARBUDDY_DIAGNOSIS_UI: 'diagnosis' }), false, env);
  assert.deepEqual([...DIAGNOSIS_SUBJECTS], ['math']);
  for (const s of ['politics', 'chinese', 'english', '', undefined, 'MATH']) assert.equal(isDiagnosisSubject(s), false, String(s));
});

test('页面先过开关（读会话之前），学科不是数学 404，只认学生本人；入口关时返回 null；只有数学科目页挂入口，家长端不引用', () => {
  const pages = code(read('src/diagnosis/DiagnosisPages.tsx'));
  assert.match(pages, /export function DiagnosisLink[^]*?\{\n\s+if \(!diagnosisEnabled\(\) \|\| !isDiagnosisSubject\(subject\)\) return null;/);
  const body = pages.slice(pages.indexOf('export async function DiagnosisStudentPage'));
  const gate = body.indexOf('if (!diagnosisEnabled()) notFound();');
  const subject = body.indexOf('if (!isDiagnosisSubject(subject)) notFound();');
  const session = body.indexOf('await requireOwnStudent(studentId)');
  assert.ok(gate > 0 && gate < subject && subject < session, '开关、学科都在读会话之前');
  assert.ok(!/requireStudent\(|requireGuardian\(/.test(pages), '只认学生本人');
  assert.match(pages, /<DiagnosisStudent csrf=\{ps\.session\.csrf\} student=\{studentId\} \/>/);
  assert.match(read('src/app/s/[studentId]/learn/[subject]/diagnosis/page.tsx'), /return <DiagnosisStudentPage studentId=\{studentId\} subject=\{subject\} \/>;/);
  assert.match(read('src/app/s/[studentId]/learn/[subject]/page.tsx'), /<DiagnosisLink studentId=\{studentId\} subject=\{m\.code\} \/>/);
  for (const f of src.filter((x) => x.path.startsWith('src/app/family/') || x.path.startsWith('src/subjects/'))) {
    assert.ok(!/DiagnosisLink|DiagnosisPages|diagnosis\/actions|DIAGNOSIS_ENTRY_COPY/.test(code(f.text)), f.path);
  }
});

test('正式 server action：先过开关再读会话，经 ops 调 BFF；不引用演示页；演示页的关卡不受开关影响', () => {
  const actions = code(read('src/diagnosis/actions.ts'));
  assert.match(actions, /^'use server';/);
  assert.match(actions, /async function account\(csrf: string\): Promise<BffSession \| null> \{\n\s+if \(!diagnosisEnabled\(\)\) return null;\n\s+return actionSession\(csrf\);/);
  const names = [...actions.matchAll(/export async function (\w+)\(/g)].map((m) => m[1]);
  assert.deepEqual(names, ['startDiagnosisAction', 'currentDiagnosisAction', 'abandonDiagnosisAction', 'probeDiagnosisAction', 'answerDiagnosisAction',
    'pauseDiagnosisAction']);
  for (const n of names) {
    const fn = actions.slice(actions.indexOf(`export async function ${n}(`)).split('\nexport ')[0]!;
    assert.match(fn, /return \w+Op\(await account\(csrf\), /, `${n} 先过 account（开关 + 会话）`);
  }
  assert.ok(!/dev\/|devRuntimeAllowed|headers\(\)/.test(actions), '不引用演示页与开发关卡');
  const demo = code(read('src/app/dev/diagnosis/actions.ts'));
  assert.match(demo, /if \(!devRuntimeAllowed\(\(await headers\(\)\)\.get\('host'\)\)\) return null;\n\s+return actionSession\(csrf\);/);
  assert.ok(!/diagnosisEnabled/.test(demo), '演示页不受前端开关影响');
  // ops 不是 server action（不能被浏览器直接调用），只被两处 'use server' 文件引用
  const ops = read('src/diagnosis/ops.ts');
  assert.ok(!/^'use server'/m.test(ops));
  const users = src.filter((f) => /diagnosis\/ops\.ts'|from '\.\/ops\.ts'/.test(f.text)).map((f) => f.path).sort();
  assert.deepEqual(users, ['src/app/dev/diagnosis/actions.ts', 'src/diagnosis/actions.ts']);
});

test('入口与返回文案是 {{TODO_COPY:diagnosis.*}} 占位，并入诊断文案的禁语扫描', () => {
  for (const [k, v] of Object.entries(DIAGNOSIS_ENTRY_COPY)) assert.match(v, new RegExp(`^\\{\\{TODO_COPY:diagnosis\\.${k}\\}\\} `), k);
  const all = allDiagnosisCopy();
  for (const v of Object.values(DIAGNOSIS_ENTRY_COPY)) assert.ok(all.includes(v), v);
});
