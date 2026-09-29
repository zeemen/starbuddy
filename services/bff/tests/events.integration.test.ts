// 行为事件与探针接口集成测试（冲刺 2-L 验收：只有学生本人能写、白名单外键与自由文本拒绝、幂等重放不重复
// （首次 201、重放 200 与 idempotent-replayed 响应头）、内容不同回 409 IDEMPOTENCY_KEY_REUSED、24 小时写入上限回 429、
// 探针关联作答、返回体不回显 payload、日志不带 payload 与学生 ID 原文）。
// 一次性 Postgres，登录角色 bff_app 只继承 starbuddy_bff；合成家庭来自 db/seeds/dev/star-map-synthetic.sql（规则 8）。
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import pg from 'pg';
import { createStarMapServer } from '../src/star-map/server.ts';

const HAS_DB = Boolean(process.env['PGHOST']);
const ROOT = new URL('../../../', import.meta.url);
const ID = {
  gS: '5a000000-0000-4000-8000-0000000000a1',  // S1、S2 的家长
  gO: '5a000000-0000-4000-8000-0000000000a2',  // 无关家庭的家长
  S1acc: '5a000000-0000-4000-8000-0000000000b1',
  S2acc: '5a000000-0000-4000-8000-0000000000b2',
  S1: '5a000000-0000-4000-8000-0000000000c1',
  S2: '5a000000-0000-4000-8000-0000000000c2',
  missing: '5a000000-0000-4000-8000-00000000ffff',
  S1zxEvidence: '5a000000-0000-4000-8000-0000000000f2',  // 种子里 S1 的道法合成证据
  S2zxEvidence: '5a000000-0000-4000-8000-0000000000f4',
};
const ITEM = 'SYN-ZX-ITEM-01';  // 种子里的合成道法题

let su: pg.Pool;
let bff: pg.Pool;
let server: Server;
let base = '';
const logs: Record<string, unknown>[] = [];

