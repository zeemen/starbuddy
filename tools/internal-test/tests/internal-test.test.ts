// tools/internal-test 的单测与端到端（specs/launch-lt06-internal-test-ops 范围第 1 条）。全部合成：号码用未分配的国家码 +999，
// 码是合成的 6 位数字，只写在临时目录里（规则 8）。与 BFF 互验：生成的条目文件由 BFF 的 StagingOtp 直接加载并签发；
// 工具按批次作废后 BFF 不再签发；BFF 在退出测试时作废的条目出现在工具的审计摘要里（事件同为 revoked），哈希链用同一把密钥核对。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { randomKeys } from '../../../services/bff/src/http/config.ts';
import { phoneHash } from '../../../services/bff/src/http/secrets.ts';
import { LOCK_STALE_MS, parseEntries, StagingOtp } from '../../../services/bff/src/http/staging-otp.ts';
import { generate, ledgerKey, ledgerText, parseKey, parseLedger, parsePhones, readExisting, renderEntries, revokeBatch, summary, ToolError, usedIds }
  from '../src/lib.ts';
import { run, writePrivateOut } from '../src/main.ts';

const dir = mkdtempSync(join(tmpdir(), 'lt06-tool-'));
after(() => rmSync(dir, { recursive: true, force: true }));
const MAIN = fileURLToPath(new URL('../src/main.ts', import.meta.url));
const P1 = '+99900007001';
const P2 = '+99900007002';
const P3 = '+99900007003';
const T0 = new Date('2026-12-01T01:00:00Z');
let codeSeq = 0;
const fixedCode = () => String(314100 + (codeSeq++ % 99)).padStart(6, '0');
const keys = randomKeys();
const keyHex = keys.log.toString('hex');
let fileSeq = 0;
const tmp = (name: string) => join(dir, `${++fileSeq}-${name}`);
/** 台账里的手机号 HMAC：用从日志密钥派生的子密钥（与 BFF 审计里的 phone_hmac 不同） */
const ledgerHash = (p: string) => phoneHash({ ...keys, log: ledgerKey(keys.log) }, p);
const noSecrets = (text: string, codes: string[] = []) => {
  for (const s of [P1, P2, P3, '99900007', ...codes]) assert.ok(!text.includes(s), `不含 ${s}`);
};

test('号码：每行一个 E.164，空行忽略；格式不对、重复、为空都拒绝，报错只带行号不带号码', () => {
  assert.deepEqual(parsePhones(`${P1}\n\n  ${P2}  \r\n`), [P1, P2]);
  for (const [text, want] of [[`${P1}\n13800000000`, /第 2 行不是 E.164/], [`${P1}\n${P1}`, /第 2 行与前面的号码重复/], ['\n \n', /没有号码/]] as const) {
    assert.throws(() => parsePhones(text), (e: Error) => e instanceof ToolError && want.test(e.message) && !e.message.includes('99900007')
                                                        && !e.message.includes('13800000000'));
  }
  assert.throws(() => parsePhones(Array.from({ length: 1000 }, (_, i) => `+9990001${String(i).padStart(4, '0')}`).join('\n')), /最多 999/);
});

test('生成：id = 批次-三位序号；窗口 = issued_at 起 1–24 小时；整份文件过 BFF 的 parseEntries；台账只有 id、批次、事件、时间与手机号 HMAC', () => {
  const r = generate({ batch: 'dec-a', phones: [P1, P2], issuedAt: T0, hours: 24, existing: [], used: new Set(), code: fixedCode, key: keys.log });
  assert.deepEqual(r.added.map((e) => [e.id, e.phone, e.issued_at, e.not_after]), [
    ['dec-a-001', P1, '2026-12-01T01:00:00.000Z', '2026-12-02T01:00:00.000Z'], ['dec-a-002', P2, '2026-12-01T01:00:00.000Z', '2026-12-02T01:00:00.000Z']]);
  assert.ok(r.added.every((e) => /^[0-9]{6}$/.test(e.code)));
  const text = renderEntries(r.entries);
  assert.equal(parseEntries(text).length, 2, 'BFF 的解析逻辑接受');
  assert.deepEqual(r.ledger.map((l) => [l.event, l.entry, l.batch, l.phone_hmac]),
                   [['generated', 'dec-a-001', 'dec-a', ledgerHash(P1)], ['generated', 'dec-a-002', 'dec-a', ledgerHash(P2)]]);
  assert.notEqual(ledgerHash(P1), phoneHash(keys, P1), '台账用派生子密钥：与 BFF 审计里的手机号 HMAC 对不上');
  assert.notDeepEqual(ledgerKey(keys.log), keys.log);
  const ledger = ledgerText(r.ledger);
  noSecrets(ledger, r.added.map((e) => e.code));
  assert.deepEqual(parseLedger(ledger).map((l) => l.entry), ['dec-a-001', 'dec-a-002']);
  // 没给密钥：HMAC 为 null（不能拿不对的密钥去算）
  assert.ok(generate({ batch: 'dec-b', phones: [P1], issuedAt: T0, hours: 1, existing: [], used: new Set() }).ledger.every((l) => l.phone_hmac === null));
  // 追加到现有条目文件：原有条目原样保留
  const more = generate({ batch: 'dec-c', phones: [P3], issuedAt: T0, hours: 2, existing: r.entries, used: new Set(), code: fixedCode });
  assert.deepEqual(more.entries.map((e) => e.id), ['dec-a-001', 'dec-a-002', 'dec-c-001']);
});

