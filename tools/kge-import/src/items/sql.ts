// 生成 SQL 的转义工具：只用单引号加倍（文件里 SET LOCAL standard_conforming_strings = on，反斜杠按字面）。
// 控制字符一律拒绝（不写进 SQL，也就不会拆开语句）；这是生成器的输入校验，不是防注入的边界——种子由人审阅后执行。

export class SqlError extends Error {}

const CONTROL = /[\u0000-\u001f\u007f]/;

export function q(s: string): string {
  if (CONTROL.test(s)) throw new SqlError("写进 SQL 的文本含控制字符");
  return `'${s.replace(/'/g, "''")}'`;
}
export function qn(s: string | null): string {
  return s === null ? "NULL" : q(s);
}
export function num3(x: number | null): string {
  if (x === null) return "NULL";
  if (!Number.isFinite(x) || x < 0 || x > 1) throw new SqlError(`置信度 ${x} 不在 0–1`);
  return x.toFixed(3);
}
export function nodeArray(xs: string[]): string {
  return xs.length === 0 ? "'{}'::kge.node_id[]" : `ARRAY[${xs.map(q).join(", ")}]::kge.node_id[]`;
}

/** 列名与迁移 0011、0024 一致（测试核对） */
export const ITEM_FAMILY_COLUMNS = ["id", "map_version_id", "main_node_id", "code"] as const;
export const ITEM_TAG_COLUMNS = [
  "map_version_id", "subject", "item_id", "main_node_id", "node_ids", "cognitive_level", "family_id", "source_kind", "license_state",
  "tag_review_status", "tag_confidence", "tag_source", "tagger_version", "tag_model_version",
] as const;
export const TAG_CANDIDATE_COLUMNS = [
  "map_version_id", "subject", "item_id", "node_id", "role", "layer", "confidence", "tagger_version", "model_version", "reason_code", "grade",
] as const;

export function insert(table: string, columns: readonly string[], rows: string[][]): string[] {
  if (rows.length === 0) return [`-- ${table}：无`];
  for (const r of rows) if (r.length !== columns.length) throw new SqlError(`${table} 的一行有 ${r.length} 个值，应为 ${columns.length} 个`);
  return [`INSERT INTO ${table} (${columns.join(", ")}) VALUES`, rows.map((r) => `  (${r.join(", ")})`).join(",\n") + ";"];
}
