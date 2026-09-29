// 作文五维判分的确定性部分：模型输出的结构校验 + 后处理 + 字数规范规则（spec 设计要点 2 第 2 段；待人确认 #1、#3）。
//
// 移植自 legacy（只读）：
//   legacy/wenli/初中语文_AI_class/wenli/apps/student/app/api/essays/grade/route.ts 与 build-grade.ts 的做法——模型给每维 L1–L4，
//   高阶维度低置信、一类卷触发人工二评（essay-rubric.ts 评分 SOP ⑥）；
//   essay-rubric.ts DEDUCTION_RULES 的“字数不足每少 50 字扣 1 分（最多 2 分）”→ conventionScore。
// 相对 legacy 的改动：
//   - 分数不由模型估（legacy est_score / score_range 由模型给），而是按量表把档位换算成分值；不确定度档位同样换算成分值区间；
//   - 证据位置必须落在作文原文内且非空，否则该维不可判（score = null）并转复核；不确定度档位必须满足 low ≤ level ≤ high，否则同样不可判；
//   - 模型输出不合 schema 时不回 502，而是四维全部不可判（整篇转复核）；
//   - 各维都判定后才重算总分；一类卷（各维最高档）强制复核；
//   - 字数规范是确定性规则（rubric_rule），不调模型。
// 纯函数，不做 IO；分数只来自量表分值与模型逐维档位经本模块处理后的结果（CLAUDE.md 规则 3）。
import { Ajv2020 } from "ajv/dist/2020.js";
import { LEVELS, type ConventionRule, type Level, type RubricDimension } from "../content.ts";

export const MAX_MODEL_OUTPUT_CHARS = 32_000;

export interface ModelDimension {
  dimension_id: string;
  level: Level;
  evidence_start: number;
  evidence_end: number;
  uncertainty_levels: { low: Level; high: Level };
}

const MODEL_OUTPUT_SCHEMA = {
  type: "object",
  required: ["dimensions"],
  additionalProperties: false,
  properties: {
    dimensions: {
      type: "array",
      minItems: 1,
      maxItems: 8,
      items: {
        type: "object",
        required: ["dimension_id", "level", "evidence_start", "evidence_end", "uncertainty_levels"],
        additionalProperties: false,
        properties: {
          dimension_id: { type: "string", pattern: "^[a-z0-9_\\-]{1,48}$" },
          level: { enum: [...LEVELS] },
          evidence_start: { type: "integer" },
          evidence_end: { type: "integer" },
          uncertainty_levels: {
            type: "object",
            required: ["low", "high"],
            additionalProperties: false,
            properties: { low: { enum: [...LEVELS] }, high: { enum: [...LEVELS] } },
          },
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

/** 解析模型输出。返回 null 表示不合格（非 JSON、不合 schema、过长、dimension_id 重复）：四维全部不可判。 */
export function parseModelOutput(output: string): ModelDimension[] | null {
  if (typeof output !== "string" || output.length > MAX_MODEL_OUTPUT_CHARS) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(stripFence(output));
  } catch {
    return null;
  }
  if (!validateModelOutput(raw)) return null;
  const dims = (raw as { dimensions: ModelDimension[] }).dimensions;
  if (new Set(dims.map((d) => d.dimension_id)).size !== dims.length) return null;
  return dims;
}

/** 一维为什么不可判（只作适配器内部与测试用，不进响应） */
export type UndecidedReason = "model_output_invalid" | "dimension_missing" | "evidence_out_of_range" | "uncertainty_inconsistent";

export type DimensionOutcome =
  | {
      dimension_id: string;
      decided: true;
      level: Level;
      score: number;
      max: number;
      /** 不确定度档位换算成的分值区间 */
      uncertainty: { low: number; high: number };
      /** [start, end)，按码点计 */
      span: [number, number];
    }
  | { dimension_id: string; decided: false; max: number; reason: UndecidedReason };

export interface EssayPostProcessed {
  dimensions: DimensionOutcome[];
  /** 模型定档各维的分数之和；有任何一维不可判时为 null（总分由调用方再加上确定性维） */
  modelTotal: number | null;
  /** 分值区间之和；有任何一维不可判时为 null */
  modelUncertainty: { low: number; high: number } | null;
  /** 一类卷：各维都判定且都是最高档 → 强制复核 */
  topBand: boolean;
}

const levelIndex = (l: Level) => LEVELS.indexOf(l);
/** 档位 → 分值（量表里的表） */
export const levelScore = (d: RubricDimension, l: Level): number => d.levels.find((x) => x.level === l)!.score;

/**
 * 逐维后处理。维度顺序以量表为准；模型多给的维度（包括不需要它判的卷面维）忽略，少给的该维不可判。
 * text 是作文原文；位置按码点计（与 Prompt 声明的计数口径一致）。
 */
export function postProcessEssay(dimensions: readonly RubricDimension[], model: ModelDimension[] | null, text: string): EssayPostProcessed {
  const length = Array.from(text).length;
  const byId = new Map((model ?? []).map((d) => [d.dimension_id, d]));
  const out: DimensionOutcome[] = dimensions.map((def): DimensionOutcome => {
    const undecided = (reason: UndecidedReason): DimensionOutcome => ({ dimension_id: def.dimension_id, decided: false, max: def.max_score, reason });
    if (model === null) return undecided("model_output_invalid");
    const m = byId.get(def.dimension_id);
    if (!m) return undecided("dimension_missing");
    const { evidence_start: s, evidence_end: e } = m;
    if (!(Number.isInteger(s) && Number.isInteger(e) && s >= 0 && e > s && e <= length)) return undecided("evidence_out_of_range");
    const lo = m.uncertainty_levels.low;
    const hi = m.uncertainty_levels.high;
    if (!(levelIndex(lo) <= levelIndex(m.level) && levelIndex(m.level) <= levelIndex(hi))) return undecided("uncertainty_inconsistent");
    return {
      dimension_id: def.dimension_id,
      decided: true,
      level: m.level,
      score: levelScore(def, m.level),
      max: def.max_score,
      uncertainty: { low: levelScore(def, lo), high: levelScore(def, hi) },
      span: [s, e],
    };
  });
  const decided = out.filter((d): d is Extract<DimensionOutcome, { decided: true }> => d.decided);
  const all = decided.length === out.length;
  return {
    dimensions: out,
    modelTotal: all ? decided.reduce((sum, d) => sum + d.score, 0) : null,
    modelUncertainty: all
      ? { low: decided.reduce((sum, d) => sum + d.uncertainty.low, 0), high: decided.reduce((sum, d) => sum + d.uncertainty.high, 0) }
      : null,
    topBand: all && out.length > 0 && decided.every((d) => d.level === LEVELS[LEVELS.length - 1]),
  };
}

/** 作文字数：去掉空白后的字符（码点）数；标点计入（按格子计数的口径） */
export function countEssayChars(text: string): number {
  return Array.from(text.replace(/\s/g, "")).length;
}

/**
 * 字数规范（确定性规则，rule_ref 见内容包）：达到最低字数得满分；不足时每少 chars_per_deduction 字扣 1 分（不足一段按一段计），
 * 最多扣 max_deduction 分。
 */
export function conventionScore(rule: ConventionRule, minChars: number, text: string): { score: number; chars: number; deduction: number } {
  const chars = countEssayChars(text);
  const deficit = minChars - chars;
  const deduction = deficit <= 0 ? 0 : Math.min(rule.max_deduction, Math.ceil(deficit / rule.chars_per_deduction));
  return { score: rule.max_score - deduction, chars, deduction };
}
