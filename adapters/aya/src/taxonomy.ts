// 错因跨科映射（docs/mapping/error-taxonomy.v0.9.yaml，唯一数据源）。
// 阿鸦的十二大根因按中文名称对应到学科编码与跨科大类。映射表待教研签字；签字前按草稿映射，签字后只需更新表。
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import type { RootCause } from "./core/types.ts";
import { ROOT_CAUSES } from "./core/types.ts";

export interface TaxonomyEntry {
  code: string;
  crossSubject: string;
  sensitive: boolean;
}

export interface AyaTaxonomy {
  version: string;
  byRootCause: Map<RootCause, TaxonomyEntry>;
}

export const DEFAULT_TAXONOMY_PATH = new URL("../../../docs/mapping/error-taxonomy.v0.9.yaml", import.meta.url);

interface RawTaxonomy {
  version: string;
  categories: { id: string }[];
  subjects: Record<string, { groups: { codes: { code: string; label: string; maps_to: string; sensitive?: boolean }[] }[] }>;
}

export function loadTaxonomy(path: string | URL = DEFAULT_TAXONOMY_PATH): AyaTaxonomy {
  const raw = parse(readFileSync(path, "utf8")) as RawTaxonomy;
  const categories = new Set(raw.categories.map((c) => c.id));
  const byRootCause = new Map<RootCause, TaxonomyEntry>();
  for (const g of raw.subjects.aya?.groups ?? []) {
    for (const c of g.codes) {
      if (!(ROOT_CAUSES as readonly string[]).includes(c.label)) continue;
      if (!categories.has(c.maps_to)) throw new Error(`映射表里 aya.${c.code} 的大类 ${c.maps_to} 未定义`);
      byRootCause.set(c.label as RootCause, { code: c.code, crossSubject: c.maps_to, sensitive: c.sensitive === true });
    }
  }
  const missing = ROOT_CAUSES.filter((r) => !byRootCause.has(r));
  if (missing.length) throw new Error(`映射表缺少阿鸦根因：${missing.join("、")}`);
  return { version: raw.version, byRootCause };
}
