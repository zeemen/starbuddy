// 知行 plan 的排序（纯函数，不调模型，不读时钟）。KGE V1.1 第八章：道法学习路径按“领域权重 × 失分 × 可提升”排序；
// 观点之间以“关联”为主，不为路径规划硬凑前置。
//
// - 规划单元：能力视图节点（契约 PlanTask.skill_nodes 只能写能力点，写不下领域；按（节点，领域）拆会出现看起来相同的任务）。
//   只有挂了道法采分点的节点才有单元（没有题就排不了练习）。
// - 领域权重：plan-weights.json（合成值，待教研校准）。节点的领域权重 = 该节点全部道法采分点的领域权重按分值加权平均，
//   即“这个维度的一分平均落在多重的领域里”。
// - 失分：有 p_mastery 用 1 − p_mastery；没有按状态取 DEFAULT_LOSS；没有掌握记录或 unseen 视为未测（kind = diagnose）。
// - 可提升：按状态取 GAIN；transfer、retain（星亮门槛，ADR 11）为 0，不排任务。
// - 前置：只用编排层给的 node_path（知识服务 node.path 只走强前置边，KGE 第八章）。观点之间只有 related 边时 node_path 为空，
//   不产生任何 prerequisite_task_ids；适配器不从 related 边或内容包推出前置。
// 以上常数都是合成值、待教研校准（spec 待人确认 #2）；输出只有 ID、编码、数字，不写关于学生的陈述（规则 11）。
import type { PackItem } from "../content.ts";

/** 没有 p_mastery 时按状态取的失分（合成值，待教研校准）；unseen 与缺记录取中性值 */
export const DEFAULT_LOSS: Readonly<Record<string, number>> = { unseen: 0.5, understand: 0.7, execute: 0.5, stable: 0.25, transfer: 0.1, retain: 0.05 };
/** 可提升系数（合成值，待教研校准）；已星亮的状态为 0 */
export const GAIN: Readonly<Record<string, number>> = { unseen: 1, understand: 1, execute: 0.8, stable: 0.5, transfer: 0, retain: 0 };
/** 领域权重不低于它时，理由编码为 high_exam_weight */
export const HIGH_EXAM_WEIGHT = 0.2;
/** 每道道法主观题的预计分钟数（合成值，待教研校准）；没有已准入题时按一道算 */
export const MINUTES_PER_ITEM = 8;
export const MAX_ITEMS_PER_TASK = 3;
/** 没有时间预算时最多给几个任务 */
export const MAX_TASKS_WITHOUT_BUDGET = 8;

export interface PlanUnit {
  skillNode: string;
  /** 按分值加权平均的领域权重 */
  weight: number;
  /** 本单元涉及的知识节点（采分点 node_ids，MORAL.*） */
  viewpoints: Set<string>;
  /** 本单元涉及的道法题（按题号排序） */
  items: PackItem[];
}

export interface MasteryInput {
  state: string;
  p_mastery?: number;
}

export interface RankedUnit extends PlanUnit {
  kind: "diagnose" | "practice";
  loss: number;
  gain: number;
  /** 领域权重 × 失分 × 可提升（未取整） */
  raw: number;
  rationale: "not_yet_assessed" | "high_exam_weight" | "weak_mastery";
}

/** 由内容包展开规划单元（只含道法题；按内容包 nodes 顺序） */
export function planUnits(items: PackItem[], nodeOrder: string[], domainOfPoint: (p: PackItem["points"][number]) => string, weightOf: (domain: string) => number): PlanUnit[] {
  const acc = new Map<string, { unit: PlanUnit; scored: number; total: number }>();
  for (const it of [...items].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) {
    if (it.discipline !== "daofa") continue;
    for (const p of it.points) {
      let a = acc.get(p.skill_node);
      if (!a) {
        a = { unit: { skillNode: p.skill_node, weight: 0, viewpoints: new Set(), items: [] }, scored: 0, total: 0 };
        acc.set(p.skill_node, a);
      }
      a.scored += p.score * weightOf(domainOfPoint(p));
      a.total += p.score;
      for (const n of p.node_ids) a.unit.viewpoints.add(n);
      if (!a.unit.items.includes(it)) a.unit.items.push(it);
    }
  }
  return [...acc.values()].map((a) => ({ ...a.unit, weight: a.scored / a.total })).sort((a, b) => compareBase(a, b, nodeOrder));
}

