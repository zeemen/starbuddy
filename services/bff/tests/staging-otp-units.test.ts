// LT-01 staging 白名单固定码 OTP（specs/launch-lt01-deploy-packaging 决定记录第 2、6 条；安全审查 S1–S4、H2，契约审查 3）与 staging
// 启动校验辅助函数（第 1、10 条，安全审查 S6、S10）的单测。全部合成：号码用未分配的国家码 +999，验证码是合成的 6 位数字，只写在临时目录里。
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';
import { checkImageEnv, listenHost, privateIpv4, randomKeys, serviceUrlOk } from '../src/http/config.ts';
import { consentVersionAllowed, isSyntheticConsentVersion, parseConsentVersions } from '../src/http/routes.ts';
import { phoneHash } from '../src/http/secrets.ts';
import { GENESIS, LOCK_STALE_MS, MAX_FAILURES, MAX_ISSUES, parseEntries, replayAudit, StagingOtp, takeLock } from '../src/http/staging-otp.ts';

const dir = mkdtempSync(join(tmpdir(), 'bff-otp-'));
after(() => rmSync(dir, { recursive: true, force: true }));
const keys = randomKeys();
const T0 = Date.parse('2026-12-01T01:00:00Z');
const PA = '+99900003001';
const PB = '+99900003002';
const PC = '+99900003003';
const entry = (id: string, phone: string, code: string, from = '2026-12-01T00:00:00Z', to = '2026-12-01T12:00:00Z') =>
  ({ id, phone, code, issued_at: from, not_after: to });
let seq = 0;
function files(entries: unknown[], { audit = true } = {}): { entriesFile: string; auditFile: string } {
  seq += 1;
  const entriesFile = join(dir, `otp-${seq}.json`);
  writeFileSync(entriesFile, JSON.stringify({ entries }), { mode: 0o600 });
  const auditFile = join(dir, `audit-${seq}.jsonl`);
  if (audit) writeFileSync(auditFile, '', { mode: 0o600 });
  return { entriesFile, auditFile };
}
let holderSeq = 0;
const make = (f: { entriesFile: string; auditFile: string }, now = () => T0, warns: Record<string, string>[] = []) =>
  new StagingOtp({ env: 'staging', keys, now, ...f, heartbeat: false, holder: { host: 'syn-host', pid: 1000 + (holderSeq++) },
                   warn: (l) => warns.push(l) });
/** 白名单号码的一次签发：挑条目 → （库写成功）→ 记账 */
const issue = (otp: StagingOtp, phone: string): string | null => {
  const p = otp.pick(phone);
  if (p) otp.markIssued(phone, p.entry);
  return p?.code ?? null;
};
/** 模拟重启：锁文件心跳过期 */
const restart = (f: { entriesFile: string; auditFile: string }, now = () => T0) => make(f, () => now() + LOCK_STALE_MS + 1);

test('条目文件：格式、id 唯一、E.164、6 位码、带时区时间、窗口 ≤ 24 小时；报错不带取值', () => {
  assert.equal(parseEntries(JSON.stringify({ entries: [entry('a-1', PA, '123456')] })).length, 1);
  const bad: unknown[] = [
    'not json', { entries: {} }, { entries: [], extra: 1 },
    { entries: [entry('A_1', PA, '123456')] },
    { entries: [entry('a-1', PA, '123456'), entry('a-1', PB, '654321')] },
    { entries: [entry('a-1', '13800000000', '123456')] },
    { entries: [entry('a-1', PA, '12345')] },
    { entries: [entry('a-1', PA, '123456', '2026-12-01 00:00')] },
    { entries: [entry('a-1', PA, '123456', '2026-12-01T00:00:00Z', '2026-12-02T00:00:01Z')] },
    { entries: [entry('a-1', PA, '123456', '2026-12-01T12:00:00Z', '2026-12-01T00:00:00Z')] },
    { entries: [{ ...entry('a-1', PA, '123456'), note: 'x' }] },
  ];
  for (const b of bad) {
    const text = typeof b === 'string' ? b : JSON.stringify(b);
    assert.throws(() => parseEntries(text), (e: Error) => !e.message.includes('123456') && !e.message.includes(PA), text);
  }
});

