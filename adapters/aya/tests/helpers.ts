// 测试辅助。全部是合成数据（CLAUDE.md 规则 8）：学生 ID、请求 ID 是固定的示例 UUID；题目来自 legacy 内容包，不含学生数据。
import { createAdapter, type LogRecord } from "../src/adapter.ts";
import { gradeAnswer } from "../src/core/grading.ts";
import { indexPack, loadPack, type ContentPack, type LoadedPack, type PackItem } from "../src/content.ts";
import { loadContract } from "../src/contract.ts";
import { loadTaxonomy } from "../src/taxonomy.ts";

export const contract = loadContract();
export const taxonomy = loadTaxonomy();
export const realPack = loadPack();

export const STUDENT = "c1000000-0000-4000-8000-0000000000c1";
export const RUBRIC = "aya-symbolic-ts@0.1.0";

export function makeAdapter(content: LoadedPack = realPack, logs?: LogRecord[]) {
  return createAdapter({ content, taxonomy, contract, ...(logs ? { log: (r) => logs.push(r) } : {}) });
}

let seq = 0;
export function context(overrides: Record<string, unknown> = {}) {
  seq++;
  const n = String(seq).padStart(12, "0");
  return {
    request_id: `00000000-0000-4000-8000-${n}`,
    contract_version: "0.9.0",
    subject: "aya",
    student_ref: STUDENT,
    data_class: "L2",
    usage_id: `00000000-0000-4000-9000-${n}`,
    idempotency_key: `synthetic-test-key-${n}`,
    content_version: { ...realPack.pack.curriculum },
    ...overrides,
  };
}

export type Response = { text: string } | { choice: string[] };

/** legacy 内容问题：预标注的错误答案与标准答案等价（legacy 与本验证器都判对）。已记入 lessons，待教研修题 */
export const KNOWN_TRAP_PATTERN_CONFLICTS = ["Q-ALG-U02-D-001 2·x+x+3", "Q-DCE-U03-D-001 0.17人"];

/** 覆盖全部 721 道题的作答用例：标准答案（应判对）、预标注错误答案（应判错并命中陷阱）、一个无法解析的填空作答 */
export interface SweepCase {
  item: PackItem;
  response: Response;
  expect: "correct" | "incorrect" | "needs_review";
  traps?: string[];
}
export function sweepCases(): SweepCase[] {
  const out: SweepCase[] = [];
  for (const item of realPack.pack.items) {
    if (item.format === "fill") {
      for (const a of item.answer) out.push({ item, response: { text: a }, expect: "correct" });
      for (const w of item.wrongAnswerTraps ?? []) {
        for (const p of w.pattern) {
          // legacy 题库里有极少数“典型错误答案”其实与标准答案等价（见 KNOWN_TRAP_PATTERN_CONFLICTS），以 legacy 判定为准
          const legacyCorrect = gradeAnswer(item, [p]).correct;
          out.push({ item, response: { text: p }, expect: legacyCorrect ? "correct" : "incorrect", traps: legacyCorrect ? [] : w.traps });
        }
      }
      out.push({ item, response: { text: "2x+" }, expect: "needs_review" });
    } else {
      out.push({ item, response: { choice: [...item.answer] }, expect: "correct" });
      for (const o of item.options ?? []) {
        if (!item.answer.includes(o.key)) out.push({ item, response: { choice: [o.key] }, expect: "incorrect", traps: o.traps ?? [] });
      }
    }
  }
  return out;
}

export function gradeBody(item: string, response: Response, extra: Record<string, unknown> = {}) {
  return {
    context: context(),
    attempt: { item_ref: `aya:${item}`, occurred_at: "2026-10-08T19:30:00+08:00", response },
    rubric_version: RUBRIC,
    ...extra,
  };
}

/** 合成题目，用于内容包里没有的边界情况 */
export function syntheticPack(items: Partial<PackItem>[], opts: { sourceVerified?: boolean; traps?: ContentPack["traps"] } = {}): LoadedPack {
  const base = realPack.pack;
  const verified = opts.sourceVerified ?? false;
  const full = items.map((it, i): PackItem => {
    const reviewStatus = it.reviewStatus ?? "Q2";
    return {
      id: `SYN-${i}`,
      format: "fill",
      answer: [],
      mainNodes: [base.nodes[0]!.id],
      reviewStatus,
      ...it,
      admission: verified && ["Q2", "Q3", "Q4", "Q5"].includes(reviewStatus) ? "admitted" : verified ? "pending_review" : "source_unverified",
    };
  });
  return indexPack({ ...base, source_verified: verified, items: full, traps: opts.traps ?? base.traps });
}

/** 深度遍历 JSON，返回全部 [路径, 值] */
export function leaves(v: unknown, path = ""): [string, unknown][] {
  if (Array.isArray(v)) return v.flatMap((x, i) => leaves(x, `${path}/${i}`));
  if (v && typeof v === "object") return Object.entries(v).flatMap(([k, x]) => leaves(x, `${path}/${k}`));
  return [[path, v]];
}

export function keysOf(v: unknown): string[] {
  if (Array.isArray(v)) return v.flatMap(keysOf);
  if (v && typeof v === "object") return Object.entries(v).flatMap(([k, x]) => [k, ...keysOf(x)]);
  return [];
}

/** 测试里直接读响应字段用的宽松类型 */
export type Json = any;
