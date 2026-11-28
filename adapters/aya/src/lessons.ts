// 讲评课内容包（specs/stage-point-to-ask-answer PR ③a）：阿鸦按契约 TeachRequest.question 的 lesson_ref 取已发布的讲解 DSL 与台词稿。
// 讲评课是学科内容（公共层），由教研与人设审核后随内容包发布；请求不携带讲解内容（契约 v1.1 草稿内追加）。
// - 格式 aya-lesson-pack/1：{ format, lessons: [{ lesson_id, lesson_version, skill_node, item_ref?, dsl, script }] }；
// - 讲解 DSL 与台词稿用 packages/stage 的同一套入口读入（migrateStageDsl、migrateStageScript：旧版本沿迁移阶梯升级、文字逐字核对），
//   不另写一份校验；任何一节不合格就整包拒绝（启动失败），不跳过；
// - skill_node 必须是内容包里的节点，item_ref（若有）必须是内容包里的题，且讲评课只讲这道题；
// - 两种来源（specs/stage-lesson-staging PR 1）：
//   - 已发布的讲评课内容包 content/lessons.json（格式 aya-lesson-pack/2）：由 scripts/build-lesson-pack.ts 从草稿（格式 1）与审核记录生成，
//     只收教研与人设都通过、且审核时的内容摘要与现在一致的讲评课，台词与板书原文过 C9 词表检查（严格模式）。
//     加载时不再跑词表（运行镜像里没有 Python），只核对发布记录与内容对得上：逐节摘要、按角色的文字条数、扫描模式与词表版本；
//     对不上就整包拒绝。仓库里还没有真实讲评课，所以不带这个文件，缺省为空（带 lesson_ref / question 的请求一律 404）；
//   - 合成讲评课内容包（格式 1，AYA_LESSON_PACK）：只给测试与演练，与替代内容包同样只在候选版开关打开时读；设了它就不读已发布的包。
// - 契约口径（specs/lesson-catalog C2）：讲解标题（目录里的 title）与台词稿每一行（字幕台词与预制答疑）还要合插件契约 1.1.0 的文字口径
//   （LessonCatalogEntry.title、StageScriptLine.text：不含尖括号、控制字符、双向控制符、零宽字符与行段分隔符，标题 ≤ 40、台词 ≤ 60 个字符）。
//   packages/stage 的口径更宽（例如 `x < y` 可以），不合契约的讲评课到了 list_lessons、teach 的响应自检才会整份回 500；
//   所以加载时（也就是 build:lessons 构建时）就整包拒绝。两条正则与契约原文逐字一致（tests/lessons.test.ts 核对）。
// 本模块只读文件、不联网、不写任何东西；讲评课文字不进日志。
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  dslTexts, migrateStageDsl, migrateStageScript, type ValidatedStageDsl, type ValidatedStageScript,
} from "../../../packages/stage/src/index.ts";
import type { LoadedPack } from "./content.ts";

export const LESSON_PACK_FORMAT = "aya-lesson-pack/1";
/** 已发布的讲评课内容包（教研与人设审核、C9 词表检查之后由构建脚本生成） */
export const LESSON_RELEASE_FORMAT = "aya-lesson-pack/2";
export const LESSON_PACK_ENV = "AYA_LESSON_PACK";
export const PUBLISHED_LESSONS = resolve(fileURLToPath(new URL(".", import.meta.url)), "../content/lessons.json");
/** 讲评课文字按谁说的过哪套词表：老师（与板书）用阿鸦，点点用点点（packages/personas 登记表） */
export const LESSON_PERSONAS = { teacher: "aya", buddy: "diandian" } as const;
export type LessonPersona = (typeof LESSON_PERSONAS)[keyof typeof LESSON_PERSONAS];
const LESSON_FIELDS = ["lesson_id", "lesson_version", "skill_node", "item_ref", "dsl", "script"] as const;
const LESSON_ID_RE = /^[a-z][a-z0-9_.-]{0,63}$/;
const SEMVER_RE = /^\d+\.\d+\.\d+$/;
const LOCAL_REF_RE = /^aya:([A-Za-z0-9][A-Za-z0-9_.-]{0,63})$/;
/** 插件契约 1.1.0 的文字口径（与 plugin-api.v1.1.yaml 逐字一致）。u 标志：与 ajv 的正则口径相同，按码位计长度 */
const CONTRACT_TEXT_CLASS = "[^<>\\u0000-\\u001F\\u007F-\\u009F\\u200B-\\u200F\\u2028\\u2029\\u202A-\\u202E\\u2066-\\u2069\\uFEFF]";
export const CONTRACT_TITLE_PATTERN = `^${CONTRACT_TEXT_CLASS}{1,40}$`;
export const CONTRACT_LINE_PATTERN = `^${CONTRACT_TEXT_CLASS}{1,60}$`;
const CONTRACT_TITLE_RE = new RegExp(CONTRACT_TITLE_PATTERN, "u");
const CONTRACT_LINE_RE = new RegExp(CONTRACT_LINE_PATTERN, "u");

