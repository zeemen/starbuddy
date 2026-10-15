// 今日计划跨科编排前端（PLT-11，specs/today-plan-orchestration-plt11 范围第 3 条）的单测与静态检查：
//   - 开关 STARBUDDY_TODAY_ORCH：默认关；只在 dev / test 可开；staging、pilot、生产一律关；
//   - “去做”：复测条目 → 复测页、再练条目 → 再练页（对应开关打开、学科有入口时），计划任务与引用为空 → 科目页；格式不对的引用不拼进地址；
//   - 文案：全部是 {{TODO_COPY:today.*}} 占位并已并入壳文案（禁语、禁用推断词表、比较营销词表的扫描在 tests/shell.test.ts）；
//   - 今日页只在开关打开、学生本人、BFF 返回了 ordered 时渲染跨科列表；渲染不出现掌握、置信度、证据、来源与原因编码；
//     关于学生的句子仍只在 TaskList 里以 data-claim-id 渲染（家长端页面不引用跨科列表）。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { ALL_SHELL_COPY, ALL_TODAY_ORCH_COPY, TODAY_MARK, TODAY_ORCH, TODAY_ORCH_KIND, WEEKLY } from '../src/shell/copy.ts';
import { todayOrchEnabled } from '../src/shell/today-orch.ts';
import { goHref } from '../src/today/orch.ts';
import { isPlanAction, PLAN_ACTIONS, showPlanMarks } from '../src/today/plan-mark.ts';
import { planMarkKey, planMarkTicket, verifyPlanMarkTicket } from '../src/today/plan-mark-server.ts';

const WEB = new URL('..', import.meta.url).pathname;
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const src = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

const S = '00000000-0000-4000-8000-00000000a001';
const E = '00000000-0000-4000-8000-00000000e001';
const ON = { STARBUDDY_ENV: 'test', STARBUDDY_TODAY_ORCH: 'today_orch', STARBUDDY_RETEST_UI: 'retest', STARBUDDY_PRACTICE: 'practice' };

test('开关：默认关；只在 dev / test 可开；staging、pilot、生产一律关；与再练、复测开关互不影响', () => {
  assert.equal(todayOrchEnabled({}), false);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_RETEST_UI: 'retest', STARBUDDY_PRACTICE: 'practice' }), false);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_TODAY_ORCH: 'today_orch' }), true);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_TODAY_ORCH: ' today_orch ' }), true);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_TODAY_ORCH: 'retest' }), false);
  for (const env of ['staging', 'pilot', 'production', '']) {
    assert.equal(todayOrchEnabled({ STARBUDDY_ENV: env, STARBUDDY_TODAY_ORCH: 'today_orch' }), false, env);
  }
});

test('“去做”：复测 → 复测页，再练 → 再练页；计划任务、引用为空、对应开关关、学科没有入口 → 科目页；格式不对不拼地址', () => {
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: E }, ON), `/s/${S}/learn/math/retest/${E}`);
  assert.equal(goHref(S, { source: 'practice', subject: 'politics', entry_id: E }, ON), `/s/${S}/learn/politics/practice/${E}`);
  assert.equal(goHref(S, { source: 'plan', subject: 'math', entry_id: null }, ON), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'plan', subject: 'math', entry_id: E }, ON), `/s/${S}/learn/math`, '计划任务不链到条目');
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: null }, ON), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'retest', subject: 'chinese', entry_id: E }, ON), `/s/${S}/learn/chinese`, '语文没有复测入口');
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: E }, { ...ON, STARBUDDY_RETEST_UI: '' }), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'practice', subject: 'math', entry_id: E }, { ...ON, STARBUDDY_PRACTICE: '' }), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: '../../x' }, ON), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'retest', subject: '../admin', entry_id: E }, ON), null);
  assert.equal(goHref('not-a-student', { source: 'plan', subject: 'math', entry_id: null }, ON), null);
});

