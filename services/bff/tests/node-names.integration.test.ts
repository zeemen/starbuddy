// 知识点名在真实库上的过滤（specs/practice-retest-list-polish）：已发布地图里未停用的节点才有名；草稿地图、已撤下地图、
// 已停用节点都没有。整段在一个事务里做、最后回滚，不留数据（一个学科同时只能有一份已发布地图，先把现有的临时撤下）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';
import { publishedNodeNames } from '../src/node-names.ts';
import { HAS_DB, setupHarness, type Harness } from './harness.ts';

let h: Harness | undefined;
before(async () => { if (HAS_DB) h = await setupHarness(); });
after(async () => { await h?.close(); });

test('已发布地图里未停用的节点有名；草稿、已撤下地图与已停用节点没有', { skip: !HAS_DB }, async () => {
  const c = await h!.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(`UPDATE kge.map_version SET status = 'retired' WHERE subject = 'aya' AND status = 'published'`);
    const [pub, draft, old] = [randomUUID(), randomUUID(), randomUUID()];
    await c.query(`INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES
                     ($1, 'aya', 2022, '9.9.1', 'published', now()), ($2, 'aya', 2022, '9.9.2', 'draft', NULL),
                     ($3, 'aya', 2022, '9.9.0', 'retired', now())`, [pub, draft, old]);
    for (const [map, suffix] of [[pub, 'P'], [draft, 'D'], [old, 'R']] as const) {
      await c.query(`INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, status) VALUES
                       ($1, 'MATH.NN${suffix}', 'domain', NULL, NULL, '（合成）领域', 'active'),
                       ($1, 'MATH.NN${suffix}.T1', 'topic', 'MATH.NN${suffix}', NULL, '（合成）主题', 'active'),
                       ($1, 'MATH.NN${suffix}.T1.A', 'node', 'MATH.NN${suffix}.T1', 'kp', '（合成）${suffix} 节点 A', 'active'),
                       ($1, 'MATH.NN${suffix}.T1.B', 'node', 'MATH.NN${suffix}.T1', 'kp', '（合成）${suffix} 节点 B', 'deprecated')`, [map]);
    }
    const names = await publishedNodeNames((sql, p) => c.query(sql, p),
      ['MATH.NNP.T1.A', 'MATH.NNP.T1.B', 'MATH.NND.T1.A', 'MATH.NNR.T1.A', 'MATH.NONE.T1.A', 'MATH.NNP.T1.A']);
    assert.deepEqual([...names.entries()], [['MATH.NNP.T1.A', '（合成）P 节点 A']]);
    assert.equal((await publishedNodeNames((sql, p) => c.query(sql, p), [])).size, 0);
  } finally {
    await c.query('ROLLBACK').catch(() => {});
    c.release();
  }
});