function compareBase(a: PlanUnit, b: PlanUnit, nodeOrder: string[]): number {
  const na = nodeOrder.indexOf(a.skillNode);
  const nb = nodeOrder.indexOf(b.skillNode);
  if (na !== nb) return na - nb;
  return a.skillNode < b.skillNode ? -1 : a.skillNode > b.skillNode ? 1 : 0;
}

/** 按“领域权重 × 失分 × 可提升”降序排；同分按内容包节点顺序。可提升为 0 的单元不出现。 */
export function rankUnits(units: PlanUnit[], mastery: Map<string, MasteryInput>, nodeOrder: string[]): RankedUnit[] {
  const ranked: RankedUnit[] = [];
  for (const u of units) {
    const m = mastery.get(u.skillNode);
    const state = m?.state ?? "unseen";
    const gain = GAIN[state] ?? 0;
    const loss = m?.p_mastery !== undefined ? 1 - m.p_mastery : (DEFAULT_LOSS[state] ?? 0);
    const raw = u.weight * loss * gain;
    if (!(raw > 0)) continue;
    const kind = state === "unseen" ? "diagnose" : "practice";
    const rationale = kind === "diagnose" ? "not_yet_assessed" : u.weight >= HIGH_EXAM_WEIGHT ? "high_exam_weight" : "weak_mastery";
    ranked.push({ ...u, kind, loss, gain, raw, rationale });
  }
  // 浮点误差内视为同分（加权平均的求和顺序不同会差最后一位），再按节点顺序
  return ranked.sort((a, b) => (Math.abs(b.raw - a.raw) > 1e-12 ? b.raw - a.raw : 0) || compareBase(a, b, nodeOrder));
}

export interface PathStep {
  from_id: string;
  to_id: string;
  depth: number;
}

/**
 * 前置边：node_path 的一步 from_id → to_id（from 是 to 的强前置）落在两个不同单元的知识节点上时，后者依赖前者。
 * 只在已选中的单元之间连；会成环的边跳过（按 depth、from_id、to_id 的顺序处理，结果确定）。返回：单元下标 → 前置单元下标。
 */
export function prerequisiteEdges(selected: PlanUnit[], steps: PathStep[]): Map<number, number[]> {
  const deps = new Map<number, number[]>();
  const reaches = (from: number, to: number): boolean => {
    const seen = new Set<number>();
    const stack = [from];
    while (stack.length) {
      const x = stack.pop()!;
      if (x === to) return true;
      if (seen.has(x)) continue;
      seen.add(x);
      // x 依赖的单元
      stack.push(...(deps.get(x) ?? []));
    }
    return false;
  };
  const ordered = [...steps].sort((a, b) => a.depth - b.depth || (a.from_id < b.from_id ? -1 : a.from_id > b.from_id ? 1 : 0) || (a.to_id < b.to_id ? -1 : a.to_id > b.to_id ? 1 : 0));
  for (const s of ordered) {
    selected.forEach((pre, i) => {
      if (!pre.viewpoints.has(s.from_id)) return;
      selected.forEach((post, j) => {
        if (i === j || !post.viewpoints.has(s.to_id)) return;
        const cur = deps.get(j) ?? [];
        if (cur.includes(i)) return;
        // j 依赖 i；如果 i 已经（直接或间接）依赖 j，加上就成环
        if (reaches(i, j)) return;
        deps.set(j, [...cur, i]);
      });
    });
  }
  return deps;
}
