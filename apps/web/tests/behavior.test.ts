// SIE-05 PR2（specs/sie05-silent-metrics/spec.md 范围第 4、5 条；审查后本 PR 只交付 session_start / session_end，plan_done / plan_skip 撤下）：
// 学生壳真实页面写会话行为事件的单测与静态检查。
//   - 会话起止的纯函数（src/behavior/presence.ts）：入口只取类别；超时、补记、一天时间窗；开始事件没记下时用同一幂等键重试；
//   - 签名 cookie（src/behavior/presence-server.ts）：签名覆盖全部字段、账号类型与会话令牌哈希；任何一项被改或换了会话都验签失败；
//     正式环境没配密钥拒绝写入，dev / test 进程内随机；
//   - 只经 BFF 现有事件接口（src/lib/events.ts）写，对学生静默、服务端按结果类别计数、配置错误不吞；路由同源 + JSON + 长度 + CSRF + 本人、一律 204；
//   - 家长端不挂；退出登录先补记再删 cookie；会话起止模块不写 plan_done / plan_skip（PLT-15 PR2 起只由今日页的 server action 写，
//     单测与静态检查在 tests/today-orch.test.ts）。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { alive, decide, endOf, entryOf, HEARTBEAT_SEC, IDLE_SEC, isEntry, MAX_AGE_SEC, type Presence } from '../src/behavior/presence.ts';
import {
  PRESENCE_KEY_ENV, presenceCookieName, presenceCookieOptions, presenceKey, signPresence, verifyPresence, type SignContext,
} from '../src/behavior/presence-server.ts';
import { ALL_SHELL_COPY } from '../src/shell/copy.ts';

const WEB = new URL('..', import.meta.url).pathname;
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const src = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

const S = '00000000-0000-4000-8000-00000000a001';
const OTHER = '00000000-0000-4000-8000-00000000a002';
const K1 = '00000000-0000-4000-8000-00000000e001';
const K2 = '00000000-0000-4000-8000-00000000e002';
const NOW = 1_800_000_000;
const P = (o: Partial<Presence> = {}): Presence =>
  ({ student: S, start: NOW - 600, last: NOW - 60, entry: 'today', started: true, startKey: K1, endKey: K2, ...o });

test('入口只取类别：今日、星图、科目（科目列表与各科页面）、其他；不是本人路径一律 other', () => {
  assert.equal(entryOf(`/s/${S}/today`, S), 'today');
  assert.equal(entryOf(`/s/${S}/star-map`, S), 'star_map');
  for (const p of ['learn', 'learn/math', 'learn/math/practice', `learn/math/retest/${K1}`]) assert.equal(entryOf(`/s/${S}/${p}`, S), 'subject', p);
  for (const p of ['plan', 'weekly', 'disputes', `why/${K1}`, 'today/x', 'learning', '']) assert.equal(entryOf(`/s/${S}/${p}`, S), 'other', p);
  assert.equal(entryOf(`/s/${OTHER}/today`, S), 'other');
  assert.equal(entryOf(`/s/${S}/today`, 'not-a-uuid'), 'other');
  assert.ok(isEntry('today') && isEntry('star_map') && isEntry('subject') && isEntry('other'));
  assert.ok(!isEntry('onboarding') && !isEntry('star-map') && !isEntry(1));
});

