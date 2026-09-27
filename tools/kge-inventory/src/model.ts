// 盘点结果的数据结构。报告（Markdown）与 JSON 都由它生成。
// 只放资产 ID、名称、计数与文件路径；不放题干、范文、原文全文，不放任何学生数据（spec 设计要点 6）。

export type Subject = "math" | "chinese" | "moral";

export const SUBJECT_NAME: Record<Subject, string> = {
  math: "数学 · 阿鸦",
  chinese: "语文 · 文砺",
  moral: "道法 · 知行",
};

export type Value = number | string | boolean | null;

export interface Finding {
  key: string; // 稳定键，便于跨次比较
  label: string;
  value: Value;
  /** 样例 ID 或名称，最多 SAMPLE_LIMIT 个，已排序 */
  samples?: string[];
  note?: string;
}

export interface Group {
  title: string;
  findings: Finding[];
}

export interface SubjectReport {
  subject: Subject;
  /** 读过的源文件（相对 legacy 根目录） */
  sources: string[];
  groups: Group[];
  /** 加载失败的模块及原因 */
  loadErrors: string[];
}

/** 开发计划 v2.0 盘点项 6 的六个问题 */
export const V2_QUESTIONS = [
  { id: "counts", text: "节点 / 题目 / Rubric 计数" },
  { id: "missing_fields", text: "字段缺失" },
  { id: "orphans_cycles", text: "孤点与环" },
  { id: "legacy_codes", text: "旧编码并存" },
  { id: "moral_pages_edition", text: "道法页码是否绑定版次" },
  { id: "reader_ingested", text: "《学生读本》是否入库" },
] as const;
export type V2QuestionId = (typeof V2_QUESTIONS)[number]["id"];

export interface Answer {
  question: V2QuestionId;
  subject: Subject;
  answer: string;
}

/** KGE V1.1 第七章 / 完整方案 v1.1 5.10 的四项阻断问题 */
export interface BlockerCheck {
  id: string;
  subject: Subject;
  problem: string;
  verdict: string;
  evidence: string[];
}

export interface ConclusionField {
  subject: Subject;
  source: string; // 文件或表
  symbol: string; // 接口 / schema / 表 / 枚举
  field: string;
  evidenceRef: boolean; // 字段本身是否是证据引用（题目 / 作答 / 证据 ID）
  confidence: boolean; // 字段本身是否是置信度或不确定度
  forbidden: string | null; // 触及 CLAUDE.md 规则 12 不推断清单的原因
}

export interface ConclusionSymbol {
  subject: Subject;
  source: string;
  symbol: string;
  kind: "interface" | "zod" | "table" | "enum";
  algorithm: string; // 规则（纯函数）/ 含模型调用 / 人工标注
  algorithmBasis: string; // 判断依据
  hasEvidenceRef: boolean;
  hasConfidence: boolean;
  fields: ConclusionField[];
}

export interface DbOutputRow {
  database: string;
  key: string;
  value: string;
}

export interface Meta {
  tool: string;
  toolVersion: string;
  legacy: { name: string; root: string; commit: string | null; clean: boolean | null; commits: number | null }[];
}

export interface Inventory {
  meta: Meta;
  subjects: SubjectReport[];
  answers: Answer[];
  blockers: BlockerCheck[];
  conclusions: ConclusionSymbol[];
  db: { provided: boolean; rows: DbOutputRow[] };
}

export const SAMPLE_LIMIT = 10;

export function samples(xs: Iterable<string>): string[] {
  return [...new Set(xs)].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)).slice(0, SAMPLE_LIMIT);
}

export function pct(n: number, d: number): string {
  if (d === 0) return "—";
  return `${((100 * n) / d).toFixed(1)}%`;
}

export function finding(key: string, label: string, value: Value, extra: Partial<Finding> = {}): Finding {
  const f: Finding = { key, label, value };
  if (extra.samples && extra.samples.length > 0) f.samples = extra.samples;
  if (extra.note) f.note = extra.note;
  return f;
}