test('只在 staging；两个文件必须不同；审计文件坏了拒绝启动', () => {
  const f = files([]);
  for (const env of ['dev', 'test', 'pilot', 'production'] as const) {
    assert.throws(() => new StagingOtp({ env, keys, ...f }), /只能在 staging/);
  }
  assert.throws(() => new StagingOtp({ env: 'staging', keys, entriesFile: f.entriesFile, auditFile: f.entriesFile }), /不能是同一个文件/);
  assert.throws(() => new StagingOtp({ env: 'staging', keys, entriesFile: join(dir, 'missing.json'), auditFile: f.auditFile }), /读不到/);
  writeFileSync(f.auditFile, 'garbage\n');
  assert.throws(() => make(f), /审计文件第 1 行/);
});

test('S1：审计文件不存在而条目已生效拒绝启动；没有生效条目时新建空文件', () => {
  const live = files([entry('a-1', PA, '246810')], { audit: false });
  assert.throws(() => make(live), /审计文件不存在而条目已生效/);
  assert.ok(!existsSync(live.auditFile));
  const future = files([entry('b-1', PB, '135791', '2026-12-02T00:00:00Z', '2026-12-02T12:00:00Z')], { audit: false });
  make(future);
  assert.equal(readFileSync(future.auditFile, 'utf8'), '');
});

test('S1：审计行带哈希链，改动、删行、插行都在启动时被发现', () => {
  const f = files([entry('a-1', PA, '246810')]);
  const otp = make(f);
  issue(otp, PA);
  otp.onVerify(PA, false);
  otp.onVerify(PA, true);
  const text = readFileSync(f.auditFile, 'utf8');
  const lines = text.trim().split('\n');
  assert.equal(JSON.parse(lines[0]!).prev, GENESIS);
  assert.equal(replayAudit(text, keys.log).states.get('a-1')?.consumed, true);
  // 改最后一行（解除作废）、删中间一行、换一把密钥重算（伪造）都断链；截断末尾的行发现不了（已知局限，靠 S1 与卷的访问控制）
  assert.throws(() => replayAudit(lines.map((l) => l.replace('"consumed"', '"verify_failed"')).join('\n'), keys.log), /哈希链断开/);
  assert.throws(() => replayAudit([lines[0], lines[2]].join('\n'), keys.log), /哈希链断开/);
  assert.throws(() => replayAudit(text, randomKeys().log), /哈希链断开/);
  assert.equal(replayAudit(lines.slice(0, -1).join('\n'), keys.log).states.get('a-1')?.consumed, false);
  writeFileSync(f.auditFile, lines.map((l) => l.replace('"consumed"', '"verify_failed"')).join('\n') + '\n', { mode: 0o600 });
  assert.throws(() => restart(f), /哈希链断开/);
});

test('S2：条目文件对属组或其他用户可写、审计文件权限宽于 0600 都拒绝启动', () => {
  const f = files([entry('a-1', PA, '246810')]);
  chmodSync(f.entriesFile, 0o620);
  assert.throws(() => make(f), /条目文件不能对属组或其他用户可写/);
  chmodSync(f.entriesFile, 0o600);
  chmodSync(f.auditFile, 0o640);
  assert.throws(() => make(f), /权限不能宽于 0600/);
  chmodSync(f.auditFile, 0o600);
  make(f);
});

test('H2：单实例锁——另一个持有者心跳新鲜时拒绝，过期后可接管', () => {
  const lock = join(dir, 'lock-test.lock');
  takeLock(lock, T0, { host: 'h1', pid: 1 });
  assert.throws(() => takeLock(lock, T0 + 1000, { host: 'h2', pid: 2 }), /另一个 BFF 实例/);
  takeLock(lock, T0 + 1000, { host: 'h1', pid: 1 });   // 自己刷新心跳
  takeLock(lock, T0 + 1000 + LOCK_STALE_MS + 1, { host: 'h2', pid: 2 });
  const f = files([entry('a-1', PA, '246810')]);
  make(f);
  assert.throws(() => make(f), /另一个 BFF 实例/);
});

