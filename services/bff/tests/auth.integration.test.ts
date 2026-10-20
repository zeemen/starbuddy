// 统一壳 H1 认证与会话的集成测试（specs/unified-shell-sprint4/spec.md 验收“登录”“会话”“学生模式”“CSRF”“限流”“日志”，
// 以及第二轮 7.1、7.2、7.3 的 H1 部分：路由 × 账号的鉴权负向用例、身份注入、连接池串号）。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），登录角色 bff_app 只继承 starbuddy_bff，走真实 RLS 与迁移 0029 的函数。
// 时钟：platform.auth_now() 换成读 authtest.clock 的版本（只在这个测试库里），过期用例推进它。
// 阈值读 config/auth.json（与 BFF 同一份）。全部合成：号码用未分配的国家码 +999（规则 8）。
import assert from 'node:assert/strict';
import { request, type IncomingHttpHeaders, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import pg from 'pg';
import { loadAuthConfig, loadKeys, randomKeys, type AuthConfig, type Keys } from '../src/http/config.ts';
import { P } from '../src/http/problems.ts';
import { ROUTES, PREFIX } from '../src/http/routes.ts';
import { logHash } from '../src/http/secrets.ts';
import { createShellServer } from '../src/http/server.ts';
import { CaptureSmsSender, createSmsSender } from '../src/http/sms.ts';

const HAS_DB = Boolean(process.env['PGHOST']);
const ORIGIN = 'http://shell.test';
const T0 = Date.parse('2027-03-01T02:00:00Z');
const id = (n: string) => `7b000000-0000-4000-8000-0000000000${n}`;
const ID = {
  gA: id('a1'), gB: id('a2'), gU: id('a3'), gR: id('a4'), gD1: id('a5'), gD2: id('a6'), gX: id('a7'),
  S1: id('c1'), S2: id('c2'), T1: id('c3'), aS1: id('b1'), aS2: id('b2'), aT1: id('b3'),
  hA: id('d1'), hB: id('d2'), missing: id('ff'),
};
const PH = { gA: '+99900001001', gB: '+99900001002', gU: '+99900001003', gR: '+99900001004', dup: '+99900001005', gX: '+99900001006' };

let su: pg.Pool;
let bff: pg.Pool;
let server: Server;
let port = 0;
let sms: CaptureSmsSender;
let keys: Keys;
let config: AuthConfig;
let clock = T0;
const logs: Record<string, unknown>[] = [];

async function setClock(ms: number) {
  clock = ms;
  await su.query('UPDATE authtest.clock SET now = $1', [new Date(ms)]);
}
const advance = (ms: number) => setClock(clock + ms);

before(async () => {
  if (!HAS_DB) return;
  su = new pg.Pool({ user: 'postgres', max: 3 });
  await su.query(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN
      CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;
      CREATE ROLE scheduler_app LOGIN IN ROLE starbuddy_scheduler;
    END IF; END $$;
    CREATE SCHEMA IF NOT EXISTS authtest;
    GRANT USAGE ON SCHEMA authtest TO starbuddy_authz;
    CREATE TABLE IF NOT EXISTS authtest.clock (now timestamptz NOT NULL);
    GRANT SELECT ON authtest.clock TO starbuddy_authz;
    CREATE OR REPLACE FUNCTION platform.auth_now() RETURNS timestamptz
      LANGUAGE sql VOLATILE SET search_path = pg_catalog AS $f$ SELECT now FROM authtest.clock $f$;`);
  await su.query('DELETE FROM authtest.clock');
  await su.query('INSERT INTO authtest.clock VALUES ($1)', [new Date(T0)]);
  // 两户家庭：gA（S1 出生年月空 → 按不满 14；S2 已满 14）、gB（T1）；gU 没有绑定；gR 曾绑定 S1、已解除；
  // gD1、gD2 同一号码（历史数据）；gX 停用
  await su.query(`
    INSERT INTO platform.account (id, kind, status) VALUES
      ('${ID.gA}', 'guardian', 'active'), ('${ID.gB}', 'guardian', 'active'), ('${ID.gU}', 'guardian', 'active'),
      ('${ID.gR}', 'guardian', 'active'), ('${ID.gD1}', 'guardian', 'active'), ('${ID.gD2}', 'guardian', 'active'),
      ('${ID.gX}', 'guardian', 'disabled'), ('${ID.aS1}', 'student', 'active'), ('${ID.aS2}', 'student', 'active'),
      ('${ID.aT1}', 'student', 'active');
    INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
      ('${ID.gA}', '${PH.gA}', now()), ('${ID.gB}', '${PH.gB}', now()), ('${ID.gU}', '${PH.gU}', now()), ('${ID.gR}', '${PH.gR}', now()),
      ('${ID.gD1}', '${PH.dup}', now()), ('${ID.gD2}', '${PH.dup}', now()), ('${ID.gX}', '${PH.gX}', now());
    INSERT INTO platform.household (id, created_by_account) VALUES ('${ID.hA}', '${ID.gA}'), ('${ID.hB}', '${ID.gB}');
    INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, birth_year_month) VALUES
      ('${ID.S1}', '${ID.aS1}', '${ID.hA}', 8, 'nanjing', NULL), ('${ID.S2}', '${ID.aS2}', '${ID.hA}', 9, 'nanjing', '2011-01-01'),
      ('${ID.T1}', '${ID.aT1}', '${ID.hB}', 8, 'nanjing', '2013-06-01');
    INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
      ('${ID.gA}', '${ID.S1}', 'household_creator'), ('${ID.gA}', '${ID.S2}', 'household_creator'),
      ('${ID.gB}', '${ID.T1}', 'household_creator'), ('${ID.gR}', '${ID.S1}', 'invite');
    UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = '${ID.gR}';`);
  bff = new pg.Pool({ user: 'bff_app', max: 20 });
  keys = randomKeys();
  config = loadAuthConfig();
  sms = new CaptureSmsSender('test');
  server = createShellServer({ pool: bff, config, keys, env: 'test', sms, shellOrigin: ORIGIN, trustClientIpHeader: true,
                               log: (l) => logs.push(l) });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  port = (server.address() as AddressInfo).port;
});

after(async () => {
  if (!HAS_DB) return;
  await new Promise<void>((r) => server.close(() => r()));
  await bff.end();
  await su.end();
});

interface Res { status: number; headers: IncomingHttpHeaders; body: string; json: any }
interface Opts { token?: string | null; csrf?: string | null; origin?: string | null; body?: unknown; headers?: Record<string, string>; ip?: string }

/** 原样的 HTTP 请求（node:http，可以带 Origin），返回状态、响应头与原始响应体 */
function call(method: string, path: string, o: Opts = {}): Promise<Res> {
  const headers: Record<string, string> = { ...(o.headers ?? {}) };
  if (o.token) headers['x-sb-session'] = o.token;
  if (o.csrf) headers['x-csrf-token'] = o.csrf;
  const origin = o.origin === undefined ? ORIGIN : o.origin;
  if (origin !== null) headers['origin'] = origin;
  headers['x-sb-client-ip'] = o.ip ?? '198.51.100.7';
  const payload = o.body === undefined ? undefined : typeof o.body === 'string' ? o.body : JSON.stringify(o.body);
  if (payload !== undefined) headers['content-type'] = 'application/json';
  return new Promise((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port, method, path: path.startsWith('/bff') ? path : PREFIX + path, headers }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (c: Buffer) => chunks.push(c));
      res.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        let json: unknown = null;
        try { json = JSON.parse(body); } catch { /* 非 JSON */ }
        resolve({ status: res.statusCode ?? 0, headers: res.headers, body, json });
      });
    });
    req.on('error', reject);
    if (payload !== undefined) req.write(payload);
    req.end();
  });
}

/** 响应头去掉每次都会变的 date，其余逐字节比较 */
const stable = (h: IncomingHttpHeaders) => Object.fromEntries(Object.entries(h).filter(([k]) => k !== 'date'));
const same = (a: Res, b: Res, msg: string) => {
  assert.equal(a.status, b.status, msg);
  assert.equal(a.body, b.body, msg);
  assert.deepEqual(stable(a.headers), stable(b.headers), msg);
};

let ipSeq = 0;
const freshIp = () => `203.0.113.${(ipSeq++ % 250) + 1}`;

async function requestCode(phone: string): Promise<string> {
  await advance(61_000);  // 每号每分钟 1 次
  const r = await call('POST', '/auth/otp/request', { body: { phone }, ip: freshIp() });
  assert.equal(r.status, 202);
  const code = sms.latest(phone);
  assert.ok(code);
  return code;
}

async function login(phone: string, prev?: string): Promise<{ token: string; csrf: string; account: string }> {
  const code = await requestCode(phone);
  const r = await call('POST', '/auth/otp/verify', { body: { phone, code }, ip: freshIp(), token: prev ?? null });
  assert.equal(r.status, 200, r.body);
  const me = await call('GET', '/me', { token: r.json.session_token });
  return { token: r.json.session_token, csrf: r.json.csrf_token, account: me.json.account_kind };
}

async function consent(g: { token: string; csrf: string }, student: string, type: string, decision = 'granted') {
  return call('POST', `/students/${student}/consents`, { token: g.token, csrf: g.csrf,
    body: { consent_type: type, document_version: `${type.replace(/_/g, '-')}@synthetic-0`, decision } });
}

// ---------------------------------------------------------------- 配置与短信桩

test('阈值与时长读 config/auth.json，与 spec 默认一致（待人确认 #4）', () => {
  const c = loadAuthConfig();
  assert.deepEqual(c.otp, { ttl_seconds: 300, max_attempts: 5 });
  assert.deepEqual(c.session.guardian, { absolute_seconds: 7 * 86400, idle_seconds: 86400 });
  assert.equal(c.rate_limits['otp_request.phone.minute']!.limit, 1);
  assert.equal(c.rate_limits['otp_request.phone.day']!.limit, 10);
  assert.equal(c.rate_limits['otp_request.ip.hour']!.limit, 30);
  assert.equal(c.rate_limits['otp_verify.phone.quarter']!.limit, 10);
  assert.equal(c.rate_limits['otp_verify.ip.hour']!.limit, 60);
  assert.equal(c.rate_limits['student_create.account.day']!.limit, 5);
  assert.equal(c.rate_limits['student_session.account.hour']!.limit, 20);
  for (const r of ROUTES) for (const l of r.rateLimits) assert.ok(c.rate_limits[l.bucket], `路由表用到的限流桶 ${l.bucket} 在配置里`);
});

test('短信：捕获桩只在 dev / test；staging 只能配白名单固定码（LT-01）；其余环境没有供应商，拒绝启动；密钥在非本地环境必须配置', () => {
  assert.throws(() => createSmsSender('staging', { BFF_STAGING_OTP_FILE: '/run/x.json' }), /BFF_STAGING_OTP_AUDIT_FILE/);
  assert.ok(createSmsSender('staging', { BFF_STAGING_OTP_FILE: '/run/x.json', BFF_STAGING_OTP_AUDIT_FILE: '/run/a.jsonl' }));
  assert.throws(() => createSmsSender('test', { BFF_STAGING_OTP_FILE: '/run/x.json' }), /只能在 staging/);
  assert.throws(() => createSmsSender('pilot', { BFF_STAGING_OTP_FILE: '/run/x.json', BFF_STAGING_OTP_AUDIT_FILE: '/run/a.jsonl' }), /只能在 staging/);
  for (const env of ['staging', 'pilot', 'production'] as const) {
    assert.throws(() => createSmsSender(env, {}), /短信供应商/);
    assert.throws(() => new CaptureSmsSender(env), /dev \/ test/);
    assert.throws(() => loadKeys(env, {}), /未设置/);
    assert.throws(() => loadKeys(env, { BFF_LOG_HMAC_KEY: 'ab'.repeat(16), BFF_AUTH_SECRET: 'ab'.repeat(8) }), /至少 32 字节/);
  }
  assert.throws(() => createSmsSender('staging', { BFF_SMS_CAPTURE_FILE: '/tmp/x' }), /dev \/ test/);
  assert.ok(createSmsSender('test', {}) instanceof CaptureSmsSender);
  assert.ok(loadKeys('test', {}).log.length === 32);
});

test('捕获桩不收非合成号码：请求照样 202、响应与合成号码相同，收件箱里没有这条', { skip: !HAS_DB }, async () => {
  await advance(61_000);
  const before = sms.inbox.length;
  const real = await call('POST', '/auth/otp/request', { body: { phone: '+15550100000' }, ip: freshIp() });
  const syn = await call('POST', '/auth/otp/request', { body: { phone: '+99900001999' }, ip: freshIp() });
  same(real, syn, '真实格式号码与合成号码的响应相同');
  assert.equal(sms.inbox.length, before + 1);
  assert.ok(sms.inbox.every((m) => m.phone.startsWith('+999')));
});

// ---------------------------------------------------------------- 登录

test('发验证码：已注册与未注册号码返回逐字节相同的 202', { skip: !HAS_DB }, async () => {
  await advance(61_000);
  const a = await call('POST', '/auth/otp/request', { body: { phone: PH.gB }, ip: freshIp() });
  const b = await call('POST', '/auth/otp/request', { body: { phone: '+99900001888' }, ip: freshIp() });
  assert.equal(a.status, 202);
  same(a, b, '已注册与未注册');
  assert.equal(a.headers['cache-control'], 'no-store');
  assert.equal(a.headers['access-control-allow-origin'], undefined);
  const bad = await call('POST', '/auth/otp/request', { body: { phone: '13800000000' }, ip: freshIp() });
  assert.equal(bad.status, 422);
});

test('验证码：错误、过期、用过、换号、格式不对都失败且响应相同；错满作废；第二次登录不新建账号', { skip: !HAS_DB }, async () => {
  const phone = '+99900001101';
  const verify = (p: string, code: string) => call('POST', '/auth/otp/verify', { body: { phone: p, code }, ip: freshIp() });
  let code = await requestCode(phone);
  const wrong = await verify(phone, code === '000000' ? '000001' : '000000');
  assert.equal(wrong.status, 401);
  assert.equal(wrong.body, P.LOGIN_FAILED);
  const ok = await verify(phone, code);
  assert.equal(ok.status, 200);
  assert.match(ok.json.session_token, /^[0-9a-f]{64}$/);
  same(await verify(phone, code), wrong, '用过的码');
  const { rows: [{ n: before }] } = await su.query(`SELECT count(*)::int AS n FROM platform.account`);
  // 过期
  code = await requestCode(phone);
  await advance(300_000);
  same(await verify(phone, code), wrong, '过期的码');
  // 换号：A 号的码拿去登录 B 号
  code = await requestCode(phone);
  await requestCode('+99900001102');
  same(await verify('+99900001102', code), wrong, '换号使用');
  same(await verify('bad', code), wrong, '号码格式不对');
  same(await verify(phone, '12ab56'), wrong, '验证码格式不对');
  // 错满 5 次作废（先离开“每号每 15 分钟 10 次”的限流窗口，重新申请）
  await advance(900_000);
  code = await requestCode(phone);
  for (let i = 0; i < 5; i++) await verify(phone, code === '999999' ? '999998' : '999999');
  same(await verify(phone, code), wrong, '错满 5 次后正确的码也失败');
  // 第二次登录：同一账号，账号数不变
  code = await requestCode(phone);
  const again = await verify(phone, code);
  assert.equal(again.status, 200);
  const { rows: [{ n: after }] } = await su.query(`SELECT count(*)::int AS n FROM platform.account`);
  assert.equal(after, before);
  const ids = await su.query(`SELECT count(*)::int AS n FROM platform.account_contact WHERE phone_e164 = $1`, [phone]);
  assert.equal(ids.rows[0].n, 1);
});

test('同号多个家长账号：码对了也拒绝登录（联系客服）；停用的家长不能登录', { skip: !HAS_DB }, async () => {
  let code = await requestCode(PH.dup);
  const r = await call('POST', '/auth/otp/verify', { body: { phone: PH.dup, code }, ip: freshIp() });
  assert.equal(r.status, 409);
  assert.equal(r.body, P.CONTACT_SUPPORT);
  code = await requestCode(PH.gX);
  const x = await call('POST', '/auth/otp/verify', { body: { phone: PH.gX, code }, ip: freshIp() });
  assert.equal(x.body, P.LOGIN_FAILED);
});

// ---------------------------------------------------------------- 会话

test('会话：/me 只认会话；登录换令牌、旧令牌 401；登出与退出所有设备后 401；账号停用后 401', { skip: !HAS_DB }, async () => {
  const noSession = await call('GET', '/me');
  assert.equal(noSession.status, 401);
  assert.equal(noSession.body, P.UNAUTHENTICATED);
  same(await call('GET', '/me', { token: 'f'.repeat(64) }), noSession, '伪造令牌');
  same(await call('GET', '/me', { token: 'not-a-token' }), noSession, '格式不对的令牌');
  const g = await login(PH.gA);
  const me = await call('GET', '/me', { token: g.token });
  assert.deepEqual(me.json, { account_kind: 'guardian', children: [{ student_id: ID.S1, grade: 8 }, { student_id: ID.S2, grade: 9 }] });
  // 重新登录时带上本设备旧令牌：令牌变化，旧的立即作废
  const g2 = await login(PH.gA, g.token);
  assert.notEqual(g2.token, g.token);
  same(await call('GET', '/me', { token: g.token }), noSession, '重新登录后的旧令牌');
  assert.equal((await call('GET', '/me', { token: g2.token })).status, 200);
  // 登出
  const out = await call('POST', '/auth/logout', { token: g2.token, csrf: g2.csrf });
  assert.equal(out.status, 200);
  same(await call('GET', '/me', { token: g2.token }), noSession, '登出后');
  // 退出所有设备
  const a = await login(PH.gA);
  const b = await login(PH.gA);
  const other = await login(PH.gB);
  assert.equal((await call('POST', '/auth/logout-all', { token: a.token, csrf: a.csrf })).status, 200);
  same(await call('GET', '/me', { token: a.token }), noSession, '退出所有设备：本设备');
  same(await call('GET', '/me', { token: b.token }), noSession, '退出所有设备：另一台设备');
  assert.equal((await call('GET', '/me', { token: other.token })).status, 200, '别的账号不受影响');
  // 账号停用
  await su.query(`UPDATE platform.account SET status = 'disabled' WHERE id = $1`, [ID.gB]);
  same(await call('GET', '/me', { token: other.token }), noSession, '账号停用后');
  await su.query(`UPDATE platform.account SET status = 'active' WHERE id = $1`, [ID.gB]);
});

test('会话：空闲 24 小时与绝对 7 天后 401（注入时钟）', { skip: !HAS_DB }, async () => {
  const g = await login(PH.gB);
  await advance(23 * 3600_000);
  assert.equal((await call('GET', '/me', { token: g.token })).status, 200);
  await advance(24 * 3600_000 + 1000);
  assert.equal((await call('GET', '/me', { token: g.token })).status, 401, '空闲满 24 小时');
  const h = await login(PH.gB);
  for (let i = 0; i < 8; i++) {
    await advance(20 * 3600_000);
    assert.equal((await call('GET', '/me', { token: h.token })).status, 200, `一直在用：第 ${i + 1} 次`);
  }
  await advance(8 * 3600_000);
  assert.equal((await call('GET', '/me', { token: h.token })).status, 401, '满 7 天');
});

// ---------------------------------------------------------------- 学生模式

test('学生模式：缺同意不能开；不满 14 另需监护人同意；打开后家长令牌作废、学生会话只看到自己', { skip: !HAS_DB }, async () => {
  let g = await login(PH.gA);
  const open = (s: string, who: { token: string; csrf: string }) =>
    call('POST', `/students/${s}/student-session`, { token: who.token, csrf: who.csrf });
  const noConsent = await open(ID.S2, g);
  assert.equal(noConsent.status, 409);
  assert.equal(noConsent.body, P.CONSENT_REQUIRED);
  assert.equal((await consent(g, ID.S2, 'privacy_policy')).status, 201);
  assert.equal((await consent(g, ID.S1, 'privacy_policy')).status, 201);
  // 出生年月为空按不满 14：只有隐私政策同意还不够
  assert.equal((await open(ID.S1, g)).body, P.CONSENT_REQUIRED);
  assert.equal((await consent(g, ID.S1, 'guardian_consent_under14')).status, 201);
  const s = await open(ID.S2, g);
  assert.equal(s.status, 200);
  assert.equal(s.json.account_kind, 'student');
  assert.notEqual(s.json.session_token, g.token);
  assert.equal((await call('GET', '/me', { token: g.token })).status, 401, '家长会话同时作废');
  const me = await call('GET', '/me', { token: s.json.session_token });
  assert.deepEqual(me.json, { account_kind: 'student', student_id: ID.S2, grade: 9 });  // 学生本人的当前年级同样由届别推算（PR3）
  // 学生会话调 guardian 路由：全表一致地拒绝（与路径里是谁无关）
  const st = { token: s.json.session_token, csrf: s.json.csrf_token };
  const guardianRoutes = ROUTES.filter((r) => r.auth === 'guardian');
  assert.ok(guardianRoutes.length >= 3);
  let first: Res | null = null;
  for (const r of guardianRoutes) {
    for (const target of [ID.S2, ID.S1, ID.T1, ID.missing]) {
      const path = r.path.replace('{student_id}', target).replace('{subject}', 'math');  // 学科码参数（H4）不是 UUID
      const x = await call(r.method, path, { token: st.token, csrf: st.csrf, body: r.body ? {} : undefined });
      assert.equal(x.status, 403, `${r.method} ${path}`);
      if (first) same(x, first, `${r.method} ${path}`); else first = x;
    }
  }
  // 签发后再开 S1（补了监护人同意）
  g = await login(PH.gA);
  assert.equal((await open(ID.S1, g)).status, 200);
});

test('学生模式：未绑定、已解除绑定、另一户家长与“学生不存在”响应逐字节相同；解除绑定后旧会话读孩子 404', { skip: !HAS_DB }, async () => {
  const gU = await login(PH.gU);
  const gR = await login(PH.gR);
  const gB = await login(PH.gB);
  const open = (s: string, who: { token: string; csrf: string }) =>
    call('POST', `/students/${s}/student-session`, { token: who.token, csrf: who.csrf });
  const missing = await open(ID.missing, gB);
  assert.equal(missing.status, 404);
  assert.equal(missing.body, P.NOT_FOUND);
  same(await open(ID.S1, gU), missing, '未绑定的家长');
  same(await open(ID.S1, gR), missing, '已解除绑定的家长');
  same(await open(ID.S1, gB), missing, '另一户的家长');
  same(await consent(gB, ID.S1, 'privacy_policy'), await consent(gB, ID.missing, 'privacy_policy'), '同意：另一户与不存在');
  assert.equal((await consent(gB, ID.S1, 'privacy_policy')).status, 404);
  // 路径参数不是 UUID：同样的 404
  same(await call('POST', '/students/not-a-uuid/student-session', { token: gB.token, csrf: gB.csrf }), missing, '非 UUID 参数');
  // 解除绑定：会话不废，但读不到孩子（404）
  const gA = await login(PH.gA);
  await su.query(`UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = $1 AND student_id = $2`, [ID.gA, ID.S2]);
  try {
    same(await open(ID.S2, gA), missing, '解除绑定后打开学生模式');
    same(await consent(gA, ID.S2, 'privacy_policy'), await consent(gA, ID.missing, 'privacy_policy'), '解除绑定后写同意');
    const me = await call('GET', '/me', { token: gA.token });
    assert.deepEqual(me.json.children.map((c: { student_id: string }) => c.student_id), [ID.S1]);
  } finally {
    await su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`,
                   [ID.gA, ID.S2]);
  }
});

