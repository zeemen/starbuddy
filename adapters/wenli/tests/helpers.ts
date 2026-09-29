// 测试辅助。全部是合成数据（CLAUDE.md 规则 8）：学生 ID、请求 ID 是固定的示例 UUID；题目是内容包里的合成题；
// 作文与作答是为测试编写的虚构文字。模型路由用本机 HTTP 桩（route-stub.ts），不连任何供应商。
import { createAdapter, type LogRecord } from "../src/adapter.ts";
import { indexPack, loadPack, type ContentPack, type LoadedPack } from "../src/content.ts";
import { loadContract } from "../src/contract.ts";
import { parseRouteConfig, type RouteConfig } from "../src/route-client.ts";
import { levelJudge, startRouteStub, type RouteStub, type StubHandler } from "./route-stub.ts";

export const contract = loadContract();
export const realPack = loadPack();

export const STUDENT = "c1000000-0000-4000-8000-0000000000c3";
export const RUBRIC = "wenli-essay-5d@0.1.0";
export const TIME = "2026-10-10T20:00:00+08:00";

/** 合成段落（虚构），重复成不同长度的作文 */
const PARAGRAPH = "那天放学后，我在巷口的旧书摊前停下脚步，看见摊主正低着头，一页一页地修补一本旧字典。";
/** 生成至少 n 个非空白字符的合成作文 */
export function essayOf(n: number, suffix = ""): string {
  let s = "";
  while (Array.from(s.replace(/\s/g, "")).length < n) s += PARAGRAPH;
  return Array.from(s).slice(0, n).join("") + suffix;
}

export const ESSAYS = {
  /** 650 字：超过 synthetic-essay-01 的最低 600 字 → 字数规范满分 */
  long: essayOf(650),
  /** 520 字：比 600 少 80 字 → 扣 2 分（每 50 字 1 分，不足一段按一段） */
  short: essayOf(520),
  /** 570 字：少 30 字 → 扣 1 分 */
  slightlyShort: essayOf(570),
} as const;

export function routeConfig(stub: RouteStub, opts: { clientTimeoutMs?: number; deadlineMs?: number } = {}): RouteConfig {
  const r = parseRouteConfig({ url: stub.url, token: stub.token }, opts);
  if (!r.ok) throw new Error(`route config invalid: ${r.reason}`);
  return r.config;
}

export interface Harness {
  adapter: ReturnType<typeof createAdapter>;
  stub: RouteStub;
  logs: LogRecord[];
  close(): Promise<void>;
}

/** 起一个路由桩与一个适配器实例。默认用档位桩判定器；测试可以换成自定义的桩回复 */
export async function harness(
  opts: {
    handler?: StubHandler;
    content?: LoadedPack;
    allowCandidate?: boolean;
    clientTimeoutMs?: number;
    route?: RouteConfig | null;
    idempotencyCacheSize?: number;
  } = {},
): Promise<Harness> {
  const content = opts.content ?? realPack;
  const stub = await startRouteStub(opts.handler ?? levelJudge());
  const logs: LogRecord[] = [];
  const adapter = createAdapter({
    content,
    contract,
    route: opts.route === undefined ? routeConfig(stub, { ...(opts.clientTimeoutMs ? { clientTimeoutMs: opts.clientTimeoutMs } : {}) }) : opts.route,
    allowCandidate: opts.allowCandidate ?? true,
    log: (r) => logs.push(r),
    ...(opts.idempotencyCacheSize ? { idempotencyCacheSize: opts.idempotencyCacheSize } : {}),
  });
  return { adapter, stub, logs, close: () => stub.close() };
}

let seq = 0;
export function context(overrides: Record<string, unknown> = {}) {
  seq++;
  const n = String(seq).padStart(12, "0");
  return {
    request_id: `00000000-0000-4000-8000-${n}`,
    contract_version: "0.9.0",
    subject: "wenli",
    student_ref: STUDENT,
    data_class: "L2",
    usage_id: `00000000-0000-4000-9000-${n}`,
    idempotency_key: `synthetic-wenli-key-${n}`,
    content_version: { ...realPack.pack.curriculum },
    ...overrides,
  };
}

/** v1.0 的上下文：幂等键是小写 UUIDv4 */
export function contextV1(overrides: Record<string, unknown> = {}) {
  seq++;
  const n = String(seq).padStart(12, "0");
  return context({ contract_version: "1.0.0", idempotency_key: `5a000000-0000-4000-8000-${n}`, ...overrides });
}

export const attemptId = (i: number) => `a1000000-0000-4000-8000-${String(i).padStart(12, "0")}`;

export function gradeBody(essay: string, text: string, extra: Record<string, unknown> = {}) {
  return {
    context: context(),
    attempt: { item_ref: `wenli:${essay}`, occurred_at: TIME, response: { text } },
    rubric_version: RUBRIC,
    ...extra,
  };
}

export function gradeBodyV1(essay: string, text: string, extra: Record<string, unknown> = {}) {
  const b = gradeBody(essay, text, extra) as Json;
  b.context = contextV1();
  b.attempt.attempt_id = attemptId(1);
  return b;
}

/** 一道客观题作答（occurred_at 按序号错开一分钟） */
export function attempt(item: string, response: Record<string, unknown>, i = 0, extra: Record<string, unknown> = {}) {
  const t = new Date(Date.parse(TIME) + i * 60_000).toISOString();
  return { item_ref: `wenli:${item}`, occurred_at: t, response, ...extra };
}

export function diagnoseBody(attempts: unknown[], extra: Record<string, unknown> = {}) {
  return { context: context(), mode: "gap_test", attempts, ...extra };
}

export function planBody(mastery: unknown[], constraints?: Record<string, unknown>) {
  const c = context() as Json;
  delete c.usage_id;
  return { context: c, mastery, ...(constraints ? { constraints } : {}) };
}

/** 单独的合成“已准入”内容包：只用于验证 plan 的选题（内容包本身全部是 pending_review） */
export function admittedPack(admitted: string[]): LoadedPack {
  const pack = structuredClone(realPack.pack) as ContentPack;
  for (const it of pack.items) if (admitted.includes(it.id)) it.admission = "admitted";
  for (const e of pack.essays) if (admitted.includes(e.id)) e.admission = "admitted";
  return indexPack(pack);
}

/** 深度遍历 JSON，返回全部 [路径, 值] */
export function leaves(v: unknown, path = ""): [string, unknown][] {
  if (Array.isArray(v)) return v.flatMap((x, i) => leaves(x, `${path}/${i}`));
  if (v && typeof v === "object") return Object.entries(v).flatMap(([k, x]) => leaves(x, `${path}/${k}`));
  return [[path, v]];
}

/** 测试里直接读响应字段用的宽松类型 */
export type Json = any;
