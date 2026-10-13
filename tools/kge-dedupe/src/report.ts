// 报告（KGE-08 范围 1）：dedupe-report.json 只含题目 ID、指纹、距离与分组，不含题干；worklist.md 给教研勾选，同样只列 ID。
// 同一输入逐字节一致：所有列表按 ID / 指纹排序，不写时间、路径与运行环境。
import type { DedupeConfig } from "./config.ts";
import { cmp, exactGroups, familyCandidates, fingerprintItems, nearPairs, representatives, type ExactGroup, type FamilyCandidate,
  type NearPair, type ReviewTemplate } from "./dedupe.ts";
import { hex64 } from "./fingerprint.ts";
import type { Input } from "./input.ts";
import { buildPapers, looseMatcher, paperPairs, type PaperComparison } from "./paper.ts";

export const REPORT_FORMAT = "kge-dedupe-report/2";

export interface LegacyConflict { id: string; legacy_family: string; exact_group: string | null; near_with: string[]; family_candidate: string | null }

export interface Report {
  format: string;
  source: "fixture" | "aya";
  synthetic: boolean;
  /** 主节点 ID 的坐标系：都不是 KGE 节点 ID，落库须按 KGE 节点重算 */
  node_system: string;
  notice: string;
  config: DedupeConfig;
  counts: Record<string, number>;
  items: { id: string; main_nodes: string[]; paper_id: string | null; seq: number | null; sha256: string; template_sha256: string; simhash: string }[];
  exact_groups: ExactGroup[];
  near_pairs: NearPair[];
  family_candidates: FamilyCandidate[];
  review_templates: ReviewTemplate[];
  legacy_family_conflicts: LegacyConflict[];
  papers: { paper_id: string; items: string[] }[];
  paper_pairs: PaperComparison[];
}

function conflicts(input: Input, exact: readonly ExactGroup[], near: readonly NearPair[], families: readonly FamilyCandidate[]): LegacyConflict[] {
  const groupOf = new Map<string, string>(), familyOf = new Map<string, string>(), nearOf = new Map<string, string[]>();
  for (const g of exact) for (const id of g.items) groupOf.set(id, g.group);
  for (const f of families) for (const id of f.items) familyOf.set(id, f.code_suggestion);
  const addNear = (x: string, y: string) => {
    const l = nearOf.get(x);
    if (l) l.push(y);
    else nearOf.set(x, [y]);
  };
  for (const p of near) {
    addNear(p.a, p.b);
    addNear(p.b, p.a);
  }
  const out: LegacyConflict[] = [];
  for (const id of [...input.legacyFamilies.keys()].sort(cmp)) {
    const g = groupOf.get(id) ?? null, f = familyOf.get(id) ?? null, n = (nearOf.get(id) ?? []).sort(cmp);
    if (g !== null || f !== null || n.length > 0) out.push({ id, legacy_family: input.legacyFamilies.get(id)!, exact_group: g, near_with: n, family_candidate: f });
  }
  return out;
}

export function analyze(input: Input, cfg: DedupeConfig): Report {
  const fps = fingerprintItems(input.items, cfg);
  const reps = representatives(fps);
  const exact = exactGroups(reps);
  const near = nearPairs(reps, cfg);
  const { families, review } = familyCandidates(fps, input.subject);
  const papers = buildPapers(fps);
  const pairs = paperPairs(papers, looseMatcher(reps, near), cfg);
  const legacy = conflicts(input, exact, near, families);
  let dupItems = 0, familyItems = 0;
  for (const g of exact) dupItems += g.items.length - 1;
  for (const f of families) familyItems += f.items.length;
  return {
    format: REPORT_FORMAT,
    source: input.source,
    synthetic: input.synthetic,
    node_system: input.nodeSystem,
    notice: "只出候选：任何归并（规范题、题目族、同卷复用解析）都须教研确认后另起任务落库；主节点不是 KGE 节点 ID，落库须按 KGE 节点重算；本报告不含题干",
    config: cfg,
    counts: {
      items: fps.length,
      unique_texts: reps.list.length,
      exact_groups: exact.length,
      exact_duplicate_items: dupItems,
      near_pairs: near.length,
      family_candidates: families.length,
      family_items: familyItems,
      review_templates: review.length,
      legacy_families: new Set(input.legacyFamilies.values()).size,
      legacy_family_conflicts: legacy.length,
      papers: papers.length,
      paper_pairs_same: pairs.filter((p) => p.verdict === "same").length,
      paper_pairs_similar: pairs.filter((p) => p.verdict === "similar").length,
    },
    items: fps.map((f) => ({
      id: f.id, main_nodes: f.mainNodes, paper_id: f.paperId ?? null, seq: f.seq ?? null, sha256: f.sha, template_sha256: f.templateSha,
      simhash: hex64(f.simhash),
    })),
    exact_groups: exact,
    near_pairs: near,
    family_candidates: families,
    review_templates: review,
    legacy_family_conflicts: legacy,
    papers: papers.map((p) => ({ paper_id: p.paperId, items: p.items.map((f) => f.id) })),
    paper_pairs: pairs,
  };
}

/** 键按字典序排列的 JSON（两个空格缩进，末尾换行） */
export function stableJson(v: unknown): string {
  const sortKeys = (x: unknown): unknown => {
    if (Array.isArray(x)) return x.map(sortKeys);
    if (x !== null && typeof x === "object") {
      return Object.fromEntries(Object.keys(x).sort(cmp).map((k) => [k, sortKeys((x as Record<string, unknown>)[k])]));
    }
    return x;
  };
  return `${JSON.stringify(sortKeys(v), null, 2)}\n`;
}