test('同意：合成版本号只在 dev / test（LT-01 决定记录第 10 条起 staging 拒绝）；取值不合要求 422', { skip: !HAS_DB }, async () => {
  const { syntheticConsentAllowed } = await import('../src/http/routes.ts');
  assert.deepEqual((['dev', 'test', 'staging', 'pilot', 'production'] as const).map(syntheticConsentAllowed), [true, true, false, false, false]);
  const g = await login(PH.gA);
  const bad = (body: unknown) => call('POST', `/students/${ID.S1}/consents`, { token: g.token, csrf: g.csrf, body });
  assert.equal((await bad({ consent_type: 'privacy_policy', document_version: 'not versioned', decision: 'granted' })).status, 422);
  assert.equal((await bad({ consent_type: 'marketing', document_version: 'x@1', decision: 'granted' })).status, 422);
  assert.equal((await bad({ consent_type: 'privacy_policy', document_version: 'x@1', decision: 'granted', account_id: ID.gB })).status, 422);
});

test('建档：家长建在自己的家庭里；学生会话不能建档；取值不合要求 422', { skip: !HAS_DB }, async () => {
  const g = await login(PH.gB);
  const r = await call('POST', '/students', { token: g.token, csrf: g.csrf,
    body: { grade: 7, region: 'nanjing', textbook_version: null, birth_year_month: '2014-09' } });
  assert.equal(r.status, 201);
  const { rows } = await su.query(`SELECT household_id, birth_year_month::text AS b FROM platform.student_profile WHERE id = $1`, [r.json.student_id]);
  assert.deepEqual(rows[0], { household_id: ID.hB, b: '2014-09-01' });
  const me = await call('GET', '/me', { token: g.token });
  assert.ok(me.json.children.some((c: { student_id: string }) => c.student_id === r.json.student_id));
  assert.equal((await call('POST', '/students', { token: g.token, csrf: g.csrf, body: { grade: 6, region: 'nanjing' } })).status, 422);
  assert.equal((await call('POST', '/students', { token: g.token, csrf: g.csrf, body: { grade: 8, region: 'Nanjing!' } })).status, 422);
});