test('生成：窗口超过 24 小时、不是整数、批次名不合格、批次用过（台账、审计、现有文件里出现过）都拒绝', () => {
  const base = { phones: [P1], issuedAt: T0, existing: [], used: new Set<string>(), code: fixedCode };
  for (const hours of [0, 25, 1.5, Number.NaN, -1]) {
    assert.throws(() => generate({ ...base, batch: 'h', hours }), /--hours 必须是 1–24 的整数/, String(hours));
  }
  for (const batch of ['', 'Dec', 'dec_a', '-dec', 'a'.repeat(32), 'dec-001', 'dec a']) {
    assert.throws(() => generate({ ...base, batch, hours: 1 }), /批次名只能是/, batch);
  }
  assert.throws(() => generate({ ...base, batch: 'dec-a', hours: 1, used: new Set(['dec-a-003']) }), /已经用过/);
  const existing = generate({ ...base, batch: 'dec-x', hours: 1 }).entries;
  assert.throws(() => generate({ ...base, batch: 'dec-x', hours: 1, existing }), /已经用过/);
  // 前缀相同的另一个批次不算用过
  assert.equal(generate({ ...base, batch: 'dec', hours: 1, used: new Set(['dec-x-001', 'dec-a-001']) }).added[0]!.id, 'dec-001');
  assert.throws(() => generate({ ...base, batch: 'dec', hours: 1, used: new Set(['dec-001']) }), /已经用过/);
  const used = usedIds(ledgerText([{ at: T0.toISOString(), event: 'revoked', entry: 'old-001', batch: 'old', phone_hmac: null }]),
                       JSON.stringify({ at: T0.toISOString(), event: 'consumed', entry: 'bff-007', phone_hmac: 'a'.repeat(16), prev: '0'.repeat(64), mac: 'b' }));
  assert.deepEqual([...used].sort(), ['bff-007', 'old-001']);
});

test('验证码：缺省用 CSPRNG（BFF 的 newCode）生成 6 位数字', () => {
  const r = generate({ batch: 'rnd', phones: Array.from({ length: 200 }, (_, i) => `+9990002${String(i).padStart(4, '0')}`), issuedAt: T0, hours: 1,
                       existing: [], used: new Set() });
  assert.ok(r.added.every((e) => /^[0-9]{6}$/.test(e.code)));
  assert.ok(new Set(r.added.map((e) => e.code)).size > 150, '200 个码不会大量重复');
  assert.ok(!readFileSync(fileURLToPath(new URL('../src/lib.ts', import.meta.url)), 'utf8').includes('Math.random'), '不用 Math.random');
});

test('按批次作废：只删该批次（前缀相同的别的批次不动）；批次不存在报错；台账记 revoked', () => {
  const a = generate({ batch: 'b1', phones: [P1, P2], issuedAt: T0, hours: 4, existing: [], used: new Set(), code: fixedCode });
  const all = generate({ batch: 'b1-x', phones: [P3], issuedAt: T0, hours: 4, existing: a.entries, used: new Set(), code: fixedCode }).entries;
  const r = revokeBatch(all, 'b1', keys.log);
  assert.deepEqual(r.entries.map((e) => e.id), ['b1-x-001']);
  assert.deepEqual(r.removed.map((e) => e.id), ['b1-001', 'b1-002']);
  assert.deepEqual(r.ledger.map((l) => [l.event, l.entry, l.phone_hmac]), [['revoked', 'b1-001', ledgerHash(P1)], ['revoked', 'b1-002', ledgerHash(P2)]]);
  assert.throws(() => revokeBatch(all, 'b2'), /没有批次 b2 的条目/);
  assert.equal(parseEntries(renderEntries(r.entries)).length, 1);
  assert.deepEqual(revokeBatch([...r.entries], 'b1-x').entries, [], '作废到空文件也是合格的条目文件');
});

