// 学习服务内部模型路由接口的客户端（spec 设计要点 5；ADR 04 第 1 条）。
//
// 适配器不持有任何模型供应商的地址或 Key：只配学习服务内部接口的地址与服务间令牌，
// 由学习服务把请求交给平台模型路由（ModelRouter.route），返回模型文本与真实的 model_version。
//   POST {ZHIXING_ROUTE_URL}/internal/v1/model/route
//   Authorization: Bearer {ZHIXING_ROUTE_TOKEN}
//   200 { output, model_version, source_version, model_mismatch }
//   400 { code: ROUTE_REJECTED }   503 { code: ROUTE_UNAVAILABLE }   401
// 上游响应原文（包括模型文本）不写日志、不进错误详情（契约 Problem.detail；zhixing inventory §1.6）。
import type { ChatMessage } from "./core/m3-prompt.ts";

export const ROUTE_PATH = "/internal/v1/model/route";
export const MIN_ROUTE_TOKEN_LENGTH = 32;
/** 契约：grade 超时 10 秒。路由拿 9 秒总时限，客户端再多等一点网络往返 */
export const DEFAULT_DEADLINE_MS = 9_000;
export const DEFAULT_CLIENT_TIMEOUT_MS = 9_500;
const MAX_RESPONSE_BYTES = 256 * 1024;

export interface RouteConfig {
  endpoint: URL;
  token: string;
  deadlineMs: number;
  clientTimeoutMs: number;
}

export type RouteConfigResult = { ok: true; config: RouteConfig } | { ok: false; reason: "missing" | "invalid_url" | "short_token" };

/** 与学习服务对适配器地址的要求一致（services/learning/learning/config.py）：https，或本机回环的 http */
const LOOPBACK_HTTP = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/;

export function parseRouteConfig(
  input: { url?: string | undefined; token?: string | undefined },
  opts: { deadlineMs?: number; clientTimeoutMs?: number } = {},
): RouteConfigResult {
  const { url, token } = input;
  if (!url || !token) return { ok: false, reason: "missing" };
  if (!(url.startsWith("https://") || LOOPBACK_HTTP.test(url))) return { ok: false, reason: "invalid_url" };
  let base: URL;
  try {
    base = new URL(url);
  } catch {
    return { ok: false, reason: "invalid_url" };
  }
  // 地址里不得带凭据、查询串或片段（令牌只走 Authorization 头）
  if (base.username || base.password || base.search || base.hash) return { ok: false, reason: "invalid_url" };
  if (token.length < MIN_ROUTE_TOKEN_LENGTH || /\s/.test(token)) return { ok: false, reason: "short_token" };
  const endpoint = new URL(`${base.pathname.replace(/\/+$/, "")}${ROUTE_PATH}`, base);
  return {
    ok: true,
    config: {
      endpoint,
      token,
      deadlineMs: opts.deadlineMs ?? DEFAULT_DEADLINE_MS,
      clientTimeoutMs: opts.clientTimeoutMs ?? DEFAULT_CLIENT_TIMEOUT_MS,
    },
  };
}

export interface RouteRequest {
  task: "T2";
  data_class: "L2" | "L3";
  subject: "zhixing";
  prompt_template: string;
  prompt_version: string;
  messages: ChatMessage[];
  usage_id: string;
  idempotency_key: string;
  deadline_ms: number;
}

export interface RouteReply {
  output: string;
  model_version: string;
  source_version: string;
  model_mismatch: boolean;
}

export type RouteFailure =
  | "timeout" // 超时：结果未知
  | "unavailable" // 503 或网络错误：可以用同一个键重试
  | "rejected" // 400：请求被路由拒绝（模板、数据级别等配置问题）
  | "unauthorized" // 401 / 403：令牌不对（部署配置问题）
  | "bad_response"; // 响应不是约定的结构

export class RouteError extends Error {
  failure: RouteFailure;
  constructor(failure: RouteFailure) {
    super(`model route ${failure}`);
    this.failure = failure;
  }
}

/** 契约 ModelVersion 的模型形态（路由回传的真实快照名；不接受 algo: 或 routed 之类） */
const MODEL_VERSION = /^model:[a-z0-9\-]+\/[A-Za-z0-9._\-]+@[A-Za-z0-9._\-]+$/;

export type FetchLike = (input: URL, init: RequestInit) => Promise<Response>;

async function readCapped(res: Response): Promise<string | null> {
  const buf = await res.arrayBuffer();
  if (buf.byteLength > MAX_RESPONSE_BYTES) return null;
  return new TextDecoder("utf-8", { fatal: false }).decode(buf);
}

export async function callRoute(config: RouteConfig, req: RouteRequest, fetchImpl: FetchLike = fetch): Promise<RouteReply> {
  let res: Response;
  try {
    res = await fetchImpl(config.endpoint, {
      method: "POST",
      headers: { authorization: `Bearer ${config.token}`, "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(req),
      // 不跟随重定向：令牌不能被转发到别的地址
      redirect: "error",
      signal: AbortSignal.timeout(config.clientTimeoutMs),
    });
  } catch (e) {
    const name = e instanceof Error ? e.name : "";
    throw new RouteError(name === "TimeoutError" || name === "AbortError" ? "timeout" : "unavailable");
  }
  let text: string | null;
  try {
    text = await readCapped(res);
  } catch (e) {
    const name = e instanceof Error ? e.name : "";
    throw new RouteError(name === "TimeoutError" || name === "AbortError" ? "timeout" : "unavailable");
  }
  if (res.status === 401 || res.status === 403) throw new RouteError("unauthorized");
  if (res.status === 400) throw new RouteError("rejected");
  if (res.status === 503 || res.status === 502 || res.status === 504) throw new RouteError("unavailable");
  if (res.status !== 200 || text === null) throw new RouteError("bad_response");
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new RouteError("bad_response");
  }
  const b = body as Partial<RouteReply> | null;
  if (
    !b ||
    typeof b !== "object" ||
    typeof b.output !== "string" ||
    typeof b.model_version !== "string" ||
    !MODEL_VERSION.test(b.model_version) ||
    b.source_version !== `prompt:${req.prompt_template}@${req.prompt_version}` ||
    typeof b.model_mismatch !== "boolean"
  ) {
    throw new RouteError("bad_response");
  }
  return { output: b.output, model_version: b.model_version, source_version: b.source_version, model_mismatch: b.model_mismatch };
}
