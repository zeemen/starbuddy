// 章节 → 六段标识（KGE-02 设计要点 1；spec 待人确认 #2）。映射表在 config/aya-chapter-segments.yaml，由教研核对。
// 六段 = region / exam_year / grade / textbook_version / subject / content_version（迁移 0003 platform.curriculum_version）；
// 册次（term）不是六段之一，但登记与核对时要用，一并输出。
import { parseYaml, type YamlMap, type YamlValue } from "./yaml.ts";

export interface SixSegments {
  region: string;
  exam_year: number;
  grade: 7 | 8 | 9;
  textbook_version: string;
  subject: "aya";
  content_version: string;
}
export interface ChapterSegment {
  chapter_code: string; // legacy 专题代码，如 fun
  label: string; // legacy chapterLabel 原文，用于核对
  term: "upper" | "lower";
  chapter_no: number;
  segments: SixSegments;
}
export interface SegmentConfig {
  chapters: Map<string, ChapterSegment>;
}

export class SegmentConfigError extends Error {}

const REGION = /^[a-z][a-z0-9-]{1,31}$/;
const TEXTBOOK = /^[a-z0-9][a-z0-9-]{1,31}$/;
const SEMVER = /^\d+\.\d+\.\d+$/;
const CHAPTER = /^[a-z]{2,8}$/;

const asMap = (v: YamlValue | undefined, where: string): YamlMap => {
  if (v === null || v === undefined || typeof v !== "object") throw new SegmentConfigError(`${where} 必须是映射`);
  return v;
};
function str(v: YamlValue | undefined, where: string, re: RegExp): string {
  const s = typeof v === "number" ? String(v) : v;
  if (typeof s !== "string" || !re.test(s)) throw new SegmentConfigError(`${where} 不合法：${String(v)}`);
  return s;
}
function int(v: YamlValue | undefined, where: string, lo: number, hi: number): number {
  if (typeof v !== "number" || !Number.isInteger(v) || v < lo || v > hi) throw new SegmentConfigError(`${where} 必须是 ${lo}–${hi} 的整数`);
  return v;
}
function only(m: YamlMap, keys: string[], where: string): void {
  for (const k of Object.keys(m)) if (!keys.includes(k)) throw new SegmentConfigError(`${where}：不认识的键 ${k}`);
}

/** 解析并校验映射表；值域与 platform.curriculum_version 的 CHECK 一致，写错在生成时就报，不等到库里 */
export function parseSegmentConfig(text: string): SegmentConfig {
  const root = parseYaml(text);
  only(root, ["version", "defaults", "chapters"], "映射表");
  if (root.version !== 1) throw new SegmentConfigError("映射表 version 必须是 1");
  const d = asMap(root.defaults, "defaults");
  only(d, ["region", "exam_year", "subject", "content_version"], "defaults");
  const region = str(d.region, "defaults.region", REGION);
  const examYear = int(d.exam_year, "defaults.exam_year", 2026, 2100);
  if (d.subject !== "aya") throw new SegmentConfigError("defaults.subject 必须是 aya");
  const contentVersion = str(d.content_version, "defaults.content_version", SEMVER);
  const chapters = new Map<string, ChapterSegment>();
  for (const [code, raw] of Object.entries(asMap(root.chapters, "chapters"))) {
    const where = `chapters.${code}`;
    if (!CHAPTER.test(code)) throw new SegmentConfigError(`${where}：章节代码必须是 2–8 个小写字母`);
    const c = asMap(raw, where);
    only(c, ["label", "grade", "term", "chapter", "textbook_version", "region", "exam_year", "content_version"], where);
    if (typeof c.label !== "string" || c.label.length === 0 || c.label.length > 64) throw new SegmentConfigError(`${where}.label 必须是 1–64 字`);
    if (c.term !== "upper" && c.term !== "lower") throw new SegmentConfigError(`${where}.term 必须是 upper 或 lower`);
    chapters.set(code, {
      chapter_code: code,
      label: c.label,
      term: c.term,
      chapter_no: int(c.chapter, `${where}.chapter`, 1, 30),
      segments: {
        region: c.region === undefined ? region : str(c.region, `${where}.region`, REGION),
        exam_year: c.exam_year === undefined ? examYear : int(c.exam_year, `${where}.exam_year`, 2026, 2100),
        grade: int(c.grade, `${where}.grade`, 7, 9) as 7 | 8 | 9,
        textbook_version: str(c.textbook_version, `${where}.textbook_version`, TEXTBOOK),
        subject: "aya",
        content_version: c.content_version === undefined ? contentVersion : str(c.content_version, `${where}.content_version`, SEMVER),
      },
    });
  }
  return { chapters };
}

/** “首次出现”的排序键：年级、册次（上先于下）、章号、章节代码 */
export function firstOccurrenceKey(c: ChapterSegment): string {
  return `${c.segments.grade}|${c.term === "upper" ? 0 : 1}|${String(c.chapter_no).padStart(2, "0")}|${c.chapter_code}`;
}
