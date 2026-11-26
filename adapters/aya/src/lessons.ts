// 讲评课内容包（specs/stage-point-to-ask-answer PR ③a）：阿鸦按契约 TeachRequest.question 的 lesson_ref 取已发布的讲解 DSL 与台词稿。
// 讲评课是学科内容（公共层），由教研与人设审核后随内容包发布；请求不携带讲解内容（契约 v1.1 草稿内追加）。
// - 格式 aya-lesson-pack/1：{ format, lessons: [{ lesson_id, lesson_version, skill_node, item_ref?, dsl, script }] }；
// - 讲解 DSL 与台词稿用 packages/stage 的同一套入口读入（migrateStageDsl、migrateStageScript：旧版本沿迁移阶梯升级、文字逐字核对），
//   不另写一份校验；任何一节不合格就整包拒绝（启动失败），不跳过；
// - skill_node 必须是内容包里的节点，item_ref（若有）必须是内容包里的题，且讲评课只讲这道题；
// - 真实讲评课还没有：仓库里不带讲评课内容包，缺省为空（带 question 的请求一律 404）。合成讲评课内容包只给测试与演练（AYA_LESSON_PACK，
//   与替代内容包同样只在候选版开关打开时读）。
// 本模块只读文件、不联网、不写任何东西；讲评课文字不进日志。
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import {
  migrateStageDsl, migrateStageScript, type ValidatedStageDsl, type ValidatedStageScript,
} from "../../../packages/stage/src/index.ts";
import type { LoadedPack } from "./content.ts";

export const LESSON_PACK_FORMAT = "aya-lesson-pack/1";
export const LESSON_PACK_ENV = "AYA_LESSON_PACK";
const LESSON_ID_RE = /^[a-z][a-z0-9_.-]{0,63}$/;
const SEMVER_RE = /^\d+\.\d+\.\d+$/;
const LOCAL_REF_RE = /^aya:([A-Za-z0-9][A-Za-z0-9_.-]{0,63})$/;

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
    const extra = Object.keys(l).filter((k) => !["lesson_id", "lesson_version", "skill_node", "item_ref", "dsl", "script"].includes(k));
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
    byRef.set(key, Object.freeze({ lesson_id: id, lesson_version: ver, skillNode: nodeId, ...(itemId ? { itemId } : {}), dsl: d.dsl, script: s.script }));
  }
  return Object.freeze({ byRef, version });
}

/**
 * 启动时读讲评课内容包。不设路径 → 没有讲评课（NO_LESSONS）。设了路径只在候选版开关打开时读（与替代内容包同口径：
 * 合成讲评课只给测试与演练），开关没开却设了就拒绝启动。
 */
export function loadLessonsFrom(path: string | undefined, content: LoadedPack, candidateAllowed: boolean): LoadedLessons {
  if (!path) return NO_LESSONS;
  if (!candidateAllowed) {
    throw new Error(`讲评课内容包（${LESSON_PACK_ENV}）签字前只给测试与演练，需同时打开候选版开关（STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1）`);
  }
  const text = readFileSync(path, "utf8");
  return indexLessons(JSON.parse(text), content, createHash("sha256").update(text).digest("hex").slice(0, 8));
}
