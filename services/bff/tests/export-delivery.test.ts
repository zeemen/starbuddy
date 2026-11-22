// 导出下载“响应写完才作废”（specs/export-download-voucher，PR 28 增量审查 N8）的 HTTP 层语义：不连库（连接池是按语句应答的桩），
// 走与生产同一个 createShellServer 与下载路由，核对 Ctx.onDelivered 的口径：
//   - 写完（res finish）→ 作废；客户端在 finish 之前断开 → 释放，同一凭据可再兑换；
//   - handler 之后的事务提交失败（回 500）→ 释放；
//   - 每个请求恰好结算一次（export_download_settled 一行）。
// 包用 32 MB 的合成内容，保证本机回环的内核缓冲吃不下、finish 不会抢在断开之前。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { request, type IncomingMessage } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { loadAuthConfig, randomKeys } from '../src/http/config.ts';
import type { ShellDeps } from '../src/http/routes.ts';
import { csrfFor, SESSION_TOKEN } from '../src/http/secrets.ts';
import { createShellServer } from '../src/http/server.ts';
import { CLAIM_HOLD_MS, MemoryExportStore } from '../src/trust/exports.ts';

const ORIGIN = 'http://shell.test';
const GUARDIAN = randomUUID();
const KID = randomUUID();
const TOKEN = 'a'.repeat(64);
const BIG = 32 * 1024 * 1024;

const keys = randomKeys();
const store = new MemoryExportStore(20, 64 * 1024 * 1024, 256 * 1024 * 1024);
const logs: Record<string, unknown>[] = [];
const db = { failCommit: false, commits: 0 };
let skew = 0;  // 注入时钟的偏移：拨过占用超时

const client = {
  query: async (sql: string) => {
    if (sql === 'COMMIT') {
      db.commits += 1;
      if (db.failCommit) throw Object.assign(new Error('synthetic commit failure'), { code: '08006' });
    }
    if (sql.includes('is_linked_guardian')) return { rows: [{ linked: true }] };
    if (sql.includes('AS blocked')) return { rows: [{ blocked: false }] };
    return { rows: [] };
  },
  release: () => undefined,
};
const pool = {
  connect: async () => client,
  query: async (sql: string) => {
    if (sql.includes('auth_session_resolve')) return { rows: [{ account_id: GUARDIAN, account_kind: 'guardian', origin: 'otp' }] };
    if (sql.includes('auth_rate_hit')) return { rows: [{ ok: true }] };
    return { rows: [] };
  },
};

const server = createShellServer({ pool, config: loadAuthConfig(), keys, env: 'test', shellOrigin: ORIGIN, log: (l: Record<string, unknown>) => logs.push(l),
                                   now: () => new Date(Date.now() + skew),
                                   trust: { features: new Set(['data_rights']), exports: store } } as unknown as ShellDeps, { readyCacheMs: 0 });
let port = 0;
before(async () => {
  assert.ok(SESSION_TOKEN.test(TOKEN), '合成会话令牌格式');
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  port = (server.address() as AddressInfo).port;
});
after(() => new Promise<void>((r) => server.close(() => r())));

/** 暂存一份合成包，返回下载凭据与任务 */
function stage(size: number): { ref: string; job: string; body: string } {
  const ref = `dl_${randomUUID().replace(/-/g, '')}${randomUUID().replace(/-/g, '')}`;
  const job = randomUUID();
  const body = JSON.stringify({ format: 'starbuddy-export@1', pad: 'x'.repeat(size) });
  assert.ok(store.put(ref, { jobId: job, studentId: KID, accountId: GUARDIAN, expiresAt: Date.now() + 600_000, body }, Date.now()));
  return { ref, job, body };
}

