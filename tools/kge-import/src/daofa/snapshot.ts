// 道法切片的“快照”：覆盖报表（tools/kge-coverage --subject zhixing）的输入。
// 两个来源产出同一格式、同一排序：
//   1. 离线：导入器由领域文件、内容包采分点、时政条目组成（本文件的 composeSnapshot）；
//   2. 库上：tools/kge-coverage/sql/daofa-snapshot.sql 在演练库里只读导出（tests/daofa-db.sh 核对两边逐项相同）。
// 只含 ID、结构与计数所需的字段：不含教材原句（本来就不入库）、时政摘要、采分点的关键词与给分规则。
import { cmp } from "./common.ts";

export const SNAPSHOT_FORMAT = "kge-daofa-snapshot/1";

export interface SnapshotNode {
  id: string;
  level: "domain" | "topic" | "node" | "detail";
  parent_id: string | null;
  kind: string | null;
  name: string;
  cognitive_levels: string[];
  status: "active" | "deprecated";
}
export interface SnapshotEdge { from_id: string; to_id: string; type: string }
export interface SnapshotProjection {
  id: string;
  node_id: string;
  edition: string;
  edition_year: number;
  volume: string;
  chapter: string;
  page_from: number;
  page_to: number;
  mapping_state: "unverified" | "verified" | "disputed";
}
export interface SnapshotScoringPoint { item_id: string; rubric_version: string; point_id: string; score: number; node_ids: string[] }
export interface SnapshotAffair { id: string; node_ids: string[]; valid_from: string; valid_until: string; review_status: string }
export interface DaofaSnapshot {
  format: typeof SNAPSHOT_FORMAT;
  subject: "zhixing";
  map_version_id: string;
  version: string;
  nodes: SnapshotNode[];
  edges: SnapshotEdge[];
  projections: SnapshotProjection[];
  scoring_points: SnapshotScoringPoint[];
  current_affairs: SnapshotAffair[];
}

/** 固定排序（与 daofa-snapshot.sql 的 ORDER BY 一致） */
export function sortSnapshot(s: DaofaSnapshot): DaofaSnapshot {
  return {
    ...s,
    nodes: [...s.nodes].sort((a, b) => cmp(a.id, b.id)),
    edges: [...s.edges].sort((a, b) => cmp(a.from_id, b.from_id) || cmp(a.to_id, b.to_id) || cmp(a.type, b.type)),
    projections: [...s.projections].sort((a, b) => cmp(a.id, b.id)),
    scoring_points: [...s.scoring_points].sort((a, b) => cmp(a.item_id, b.item_id) || cmp(a.rubric_version, b.rubric_version) || cmp(a.point_id, b.point_id)),
    current_affairs: [...s.current_affairs].sort((a, b) => cmp(a.id, b.id)),
  };
}

/** 稳定的 JSON 文本：对象键按码点排序、两空格缩进、结尾换行（同一输入逐字节相同） */
export function stableJson(v: unknown): string {
  const norm = (x: unknown): unknown => {
    if (Array.isArray(x)) return x.map(norm);
    if (x !== null && typeof x === "object") {
      return Object.fromEntries(Object.keys(x).sort(cmp).map((k) => [k, norm((x as Record<string, unknown>)[k])]));
    }
    return x;
  };
  return `${JSON.stringify(norm(v), null, 2)}\n`;
}
