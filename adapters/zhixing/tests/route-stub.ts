// 学习服务内部模型路由接口的本机 HTTP 桩（只用于测试与一致性测试；不是模型、不连任何供应商）。
// 接口约定见 src/route-client.ts。桩按约定校验请求（令牌、任务、数据级别、Prompt 版本、幂等键形状），
// 默认用“关键词桩判定器”返回结构化 JSON：作答里出现核心术语 → full；出现可接受或口语表述 → partial；都没有 → miss。
// 作答里的合成标记可以触发各种异常输出（见 MARKERS），用来覆盖“JSON 不合格”“证据越界”等情况。
// 全部数据是合成的（CLAUDE.md 规则 8）。
import { randomBytes } from "node:crypto";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import type { LoadedPack, PackItem } from "../src/content.ts";

export const STUB_MODEL_VERSION = "model:synthetic-stub/fake-grader@2026-09-01";

export interface StubRequest {
  task: string;
  data_class: string;
  subject: string;
  prompt_template: string;
  prompt_version: string;
  messages: { role: string; content: string }[];
  usage_id: string;
  idempotency_key: string;
  deadline_ms: number;
}

export interface StubReply {
  status: number;
  body: unknown;
  /** 在回复前等待的毫秒数（模拟超时） */
  delayMs?: number;
}

export type StubHandler = (req: StubRequest) => StubReply | Promise<StubReply>;

export interface RouteStub {
  url: string;
  token: string;
  /** 收到的请求（已通过鉴权与形状校验的）；测试用来断言适配器发了什么 */
  calls: StubRequest[];
  /** 收到的全部请求头里的 authorization（含鉴权失败的） */
  authHeaders: (string | undefined)[];
  setHandler(h: StubHandler): void;
  close(): Promise<void>;
}

const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function shapeOk(b: StubRequest): boolean {
  return (
    b &&
    b.task === "T2" &&
    (b.data_class === "L2" || b.data_class === "L3") &&
    b.subject === "zhixing" &&
    /^[a-z0-9][a-z0-9\-]{0,47}$/.test(b.prompt_template) &&
    /^v?\d+(\.\d+){0,3}$/.test(b.prompt_version) &&
    Array.isArray(b.messages) &&
    b.messages.length > 0 &&
    b.messages.every((m) => ["system", "user", "assistant"].includes(m.role) && typeof m.content === "string") &&
    UUID.test(b.usage_id) &&
    UUID4.test(b.idempotency_key) &&
    Number.isInteger(b.deadline_ms) &&
    b.deadline_ms > 0 &&
    b.deadline_ms <= 600_000
  );
}

export const ok = (req: StubRequest, output: string, extra: Record<string, unknown> = {}): StubReply => ({
  status: 200,
  body: { output, model_version: STUB_MODEL_VERSION, source_version: `prompt:${req.prompt_template}@${req.prompt_version}`, model_mismatch: false, ...extra },
});

export async function startRouteStub(handler: StubHandler, token = randomBytes(24).toString("hex")): Promise<RouteStub> {
  let current = handler;
  const calls: StubRequest[] = [];
  const authHeaders: (string | undefined)[] = [];
  const server: Server = createServer(async (req, res) => {
    const send = (status: number, body: unknown) => {
      if (res.headersSent || res.destroyed) return;
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };
    try {
      authHeaders.push(req.headers.authorization);
      const chunks: Buffer[] = [];
      for await (const c of req) chunks.push(c as Buffer);
      if (req.method !== "POST" || req.url !== "/internal/v1/model/route") return send(404, { code: "NOT_FOUND" });
      if (req.headers.authorization !== `Bearer ${token}`) return send(401, { code: "UNAUTHORIZED" });
      let body: StubRequest;
      try {
        body = JSON.parse(Buffer.concat(chunks).toString("utf8")) as StubRequest;
      } catch {
        return send(400, { code: "ROUTE_REJECTED" });
      }
      if (!shapeOk(body)) return send(400, { code: "ROUTE_REJECTED" });
      calls.push(body);
      const r = await current(body);
      if (r.delayMs) await new Promise((ok) => setTimeout(ok, r.delayMs));
      send(r.status, r.body);
    } catch {
      send(500, { code: "STUB_ERROR" });
    }
  });
  await new Promise<void>((ok) => server.listen(0, "127.0.0.1", ok));
  const port = (server.address() as AddressInfo).port;
  return {
    url: `http://127.0.0.1:${port}`,
    token,
    calls,
    authHeaders,
    setHandler: (h) => {
      current = h;
    },
    close: () =>
      new Promise<void>((ok) => {
        server.closeAllConnections();
        server.close(() => ok());
      }),
  };
}

