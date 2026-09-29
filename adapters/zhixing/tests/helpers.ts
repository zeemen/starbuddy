// 测试辅助。全部是合成数据（CLAUDE.md 规则 8）：学生 ID、请求 ID 是固定的示例 UUID；题目是内容包里的合成题；
// 作答是为测试编写的虚构文字。模型路由用本机 HTTP 桩（route-stub.ts），不连任何供应商。
import { createAdapter, type LogRecord } from "../src/adapter.ts";
import { indexPack, loadPack, type ContentPack, type LoadedPack, type PackItem } from "../src/content.ts";
import { loadContract } from "../src/contract.ts";
import { parseRouteConfig, type RouteConfig } from "../src/route-client.ts";
import { loadTaxonomy } from "../src/taxonomy.ts";
import { keywordJudge, startRouteStub, type RouteStub, type StubHandler } from "./route-stub.ts";

export const contract = loadContract();
export const taxonomy = loadTaxonomy();
export const realPack = loadPack();

export const STUDENT = "c1000000-0000-4000-8000-0000000000c2";
export const RUBRIC = "zhixing-synthetic@0.1.0";
export const TIME = "2026-10-08T19:30:00+08:00";

/** 合成作答：都是为测试编写的虚构文字 */
export const ANSWERS = {
  /** synthetic-m3-01 三个采分点都命中核心术语 → 满分 */
  full01: "居民通过议事会参与民主决策，这是基层群众自治的体现，也能增强规则意识。",
  /** synthetic-m3-01：p1 核心、p2 可接受表述、p3 什么都没有 → 部分得分 */
  partial01: "居民参与民主决策，体现了居民自治。",
  /** synthetic-m3-01：什么都没命中 → 0 分 */
  miss01: "楼道里的书很多，大家都喜欢看书。",
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

/** 起一个路由桩与一个适配器实例。默认用关键词桩判定器；测试可以换成自定义的桩回复 */
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
  const stub = await startRouteStub(opts.handler ?? keywordJudge(content));
  const logs: LogRecord[] = [];
  const adapter = createAdapter({
    content,
    taxonomy,
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
    subject: "zhixing",
    student_ref: STUDENT,
    data_class: "L2",
    usage_id: `00000000-0000-4000-9000-${n}`,
    idempotency_key: `synthetic-zhixing-key-${n}`,
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

export function gradeBody(item: string, text: string, extra: Record<string, unknown> = {}) {
  return {
    context: context(),
    attempt: { item_ref: `zhixing:${item}`, occurred_at: TIME, response: { text } },
    rubric_version: RUBRIC,
    ...extra,
  };
}

export function gradeBodyV1(item: string, text: string, extra: Record<string, unknown> = {}) {
  const b = gradeBody(item, text, extra) as Json;
  b.context = contextV1();
  b.attempt.attempt_id = attemptId(1);
  return b;
}

/** 单独的合成“已准入”内容包：只用于验证变式的选题逻辑（内容包本身全部是 pending_review） */
export function admittedPack(admitted: string[]): LoadedPack {
  const pack = structuredClone(realPack.pack) as ContentPack;
  for (const it of pack.items) if (admitted.includes(it.id)) it.admission = "admitted";
  return indexPack(pack);
}

export function itemOf(id: string): PackItem {
  return realPack.itemById.get(id)!;
}

/** 深度遍历 JSON，返回全部 [路径, 值] */
export function leaves(v: unknown, path = ""): [string, unknown][] {
  if (Array.isArray(v)) return v.flatMap((x, i) => leaves(x, `${path}/${i}`));
  if (v && typeof v === "object") return Object.entries(v).flatMap(([k, x]) => leaves(x, `${path}/${k}`));
  return [[path, v]];
}

/** 测试里直接读响应字段用的宽松类型 */
export type Json = any;
