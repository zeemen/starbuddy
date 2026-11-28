// 讲评课页面（specs/stage-lesson-session ③b-5）的单测与静态检查：
//   - 开关：默认关；只在 dev / test 可开（staging、pilot、生产一律关）；关时 BFF 客户端不发任何请求（按 404 返回）；页面与 server action 在读会话之前先过开关；
//   - 视图只挑白名单字段（会话 ID、usage_id、过期时间、第几问不进页面）；票据只认 BFF 封装的 sbl1.；错误编码映射；
//   - 地址栏参数：讲评课 ID、版本、节点、题只认合规的写法；
//   - 静态：讲评课的 BFF 调用只在 src/lib/lesson-api.ts 的 call 一处 fetch；不直连学习服务；票据不进 DOM 属性与地址栏、不写浏览器存储；
//     页面载入不打开讲评课（要点“开始”，打开会计一次）；路由文件是普通页面（page.tsx，specs/stage-production-build），只挂组件；
//   - 文案：全部是 {{TODO_COPY:lesson.*}} 占位并已并入壳文案（禁语等扫描在 tests/shell.test.ts）。
//   - 讲评课目录（specs/lesson-catalog PR D）：地址只带编号与版本（node、item 404）；目录与条目上的引用只挑白名单字段；
//     科目页列表与再练、复测的入口先过开关；入口只在判分结果之后渲染。
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { ALL_LESSON_COPY, ALL_SHELL_COPY, LESSON, LESSON_ERROR } from '../src/shell/copy.ts';
import { isLessonSubject, LESSON_SUBJECTS, lessonEnabled } from '../src/lesson/features.ts';
import { entryLessonRef, lessonHref, parseLessonRef, pickAsk, pickCatalog, pickOpen, pickWireRef, uiError } from '../src/lesson/view.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const TICKET = `sbl1.${'A'.repeat(40)}`;

test('开关：默认关；只在 dev / test 可开；staging、pilot、生产一律关；讲评课学科只有数学（阿鸦）', () => {
  assert.equal(lessonEnabled({}), false);
  assert.equal(lessonEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(lessonEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_LESSON: 'lesson' }), true);
  assert.equal(lessonEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_LESSON: ' lesson ' }), true);
  assert.equal(lessonEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_LESSON: 'lessons' }), false);
  for (const env of ['staging', 'pilot', 'production', '']) assert.equal(lessonEnabled({ STARBUDDY_ENV: env, STARBUDDY_LESSON: 'lesson' }), false, env);
  assert.deepEqual(LESSON_SUBJECTS, { math: 'aya' });
  for (const s of ['politics', 'chinese', 'aya', '']) assert.equal(isLessonSubject(s), false, s);
  assert.equal(isLessonSubject('math'), true);
});

test('开关关时 BFF 客户端不发任何请求（按 404 返回）', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], flag: process.env['STARBUDDY_LESSON'], fetch: globalThis.fetch };
  let calls = 0;
  globalThis.fetch = (async () => { calls += 1; return new Response('{}'); }) as typeof fetch;
  try {
    delete process.env['STARBUDDY_LESSON'];
    process.env['STARBUDDY_ENV'] = 'test';
    const { askLesson, fetchLessonCatalog, openLesson } = await import('../src/lib/lesson-api.ts');
    const s = { token: 'a'.repeat(64), csrf: 'b'.repeat(64) };
    for (const r of [await openLesson(s, '00000000-0000-4000-8000-00000000a001', {}), await askLesson(s, '00000000-0000-4000-8000-00000000a001', {}),
                     await fetchLessonCatalog(s, '00000000-0000-4000-8000-00000000a001', 'math')]) {
      assert.deepEqual(r, { ok: false, status: 404, code: 'NOT_FOUND', reason: null });
    }
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = saved.fetch;
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.flag === undefined) delete process.env['STARBUDDY_LESSON']; else process.env['STARBUDDY_LESSON'] = saved.flag;
  }
});

