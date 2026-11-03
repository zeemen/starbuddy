// 复测与验证题学生端页面（specs/retest-delivery-kge05）的单测与静态检查：
//   - 开关 STARBUDDY_RETEST_UI：默认关；只在 dev / test 可开；关时 BFF 客户端不发任何请求，页面与 server action 在读会话之前先过开关；
//   - 视图只挑白名单字段（任务原因、作答用途、空原因编码、题目引用、掌握、置信度、证据 ID 一律丢弃）；待复核不给结论；票据只认 sbr1；
//   - 错误映射：RETEST_NOT_DUE → not_due，其余同错题再练；
//   - 文案：全部是 {{TODO_COPY:retest.*}} 占位并已并入壳文案（禁语、禁用推断词表、比较营销词表的扫描在 tests/shell.test.ts）；
//     条目类别是纯占位、不附提示（任务说明只用固定占位）；没有关于学生的陈述与评价词；
//   - 家长端不引用复测；入口组件开关关时返回 null；组件里没有中文文案、不写浏览器存储、票据不进 DOM。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import {
  ALL_RETEST_COPY, ALL_SHELL_COPY, RETEST, RETEST_AFTER, RETEST_ERROR, RETEST_KIND, RETEST_LEVEL, RETEST_STATUS, RETEST_VERDICT,
} from '../src/shell/copy.ts';
import { isRetestSubject, RETEST_SUBJECTS, retestEnabled } from '../src/retest/features.ts';
import { AFTER_STATUSES, ENTRY_STATUSES, KINDS, pickRetestAnswer, pickRetestNext, pickRetestQueue, retestUiError } from '../src/retest/view.ts';
import { LEVELS, VERDICTS } from '../src/practice/view.ts';

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
const TICKET = `sbr1.${'A'.repeat(40)}`;

test('开关：默认关；只在 dev / test 可开；staging、pilot、生产一律关；与错题再练的开关互不影响；学科只有数学与道法', () => {
  assert.equal(retestEnabled({}), false);
  assert.equal(retestEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(retestEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_PRACTICE: 'practice' }), false);
  assert.equal(retestEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_RETEST_UI: 'retest' }), true);
  assert.equal(retestEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_RETEST_UI: ' retest ' }), true);
  assert.equal(retestEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_RETEST_UI: 'practice' }), false);
  assert.equal(retestEnabled({ STARBUDDY_ENV: 'staging', STARBUDDY_RETEST_UI: 'retest' }), true);  // LT-01
  for (const env of ['pilot', 'production', '']) assert.equal(retestEnabled({ STARBUDDY_ENV: env, STARBUDDY_RETEST_UI: 'retest' }), false, env);
  assert.deepEqual([...RETEST_SUBJECTS], ['math', 'politics']);
  for (const s of ['chinese', 'english', 'aya', '']) assert.equal(isRetestSubject(s), false, s);
});

