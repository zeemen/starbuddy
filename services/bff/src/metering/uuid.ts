// usage_id = uuidv5(计量命名空间, idempotency_key)，与迁移 0009 的 platform.meter_usage_id() 一致。
// BFF 在预留之前就能算出 usage_id，并按契约放进发给学习服务的 context.usage_id。
import { createHash } from 'node:crypto';

export const METERING_NAMESPACE = '3b1e4c1a-6f0d-4d3e-9a52-7c8e2f1b5a40';
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export function isUuidV4(key: string): boolean {
  return UUID_V4.test(key);
}

export function uuidv5(namespace: string, name: string): string {
  const ns = Buffer.from(namespace.replace(/-/g, ''), 'hex');
  if (ns.length !== 16) throw new TypeError('uuidv5: invalid namespace');
  const hash = createHash('sha1').update(ns).update(name, 'utf8').digest().subarray(0, 16);
  hash[6] = ((hash[6] as number) & 0x0f) | 0x50;
  hash[8] = ((hash[8] as number) & 0x3f) | 0x80;
  const hex = hash.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function usageIdFor(idempotencyKey: string): string {
  return uuidv5(METERING_NAMESPACE, idempotencyKey);
}