// ---------- 从适配器发来的 messages 里取回题目与作答（桩判定器用） ----------
export function extractAnswer(req: StubRequest): { itemId: string; answer: string } {
  const user = req.messages.filter((m) => m.role === "user").at(-1)!.content;
  const itemId = /题目编号：(\S+)/.exec(user)![1]!;
  const marker = "作答原文（JSON 字符串）：\n";
  const answer = JSON.parse(user.slice(user.lastIndexOf(marker) + marker.length)) as string;
  return { itemId, answer };
}

/** 按码点计的位置（与适配器、Prompt 的口径一致） */
export function cpIndexOf(text: string, needle: string): number {
  const i = text.indexOf(needle);
  return i < 0 ? -1 : Array.from(text.slice(0, i)).length;
}

export interface StubPoint {
  point_id: string;
  judgment: string;
  score_awarded: number;
  evidence_start: number | null;
  evidence_end: number | null;
  attribution: string;
}

/** 关键词桩判定：只为测试提供可预期的“模型输出”，不是判分算法 */
export function keywordPoints(item: PackItem, answer: string): StubPoint[] {
  return item.points.map((p) => {
    const find = (words: string[]) => {
      for (const w of words) {
        const at = cpIndexOf(answer, w);
        if (at >= 0) return [at, at + Array.from(w).length] as const;
      }
      return null;
    };
    const core = find(p.keyword_family.core);
    if (core) return { point_id: p.point_id, judgment: "full", score_awarded: p.score, evidence_start: core[0], evidence_end: core[1], attribution: "none" };
    const near = find([...p.keyword_family.accepted, ...p.keyword_family.colloquial]);
    if (near) return { point_id: p.point_id, judgment: "partial", score_awarded: p.score / 2, evidence_start: near[0], evidence_end: near[1], attribution: "guifan_cuo" };
    return { point_id: p.point_id, judgment: "miss", score_awarded: 0, evidence_start: null, evidence_end: null, attribution: "dingwei_cuo" };
  });
}

/** 作答里的合成标记 → 桩的异常输出 */
export const MARKERS = {
  notJson: "〔桩:非JSON〕",
  outOfRange: "〔桩:越界〕",
  missingPoint: "〔桩:缺点〕",
  conflict: "〔桩:矛盾〕",
  fenced: "〔桩:代码块〕",
  extraField: "〔桩:多字段〕",
  unavailable: "〔桩:503〕",
} as const;

export function keywordJudge(pack: LoadedPack): StubHandler {
  return (req) => {
    const { itemId, answer } = extractAnswer(req);
    const item = pack.itemById.get(itemId)!;
    if (answer.includes(MARKERS.unavailable)) return { status: 503, body: { code: "ROUTE_UNAVAILABLE" } };
    if (answer.includes(MARKERS.notJson)) return ok(req, "判定如下：p1 完整命中。");
    let points: (StubPoint & { comment?: string })[] = keywordPoints(item, answer);
    if (answer.includes(MARKERS.outOfRange)) points = points.map((p) => ({ ...p, judgment: "full", score_awarded: 99, attribution: "none", evidence_start: 0, evidence_end: Array.from(answer).length + 5 }));
    if (answer.includes(MARKERS.missingPoint)) points = points.slice(0, -1);
    if (answer.includes(MARKERS.conflict)) points = points.map((p, i) => (i === 0 ? { ...p, judgment: "full", score_awarded: 0, attribution: "none", evidence_start: null, evidence_end: null } : p));
    if (answer.includes(MARKERS.extraField)) points = points.map((p) => ({ ...p, comment: "自由文本" }));
    const json = JSON.stringify({ points });
    return ok(req, answer.includes(MARKERS.fenced) ? "```json\n" + json + "\n```" : json);
  };
}