test('会话：没有或换了学生 → 新会话；IDLE 内 → 接着（开始没记下时重试）；超时 → 补记已记下开始的上一段再开始', () => {
  assert.deepEqual(decide(null, S, NOW), { kind: 'restart', end: null });
  const fresh = P({ start: NOW - IDLE_SEC - 600, last: NOW - IDLE_SEC });
  assert.deepEqual(decide(fresh, S, NOW), { kind: 'continue', next: { ...fresh, last: NOW } });
  assert.deepEqual(decide({ ...fresh, started: false }, S, NOW), { kind: 'retry_start', next: { ...fresh, started: false, last: NOW } });
  assert.deepEqual(decide({ ...fresh, student: OTHER }, S, NOW), { kind: 'restart', end: null }, '别的学生的会话不补记');
  const stale = P({ start: NOW - 3600, last: NOW - IDLE_SEC - 1 });
  assert.deepEqual(decide(stale, S, NOW), { kind: 'restart', end: { durationSec: 3600 - IDLE_SEC - 1, at: NOW - IDLE_SEC - 1, key: K2 } });
  assert.deepEqual(decide({ ...stale, started: false }, S, NOW), { kind: 'restart', end: null }, '开始没记下的会话不补记结束');
  assert.deepEqual(decide({ ...stale, start: NOW - MAX_AGE_SEC, last: NOW - MAX_AGE_SEC + 60 }, S, NOW), { kind: 'restart', end: null });
  assert.deepEqual(decide({ ...fresh, last: NOW + 5 }, S, NOW), { kind: 'restart', end: null }, '时间在未来不接着、不补记');
  const long = P({ start: NOW - MAX_AGE_SEC, last: NOW - 10 });
  assert.equal(alive(long, S, NOW), false);
  assert.deepEqual(decide(long, S, NOW), { kind: 'restart', end: { durationSec: MAX_AGE_SEC - 10, at: NOW - 10, key: K2 } });
  // 退出登录时按“现在”结束；发生时间不早于开始、不晚于现在
  assert.deepEqual(endOf(P(), S, NOW, NOW), { durationSec: 600, at: NOW, key: K2 });
  assert.equal(endOf(P(), S, NOW, NOW + 1), null);
  assert.equal(endOf(P(), S, NOW, NOW - 601), null);
  assert.ok(HEARTBEAT_SEC < IDLE_SEC, '有活动时的报告间隔必须小于超时');
});

test('签名 cookie：往返一致；任何一项被改、换了会话令牌或密钥都验签失败；格式不对一律当没有', () => {
  const ctx: SignContext = { key: Buffer.from('k'.repeat(32)), accountKind: 'student', token: 'synthetic-session-a' };
  const p = P({ entry: 'star_map', started: false });
  const v = signPresence(p, ctx);
  assert.match(v, new RegExp(`^v1\\.${S}\\.${p.start}\\.${p.last}\\.star_map\\.0\\.${K1}\\.${K2}\\.[A-Za-z0-9_-]{43}$`));
  assert.ok(!v.includes('synthetic-session-a'), '会话令牌不进 cookie');
  assert.deepEqual(verifyPresence(v, ctx), p);
  const parts = v.split('.');
  const swaps: [number, string][] = [[1, OTHER], [2, String(p.start - 1)], [3, String(p.last + 1)], [4, 'today'], [5, '1'], [6, K2], [7, K1],
    [8, `${parts[8]!.slice(0, -1)}${parts[8]!.endsWith('A') ? 'B' : 'A'}`]];
  for (const [i, x] of swaps) {
    const t = [...parts];
    t[i] = x;
    assert.equal(verifyPresence(t.join('.'), ctx), null, `第 ${i} 项被改`);
  }
  assert.equal(verifyPresence(v, { ...ctx, token: 'synthetic-session-b' }), null, '换了会话');
  assert.equal(verifyPresence(v, { ...ctx, key: Buffer.from('j'.repeat(32)) }), null, '换了密钥');
  for (const bad of [undefined, null, '', 'x', parts.slice(0, 8).join('.'), `${v}.x`, `v2${v.slice(2)}`, v + 'x'.repeat(300)]) {
    assert.equal(verifyPresence(bad, ctx), null, String(bad).slice(0, 40));
  }
  // 签名本身对、但开始晚于最近活动：也当没有
  assert.equal(verifyPresence(signPresence(P({ start: NOW, last: NOW - 1 }), ctx), ctx), null);
});

