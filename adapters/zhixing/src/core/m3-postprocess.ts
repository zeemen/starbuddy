// 道法 M3 判分的确定性部分：模型输出的结构校验 + 后处理（spec 设计要点 3 第 2 段；待人确认 #2 口径）。
//
// 移植自 legacy（只读）：legacy/wenli/初中语文_AI_class/wenli/apps/daofa/app/api/m3/grade/route.ts
//   :22 round2（0.5 的整数倍）、:84-90 逐点 clamp 与总分重算、:92-93 满分强制二评。
// 相对 legacy 的改动：
//   - 模型输出不合 schema 时不回 502，而是整题转复核（全部采分点 achieved = null）；
//   - 证据位置必须落在作答原文内且非空（得分 > 0 时必填），否则该点转复核；
//   - 判定档、归因、得分三者矛盾（例如满分却有失分归因、归因为 none 却不是满分、miss 却得分）时该点转复核；
//   - 不读模型自报的 needs_human_review：M3 一律复核（决定 A2），复核原因由适配器按规则给出。
// 纯函数，不做 IO；分数只来自 Rubric 分值与模型逐点判定经本模块处理后的结果（CLAUDE.md 规则 3）。
import { Ajv2020 } from "ajv/dist/2020.js";
import type { PackItem } from "../content.ts";
import { ATTRIBUTIONS, JUDGMENTS, type JudgmentCode, type ModelAttribution } from "./m3-prompt.ts";

export const MAX_MODEL_OUTPUT_CHARS = 32_000;

export interface ModelPoint {
  point_id: string;
  judgment: JudgmentCode;
  score_awarded: number;
  evidence_start: number | null;
  evidence_end: number | null;
  attribution: ModelAttribution;
}

const MODEL_OUTPUT_SCHEMA = {
  type: "object",
  required: ["points"],
  additionalProperties: false,
  properties: {
    points: {
      type: "array",
      minItems: 1,
      maxItems: 30,
      items: {
        type: "object",
        required: ["point_id", "judgment", "score_awarded", "evidence_start", "evidence_end", "attribution"],
        additionalProperties: false,
        properties: {
          point_id: { type: "string", pattern: "^[a-z0-9_\\-]{1,48}$" },
          judgment: { enum: [...JUDGMENTS] },
          score_awarded: { type: "number" },
          evidence_start: { type: ["integer", "null"] },
          evidence_end: { type: ["integer", "null"] },
          attribution: { enum: [...ATTRIBUTIONS] },
        },
      },
    },
  },
} as const;

const validateModelOutput = new Ajv2020({ allErrors: false, strict: true }).compile(MODEL_OUTPUT_SCHEMA);

/** 允许模型在 JSON 外面包一层 ```json 代码块（常见的格式偏差）；除此之外必须是纯 JSON。 */
function stripFence(s: string): string {
  const t = s.trim();
  const m = /^```(?:json)?\s*\n([\s\S]*)\n```$/.exec(t);
  return m ? m[1]!.trim() : t;
}

/** 解析模型输出。返回 null 表示不合格（非 JSON、不合 schema、过长、point_id 重复）：整题转复核。 */
export function parseModelOutput(output: string): ModelPoint[] | null {
  if (typeof output !== "string" || output.length > MAX_MODEL_OUTPUT_CHARS) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(stripFence(output));
  } catch {
    return null;
  }
  if (!validateModelOutput(raw)) return null;
  const points = (raw as { points: ModelPoint[] }).points;
  if (new Set(points.map((p) => p.point_id)).size !== points.length) return null;
  if (!points.every((p) => Number.isFinite(p.score_awarded))) return null;
  return points;
}

/** legacy round2：取 0.5 的整数倍 */
export const roundHalf = (n: number) => Math.round(n * 2) / 2;
export const clamp = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), hi);

/** 一个采分点为什么转复核（只作适配器内部与测试用，不进响应） */
export type UndecidedReason =
  | "model_output_invalid"
  | "point_missing"
  | "evidence_out_of_range"
  | "evidence_missing"
  | "judgment_score_conflict"
  | "attribution_score_conflict";

export type PointOutcome =
  | {
      point_id: string;
      decided: true;
      score: number;
      max: number;
      achieved: boolean;
      /** [start, end)，按码点计；得分为 0 时为 null */
      span: [number, number] | null;
      attribution: Exclude<ModelAttribution, "none"> | null;
    }
  | { point_id: string; decided: false; max: number; reason: UndecidedReason };

export interface PostProcessed {
  points: PointOutcome[];
  /** 全部采分点都已判定时的总分（0.5 的整数倍）；有任何一点待复核时为 null */
  total: number | null;
  /** 满分：强制二评（legacy :92-93） */
  fullScore: boolean;
}

function checkSpan(p: ModelPoint, answerLength: number, score: number): [number, number] | null | "missing" | "out_of_range" {
  const { evidence_start: s, evidence_end: e } = p;
  if (s === null && e === null) return score === 0 && p.judgment === "miss" ? null : "missing";
  if (s === null || e === null) return "out_of_range";
  if (!(Number.isInteger(s) && Number.isInteger(e) && s >= 0 && e > s && e <= answerLength)) return "out_of_range";
  return [s, e];
}

/**
 * 逐点后处理。points 顺序以内容包的 Rubric 为准；模型多给的 point_id 忽略，少给的该点转复核。
 * answerLength 是作答的码点数（与 Prompt 声明的计数口径一致）。
 */
export function postProcess(item: PackItem, modelPoints: ModelPoint[] | null, answerLength: number): PostProcessed {
  const byId = new Map((modelPoints ?? []).map((p) => [p.point_id, p]));
  const points: PointOutcome[] = item.points.map((def): PointOutcome => {
    const max = def.score;
    if (modelPoints === null) return { point_id: def.point_id, decided: false, max, reason: "model_output_invalid" };
    const mp = byId.get(def.point_id);
    if (!mp) return { point_id: def.point_id, decided: false, max, reason: "point_missing" };
    const score = roundHalf(clamp(mp.score_awarded, 0, max));
    const span = checkSpan(mp, answerLength, score);
    if (span === "missing") return { point_id: def.point_id, decided: false, max, reason: "evidence_missing" };
    if (span === "out_of_range") return { point_id: def.point_id, decided: false, max, reason: "evidence_out_of_range" };
    const full = score === max;
    // 判定档与得分：full ⇔ 满分；miss ⇔ 0 分；其余两档必须在 (0, 满分) 之间
    const judgmentOk =
      mp.judgment === "full" ? full : mp.judgment === "miss" ? score === 0 : score > 0 && score < max;
    if (!judgmentOk) return { point_id: def.point_id, decided: false, max, reason: "judgment_score_conflict" };
    // 归因与得分：满分 ⇔ none（包括“归因 none 却 0 分”这类矛盾）
    if (full !== (mp.attribution === "none")) return { point_id: def.point_id, decided: false, max, reason: "attribution_score_conflict" };
    return {
      point_id: def.point_id,
      decided: true,
      score,
      max,
      achieved: full,
      // 片段证据只记“命中”的片段（契约 EvidencePayloadSpan）：0 分点即使给了失分句段也不记为证据（位置仍已核对）
      span: score > 0 ? span : null,
      attribution: mp.attribution === "none" ? null : mp.attribution,
    };
  });
  const allDecided = points.every((p) => p.decided);
  const total = allDecided ? roundHalf(points.reduce((s, p) => s + (p.decided ? p.score : 0), 0)) : null;
  return { points, total, fullScore: total !== null && total >= item.total_score };
}