/** 讲解标题与台词稿每一行是否合契约口径；不合格时返回位置（title 或 line:<台词编号>），不带文字本身 */
export function contractTextProblem(dsl: ValidatedStageDsl, script: ValidatedStageScript): string | null {
  if (!CONTRACT_TITLE_RE.test(dsl.title)) return "title";
  const bad = script.lines.find((x) => !CONTRACT_LINE_RE.test(x.text));
  return bad ? `line:${bad.id}` : null;
}

export interface Lesson {
  lesson_id: string;
  lesson_version: string;
  /** 讲评课讲的主节点（本地 ID，不带 aya: 前缀） */
  skillNode: string;
  /** 讲评课讲的那道题（本地 ID）；没有时这节课不针对某道题 */
  itemId?: string;
  dsl: ValidatedStageDsl;
  script: ValidatedStageScript;
}

export interface LoadedLessons {
  /** 键：`${lesson_id}@${lesson_version}` */
  byRef: ReadonlyMap<string, Lesson>;
  /** 讲评课内容包的版本标记（文件 sha256 前 8 位；没有内容包时为 none），进 source_version */
  version: string;
}

export const NO_LESSONS: LoadedLessons = Object.freeze({ byRef: new Map(), version: "none" });

export const lessonKey = (id: string, version: string) => `${id}@${version}`;

/** 校验并建索引。任何问题都抛错（启动失败）：错误信息只有讲评课编号与错误码，不含讲解文字 */
export function indexLessons(raw: unknown, content: LoadedPack, version = "inline"): LoadedLessons {
  const doc = raw as { format?: unknown; lessons?: unknown };
  if (!doc || doc.format !== LESSON_PACK_FORMAT || !Array.isArray(doc.lessons)) {
    throw new Error(`讲评课内容包不合格：format 应为 ${LESSON_PACK_FORMAT}，lessons 应为数组`);
  }
  const byRef = new Map<string, Lesson>();
  for (const [i, l] of (doc.lessons as Record<string, unknown>[]).entries()) {
    const where = `lessons[${i}]`;
    if (!l || typeof l !== "object") throw new Error(`${where}：不是对象`);
    const extra = Object.keys(l).filter((k) => !(LESSON_FIELDS as readonly string[]).includes(k));
    if (extra.length) throw new Error(`${where}：多余字段 ${extra.join(", ")}`);
    const { lesson_id: id, lesson_version: ver, skill_node: node, item_ref: item } = l;
    if (typeof id !== "string" || !LESSON_ID_RE.test(id) || typeof ver !== "string" || !SEMVER_RE.test(ver)) {
      throw new Error(`${where}：lesson_id 或 lesson_version 不合格`);
    }
    const key = lessonKey(id, ver);
    if (byRef.has(key)) throw new Error(`${where}：讲评课 ${key} 重复`);
    const nodeId = typeof node === "string" ? LOCAL_REF_RE.exec(node)?.[1] : undefined;
    if (!nodeId || !content.nodeById.has(nodeId)) throw new Error(`${where}（${key}）：skill_node 不在内容包里`);
    let itemId: string | undefined;
    if (item !== undefined) {
      itemId = typeof item === "string" ? LOCAL_REF_RE.exec(item)?.[1] : undefined;
      const it = itemId ? content.itemById.get(itemId) : undefined;
      if (!it) throw new Error(`${where}（${key}）：item_ref 不在内容包里`);
      if (!it.mainNodes.includes(nodeId)) throw new Error(`${where}（${key}）：skill_node 不是这道题的主节点`);
    }
    const d = migrateStageDsl(l.dsl);
    if (!d.ok) throw new Error(`${where}（${key}）：讲解 DSL 不合格（${d.errors.slice(0, 3).map((e) => e.code).join(", ")}）`);
    const s = migrateStageScript(d.dsl, l.script);
    if (!s.ok) throw new Error(`${where}（${key}）：台词稿不合格（${s.errors.slice(0, 3).map((e) => e.code).join(", ")}）`);
    const off = contractTextProblem(d.dsl, s.script);
    if (off) throw new Error(`${where}（${key}）：文字不合插件契约口径（${off}；不收尖括号、控制字符与零宽字符，标题不超过 40 个字、台词不超过 60 个字）`);
    byRef.set(key, Object.freeze({ lesson_id: id, lesson_version: ver, skillNode: nodeId, ...(itemId ? { itemId } : {}), dsl: d.dsl, script: s.script }));
  }
  return Object.freeze({ byRef, version });
}

