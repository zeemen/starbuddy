// 幂等的证据 ID（ADR 05 第 2 条；契约 JobAccepted 说明）：同一个 idempotency_key、同一请求体重试时，
// 返回同一批 evidence_id。ID 由 key 与请求体指纹确定性派生，进程重启后仍然一致，不需要存状态。
// 请求体指纹不含 context.request_id：重试时 request_id 可能变化，但作答内容不变。
import { createHash } from "node:crypto";

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

/** RFC 9562 UUIDv8（自定义派生）。label 区分同一请求里的多条证据，例如 `a0:response`。 */
export function deriveUuid(method: string, idempotencyKey: string, fingerprint: string, label: string): string {
  const h = createHash("sha256").update(`starbuddy/aya-adapter/v1\0${method}\0${idempotencyKey}\0${fingerprint}\0${label}`).digest();
  h[6] = (h[6]! & 0x0f) | 0x80;
  h[8] = (h[8]! & 0x3f) | 0x80;
  const x = h.subarray(0, 16).toString("hex");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20, 32)}`;
}