test('签发：白名单内有效条目给条目的码；不在白名单、过期、未生效给 null（调用方用随机码）；库写成功后才记账', () => {
  const f = files([entry('a-1', PA, '246810'), entry('b-1', PB, '135791', '2026-12-01T02:00:00Z', '2026-12-01T03:00:00Z')]);
  const otp = make(f);
  const p = otp.pick(PA);
  assert.deepEqual(p, { code: '246810', entry: 'a-1' });
  assert.equal(otp.stateOf('a-1')?.issues ?? 0, 0, 'pick 不记账');
  otp.markIssued(PA, 'a-1');
  assert.equal(otp.stateOf('a-1')?.issues, 1);
  assert.equal(otp.pick(PC), null);   // 不在白名单
  assert.equal(otp.pick(PB), null);   // 未生效
  const late = make(files([entry('a-2', PA, '246810')]), () => Date.parse('2026-12-01T12:00:00Z'));
  assert.equal(late.pick(PA), null);  // not_after 当时即失效
});

test('一次性：登录成功即作废；之后再签发给 null', () => {
  const otp = make(files([entry('a-1', PA, '246810')]));
  assert.equal(issue(otp, PA), '246810');
  otp.onVerify(PA, true);
  assert.equal(otp.stateOf('a-1')?.consumed, true);
  assert.equal(issue(otp, PA), null);
});

test('S3：成功时先在内存作废，审计写失败只告警，仍作废、不抛错', () => {
  const f = files([entry('a-1', PA, '246810')]);
  const warns: Record<string, string>[] = [];
  const otp = make(f, () => T0, warns);
  issue(otp, PA);
  chmodSync(f.auditFile, 0o400);  // 写不进去（root 运行时不受文件权限限制，就跳过这一段断言）
  const root = process.getuid?.() === 0;
  otp.onVerify(PA, true);
  chmodSync(f.auditFile, 0o600);
  assert.equal(otp.stateOf('a-1')?.consumed, true);
  if (!root) assert.ok(warns.some((w) => w['event'] === 'staging_otp_audit_write_failed' && w['audit_event'] === 'consumed'));
  assert.ok(!JSON.stringify(warns).includes('246810') && !JSON.stringify(warns).includes(PA));
});

test(`限次：每条目签发 ≤ ${MAX_ISSUES} 次；失败 ${MAX_FAILURES} 次锁定`, () => {
  const otp = make(files([entry('a-1', PA, '246810'), entry('b-1', PB, '135791')]));
  for (let i = 0; i < MAX_ISSUES; i++) assert.equal(issue(otp, PA), '246810');
  assert.equal(issue(otp, PA), null);
  assert.equal(issue(otp, PB), '135791');
  for (let i = 0; i < MAX_FAILURES - 1; i++) otp.onVerify(PB, false);
  assert.equal(otp.stateOf('b-1')?.locked, false);
  otp.onVerify(PB, false);
  assert.equal(otp.stateOf('b-1')?.locked, true);
  assert.equal(issue(otp, PB), null);
  otp.onVerify(PC, true);  // 没有用条目签发过的号码：不记账
  assert.equal(otp.stateOf('c-1'), undefined);
});

test('审计：每个事件一行，只有 at、event、entry、phone_hmac、prev；不含码与号码原文；所有号码都有同等的文件写入（S4）', () => {
  const f = files([entry('a-1', PA, '246810')]);
  const otp = make(f);
  issue(otp, PA);
  otp.onVerify(PA, false);
  otp.onVerify(PA, true);
  issue(otp, PC);
  otp.onVerify(PC, false);
  const text = readFileSync(f.auditFile, 'utf8');
  assert.doesNotMatch(text, /246810/);
  assert.ok(!text.includes(PA) && !text.includes(PC) && !text.includes('99900003'));
  const lines = text.trim().split('\n').map((l) => JSON.parse(l));
  assert.deepEqual(lines.map((l) => l.event), ['issued', 'verify_failed', 'consumed', 'not_whitelisted', 'verify_other']);
  for (const l of lines) assert.deepEqual(Object.keys(l).sort(), ['at', 'entry', 'event', 'mac', 'phone_hmac', 'prev']);
  assert.equal(lines[0].phone_hmac, phoneHash(keys, PA));
});