test('与 BFF 互验：工具生成的文件 BFF 直接签发；工具按批次作废后不再签发；BFF 退出时作废的条目进摘要（事件同为 revoked、哈希链核对）', () => {
  const entriesFile = tmp('otp.json');
  const auditFile = tmp('audit.jsonl');
  const gen = generate({ batch: 'x1', phones: [P1, P2], issuedAt: new Date(Date.now() - 60_000), hours: 2, existing: [], used: new Set(), key: keys.log });
  writeFileSync(entriesFile, renderEntries(gen.entries), { mode: 0o600 });
  writeFileSync(auditFile, '', { mode: 0o600 });
  let ledger = ledgerText(gen.ledger);
  const otp = new StagingOtp({ env: 'staging', keys, entriesFile, auditFile, heartbeat: false, holder: { host: 'tool', pid: 1 } });
  const p1 = otp.pick(P1);
  assert.equal(p1?.code, gen.added[0]!.code, 'BFF 用工具生成的码签发');
  otp.markIssued(P1, p1!.entry);
  // BFF 侧作废（家长退出测试，按账号）：P2 的家长用固定码登录过（条目与账号对应），退出时按账号作废
  const ACC = '9a590000-0000-4000-8000-0000000070a2';
  const p2 = otp.pick(P2);
  otp.markIssued(P2, p2!.entry);
  otp.onVerify(P2, true, ACC);
  assert.equal(otp.revokeByAccounts(new Set([ACC])).revoked, 1);
  assert.equal(otp.pick(P2), null);
  // 工具侧按批次作废：从文件里删掉批次 x1，BFF 重读后不再签发 P1
  const rv = revokeBatch(readExisting(readFileSync(entriesFile, 'utf8')), 'x1', keys.log);
  writeFileSync(entriesFile, renderEntries(rv.entries), { mode: 0o600 });
  ledger += ledgerText(rv.ledger);
  assert.equal(otp.pick(P1), null, '工具作废后 BFF 不再签发');
  const audit = readFileSync(auditFile, 'utf8');
  const s = summary(ledger, audit, keys.log);
  assert.equal(s.chain_verified, true);
  assert.deepEqual(s.events.filter((e) => e.event === 'revoked').map((e) => e.entry).sort(), ['x1-001', 'x1-002', 'x1-002'],
                   'BFF 作废了 x1-002，工具又按批次作废了 x1-001、x1-002');
  for (const e of s.events) assert.deepEqual(Object.keys(e).sort(), ['at', 'entry', 'event', 'phone_hmac']);
  assert.ok(s.events.some((e) => e.event === 'issued' && 'phone_hmac' in e && e.phone_hmac === phoneHash(keys, P1)));
  assert.ok(audit.includes('account_hmac') && !JSON.stringify(s).includes('account_hmac') && !JSON.stringify(s).includes(ACC),
            'BFF 审计里登录成功的行带账号摘要，摘要里不输出');
  noSecrets(JSON.stringify(s), gen.added.map((e) => e.code));
  // 贴工单用：去掉手机号 HMAC，只剩条目 id、事件与时间
  const ticket = summary(ledger, audit, keys.log, true);
  assert.equal(ticket.chain_verified, true);
  for (const e of ticket.events) assert.deepEqual(Object.keys(e).sort(), ['at', 'entry', 'event']);
  assert.deepEqual(ticket.counts, s.counts);
  assert.ok(!JSON.stringify(ticket).includes(phoneHash(keys, P1)) && !JSON.stringify(ticket).includes(ledgerHash(P1)));
  // 用过的批次名不能再用（台账 + 审计）
  assert.throws(() => generate({ batch: 'x1', phones: [P3], issuedAt: T0, hours: 1, existing: rv.entries, used: usedIds(ledger, audit) }), /已经用过/);
  // 审计被改：给了密钥时摘要拒绝（哈希链），没给密钥时只核对格式
  const tampered = audit.replace('"revoked"', '"consumed"');
  assert.throws(() => summary(ledger, tampered, keys.log), /审计文件核对失败/);
  assert.equal(summary(ledger, tampered, null).chain_verified, false);
  assert.throws(() => summary(ledger, audit, randomKeys().log), /审计文件核对失败/, '换一把密钥也对不上');
  // BFF 重启：工具删掉的条目已不在文件里，BFF 作废的从审计恢复
  const again = new StagingOtp({ env: 'staging', keys, entriesFile, auditFile, heartbeat: false, holder: { host: 'tool', pid: 2 },
                                 now: () => Date.now() + LOCK_STALE_MS + 1 });
  assert.equal(again.stateOf('x1-002')?.revoked, true);
});

