// HTTP 层（node:http，待人确认 #1 默认方案）。路径前缀 /plugin/v0（契约 servers）。
// 平台到适配器用服务间令牌（契约 platformServiceToken）；学生与家长的身份不经过适配器。
import { timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server } from "node:http";
import type { Adapter } from "./adapter.ts";

export const PATH_PREFIX = "/plugin/v0/";
const MAX_BODY_BYTES = 1024 * 1024;

function bearerMatches(header: string | undefined, expected: string): boolean {
  const m = /^Bearer (.+)$/.exec(header ?? "");
  if (!m) return false;
  const a = Buffer.from(m[1]!);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

async function readBody(req: IncomingMessage): Promise<Buffer | null> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) return null;
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks);
}

// 适配器之外的 HTTP 层错误（令牌、请求体）。契约 Problem.code 没有鉴权类编码，暂用 INVALID_REQUEST（lessons 待人确认）
const httpProblem = (status: number, title: string) => ({
  type: "/problems/invalid_request",
  title,
  status,
  code: "INVALID_REQUEST",
  retryable: false,
});

export function createAdapterServer(adapter: Adapter, serviceCredential: string): Server {
  if (serviceCredential.length < 32) throw new Error("服务间令牌至少 32 个字符");
  return createServer(async (req, res) => {
    const send = (status: number, body: unknown) => {
      res.writeHead(status, { "content-type": status >= 400 ? "application/problem+json" : "application/json" });
      res.end(JSON.stringify(body));
    };
    const url = new URL(req.url ?? "/", "http://adapter.invalid");
    if (!url.pathname.startsWith(PATH_PREFIX)) return send(404, httpProblem(404, "unknown route"));
    if (!bearerMatches(req.headers.authorization, serviceCredential)) {
      return send(401, httpProblem(401, "missing or invalid service token"));
    }
    let body: unknown;
    if (req.method === "POST") {
      const raw = await readBody(req);
      if (raw === null) return send(413, httpProblem(413, "request body too large"));
      try {
        body = JSON.parse(raw.toString("utf8"));
      } catch {
        return send(400, httpProblem(400, "request body is not valid JSON"));
      }
    }
    const r = adapter.handle(req.method ?? "GET", url.pathname.slice(PATH_PREFIX.length), body);
    send(r.status, r.body);
  });
}
