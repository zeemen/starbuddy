// 结论字段清单（开发计划 v2.0 盘点项 7；spec 设计要点 5）。
// 只读代码与迁移文本，提取“对学生下结论”的类型、schema、表与枚举的字段结构；不读任何学生数据。
// 每条都标“SIE 迁移处理：不迁移，只存档”（完整方案 v1.1 5.5）。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { extractBlock, filesMatching, listTextFiles, readText } from "./legacy.ts";
import type { ConclusionField, ConclusionSymbol, Subject } from "./model.ts";
import { SHARED, wenliAppDir } from "./subjects/chinese.ts";

const MODEL_CALL = /from\s+["'][^"']*\/llm["']|callTextJSON|callVision|anthropic|generateObject|messages\.create|chat\.completions/i;
// 证据引用：指向作答、题目、提交或证据本身的字段；student_id 之类的归属字段不算
const EVIDENCE = /evidence|^(attempt|question|item|submission)_?ids?$|^(attempt|question|item|submission)Ids?$/i;
const CONFIDENCE = /confidence|Confidence|uncertain|certainty/;

/** 触及 CLAUDE.md 规则 12 与 SIE“不推断清单”、不展示同伴比较的字段名或取值 */
const FORBIDDEN: [RegExp, string][] = [
  [/基础薄弱|中等提升|高分冲刺|目标校冲刺|培优|尖子|^segment$|student_label|abilityBand|ability_band/i, "层级标签（规则 12：不贴层级标签）"],
  [/personality|性格|人格/i, "人格与性格（规则 12）"],
  [/emotion|mood|情绪|心理|stress|压力/i, "情绪、心理或压力推断（规则 12）"],
  [/\biq\b|智力|天赋|talent/i, "智力或天赋（规则 12）"],
  [/rank/i, "排名 / 位次（SIE：不展示同伴比较，外部考试只记排名信息）"],
];

function forbiddenReason(name: string): string | null {
  for (const [re, why] of FORBIDDEN) if (re.test(name)) return why;
  return null;
}

