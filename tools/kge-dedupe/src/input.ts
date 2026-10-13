// 输入守卫（KGE-08 范围 1）：只接受题目层字段；作答、学生、手写、批改类字段一律拒绝（规则 13：作答永不进公共层）。
// 合成样例必须 synthetic: true，且自由文本（notice、每道题的 text）以“（合成）”开头；真实题目只走 --aya（legacy 只读加载器）。
// 资源上限：样例字节数、题数、题目文本总字数；错误信息不回显输入内容（不拼解析器原文、不回显不认识的键名）。
import type { LegacyBank } from "../../kge-import/src/legacy-items.ts";

export interface Item {
  id: string;
  /** 题目文本：只在内存里用于规范化与指纹，不写进任何产物 */
  text: string;
  mainNodes: string[];
  paperId?: string;
  seq?: number;
}
export interface Input {
  source: "fixture" | "aya";
  /** 族码前缀用的学科代号 */
  subject: "syn" | "aya";
  /** 主节点 ID 所在的坐标系（报告里写明：不是 KGE 节点 ID，落库须重算） */
  nodeSystem: string;
  synthetic: boolean;
  items: Item[];
  /** 题目 ID → 旧题组码（aya-sprint-*）；只有 --aya 有 */
  legacyFamilies: Map<string, string>;
}

export class InputError extends Error {}

export const FORMAT = "kge-dedupe-input/1";
export const SYNTHETIC_PREFIX = "（合成）";
export const NODE_SYSTEM = {
  fixture: "fixture：合成样例自带的节点 ID（只供单测，不对应任何地图版本）",
  aya: "legacy-aya：阿鸦旧编码 mainNodes（不是 KGE 节点 ID）",
} as const;
export const MAX_ITEMS = 20000;
export const MAX_FIXTURE_BYTES = 8 * 1024 * 1024;
/** 全部题目文本的总字数（码点）上限 */
export const MAX_TOTAL_CHARS = 1_000_000;
const TOP_KEYS = ["format", "synthetic", "notice", "items"] as const;
const ITEM_KEYS = ["id", "text", "main_nodes", "paper_id", "seq"] as const;
/** 作答、学生、手写、批改类键名（中英文，大小写不敏感）；先于“不认识的键”检查，单独说明原因 */
export const FORBIDDEN_KEY =
  /(answer|response|respond|submission|student|learner|pupil|handwrit|scribble|grad(e|ing|ed)|score|marking|correction|feedback|作答|答案|答题|学生|学员|手写|笔迹|批改|批注|评分|得分|判分|订正)/i;
const ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/; // 与 platform.skill_local_id 同一规则
export const NODE_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;
/** 个人信息（与 KGE-01 导入器同一组模式）：先 NFKC、去空白再匹配，合成样例里也不许出现。
 *  邮箱模式加了起点断言（前面不是同类字符才开始匹配）：否则长串无 @ 的字母数字会让它回溯成平方级 */
const PERSONAL = [/1[3-9]\d{9}/, /\d{17}[\dXx]/, /(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.]+/, /[一-龥]{1,3}同学/, /学生[一-龥]{2,3}(说|写|答)/];

export const looksPersonal = (v: string): boolean => {
  const t = v.normalize("NFKC").replace(/\s+/g, "");
  return PERSONAL.some((r) => r.test(t));
};

const clip = (k: string): string => ([...k].length > 16 ? `${[...k].slice(0, 16).join("")}…` : k);

function checkKeys(where: string, o: Record<string, unknown>, allowed: readonly string[]): void {
  Object.keys(o).forEach((k, i) => {
    if (FORBIDDEN_KEY.test(k)) throw new InputError(`${where}：不得出现作答、学生、手写或批改类字段“${clip(k)}”（只收题目层，规则 13）`);
    if (!allowed.includes(k)) throw new InputError(`${where}：第 ${String(i + 1)} 个键不认识（只收 ${allowed.join("、")}）`);
  });
}

function obj(where: string, v: unknown): Record<string, unknown> {
  if (v === null || typeof v !== "object" || Array.isArray(v)) throw new InputError(`${where} 必须是对象`);
  return v as Record<string, unknown>;
}

function freeText(where: string, v: unknown, max: number): string {
  if (typeof v !== "string" || v.trim().length === 0 || [...v].length > max || CONTROL.test(v)) {
    throw new InputError(`${where} 必须是 1–${String(max)} 字、无控制字符的文本`);
  }
  if (looksPersonal(v)) throw new InputError(`${where} 疑似含个人信息（规则 8、13）`);
  if (!v.startsWith(SYNTHETIC_PREFIX)) throw new InputError(`${where} 是合成样例的自由文本，必须以“${SYNTHETIC_PREFIX}”开头`);
  return v;
}