test('重启恢复：作废、锁定、签发次数与待核验的条目都从审计文件恢复', () => {
  const f = files([entry('a-1', PA, '246810'), entry('b-1', PB, '135791')]);
  const first = make(f);
  issue(first, PA);
  issue(first, PB);
  issue(first, PB);
  for (let i = 0; i < MAX_FAILURES; i++) first.onVerify(PB, false);
  const second = restart(f);
  assert.equal(second.stateOf('a-1')?.issues, 1);
  assert.equal(second.stateOf('b-1')?.locked, true);
  assert.equal(issue(second, PB), null);
  second.onVerify(PA, true);
  const third = restart(f, () => T0 + LOCK_STALE_MS + 1);
  assert.equal(third.stateOf('a-1')?.consumed, true);
  assert.equal(issue(third, PA), null);
  assert.throws(() => replayAudit(`{"at":"x","event":"other","entry":null,"phone_hmac":null,"prev":"${GENESIS}","mac":"0"}`, keys.log), /格式不对/);
  assert.throws(() => replayAudit(`{"at":"x","event":"issued","entry":"BAD ID","phone_hmac":null,"prev":"${GENESIS}","mac":"0"}`, keys.log),
                /格式不对/);
});

const ACC_A = '9a590000-0000-4000-8000-0000000000a1';
const ACC_B = '9a590000-0000-4000-8000-0000000000b2';
const ACC_X = '9a590000-0000-4000-8000-0000000000ff';

test('作废（LT-06 退出测试，按账号）：登录成功时条目与账号对应；按账号作废该号码的全部条目（含已用过、未用的），审计逐条记 revoked、不含码、号码与账号；不再签发；重启后仍作废；不重复记', () => {
  const f = files([entry('a-1', PA, '246810'), entry('a-2', PA, '135790'), entry('b-1', PB, '135791'), entry('c-1', PC, '975310')]);
  const otp = make(f);
  assert.equal(issue(otp, PA), '246810');
  otp.onVerify(PA, true, ACC_A);    // a-1 已用过，PA 对应账号 A
  assert.equal(issue(otp, PB), '135791');  // b-1 已签发、待核验（PB 还没有登录过，对应不上账号）
  assert.equal(issue(otp, PC), '975310');
  otp.onVerify(PC, true, ACC_B);    // c-1 已用过，PC 对应账号 B
  assert.deepEqual(otp.revokeByAccounts(new Set([ACC_X])), { revoked: 0, auditFailed: false }, '没有登录过的账号对应不上任何条目');
  assert.equal(issue(otp, PA), '135790');  // a-2 已签发、待核验
  const r = otp.revokeByAccounts(new Set([ACC_A.toUpperCase()]));
  assert.deepEqual(r, { revoked: 2, auditFailed: false });
  otp.onVerify(PA, true, ACC_A);  // 作废前签发的待核验条目已清掉：不再记 consumed
  assert.equal(otp.stateOf('a-2')?.consumed, false, '作废清掉待核验');
  for (const id of ['a-1', 'a-2']) assert.equal(otp.stateOf(id)?.revoked, true, id);
  assert.equal(otp.stateOf('b-1')?.revoked ?? false, false, '别的号码不受影响');
  assert.equal(otp.stateOf('c-1')?.revoked ?? false, false, '别的账号不受影响');
  assert.equal(issue(otp, PA), null, '作废后不再签发');
  assert.deepEqual(otp.revokeByAccounts(new Set([ACC_A])), { revoked: 0, auditFailed: false }, '已作废的不重复记');
  const text = readFileSync(f.auditFile, 'utf8');
  const lines = text.trim().split('\n').map((l) => JSON.parse(l));
  assert.deepEqual(lines.filter((l) => l.event === 'revoked').map((l) => [l.entry, l.phone_hmac]),
                   [['a-1', phoneHash(keys, PA)], ['a-2', phoneHash(keys, PA)]]);
  const consumed = lines.filter((l) => l.event === 'consumed');
  assert.ok(consumed.length === 2 && consumed.every((l) => /^[0-9a-f]{16}$/.test(l.account_hmac)), '登录成功的审计行带账号摘要');
  assert.ok(lines.filter((l) => l.event !== 'consumed').every((l) => !('account_hmac' in l)), '其余审计行不带账号摘要');
  assert.ok(!text.includes(PA) && !text.includes('246810') && !text.includes(ACC_A) && !text.includes(ACC_B));
  // 重启：账号对应从审计恢复，按账号作废照常
  const again = restart(f);
  for (const id of ['a-1', 'a-2']) assert.equal(again.stateOf(id)?.revoked, true, `${id} 重启后仍作废`);
  assert.equal(issue(again, PA), null);
  assert.deepEqual(again.revokeByAccounts(new Set([ACC_B])), { revoked: 1, auditFailed: false }, '重启后从审计恢复账号对应');
  assert.equal(again.stateOf('c-1')?.revoked, true);
});

