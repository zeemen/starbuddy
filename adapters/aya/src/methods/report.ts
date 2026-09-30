// report：按时间窗提供学科字段（specs/adapter-aya-methods-sprint4/spec.md 设计要点第 3 节）。算法 aya-report@0.1.0。
//
// 只给字段与证据引用，不写结论句（规则 11；评审 sie-#1）：trend、highlight_codes 是编码，面向学生家长的句子由洞察服务
// 按判断模板生成并引用判断账本。没有 forecast（v1.0 已移除，预测只由 insight.predict 给编排层）。
// 不输出原文、排名、百分位、同伴比较、掌握度数值；不重算掌握度，只读平台给的 mastery_history 里的 state。
// 三种受众输出同样的字段（家长端不出现阿鸦，规格书 §4.11，由平台人设层负责）。learner_context 在 v0 不参与计算。
import { ProblemError } from "../problem.ts";
import type { AyaTaxonomy } from "../taxonomy.ts";
import { instant, isAyaRef } from "./shared.ts";

export const REPORT_ALGORITHM = "aya-report@0.1.0";

/** 掌握状态的先后（KGE 五道门槛之前的 unseen 最低） */
export const STATE_ORDER = ["unseen", "understand", "execute", "stable", "transfer", "retain"] as const;
/** 封闭白名单（编码待教研与 5-H 确认）：不含性格、态度、天赋类编码（规则 12） */
export const HIGHLIGHT_CODES = ["skills_improved", "skills_declined", "no_activity"] as const;
const RESPONSE_KINDS = new Set(["response_text", "response_choice"]);