// ---------------------------------------------------------------- CSRF

test('CSRF：缺令牌、令牌属于别的会话、Origin 不符都拒绝；响应没有 CORS 放行头', { skip: !HAS_DB }, async () => {
  const g = await login(PH.gA);
  const other = await login(PH.gB);
  const path = `/students/${ID.S1}/consents`;
  const body = { consent_type: 'pilot_participation', document_version: 'pilot@synthetic-0', decision: 'granted' };
  const noToken = await call('POST', path, { token: g.token, body });
  assert.equal(noToken.status, 403);
  assert.equal(noToken.body, P.CSRF);
  same(await call('POST', path, { token: g.token, csrf: other.csrf, body }), noToken, '别的会话的 CSRF 令牌');
  same(await call('POST', path, { token: g.token, csrf: g.csrf, origin: 'http://evil.test', body }), noToken, 'Origin 不符');
  same(await call('POST', path, { token: g.token, csrf: g.csrf, origin: null, body }), noToken, '没有 Origin');
  same(await call('POST', '/auth/logout', { token: g.token, csrf: other.csrf }), noToken, '登出也要 CSRF 令牌');
  const loginCsrf = await call('POST', '/auth/otp/request', { body: { phone: '+99900001777' }, origin: 'http://evil.test', ip: freshIp() });
  same(loginCsrf, noToken, '登录前的请求也核对 Origin');
  const ok = await call('POST', path, { token: g.token, csrf: g.csrf, body });
  assert.equal(ok.status, 201);
  for (const r of [noToken, ok, loginCsrf]) assert.equal(r.headers['access-control-allow-origin'], undefined);
  const pre = await call('OPTIONS', path, { token: g.token });
  assert.equal(pre.headers['access-control-allow-origin'], undefined);
  assert.equal(pre.headers['access-control-allow-methods'], undefined);
});

