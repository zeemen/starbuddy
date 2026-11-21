// 知识节点名（公共知识层，不是对学生的判断）：只取已发布地图里未停用的节点。今日计划、错题再练、复测三处同一口径
// （specs/practice-retest-list-polish）。名称只用于显示；查不到的节点不在结果里，调用方显示编号或中性标题。
import type { Pool } from 'pg';
import type { Query } from './http/routes.ts';
import { withAccount } from './metering/db.ts';

export const PUBLISHED_NODE_NAMES_SQL =
  `SELECT n.id, n.name FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id
    WHERE v.status = 'published' AND n.status = 'active' AND n.id = ANY ($1::text[])`;

/** 在给定连接上（调用方已在 withAccount 里）按节点 ID 批量取名 */
export async function publishedNodeNames(q: Query, ids: readonly string[]): Promise<Map<string, string>> {
  const unique = [...new Set(ids)];
  if (unique.length === 0) return new Map();
  const { rows } = await q<{ id: string; name: string }>(PUBLISHED_NODE_NAMES_SQL, [unique]);
  return new Map(rows.map((r) => [r.id, r.name]));
}

/**
 * 单独开一次 RLS 连接取名；任何失败都返回空表（名称拿不到不影响列表本身，界面退回显示编号）。
 * note 只收事件名（不带学生、节点 ID）。
 */
export async function nodeNamesOrEmpty(pool: Pool, account: string, ids: readonly string[],
  note?: (event: string) => void): Promise<Map<string, string>> {
  if (ids.length === 0) return new Map();
  try {
    return await withAccount(pool, account, (c) => publishedNodeNames((sql, p) => c.query(sql, p), ids));
  } catch {
    note?.('node_names_unavailable');
    return new Map();
  }
}