test('密钥文件：十六进制、至少 32 字节；报错不带取值', () => {
  assert.equal(parseKey(`${keyHex}\n`).length, 32);
  for (const bad of ['', 'zz', 'ab'.repeat(16), `${keyHex}x`]) {
    assert.throws(() => parseKey(bad), (e: Error) => /至少 32 字节的十六进制串/.test(e.message) && (bad === '' || !e.message.includes(bad)));
  }
});

// ---------------------------------------------------------------- 命令行（子进程，号码经标准输入）

const cli = (args: string[], input = '') => spawnSync(process.execPath, ['--no-warnings', MAIN, ...args], { input, encoding: 'utf8', cwd: dir });

test('命令行 generate → revoke → summary：号码只经标准输入、条目文件只到标准输出；台账 0600、不含号码与码；不写别的文件', () => {
  const work = mkdtempSync(join(dir, 'cli-'));
  const ledger = join(work, 'ledger.jsonl');
  const keyFile = join(work, 'log.key');
  writeFileSync(keyFile, keyHex, { mode: 0o600 });
  const g = cli(['generate', '--batch', 'dec-1', '--hours', '12', '--issued-at', '2026-12-01T09:00:00+08:00', '--ledger', ledger, '--key-file', keyFile],
                `${P1}\n${P2}\n`);
  assert.equal(g.status, 0, g.stderr);
  const doc = JSON.parse(g.stdout) as { entries: { id: string; phone: string; code: string; issued_at: string; not_after: string }[] };
  assert.deepEqual(doc.entries.map((e) => [e.id, e.phone, e.not_after]), [['dec-1-001', P1, '2026-12-01T13:00:00.000Z'], ['dec-1-002', P2, '2026-12-01T13:00:00.000Z']]);
  noSecrets(g.stderr, doc.entries.map((e) => e.code));
  assert.equal(statSync(ledger).mode & 0o777, 0o600);
  noSecrets(readFileSync(ledger, 'utf8'), doc.entries.map((e) => e.code));
  assert.deepEqual(readdirSync(work).sort(), ['ledger.jsonl', 'log.key'], '工具不写条目文件、不写临时文件');
  // 管理员把标准输出导入密钥管理；这里写进临时目录只为下一步作废（模拟挂载的条目文件，0600）
  const mounted = join(work, 'mounted.json');
  writeFileSync(mounted, g.stdout, { mode: 0o600 });
  const again = cli(['generate', '--batch', 'dec-1', '--hours', '1', '--ledger', ledger, '--existing', mounted], `${P3}\n`);
  assert.equal(again.status, 1);
  assert.match(again.stderr, /已经用过/);
  const add = cli(['generate', '--batch', 'dec-2', '--hours', '1', '--ledger', ledger, '--existing', mounted], `${P3}\n`);
  assert.equal(add.status, 0, add.stderr);
  assert.deepEqual((JSON.parse(add.stdout) as typeof doc).entries.map((e) => e.id), ['dec-1-001', 'dec-1-002', 'dec-2-001']);
  const r = cli(['revoke', '--batch', 'dec-1', '--existing', mounted, '--ledger', ledger, '--key-file', keyFile]);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual((JSON.parse(r.stdout) as typeof doc).entries, []);
  const s = cli(['summary', '--ledger', ledger]);
  assert.equal(s.status, 0, s.stderr);
  const sum = JSON.parse(s.stdout) as { chain_verified: boolean; counts: Record<string, number>; events: unknown[] };
  assert.deepEqual(sum.counts, { generated: 3, revoked: 2 });
  assert.equal(sum.chain_verified, false);
  noSecrets(s.stdout, doc.entries.map((e) => e.code));
});

