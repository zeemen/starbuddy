// 打标报告（tagging-report.json）的读取与核对。格式以打标器的类型为准：tools/kge-import/src/items/report-types.ts
// （format = "kge-tagging-report/1"）。这里只读报表要用的字段，多出的字段忽略；缺字段或类型不对就报错（按不可信输入核对）。
// 也接受任务约定的简化格式（没有 format 字段）：{ map_version: "<版本>", items: [...], candidates_by_layer: {...} }，单测夹具用它。
// 口径：
//   - status = out_of_scope（题目所在章节还没有导入地图）或 in_scope = false 的题不打标，只计数，不进任何统计；
//   - 各层候选：优先 counts.candidates_all_by_layer（已导入主题里三层去重后的全部候选），其次 candidates_by_layer，
//     都没有时按各题 candidates 数组现算；写进 kge.tag_candidate 的行数（counts.candidate_rows_by_layer）另列；
//   - 报告没有 cognitive_level 字段时，按导入器写 kge.item_tag 的同一规则推：主考点节点只声明了一个认知层次就用它，否则“未标”
//     （tools/kge-import/src/items/build.ts；建议打标报告直接带上 cognitive_level，见 lessons 待人确认）。
import { readFileSync } from "node:fs";
import { COGNITIVE_LEVELS, type CognitiveLevel } from "./map.ts";

export const LAYERS = ["rule", "vector", "model", "human"] as const; // human：教研接受候选后写回（迁移 0024 的 tag_source）
export type Layer = (typeof LAYERS)[number];
export const REPORT_FORMAT = "kge-tagging-report/1";

export interface TaggedItem {
  item_id: string;
  status: string | null; // item_tag / candidate / untaggable；简化格式为 null
  main_node_id: string | null;
  node_ids: string[];
  confidence: number | null;
  layer: Layer | null;
  grade: "A" | "B" | null;
  cognitive_level: CognitiveLevel | null;
  cognitive_level_given: boolean; // 报告里有没有这个字段（正式格式 kge-tagging-report/1 目前没有）
  over_cap: boolean; // 合并前节点数超过上限（node_ids 已被截断）
}

export interface TaggingReport {
  map_version: string; // 版本号（或地图版本 ID，简化格式里两者都可）
  map_version_id: string | null;
  items: TaggedItem[]; // 只含已导入主题里的题，按 item_id 排序由计算方负责
  out_of_scope: number;
  candidates_by_layer: Record<string, number>;
  candidate_rows_by_layer: Record<string, number> | null;
}

export class TaggingError extends Error {}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isCount = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0;

function nullableEnum<T extends string>(v: unknown, allowed: readonly T[], what: string, id: string): T | null {
  if (v === null || v === undefined) return null;
  if (typeof v !== "string" || !(allowed as readonly string[]).includes(v)) throw new TaggingError(`题目 ${id} 的 ${what} 不合法：${String(v)}`);
  return v as T;
}

function layerCounts(v: unknown, what: string): Record<string, number> {
  if (!isObj(v)) throw new TaggingError(`${what} 应为对象`);
  const out: Record<string, number> = {};
  for (const [layer, n] of Object.entries(v)) {
    if (Array.isArray(n)) out[layer] = n.length;
    else if (isCount(n)) out[layer] = n;
    else if (isObj(n) && isCount(n.count)) out[layer] = n.count;
    else throw new TaggingError(`${what}.${layer} 应为条数、候选数组或 {count}`);
  }
  return out;
}