/** 按顶层逗号 / 分号 / 换行切段，取每段开头的字段名（跳过嵌套括号、字符串与注释）。 */
export function topLevelKeys(body: string): string[] {
  const keys: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let seg = "";
  const flush = () => {
    const m = /^\s*(?:readonly\s+)?["']?([A-Za-z_$一-龥][\w$一-龥]*)["']?\??\s*:/.exec(seg);
    if (m) keys.push(m[1]!);
    seg = "";
  };
  for (let i = 0; i < body.length; i++) {
    const ch = body[i]!;
    if (quote) {
      if (depth === 0) seg += ch;
      if (ch === "\\") i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "/" && body[i + 1] === "/") {
      const nl = body.indexOf("\n", i);
      i = (nl < 0 ? body.length : nl) - 1;
      continue;
    }
    if (ch === "/" && body[i + 1] === "*") {
      const end = body.indexOf("*/", i + 2);
      i = end < 0 ? body.length : end + 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      if (depth === 0) seg += ch;
      quote = ch;
      continue;
    }
    if ("([{".includes(ch)) depth++;
    else if (")]}".includes(ch)) depth--;
    else if (depth === 0 && (ch === "," || ch === ";" || ch === "\n")) {
      flush();
      continue;
    }
    if (depth === 0) seg += ch;
  }
  flush();
  return [...new Set(keys)];
}

function fieldsOf(subject: Subject, source: string, symbol: string, names: string[]): ConclusionField[] {
  return names.map((field) => ({
    subject,
    source,
    symbol,
    field,
    evidenceRef: EVIDENCE.test(field),
    confidence: CONFIDENCE.test(field),
    forbidden: forbiddenReason(field),
  }));
}

function symbolOf(
  subject: Subject,
  source: string,
  symbol: string,
  kind: ConclusionSymbol["kind"],
  algorithm: string,
  algorithmBasis: string,
  names: string[],
): ConclusionSymbol {
  const fields = fieldsOf(subject, source, symbol, names);
  return {
    subject,
    source,
    symbol,
    kind,
    algorithm,
    algorithmBasis,
    hasEvidenceRef: fields.some((f) => f.evidenceRef),
    hasConfidence: fields.some((f) => f.confidence),
    fields,
  };
}

/** 文件里全部 `export interface` 的字段 */
function interfacesIn(root: string, rel: string, subject: Subject, only?: string[]): ConclusionSymbol[] {
  const text = readText(root, rel);
  if (text === null) return [];
  const pureRule = !MODEL_CALL.test(text);
  const names = [...text.matchAll(/export interface (\w+)/g)].map((m) => m[1]!).filter((n) => !only || only.includes(n));
  return names.map((name) => {
    const body = extractBlock(text, new RegExp(`export interface ${name}\\b[^{]*`)) ?? "";
    return symbolOf(
      subject,
      rel,
      name,
      "interface",
      pureRule ? "规则（纯函数）" : "含模型调用",
      pureRule ? "所在文件没有模型调用" : "所在文件有模型调用",
      topLevelKeys(body),
    );
  });
}

/** zod schema：算法看引用它的文件里有没有模型调用 */
function zodSchema(app: string, rel: string, name: string, subject: Subject, searchFiles: string[]): ConclusionSymbol | null {
  const text = readText(app, rel);
  if (text === null) return null;
  const body = extractBlock(text, new RegExp(`export const ${name}\\s*=\\s*z\\s*\\.\\s*object\\s*\\(`));
  if (body === null) return null;
  const users = filesMatching(app, searchFiles, new RegExp(`\\b${name}\\b`)).filter(
    (f) => f !== rel && !/\.test\.|\/mock\//.test(f),
  );
  const withModel = users.filter((f) => MODEL_CALL.test(readFileSync(join(app, f), "utf8")));
  return symbolOf(
    subject,
    rel,
    name,
    "zod",
    withModel.length > 0 ? "含模型调用" : "未判断",
    withModel.length > 0 ? `被含模型调用的文件使用：${withModel.slice(0, 3).join("、")}` : "没有找到含模型调用的使用方",
    topLevelKeys(body),
  );
}

/** 库表：合并 create table 与 alter table add column 的列 */
export function tableColumns(app: string, table: string): string[] | null {
  const files = listTextFiles(app, ["supabase/migrations"]);
  const cols: string[] = [];
  let found = false;
  for (const f of files) {
    const text = readFileSync(join(app, f), "utf8");
    const body = extractBlock(text, new RegExp(`create\\s+table\\s+(?:if\\s+not\\s+exists\\s+)?(?:public\\.)?${table}\\s*(?=\\()`, "i"), "(");
    if (body !== null) {
      found = true;
      for (const line of body.split("\n")) {
        const m = /^\s*([a-z_][a-z0-9_]*)\s+[a-z]/i.exec(line.replace(/--.*$/, ""));
        if (m && !/^(primary|unique|constraint|foreign|check|exclude)$/i.test(m[1]!)) cols.push(m[1]!);
      }
    }
    for (const m of text.matchAll(new RegExp(`alter\\s+table\\s+(?:if\\s+exists\\s+)?(?:public\\.)?${table}\\s+add\\s+column\\s+(?:if\\s+not\\s+exists\\s+)?([a-z_][a-z0-9_]*)`, "gi"))) {
      cols.push(m[1]!);
    }
  }
  return found ? [...new Set(cols)] : null;
}

export function inventoryConclusions(ayaRoot: string, wenliRoot: string): ConclusionSymbol[] {
  const out: ConclusionSymbol[] = [];

  // 数学：全部是 lib/ 下的规则式纯函数，数据在浏览器 localStorage
  for (const f of ["lib/evaluation.ts", "lib/calibration.ts", "lib/weekly.ts", "lib/teacher.ts", "lib/consultation.ts", "lib/trends.ts"]) {
    out.push(...interfacesIn(ayaRoot, f, "math"));
  }
  out.push(...interfacesIn(ayaRoot, "lib/exam.ts", "math", ["ExamAnalysis"]));
  out.push(...interfacesIn(ayaRoot, "lib/types.ts", "math", ["StudentProfile", "NodeMastery"]));

  // 语文与道法：zod schema（约束模型输出）、库表、枚举
  const app = wenliAppDir(wenliRoot);
  const searchFiles = listTextFiles(app, [SHARED, "apps"]).filter((f) => /\.(ts|tsx)$/.test(f));
  for (const name of ["DiagnoseResultSchema", "PlanResultSchema", "GradeResultSchema", "ParentReportSchema"]) {
    const s = zodSchema(app, `${SHARED}/domain/schemas.ts`, name, "chinese", searchFiles);
    if (s) out.push(s);
  }
  const tables: [string, Subject][] = [
    ["ability_state", "chinese"],
    ["point_mastery", "chinese"],
    ["mastery_snapshot", "chinese"],
    ["parent_report", "chinese"],
    ["stage_report", "chinese"],
    ["profiles", "chinese"],
    ["daofa_ability", "moral"],
    ["daofa_submission", "moral"],
  ];
  for (const [t, subject] of tables) {
    const cols = tableColumns(app, t);
    if (cols === null) continue;
    // profiles 只取结论性的列，账号类列与本清单无关
    const pick = t === "profiles" ? cols.filter((c) => /segment|level|stage|rank|score|band/i.test(c)) : cols;
    if (pick.length === 0) continue;
    out.push(symbolOf(subject, "supabase/migrations", t, "table", "未判断（库表）", "写入方要看应用代码", pick));
  }
  const seg = (() => {
    for (const f of listTextFiles(app, ["supabase/migrations"])) {
      const m = /create\s+type\s+student_seg_t\s+as\s+enum\s*\(([^)]*)\)/i.exec(readFileSync(join(app, f), "utf8"));
      if (m) return [...m[1]!.matchAll(/'([^']+)'/g)].map((x) => x[1]!);
    }
    return null;
  })();
  if (seg) {
    const s = symbolOf("chinese", "supabase/migrations", "student_seg_t", "enum", "未判断（枚举）", "学生分型的取值", seg);
    out.push(s);
  }
  out.push(...interfacesIn(app, `${SHARED}/domain/daofa-diagnosis.ts`, "moral"));

  return out.sort((a, b) =>
    a.subject !== b.subject ? (a.subject < b.subject ? -1 : 1) : a.source !== b.source ? (a.source < b.source ? -1 : 1) : a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : 0,
  );
}