const ids = (xs: readonly string[]): string => xs.map((x) => `\`${x}\``).join("、");
const nodes = (xs: readonly string[]): string => (xs.length === 0 ? "（无主节点）" : ids(xs));
const VERDICT: Record<string, string> = { same: "同卷", similar: "相似卷" };
const REASON: Record<string, string> = { cross_node: "跨主节点", multi_node: "含多主节点题", no_node: "含无主节点题" };

export function renderWorklist(r: Report): string {
  const L: string[] = [];
  L.push("# 题目去重与同卷识别 · 教研工作清单（KGE-08 第一步）", "");
  L.push(`来源：${r.source === "fixture" ? "合成样例" : "阿鸦 legacy 题库（只读）"}。只出候选，勾选后由另起的任务落库；本清单与报告都不含题干，请按题目 ID 回原题库查看。`);
  L.push("阈值见 dedupe-report.json 的 config（待教研确认）。", "");
  L.push("## 勾选前须知", "");
  L.push(`- 节点坐标系：${r.node_system}。下面的主节点只用于分组，落库须按 KGE 节点（kge.knowledge_node）重算主节点与题目族，不能直接照抄。`);
  L.push("- 族划得偏宽的代价：同族的练习在掌握计算里记为 repeat_family（不算新证据），复测也会因为找不到新族而落空。拿不准时宁可不并族。");
  L.push("- 完全重复组的代价：若不并为同一规范题、也不并族，掌握计算会把重复题当成新族，同一道题被重复计入。");
  if (r.source === "aya") L.push("- 旧题组：阿鸦压轴专项已有题组（aya-sprint-*）。第六节列出同时在旧题组与新候选里的题，须先定以哪边为准。");
  L.push("");
  const c = r.counts;
  L.push("## 计数", "");
  for (const k of Object.keys(c).sort(cmp)) L.push(`- ${k}: ${String(c[k])}`);
  L.push("", "## 一、完全重复（规范文本相同）→ 是否同一规范题", "");
  if (r.exact_groups.length === 0) L.push("（无）");
  for (const g of r.exact_groups) {
    L.push(`- [ ] ${g.group}：${ids(g.items)}；规范题建议 \`${g.canonical_candidate}\`。是否同一规范题：是 / 否；规范题：（待填）`);
  }
  L.push("", "## 二、近似题（SimHash 与 Jaccard 两道门都过）→ 是否同一规范题", "");
  if (r.near_pairs.length === 0) L.push("（无）");
  for (const p of r.near_pairs) {
    L.push(`- [ ] \`${p.a}\` ↔ \`${p.b}\`（汉明距离 ${String(p.hamming)}，Jaccard ${String(p.jaccard)}）。是否同一规范题：是 / 否`);
  }
  L.push("", "## 三、题目族候选（模板相同、每题恰好一个主节点且相同）→ 是否成族", "");
  if (r.family_candidates.length === 0) L.push("（无）");
  for (const f of r.family_candidates) {
    L.push(`- [ ] 主节点 \`${f.main_node}\`：${ids(f.items)}。是否成族：是 / 否；族码建议 \`${f.code_suggestion}\`，确认族码：（待填）`);
  }
  L.push("", "## 四、待看：模板相同但跨主节点、含多主节点或无主节点的题（不成族）", "");
  if (r.review_templates.length === 0) L.push("（无）");
  for (const t of r.review_templates) {
    L.push(`- [ ] 模板 \`${t.template_sha256.slice(0, 12)}\`（${t.reasons.map((x) => REASON[x] ?? x).join("、")}）：`
      + `${t.groups.map((g) => `${nodes(g.main_nodes)} → ${ids(g.items)}`).join("；")}。处理意见：（待填）`);
  }
  L.push("", "## 五、同卷 / 相似卷（同卷只认规范文本一致的题，可复用解析；相似卷不复用）", "");
  if (r.paper_pairs.length === 0) L.push("（无）");
  for (const p of r.paper_pairs) {
    L.push(`- [ ] \`${p.a}\` ↔ \`${p.b}\`：${VERDICT[p.verdict]!}（题数 ${String(p.count_a)} / ${String(p.count_b)}，按序一致 ${String(p.ordered_matches)}，`
      + `占比 ${String(p.ordered_ratio)}；含近似 ${String(p.loose_ordered_matches)}，占比 ${String(p.loose_ratio)}；`
      + `首题${p.first_match ? "一致" : "不一致"}，尾题${p.last_match ? "一致" : "不一致"}，换序 ${String(p.out_of_order)}`
      + `${p.failed.length > 0 ? `；未判同卷：${p.failed.join("、")}` : ""}）。确认：是 / 否`);
  }
  if (r.source === "aya") {
    L.push("", "## 六、冲突：同时在旧题组（aya-sprint-*）与新候选里的题", "");
    if (r.legacy_family_conflicts.length === 0) L.push("（无）");
    for (const x of r.legacy_family_conflicts) {
      const parts = [x.exact_group ? `完全重复 ${x.exact_group}` : "", x.near_with.length > 0 ? `近似 ${ids(x.near_with)}` : "",
        x.family_candidate ? `族候选 \`${x.family_candidate}\`` : ""].filter((s) => s !== "");
      L.push(`- [ ] \`${x.id}\`：旧题组 \`${x.legacy_family}\`；新候选：${parts.join("；")}。以哪边为准：旧题组 / 新候选`);
    }
  }
  L.push("");
  return L.join("\n");
}