type Mode = 'read' | 'drop';
/** 发下载请求：read 读完整个响应体；drop 收到响应头就断开（不读响应体） */
function download(job: string, ref: string, mode: Mode): Promise<{ status: number; text: string | null }> {
  const payload = JSON.stringify({ download_ref: ref });
  return new Promise((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port, method: 'POST', path: `/bff/v0/students/${KID}/exports/${job}/download`, agent: false,
                          headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(payload), origin: ORIGIN,
                                     'x-sb-session': TOKEN, 'x-csrf-token': csrfFor(keys, TOKEN) } }, (res: IncomingMessage) => {
      if (mode === 'drop' && res.statusCode === 200) {
        res.destroy();
        req.destroy();
        resolve({ status: 200, text: null });
        return;
      }
      const chunks: Buffer[] = [];
      res.on('data', (c: Buffer) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode ?? 0, text: Buffer.concat(chunks).toString('utf8') }));
      res.on('error', reject);
    });
    req.on('error', (e) => (mode === 'drop' ? undefined : reject(e)));
    req.end(payload);
  });
}

const settled = (job: string) => logs.filter((l) => l['event'] === 'export_download_settled' && l['export_job'] === job);
async function waitSettled(job: string, n: number): Promise<Record<string, unknown>[]> {
  for (let i = 0; i < 200 && settled(job).length < n; i += 1) await new Promise((r) => setTimeout(r, 25));
  const got = settled(job);
  assert.equal(got.length, n, `结算行 ${got.length} 条，应为 ${n}`);
  return got;
}

test('写完才作废：读完整个响应 → delivered，同一凭据再兑换 404', { timeout: 60_000 }, async () => {
  const x = stage(1024);
  const d = await download(x.job, x.ref, 'read');
  assert.equal(d.status, 200);
  assert.equal(d.text, x.body);
  const [line] = await waitSettled(x.job, 1);
  assert.equal(line!['outcome'], 'delivered');
  assert.equal(line!['attempts'], '1');
  assert.equal((await download(x.job, x.ref, 'read')).status, 404);
  skew = CLAIM_HOLD_MS + 1;  // 作废而不是一直占着：拨过占用超时仍 404
  try {
    assert.equal((await download(x.job, x.ref, 'read')).status, 404);
  } finally {
    skew = 0;
  }
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(settled(x.job).length, 1, '拿不到占用的请求不结算');
});

test('收到响应头就断开（finish 之前 close）→ released，同一凭据可再兑换、内容完整；之后 404', { timeout: 60_000 }, async () => {
  const x = stage(BIG);
  assert.equal((await download(x.job, x.ref, 'drop')).status, 200);
  const [first] = await waitSettled(x.job, 1);
  assert.equal(first!['outcome'], 'released');
  const d = await download(x.job, x.ref, 'read');
  assert.equal(d.status, 200);
  assert.equal(d.text!.length, x.body.length);
  assert.equal(d.text, x.body);
  const lines = await waitSettled(x.job, 2);
  assert.deepEqual(lines.map((l) => [l['outcome'], l['attempts']]), [['released', '1'], ['delivered', '2']]);
  assert.equal((await download(x.job, x.ref, 'read')).status, 404);
});

test('handler 之后事务提交失败（回 500）→ released；提交恢复后同一凭据可再兑换', { timeout: 60_000 }, async () => {
  const x = stage(1024);
  db.failCommit = true;
  try {
    assert.equal((await download(x.job, x.ref, 'read')).status, 500);
  } finally {
    db.failCommit = false;
  }
  const [first] = await waitSettled(x.job, 1);
  assert.equal(first!['outcome'], 'released');
  const d = await download(x.job, x.ref, 'read');
  assert.equal(d.status, 200);
  assert.equal(d.text, x.body);
  assert.deepEqual((await waitSettled(x.job, 2)).map((l) => l['outcome']), ['released', 'delivered']);
});

test('结算行只有元数据：键在白名单里，账号是 16 位摘要，不含凭据、凭据摘要与包内容', { timeout: 60_000 }, async () => {
  const x = stage(64);
  assert.equal((await download(x.job, x.ref, 'read')).status, 200);
  const [line] = await waitSettled(x.job, 1);
  assert.deepEqual(Object.keys(line!).sort(), ['account', 'attempts', 'event', 'export_job', 'outcome']);
  assert.match(String(line!['account']), /^[0-9a-f]{16}$/);
  assert.notEqual(line!['account'], GUARDIAN);
  const text = JSON.stringify(logs);
  assert.ok(!text.includes(x.ref) && !text.includes(x.ref.slice(3)), '日志不含凭据');
  assert.ok(!text.includes('starbuddy-export@1'), '日志不含包内容');
});
