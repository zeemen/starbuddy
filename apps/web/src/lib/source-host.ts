// 官方来源清单的主机名显示（审核台 /review/policy/sources，迁移 0048）：防同形字仿冒官网。只在服务端用（node:url）。
//   - 任一标签以 xn-- 开头即“国际化域名”；
//   - 解码失败（node 的 domainToUnicode 返回空串，或解码后仍是 xn--）显示“无法解码”，不显示成“—”；
//   - 解码出的标签含“既不是汉字、也不是 ASCII”的字符（如西里尔字母 а 冒充拉丁 a），或同一标签里汉字与 ASCII 字母混写，加醒目警告。
import { domainToUnicode } from 'node:url';

export interface HostView {
  /** 至少一个标签以 xn-- 开头 */
  idn: boolean;
  /** Unicode 形式；不是国际化域名或解码失败时为 null */
  unicode: string | null;
  /** 国际化域名但解码失败 */
  undecodable: boolean;
  /** 解码出的标签含非汉字非 ASCII 字符，或混合书写系统 */
  suspicious: boolean;
}

const HAN = /\p{Script=Han}/u;
const ASCII_LETTER = /[A-Za-z]/;

/** 一个解码后的标签是否可疑：有既不是汉字也不是 ASCII 的字符，或汉字与 ASCII 字母混写 */
export function suspiciousLabel(label: string): boolean {
  let han = false;
  let latin = false;
  for (const ch of label) {
    if (HAN.test(ch)) han = true;
    else if (ch.charCodeAt(0) < 0x80) { if (ASCII_LETTER.test(ch)) latin = true; }
    else return true;
  }
  return han && latin;
}

export function hostView(host: string): HostView {
  const labels = host.split('.');
  const idn = labels.some((l) => l.startsWith('xn--'));
  if (!idn) return { idn: false, unicode: null, undecodable: false, suspicious: false };
  const u = domainToUnicode(host);
  const decoded = u === '' ? [] : u.split('.');
  if (decoded.length !== labels.length || decoded.some((l) => l.startsWith('xn--'))) {
    return { idn: true, unicode: null, undecodable: true, suspicious: true };
  }
  const suspicious = labels.some((l, i) => l.startsWith('xn--') && suspiciousLabel(decoded[i]!));
  return { idn: true, unicode: u, undecodable: false, suspicious };
}