test('地址栏参数：只认讲评课编号与版本；地址由编号与版本拼出，不带节点与题', () => {
  assert.deepEqual(parseLessonRef('syn.lesson.a', '0.1.0'), { lessonId: 'syn.lesson.a', lessonVersion: '0.1.0' });
  for (const args of [['Syn', '0.1.0'], ['syn', 'v1'], ['syn', undefined], [undefined, '0.1.0'], ['../x', '0.1.0'], ['syn', ['0.1.0']]] as const) {
    assert.equal(parseLessonRef(args[0], args[1]), null, JSON.stringify(args));
  }
  const S = '00000000-0000-4000-8000-00000000a001';
  assert.equal(lessonHref(S, 'math', { lessonId: 'syn.lesson.a', lessonVersion: '0.1.0' }), `/s/${S}/learn/math/lesson/syn.lesson.a?v=0.1.0`);
  // 以前带 node、item 的地址 404：页面在读会话之前判断
  const page = code(read('src/lesson/LessonPage.tsx'));
  assert.match(page, /if \('node' in search \|\| 'item' in search\) notFound\(\);/);
  assert.ok(page.indexOf("'node' in search") < page.indexOf('requireOwnStudent('));
});

test('目录与条目上的讲评课引用：只挑编号、版本、标题与步数；任何一条不合格整份不显示；条目引用不合格当作没有', () => {
  const row = { lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' }, title: '（合成）讲评课', step_count: 3, skill_node: 'aya:K-1' };
  assert.deepEqual(pickCatalog({ subject: 'math', lessons: [row] }), [{ lessonId: 'syn.lesson.a', lessonVersion: '0.1.0', title: '（合成）讲评课', stepCount: 3 }]);
  assert.deepEqual(pickCatalog({ subject: 'math', lessons: [] }), []);
  for (const bad of [null, {}, { lessons: {} }, { lessons: [row, { ...row, title: '' }] }, { lessons: [{ ...row, title: 'x'.repeat(41) }] },
                     { lessons: [{ ...row, title: '（合成）\u0007' }] }, { lessons: [{ ...row, step_count: 0 }] }, { lessons: [{ ...row, step_count: 101 }] },
                     { lessons: [{ ...row, lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0', item_ref: 'aya:SYN-1' } }] },
                     { lessons: Array.from({ length: 201 }, () => row) }]) {
    assert.equal(pickCatalog(bad), null, JSON.stringify(bad).slice(0, 80));
  }
  assert.deepEqual(pickWireRef({ lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' }), { lessonId: 'syn.lesson.a', lessonVersion: '0.1.0' });
  const E = '00000000-0000-4000-8000-00000000e001';
  const q = { entries: [{ entry_id: E, lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' } }, { entry_id: 'other' }] };
  assert.deepEqual(entryLessonRef(q, E), { lessonId: 'syn.lesson.a', lessonVersion: '0.1.0' });
  assert.equal(entryLessonRef(q, 'other'), null);
  assert.equal(entryLessonRef(q, 'missing'), null);
  assert.equal(entryLessonRef({ entries: [{ entry_id: E, lesson_ref: { lesson_id: 'Bad', lesson_version: '0.1.0' } }] }, E), null);
  assert.equal(entryLessonRef(null, E), null);
});

test('静态：科目页列表与再练、复测的入口先过讲评课开关与学科；入口只在判分结果之后渲染', () => {
  const list = code(read('src/lesson/LessonList.tsx'));
  assert.ok(list.indexOf('lessonEnabled()') < list.indexOf('fetchLessonCatalog('), '开关关时不取目录');
  assert.match(list, /if \(!lessonEnabled\(\) \|\| !isLessonSubject\(subject\)\) return null;/);
  assert.match(code(read('src/app/s/[studentId]/learn/[subject]/page.tsx')), /<LessonList session=\{ps\.session\} studentId=\{studentId\} subject=\{m\.code\} \/>/);
  for (const [pages, item, q, tid] of [['src/practice/PracticePages.tsx', 'src/practice/PracticeItem.tsx', 'fetchPracticeQueue', 'practice-lesson'],
                                       ['src/retest/RetestPages.tsx', 'src/retest/RetestItem.tsx', 'fetchRetestQueue', 'retest-lesson']] as const) {
    const p = code(read(pages));
    assert.match(p, new RegExp(`lessonEnabled\\(\\) && isLessonSubject\\(subject\\) \\? await ${q}\\(`), `${pages}：先过开关再取列表`);
    const c = code(read(item));
    const link = c.indexOf(`data-testid="${tid}"`);
    assert.ok(link > 0, item);
    assert.equal(c.match(/lessonHref \?/g)?.length, 1, `${item}：入口只有一处`);
    assert.ok(c.lastIndexOf("phase === 'result'", link) > c.lastIndexOf("phase === 'item'", link), `${item}：入口在判分结果那一段里`);
  }
});

test('视图：打开只挑讲解、台词稿、票据与余量；提问只挑结果、文字或原因与余量；不合格整体丢弃', () => {
  const wire = { dsl_version: '0.3.0', dsl: { steps: [] }, script: { lines: [] }, ticket: TICKET, expires_at: '2026-11-27T10:00:00Z',
                 questions_left: 20, session_id: '00000000-0000-4000-8000-00000000b001', usage_id: 'x' };
  assert.deepEqual(pickOpen(wire), { dsl: { steps: [] }, script: { lines: [] }, ticket: TICKET, questionsLeft: 20 });
  for (const bad of [{ ...wire, ticket: 'lt1.a.b' }, { ...wire, ticket: 'sbp1.AAAA' }, { ...wire, dsl_version: '0.2.0' }, { ...wire, dsl: 'x' },
                     { ...wire, questions_left: 21 }, { ...wire, questions_left: -1 }, null, []]) {
    assert.equal(pickOpen(bad), null, JSON.stringify(bad));
  }
  assert.deepEqual(pickAsk({ outcome: 'answered', text: '（合成答疑）', questions_left: 19, question_seq: 1 }),
                   { kind: 'answered', text: '（合成答疑）', questionsLeft: 19 });
  assert.deepEqual(pickAsk({ outcome: 'no_answer', reason: 'limit', questions_left: 0 }), { kind: 'no_answer', reason: 'limit', questionsLeft: 0 });
  for (const bad of [{ outcome: 'answered', text: ' ', questions_left: 1 }, { outcome: 'no_answer', reason: 'x', questions_left: 1 },
                     { outcome: 'answered', text: 'a', questions_left: '1' }, {}]) {
    assert.equal(pickAsk(bad), null, JSON.stringify(bad));
  }
});

test('错误映射：401 登录；403、404 没有找到；402 宵禁与额度分开；410 过期；409 不能继续；503 结果未知可重试', () => {
  const e = (status: number, code = 'X', reason: string | null = null) => uiError({ status, code, reason });
  assert.equal(e(401), 'login');
  assert.equal(e(403), 'not_found');
  assert.equal(e(404, 'NOT_FOUND'), 'not_found');
  assert.equal(e(402, 'QUOTA_UNAVAILABLE', 'curfew'), 'curfew');
  assert.equal(e(402, 'QUOTA_UNAVAILABLE', 'insufficient'), 'quota');
  assert.equal(e(402, 'QUOTA_UNAVAILABLE', 'no_entitlement'), 'quota');
  assert.equal(e(410, 'TICKET_EXPIRED'), 'expired');
  for (const c of ['LESSON_CLOSED', 'KEY_FAILED_PREVIOUSLY', 'KEY_EXPIRED']) assert.equal(e(409, c), 'closed', c);
  assert.equal(e(503, 'UPSTREAM_UNAVAILABLE'), 'retry');
  assert.equal(e(503, 'LESSON_UNAVAILABLE'), 'unavailable');
  assert.equal(e(422, 'LESSON_NOT_AVAILABLE'), 'unavailable');
  assert.equal(e(422, 'INVALID'), 'invalid');
  for (const k of ['not_found', 'quota', 'curfew', 'expired', 'closed', 'retry', 'unavailable', 'invalid', 'content'] as const) assert.ok(LESSON_ERROR[k]);
});

test('静态：只在 src/lib/lesson-api.ts 的 call 一处 fetch；不直连学习服务；票据不进 DOM 属性、地址栏与浏览器存储；页面载入不打开', () => {
  const api = code(read('src/lib/lesson-api.ts'));
  assert.equal(api.match(/\bfetch\(/g)?.length, 1);
  assert.match(api, /`catalog\/\$\{subject\}`/, '目录走同一个 call');
  assert.ok(api.includes('/bff/v0/students/${student}/lessons/${tail}'));
  for (const f of ['src/lesson/LessonPlayer.tsx', 'src/lesson/LessonPage.tsx', 'src/lesson/LessonList.tsx', 'src/lesson/actions.ts', 'src/lesson/view.ts',
                   'src/lesson/features.ts']) {
    const t = code(read(f));
    assert.ok(!/\bfetch\(|\/internal\/v1|localStorage|sessionStorage|indexedDB|document\.cookie/.test(t), f);
    assert.ok(!/data-[a-z-]*=\{[^}]*ticket/i.test(t), `${f}：票据不进 DOM 属性`);
    assert.ok(!/history\.(push|replace)State|router\.(push|replace)/.test(t), `${f}：不改地址栏`);
  }
  const player = code(read('src/lesson/LessonPlayer.tsx'));
  // 打开只在“开始”按钮（与结果未知后的“再试一次”）里调用：没有挂载即打开的 useEffect
  assert.ok(!/useEffect\([^)]*open\(/.test(player));
  assert.equal(player.match(/openLessonAction\(/g)?.length, 1);
  // 讲解 DSL 与台词稿在播放前经 packages/stage 的同一套入口读一次
  assert.match(player, /migrateStageDsl\(r\.view\.dsl\)/);
  assert.match(player, /migrateStageScript\(d\.dsl, r\.view\.script\)/);
  // server action 先过开关再读会话
  const actions = code(read('src/lesson/actions.ts'));
  assert.ok(actions.indexOf('lessonEnabled()') < actions.indexOf('actionSession('));
  const page = code(read('src/lesson/LessonPage.tsx'));
  assert.ok(page.indexOf('lessonEnabled()') < page.indexOf('requireOwnStudent('));
  // 路由文件是普通页面（生产构建里也有，可达与否由开关决定）：只挂 LessonPage，开关与会话都在 LessonPage 里先后核对
  const ROUTE = 'src/app/s/[studentId]/learn/[subject]/lesson/[lessonId]';
  assert.ok(existsSync(join(WEB, ROUTE, 'page.tsx')));
  assert.ok(!existsSync(join(WEB, ROUTE, 'page.dev.tsx')));
  const route = code(read(`${ROUTE}/page.tsx`));
  assert.match(route, /return <LessonPage /);
  assert.ok(!/lessonEnabled|requireOwnStudent|cookies\(|headers\(|fetch\(/.test(route), '路由文件只挂组件');
});

test('文案：全部是 {{TODO_COPY:lesson.*}} 占位，已并入壳文案', () => {
  assert.ok(ALL_LESSON_COPY.length >= 20);
  for (const c of ALL_LESSON_COPY) {
    assert.match(c, /^\{\{TODO_COPY:lesson\.[a-z_.]+\}\}/, c);
    assert.ok(ALL_SHELL_COPY.includes(c), c);
  }
  assert.ok(LESSON.start && LESSON.limit);
});