// ---------------------------------------------------------------- 已发布的讲评课内容包（格式 2）

const sortKeys = (v: unknown): unknown =>
  Array.isArray(v) ? v.map(sortKeys)
    : v && typeof v === "object" ? Object.fromEntries(Object.keys(v as object).sort().map((k) => [k, sortKeys((v as Record<string, unknown>)[k])]))
      : v;

/** 一节讲评课的内容摘要：六个字段按键排序后的 JSON 的 sha256。审核记录与发布包都按它把“审的是哪一版内容”钉住 */
export function lessonDigest(raw: Record<string, unknown>): string {
  const picked = Object.fromEntries(LESSON_FIELDS.filter((k) => raw[k] !== undefined).map((k) => [k, raw[k]]));
  return `sha256:${createHash("sha256").update(JSON.stringify(sortKeys(picked)), "utf8").digest("hex")}`;
}

export interface LessonText {
  lesson: string;
  /** 文字的位置：line:<台词编号> 或 board:<序号>（板书、标题与公式原文） */
  part: string;
  persona: LessonPersona;
  text: string;
}

/** 一节讲评课全部要过词表的文字：台词稿每一行（字幕台词与预制答疑，按角色选词表）与板书原文（按老师） */
export function lessonTexts(key: string, l: Pick<Lesson, "dsl" | "script">): LessonText[] {
  const lines = l.script.lines.map((x) => ({ lesson: key, part: `line:${x.id}`, persona: LESSON_PERSONAS[x.role], text: x.text }));
  const board = dslTexts(l.dsl).map((text, i) => ({ lesson: key, part: `board:${i}`, persona: LESSON_PERSONAS.teacher, text }));
  return [...lines, ...board];
}

export interface LessonScanRecord {
  check: "C9";
  mode: "strict";
  lexicon_version: string;
  guard_version: string;
  /** 每套词表扫了多少条文字 */
  texts: Record<LessonPersona, number>;
}

export interface LessonRelease {
  /** 审核记录文件（aya-lesson-reviews/1）规范化后的 sha256 */
  reviews_digest: string;
  scan: LessonScanRecord;
}

/** 按角色数文字条数 */
export function countTexts(texts: readonly LessonText[]): Record<LessonPersona, number> {
  const out = { aya: 0, diandian: 0 } as Record<LessonPersona, number>;
  for (const t of texts) out[t.persona] += 1;
  return out;
}

/**
 * 校验已发布的讲评课内容包并建索引。除了格式 1 的全部规则，还要发布记录与内容对得上：
 * 每节的 digest 与按内容重算的一致；C9 记录是严格模式、有词表版本，按角色的文字条数与重算的一致。任何一项对不上都抛错。
 */
