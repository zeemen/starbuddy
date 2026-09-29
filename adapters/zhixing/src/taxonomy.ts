// 错因跨科映射（docs/mapping/error-taxonomy.v0.9.yaml，唯一数据源；写法同 adapters/aya/src/taxonomy.ts）。
// 知行四类（审题、定位、转化、规范）→ 跨科七大类。映射表待教研签字（#8/A3），签字前按草稿映射，签字后只需更新表。
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { ATTRIBUTION_CODES, type AttributionCode } from "./content.ts";

export interface ZhixingTaxonomy {
  version: string;
  crossSubjectOf: Map<AttributionCode, string>;
}

export const DEFAULT_TAXONOMY_PATH = new URL("../../../docs/mapping/error-taxonomy.v0.9.yaml", import.meta.url);

interface RawTaxonomy {
  version: string;
  categories: { id: string }[];
  subjects: Record<string, { groups: { codes: { code: string; maps_to: string; sensitive?: boolean }[] }[] }>;
}

export function loadTaxonomy(path: string | URL = DEFAULT_TAXONOMY_PATH): ZhixingTaxonomy {
  const raw = parse(readFileSync(path, "utf8")) as RawTaxonomy;
  const categories = new Set(raw.categories.map((c) => c.id));
  const crossSubjectOf = new Map<AttributionCode, string>();
  for (const g of raw.subjects.zhixing?.groups ?? []) {
    for (const c of g.codes) {
      if (!(ATTRIBUTION_CODES as readonly string[]).includes(c.code)) continue;
      if (!categories.has(c.maps_to)) throw new Error(`映射表里 zhixing.${c.code} 的大类 ${c.maps_to} 未定义`);
      // 敏感类不自动归因（完整方案第四章红线）；知行四类目前都不是敏感类
      if (c.sensitive === true) continue;
      crossSubjectOf.set(c.code as AttributionCode, c.maps_to);
    }
  }
  const missing = ATTRIBUTION_CODES.filter((a) => !crossSubjectOf.has(a));
  if (missing.length) throw new Error(`映射表缺少知行错因：${missing.join("、")}`);
  return { version: raw.version, crossSubjectOf };
}