// ---------------------------------------------------------------- 限流

test('限流：发验证码、验证验证码、建档、打开学生模式超过阈值后返回逐字节相同的 429', { skip: !HAS_DB }, async () => {
  const bodies: Res[] = [];
  // 每号每分钟 1 次
  await advance(61_000);
  assert.equal((await call('POST', '/auth/otp/request', { body: { phone: '+99900001201' }, ip: freshIp() })).status, 202);
  bodies.push(await call('POST', '/auth/otp/request', { body: { phone: '+99900001201' }, ip: freshIp() }));
  // 每 IP 每小时 30 次
  const ip = '192.0.2.50';
  const n = config.rate_limits['otp_request.ip.hour']!.limit;
  for (let i = 0; i < n; i++) {
    assert.equal((await call('POST', '/auth/otp/request', { body: { phone: `+9990000130${String(i).padStart(2, '0')}` }, ip })).status, 202);
  }
  bodies.push(await call('POST', '/auth/otp/request', { body: { phone: '+99900001399' }, ip }));
  // 验证验证码：每号每 15 分钟 10 次
  await advance(3600_000);
  const vPhone = '+99900001202';
  for (let i = 0; i < config.rate_limits['otp_verify.phone.quarter']!.limit; i++) {
    assert.equal((await call('POST', '/auth/otp/verify', { body: { phone: vPhone, code: '000000' }, ip: freshIp() })).status, 401);
  }
  bodies.push(await call('POST', '/auth/otp/verify', { body: { phone: vPhone, code: '000000' }, ip: freshIp() }));
  // 建档：每家长账号每天 5 次
  const g = await login(PH.gU);
  for (let i = 0; i < config.rate_limits['student_create.account.day']!.limit; i++) {
    assert.equal((await call('POST', '/students', { token: g.token, csrf: g.csrf, body: { grade: 8, region: 'nanjing' } })).status, 201);
  }
  bodies.push(await call('POST', '/students', { token: g.token, csrf: g.csrf, body: { grade: 8, region: 'nanjing' } }));
  // 打开学生模式：每家长账号每小时 20 次（失败的尝试也算）
  const gx = await login(PH.gB);
  for (let i = 0; i < config.rate_limits['student_session.account.hour']!.limit; i++) {
    assert.equal((await call('POST', `/students/${ID.missing}/student-session`, { token: gx.token, csrf: gx.csrf })).status, 404);
  }
  bodies.push(await call('POST', `/students/${ID.T1}/student-session`, { token: gx.token, csrf: gx.csrf }));
  assert.equal(bodies[0]!.status, 429);
  assert.equal(bodies[0]!.body, P.TOO_MANY);
  for (const b of bodies.slice(1)) same(b, bodies[0]!, '所有 429 相同');
});

