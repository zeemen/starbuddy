// 道法导入器的公共部分（冲刺 4-K）：ID 规范、文本校验、日期、确定性 ID、禁用字段。
// 节点 ID 正则与迁移 0011 的 kge.node_id 域一致（tests/daofa/domain.test.ts 从迁移与 docs/design/kge/id-and-segments.md 读出来比对）。
import { stableUuid } from "../../../kge-inventory/src/import/build.ts";

export { stableUuid };

export const SUBJECT = "zhixing" as const;
/** kge.subject_prefix('zhixing') */
export const PREFIX = "MORAL";
export const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
export const COGNITIVE = ["know", "understand", "apply", "synthesize"] as const;
export const SCOPES = ["required", "elective", "edition_ext", "exam_ext", "enrichment"] as const;
/** 教材版次编码（kge.textbook_projection.edition，与 platform.curriculum_version.textbook_version 同一套） */
export const EDITION = /^[a-z0-9][a-z0-9-]{1,31}$/;
/** 工号（核验人、核对人）：与 verified_by 的长度一致，不写姓名 */
export const STAFF_CODE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
export const VERSION = /^\d{1,3}\.\d{1,3}\.\d{1,4}$/;

export class DaofaError extends Error {}

/**
 * 教材原句一类的字段一律拒绝（inventory-zhixing decisions #5：原文“来源待核”，法务确认前不入库）。
 * 按键名判断，出现在文件任何层级都拒绝；时政的“转载原文”字段同样拒绝（decisions #6）。
 */
export const FORBIDDEN_KEY = /(quote|original|excerpt|verbatim|phrases|full_?text|reprint|原句|原文|摘抄|转载|全文)/i;

const CONTROL = /[\u0000-\u001f\u007f]/;
// 公共知识层不收个人信息（规则 13），与 KGE-01 导入器同一组模式
const PERSONAL = [/1[3-9]\d{9}/, /\d{17}[\dXx]/, /[\w.+-]+@[\w-]+\.[\w.]+/, /[一-龥]{1,3}同学/, /学生[一-龥]{2,3}(说|写|答)/];

export function text(where: string, v: unknown, max: number): string {
  if (typeof v !== "string" || v.trim().length === 0 || [...v].length > max) throw new DaofaError(`${where} 必须是 1–${max} 字的文本`);
  if (CONTROL.test(v)) throw new DaofaError(`${where} 含控制字符`);
  if (PERSONAL.some((r) => r.test(v))) throw new DaofaError(`${where} 疑似含个人信息（公共知识层不收，规则 13）`);
  return v;
}

export function textList(where: string, v: unknown, max = 200, maxItems = 32): string[] {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v) || v.length > maxItems) throw new DaofaError(`${where} 必须是不超过 ${maxItems} 条的列表`);
  return v.map((x, i) => text(`${where}[${i}]`, x, max));
}

export function obj(where: string, v: unknown): Record<string, unknown> {
  if (v === null || typeof v !== "object" || Array.isArray(v)) throw new DaofaError(`${where} 必须是映射`);
  return v as Record<string, unknown>;
}

/** 只认白名单里的键；教材原句 / 转载原文一类的键单独报错，说明原因 */
export function keys(where: string, o: Record<string, unknown>, allowed: readonly string[], required: readonly string[] = []): void {
  for (const k of Object.keys(o)) {
    if (FORBIDDEN_KEY.test(k)) {
      throw new DaofaError(`${where}：不得出现原句或原文字段“${k}”（教材与读本原文、时政原文在法务确认前不入库，decisions #5、#6）`);
    }
    if (!allowed.includes(k)) throw new DaofaError(`${where}：不认识的键“${k}”`);
  }
  for (const k of required) if (o[k] === undefined || o[k] === null) throw new DaofaError(`${where}：缺“${k}”`);
}

/** 递归扫描：任何层级出现原句类键名都拒绝（列表里的映射也扫） */
export function scanForbidden(where: string, v: unknown): void {
  if (Array.isArray(v)) v.forEach((x, i) => scanForbidden(`${where}[${i}]`, x));
  else if (v !== null && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      if (FORBIDDEN_KEY.test(k)) {
        throw new DaofaError(`${where}.${k}：不得出现原句或原文字段（教材与读本原文、时政原文在法务确认前不入库，decisions #5、#6）`);
      }
      scanForbidden(`${where}.${k}`, x);
    }
  }
}

export function nodeId(where: string, v: unknown, depth: number): string {
  if (typeof v !== "string" || !NODE_ID.test(v)) throw new DaofaError(`${where}：节点 ID 不符合规范（docs/design/kge/id-and-segments.md）：${String(v)}`);
  if (v.split(".")[0] !== PREFIX) throw new DaofaError(`${where}：道法节点 ID 必须以 ${PREFIX}. 开头：${v}`);
  if (v.split(".").length !== depth) {
    const what = depth === 2 ? "领域" : depth === 3 ? "大概念（主题层）" : "观点（知识节点层）";
    throw new DaofaError(`${where}：${what}的 ID 应为 ${depth} 段：${v}`);
  }
  return v;
}

export const parentOf = (id: string): string => id.split(".").slice(0, -1).join(".");

export const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

// ---------- 日期（YYYY-MM-DD，按公历整数运算，与时区无关） ----------
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function parseDate(where: string, v: unknown): string {
  if (typeof v !== "string" || !DATE.test(v)) throw new DaofaError(`${where} 必须是 YYYY-MM-DD 日期`);
  const [y, m, d] = v.split("-").map(Number) as [number, number, number];
  if (m < 1 || m > 12 || d < 1 || d > daysInMonth(y, m)) throw new DaofaError(`${where} 不是有效日期：${v}`);
  return v;
}

function daysInMonth(y: number, m: number): number {
  return [31, (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]!;
}

const pad = (n: number, w = 2) => String(n).padStart(w, "0");

/** 加月数；目标月没有这一天时取月末（2026-01-31 + 1 个月 = 2026-02-28） */
export function addMonths(date: string, months: number): string {
  const [y, m, d] = date.split("-").map(Number) as [number, number, number];
  const idx = y * 12 + (m - 1) + months;
  const ny = Math.floor(idx / 12), nm = (idx % 12) + 1;
  return `${pad(ny, 4)}-${pad(nm)}-${pad(Math.min(d, daysInMonth(ny, nm)))}`;
}

/** 两个日期相差的天数（b − a） */
export function daysBetween(a: string, b: string): number {
  const t = (s: string) => {
    const [y, m, d] = s.split("-").map(Number) as [number, number, number];
    return Date.UTC(y, m - 1, d) / 86_400_000;
  };
  return t(b) - t(a);
}
