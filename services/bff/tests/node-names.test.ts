// 再练、复测列表的知识点名（specs/practice-retest-list-polish）：与今日计划同一口径（已发布地图、未停用节点），
// 一次列表按去重后的节点 ID 只查一次；名称查不到为 null；查询出错时列表照常返回、名称全为 null。
// 这里用假的连接池（只认 is_self 与取名两句 SQL），真实库上的过滤见 tests/node-names.integration.test.ts。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { test } from 'node:test';
import type { Pool } from 'pg';
import { PUBLISHED_NODE_NAMES_SQL } from '../src/node-names.ts';
import { practiceQueue, type PracticeDeps } from '../src/practice/service.ts';
import { retestQueue, type RetestDeps } from '../src/retest/service.ts';

const ACCOUNT = randomUUID();
const STUDENT = randomUUID();

function fakePool(names: Record<string, string> | 'throw', seen: unknown[][]): Pool {
  const client = {
    async query(sql: string, params?: unknown[]) {
      if (sql.includes('platform.is_self_student')) return { rows: [{ ok: true }] };
      if (sql === PUBLISHED_NODE_NAMES_SQL) {
        seen.push(params ?? []);
        if (names === 'throw') throw new Error('synthetic failure');
        const ids = (params?.[0] ?? []) as string[];
        return { rows: ids.filter((id) => names[id] !== undefined).map((id) => ({ id, name: names[id] })) };
      }
      return { rows: [] };
    },
    release() {},
  };
  return { connect: async () => client } as unknown as Pool;
}

const practiceEntry = (node: string) => ({ entry_id: randomUUID(), node_id: node, cognitive_level: 'apply', source: 'wrong', status: 'due',
  round: 1, max_rounds: 3, next_round_at: null, empty_reason: null });
const retestEntry = (node: string) => ({ entry_id: randomUUID(), node_id: node, cognitive_level: 'apply', kind: 'retest', status: 'due' });

function practiceDeps(pool: Pool, nodes: string[], notes: string[]): PracticeDeps {
  return {
    pool, key: Buffer.alloc(32, 1), now: () => Date.now(), note: (e) => notes.push(e),
    learning: { queue: async () => ({ kind: 'ok', status: 200, replayed: false, body: { entries: nodes.map(practiceEntry) } }) } as unknown as PracticeDeps['learning'],
  };
}
function retestDeps(pool: Pool, nodes: string[], notes: string[]): RetestDeps {
  return {
    pool, key: Buffer.alloc(32, 1), now: () => Date.now(), note: (e) => notes.push(e),
    learning: { queue: async () => ({ kind: 'ok', status: 200, replayed: false, body: { entries: nodes.map(retestEntry) } }) } as unknown as RetestDeps['learning'],
  };
}

const NAMES = { 'MATH.SYN.NN.A': '（合成）一次函数的图像' };
const NODES = ['MATH.SYN.NN.A', 'MATH.SYN.NN.B', 'MATH.SYN.NN.A'];

for (const [label, run] of [
  ['再练', (pool: Pool, notes: string[]) => practiceQueue(practiceDeps(pool, NODES, notes), ACCOUNT, STUDENT, 'math')],
  ['复测', (pool: Pool, notes: string[]) => retestQueue(retestDeps(pool, NODES, notes), ACCOUNT, STUDENT, 'math')],
] as const) {
  test(`${label}列表：有名称的条目带 node_name，没有的为 null；节点 ID 去重后只查一次`, async () => {
    const seen: unknown[][] = [];
    const notes: string[] = [];
    const r = await run(fakePool(NAMES, seen), notes);
    assert.equal(r.status, 200);
    const entries = (JSON.parse(String(r.body)) as { entries: { node_id: string; node_name: string | null }[] }).entries;
    assert.deepEqual(entries.map((e) => [e.node_id, e.node_name]),
      [['MATH.SYN.NN.A', '（合成）一次函数的图像'], ['MATH.SYN.NN.B', null], ['MATH.SYN.NN.A', '（合成）一次函数的图像']]);
    assert.deepEqual(seen, [[['MATH.SYN.NN.A', 'MATH.SYN.NN.B']]]);
    assert.deepEqual(notes.filter((n) => n.startsWith('node_names')), []);
  });

  test(`${label}列表：取名称出错时列表照常返回，名称全为 null，只记事件名`, async () => {
    const notes: string[] = [];
    const r = await run(fakePool('throw', []), notes);
    assert.equal(r.status, 200);
    const entries = (JSON.parse(String(r.body)) as { entries: { node_name: string | null }[] }).entries;
    assert.deepEqual(entries.map((e) => e.node_name), [null, null, null]);
    assert.deepEqual(notes.filter((n) => n.startsWith('node_names')), ['node_names_unavailable']);
  });
}

test('取名称的 SQL：只取已发布地图里未停用的节点（与今日计划同一句）', () => {
  assert.match(PUBLISHED_NODE_NAMES_SQL, /v\.status = 'published'/);
  assert.match(PUBLISHED_NODE_NAMES_SQL, /n\.status = 'active'/);
  assert.match(PUBLISHED_NODE_NAMES_SQL, /n\.id = ANY \(\$1::text\[\]\)/);
});
