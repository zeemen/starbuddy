// LT-01 staging 白名单固定码 OTP 的集成测试（specs/launch-lt01-deploy-packaging 决定记录第 6 条）：一次性 Postgres，真实的
// platform.auth_otp_issue / auth_otp_login（迁移 0029），BFF 以 staging 运行、发送方不投递。
//   - 白名单号码用管理员下发的码登录成功；同一个码第二次不能再用（成功即作废）；
//   - 不在白名单的号码：请求验证码的响应与白名单号码逐字节相同，之后任何码都登录不了；
//   - 失败 5 次锁定条目；审计文件不含码与号码原文；访问日志不含码与号码。
// 全部合成：号码用未分配的国家码 +999，码是合成的 6 位数字。
import assert from 'node:assert/strict';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import pg from 'pg';
import { loadAuthConfig, randomKeys } from '../src/http/config.ts';
import { createShellServer } from '../src/http/server.ts';
import { MAX_FAILURES, NoDeliverySender, StagingOtp } from '../src/http/staging-otp.ts';

const HAS_DB = Boolean(process.env['PGHOST']);
const ORIGIN = 'https://shell.example.invalid';
const id = (n: string) => `5f0e0000-0000-4000-8000-0000000000${n}`;
const G = { a: id('a1'), b: id('a2'), c: id('a3'), d: id('a4') };
const PH = { a: '+99900004001', b: '+99900004002', c: '+99900004003', d: '+99900004004' };
const CODE = { a: '314159', c: '271828', d: '161803' };

let su: pg.Pool;
let bff: pg.Pool;
let server: Server;
let base = '';
let dir = '';
let auditFile = '';
const logs: Record<string, unknown>[] = [];

before(async () => {
  if (!HAS_DB) return;
  su = new pg.Pool({ user: 'postgres', max: 2 });
  await su.query(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff; END IF; END $$;`);
  await su.query(`INSERT INTO platform.account (id, kind, status) VALUES
      ('${G.a}', 'guardian', 'active'), ('${G.b}', 'guardian', 'active'), ('${G.c}', 'guardian', 'active'), ('${G.d}', 'guardian', 'active')
      ON CONFLICT DO NOTHING;
    INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
      ('${G.a}', '${PH.a}', now()), ('${G.b}', '${PH.b}', now()), ('${G.c}', '${PH.c}', now()), ('${G.d}', '${PH.d}', now())
      ON CONFLICT DO NOTHING;`);
  dir = mkdtempSync(join(tmpdir(), 'bff-otp-int-'));
  const entriesFile = join(dir, 'otp.json');
  auditFile = join(dir, 'audit.jsonl');
  // 窗口按真实时钟取（条目的有效期由 BFF 进程时钟判断；验证码本身的有效期仍由库函数按 auth_now 判断）
  const from = new Date(Date.now() - 3600_000).toISOString();
  const to = new Date(Date.now() + 3600_000).toISOString();
  writeFileSync(entriesFile, JSON.stringify({ entries: [
    { id: 'int-a', phone: PH.a, code: CODE.a, issued_at: from, not_after: to },
    { id: 'int-c', phone: PH.c, code: CODE.c, issued_at: from, not_after: to },
    { id: 'int-d', phone: PH.d, code: CODE.d, issued_at: from, not_after: to },
  ] }), { mode: 0o600 });
  writeFileSync(auditFile, '', { mode: 0o600 });  // 条目已生效时审计文件必须先由人建好（安全审查 S1）
  const keys = randomKeys();
  const config = loadAuthConfig();
  config.rate_limits = Object.fromEntries(Object.entries(config.rate_limits).map(([k, v]) => [k, { ...v, limit: v.limit * 1000 }]));
  bff = new pg.Pool({ user: 'bff_app', max: 5 });
  server = createShellServer({ pool: bff, config, keys, env: 'staging', sms: new NoDeliverySender(), shellOrigin: ORIGIN,
                               stagingOtp: new StagingOtp({ env: 'staging', keys, entriesFile, auditFile, heartbeat: false }),
                               log: (l) => logs.push(l) });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/bff/v0`;
});

after(async () => {
  if (!HAS_DB) return;
  await new Promise<void>((r) => server.close(() => r()));
  await bff.end();
  await su.end();
  rmSync(dir, { recursive: true, force: true });
});

async function post(path: string, body: unknown): Promise<{ status: number; text: string; headers: Headers }> {
  const r = await fetch(`${base}${path}`, { method: 'POST', headers: { origin: ORIGIN, 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return { status: r.status, text: await r.text(), headers: r.headers };
}
const request = (phone: string) => post('/auth/otp/request', { phone });
const verify = (phone: string, code: string) => post('/auth/otp/verify', { phone, code });

test('白名单号码用下发的码登录成功；同一个码不能再用', { skip: !HAS_DB }, async () => {
  assert.equal((await request(PH.a)).status, 202);
  const ok = await verify(PH.a, CODE.a);
  assert.equal(ok.status, 200, ok.text);
  assert.equal(JSON.parse(ok.text).account_kind, 'guardian');
  assert.equal((await request(PH.a)).status, 202);  // 条目已作废：签发随机码、不投递
  assert.equal((await verify(PH.a, CODE.a)).status, 401);
});

test('不在白名单的号码：请求验证码的响应与白名单号码相同，之后登录不了', { skip: !HAS_DB }, async () => {
  const listed = await request(PH.c);
  const unlisted = await request(PH.b);
  assert.equal(unlisted.status, listed.status);
  assert.equal(unlisted.text, listed.text);
  assert.deepEqual([...unlisted.headers.keys()].filter((k) => k !== 'date' && k !== 'x-request-id').sort(),
                   [...listed.headers.keys()].filter((k) => k !== 'date' && k !== 'x-request-id').sort());
  for (const code of [CODE.a, CODE.c, '000000']) assert.equal((await verify(PH.b, code)).status, 401);
});

test(`失败 ${MAX_FAILURES} 次锁定条目，之后正确的码也不再签发`, { skip: !HAS_DB }, async () => {
  assert.equal((await request(PH.d)).status, 202);
  for (let i = 0; i < MAX_FAILURES; i++) assert.equal((await verify(PH.d, '000001')).status, 401);
  assert.equal((await request(PH.d)).status, 202);  // 条目已锁定：签发随机码、不投递
  assert.equal((await verify(PH.d, CODE.d)).status, 401);
});

test('审计文件与访问日志不含码与号码原文', { skip: !HAS_DB }, async () => {
  const audit = readFileSync(auditFile, 'utf8');
  const all = audit + JSON.stringify(logs);
  for (const s of [...Object.values(CODE), ...Object.values(PH), '99900004']) assert.ok(!all.includes(s), s);
  const events = audit.trim().split('\n').map((l) => JSON.parse(l).event);
  assert.ok(events.includes('issued') && events.includes('consumed') && events.includes('not_whitelisted') && events.includes('verify_failed'));
  assert.ok(events.includes('verify_other') && events.includes('locked'));
});
