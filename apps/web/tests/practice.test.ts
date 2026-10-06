// 错题再练学生端页面（specs/practice-loop-stage1 PR 4）的单测与静态检查：
//   - 开关：默认关；只在 dev / test 可开；关时 BFF 客户端不发任何请求（按 404 返回），页面与 server action 在读会话之前先过开关；
//   - 不直连学习服务：前端源码里没有学习服务的地址、内部接口路径与令牌；再练的 BFF 调用只在 src/lib/practice-api.ts 的 call 一处 fetch；
//   - 不渲染内部字段：视图只挑白名单字段（题目引用、复测标记、同族限定标记、空原因编码、掌握、置信度、证据 ID、错因一律丢弃）；
//     待复核不给结论；票据只在组件内存，不进 DOM；
//   - 错误映射：402（宵禁、额度）、409、410、422、429、503 → 页面编码；结果未知与限流时原样再发首次作答快照（控件禁用）；
//   - 列表的知识节点编号不合口径只隐藏那一格；学生壳回跳白名单的再练段只在数学、道法之后；
//   - 文案：全部是 {{TODO_COPY:practice.*}} 占位并已并入壳文案（禁语、禁用推断词表、比较营销词表的扫描在 tests/shell.test.ts）；
//     条目来源（“上次没做对”一类）是纯占位、不附提示（lessons 待人确认 9 的默认）；不出现“粗心”、错因、掌握类词；
//   - 家长端不引用再练；入口组件开关关时返回 null。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import {
  ALL_PRACTICE_COPY, ALL_SHELL_COPY, PRACTICE, PRACTICE_AFTER, PRACTICE_ERROR, PRACTICE_LEVEL, PRACTICE_SOURCE, PRACTICE_STATUS, PRACTICE_VERDICT,
} from '../src/shell/copy.ts';
import { isPracticeSubject, PRACTICE_SUBJECTS, practiceEnabled } from '../src/practice/features.ts';
import {
  AFTER_STATUSES, ENTRY_STATUSES, keepsTicket, LEVELS, needsNewItem, pickAnswer, pickNext, pickQueue, resubmitsSnapshot, shanghaiMinute, uiError, VERDICTS,
  type UiError,
} from '../src/practice/view.ts';

const WEB = new URL('..', import.meta.url).pathname;
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const src = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

const S = '00000000-0000-4000-8000-00000000a001';
const E1 = '00000000-0000-4000-8000-00000000e001';
const E2 = '00000000-0000-4000-8000-00000000e002';
const TICKET = `sbp1.${'A'.repeat(40)}`;

const wireEntry = (over: Record<string, unknown> = {}) => ({
  entry_id: E1, node_id: 'MATH.SYN.N1', cognitive_level: 'apply', source: 'wrong', status: 'due', round: 1, max_rounds: 3,
  next_round_at: null, empty_reason: null, ...over,
});

// ---------------------------------------------------------------- 开关

test('开关：默认关；只在 dev / test 可开；staging、pilot、生产一律关；再练学科只有数学与道法', () => {
  assert.equal(practiceEnabled({}), false);
  assert.equal(practiceEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(practiceEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_PRACTICE: 'practice' }), true);
  assert.equal(practiceEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_PRACTICE: ' practice ' }), true);
  assert.equal(practiceEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_PRACTICE: 'practise' }), false);
  for (const env of ['staging', 'pilot', 'production', '']) {
    assert.equal(practiceEnabled({ STARBUDDY_ENV: env, STARBUDDY_PRACTICE: 'practice' }), false, env);
  }
  assert.deepEqual([...PRACTICE_SUBJECTS], ['math', 'politics']);
  for (const s of ['chinese', 'english', 'aya', '../x', '']) assert.equal(isPracticeSubject(s), false, s);
});