// ---------------------------------------------------------------- 路由表、身份注入、连接池

test('路由表：未登录一律 401；表外路径 404；非 UUID 参数与不存在同一个 404', { skip: !HAS_DB }, async () => {
  for (const r of ROUTES.filter((x) => x.auth !== 'public')) {
    const path = r.path.replace('{student_id}', ID.S1).replace('{subject}', 'math').replace(/\{[a-z_]+\}/g, ID.missing);
    const x = await call(r.method, path, { body: r.body ? {} : undefined });
    assert.equal(x.status, 401, `${r.method} ${path}`);
    assert.equal(x.body, P.UNAUTHENTICATED);
  }
  const off = await call('GET', '/bff/v0/nope');
  assert.equal(off.status, 404);
  assert.equal(off.body, P.NOT_FOUND);
  assert.equal((await call('GET', '/bff/v0/students/' + ID.S1 + '/secret')).status, 404);
  assert.equal((await call('GET', '/auth/otp/request')).status, 405);
});

test('身份注入：请求头与查询参数里的 account_id / student_id 被忽略，结果与不注入相同', { skip: !HAS_DB }, async () => {
  const g = await login(PH.gB);
  const plain = await call('GET', '/me', { token: g.token });
  const injected = await call('GET', `/me?account_id=${ID.gA}&student_id=${ID.S1}`, { token: g.token,
    headers: { 'x-account-id': ID.gA, 'x-dev-account-id': ID.gA, 'x-student-id': ID.S1 } });
  assert.equal(injected.body, plain.body);
  const anon = await call('GET', '/me', { headers: { 'x-account-id': ID.gA, 'x-dev-account-id': ID.gA } });
  assert.equal(anon.status, 401, '没有会话时注入身份头仍是未登录');
  const u = await login(PH.gU);  // 这个账号本小时还没开过学生模式（不撞限流）
  const openPlain = await call('POST', `/students/${ID.S1}/student-session`, { token: u.token, csrf: u.csrf });
  assert.equal(openPlain.status, 404);
  const openInjected = await call('POST', `/students/${ID.S1}/student-session?account_id=${ID.gA}`, { token: u.token, csrf: u.csrf,
    headers: { 'x-account-id': ID.gA, 'x-dev-account-id': ID.gA } });
  same(openInjected, openPlain, '打开学生模式：注入别人的账号不改变结果');
});