export function indexReleasedLessons(raw: unknown, content: LoadedPack, version = "inline"): LoadedLessons {
  const doc = raw as { format?: unknown; lessons?: unknown; release?: unknown };
  if (!doc || typeof doc !== "object" || doc.format !== LESSON_RELEASE_FORMAT || !Array.isArray(doc.lessons)) {
    throw new Error(`已发布的讲评课内容包不合格：format 应为 ${LESSON_RELEASE_FORMAT}，lessons 应为数组`);
  }
  const extraTop = Object.keys(doc).filter((k) => !["format", "lessons", "release"].includes(k));
  if (extraTop.length) throw new Error(`已发布的讲评课内容包：多余字段 ${extraTop.join(", ")}`);
  const rel = doc.release as Partial<LessonRelease> | undefined;
  const scan = rel?.scan as Partial<LessonScanRecord> | undefined;
  if (!rel || typeof rel !== "object" || Object.keys(rel).sort().join() !== "reviews_digest,scan" ||
      typeof rel.reviews_digest !== "string" || !/^sha256:[0-9a-f]{64}$/.test(rel.reviews_digest)) {
    throw new Error("已发布的讲评课内容包：缺少发布记录（release.reviews_digest、release.scan）");
  }
  if (!scan || typeof scan !== "object" || Object.keys(scan).sort().join() !== "check,guard_version,lexicon_version,mode,texts" ||
      scan.check !== "C9" || scan.mode !== "strict" || typeof scan.lexicon_version !== "string" || scan.lexicon_version === "" ||
      typeof scan.guard_version !== "string" || scan.guard_version === "" || !scan.texts || typeof scan.texts !== "object") {
    throw new Error("已发布的讲评课内容包：C9 记录不合格（须为严格模式并带词表版本）");
  }
  const plain: Record<string, unknown>[] = [];
  for (const [i, l] of (doc.lessons as Record<string, unknown>[]).entries()) {
    if (!l || typeof l !== "object") throw new Error(`lessons[${i}]：不是对象`);
    const { digest, ...rest } = l;
    if (typeof digest !== "string") throw new Error(`lessons[${i}]：缺少 digest`);
    if (lessonDigest(rest) !== digest) throw new Error(`lessons[${i}]：digest 与内容不一致（发布后被改过）`);
    plain.push(rest);
  }
  const loaded = indexLessons({ format: LESSON_PACK_FORMAT, lessons: plain }, content, version);
  const texts = [...loaded.byRef].flatMap(([k, l]) => lessonTexts(k, l));
  const want = countTexts(texts);
  const got = scan.texts as Record<string, unknown>;
  if (Object.keys(got).sort().join() !== Object.keys(want).sort().join() ||
      (Object.keys(want) as LessonPersona[]).some((p) => got[p] !== want[p])) {
    throw new Error("已发布的讲评课内容包：C9 记录的文字条数与内容对不上（内容改过而没有重新检查）");
  }
  return loaded;
}

/**
 * 启动时读讲评课。
 * - 设了 AYA_LESSON_PACK（合成讲评课，格式 1）：只在候选版开关打开时读（与替代内容包同口径），开关没开却设了就拒绝启动；
 * - 否则读已发布的讲评课内容包（content/lessons.json，格式 2），不需要候选版开关；
 * - 两者都没有 → 没有讲评课（NO_LESSONS）。
 */
export function loadLessonsFrom(path: string | undefined, content: LoadedPack, candidateAllowed: boolean,
                                published: string = PUBLISHED_LESSONS): LoadedLessons {
  if (!path) {
    if (!existsSync(published)) return NO_LESSONS;
    const text = readFileSync(published, "utf8");
    return indexReleasedLessons(JSON.parse(text), content, `r${createHash("sha256").update(text).digest("hex").slice(0, 8)}`);
  }
  if (!candidateAllowed) {
    throw new Error(`讲评课内容包（${LESSON_PACK_ENV}）签字前只给测试与演练，需同时打开候选版开关（STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1）`);
  }
  const text = readFileSync(path, "utf8");
  return indexLessons(JSON.parse(text), content, createHash("sha256").update(text).digest("hex").slice(0, 8));
}
