// generate_variation：从同族候选题里**选**题，不生成（契约 v1；specs/adapter-aya-methods-sprint4/spec.md 设计要点第 2 节）。
// 算法 aya-variant-select@0.1.0，改写自 legacy lib/mistakes.ts pickVariant（同主节点、非原题），加准入过滤与同错因匹配。
//
// - 候选必须 admitted（契约 VariationCandidate.admission = admitted）。准入的唯一真相源是平台题目表的准入列，
//   内容包里是副本（由 4-G 的准入决定文件重建）；ItemTagView 不带准入，所以 item_family 里的题也在内容包里查准入。
// - 有 item_family（编排层调知识服务 item.similar 的结果）时只从其中选；没有时按内容包“同主节点或同错因”取（过渡实现）。
// - 721 道题目前全部未准入，所以真实内容包下一律返回空结果 no_admitted_candidate：这是契约要求的正确行为。
import type { LoadedPack, PackItem } from "../content.ts";
import { ProblemError } from "../problem.ts";
import type { AyaTaxonomy } from "../taxonomy.ts";
import { formatAllowed, isAyaRef, localId } from "./shared.ts";

export const VARIANT_ALGORITHM = "aya-variant-select@0.1.0";

export interface ItemTagView {
  item_ref: string;
  main_node_id: string;
  prior_difficulty?: number;
  license_state: "unverified" | "licensed" | "original" | "public" | "restricted";
}
export interface VariationInput {
  source_item_ref?: string;
  skill_node?: string;
  subject_error_category?: string;
  count: number;
  exclude_item_refs?: string[];
  item_family?: ItemTagView[];
  exam_config?: { question_type_codes?: string[] };
}
export type EmptyReason = "no_admitted_candidate" | "all_excluded" | "skill_node_unknown";
export interface VariationPick {
  item: PackItem;
  sameSkillNode: boolean;
  sameErrorCategory: boolean;
  difficulty?: number;
}
export type VariationOutcome = { picks: VariationPick[]; emptyReason?: EmptyReason };

/** 一道题的全部陷阱码 → 阿鸦错因编码（敏感类不计，与 grade 的错因归因一致） */
export function errorCategoriesOf(item: PackItem, content: LoadedPack, taxonomy: AyaTaxonomy): Set<string> {
  const traps = [...(item.options ?? []).flatMap((o) => o.traps ?? []), ...(item.wrongAnswerTraps ?? []).flatMap((w) => w.traps)];
  const out = new Set<string>();
  for (const t of traps) {
    const root = content.trapByCode.get(t);
    const entry = root && taxonomy.byRootCause.get(root);
    if (entry && !entry.sensitive) out.add(entry.code);
  }
  return out;
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

export function selectVariants(content: LoadedPack, taxonomy: AyaTaxonomy, input: VariationInput): VariationOutcome {
  const knownCodes = new Set([...taxonomy.byRootCause.values()].map((e) => e.code));
  if (input.subject_error_category !== undefined && !knownCodes.has(input.subject_error_category)) {
    throw new ProblemError(422, "INVALID_REQUEST", "subject_error_category is not an aya error code in the taxonomy");
  }
  // item_family 的前缀不在 schema 的学科约束里，由适配器检查（spec 设计要点第 0 节“前缀”）
  if ((input.item_family ?? []).some((f) => !isAyaRef(f.item_ref))) {
    throw new ProblemError(400, "SUBJECT_MISMATCH", "item_family contains a non-aya item");
  }

  let source: PackItem | undefined;
  if (input.source_item_ref !== undefined) {
    source = content.itemById.get(localId(input.source_item_ref));
    if (!source) throw new ProblemError(404, "ITEM_NOT_FOUND", "source_item_ref is not in the content pack");
  }
  const nodes = new Set<string>(source?.mainNodes ?? []);
  if (input.skill_node !== undefined) {
    const skill = localId(input.skill_node);
    if (!content.nodeById.has(skill)) return { picks: [], emptyReason: "skill_node_unknown" };
    nodes.add(skill);
  }
  const categories =
    input.subject_error_category !== undefined
      ? new Set([input.subject_error_category])
      : source
        ? errorCategoriesOf(source, content, taxonomy)
        : new Set<string>();

  // 候选池：item_family（只取内容包里有的，判分要用答案；版权受限的排除）或内容包
  const family = new Map<string, ItemTagView>();
  let pool: PackItem[];
  if (input.item_family !== undefined) {
    pool = [];
    for (const f of input.item_family) {
      if (f.license_state === "restricted") continue;
      const it = content.itemById.get(localId(f.item_ref));
      if (!it || family.has(it.id)) continue;
      family.set(it.id, f);
      pool.push(it);
    }
  } else {
    pool = content.pack.items;
  }

  // 原题本身不是候选（不计入 all_excluded）；exclude_item_refs 是平台给的“做过的题”
  const excluded = new Set((input.exclude_item_refs ?? []).map(localId));
  const codes = input.exam_config?.question_type_codes;
  const matched: VariationPick[] = [];
  for (const it of pool) {
    if (source && it.id === source.id) continue;
    const sameSkillNode = it.mainNodes.some((n) => nodes.has(n));
    const itCats = categories.size > 0 ? errorCategoriesOf(it, content, taxonomy) : new Set<string>();
    const sameErrorCategory = [...itCats].some((c) => categories.has(c));
    if (!sameSkillNode && !sameErrorCategory) continue;
    if (it.admission !== "admitted" || !formatAllowed(it, codes)) continue;
    const prior = family.get(it.id)?.prior_difficulty;
    matched.push({ item: it, sameSkillNode, sameErrorCategory, ...(prior !== undefined ? { difficulty: prior } : {}) });
  }
  const eligible = matched.filter((p) => !excluded.has(p.item.id));
  if (eligible.length === 0) return { picks: [], emptyReason: matched.length > 0 ? "all_excluded" : "no_admitted_candidate" };

  // 排序：同节点且同错因 → 与原题同用途 → 题号
  const rank = (p: VariationPick) => [p.sameSkillNode && p.sameErrorCategory ? 0 : 1, source && p.item.usage === source.usage ? 0 : 1] as const;
  eligible.sort((a, b) => {
    const [a1, a2] = rank(a);
    const [b1, b2] = rank(b);
    return a1 - b1 || a2 - b2 || cmp(a.item.id, b.item.id);
  });
  return { picks: eligible.slice(0, input.count) };
}
