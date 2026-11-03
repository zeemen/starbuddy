// 指纹（KGE-08 范围 1）：规范文本 SHA-256（完全重复）；字符 n-gram 的 64 位 SimHash（BigInt 实现，确定性）；汉明距离；Jaccard。
// n-gram 的 64 位哈希：FNV-1a 64 后接 MurmurHash3 的 fmix64 终混（FNV 对很短的输入高位扩散不足，终混后各位才近似独立）。
import { createHash } from "node:crypto";

const MASK64 = (1n << 64n) - 1n;
const FNV_OFFSET = 0xcbf29ce484222325n;
const FNV_PRIME = 0x100000001b3n;

export const sha256 = (s: string): string => createHash("sha256").update(s, "utf8").digest("hex");

export function fnv1a64(s: string): bigint {
  let h = FNV_OFFSET;
  for (const b of Buffer.from(s, "utf8")) {
    h ^= BigInt(b);
    h = (h * FNV_PRIME) & MASK64;
  }
  return h;
}

export function fmix64(x: bigint): bigint {
  let k = x & MASK64;
  k ^= k >> 33n;
  k = (k * 0xff51afd7ed558ccdn) & MASK64;
  k ^= k >> 33n;
  k = (k * 0xc4ceb9fe1a85ec53n) & MASK64;
  k ^= k >> 33n;
  return k;
}

export const gramHash = (g: string): bigint => fmix64(fnv1a64(g));

/** 按码点切 n-gram；不足 n 个码点的非空文本整体算一个 gram；空文本没有 gram */
export function ngrams(s: string, n: number): string[] {
  const cp = Array.from(s);
  if (cp.length === 0) return [];
  if (cp.length < n) return [cp.join("")];
  const out: string[] = [];
  for (let i = 0; i + n <= cp.length; i++) out.push(cp.slice(i, i + n).join(""));
  return out;
}

/** 64 位 SimHash：每个 gram（按出现次数加权）对 64 位逐位投票，正票为 1 */
export function simhash64(grams: readonly string[]): bigint {
  const v = new Array<number>(64).fill(0);
  for (const g of grams) {
    const h = gramHash(g);
    const hi = Number(h >> 32n), lo = Number(h & 0xffffffffn);
    for (let bit = 0; bit < 32; bit++) {
      v[bit] += (lo >>> bit) & 1 ? 1 : -1;
      v[bit + 32] += (hi >>> bit) & 1 ? 1 : -1;
    }
  }
  let out = 0n;
  for (let bit = 0; bit < 64; bit++) if (v[bit]! > 0) out |= 1n << BigInt(bit);
  return out;
}

export function hamming(a: bigint, b: bigint): number {
  let x = (a ^ b) & MASK64;
  let c = 0;
  while (x !== 0n) {
    x &= x - 1n;
    c++;
  }
  return c;
}

/** 成对扫描用的快速版本：把 64 位拆成两个 32 位数（结果与 hamming 相同，单测核对） */
export interface Split64 { hi: number; lo: number }
export const split64 = (h: bigint): Split64 => ({ hi: Number(h >> 32n), lo: Number(h & 0xffffffffn) });
function popcount32(x: number): number {
  let v = x >>> 0;
  v -= (v >>> 1) & 0x55555555;
  v = (v & 0x33333333) + ((v >>> 2) & 0x33333333);
  return (((v + (v >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
}
export const hammingSplit = (a: Split64, b: Split64): number => popcount32(a.hi ^ b.hi) + popcount32(a.lo ^ b.lo);

export function jaccard(a: ReadonlySet<string>, b: ReadonlySet<string>): number {
  if (a.size === 0 && b.size === 0) return 1;
  let inter = 0;
  const [small, big] = a.size <= b.size ? [a, b] : [b, a];
  for (const g of small) if (big.has(g)) inter++;
  return inter / (a.size + b.size - inter);
}

export const hex64 = (h: bigint): string => h.toString(16).padStart(16, "0");
