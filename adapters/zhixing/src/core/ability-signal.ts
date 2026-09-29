// M3 逐点判定 → 五维中四维的达成率（算法参考，不是掌握度，决定 A1：掌握度只由平台计算）。
//
// 移植自 legacy（只读）：
//   legacy/wenli/初中语文_AI_class/wenli/packages/shared/lib/domain/daofa-ability-signal.ts（m3ToDimensionRates）
//   legacy/wenli/初中语文_AI_class/wenli/packages/shared/lib/domain/daofa-ability.ts（ATTRIBUTION_TO_DIMENSION、M5 五维）
// 改动：归因改用错因编码（shenti_cuo 等），维度键改用合成节点的 dimension 编码；去掉面向学生的维度释义文本。
//
// 现状（适配器 README“待人确认”）：契约 DiagnoseRequest 没有携带既往逐点判分（得分与归因）的字段，
// diagnose 只能看到既往证据，拿不到本函数需要的输入，所以本函数暂未接入 diagnose；契约补字段（必停项）后直接可用。
import type { AttributionCode } from "../content.ts";

export const ATTRIBUTION_TO_DIMENSION: Record<AttributionCode, string> = {
  shenti_cuo: "shenti",
  dingwei_cuo: "dingwei",
  zhuanhua_cuo: "zhuanhua",
  guifan_cuo: "guifan",
};

/** 一个采分点失分，最多把该维度从整体达成率下拉 50%（legacy PENALTY_WEIGHT） */
export const PENALTY_WEIGHT = 0.5;

const clamp01 = (n: number) => Math.max(0, Math.min(1, n));
const round3 = (n: number) => Number(n.toFixed(3));

export interface PointForSignal {
  score_awarded: number;
  /** null 表示满分、无失分归因 */
  attribution: AttributionCode | null;
}

export interface M3Signal {
  overallRate: number;
  /** 只含 M3 供给的四维（时政敏感度不由 M3 供给） */
  rates: Record<string, number>;
}

export function m3ToDimensionRates(points: PointForSignal[], totalMax: number): M3Signal {
  const total = points.reduce((s, p) => s + p.score_awarded, 0);
  const overall = clamp01(totalMax > 0 ? total / totalMax : 0);
  const n = points.length || 1;
  const failByAttr: Partial<Record<AttributionCode, number>> = {};
  for (const p of points) if (p.attribution) failByAttr[p.attribution] = (failByAttr[p.attribution] ?? 0) + 1;
  const rates: Record<string, number> = {};
  for (const [attr, dim] of Object.entries(ATTRIBUTION_TO_DIMENSION) as [AttributionCode, string][]) {
    const penalty = (failByAttr[attr] ?? 0) / n;
    rates[dim] = round3(clamp01(overall * (1 - PENALTY_WEIGHT * penalty)));
  }
  return { overallRate: round3(overall), rates };
}