test('审计格式向后兼容：没有 account_hmac 的旧行照常核对哈希链；account_hmac 只能出现在 consumed 行，改了它哈希链断开', () => {
  const f = files([entry('a-1', PA, '246810')]);
  const otp = make(f);
  issue(otp, PA);
  otp.onVerify(PA, true);  // 没有账号（旧版行为）：行里没有 account_hmac
  const text = readFileSync(f.auditFile, 'utf8');
  assert.ok(!text.includes('account_hmac'));
  assert.equal(replayAudit(text, keys.log).accounts.size, 0);
  const g = files([entry('a-1', PA, '246810')]);
  const o2 = make(g);
  issue(o2, PA);
  o2.onVerify(PA, true, ACC_A);
  const t2 = readFileSync(g.auditFile, 'utf8');
  assert.equal(replayAudit(t2, keys.log).accounts.size, 1);
  const lines = t2.trim().split('\n');
  const last = JSON.parse(lines.at(-1)!) as Record<string, unknown>;
  const forged = JSON.stringify({ ...last, account_hmac: '0123456789abcdef' });
  assert.throws(() => replayAudit([...lines.slice(0, -1), forged].join('\n'), keys.log), /哈希链断开/);
  const wrongEvent = JSON.stringify({ ...JSON.parse(lines[0]!) as Record<string, unknown>, account_hmac: '0123456789abcdef' });
  assert.throws(() => replayAudit(wrongEvent, keys.log), /格式不对/);
});

test('作废：审计写失败只告警并标出（内存里仍作废、不抛错）', () => {
  const f = files([entry('a-1', PA, '246810'), entry('a-2', PA, '135790')]);
  const warns: Record<string, string>[] = [];
  const otp = make(f, () => T0, warns);
  issue(otp, PA);
  otp.onVerify(PA, true, ACC_A);
  chmodSync(f.auditFile, 0o400);
  const root = process.getuid?.() === 0;
  const r = otp.revokeByAccounts(new Set([ACC_A]));
  chmodSync(f.auditFile, 0o600);
  assert.equal(r.revoked, 2);
  assert.equal(otp.stateOf('a-1')?.revoked, true);
  assert.equal(otp.stateOf('a-2')?.revoked, true);
  if (!root) {
    assert.equal(r.auditFailed, true);
    assert.ok(warns.some((w) => w['event'] === 'staging_otp_audit_write_failed' && w['audit_event'] === 'revoked'));
  }
});

test('条目文件可热更新；坏了保留上一份并记 config_invalid', () => {
  const f = files([entry('a-1', PA, '246810')]);
  const otp = make(f);
  writeFileSync(f.entriesFile, JSON.stringify({ entries: [entry('a-1', PA, '246810'), entry('c-1', PC, '975310')] }));
  assert.equal(issue(otp, PC), '975310');
  writeFileSync(f.entriesFile, 'broken');
  assert.equal(issue(otp, PA), '246810');
  assert.match(readFileSync(f.auditFile, 'utf8'), /config_invalid/);
});

