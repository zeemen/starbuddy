// HTTP 层（node:http，待人确认 #1 默认方案）。路径前缀 /plugin/v0（契约 servers）。
// 平台到适配器用服务间令牌（契约 platformServiceToken）；学生与家长的身份不经过适配器。
import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server } from "node:http";
import type { Adapter } from "./adapter.ts";

export const PATH_PREFIX = "/plugin/v0/";
const MAX_BODY_BYTES = 1024 * 1024;

/** 两边先做 SHA-256 再定长比较，不泄露令牌长度 */
function bearerMatches(header: string | undefined, expected: string): boolean {
  const m = /^Bearer (.+)$/.exec(header ?? "");
  if (!m) return false;
  const a = createHash("sha256").update(m[1]!).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

/** 读请求体。超过上限返回 null（读完剩余数据后再回 413，客户端才能收到响应）；连接中断时抛出 */
async function readBody(req: IncomingMessage): Promise<Buffer | null> {
  const chunks: Buffer[] = [];
  let size = 0;
  let tooLarge = false;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) tooLarge = true;
    if (!tooLarge) chunks.push(chunk as Buffer);
    if (size > 4 * MAX_BODY_BYTES) break; // 明显滥用：不再读，直接断开
  }
  return tooLarge ? null : Buffer.concat(chunks);
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
  const server = createServer(async (req, res) => {
    const send = (status: number, body: unknown) => {
      if (res.headersSent || res.destroyed) return;
      res.writeHead(status, { "content-type": status >= 400 ? "application/problem+json" : "application/json" });
      res.end(JSON.stringify(body));
    };
    // 任何异常都不能冒泡成 unhandled rejection（Node 默认会让进程退出）
    try {
      let url: URL;
      try {
        url = new URL(req.url ?? "/", "http://adapter.invalid");
      } catch {
        return send(400, httpProblem(400, "invalid request target"));
      }
      if (!url.pathname.startsWith(PATH_PREFIX)) return send(404, httpProblem(404, "unknown route"));
      if (!bearerMatches(req.headers.authorization, serviceCredential)) {
        return send(401, httpProblem(401, "missing or invalid service token"));
      }
      let body: unknown;
      if (req.method === "POST") {
        const raw = await readBody(req);
        if (raw === null) {
          res.setHeader("connection", "close");
          return send(413, httpProblem(413, "request body too large"));
        }
        try {
          body = JSON.parse(raw.toString("utf8"));
        } catch {
          return send(400, httpProblem(400, "request body is not valid JSON"));
        }
      }
      const r = adapter.handle(req.method ?? "GET", url.pathname.slice(PATH_PREFIX.length), body);
      send(r.status, r.body);
    } catch {
      // 多半是客户端中途断开（ECONNRESET / aborted）：连接已不可用，直接销毁
      if (!res.headersSent && !req.destroyed) send(500, { ...httpProblem(500, "internal error"), code: "INTERNAL" });
      else res.destroy();
    }
  });
  server.requestTimeout = 30_000;
  server.headersTimeout = 10_000;
  return server;
}
