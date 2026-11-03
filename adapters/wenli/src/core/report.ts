// report 的计数（纯函数，不调模型，不读时钟）。只产出编码、计数与证据 ID，不写任何结论句与评价（规则 11）；
// 面向学生家长的句子由洞察服务按判断模板生成（ADR 13，评审 sie-#1）。
// - 时间一律按带时区的时刻比较（评审 N10），窗口为 [from, to)。
// - 作答次数：窗内作答类证据按 attempt_id 去重（没有 attempt_id 时按 evidence_id），同一道作答的多条证据只算一次。
// - trend（契约必填编码）：只比较平台给的 mastery_history 的状态序，不重算掌握度（规则 2）：
//   窗前最后一条为基线；没有基线 → new；窗内没有记录 → flat；否则比较窗内最后一条与基线。

/** 作答类证据（学生原文、选项、转写） */
export const ATTEMPT_KINDS: ReadonlySet<string> = new Set(["response_text", "response_choice", "ocr_transcription", "asr_transcription"]);
/** 平台掌握状态的顺序（契约 MasterySnapshot.state） */
export const STATE_ORDER = ["unseen", "understand", "execute", "stable", "transfer", "retain"] as const;

export type Trend = "up" | "flat" | "down" | "new";

export interface HistoryRecord {
  skill_node: string;
  state: string;
  at: string;
}

export const instant = (t: string): number => Date.parse(t);

export function inWindow(t: string, from: number, to: number): boolean {
  const x = instant(t);
  return Number.isFinite(x) && x >= from && x < to;
}

/** 一个能力点的 trend。history 只含该能力点的记录（顺序不限，同一时刻按出现顺序） */
export function trendOf(history: HistoryRecord[], from: number, to: number): Trend {
  const sorted = history
    .map((h, i) => ({ h, i, t: instant(h.at) }))
    .filter((x) => Number.isFinite(x.t) && x.t < to)
    .sort((a, b) => a.t - b.t || a.i - b.i);
  const before = sorted.filter((x) => x.t < from);
  const within = sorted.filter((x) => x.t >= from);
  if (before.length === 0) return "new";
  if (within.length === 0) return "flat";
  const rank = (s: string) => (STATE_ORDER as readonly string[]).indexOf(s);
  const d = rank(within[within.length - 1]!.h.state) - rank(before[before.length - 1]!.h.state);
  return d > 0 ? "up" : d < 0 ? "down" : "flat";
}