export function checkTaggingReport(raw: unknown): TaggingReport {
  if (!isObj(raw)) throw new TaggingError("打标报告应为 JSON 对象");
  if (raw.format !== undefined && raw.format !== REPORT_FORMAT) throw new TaggingError(`不认识的打标报告格式：${String(raw.format)}（只认 ${REPORT_FORMAT}）`);
  let mapVersion: string, mapVersionId: string | null = null;
  if (typeof raw.map_version === "string" && raw.map_version !== "") {
    mapVersion = raw.map_version;
  } else if (isObj(raw.map_version) && typeof raw.map_version.version === "string" && typeof raw.map_version.id === "string") {
    mapVersion = raw.map_version.version;
    mapVersionId = raw.map_version.id;
  } else {
    throw new TaggingError("打标报告缺 map_version");
  }
  if (!Array.isArray(raw.items)) throw new TaggingError("打标报告缺 items 数组");
  const seen = new Set<string>();
  const items: TaggedItem[] = [];
  let outOfScope = 0;
  const computed: Record<string, number> = {};
  for (const [k, it] of raw.items.entries()) {
    if (!isObj(it) || typeof it.item_id !== "string" || it.item_id === "") throw new TaggingError(`items[${String(k)}] 缺 item_id`);
    const id = it.item_id;
    if (seen.has(id)) throw new TaggingError(`题目 ${id} 重复`);
    seen.add(id);
    const status = it.status === undefined || it.status === null ? null : String(it.status);
    if (status !== null && !["item_tag", "candidate", "untaggable", "out_of_scope"].includes(status)) {
      throw new TaggingError(`题目 ${id} 的 status 不合法：${status}`);
    }
    if (status === "out_of_scope" || it.in_scope === false) {
      outOfScope++;
      continue;
    }
    if (it.main_node_id !== null && it.main_node_id !== undefined && typeof it.main_node_id !== "string") {
      throw new TaggingError(`题目 ${id} 的 main_node_id 应为字符串或 null`);
    }
    const nodeIds = it.node_ids ?? [];
    if (!Array.isArray(nodeIds) || !nodeIds.every((x) => typeof x === "string")) throw new TaggingError(`题目 ${id} 的 node_ids 应为字符串数组`);
    const c = it.confidence ?? null;
    if (c !== null && (typeof c !== "number" || !(c >= 0 && c <= 1))) throw new TaggingError(`题目 ${id} 的 confidence 应在 0–1`);
    if (it.candidates !== undefined) {
      if (!Array.isArray(it.candidates)) throw new TaggingError(`题目 ${id} 的 candidates 应为数组`);
      for (const cand of it.candidates) {
        if (!isObj(cand)) throw new TaggingError(`题目 ${id} 的候选格式不对`);
        const layer = nullableEnum(cand.layer, LAYERS, "候选 layer", id);
        if (layer) computed[layer] = (computed[layer] ?? 0) + 1;
      }
    }
    items.push({
      item_id: id,
      status,
      main_node_id: (it.main_node_id as string | null | undefined) ?? null,
      node_ids: nodeIds as string[],
      confidence: c,
      layer: nullableEnum(it.layer, LAYERS, "layer", id),
      grade: nullableEnum(it.grade, ["A", "B"] as const, "grade", id),
      cognitive_level: nullableEnum(it.cognitive_level, COGNITIVE_LEVELS, "cognitive_level", id),
      cognitive_level_given: "cognitive_level" in it,
      over_cap: it.over_cap === true,
    });
  }
  const counts = isObj(raw.counts) ? raw.counts : {};
  const byLayer = counts.candidates_all_by_layer !== undefined ? layerCounts(counts.candidates_all_by_layer, "counts.candidates_all_by_layer")
    : raw.candidates_by_layer !== undefined ? layerCounts(raw.candidates_by_layer, "candidates_by_layer")
    : computed;
  const rows = counts.candidate_rows_by_layer !== undefined ? layerCounts(counts.candidate_rows_by_layer, "counts.candidate_rows_by_layer") : null;
  return { map_version: mapVersion, map_version_id: mapVersionId, items, out_of_scope: outOfScope, candidates_by_layer: byLayer, candidate_rows_by_layer: rows };
}

export function loadTaggingReport(path: string): TaggingReport {
  return checkTaggingReport(JSON.parse(readFileSync(path, "utf8")));
}
