// 规范化 JSON：与 services/learning/learning/fingerprint.py 的 canonical() 逐字节一致
// （Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)）。
// 共享测试向量：packages/contracts/testdata/canonical-vectors.json。
// 两种语言表示不同的值直接拒绝：NaN / Infinity、超出安全整数范围的整数、孤立代理项。
// 整数值的浮点数（Python 的 1.0）在 JS 里与整数无法区分，一律按整数输出；计量指纹只在 BFF 内部比较，不受影响。
import { createHash } from 'node:crypto';

export function canonical(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return canonicalNumber(value);
  if (typeof value === 'string') return canonicalString(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined);
    entries.sort(([a], [b]) => compareCodePoints(a, b));
    return '{' + entries.map(([k, v]) => canonicalString(k) + ':' + canonical(v)).join(',') + '}';
  }
  throw new TypeError(`canonical: unsupported value of type ${typeof value}`);
}

export function sha256Hex(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

// Python 按码点排序；JS 默认按 UTF-16 码元排序，两者在 BMP 高位字符与增补平面字符之间不同
function compareCodePoints(a: string, b: string): number {
  const ca = Array.from(a, (ch) => ch.codePointAt(0) as number);
  const cb = Array.from(b, (ch) => ch.codePointAt(0) as number);
  const n = Math.min(ca.length, cb.length);
  for (let i = 0; i < n; i++) {
    if (ca[i] !== cb[i]) return (ca[i] as number) - (cb[i] as number);
  }
  return ca.length - cb.length;
}

const SHORT_ESCAPES: Record<string, string> = {
  '"': '\\"', '\\': '\\\\', '\b': '\\b', '\f': '\\f', '\n': '\\n', '\r': '\\r', '\t': '\\t',
};

function canonicalString(s: string): string {
  if (!s.isWellFormed()) throw new TypeError('canonical: lone surrogate in string');
  let out = '"';
  for (const ch of s) {
    const short = SHORT_ESCAPES[ch];
    if (short !== undefined) out += short;
    else if ((ch.codePointAt(0) as number) < 0x20) out += '\\u' + (ch.codePointAt(0) as number).toString(16).padStart(4, '0');
    else out += ch;
  }
  return out + '"';
}

function canonicalNumber(x: number): string {
  if (!Number.isFinite(x)) throw new TypeError('canonical: NaN and Infinity are not JSON');
  if (Number.isInteger(x)) {
    if (!Number.isSafeInteger(x)) throw new TypeError('canonical: integer outside the safe range');
    return String(x === 0 ? 0 : x);
  }
  return pythonFloatRepr(x);
}

// Python float.__repr__：最短往返数字；指数在 [-4, 16) 内用定点，否则用科学计数（指数至少两位）
function pythonFloatRepr(x: number): string {
  const sign = x < 0 ? '-' : '';
  const [mant, expText] = Math.abs(x).toExponential().split('e') as [string, string];
  const digits = mant.replace('.', '');
  const exp = Number(expText);
  if (exp >= -4 && exp < 16) {
    if (exp >= 0) {
      const intPart = digits.slice(0, exp + 1).padEnd(exp + 1, '0');
      const frac = digits.slice(exp + 1);
      return sign + intPart + '.' + (frac === '' ? '0' : frac);
    }
    return sign + '0.' + '0'.repeat(-exp - 1) + digits;
  }
  const m = digits.length > 1 ? digits[0] + '.' + digits.slice(1) : digits;
  const e = Math.abs(exp).toString().padStart(2, '0');
  return sign + m + 'e' + (exp < 0 ? '-' : '+') + e;
}