test('文案：全部是 {{TODO_COPY:today.*}} 占位，已并入壳文案；类别只给占位键、不附提示', () => {
  for (const s of ALL_TODAY_ORCH_COPY) {
    assert.match(s, /^\{\{TODO_COPY:today\.[a-z_.]+\}\}/, s);
    assert.ok(ALL_SHELL_COPY.includes(s), s);
  }
  for (const s of Object.values(TODAY_ORCH_KIND)) assert.match(s, /^\{\{TODO_COPY:today\.orch\.kind\.[a-z_]+\}\}$/, s);
  assert.deepEqual(Object.keys(TODAY_ORCH_KIND).sort(), ['diagnose', 'lesson', 'mock_exam', 'practice', 'retest', 'review', 'verify']);
  assert.ok(!/掌握|置信|证据|薄弱|粗心/.test(Object.values(TODAY_ORCH).join('')));
});

test('今日页：只在开关打开、学生本人、BFF 返回了 ordered 时渲染跨科列表；理由仍只在 TaskList 以 data-claim-id 渲染', () => {
  const page = code(read('src/app/s/[studentId]/today/page.tsx'));
  assert.match(page, /todayOrchEnabled\(\) && ps\.me\.account_kind === 'student' && ordered !== undefined/);
  assert.match(page, /import \{ OrderedTaskList \} from '\.\.\/\.\.\/\.\.\/\.\.\/today\/OrderedTaskList\.tsx'/);
  const ordered = code(read('src/today/OrderedTaskList.tsx'));
  assert.match(ordered, /<Reason r=\{t\.rationale\} audience="student"/);
  assert.match(ordered, /import \{ Reason \} from '\.\.\/shell\/TaskList\.tsx'/);
  // 跨科列表不渲染内部字段：来源、条目引用只进链接地址，理由编码、掌握、置信度、证据不出现；也不自己渲染判断句
  for (const k of ['t.source}', 't.entry_id}', 'rationale_code', 'p_mastery', 'confidence', 'evidence', 'priority', 'node_id', 'data-claim-id']) {
    assert.ok(!ordered.includes(k), k);
  }
  assert.deepEqual(ordered.match(/[一-鿿]+/g) ?? [], [], '组件里没有中文文案');
  // 家长端与壳目录不引用跨科列表与“去做”地址（壳目录不链再练、复测页，tests/practice.test.ts）
  for (const f of src.filter((x) => /^src\/(app\/family|shell|subjects)\//.test(x.path))) {
    assert.ok(!/OrderedTaskList|today\/orch/.test(code(f.text)), f.path);
  }
  assert.ok(src.filter((x) => /OrderedTaskList/.test(code(x.text))).map((x) => x.path).sort().join(',')
    === 'src/app/s/[studentId]/today/page.tsx,src/today/OrderedTaskList.tsx');
});

// ---------- 今日计划“完成”“先跳过”（PLT-15 PR2，specs/today-plan-snapshot-plt15 范围第 3 条） ----------
const T1 = '00000000-0000-4000-8000-00000000f001';
const T2 = '00000000-0000-4000-8000-00000000f002';

test('“完成”“先跳过”只在当天快照已冻结、条目带合格任务 ID 时显示；not_frozen、没有 snapshot、没有或不合格的任务 ID 都不显示', () => {
  assert.equal(showPlanMarks('frozen', T1), true);
  for (const [snap, task] of [['not_frozen', T1], [undefined, T1], ['frozen', undefined], ['frozen', ''], ['frozen', 'T1'],
                              ['frozen', T1.toUpperCase()], ['frozen', `${T1}/x`], ['FROZEN', T1]] as const) {
    assert.equal(showPlanMarks(snap, task), false, `${snap} ${task}`);
  }
  assert.deepEqual([...PLAN_ACTIONS], ['plan_done', 'plan_skip']);
  assert.ok(isPlanAction('plan_done') && isPlanAction('plan_skip'));
  for (const a of ['session_start', 'plan_done ', 'PLAN_DONE', '', null]) assert.equal(isPlanAction(a), false, String(a));
});

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const KEY = Buffer.alloc(32, 7);  // 合成密钥（只在本测试里）
const CTX = { key: KEY, token: 'syn-token-a', student: S };

test('幂等键 = H(任务 ID, 动作, nonce)：同一票据内确定性（重复点击、重试同键）；换任务、动作、nonce 都不同；形状是 UUIDv4', () => {
  const n1 = 'AAAAAAAAAAAAAAAAAAAAAA';
  const n2 = 'BBBBBBBBBBBBBBBBBBBBBB';
  const k = planMarkKey(T1, 'plan_done', n1);
  assert.equal(planMarkKey(T1, 'plan_done', n1), k, '确定性');
  assert.match(k, V4);
  const all = new Set([k, planMarkKey(T1, 'plan_skip', n1), planMarkKey(T2, 'plan_done', n1), planMarkKey(T1, 'plan_done', n2)]);
  assert.equal(all.size, 4);
  assert.notEqual(k, T1, '不是任务 ID 本身');
  const many = Array.from({ length: 200 }, (_, i) => planMarkKey(`00000000-0000-4000-8000-${String(i).padStart(12, '0')}`, 'plan_done', n1));
  assert.equal(new Set(many).size, 200);
  for (const m of many) assert.match(m, V4);
});

test('票据：每次签都不同；只对签发时的会话、学生、任务有效；篡改、换密钥、超长一律无效', () => {
  const t = planMarkTicket(CTX, T1);
  const nonce = verifyPlanMarkTicket(t, CTX, T1);
  assert.ok(nonce && /^[A-Za-z0-9_-]{22}$/.test(nonce));
  assert.equal(t.split('.')[0], nonce);
  assert.notEqual(planMarkTicket(CTX, T1), t, '每次随机');
  assert.ok(!t.includes(CTX.token) && !t.includes(T1) && !t.includes(S), '票据里没有令牌、任务 ID、学生 ID');
  assert.equal(verifyPlanMarkTicket(t, CTX, T2), null, '换任务');
  assert.equal(verifyPlanMarkTicket(t, { ...CTX, token: 'syn-token-b' }, T1), null, '换会话');
  assert.equal(verifyPlanMarkTicket(t, { ...CTX, student: '00000000-0000-4000-8000-00000000a002' }, T1), null, '换学生');
  assert.equal(verifyPlanMarkTicket(t, { ...CTX, key: Buffer.alloc(32, 8) }, T1), null, '换密钥');
  const [n, sig] = t.split('.') as [string, string];
  for (const bad of [`${n}.${sig.slice(0, -1)}${sig.endsWith('A') ? 'B' : 'A'}`, `${n.slice(0, -1)}${n.endsWith('A') ? 'B' : 'A'}.${sig}`,
                     `${n}.${sig}.x`, n, '', `${'A'.repeat(60)}.${sig}`, null, 42]) {
    assert.equal(verifyPlanMarkTicket(bad, CTX, T1), null, String(bad));
  }
});

test('文案：“完成”“先跳过”“已记下”是 {{TODO_COPY:today.mark.*}} 占位、中性短语；不评价学生、不说掌握或核实', () => {
  assert.deepEqual(Object.keys(TODAY_MARK).sort(), ['done', 'recorded', 'skip']);
  for (const s of Object.values(TODAY_MARK)) {
    assert.match(s, /^\{\{TODO_COPY:today\.mark\.[a-z]+\}\} /, s);
    assert.ok(ALL_SHELL_COPY.includes(s), s);
    assert.ok(!/掌握|核实|真棒|厉害|加油|偷懒|又|总是|应该|必须|点点|阿鸦|妙笔|[!！]/.test(s), s);
  }
  // 周报的计划栏：说明是自己标记的记录，不说成已掌握或已核实完成
  for (const s of [WEEKLY.planDone, WEEKLY.planSkipped]) {
    assert.match(s, /自己标记/, s);
    assert.ok(!/掌握|核实|学会|达标/.test(s), s);
  }
});

test('server action：CSRF、本人、格式核对之后才写；幂等键用 planMarkKey；只经 postEventQuietly；失败静默（不回原因）', () => {
  const a = code(read('src/app/s/[studentId]/actions.ts'));
  const body = a.slice(a.indexOf('export async function markPlanAction'));
  const order = ['actionSession(f.get(\'csrf\'))', 'isPlanAction(action)', 'await pageSession()', 'verifyPlanMarkTicket(', 'postEventQuietly('];
  const at = order.map((k) => body.indexOf(k));
  assert.ok(at.every((i) => i >= 0), at.join(','));
  assert.deepEqual([...at].sort((x, y) => x - y), at, '先 CSRF、再格式、再本人、再票据、最后写');
  assert.match(body, /ps\.me\.account_kind !== 'student' \|\| ps\.me\.student_id !== student\) return SILENT;/);
  assert.ok(!/ps\.session\.token !== s\.token/.test(body), '不再比较两个同源的令牌');
  assert.match(body, /if \(!nonce\) return SILENT;/);
  assert.match(body, /\{ kind: action, payload: \{ plan_task_id: task \} \}, planMarkKey\(task, action, nonce\)\)/);
  assert.match(body, /return ok \? \{ code: 'recorded', action, ticket: planMarkTicket\(ctx, task\) \} : SILENT;/);
  assert.ok(!/throw|redirect\(|console\./.test(body), '失败静默：不抛、不跳转、不记日志');
  // 按钮组件：客户端组件，只提交给 server action；不直连 BFF、不读会话、不碰浏览器存储；中文只来自 copy.ts
  const marks = code(read('src/today/PlanMarks.tsx'));
  assert.match(marks, /^'use client';/);
  assert.match(marks, /useActionState<PlanMarkResult \| null, FormData>\(markPlanAction, null\)/);
  assert.ok(!/bff|fetch\(|localStorage|sessionStorage|lib\/session|lib\/events|plan-mark-server/.test(marks));
  assert.deepEqual(marks.match(/[一-鿿]+/g) ?? [], []);
  // 幂等键模块只在服务端：没有客户端组件引用它
  for (const f of src.filter((x) => /^['"]use client['"]/m.test(x.text))) assert.ok(!/plan-mark-server/.test(f.text), f.path);
  // 跨科列表：只在拿到 csrf（学生本人）且 showPlanMarks 时渲染按钮；今日页只在本人会话传 csrf
  const ordered = code(read('src/today/OrderedTaskList.tsx'));
  assert.match(ordered, /\{csrf && showPlanMarks\(snapshot, t\.task_id\) && tickets\?\.\[t\.task_id\]\s*\? <PlanMarks studentId=\{studentId\} taskId=\{t\.task_id\} csrf=\{csrf\} ticket=\{tickets\[t\.task_id\]!\} \/> : null\}/);
  // 按钮换票据：记下后用 action 返回的新票据，没记下保留原票据
  assert.match(marks, /<input type="hidden" name="ticket" value=\{state\?\.ticket \?\? current\} \/>/);
  assert.match(marks, /useEffect\(\(\) => \{ if \(state\?\.ticket\) setCurrent\(state\.ticket\); \}, \[state\]\);/);
  const page = code(read('src/app/s/[studentId]/today/page.tsx'));
  assert.match(page, /const ownStudent = ps\.me\.account_kind === 'student' && ps\.me\.student_id === studentId;/);
  assert.match(page, /const markKey = ownStudent && showOrch && ordered \? presenceKey\(\) : null;/);
  assert.match(page, /\{\.\.\.\(ownStudent \? \{ csrf: ps\.session\.csrf, tickets \} : \{\}\)\}/);
  // 家长端不引用按钮
  for (const f of src.filter((x) => /^src\/app\/family\//.test(x.path))) assert.ok(!/PlanMarks|markPlanAction|plan-mark/.test(code(f.text)), f.path);
});