test('开关关时 BFF 客户端不发任何请求（按 404 返回）；打开后只请求 BFF 的复测路由；作答请求体只有票据与作答', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], r: process.env['STARBUDDY_RETEST_UI'], fetch: globalThis.fetch };
  const calls: { url: string; body: string | undefined }[] = [];
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body as string | undefined });
    return new Response(JSON.stringify({ subject: 'math', entries: [] }), { status: 200 });
  }) as typeof fetch;
  try {
    const api = await import('../src/lib/retest-api.ts');
    const sess = { token: 'f'.repeat(64), csrf: 'c'.repeat(64) } as never;
    process.env['STARBUDDY_ENV'] = 'test';
    delete process.env['STARBUDDY_RETEST_UI'];
    for (const r of [await api.fetchRetestQueue(sess, S, 'math'), await api.nextRetestItem(sess, S, 'math', E1),
                     await api.answerRetest(sess, S, 'math', TICKET, { text: '1' })]) {
      assert.deepEqual(r, { ok: false, status: 404, code: 'NOT_FOUND', reason: null });
    }
    assert.equal(calls.length, 0);
    process.env['STARBUDDY_RETEST_UI'] = 'retest';
    assert.equal((await api.fetchRetestQueue(sess, S, 'chinese')).ok, false);
    assert.equal((await api.nextRetestItem(sess, S, 'math', 'x')).ok, false);
    assert.equal(calls.length, 0);
    await api.fetchRetestQueue(sess, S, 'math');
    assert.deepEqual(calls.map((c) => c.url), [`http://127.0.0.1:8787/bff/v0/students/${S}/retest/math`]);
    // 写请求要读 next/headers（单测里解析不了），作答请求体按源码核对：只有 {ticket, answer}，只有 call 一处 fetch，先过开关
    const src = code(read('src/lib/retest-api.ts'));
    assert.equal(src.match(/fetch\(/g)?.length, 1);
    assert.match(src, /async function call<T>\([^)]*\)[^{]*\{\n\s+if \(!retestEnabled\(\)\) return OFF;/);
    assert.match(src, /\$\{bffBaseUrl\(\)\}\/bff\/v0\/students\/\$\{student\}\/retest\/\$\{subject\}\$\{tail\}/);
    assert.match(src, /'POST', '\/answer', \{ ticket, answer \}\)/);
    assert.ok(!/idempotency|usage_id|purpose|channel/.test(src));
  } finally {
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.r === undefined) delete process.env['STARBUDDY_RETEST_UI']; else process.env['STARBUDDY_RETEST_UI'] = saved.r;
    globalThis.fetch = saved.fetch;
  }
});

test('列表白名单：只取条目 ID、节点、层次、类别与状态；任务原因、空原因编码、掌握等丢弃；节点编号不合口径只隐藏那一格；形状不对整页不给', () => {
  const wire = (o: Record<string, unknown> = {}) => ({ entry_id: E1, node_id: 'MATH.SYN.N1', cognitive_level: 'apply', kind: 'retest', status: 'due',
                                                       reason: 'scheduled', empty_reason: null, p_mastery: 0.5, ...o });
  const out = pickRetestQueue({ subject: 'math', entries: [wire(), wire({ kind: 'verify', status: 'unavailable', node_id: 'bad node' })] })!;
  assert.deepEqual(out, [{ entry_id: E1, node_id: 'MATH.SYN.N1', cognitive_level: 'apply', kind: 'retest', status: 'due' },
                         { entry_id: E1, node_id: null, cognitive_level: 'apply', kind: 'verify', status: 'unavailable' }]);
  for (const bad of [{ kind: 'scheduled' }, { status: 'done' }, { entry_id: 'x' }, { cognitive_level: 'genius' }]) {
    assert.equal(pickRetestQueue({ entries: [wire(bad)] }), null, JSON.stringify(bad));
  }
  assert.equal(pickRetestQueue({ entries: 'x' }), null);
});

test('取题：只认 sbr1 票据（再练票据不认）、类别与题面；不可用只给 empty；作答结果只给结论与 due / done，待复核不给结论', () => {
  const ready = { status: 'ready', entry_id: E1, kind: 'verify', ticket: TICKET, item_ref: 'aya:x', item: { format: 'fill', stem: '（合成）' } };
  const n = pickRetestNext(ready, E1)!;
  assert.deepEqual(n, { kind: 'ready', ready: { entry_id: E1, kind: 'verify', ticket: TICKET, item: { format: 'fill', stem: '（合成）' } } });
  assert.equal(pickRetestNext({ ...ready, ticket: `sbp1.${'A'.repeat(40)}` }, E1), null);
  assert.equal(pickRetestNext({ ...ready, kind: 'transfer_check' }, E1), null);
  assert.deepEqual(pickRetestNext({ status: 'unavailable', empty_reason: 'no_new_family' }, E1), { kind: 'empty' });
  assert.deepEqual(pickRetestAnswer({ verdict: 'correct', entry: { entry_id: E1, status: 'done' } }, E1),
                   { verdict: 'correct', pending_review: false, entry: { entry_id: E1, status: 'done' } });
  assert.deepEqual(pickRetestAnswer({ verdict: 'incorrect', pending_review: true, entry: { entry_id: E1, status: 'due' } }, E1),
                   { verdict: null, pending_review: true, entry: { entry_id: E1, status: 'due' } });
  for (const bad of [{ verdict: 'correct', entry: { entry_id: E1, status: 'closed' } }, { verdict: 'mastered', entry: { entry_id: E1, status: 'done' } }]) {
    assert.equal(pickRetestAnswer(bad, E1), null);
  }
  assert.equal(retestUiError({ ok: false, status: 409, code: 'RETEST_NOT_DUE', reason: null }, 'answer'), 'not_due');
  assert.equal(retestUiError({ ok: false, status: 402, code: 'QUOTA_UNAVAILABLE', reason: 'curfew' }, 'next'), 'curfew');
  assert.equal(retestUiError({ ok: false, status: 503, code: 'RETEST_UNAVAILABLE', reason: null }, 'answer'), 'unavailable');
  assert.equal(retestUiError({ ok: false, status: 503, code: 'UPSTREAM_UNAVAILABLE', reason: null }, 'answer'), 'retry');
  assert.equal(retestUiError({ ok: false, status: 503, code: 'SUBJECT_BUSY', reason: null }, 'answer'), 'rate_limited', 'PLT-16 闸门被拒的作答：留票据原样重发');
  assert.equal(retestUiError({ ok: false, status: 503, code: 'SUBJECT_BUSY', reason: null }, 'next'), 'unavailable', 'PLT-16 闸门被拒的取题：稍后再试');
});