test('开关关时 BFF 客户端不发任何请求（按 404 返回）；打开后只请求 BFF 的再练路由；学生、学科、条目不合格也不发请求', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], p: process.env['STARBUDDY_PRACTICE'], fetch: globalThis.fetch };
  const calls: string[] = [];
  globalThis.fetch = (async (u: string | URL) => {
    calls.push(String(u));
    return new Response('{"code":"QUOTA_UNAVAILABLE","reason":"curfew"}', { status: 402 });
  }) as typeof fetch;
  const OFF = { ok: false, status: 404, code: 'NOT_FOUND', reason: null };
  try {
    const api = await import('../src/lib/practice-api.ts');
    const s = { token: 'a'.repeat(64), csrf: 'b'.repeat(64) };
    process.env['STARBUDDY_ENV'] = 'test';
    delete process.env['STARBUDDY_PRACTICE'];
    assert.deepEqual(await api.fetchPracticeQueue(s, S, 'math'), OFF);
    assert.deepEqual(await api.nextPracticeItem(s, S, 'math', E1), OFF);
    assert.deepEqual(await api.answerPractice(s, S, 'math', TICKET, { choice: ['A'] }), OFF);
    process.env['STARBUDDY_ENV'] = 'staging';
    process.env['STARBUDDY_PRACTICE'] = 'practice';
    assert.deepEqual(await api.fetchPracticeQueue(s, S, 'math'), OFF, 'staging 以上开关一律关');
    process.env['STARBUDDY_ENV'] = 'test';
    assert.deepEqual(await api.fetchPracticeQueue(s, '../admin', 'math'), OFF);
    assert.deepEqual(await api.fetchPracticeQueue(s, S, 'chinese'), OFF);
    assert.deepEqual(await api.nextPracticeItem(s, S, 'math', 'x/../y'), OFF);
    assert.deepEqual(calls, []);
    assert.deepEqual(await api.fetchPracticeQueue(s, S, 'math'), { ok: false, status: 402, code: 'QUOTA_UNAVAILABLE', reason: 'curfew' });
    assert.equal(calls.length, 1);
    assert.equal(calls[0], `http://127.0.0.1:8787/bff/v0/students/${S}/practice/math`);
  } finally {
    globalThis.fetch = saved.fetch;
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.p === undefined) delete process.env['STARBUDDY_PRACTICE']; else process.env['STARBUDDY_PRACTICE'] = saved.p;
  }
});