test('密钥：配了（至少 32 字符）就用；dev / test 没配时进程内随机且稳定；staging、pilot、生产没配或太短一律 null（拒绝写入）', () => {
  assert.deepEqual(presenceKey({ STARBUDDY_ENV: 'pilot', [PRESENCE_KEY_ENV]: 'p'.repeat(32) }), Buffer.from('p'.repeat(32)));
  for (const env of ['staging', 'pilot', 'production', '']) {
    assert.equal(presenceKey({ STARBUDDY_ENV: env }), null, env);
    assert.equal(presenceKey({ STARBUDDY_ENV: env, [PRESENCE_KEY_ENV]: 'short' }), null, env);
  }
  const a = presenceKey({ STARBUDDY_ENV: 'test' });
  assert.ok(a && a.length === 32);
  assert.deepEqual(presenceKey({ STARBUDDY_ENV: 'dev' }), a);
  assert.ok(!/TOKEN/.test(PRESENCE_KEY_ENV));
  assert.equal(presenceCookieName({ STARBUDDY_ENV: 'test' }), 'sb_presence');
  assert.equal(presenceCookieName({ STARBUDDY_ENV: 'pilot' }), '__Host-sb_presence');
  assert.deepEqual(presenceCookieOptions({ STARBUDDY_ENV: 'production' }), { httpOnly: true, sameSite: 'lax', path: '/', secure: true, maxAge: MAX_AGE_SEC });
  // 密钥只在服务端读：客户端组件不引用签名模块
  for (const f of src.filter((x) => /^['"]use client['"]/m.test(x.text))) assert.ok(!/presence-server|STARBUDDY_PRESENCE_HMAC_KEY/.test(f.text), f.path);
});

test('只经 BFF 现有事件接口写行为事件：静默、409 算已记下、按结果类别计数（不含学生 ID）、配置错误不吞；计划事件只由今日页的 server action 写', () => {
  const events = code(read('src/lib/events.ts'));
  assert.match(events, /post\(account, student, 'events', \{ kind: e\.kind, payload: e\.payload, occurred_at: occurredAt\.toISOString\(\), idempotency_key: key \}\)/);
  assert.match(events, /bffBaseUrl\(\);\s*try \{/, 'bffBaseUrl 在 try 之外');
  // 403 与 422 单独计，其余按类别（PLT-15 PR2 审查建议）
  assert.match(events, /countWrite\(e\.kind, r\.status === 403 \|\| r\.status === 422 \? String\(r\.status\) : `\$\{Math\.floor\(r\.status \/ 100\)\}xx`\);/);
  assert.match(events, /return r\.ok \|\| r\.status === 409;\s*\} catch \{\s*countWrite\(e\.kind, 'network'\);\s*return false;/);
  assert.match(events, /console\.info\(`behavior_event write kind=\$\{kind\} result=\$\{result\} count=\$\{n\}`\)/);
  const writers = src.filter((f) => /postEventQuietly\(/.test(code(f.text)) && f.path !== 'src/lib/events.ts').map((f) => f.path).sort();
  assert.deepEqual(writers, ['src/app/s/[studentId]/actions.ts', 'src/app/s/[studentId]/presence/route.ts', 'src/behavior/presence-server.ts']);
  // PLT-15 PR2 起计划完成 / 跳过接入：只在事件客户端（类型）、今日页的 server action 与“完成”“先跳过”的纯函数、按钮组件里出现；
  // 会话起止的模块（behavior/、presence 路由）与壳仍不写计划事件。周报的计划栏是既有的只读展示（src/trust/），不在此列
  const planWriters = ['src/lib/events.ts', 'src/app/s/[studentId]/actions.ts', 'src/today/plan-mark.ts', 'src/today/PlanMarks.tsx'];
  for (const f of src.filter((x) => /^src\/(behavior|today|shell|lib|app\/s)\//.test(x.path) && x.path !== 'src/shell/copy.ts')) {
    if (planWriters.includes(f.path)) continue;
    assert.ok(!/plan_done|plan_skip|plan_task_id/.test(code(f.text)), f.path);
  }
  assert.ok(!/plan_done|plan_skip|planMarkKey/.test(code(read('src/app/s/[studentId]/presence/route.ts'))));
  assert.ok(ALL_SHELL_COPY.filter((s) => /today\.mark\.(done|skip)/.test(s)).length === 2);
  for (const p of ['src/behavior/SessionBeacon.tsx']) {
    const t = code(read(p));
    assert.match(t, /^'use client';/, p);
    assert.ok(!/bff|STARBUDDY_BFF_URL|x-sb-session|lib\/events|lib\/session|presence-server/.test(t), p);
  }
});

test('会话路由：只认 POST；JSON、声明与实际长度 ≤ 512、同源、CSRF、密钥、本人之后才碰 cookie；一律 204、no-store', () => {
  const route = code(read('src/app/s/[studentId]/presence/route.ts'));
  assert.deepEqual([...route.matchAll(/export (?:async )?(?:function|const) (\w+)/g)].map((m) => m[1]), ['dynamic', 'POST']);
  assert.match(route, /if \(type !== 'application\/json'\) return null;/);
  assert.match(route, /Number\(declared\) <= MAX_BODY\)\) return null;/);
  assert.match(route, /if \(text\.length > MAX_BODY\) return null;/);
  assert.match(route, /if \(!isUuid\(studentId\) \|\| !sameOrigin\(req\)\) return done\(\);/);
  assert.match(route, /if \(!key\) return done\(\);/);
  assert.match(route, /const s = await actionSession\(body\.csrf\);\s*if \(!s\) return done\(\);/);
  assert.match(route, /if \(!ps \|\| ps\.me\.account_kind !== 'student' \|\| ps\.me\.student_id !== studentId \|\| ps\.session\.token !== s\.token\) return done\(\);/);
  // 本人核对在第一次读 cookie 之前
  assert.ok(route.indexOf("ps.me.account_kind !== 'student'") < route.indexOf('await cookies()'));
  assert.match(route, /new Response\(null, \{ status: 204, headers: \{ 'cache-control': 'no-store' \} \}\)/);
  assert.equal((route.match(/new Response\(/g) ?? []).length, 1, '只有一种响应');
  assert.match(route, /prev && prev\.student === studentId && now - prev\.last >= IDLE_SEC - 60/);
  assert.match(route, /if \(body\.op === 'touch' \|\| !body\.entry\) return done\(\);/);
  // cookie 只经签名写入（或删除）
  assert.ok(!/jar\.set\(name, (?!signPresence|'')/.test(route));
  assert.match(route, /postEventQuietly\(s, p\.student, \{ kind: 'session_start', payload: \{ entry: p\.entry \} \}, p\.startKey, new Date\(p\.start \* 1000\)\)/);
});

test('页面端：首次只报一次；有活动才按间隔报，没有定时轮询；无活动超时只发一次结束', () => {
  const beacon = code(read('src/behavior/SessionBeacon.tsx'));
  assert.ok(!/setInterval|retry|while\s*\(/.test(beacon), '不轮询、不重试');
  assert.match(beacon, /if \(!pinged\.current\) \{\s*pinged\.current = true;\s*send\('ping'\);/);
  assert.match(beacon, /fetch\(`\/s\/\$\{studentId\}\/presence`/);
  assert.match(beacon, /headers: \{ 'content-type': 'application\/json' \}/);
  assert.match(beacon, /\.catch\(\(\) => undefined\)/);
  assert.match(beacon, /timer = setTimeout\(\(\) => \{ ended = true; send\('end'\); \}, IDLE_SEC \* 1000\);/);
  assert.match(beacon, /return null;/);
});

test('只挂在学生本人的壳：家长端不挂；退出登录先补记再删 cookie（撤销会话之前）', () => {
  const shell = code(read('src/shell/StudentShell.tsx'));
  assert.match(shell, /<SessionBeacon studentId=\{studentId\} csrf=\{csrf\} entry=\{entryOf\(path, studentId\)\} \/>/);
  assert.match(read('src/app/s/[studentId]/layout.tsx'), /ps\.me\.account_kind !== 'student' \|\| ps\.me\.student_id !== studentId\) return <>\{children\}<\/>;/);
  const users = (rx: RegExp) => src.filter((f) => rx.test(code(f.text))).map((f) => f.path).sort();
  assert.deepEqual(users(/SessionBeacon/), ['src/behavior/SessionBeacon.tsx', 'src/shell/StudentShell.tsx']);
  // 今日计划“完成”“先跳过”（PLT-15 PR2）的票据复用同一把签名密钥（只取 presenceKey，不碰会话 cookie）
  assert.deepEqual(users(/presence-server/), ['src/app/family/actions.ts', 'src/app/s/[studentId]/actions.ts', 'src/app/s/[studentId]/presence/route.ts',
                                              'src/app/s/[studentId]/today/page.tsx']);
  for (const p of ['src/app/s/[studentId]/actions.ts', 'src/app/s/[studentId]/today/page.tsx']) {
    assert.match(code(read(p)), /import \{ presenceKey \} from '[./]+behavior\/presence-server\.ts';/, p);
  }
  for (const f of src.filter((x) => /^src\/(app\/family|family-goals|monthly-plan|auth)\//.test(x.path) && x.path !== 'src/app/family/actions.ts')) {
    assert.ok(!/SessionBeacon|presence|postEventQuietly/.test(code(f.text)), f.path);
  }
  const actions = code(read('src/app/family/actions.ts'));
  assert.match(actions, /await endPresenceOnLogout\(s\);\s*await logout\(s\.token, s\.csrf\);/);
  const server = code(read('src/behavior/presence-server.ts'));
  assert.match(server, /jar\.set\(name, '', \{ \.\.\.presenceCookieOptions\(\), maxAge: 0 \}\);/);
  assert.match(server, /verifyPresence\(raw, \{ key, accountKind: 'student', token: s\.token \}\)/);
});