test('复测文案全部是 {{TODO_COPY:retest.*}} 占位，已并入壳文案；编码映射齐全；条目类别是纯占位；没有关于学生的陈述与评价词', () => {
  assert.ok(ALL_RETEST_COPY.length > 30);
  for (const s of ALL_RETEST_COPY) assert.match(s, /^\{\{TODO_COPY:retest\.[a-z0-9_.]+\}\}/, s);
  for (const s of ALL_RETEST_COPY) assert.ok(ALL_SHELL_COPY.includes(s), s);
  for (const v of Object.values(RETEST_KIND)) assert.match(v, /^\{\{TODO_COPY:retest\.kind\.\d\}\}$/, v);
  assert.deepEqual(Object.keys(RETEST_KIND).sort(), [...KINDS].sort());
  assert.deepEqual(Object.keys(RETEST_STATUS).sort(), [...ENTRY_STATUSES].sort());
  assert.deepEqual(Object.keys(RETEST_AFTER).sort(), [...AFTER_STATUSES].sort());
  assert.deepEqual(Object.keys(RETEST_LEVEL).sort(), [...LEVELS].sort());
  assert.deepEqual(Object.keys(RETEST_VERDICT).sort(), VERDICTS.filter((v) => v !== 'needs_review').sort());
  assert.deepEqual(Object.keys(RETEST_ERROR).sort(),
                   ['curfew', 'daily_limit_reached', 'expired', 'invalid_answer', 'no_entitlement', 'not_due', 'rate_limited', 'retry', 'ticket_used',
                    'unavailable']);
  for (const s of ALL_RETEST_COPY) {
    assert.ok(!/粗心|马虎|失误|不认真|薄弱|弱项|不够|较差|掌握|错因|星|聪明|笨|天赋|加油|棒|忘|退步|进步|其实会|你会|你不会/.test(s), s);
    for (const c of ['scheduled', 'early', 'decay', 'verify_', 'transfer_check', 'no_new_family', 'no_source_item', 'hint_followup']) {
      assert.ok(!s.includes(c), `${s} 含编码 ${c}`);
    }
  }
  assert.ok(RETEST.pendingReview.includes('retest.pending_review'));
});