before(async () => {
  if (!HAS_DB) return;
  su = new pg.Pool({ user: 'postgres', max: 3 });
  await su.query(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN
      CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;
      CREATE ROLE scheduler_app LOGIN IN ROLE starbuddy_scheduler;
    END IF; END $$;`);
  const seeded = await su.query(`SELECT 1 FROM platform.household WHERE id = '5a000000-0000-4000-8000-0000000000d1'`);
  if (seeded.rowCount === 0) {
    const register = execFileSync(process.execPath, ['--no-warnings', 'scripts/register-content.ts'],
      { cwd: new URL('adapters/aya/', ROOT), encoding: 'utf8', maxBuffer: 16 << 20 });
    await su.query(register);
    const seeder = await su.connect();
    try {
      await seeder.query(`SET starbuddy.allow_synthetic_seed = 'on'`);
      await seeder.query(readFileSync(new URL('db/seeds/dev/star-map-synthetic.sql', ROOT), 'utf8'));
    } finally {
      seeder.release(true);
    }
  }
  bff = new pg.Pool({ user: 'bff_app', max: 5 });
  server = createStarMapServer({ pool: bff, devAuth: true, log: (l) => logs.push(l) });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  if (!HAS_DB) return;
  await new Promise<void>((r) => server.close(() => r()));
  await bff.end();
  await su.end();
});

const url = (student: string, sub: string) => `${base}/bff/v0/students/${student}/${sub}`;
const post = (u: string, account?: string, body?: unknown, type = 'application/json') =>
  fetch(u, {
    method: 'POST',
    headers: { ...(account ? { 'x-dev-account-id': account } : {}), ...(body !== undefined ? { 'content-type': type } : {}) },
    body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body),
  });
const now = () => new Date().toISOString();
const start = (over: Record<string, unknown> = {}) =>
  ({ kind: 'session_start', payload: { entry: 'today' }, occurred_at: now(), idempotency_key: randomUUID(), ...over });
const probe = (over: Record<string, unknown> = {}) =>
  ({ subject: 'zhixing', item_id: ITEM, prediction: 'unsure', asked_at: now(), idempotency_key: randomUUID(), ...over });
const count = async (q: string, args: unknown[]) => Number((await su.query(q, args)).rows[0].n);

/** 以学习服务的身份造一条合成作答（发生在现在，落在已建好的月分区里） */
async function syntheticAttempt(student: string, evidence: string, item = ITEM): Promise<string> {
  const id = randomUUID();
  await su.query(`INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, request_idempotency_key)
                  VALUES ($1, now(), $2, 'zhixing', $3, $4, $5)`, [id, student, item, evidence, `synthetic-probe-${id}`]);
  return id;
}

test('学生本人写四种事件：201 只回 ID，库里来源类型是客观记录', { skip: !HAS_DB }, async () => {
  const task = randomUUID();
  const bodies = [
    start(),
    { kind: 'session_end', payload: { duration_sec: 600 }, occurred_at: now(), idempotency_key: randomUUID() },
    { kind: 'plan_done', payload: { plan_task_id: task }, occurred_at: now(), idempotency_key: randomUUID() },
    { kind: 'plan_skip', payload: { plan_task_id: task }, occurred_at: new Date().toISOString().replace('Z', '+00:00'), idempotency_key: randomUUID() },
  ];
  for (const b of bodies) {
    const r = await post(url(ID.S1, 'events'), ID.S1acc, b);
    assert.equal(r.status, 201, b.kind);
    assert.equal(r.headers.get('cache-control'), 'no-store');
    assert.equal(r.headers.get('idempotent-replayed'), 'false', '首次写入不是重放');
    const text = await r.text();
    const json = JSON.parse(text) as Record<string, unknown>;
    assert.deepEqual(Object.keys(json), ['id'], '只回记录 ID');
    for (const echo of ['today', '600', task, b.kind]) assert.ok(!text.includes(echo), `不回显 ${echo}`);
    const { rows } = await su.query(`SELECT student_id, kind, source_type::text AS s FROM insight.behavior_event WHERE id = $1`, [json['id']]);
    assert.deepEqual(rows[0], { student_id: ID.S1, kind: b.kind, s: 'objective' });
  }
});

test('家长、其他学生、别家家长、学生不存在：同一个 403；未登录 401', { skip: !HAS_DB }, async () => {
  const before = await count(`SELECT count(*) AS n FROM insight.behavior_event`, []);
  const ref = await post(url(ID.missing, 'events'), ID.S1acc, start());
  assert.equal(ref.status, 403);
  const refBody = await ref.text();
  for (const [student, account] of [[ID.S1, ID.gS], [ID.S1, ID.S2acc], [ID.S1, ID.gO], [ID.S2, ID.S1acc], ['not-a-uuid', ID.S1acc]] as const) {
    const r = await post(url(student, 'events'), account, start());
    assert.equal(r.status, 403, `${student} ${account}`);
    assert.equal(await r.text(), refBody);
    const p = await post(url(student, 'probes'), account, probe());
    assert.equal(p.status, 403, `probe ${student} ${account}`);
  }
  assert.equal((await post(url(ID.S1, 'events'), undefined, start())).status, 401);
  assert.equal((await post(url(ID.S1, 'probes'), undefined, probe())).status, 401);
  assert.equal(await count(`SELECT count(*) AS n FROM insight.behavior_event`, []), before, '被拒的请求一条都没写进去');
});

test('白名单外键、自由文本、类型不对：422，不写入、不回显', { skip: !HAS_DB }, async () => {
  const before = await count(`SELECT count(*) AS n FROM insight.behavior_event WHERE student_id = $1`, [ID.S1]);
  const bad: [string, unknown][] = [
    ['payload 带 mood', start({ payload: { entry: 'today', mood: 'tired' } })],
    ['payload 只有 mood', start({ payload: { mood: 'tired' } })],
    ['自由文本当入口', start({ payload: { entry: '今天好累不想学' } })],
    ['session_end 带备注', { kind: 'session_end', payload: { duration_sec: 60, note: '合成备注' }, occurred_at: now(), idempotency_key: randomUUID() }],
    ['时长是字符串', { kind: 'session_end', payload: { duration_sec: '60' }, occurred_at: now(), idempotency_key: randomUUID() }],
    ['时长是小数', { kind: 'session_end', payload: { duration_sec: 60.5 }, occurred_at: now(), idempotency_key: randomUUID() }],
    ['时长超上限', { kind: 'session_end', payload: { duration_sec: 86401 }, occurred_at: now(), idempotency_key: randomUUID() }],
    ['计划任务 ID 不是 UUID', { kind: 'plan_done', payload: { plan_task_id: 'x' }, occurred_at: now(), idempotency_key: randomUUID() }],
    ['学生端不能写申诉', { kind: 'appeal', payload: {}, occurred_at: now(), idempotency_key: randomUUID() }],
    ['学生端不能写派生事件', { kind: 'exit_after_errors', payload: {}, occurred_at: now(), idempotency_key: randomUUID() }],
    ['顶层多一个键', { ...start(), note: '合成备注' }],
    ['顶层带 student_id', { ...start(), student_id: ID.S1 }],
    ['缺 idempotency_key', { kind: 'session_start', payload: { entry: 'today' }, occurred_at: now() }],
    ['幂等键不是 UUIDv4', start({ idempotency_key: '5a000000-0000-1000-8000-000000000001' })],
    ['时间不带时区', start({ occurred_at: '2026-09-29T10:00:00' })],
    ['时间不是 RFC 3339', start({ occurred_at: '2026/09/29 10:00' })],
    ['时间早于 1 天前（库里拦）', start({ occurred_at: new Date(Date.now() - 3 * 86400_000).toISOString() })],
    ['时间在未来（库里拦）', start({ occurred_at: new Date(Date.now() + 3600_000).toISOString() })],
  ];
  for (const [name, b] of bad) {
    const r = await post(url(ID.S1, 'events'), ID.S1acc, b);
    assert.equal(r.status, 422, name);
    const text = await r.text();
    for (const echo of ['tired', '今天好累', '合成备注']) assert.ok(!text.includes(echo), `${name} 不回显`);
  }
  assert.equal(await count(`SELECT count(*) AS n FROM insight.behavior_event WHERE student_id = $1`, [ID.S1]), before);
});

/** 409 的错误码与文案：同一幂等键内容不同，不复用会误导重试的 CONFLICT */
async function assertKeyReused(r: Response, name: string): Promise<void> {
  assert.equal(r.status, 409, name);
  const body = (await r.json()) as Record<string, unknown>;
  assert.equal(body['code'], 'IDEMPOTENCY_KEY_REUSED', name);
  assert.equal(body['title'], 'idempotency key reused with different content', name);
  assert.ok(!/retry/i.test(String(body['title'])), `${name}：文案不能暗示原样重试`);
}

test('同一幂等键重放：首次 201、重放 200 与 idempotent-replayed: true、同一个 ID、只有一行；内容不同 409', { skip: !HAS_DB }, async () => {
  const b = start({ payload: { entry: 'star_map' } });
  const first = await post(url(ID.S2, 'events'), ID.S2acc, b);
  assert.equal(first.status, 201);
  assert.equal(first.headers.get('idempotent-replayed'), 'false');
  const again = await post(url(ID.S2, 'events'), ID.S2acc, b);
  assert.equal(again.status, 200, '重放回 200');
  assert.equal(again.headers.get('idempotent-replayed'), 'true');
  assert.equal(again.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await again.json(), await first.json());
  assert.equal(await count(`SELECT count(*) AS n FROM insight.behavior_event WHERE idempotency_key = $1`, [b.idempotency_key]), 1);
  await assertKeyReused(await post(url(ID.S2, 'events'), ID.S2acc, { ...b, payload: { entry: 'subject' } }), '事件 payload 不同');
  await assertKeyReused(await post(url(ID.S2, 'events'), ID.S2acc, { ...b, occurred_at: now() }), '事件时间不同');
  // 探针同理
  const p = probe({ prediction: 'yes' });
  const p1 = await post(url(ID.S2, 'probes'), ID.S2acc, p);
  assert.equal(p1.status, 201);
  assert.equal(p1.headers.get('idempotent-replayed'), 'false');
  const p2 = await post(url(ID.S2, 'probes'), ID.S2acc, p);
  assert.equal(p2.status, 200);
  assert.equal(p2.headers.get('idempotent-replayed'), 'true');
  assert.deepEqual(await p2.json(), await p1.json());
  assert.equal(await count(`SELECT count(*) AS n FROM insight.confidence_probe WHERE idempotency_key = $1`, [p.idempotency_key]), 1);
  await assertKeyReused(await post(url(ID.S2, 'probes'), ID.S2acc, { ...p, prediction: 'no' }), '探针预测不同');
});

test('离线超过 1 天后的重放：仍回 200 与原 ID（先查幂等键再核对时间窗）', { skip: !HAS_DB }, async () => {
  const b = start({ payload: { entry: 'subject' } });
  const first = await post(url(ID.S2, 'events'), ID.S2acc, b);
  assert.equal(first.status, 201);
  const { id } = (await first.json()) as { id: string };
  // 合成：把这条事件的发生时间挪到 3 天前，模拟客户端离线很久后才重发同一请求
  const old = new Date(Date.now() - 3 * 86400_000).toISOString();
  await su.query(`UPDATE insight.behavior_event SET occurred_at = $2 WHERE id = $1`, [id, old]);
  const again = await post(url(ID.S2, 'events'), ID.S2acc, { ...b, occurred_at: old });
  assert.equal(again.status, 200);
  assert.equal(again.headers.get('idempotent-replayed'), 'true');
  assert.deepEqual(await again.json(), { id });
});

test('探针：学生自述入库，不存实际对错；题目未登记、学科不对、多余键 422', { skip: !HAS_DB }, async () => {
  const r = await post(url(ID.S1, 'probes'), ID.S1acc, probe());
  assert.equal(r.status, 201);
  const text = await r.text();
  assert.ok(!text.includes('unsure') && !text.includes(ITEM), '不回显预测与题目');
  const { id } = JSON.parse(text) as { id: string };
  const { rows } = await su.query(`SELECT source_type::text AS s, prediction, attempt_id FROM insight.confidence_probe WHERE id = $1`, [id]);
  assert.deepEqual(rows[0], { s: 'self', prediction: 'unsure', attempt_id: null });
  for (const [name, b] of [
    ['题目未登记', probe({ item_id: 'SYN-NOPE' })],
    ['学科不是枚举值', probe({ subject: 'math' })],
    ['预测不在三种里', probe({ prediction: 'maybe' })],
    ['预测是自由文本', probe({ prediction: '应该能吧' })],
    ['多一个 note', { ...probe(), note: '合成备注' }],
    ['多一个 correct', { ...probe(), correct: true }],
    ['题目 ID 带空格', probe({ item_id: 'SYN ZX' })],
  ] as const) {
    assert.equal((await post(url(ID.S1, 'probes'), ID.S1acc, b)).status, 422, name);
  }
});

test('探针关联作答：本人同题作答 200、重复关联幂等；别的作答 409；别人的作答 422；家长 403', { skip: !HAS_DB }, async () => {
  const { id } = (await (await post(url(ID.S1, 'probes'), ID.S1acc, probe({ prediction: 'no' }))).json()) as { id: string };
  const mine = await syntheticAttempt(ID.S1, ID.S1zxEvidence);
  const link = (attempt: string, account = ID.S1acc, probeId = id, student = ID.S1) =>
    post(url(student, `probes/${probeId}/attempt`), account, { attempt_id: attempt });
  assert.equal((await link(mine, ID.gS)).status, 403, '家长不能关联');
  assert.equal((await link(mine, ID.S2acc)).status, 403, '其他学生不能关联');
  const theirs = await syntheticAttempt(ID.S2, ID.S2zxEvidence);
  assert.equal((await link(theirs)).status, 422, '不能关联别人的作答');
  assert.equal((await link(randomUUID())).status, 422, '作答不存在');
  assert.equal((await link(mine, ID.S1acc, randomUUID())).status, 422, '探针不存在');
  assert.equal((await post(url(ID.S1, `probes/${id}/attempt`), ID.S1acc, { attempt_id: mine, correct: true })).status, 422, '多余键');
  assert.equal((await post(url(ID.S1, `probes/${id}/attempt`), ID.S1acc, { attempt_id: 'x' })).status, 422, '作答 ID 不是 UUID');
  const ok = await link(mine);
  assert.equal(ok.status, 200);
  assert.deepEqual(await ok.json(), { status: 'ok' });
  assert.equal((await link(mine)).status, 200, '同一作答重复关联是幂等的');
  const other = await syntheticAttempt(ID.S1, ID.S1zxEvidence);
  const linked = await link(other);
  assert.equal(linked.status, 409, '已关联别的作答');
  const problem = (await linked.json()) as Record<string, unknown>;
  assert.equal(problem['code'], 'PROBE_ALREADY_LINKED', '与幂等键冲突用不同的错误码');
  assert.equal(problem['title'], 'probe already linked to a different attempt');
  const { rows } = await su.query(`SELECT attempt_id FROM insight.confidence_probe WHERE id = $1`, [id]);
  assert.equal(rows[0].attempt_id, mine);
});

test('24 小时写入上限：事件满 2000 条、探针满 500 条后回 429 TOO_MANY，已有记录的重放仍回 200', { skip: !HAS_DB }, async () => {
  // 先各写一条正常记录（上限前），用来验证满额后重放不受上限影响
  const ev = start({ payload: { entry: 'other' } });
  assert.equal((await post(url(ID.S2, 'events'), ID.S2acc, ev)).status, 201);
  const pr = probe({ prediction: 'unsure' });
  assert.equal((await post(url(ID.S2, 'probes'), ID.S2acc, pr)).status, 201);
  // 超级用户直接插满合成记录（绕过写入函数；这些行测试结束时删掉，不影响后面的用例）
  const evCount = await count(`SELECT count(*) AS n FROM insight.behavior_event WHERE student_id = $1 AND created_at > now() - interval '24 hours'`, [ID.S2]);
  const prCount = await count(`SELECT count(*) AS n FROM insight.confidence_probe WHERE student_id = $1 AND created_at > now() - interval '24 hours'`, [ID.S2]);
  const filler = await su.query<{ id: string }>(
    `INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
     SELECT $1, 'session_start', '{"entry":"other"}', now(), gen_random_uuid() FROM generate_series(1, $2::int) RETURNING id`,
    [ID.S2, Math.max(0, 2000 - evCount)]);
  const probeFiller = await su.query<{ id: string }>(
    `INSERT INTO insight.confidence_probe (student_id, subject, item_id, prediction, asked_at, idempotency_key)
     SELECT $1, 'zhixing', $2, 'unsure', now(), gen_random_uuid() FROM generate_series(1, $3::int) RETURNING id`,
    [ID.S2, ITEM, Math.max(0, 500 - prCount)]);
  try {
    const eventsBefore = await count(`SELECT count(*) AS n FROM insight.behavior_event WHERE student_id = $1`, [ID.S2]);
    for (const [name, sub, body] of [['事件', 'events', start()], ['探针', 'probes', probe()]] as const) {
      const r = await post(url(ID.S2, sub), ID.S2acc, body);
      assert.equal(r.status, 429, name);
      assert.equal(r.headers.get('content-type'), 'application/problem+json');
      const problem = (await r.json()) as Record<string, unknown>;
      assert.deepEqual([problem['code'], problem['status']], ['TOO_MANY', 429], name);
    }
    assert.equal(await count(`SELECT count(*) AS n FROM insight.behavior_event WHERE student_id = $1`, [ID.S2]), eventsBefore, '超限的请求没有写入');
    const replay = await post(url(ID.S2, 'events'), ID.S2acc, ev);
    assert.equal(replay.status, 200, '满额后已有记录的重放仍回原 ID');
    assert.equal(replay.headers.get('idempotent-replayed'), 'true');
    const probeReplay = await post(url(ID.S2, 'probes'), ID.S2acc, pr);
    assert.equal(probeReplay.status, 200);
    assert.equal(probeReplay.headers.get('idempotent-replayed'), 'true');
    // 别的学生不受影响
    assert.equal((await post(url(ID.S1, 'events'), ID.S1acc, start())).status, 201);
  } finally {
    await su.query(`DELETE FROM insight.behavior_event WHERE id = ANY($1::uuid[])`, [filler.rows.map((r) => r.id)]);
    await su.query(`DELETE FROM insight.confidence_probe WHERE id = ANY($1::uuid[])`, [probeFiller.rows.map((r) => r.id)]);
  }
});

test('请求体：不是 JSON 对象 422，超过 16 KB 413，不是 JSON 415；方法不对 405', { skip: !HAS_DB }, async () => {
  assert.equal((await post(url(ID.S1, 'events'), ID.S1acc, '{"kind":')).status, 422);
  assert.equal((await post(url(ID.S1, 'events'), ID.S1acc, '[1,2]')).status, 422);
  assert.equal((await post(url(ID.S1, 'events'), ID.S1acc, start({ payload: { entry: 'x'.repeat(20000) } }))).status, 413);
  assert.equal((await post(url(ID.S1, 'events'), ID.S1acc, start(), 'text/plain')).status, 415);
  assert.equal((await fetch(url(ID.S1, 'events'), { headers: { 'x-dev-account-id': ID.S1acc } })).status, 405, '只写不读');
  assert.equal((await fetch(url(ID.S1, 'probes'), { headers: { 'x-dev-account-id': ID.S1acc } })).status, 405, '只写不读');
  assert.equal((await post(url(ID.S1, `events/${randomUUID()}/attempt`), ID.S1acc, {})).status, 404);
});

test('访问日志不含 payload、预测与学生 ID 原文', { skip: !HAS_DB }, () => {
  const text = JSON.stringify(logs);
  for (const s of [ID.S1, ID.S2, 'today', 'star_map', 'unsure', 'tired', '今天好累', ITEM]) assert.ok(!text.includes(s), s);
  for (const route of ['events', 'probes', 'probes/attempt']) assert.ok(logs.some((l) => l['route'] === route), route);
});
