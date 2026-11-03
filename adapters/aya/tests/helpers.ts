// 测试辅助。全部是合成数据（CLAUDE.md 规则 8）：学生 ID、请求 ID 是固定的示例 UUID；题目来自 legacy 内容包，不含学生数据。
import { createAdapter, type LogRecord } from "../src/adapter.ts";
import { gradeAnswer } from "../src/core/grading.ts";
import { indexPack, loadPack, type ContentPack, type LoadedPack, type PackItem } from "../src/content.ts";
import { ADMISSION_DECISIONS_FORMAT, applyAdmissionDecisions, DEFAULT_SOURCE, parseAdmissionDecisions, type AdmissionDecision } from "../src/pack/admission.ts";
import { loadContract } from "../src/contract.ts";
import { loadTaxonomy } from "../src/taxonomy.ts";

// 这组用例按 0.9.0 写（context() 声明 0.9.0）：显式钉住 0.9.0，作为迁移期旧版的回归。
// v1.0 签字后缺省版本是 1.0.0（contract.test.ts 断言），1.0.0 用例用下面的 contractV1 / contextV1
export const contract = loadContract({ defaultVersion: "0.9.0" });
export const taxonomy = loadTaxonomy();
export const realPack = loadPack();

export const STUDENT = "c1000000-0000-4000-8000-0000000000c1";
export const RUBRIC = "aya-symbolic-ts@0.1.0";

// 测试即演练环境：打开候选版开关（v1.0 已签字不需要它，只为 1.1.0 present_item 用例）；关着时的行为由 contract-v1.test.ts 单独测
export function makeAdapter(content: LoadedPack = realPack, logs?: LogRecord[], allowCandidate = true) {
  return createAdapter({ content, taxonomy, contract, allowCandidate, ...(logs ? { log: (r) => logs.push(r) } : {}) });
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

/** 默认契约版本为 1.0.0 的适配器（冲刺 4-D 的四个方法只按 v1.0 实现；能力声明报 1.0.0） */
export const contractV1 = loadContract({ defaultVersion: "1.0.0" });
export function makeAdapterV1(content: LoadedPack = realPack, logs?: LogRecord[]) {
  return createAdapter({ content, taxonomy, contract: contractV1, allowCandidate: true, ...(logs ? { log: (r) => logs.push(r) } : {}) });
}

let seqV1 = 0;
/** v1.0 请求上下文：幂等键是小写 UUIDv4；消耗类方法（teach）带 usage_id */
export function contextV1(overrides: Record<string, unknown> = {}) {
  seqV1++;
  const n = String(seqV1).padStart(12, "0");
  return context({ contract_version: "1.0.0", idempotency_key: `4d000000-0000-4000-8000-${n}`, ...overrides });
}

let seqV11 = 0;
/** v1.1 的 RequestContext（六个已有方法用）：同 v1.0，契约版本 1.1.0，不带 usage_id（消耗类方法自己加） */
export function contextV11(overrides: Record<string, unknown> = {}) {
  seqV11++;
  const n = String(seqV11).padStart(12, "0");
  const { usage_id: _drop, ...ctx } = context({ contract_version: "1.1.0", idempotency_key: `4e000000-0000-4000-8000-${n}` });
  return { ...ctx, ...overrides };
}

/** v1.1 PresentItemContext（PLT-12 present_item）：题面与学生无关——不带 student_ref、usage_id，幂等键可选（默认不带），L1 */
export function presentContext(overrides: Record<string, unknown> = {}) {
  seqV11++;
  const n = String(seqV11).padStart(12, "0");
  return {
    request_id: `00000000-0000-4000-8000-7${n.slice(1)}`,
    contract_version: "1.1.0",
    subject: "aya",
    data_class: "L1",
    content_version: { ...realPack.pack.curriculum },
    ...overrides,
  };
}

/** 合成审核记录编号（UUIDv4 格式） */
export const synReviewId = (n: number) => `4a000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

/** 合成准入决定（格式 aya-admission-decisions/1）：来源已核的题按审核等级定为 admitted（≥ Q2）或 pending_review */
export function syntheticDecisionFile(items: Pick<PackItem, "id" | "reviewStatus">[], overrides: Partial<AdmissionDecision>[] = []) {
  return {
    format: ADMISSION_DECISIONS_FORMAT,
    export_ref: "SYN-EXPORT-0001",
    exported_at: "2026-10-01T00:00:00Z",
    curriculum: { ...realPack.pack.curriculum },
    decisions: items.map((it, i): AdmissionDecision => ({
      item_id: it.id,
      admission: ["Q2", "Q3", "Q4", "Q5"].includes(it.reviewStatus) ? "admitted" : "pending_review",
      source_ref: `SYN-SRC-${String(i).padStart(4, "0")}`,
      source_type: "self_authored",
      license_status: "cleared",
      review_level: it.reviewStatus,
      review_ids: [synReviewId(i + 1)],
      ...overrides[i],
    })),
  };
}

/** 合成题目，用于内容包里没有的边界情况。sourceVerified = true 时经准入决定文件重建（与构建脚本同一条路径） */
export function syntheticPack(
  items: Partial<PackItem>[],
  opts: { sourceVerified?: boolean; traps?: ContentPack["traps"]; skillLinks?: ContentPack["skill_links"]; decisionOverrides?: Partial<AdmissionDecision>[] } = {},
): LoadedPack {
  const base = realPack.pack;
  const full = items.map((it, i): PackItem => ({
    id: `SYN-${i}`,
    format: "fill",
    answer: [],
    mainNodes: [base.nodes[0]!.id],
    reviewStatus: "Q2",
    usage: "D",
    estMinutes: 2,
    hintPath: ["（合成）0 级提示", "（合成）1 级提示", "（合成）2 级提示", "（合成）3 级提示", "（合成）4 级提示", "（合成）5 级提示"],
    ...it,
    admission: "source_unverified",
    source: { ...DEFAULT_SOURCE },
  }));
  const pack: ContentPack = { ...base, items: full, traps: opts.traps ?? base.traps, skill_links: opts.skillLinks ?? base.skill_links };
  const decisions = opts.sourceVerified
    ? parseAdmissionDecisions(syntheticDecisionFile(full, opts.decisionOverrides), pack.curriculum, new Set(full.map((i) => i.id)))
    : null;
  return indexPack(applyAdmissionDecisions(pack, decisions));
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
