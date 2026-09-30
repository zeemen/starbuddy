// 错因跨科映射（docs/mapping/error-taxonomy.v0.9.yaml，唯一数据源；写法同 adapters/zhixing/src/taxonomy.ts）。
// 文砺：平台层六类与阅读模块六个大类（下辖错因标签，标签继承所属大类的映射）→ 跨科七大类。映射表待教研签字。
// 只在 report 里用于核对平台给的错因记录：学科编码必须是语文的（CHN 口径，与道法 MORAL 口径的同名问题区分，KGE 第八章），
// 跨科大类必须与映射表一致。敏感类不计入（完整方案第四章红线）。
import { readFileSync } from "node:fs";
import { parse } from "yaml";

export interface WenliTaxonomy {
  version: string;
  /** 学科编码（含阅读错因标签）→ 跨科大类 */
  crossSubjectOf: Map<string, string>;
  /** 学科编码的顺序（映射表里的顺序；标签跟在所属大类后面） */
  order: string[];
}

export const DEFAULT_TAXONOMY_PATH = new URL("../../../docs/mapping/error-taxonomy.v0.9.yaml", import.meta.url);

interface RawTaxonomy {
  version: string;
  categories: { id: string }[];
  subjects: Record<string, { groups: { codes: { code: string; maps_to: string; sensitive?: boolean; tags?: string[] }[] }[] }>;
}

export function loadTaxonomy(path: string | URL = DEFAULT_TAXONOMY_PATH): WenliTaxonomy {
  const raw = parse(readFileSync(path, "utf8")) as RawTaxonomy;
  const categories = new Set(raw.categories.map((c) => c.id));
  const crossSubjectOf = new Map<string, string>();
  const order: string[] = [];
  for (const g of raw.subjects.wenli?.groups ?? []) {
    for (const c of g.codes) {
      if (!categories.has(c.maps_to)) throw new Error(`映射表里 wenli.${c.code} 的大类 ${c.maps_to} 未定义`);
      if (c.sensitive === true) continue;
      for (const code of [c.code, ...(c.tags ?? [])]) {
        if (crossSubjectOf.has(code)) throw new Error(`映射表里 wenli.${code} 重复`);
        crossSubjectOf.set(code, c.maps_to);
        order.push(code);
      }
    }
  }
  if (crossSubjectOf.size === 0) throw new Error("映射表缺少文砺错因");
  return { version: raw.version, crossSubjectOf, order };
}
