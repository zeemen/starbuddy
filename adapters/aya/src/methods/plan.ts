// plan：学科内的候选任务（specs/adapter-aya-methods-sprint4/spec.md 设计要点第 1 节）。算法 aya-plan@0.1.0。
//
// 改写自 legacy lib/scoring.ts computePriorities（薄弱 × 前置影响 × 考查价值 × 板块频次 ÷ 成本），输入改为请求里平台给的
// 掌握快照 mastery[] 与前置路径 node_path，输出只有 ID、编码与数字（规则 11：不写关于学生的陈述）。
// - 不算掌握度（规则 2）：只读平台给的 state / p_mastery；
// - 不排期：复测何时做由 4-C 决定，这里只按 next_retest_at 的时刻升序排列，不判断是否到期；
// - 不读系统时钟：输出只由请求与内容包决定；
// - 前置关系只来自 node_path（知识结构以 KGE 为准），并且要有“阿鸦节点 → KGE 知识节点”对应表才用得上；
// - learner_context、constraints.exam_date 在 v0 不参与计算（只参与请求体指纹）。
// 公式与参数照搬 legacy，待教研校准（spec 待人确认 #6）；这是排序启发式，不是证据权重或门槛。
import type { LoadedPack, PackItem } from "../content.ts";
import { ProblemError } from "../problem.ts";
import { formatAllowed, instant, localId } from "./shared.ts";

export const PLAN_ALGORITHM = "aya-plan@0.1.0";
/** 没有时间预算时最多给几个任务 */
export const PLAN_MAX_TASKS = 8;
/** 每个任务最多带几道题（按题号取前几道） */
export const PLAN_ITEMS_PER_TASK = 3;
/** 复测任务的优先级区间：高于全部练习任务 */
export const RETEST_PRIORITY_HIGH = 1.0;
export const RETEST_PRIORITY_LOW = 0.85;
/** 练习任务的优先级区间 */
export const PRACTICE_PRIORITY_HIGH = 0.8;
export const PRACTICE_PRIORITY_LOW = 0.1;
/** 没有 p_mastery 时按状态取的薄弱度 */
export const DEFAULT_WEAKNESS: Record<string, number> = { understand: 0.7, execute: 0.5 };
/** rationale_code 的阈值（legacy：后续节点数、南京卷高频板块） */
export const MANY_SUCCESSORS = 3;
export const HIGH_EXAM_SHARE = 0.15;

const RETEST_STATES = new Set(["stable", "transfer", "retain"]);
const PRACTICE_STATES = new Set(["understand", "execute"]);
const RETEST_USAGE_FALLBACK = new Set(["T", "L"]);
const PRACTICE_USAGES = new Set(["D", "T", "L"]);

/** 封闭的理由编码白名单（规则 12：不含性格、态度、天赋类编码） */
export const RATIONALE_CODES = ["retest_scheduled", "prerequisite_of_many", "high_exam_weight", "weak_mastery"] as const;
export type RationaleCode = (typeof RATIONALE_CODES)[number];

export interface MasterySnapshot {
  skill_node: string;
  state: "unseen" | "understand" | "execute" | "stable" | "transfer" | "retain";
  p_mastery?: number;
  next_retest_at?: string;
}
export interface PathStep {
  from_id: string;
  to_id: string;
  depth: number;
}
export interface PlanInput {
  mastery: MasterySnapshot[];
  constraints?: { time_budget_minutes?: number; exam_date?: string; focus_skill_nodes?: string[] };
  node_path?: PathStep[];
  exam_config?: { question_type_codes?: string[] };
}

export interface PlannedTask {
  kind: "retest" | "practice";
  /** 阿鸦节点（不带前缀） */
  skill: string;
  items: PackItem[];
  priority: number;
  estimatedMinutes: number;
  rationale: RationaleCode;
  /** 本任务的前置任务在 tasks 里的下标 */
  prerequisites: number[];
}

export interface PlanOutcome {
  tasks: PlannedTask[];
  /** 是否用到了 node_path 与对应表（用到时 source_version 带 graph 段） */
  usedGraph: boolean;
}