test('命令行 --out：只新建不覆盖、强制 0600（与 umask 无关）、标准输出不再带条目；--for-ticket 去掉手机号 HMAC；选项用错子命令为 2', () => {
  const work = mkdtempSync(join(dir, 'cli-out-'));
  const ledger = join(work, 'ledger.jsonl');
  const keyFile = join(work, 'log.key');
  writeFileSync(keyFile, keyHex, { mode: 0o600 });
  const out = join(work, 'entries.json');
  const umask = spawnSync('sh', ['-c', `umask 000; exec "${process.execPath}" --no-warnings "${MAIN}" generate --batch out-1 --hours 2 --ledger "${ledger}" --key-file "${keyFile}" --out "${out}"`],
                          { input: `${P1}\n`, encoding: 'utf8', cwd: work });
  assert.equal(umask.status, 0, umask.stderr);
  assert.equal(umask.stdout, '', '给了 --out 时标准输出不带条目');
  assert.equal(statSync(out).mode & 0o777, 0o600, 'umask 000 下仍是 0600');
  assert.equal(parseEntries(readFileSync(out, 'utf8')).length, 1);
  const twice = cli(['generate', '--batch', 'out-2', '--hours', '1', '--ledger', ledger, '--out', out], `${P2}\n`);
  assert.equal(twice.status, 1, '已存在的文件不覆盖');
  assert.match(twice.stderr, /不覆盖/);
  assert.equal(parseLedger(readFileSync(ledger, 'utf8')).length, 1, '拒绝时不追加台账');
  const rv = cli(['revoke', '--batch', 'out-1', '--existing', out, '--ledger', ledger, '--out', join(work, 'kept.json')]);
  assert.equal(rv.status, 0, rv.stderr);
  assert.equal(statSync(join(work, 'kept.json')).mode & 0o777, 0o600);
  const t = cli(['summary', '--ledger', ledger, '--key-file', keyFile, '--for-ticket']);
  assert.equal(t.status, 0, t.stderr);
  assert.ok(!t.stdout.includes('phone_hmac') && !t.stdout.includes(ledgerHash(P1)), '工单摘要不带手机号 HMAC');
  assert.equal(cli(['summary', '--ledger', ledger, '--out', join(work, 'x.json')]).status, 2);
  // 写出函数本身也不覆盖（不只靠命令行先查文件在不在）
  const exists = join(work, 'exists.json');
  writeFileSync(exists, 'old', { mode: 0o600 });
  assert.throws(() => writePrivateOut(exists, 'new'), /不覆盖/);
  assert.equal(readFileSync(exists, 'utf8'), 'old');
  assert.equal(cli(['generate', '--batch', 'out-3', '--hours', '1', '--ledger', ledger, '--for-ticket'], `${P3}\n`).status, 2);
});

test('命令行：参数不对 2；密钥文件或条目文件权限过宽、窗口超 24 小时、号码不合格 1；报错不带号码', () => {
  const work = mkdtempSync(join(dir, 'cli-bad-'));
  const ledger = join(work, 'ledger.jsonl');
  assert.equal(cli(['nope']).status, 2);
  assert.equal(cli(['generate', '--batch', 'a']).status, 2);
  assert.equal(cli(['generate', '--batch', 'a', '--hours', '1', '--ledger', ledger, '--unknown', 'x'], P1).status, 2);
  assert.equal(cli(['generate', '--batch', 'a', '--hours', '1', '--ledger', ledger, '--issued-at', '2026-12-01 09:00'], P1).status, 2);
  const wide = join(work, 'wide.key');
  writeFileSync(wide, keyHex, { mode: 0o644 });
  chmodSync(wide, 0o644);
  const k = cli(['generate', '--batch', 'a', '--hours', '1', '--ledger', ledger, '--key-file', wide], P1);
  assert.equal(k.status, 1);
  assert.match(k.stderr, /权限不能宽于 0600/);
  const h = cli(['generate', '--batch', 'a', '--hours', '25', '--ledger', ledger], P1);
  assert.equal(h.status, 1);
  assert.match(h.stderr, /窗口 ≤ 24 小时/);
  const bad = cli(['generate', '--batch', 'a', '--hours', '1', '--ledger', ledger], `${P1}\n12345\n`);
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /第 2 行不是 E.164/);
  noSecrets(bad.stderr + k.stderr + h.stderr);
  assert.ok(!existsSync(ledger), '失败时不写台账');
});

test('run 的注入接口：意外错误不打印细节', () => {
  const err: string[] = [];
  const code = run(['generate', '--batch', 'z', '--hours', '1', '--ledger', join(dir, 'missing-dir', 'ledger.jsonl')],
                   { stdin: () => `${P1}\n`, stdout: () => undefined, stderr: (s) => err.push(s) });
  assert.equal(code, 1);
  assert.match(err.join(''), /意外错误/);
  noSecrets(err.join(''));
});
