// 信号来源可靠性权重与缺口能力格选择（纯函数，spec 设计要点 3）。
//
// 移植自 legacy（只读）：legacy/wenli/初中语文_AI_class/wenli/packages/shared/lib/domain/coverage.ts
//   （SOURCE_WEIGHT、computeCoverage、pickGapCells）。
// 相对 legacy 的改动：
//   - SOURCE_WEIGHT 只保留本适配器能从契约请求里分辨的两种通道：原生测评 1.0、拍照已确认 0.7（待人确认 #6：沿用 legacy 值，未校准）；
//     history / guided_input / interview / self_report 没有移植（契约里没有对应的通道信息）；
//   - 28 格全集改为内容包里的合成能力格，域 ROI 权重来自内容包（待人确认 #5），域、层的自然顺序取内容包顺序；
//   - 没有移植 summarizeCoverageByDomain（报告层“学情画像可信度”，属于关于学生的陈述，规则 11）。
// pickGapCells 目前没有接入任何方法的输出：契约 DiagnoseResult 没有“下一轮建议测评的能力格”字段（待人确认 #7，见 README）。
import { LEVELS, type LoadedPack } from "../content.ts";

export type SignalSource = "native_test" | "ocr_confirmed";

/** 各来源可靠性权重（legacy 文档 §5 融合权重，原值） */
export const SOURCE_WEIGHT: Readonly<Record<SignalSource, number>> = {
  native_test: 1.0, // 系统原生作答（键盘、点选），最可靠
  ocr_confirmed: 0.7, // 拍照识别经确认（ADR 15 B 级）
};

export interface CoverageEvent {
  cell: string;
  source: SignalSource;
}

export interface CellCoverage {
  /** 该格已见来源的最大权重 */
  confidence: number;
  /** 去重来源列表 */
  covered_by: SignalSource[];
}

/** 聚合信号事件 → 每格覆盖度。confidence 取最强来源，covered_by 去重。 */
export function computeCoverage(events: readonly CoverageEvent[]): Map<string, CellCoverage> {
  const out = new Map<string, CellCoverage>();
  for (const e of events) {
    const cur = out.get(e.cell) ?? { confidence: 0, covered_by: [] };
    if (!cur.covered_by.includes(e.source)) cur.covered_by.push(e.source);
    cur.confidence = Math.max(cur.confidence, SOURCE_WEIGHT[e.source] ?? 0);
    out.set(e.cell, cur);
  }
  return out;
}

export type GapReason = "untouched" | "low_confidence";

export interface GapCell {
  cell: string;
  domain: string;
  level: string;
  reason: GapReason;
  roi: number;
  confidence: number;
}

export interface GapOptions {
  /** 覆盖置信低于此值即缺口，默认 0.6 */
  minConfidence?: number;
  /** 返回上限，默认 10 */
  n?: number;
}

/**
 * 遍历内容包的能力格全集，挑出缺口并按 ROI 降序。ROI = 域权重 × (1 - 覆盖置信)：越重要、越没把握的格越优先。
 * 同 ROI 时按域、层的自然顺序稳定排序。
 */
export function pickGapCells(content: LoadedPack, coverage: Map<string, CellCoverage>, opts: GapOptions = {}): GapCell[] {
  const minConfidence = opts.minConfidence ?? 0.6;
  const n = opts.n ?? 10;
  const domainOrder = content.pack.domains.map((d) => d.code);
  const gaps: GapCell[] = [];
  for (const cell of content.pack.cells) {
    const confidence = coverage.get(cell.id)?.confidence ?? 0;
    if (confidence >= minConfidence) continue; // 已覆盖，非缺口
    gaps.push({
      cell: cell.id,
      domain: cell.domain,
      level: cell.level,
      reason: confidence === 0 ? "untouched" : "low_confidence",
      roi: content.domainByCode.get(cell.domain)!.roi * (1 - confidence),
      confidence,
    });
  }
  gaps.sort((a, b) => {
    if (b.roi !== a.roi) return b.roi - a.roi;
    const dd = domainOrder.indexOf(a.domain) - domainOrder.indexOf(b.domain);
    if (dd !== 0) return dd;
    return LEVELS.indexOf(a.level as (typeof LEVELS)[number]) - LEVELS.indexOf(b.level as (typeof LEVELS)[number]);
  });
  return gaps.slice(0, n);
}
