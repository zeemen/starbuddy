// 学习服务内部模型路由接口的本机 HTTP 桩（只用于测试与一致性测试；不是模型、不连任何供应商）。
// 来源：adapters/zhixing/tests/route-stub.ts（HTTP 桩与请求形状校验原样），判定器改为作文五维的“档位桩”。
//
// 桩按约定校验请求（令牌、任务 T4、学科 wenli、Prompt 版本、幂等键形状），默认用“档位桩判定器”返回结构化 JSON：
//   审题立意 L3（L2–L3）、内容材料 L2（L2–L3）、结构层次 L3（L3–L3）、语言表达 L2（L1–L2）；证据位置 [0, min(10, 字数))。
//   按合成量表：8 + 7 + 8 + 7 = 30 分；不确定度区间 25–33。
// 作文里的合成标记可以触发各种输出（见 MARKERS），用来覆盖“一类卷”“JSON 不合格”“证据越界”等情况。
// 全部数据是合成的（CLAUDE.md 规则 8）。
import { randomBytes } from "node:crypto";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

export const STUB_MODEL_VERSION = "model:synthetic-stub/fake-essay-grader@2026-09-01";

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
    b.task === "T4" &&
    (b.data_class === "L2" || b.data_class === "L3") &&
    b.subject === "wenli" &&
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

// ---------- 从适配器发来的 messages 里取回题目与作文（桩判定器用） ----------
export function extractEssay(req: StubRequest): { essayId: string; text: string; dimensionIds: string[] } {
  // Prompt 0.1.0：题目与量表在 system，作文单独在最后一条 user 消息
  const system = req.messages.filter((m) => m.role === "system").map((m) => m.content).join("\n");
  const user = req.messages.filter((m) => m.role === "user").at(-1)!.content;
  const essayId = /题目编号：(\S+)/.exec(system)![1]!;
  const dimensionIds = [...system.matchAll(/^- 维度 ([a-z0-9_\-]+)（/gm)].map((m) => m[1]!);
  const marker = "作文原文（JSON 字符串）：\n";
  const text = JSON.parse(user.slice(user.lastIndexOf(marker) + marker.length)) as string;
  return { essayId, text, dimensionIds };
}

export interface StubDimension {
  dimension_id: string;
  level: string;
  evidence_start: number;
  evidence_end: number;
  uncertainty_levels: { low: string; high: string };
}

/** 默认档位（测试与夹具据此推导期望） */
export const DEFAULT_LEVELS: Readonly<Record<string, [string, string, string]>> = {
  shenti_liyi: ["L3", "L2", "L3"],
  neirong_cailiao: ["L2", "L2", "L3"],
  jiegou_cengci: ["L3", "L3", "L3"],
  yuyan_biaoda: ["L2", "L1", "L2"],
};

/** 作文里的合成标记 → 桩的输出 */
export const MARKERS = {
  topBand: "〔桩:一类〕",
  lowBand: "〔桩:低档〕",
  notJson: "〔桩:非JSON〕",
  outOfRange: "〔桩:越界〕",
  missingDim: "〔桩:缺维〕",
  badInterval: "〔桩:区间矛盾〕",
  fenced: "〔桩:代码块〕",
  extraField: "〔桩:多字段〕",
  handwriting: "〔桩:卷面〕",
  unavailable: "〔桩:503〕",
} as const;

/** 档位桩判定：只为测试提供可预期的“模型输出”，不是判分算法 */
export function levelJudge(): StubHandler {
  return (req) => {
    const { text, dimensionIds } = extractEssay(req);
    if (text.includes(MARKERS.unavailable)) return { status: 503, body: { code: "ROUTE_UNAVAILABLE" } };
    if (text.includes(MARKERS.notJson)) return ok(req, "审题立意定为 L3。");
    const len = Array.from(text).length;
    const span = { evidence_start: 0, evidence_end: Math.min(10, len) };
    let dims: (StubDimension & { comment?: string })[] = dimensionIds.map((id) => {
      const [level, low, high] = text.includes(MARKERS.topBand)
        ? ["L4", "L4", "L4"]
        : text.includes(MARKERS.lowBand)
          ? ["L1", "L1", "L2"]
          : (DEFAULT_LEVELS[id] ?? ["L2", "L2", "L2"]);
      return { dimension_id: id, level, ...span, uncertainty_levels: { low, high } };
    });
    if (text.includes(MARKERS.outOfRange)) dims = dims.map((d, i) => (i === 0 ? { ...d, evidence_start: 0, evidence_end: len + 5 } : d));
    if (text.includes(MARKERS.missingDim)) dims = dims.slice(0, -1);
    if (text.includes(MARKERS.badInterval)) dims = dims.map((d, i) => (i === 0 ? { ...d, level: "L3", uncertainty_levels: { low: "L4", high: "L4" } } : d));
    if (text.includes(MARKERS.extraField)) dims = dims.map((d) => ({ ...d, comment: "自由文本" }));
    if (text.includes(MARKERS.handwriting)) dims = [...dims, { dimension_id: "juanmian_shuxie", level: "L4", ...span, uncertainty_levels: { low: "L4", high: "L4" } }];
    const json = JSON.stringify({ dimensions: dims });
    return ok(req, text.includes(MARKERS.fenced) ? "```json\n" + json + "\n```" : json);
  };
}
