// 练习目标选择（纯函数，spec 设计要点 4）。
//
// 移植自 legacy（只读）：legacy/wenli/初中语文_AI_class/wenli/packages/shared/lib/domain/adaptive.ts
//   （recommendTargets、pickForDomain、zoneDistance；MASTERED = EXCELLENT_LINE 0.85，最优练习窗 0.4–0.6）。
// 相对 legacy 的改动：
//   - 域与层来自内容包的合成能力格（每域的层按 L1→L4 自然进阶），不再是固定的 7 × 4；
//   - “是否已掌握”由调用方给出（平台 MasterySnapshot.state，见 adapter.ts 的映射），不在这里用 0.85 门槛判：
//     掌握状态只由平台计算（规则 2），适配器不重算；数值 mastery 只用于“离最优练习窗多远”的排序；
//   - 没有移植 nextLevel（session 内难度步进：legacy 没有调用点，本任务也不出题）。
import { LEVELS, type LoadedPack } from "../content.ts";

/** legacy EXCELLENT_LINE：数值掌握度的“已牢”线（只在调用方没有状态、只有数值时参考） */
export const MASTERED_LINE = 0.85;
const ZONE_LO = 0.4;
const ZONE_HI = 0.6;

export interface CellState {
  cell: string;
  /** 用于排序的数值掌握度（0–1） */
  mastery: number;
  /** 平台判定为已掌握 */
  mastered: boolean;
}

/** 距最优练习窗的距离（越小越该练） */
export function zoneDistance(mastery: number): number {
  if (mastery >= ZONE_LO && mastery <= ZONE_HI) return 0;
  return mastery < ZONE_LO ? ZONE_LO - mastery : mastery - ZONE_HI;
}

export interface Target {
  cell: string;
  domain: string;
  level: string;
  /** 该格没有任何掌握数据（空白起点） */
  blank: boolean;
}

/** 某域代表格：取最低的“未练或未掌握”层级（L1→L4 自然进阶）；各层都已掌握 → null。空白域落在最低层。 */
function pickForDomain(content: LoadedPack, domain: string, states: Map<string, CellState>): Target | null {
  const cells = content.pack.cells
    .filter((c) => c.domain === domain)
    .sort((a, b) => LEVELS.indexOf(a.level) - LEVELS.indexOf(b.level));
  for (const c of cells) {
    const s = states.get(c.id);
    if (!s || !s.mastered) return { cell: c.id, domain, level: c.level, blank: !s };
  }
  return null;
}

/**
 * 推荐 n 个最近发展区目标，每域至多 1 个（保广度）。价值越小越优先（离练习窗近 / 空白起点 = 0）；
 * 同价值按内容包的域顺序（稳定排序）。
 */
export function recommendTargets(content: LoadedPack, states: readonly CellState[], n = 3): Target[] {
  const byCell = new Map(states.map((s) => [s.cell, s]));
  const picks = content.pack.domains
    .map((d) => {
      const p = pickForDomain(content, d.code, byCell);
      if (!p) return null;
      const s = byCell.get(p.cell);
      return { pick: p, value: s ? zoneDistance(s.mastery) : 0 };
    })
    .filter((x): x is { pick: Target; value: number } => !!x);
  picks.sort((a, b) => a.value - b.value);
  return picks.slice(0, n).map((x) => x.pick);
}