test('连接池串号：两个账号 × 200 次交替并发 /me 没有一次串号；出错后连接上没有残留账号', { skip: !HAS_DB }, async () => {
  const a = await login(PH.gA);
  const b = await login(PH.gB);
  const want = new Map([[a.token, (await call('GET', '/me', { token: a.token })).body], [b.token, (await call('GET', '/me', { token: b.token })).body]]);
  assert.notEqual(want.get(a.token), want.get(b.token));
  const jobs = Array.from({ length: 200 }, (_, i) => (i % 2 === 0 ? a.token : b.token));
  const results = await Promise.all(jobs.map(async (t) => ({ t, r: await call('GET', '/me', { token: t }) })));
  for (const { t, r } of results) assert.equal(r.body, want.get(t));
  // 出错的事务之后，同一个池里的连接都不带账号
  const clients = await Promise.all(Array.from({ length: 5 }, () => bff.connect()));
  try {
    for (const c of clients) {
      const { rows } = await c.query(`SELECT coalesce(current_setting('app.account_id', true), '') AS v`);
      assert.equal(rows[0].v, '');
    }
  } finally {
    clients.forEach((c) => c.release());
  }
});

// ---------------------------------------------------------------- 日志

test('日志：student_id 只记带密钥 HMAC（换密钥值变化）；找不到令牌、验证码、手机号、UUID 原文', { skip: !HAS_DB }, async () => {
  const k2 = randomKeys();
  assert.notEqual(logHash(keys, ID.S1), logHash(k2, ID.S1));
  assert.match(logHash(keys, ID.S1), /^[0-9a-f]{16}$/);
  const text = logs.map((l) => JSON.stringify(l)).join('\n');
  assert.ok(logs.some((l) => l['student'] === logHash(keys, ID.S1)), '带学生的路由记了 HMAC');
  assert.ok(!/[0-9a-f]{64}/.test(text), '没有令牌');
  assert.ok(!/\+999/.test(text), '没有手机号');
  assert.ok(!/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}/.test(text.replace(/"request_id":"[^"]+"/g, '')), '没有 UUID 原文（请求 ID 除外）');
  for (const m of sms.inbox) assert.ok(!text.includes(`"${m.code}"`), '没有验证码');
  for (const l of logs) {
    for (const k of Object.keys(l)) {
      assert.ok(['request_id', 'route', 'status', 'duration_ms', 'account_kind', 'student', 'outcome', 'sms', 'error_class'].includes(k), k);
    }
  }
});

test('请求 ID 每次不同，只进日志不回响应头', { skip: !HAS_DB }, async () => {
  const before = logs.length;
  const r = await call('GET', '/me');
  assert.equal(r.headers['x-request-id'], undefined);
  const ids = logs.slice(before).map((l) => l['request_id']);
  assert.equal(new Set(ids).size, ids.length);
  assert.match(String(ids[0]), /^[0-9a-f-]{36}$/);
});