export interface EvidenceRecord {
  evidence_id: string;
  attempt_id?: string;
  kind: string;
  occurred_at: string;
  item_ref?: string;
  skill_node?: string;
  admission: string;
}
export interface ErrorAttributionIn {
  skill_node?: string;
  subject_category: string;
  cross_subject_category: string;
  confidence: number;
  evidence_ids: string[];
}
export interface MasteryPoint {
  skill_node: string;
  state: (typeof STATE_ORDER)[number];
  at: string;
}
export interface ReportInput {
  window: { from: string; to: string };
  evidence?: EvidenceRecord[];
  error_attributions?: ErrorAttributionIn[];
  mastery_history?: MasteryPoint[];
}
export type Trend = "up" | "flat" | "down" | "new";
export interface ReportOutcome {
  skillSummary: { skill_node: string; trend: Trend; attempts: number }[];
  errorDistribution: { cross_subject_category: string; subject_category: string; count: number }[];
  highlightCodes: (typeof HIGHLIGHT_CODES)[number][];
  /** 计入统计的请求证据 ID（窗内的作答证据，以及计入的错因引用到的窗内证据） */
  evidenceIds: string[];
  /** 计入统计的证据里有未准入的 */
  anyUnadmitted: boolean;
  /** 计入的错因记录置信度的最小值；没有错因记录时是 1.0 */
  confidence: number;
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/**
 * 趋势：比较窗前最后一条记录（平台附上的基线）与窗内最后一条记录的状态先后。
 * 窗前没有任何记录 → new；窗内没有新记录 → 与基线相同，flat。窗后（≥ to）的记录不计。
 */
export function trendOf(points: { state: MasteryPoint["state"]; at: number }[], from: number, to: number): Trend {
  const sorted = points.map((p, i) => ({ ...p, i })).sort((a, b) => a.at - b.at || a.i - b.i);
  const before = sorted.filter((p) => p.at < from);
  const inside = sorted.filter((p) => p.at >= from && p.at < to);
  if (before.length === 0) return "new";
  const base = STATE_ORDER.indexOf(before[before.length - 1]!.state);
  const last = inside.length > 0 ? STATE_ORDER.indexOf(inside[inside.length - 1]!.state) : base;
  return last > base ? "up" : last < base ? "down" : "flat";
}

export function buildReport(taxonomy: AyaTaxonomy, input: ReportInput): ReportOutcome {
  const evidence = input.evidence ?? [];
  const attributions = input.error_attributions ?? [];
  const history = input.mastery_history ?? [];
  // report 的历史数据不在 schema 的学科前缀约束内（评审 N7），由适配器检查
  const refs = [
    ...evidence.flatMap((e) => [e.item_ref, e.skill_node]),
    ...attributions.map((a) => a.skill_node),
    ...history.map((h) => h.skill_node),
  ].filter((r): r is string => r !== undefined);
  if (refs.some((r) => !isAyaRef(r))) throw new ProblemError(400, "SUBJECT_MISMATCH", "report data contains non-aya references");

  const from = instant(input.window.from);
  const to = instant(input.window.to);
  if (to <= from) throw new ProblemError(422, "INVALID_REQUEST", "window.to must be later than window.from");

  const inWindow = (ts: string) => {
    const t = instant(ts);
    return t >= from && t < to;
  };
  const windowed = new Map(evidence.filter((e) => inWindow(e.occurred_at)).map((e) => [e.evidence_id, e]));
  const outside = new Set(evidence.filter((e) => !windowed.has(e.evidence_id)).map((e) => e.evidence_id));

  // attempts：窗内作答证据按能力点、按 attempt_id 去重计数（同一道作答的多条证据只算一次）
  const counted = new Set<string>();
  const attemptsBySkill = new Map<string, Set<string>>();
  for (const e of windowed.values()) {
    if (!RESPONSE_KINDS.has(e.kind)) continue;
    counted.add(e.evidence_id);
    if (!e.skill_node) continue;
    const set = attemptsBySkill.get(e.skill_node) ?? new Set<string>();
    set.add(e.attempt_id ?? e.evidence_id);
    attemptsBySkill.set(e.skill_node, set);
  }

  // 错因：平台已按时间窗选好；引用的证据全部在窗外的不计；敏感类（心理与习惯）不计入，与 grade 一致
  const sensitive = new Set([...taxonomy.byRootCause.values()].filter((e) => e.sensitive).map((e) => e.code));
  const dist = new Map<string, { cross_subject_category: string; subject_category: string; count: number }>();
  let confidence = 1;
  for (const a of attributions) {
    if (sensitive.has(a.subject_category)) continue;
    if (a.evidence_ids.every((id) => outside.has(id))) continue;
    const key = `${a.cross_subject_category}\0${a.subject_category}`;
    const row = dist.get(key) ?? { cross_subject_category: a.cross_subject_category, subject_category: a.subject_category, count: 0 };
    row.count++;
    dist.set(key, row);
    confidence = Math.min(confidence, a.confidence);
    for (const id of a.evidence_ids) if (windowed.has(id)) counted.add(id);
  }

  // 能力点：窗内有作答或窗内有掌握记录的
  const historyBySkill = new Map<string, { state: MasteryPoint["state"]; at: number }[]>();
  for (const h of history) {
    const list = historyBySkill.get(h.skill_node) ?? [];
    list.push({ state: h.state, at: instant(h.at) });
    historyBySkill.set(h.skill_node, list);
  }
  const skills = new Set<string>(attemptsBySkill.keys());
  for (const [skill, points] of historyBySkill) if (points.some((p) => p.at >= from && p.at < to)) skills.add(skill);
  const skillSummary = [...skills].sort(cmp).map((skill) => ({
    skill_node: skill,
    trend: trendOf(historyBySkill.get(skill) ?? [], from, to),
    attempts: attemptsBySkill.get(skill)?.size ?? 0,
  }));

  const highlightCodes: ReportOutcome["highlightCodes"] = [];
  if (skillSummary.some((s) => s.trend === "up")) highlightCodes.push("skills_improved");
  if (skillSummary.some((s) => s.trend === "down")) highlightCodes.push("skills_declined");
  if (![...windowed.values()].some((e) => RESPONSE_KINDS.has(e.kind))) highlightCodes.push("no_activity");

  const evidenceIds = [...counted].sort(cmp);
  return {
    skillSummary,
    errorDistribution: [...dist.values()].sort((a, b) => cmp(a.cross_subject_category, b.cross_subject_category) || cmp(a.subject_category, b.subject_category)),
    highlightCodes,
    evidenceIds,
    anyUnadmitted: evidenceIds.some((id) => windowed.get(id)!.admission !== "admitted"),
    confidence,
  };
}