test('BFF 客户端：只有 call 一处 fetch，先过开关；作答请求体只有 {ticket, answer}（幂等键、usage_id、purpose、channel 都不由前端给）', () => {
  const api = code(read('src/lib/practice-api.ts'));
  assert.equal(api.match(/fetch\(/g)?.length, 1);
  assert.match(api, /async function call<T>\([^)]*\)[^{]*\{\n\s+if \(!practiceEnabled\(\)\) return OFF;/);
  assert.match(api, /\$\{bffBaseUrl\(\)\}\/bff\/v0\/students\/\$\{student\}\/practice\/\$\{subject\}\$\{tail\}/);
  assert.match(api, /'POST', '\/answer', \{ ticket, answer \}\)/);
  assert.ok(!/idempotency|usage_id|purpose|channel/.test(api));
});

test('不直连学习服务：前端源码没有学习服务的地址、内部接口与令牌；再练路由只在 practice-api 里出现', () => {
  for (const f of src) {
    assert.ok(!/\/internal\/v1|LEARNING_|BFF_LEARNING|learning-client|practice-internal/.test(code(f.text)), f.path);
  }
  for (const f of src.filter((x) => /\.(tsx?)$/.test(x.path) && x.path !== 'src/lib/practice-api.ts')) {
    assert.ok(!/\/practice\/\$\{subject\}\$\{tail\}|\/entries\/|['"`]\/answer['"`]/.test(code(f.text)), f.path);
  }
  // 再练的组件、页面与 action 不自己发请求，只经 practice-api（服务端）与 server action（客户端组件）
  for (const f of src.filter((x) => x.path.startsWith('src/practice/'))) {
    assert.ok(!/\bfetch\(|XMLHttpRequest|EventSource|WebSocket|lib\/(bff|shell-api|trust-api)\.ts/.test(code(f.text)), f.path);
  }
  assert.match(read('src/practice/PracticeItem.tsx'), /^'use client';/);
  assert.ok(!/practice-api/.test(code(read('src/practice/PracticeItem.tsx'))), '客户端组件不引用服务端客户端');
});

// ---------------------------------------------------------------- 视图：只挑白名单字段

test('列表：每条只取白名单字段，内部字段（原题引用、复测、同族、空原因）与多出来的字段一律丢弃；任何一条不合格整页不给', () => {
  const raw = { subject: 'math', entries: [wireEntry({ source_item_ref: 'aya:item-1', retest_due: true, same_family_only: true, p_mastery: 0.4,
                                                       evidence_ids: ['x'], error_category: 'aya:sign', confidence: 0.9 }),
                                           wireEntry({ entry_id: E2, status: 'waiting', round: 2, next_round_at: '2026-12-01T10:00:00+08:00' }),
                                           wireEntry({ entry_id: E2, status: 'unavailable', empty_reason: 'no_admitted_candidate' })] };
  const out = pickQueue(raw)!;
  assert.equal(out.length, 3);
  for (const e of out) {
    assert.deepEqual(Object.keys(e).sort(), ['cognitive_level', 'entry_id', 'max_rounds', 'next_round_at', 'node_id', 'round', 'source', 'status']);
  }
  assert.ok(!JSON.stringify(out).includes('no_admitted_candidate'));
  for (const bad of [{ status: 'mastered' }, { cognitive_level: 'genius' }, { source: 'careless' }, { round: 0 },
                     { next_round_at: 'tomorrow' }, { entry_id: 'E1' }]) {
    assert.equal(pickQueue({ entries: [wireEntry(), wireEntry(bad)] }), null, JSON.stringify(bad));
  }
  assert.equal(pickQueue({ entries: 'x' }), null);
  assert.deepEqual(pickQueue({ subject: 'math', entries: [] }), []);
});

test('列表：知识节点编号不合前端口径时只隐藏这一行的编号（node_id 置 null），这一行其余字段与别的行照常；页面上这一格不放 data-value', () => {
  for (const node of ['x', 'math.syn.n1', 'MATH', '<b>MATH.SYN</b>', 42, null, undefined]) {
    const out = pickQueue({ entries: [wireEntry(), wireEntry({ entry_id: E2, node_id: node, status: 'waiting', round: 2, next_round_at: '2026-12-01T10:00:00+08:00' })] });
    assert.ok(out, String(node));
    assert.equal(out.length, 2);
    assert.equal(out[0]!.node_id, 'MATH.SYN.N1');
    assert.deepEqual(out[1], { entry_id: E2, node_id: null, cognitive_level: 'apply', source: 'wrong', status: 'waiting', round: 2, max_rounds: 3,
                               next_round_at: '2026-12-01T10:00:00+08:00' }, String(node));
  }
  const pages = code(read('src/practice/PracticePages.tsx'));
  assert.match(pages, /\{e\.node_id \? <span data-value="node">\{e\.node_id\}<\/span> : null\}/);
  assert.ok(!/data-entry-source/.test(pages), '条目来源不另放进 DOM 属性');
});

test('取题：只取题面（format、stem、options）与票据；题目引用与同族标记丢弃；不可用时不带空原因编码；条目对不上或票据不对整题不给', () => {
  const ready = { status: 'ready', entry_id: E1, round: 1, item_ref: 'aya:item-9', same_family_only: true, expires_at: '2026-10-06T10:00:00Z',
                  item: { format: 'single', stem: '（合成）题干', options: [{ key: 'A', text: '甲', answer: true }, { key: 'B', text: '乙' }], answer: 'A',
                          solution: '（合成）解析' }, ticket: TICKET };
  const v = pickNext(ready, E1)!;
  assert.equal(v.kind, 'ready');
  assert.ok(v.kind === 'ready');
  assert.deepEqual(v.ready, { entry_id: E1, round: 1, ticket: TICKET, item: { format: 'single', stem: '（合成）题干', options: [{ key: 'A', text: '甲' }, { key: 'B', text: '乙' }] } });
  assert.ok(!/item_ref|aya:item|same_family|answer|solution|expires/.test(JSON.stringify(v)));
  assert.deepEqual(pickNext({ status: 'unavailable', empty_reason: 'all_excluded' }, E1), { kind: 'empty' });
  assert.equal(pickNext(ready, E2), null);
  assert.equal(pickNext({ ...ready, ticket: 'pt1.raw.learning.ticket' }, E1), null, '学习服务的原票据不该到前端');
  assert.equal(pickNext({ ...ready, item: { format: 'fill', stem: 'x', options: [{ key: 'A', text: '甲' }] } }, E1), null);
  assert.equal(pickNext({ ...ready, item: { format: 'single', stem: 'x' } }, E1), null);
  assert.deepEqual((pickNext({ ...ready, item: { format: 'fill', stem: 'x' } }, E1) as { ready: { item: unknown } }).ready.item, { format: 'fill', stem: 'x' });
});

test('作答结果：只有判分结论与条目新状态；待复核（pending_review 或 needs_review）不给结论；掌握、置信度、证据 ID、错因一律丢弃', () => {
  const entry = { entry_id: E1, status: 'closed', round: 1, next_round_at: null };
  const r = pickAnswer({ verdict: 'correct', entry: { ...entry, mastery: 0.9 }, evidence_ids: ['x'], confidence: 0.8, error_category: 'aya:sign',
                         p_mastery: 0.7, explanation: '（合成）' }, E1)!;
  assert.deepEqual(r, { verdict: 'correct', pending_review: false, entry });
  assert.deepEqual(pickAnswer({ verdict: 'incorrect', pending_review: true, entry }, E1), { verdict: null, pending_review: true, entry });
  assert.deepEqual(pickAnswer({ verdict: 'needs_review', entry }, E1), { verdict: null, pending_review: true, entry });
  assert.equal(pickAnswer({ verdict: 'great', entry }, E1), null);
  assert.equal(pickAnswer({ verdict: 'correct', pending_review: false, entry }, E1), null);
  assert.equal(pickAnswer({ verdict: 'correct', entry: { ...entry, status: 'unavailable' } }, E1), null);
  assert.equal(pickAnswer({ verdict: 'correct', entry }, E2), null, '条目对不上');
});

test('错误映射：402 宵禁与额度、409、410、422、503（作答时结果未知留票据再提交）', () => {
  const f = (status: number, code: string, reason: string | null = null) => ({ ok: false as const, status, code, reason });
  const cases: [ReturnType<typeof f>, 'next' | 'answer', UiError][] = [
    [f(402, 'QUOTA_UNAVAILABLE', 'curfew'), 'next', 'curfew'], [f(402, 'QUOTA_UNAVAILABLE', 'curfew'), 'answer', 'curfew'],
    [f(402, 'QUOTA_UNAVAILABLE', 'no_entitlement'), 'answer', 'no_entitlement'],
    [f(402, 'QUOTA_UNAVAILABLE', 'daily_limit_reached'), 'answer', 'daily_limit_reached'], [f(402, 'QUOTA_UNAVAILABLE', 'bogus'), 'answer', 'unavailable'],
    [f(409, 'PRACTICE_NOT_DUE'), 'next', 'not_due'], [f(409, 'PRACTICE_NOT_DUE'), 'answer', 'not_due'], [f(409, 'TICKET_USED'), 'answer', 'ticket_used'],
    [f(409, 'KEY_SEALED'), 'answer', 'ticket_used'], [f(409, 'IDEMPOTENCY_KEY_REUSED'), 'answer', 'unavailable'],
    [f(410, 'TICKET_EXPIRED'), 'answer', 'expired'], [f(422, 'TICKET_INVALID'), 'answer', 'ticket_used'], [f(422, 'INVALID_ANSWER'), 'answer', 'invalid_answer'],
    [f(422, 'INVALID'), 'answer', 'unavailable'], [f(503, 'UPSTREAM_UNAVAILABLE'), 'answer', 'retry'], [f(503, 'UNAVAILABLE'), 'answer', 'retry'],
    [f(503, 'UPSTREAM_UNAVAILABLE'), 'next', 'unavailable'], [f(503, 'PRACTICE_UNAVAILABLE'), 'answer', 'unavailable'],
    [f(404, 'NOT_FOUND'), 'next', 'not_found'], [f(401, 'UNAUTHENTICATED'), 'answer', 'login'], [f(403, 'FORBIDDEN'), 'next', 'unavailable'],
    [f(429, 'TOO_MANY_REQUESTS'), 'answer', 'rate_limited'], [f(429, 'RATE_LIMITED'), 'answer', 'rate_limited'],
    [f(429, 'TOO_MANY_REQUESTS'), 'next', 'unavailable'],
  ];
  for (const [r, op, want] of cases) assert.equal(uiError(r, op), want, `${r.status} ${r.code} ${r.reason} ${op}`);
  // [留票据, 换一道题, 原样再发首次作答快照（控件禁用）]
  assert.deepEqual((['retry', 'rate_limited', 'invalid_answer', 'expired', 'ticket_used', 'curfew', 'not_due'] as UiError[])
                     .map((e) => [keepsTicket(e), needsNewItem(e), resubmitsSnapshot(e)]),
                   [[true, false, true], [true, false, true], [true, false, false], [false, true, false], [false, true, false], [false, false, false],
                    [false, false, false]]);
  assert.equal(shanghaiMinute('2026-12-01T02:00:00Z'), '2026-12-01 10:00');
  assert.equal(shanghaiMinute('x'), '');
});

// ---------------------------------------------------------------- 文案

test('再练文案全部是 {{TODO_COPY:practice.*}} 占位，已并入壳文案；编码映射齐全；条目来源是纯占位（规则 11 待人确认的默认）；不出现评价与错因类词', () => {
  assert.ok(ALL_PRACTICE_COPY.length > 40);
  for (const s of ALL_PRACTICE_COPY) assert.match(s, /^\{\{TODO_COPY:practice\.[a-z0-9_.]+\}\}/, s);
  for (const s of ALL_PRACTICE_COPY) assert.ok(ALL_SHELL_COPY.includes(s), s);
  for (const v of Object.values(PRACTICE_SOURCE)) assert.match(v, /^\{\{TODO_COPY:practice\.source\.\d\}\}$/, v);
  assert.deepEqual(Object.keys(PRACTICE_SOURCE).sort(), ['hint_followup', 'wrong']);
  assert.deepEqual(Object.keys(PRACTICE_STATUS).sort(), [...ENTRY_STATUSES].sort());
  assert.deepEqual(Object.keys(PRACTICE_AFTER).sort(), [...AFTER_STATUSES].sort());
  assert.deepEqual(Object.keys(PRACTICE_LEVEL).sort(), [...LEVELS].sort());
  assert.deepEqual(Object.keys(PRACTICE_VERDICT).sort(), VERDICTS.filter((v) => v !== 'needs_review').sort());
  assert.deepEqual(Object.keys(PRACTICE_ERROR).sort(),
                   ['curfew', 'daily_limit_reached', 'expired', 'invalid_answer', 'no_entitlement', 'not_due', 'rate_limited', 'retry', 'ticket_used',
                    'unavailable']);
  for (const s of ALL_PRACTICE_COPY) {
    assert.ok(!/粗心|马虎|失误|不认真|算错|薄弱|弱项|不够|较差|太差|掌握|错因|星|聪明|笨|天赋|加油|棒|上次没做对/.test(s), s);
    for (const c of ['wrong', 'hint_followup', 'no_admitted_candidate', 'all_excluded', 'same_family', 'retest']) assert.ok(!s.includes(c), `${s} 含编码 ${c}`);
  }
  assert.ok(PRACTICE.pendingReview.includes('practice.pending_review'));
});

// ---------------------------------------------------------------- 页面与组件

test('页面与 action 先过开关（读会话之前）；只认学生本人；入口组件关时返回 null；家长端不引用再练', () => {
  const pages = code(read('src/practice/PracticePages.tsx'));
  assert.match(pages, /export function PracticeLink[^]*?\{\n\s+if \(!practiceEnabled\(\) \|\| !isPracticeSubject\(subject\)\) return null;/);
  for (const fn of ['PracticeListPage', 'PracticeItemPage']) {
    const body = pages.slice(pages.indexOf(`export async function ${fn}`));
    const gate = body.indexOf('if (!practiceEnabled()) notFound();');
    assert.ok(gate > 0 && gate < body.indexOf('await requireOwnStudent(studentId)'), `${fn}：开关闸门要在读会话之前`);
  }
  assert.ok(!/requireStudent\(|requireGuardian\(/.test(pages), '只认学生本人');
  const actions = code(read('src/practice/actions.ts'));
  assert.match(actions, /^'use server';/);
  assert.match(actions, /async function begin\([^)]*\) \{\n\s+if \(!practiceEnabled\(\)\) return null;/);
  assert.ok(actions.indexOf('practiceEnabled()') < actions.indexOf('await actionSession'));
  for (const p of ['s/[studentId]/learn/[subject]/practice/page.tsx', 's/[studentId]/learn/[subject]/practice/[entryId]/page.tsx']) {
    assert.match(read(`src/app/${p}`), /return <(PracticeListPage|PracticeItemPage) /, p);
  }
  assert.match(read('src/app/s/[studentId]/learn/[subject]/page.tsx'), /<PracticeLink studentId=\{studentId\} subject=\{m\.code\} \/>/);
  for (const f of src.filter((x) => x.path.startsWith('src/app/family/') || x.path.startsWith('src/subjects/') || x.path.startsWith('src/shell/'))) {
    assert.ok(!/practice\/|PracticeLink|PRACTICE\.entry/.test(code(f.text).replace(/PRACTICE_[A-Z]+|practice_daily_limit|practice_remaining_today/g, '')), f.path);
  }
});

test('组件里没有中文文案；不渲染判断句、错因与票据；不写浏览器存储；题面与数值放在 data-item-text / data-value 里', () => {
  const tsx = src.filter((f) => f.path.startsWith('src/practice/') && f.path.endsWith('.tsx'));
  assert.equal(tsx.length, 2);
  for (const f of [...tsx, ...src.filter((x) => x.path.startsWith('src/app/s/[studentId]/learn/[subject]/practice/'))]) {
    assert.deepEqual(code(f.text).match(/[一-鿿]+/g) ?? [], [], f.path);
    assert.ok(!/data-claim-id|error_category|subject_category|mastery|confidence|evidence/.test(code(f.text)), f.path);
    assert.ok(!/localStorage|sessionStorage|indexedDB/.test(code(f.text)), f.path);
  }
  const item = code(read('src/practice/PracticeItem.tsx'));
  assert.ok(!/\{ready\.ticket\}|\{r\.ready\.ticket\}|type="hidden"|name="ticket"/.test(item), '票据只在组件内存');
  assert.match(item, /data-item-text="stem">\{item\.stem\}/);
  assert.match(item, /result\.pending_review \|\| !result\.verdict \? C\.pendingReview : PRACTICE_VERDICT\[result\.verdict\]/);
  const pages = code(read('src/practice/PracticePages.tsx'));
  assert.ok(!/empty_reason|source_item_ref|retest_due|same_family_only/.test(pages));
});

test('再提交：首次提交时记下作答快照（ref，只在组件内存），结果未知或限流时控件禁用、再提交原样发快照；取新题时清掉快照', () => {
  const item = code(read('src/practice/PracticeItem.tsx'));
  assert.match(item, /const sent = useRef<Answer \| null>\(null\);/);
  assert.match(item, /const answer: Answer \| null = sent\.current\s*\?\?/);
  assert.match(item, /sent\.current = answer;\n\s+setBusy\(true\);/);
  assert.match(item, /if \(!resubmitsSnapshot\(r\.error\)\) sent\.current = null;/);
  assert.match(item, /setPhase\('loading'\);\n\s+sent\.current = null;/);
  assert.match(item, /const locked = error !== null && resubmitsSnapshot\(error\);/);
  assert.match(item, /<input id=\{ids\.answer\}[^>]*disabled=\{locked\}/);
  assert.match(item, /<fieldset className="diagnosis-options" disabled=\{locked\}>/);
});

test('学生壳“知道了”回跳白名单：再练只在数学、道法之后；语文没有再练段', () => {
  const m = /const BACK = (\/.*\/);/.exec(read('src/app/s/[studentId]/actions.ts'))!;
  const back = new Function(`return ${m[1]}`)() as RegExp;
  for (const ok of [`/s/${S}/learn`, `/s/${S}/learn/math`, `/s/${S}/learn/chinese`, `/s/${S}/learn/politics`, `/s/${S}/learn/math/practice`,
                    `/s/${S}/learn/politics/practice`, `/s/${S}/learn/math/practice/${E1}`, `/s/${S}/learn/politics/practice/${E2}`]) {
    assert.ok(back.test(ok), ok);
  }
  for (const bad of [`/s/${S}/learn/chinese/practice`, `/s/${S}/learn/chinese/practice/${E1}`, `/s/${S}/learn/english/practice`,
                     `/s/${S}/learn/math/practice/x`, `/s/${S}/learn/practice`, `/s/${S}/learn/math/practice/${E1}/answer`]) {
    assert.ok(!back.test(bad), bad);
  }
});