test('页面与 action 先过开关（读会话之前）；只认学生本人；入口组件关时返回 null；科目页挂入口；家长端不引用复测', () => {
  const pages = code(read('src/retest/RetestPages.tsx'));
  assert.match(pages, /export function RetestLink[^]*?\{\n\s+if \(!retestEnabled\(\) \|\| !isRetestSubject\(subject\)\) return null;/);
  for (const fn of ['RetestListPage', 'RetestItemPage']) {
    const body = pages.slice(pages.indexOf(`export async function ${fn}`));
    const gate = body.indexOf('if (!retestEnabled()) notFound();');
    assert.ok(gate > 0 && gate < body.indexOf('await requireOwnStudent(studentId)'), `${fn}：开关闸门要在读会话之前`);
  }
  assert.ok(!/requireStudent\(|requireGuardian\(/.test(pages), '只认学生本人');
  const actions = code(read('src/retest/actions.ts'));
  assert.match(actions, /^'use server';/);
  assert.match(actions, /async function begin\([^)]*\) \{\n\s+if \(!retestEnabled\(\)\) return null;/);
  assert.ok(actions.indexOf('retestEnabled()') < actions.indexOf('await actionSession'));
  for (const p of ['s/[studentId]/learn/[subject]/retest/page.tsx', 's/[studentId]/learn/[subject]/retest/[entryId]/page.tsx']) {
    assert.match(read(`src/app/${p}`), /return <(RetestListPage|RetestItemPage) /, p);
  }
  assert.match(read('src/app/s/[studentId]/learn/[subject]/page.tsx'), /<RetestLink studentId=\{studentId\} subject=\{m\.code\} \/>/);
  for (const f of src.filter((x) => x.path.startsWith('src/app/family/') || x.path.startsWith('src/subjects/'))) {
    assert.ok(!/retest\/|RetestLink|RETEST\.entry/.test(code(f.text)), f.path);
  }
});

test('组件里没有中文文案；不渲染判断句、任务原因与票据；不写浏览器存储；题面放在 data-item-text 里；再提交原样发首次作答快照', () => {
  const tsx = src.filter((f) => f.path.startsWith('src/retest/') && f.path.endsWith('.tsx'));
  assert.equal(tsx.length, 2);
  for (const f of [...tsx, ...src.filter((x) => x.path.startsWith('src/app/s/[studentId]/learn/[subject]/retest/'))]) {
    assert.deepEqual(code(f.text).match(/[一-鿿]+/g) ?? [], [], f.path);
    assert.ok(!/data-claim-id|reason|purpose|mastery|confidence|evidence|transfer_check/.test(code(f.text)), f.path);
    assert.ok(!/localStorage|sessionStorage|indexedDB/.test(code(f.text)), f.path);
  }
  // 复测的组件、页面与 action 不自己发请求，只经 retest-api（服务端）与 server action（客户端组件）
  for (const f of src.filter((x) => x.path.startsWith('src/retest/'))) {
    assert.ok(!/\bfetch\(|XMLHttpRequest|EventSource|WebSocket|lib\/(bff|shell-api|trust-api)\.ts/.test(code(f.text)), f.path);
  }
  assert.match(read('src/retest/RetestItem.tsx'), /^'use client';/);
  assert.ok(!/retest-api/.test(code(read('src/retest/RetestItem.tsx'))), '客户端组件不引用服务端客户端');
  const item = code(read('src/retest/RetestItem.tsx'));
  assert.ok(!/\{ready\.ticket\}|\{r\.ready\.ticket\}|type="hidden"|name="ticket"/.test(item), '票据只在组件内存');
  assert.match(item, /data-item-text="stem">\{item\.stem\}/);
  assert.match(item, /result\.pending_review \|\| !result\.verdict \? C\.pendingReview : RETEST_VERDICT\[result\.verdict\]/);
  assert.match(item, /const answer: Answer \| null = sent\.current\s*\?\?/);
  assert.match(item, /if \(!resubmitsSnapshot\(r\.error\)\) sent\.current = null;/);
  assert.match(item, /<input id=\{ids\.answer\}[^>]*disabled=\{locked\}/);
});

test('学生壳“知道了”回跳白名单：复测只在数学、道法之后', () => {
  const m = /const BACK = (\/.*\/);/.exec(read('src/app/s/[studentId]/actions.ts'))!;
  const back = new Function(`return ${m[1]}`)() as RegExp;
  for (const ok of [`/s/${S}/learn/math/retest`, `/s/${S}/learn/politics/retest/${E1}`]) assert.ok(back.test(ok), ok);
  for (const bad of [`/s/${S}/learn/chinese/retest`, `/s/${S}/learn/math/retest/x`, `/s/${S}/learn/retest`]) assert.ok(!back.test(bad), bad);
});