test('staging 辅助：内网 IPv4、服务间地址、监听地址、镜像环境标记', () => {
  for (const h of ['10.0.0.1', '172.16.0.1', '172.31.255.255', '192.168.1.1']) assert.ok(privateIpv4(h), h);
  for (const h of ['172.32.0.1', '8.8.8.8', '127.0.0.1', '010.0.0.1', '10.0.0', '10.0.0.256', 'bff', '::1']) assert.ok(!privateIpv4(h), h);
  assert.ok(serviceUrlOk('http://127.0.0.1:8788', 'test') && serviceUrlOk('https://svc.example.invalid', 'production'));
  assert.ok(!serviceUrlOk('http://172.28.0.10:8788', 'test') && !serviceUrlOk('http://172.28.0.10:8788', 'pilot'));
  assert.ok(serviceUrlOk('http://172.28.0.10:8788', 'staging'));
  assert.ok(!serviceUrlOk('http://8.8.8.8:8788', 'staging') && !serviceUrlOk('http://learning:8788', 'staging'));
  assert.ok(!serviceUrlOk('http://172.28.0.10.example.invalid:8788', 'staging'));
  assert.equal(listenHost(undefined, 'production'), '127.0.0.1');
  assert.equal(listenHost('172.28.0.30', 'staging'), '172.28.0.30');
  for (const env of ['dev', 'test', 'pilot', 'production'] as const) assert.throws(() => listenHost('172.28.0.30', env), /只有 staging/);
  for (const h of ['0.0.0.0', '8.8.8.8', 'localhost', '::']) assert.throws(() => listenHost(h, 'staging'), /BFF_HOST/);
  checkImageEnv('staging', undefined);
  checkImageEnv('staging', 'staging');
  for (const env of ['dev', 'test'] as const) assert.throws(() => checkImageEnv(env, 'staging'), /STARBUDDY_IMAGE_ENV/);
});

test('同意版本（决定记录第 10 条，S6）：staging 拒绝合成版本（不区分大小写），只收登记的真实版本；dev / test、pilot / 生产不变', () => {
  const reg = parseConsentVersions('staging', 'privacy_policy=privacy@2026-10-v1, guardian_consent_under14=guardian-consent@2026-10-v1');
  assert.ok(consentVersionAllowed('staging', reg, 'privacy_policy', 'privacy@2026-10-v1'));
  assert.ok(!consentVersionAllowed('staging', reg, 'privacy_policy', 'privacy@2026-11-v2'));
  assert.ok(!consentVersionAllowed('staging', reg, 'voice_processing', 'privacy@2026-10-v1'));
  assert.ok(!consentVersionAllowed('staging', reg, 'privacy_policy', 'privacy@synthetic-v1'));
  assert.ok(!consentVersionAllowed('staging', parseConsentVersions('staging', undefined), 'privacy_policy', 'privacy@2026-10-v1'));
  assert.ok(consentVersionAllowed('test', undefined, 'privacy_policy', 'privacy@synthetic-v1'));
  assert.ok(!consentVersionAllowed('pilot', undefined, 'privacy_policy', 'privacy@synthetic-v1'));
  assert.ok(!consentVersionAllowed('pilot', undefined, 'privacy_policy', 'privacy@SYNTHETIC-v1'));
  assert.ok(consentVersionAllowed('pilot', undefined, 'privacy_policy', 'privacy@2026-10-v1'));
  assert.ok(isSyntheticConsentVersion('privacy@Synthetic-0') && !isSyntheticConsentVersion('privacy@2026-10-v1'));
  for (const bad of ['privacy_policy=privacy@synthetic-v1', 'privacy_policy=privacy@SYNTHETIC-v1', 'unknown_type=privacy@v1', 'privacy@v1',
                     'privacy_policy=privacy@v1=x']) {
    assert.throws(() => parseConsentVersions('staging', bad), /BFF_CONSENT_VERSIONS/, bad);
  }
  assert.throws(() => parseConsentVersions('test', 'privacy_policy=privacy@v1'), /只在 staging/);
  assert.equal(parseConsentVersions('test', undefined), undefined);
});