const round3 = (x: number) => Math.round(x * 1000) / 1000;
const byId = (a: PackItem, b: PackItem) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function planTasks(content: LoadedPack, input: PlanInput): PlanOutcome {
  const seen = new Set<string>();
  for (const m of input.mastery) {
    if (seen.has(m.skill_node)) throw new ProblemError(422, "INVALID_REQUEST", "mastery lists the same skill_node more than once");
    seen.add(m.skill_node);
  }
  const focus = new Set((input.constraints?.focus_skill_nodes ?? []).map(localId));
  const codes = input.exam_config?.question_type_codes;
  // 前置边只在有对应表时可用：node_path 用 KGE 知识节点 ID，mastery 用阿鸦能力点（spec 待人确认 #2）
  const steps = input.node_path ?? [];
  const usedGraph = steps.length > 0 && content.knowledgeNodesOf.size > 0;
  const edges = usedGraph ? steps : [];
  const successors = (skill: string) => {
    const mine = new Set(content.knowledgeNodesOf.get(skill) ?? []);
    return new Set(edges.filter((e) => mine.has(e.from_id)).map((e) => e.to_id)).size;
  };

  const pick = (skill: string, usages: Set<string>) =>
    (content.itemsByNode.get(skill) ?? []).filter((it) => usages.has(it.usage) && formatAllowed(it, codes)).sort(byId).slice(0, PLAN_ITEMS_PER_TASK);
  const minutes = (items: PackItem[]) => Math.max(1, items.reduce((s, it) => s + it.estMinutes, 0));

  const retests: { skill: string; at: number; items: PackItem[] }[] = [];
  const practice: { skill: string; raw: number; items: PackItem[]; rationale: RationaleCode }[] = [];
  for (const m of input.mastery) {
    const skill = localId(m.skill_node);
    const node = content.nodeById.get(skill);
    if (!node) continue; // 不在内容包里的能力点跳过，不报错
    if (focus.size > 0 && !focus.has(skill)) continue;
    if (RETEST_STATES.has(m.state) && m.next_retest_at !== undefined) {
      const r = pick(skill, new Set(["R"]));
      const items = r.length > 0 ? r : pick(skill, RETEST_USAGE_FALLBACK);
      if (items.length > 0) retests.push({ skill, at: instant(m.next_retest_at), items });
      continue;
    }
    if (!PRACTICE_STATES.has(m.state)) continue; // unseen 不排任务（应先诊断）
    const items = pick(skill, PRACTICE_USAGES);
    if (items.length === 0) continue;
    const weakness = m.p_mastery !== undefined ? 1 - m.p_mastery : DEFAULT_WEAKNESS[m.state]!;
    const succ = successors(skill);
    const examValue = node.kind === "S" || node.kind === "M" ? 1.2 : 1.0;
    const share = content.pack.board_exam_share.shares[node.board] ?? 0.1;
    const examFreq = 0.8 + 2.5 * share;
    const cost = node.kind === "M" ? 1.3 : 1.0;
    const raw = (weakness * (1 + 0.5 * succ) * examValue * examFreq) / cost;
    const rationale: RationaleCode = succ >= MANY_SUCCESSORS ? "prerequisite_of_many" : share >= HIGH_EXAM_SHARE ? "high_exam_weight" : "weak_mastery";
    practice.push({ skill, raw, items, rationale });
  }

  // 复测：按 next_retest_at 的时刻升序（不是字符串顺序），同一时刻按能力点 ID
  retests.sort((a, b) => a.at - b.at || (a.skill < b.skill ? -1 : a.skill > b.skill ? 1 : 0));
  practice.sort((a, b) => b.raw - a.raw || (a.skill < b.skill ? -1 : a.skill > b.skill ? 1 : 0));
  const ordered: Omit<PlannedTask, "prerequisites">[] = [
    ...retests.map((r, i) => ({
      kind: "retest" as const,
      skill: r.skill,
      items: r.items,
      priority: round3(RETEST_PRIORITY_HIGH - (RETEST_PRIORITY_HIGH - RETEST_PRIORITY_LOW) * (retests.length > 1 ? i / (retests.length - 1) : 0)),
      estimatedMinutes: minutes(r.items),
      rationale: "retest_scheduled" as const,
    })),
    // 练习的原始值没有上界，用 raw / (1 + raw) 折进区间：只依赖本能力点，不随同批其他任务变化
    ...practice.map((p) => ({
      kind: "practice" as const,
      skill: p.skill,
      items: p.items,
      priority: round3(PRACTICE_PRIORITY_LOW + (PRACTICE_PRIORITY_HIGH - PRACTICE_PRIORITY_LOW) * (p.raw / (1 + p.raw))),
      estimatedMinutes: minutes(p.items),
      rationale: p.rationale,
    })),
  ];

  // 截断：有预算时按顺序累加，超出就停；没有预算时最多 PLAN_MAX_TASKS 个
  const budget = input.constraints?.time_budget_minutes;
  const selected: Omit<PlannedTask, "prerequisites">[] = [];
  let used = 0;
  for (const t of ordered) {
    if (budget !== undefined) {
      if (used + t.estimatedMinutes > budget) break;
    } else if (selected.length >= PLAN_MAX_TASKS) break;
    used += t.estimatedMinutes;
    selected.push(t);
  }

  // 前置：A 的某个知识节点是 B 的某个知识节点的前置（node_path 里有 A → B 的边）时，A 是 B 的前置任务
  const tasks: PlannedTask[] = selected.map((t) => ({ ...t, prerequisites: [] }));
  if (usedGraph) {
    for (const [bi, b] of tasks.entries()) {
      const to = new Set(content.knowledgeNodesOf.get(b.skill) ?? []);
      for (const [ai, a] of tasks.entries()) {
        if (ai === bi) continue;
        const from = new Set(content.knowledgeNodesOf.get(a.skill) ?? []);
        if (edges.some((e) => from.has(e.from_id) && to.has(e.to_id))) b.prerequisites.push(ai);
      }
    }
  }
  return { tasks, usedGraph };
}
