// 幂等的证据 ID（ADR 05 第 2 条；契约 JobAccepted 说明）。来源：adapters/zhixing/src/ids.ts（其来源为 adapters/aya/src/ids.ts），命名空间改为文砺。
// - 请求体指纹不含 context.request_id：重试时 request_id 可能变化，但作答内容不变。
// - 作答证据：请求带 attempt_id 时按“作答本身”派生（attempt_id + 题目 + 时间 + 原文），同一道作答在 grade 与 diagnose
//   里是同一个 evidence_id（契约“证据只记一次，推断可以多次”）；不带 attempt_id 时按 key 与请求体指纹派生（同阿鸦）。
import { createHash } from "node:crypto";

const NAMESPACE = "starbuddy/wenli-adapter/v1";

export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function requestFingerprint(body: { context: Record<string, unknown> } & Record<string, unknown>): string {
  const { request_id: _ignored, ...context } = body.context;
  return createHash("sha256").update(canonical({ ...body, context })).digest("hex");
}

function digest(parts: string[]): Buffer {
  return createHash("sha256").update([NAMESPACE, ...parts].join("\0")).digest();
}

function format(h: Buffer, version: 4 | 8): string {
  h[6] = (h[6]! & 0x0f) | (version << 4);
  h[8] = (h[8]! & 0x3f) | 0x80;
  const x = h.subarray(0, 16).toString("hex");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20, 32)}`;
}

/** RFC 9562 UUIDv8（自定义派生）。label 区分同一请求里的多条证据，例如 `a0:response`。 */
export function deriveUuid(method: string, idempotencyKey: string, fingerprint: string, label: string): string {
  return format(digest([method, idempotencyKey, fingerprint, label]), 8);
}

/** 按内容派生的 UUIDv8（作答证据、采分点片段证据） */
export function contentUuid(...parts: string[]): string {
  return format(digest(["content", ...parts]), 8);
}

/**
 * 发给模型路由的幂等键：学习服务内部接口要求小写 UUIDv4 形状。由本请求的 key 与指纹确定性派生，
 * 同一请求重试时路由看到的是同一个键；不同请求体（409 之外）一定不同。
 */
export function routeIdempotencyKey(idempotencyKey: string, fingerprint: string): string {
  return format(digest(["route", idempotencyKey, fingerprint]), 4);
}