function checkTotal(items: readonly Item[]): void {
  let total = 0;
  for (const it of items) total += [...it.text].length;
  if (total > MAX_TOTAL_CHARS) throw new InputError(`题目文本总字数 ${String(total)} 超过上限 ${String(MAX_TOTAL_CHARS)}`);
}

/** 解析合成样例（JSON）。真实内容（synthetic 不是 true）一律拒绝 */
export function parseFixture(raw: string): Input {
  if (Buffer.byteLength(raw, "utf8") > MAX_FIXTURE_BYTES) throw new InputError(`样例超过 ${String(MAX_FIXTURE_BYTES)} 字节`);
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new InputError("样例不是合法 JSON");
  }
  const root = obj("样例", data);
  checkKeys("样例", root, TOP_KEYS);
  if (root.format !== FORMAT) throw new InputError(`format 必须是 ${FORMAT}`);
  if (root.synthetic !== true) throw new InputError("--fixture 只收合成样例（synthetic: true）；真实题目经 --aya 走 legacy 只读加载器");
  freeText("notice", root.notice, 200);
  if (!Array.isArray(root.items) || root.items.length === 0 || root.items.length > MAX_ITEMS) {
    throw new InputError(`items 必须是 1–${String(MAX_ITEMS)} 条`);
  }
  const items = root.items.map((x, i): Item => {
    const o = obj(`items[${String(i)}]`, x);
    checkKeys(`items[${String(i)}]`, o, ITEM_KEYS);
    if (typeof o.id !== "string" || !ITEM_ID.test(o.id)) throw new InputError(`items[${String(i)}].id 不符合 platform.skill_local_id`);
    const where = `题目 ${o.id}`;
    const text = freeText(`${where}.text`, o.text, 20000);
    if (!Array.isArray(o.main_nodes) || o.main_nodes.length > 16 || !o.main_nodes.every((n) => typeof n === "string" && NODE_ID.test(n))) {
      throw new InputError(`${where}.main_nodes 必须是不超过 16 个节点 ID 的数组`);
    }
    const item: Item = { id: o.id, text, mainNodes: [...(o.main_nodes as string[])] };
    const hasPaper = o.paper_id !== undefined, hasSeq = o.seq !== undefined;
    if (hasPaper !== hasSeq) throw new InputError(`${where}：paper_id 与 seq 要么都给，要么都不给`);
    if (hasPaper) {
      if (typeof o.paper_id !== "string" || !ITEM_ID.test(o.paper_id)) throw new InputError(`${where}.paper_id 不合法`);
      if (typeof o.seq !== "number" || !Number.isInteger(o.seq) || o.seq < 1 || o.seq > 1000) throw new InputError(`${where}.seq 必须是 1–1000 的整数`);
      item.paperId = o.paper_id;
      item.seq = o.seq;
    }
    return item;
  });
  checkUnique(items);
  checkTotal(items);
  return { source: "fixture", subject: "syn", nodeSystem: NODE_SYSTEM.fixture, synthetic: true, items, legacyFamilies: new Map() };
}

function checkUnique(items: readonly Item[]): void {
  const ids = new Set<string>(), slots = new Set<string>();
  for (const it of items) {
    if (ids.has(it.id)) throw new InputError(`题目 ID ${it.id} 重复`);
    ids.add(it.id);
    if (it.paperId !== undefined) {
      const slot = `${it.paperId}#${String(it.seq)}`;
      if (slots.has(slot)) throw new InputError(`试卷 ${it.paperId} 的第 ${String(it.seq)} 题重复`);
      slots.add(slot);
    }
  }
}

/** 阿鸦 legacy 题库（经 tools/kge-import 的隔离加载器只读）→ 题目层输入。打标用文本开头的单元名是元数据，不算题干，去掉。
 *  旧题组：与 tools/kge-import/src/items/build.ts 同一口径（压轴专项 aya-sprint-<代码>，一题多组时取第一个） */
export function fromLegacy(bank: LegacyBank): Input {
  if (bank.questions.length > MAX_ITEMS) throw new InputError(`题目超过 ${String(MAX_ITEMS)} 条`);
  const items = bank.questions.map((q): Item => {
    const prefix = q.unitName === "" ? "" : `${q.unitName}\n`;
    const text = prefix !== "" && q.text.startsWith(prefix) ? q.text.slice(prefix.length) : q.text;
    if (!q.mainNodes.every((n) => NODE_ID.test(n))) throw new InputError(`题目 ${q.id} 的主节点 ID 不合规`);
    return { id: q.id, text, mainNodes: [...q.mainNodes] };
  });
  checkUnique(items);
  checkTotal(items);
  const legacyFamilies = new Map<string, string>();
  for (const c of bank.sprintCategories) for (const id of c.questionIds) if (!legacyFamilies.has(id)) legacyFamilies.set(id, `aya-sprint-${c.id}`);
  return { source: "aya", subject: "aya", nodeSystem: NODE_SYSTEM.aya, synthetic: false, items, legacyFamilies };
}
