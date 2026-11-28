// 讲评课内容包的发布（specs/stage-lesson-staging PR 1；规格 `stage-point-to-ask-answer` lessons Q6、`stage-lesson-session` lessons R2）：
//   草稿（aya-lesson-pack/1）+ 审核记录（aya-lesson-reviews/1）→ 已发布的讲评课内容包（aya-lesson-pack/2，content/lessons.json）。
//
// 发布闸门（ADR 07 决定 5：AI 草稿 → 确定性检查 → 教研审核 → 版本发布），这里做其中由机器做的部分：
//   1. 确定性检查：草稿整包按 src/lessons.ts 的规则读入（packages/stage 的校验与文字安全、节点与题核对）；任何一节不合格整包失败；
//   2. 审核：只收教研（content_review）与人设（persona_review）都是 approved、且审核记录里的 digest 与现在按内容重算的一致的讲评课。
//      没有审核记录、被退回、审核之后内容又改过的，都不发布，在报告里列出原因（只有编号与原因，没有讲解文字）；
//      审核记录里有草稿中没有的讲评课 → 失败（记录与草稿对不上）。审核记录只有结论，不写审核人姓名（规则 8）；
//   3. C9 词表检查：已收的讲评课的台词稿每一行（字幕台词与预制答疑；老师用阿鸦的词表，点点用点点的）与板书原文（阿鸦）
//      逐条过 packages/personas 的 guard（与提示阶梯同一个入口，src/pack/hint-scan.ts 的 guardRunner）。只有严格模式：有命中就失败，
//      报告里只有讲评课编号、文字位置与规则编号，不回显原文。人设回归（evals/persona）不在这里，另起（规格第五节第 7 条）。
// 产物规范化序列化（键排序、按编号排序），同一草稿、同一审核记录、同一词表两次生成逐字节相同；不读时钟。
import { createHash } from "node:crypto";
import type { LoadedPack } from "../content.ts";
import {
  countTexts, indexLessons, LESSON_PACK_FORMAT, LESSON_RELEASE_FORMAT, lessonDigest, lessonKey, lessonTexts,
  type LessonPersona, type LessonRelease, type LessonText,
} from "../lessons.ts";
import { guardRunner, type GuardRunner, type GuardScanOutput } from "./hint-scan.ts";

export const LESSON_REVIEWS_FORMAT = "aya-lesson-reviews/1";
export const LESSON_RELEASE_REPORT_FORMAT = "aya-lesson-release-report/1";
const VERDICTS = ["approved", "rejected"] as const;
type Verdict = (typeof VERDICTS)[number];

export interface LessonReview {
  lesson_id: string;
  lesson_version: string;
  /** 审核时那一版内容的摘要（src/lessons.ts lessonDigest） */
  digest: string;
  content_review: Verdict;
  persona_review: Verdict;
}

export interface LessonReviewFile {
  format: typeof LESSON_REVIEWS_FORMAT;
  reviews: LessonReview[];
}

export type LessonOutcome = "published" | "not_reviewed" | "digest_mismatch" | "content_rejected" | "persona_rejected";

export interface LessonScanHit {
  lesson: string;
  part: string;
  persona: LessonPersona;
  rule_id: string;
  class: string;
}

/** 构建报告（打印或写文件；不进内容包，不含讲解文字） */
export interface LessonReleaseReport {
  format: typeof LESSON_RELEASE_REPORT_FORMAT;
  lessons: { lesson: string; outcome: LessonOutcome }[];
  lexicon_version: string;
  guard_version: string;
  texts: Record<LessonPersona, number>;
  hits: LessonScanHit[];
}

export interface ReleasedLessonPack {
  format: typeof LESSON_RELEASE_FORMAT;
  lessons: Record<string, unknown>[];
  release: LessonRelease;
}

/** 按角色取扫描器；缺省是本仓 packages/personas 的 guard */
export type GuardRunnerFor = (persona: LessonPersona) => GuardRunner;
export const defaultRunnerFor: GuardRunnerFor = (persona) => guardRunner({ persona });

const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;

/** 严格解析审核记录：字段齐全、结论只认 approved / rejected、同一节课不重复 */
export function parseLessonReviews(raw: unknown): LessonReviewFile {
  const doc = raw as { format?: unknown; reviews?: unknown };
  if (!doc || typeof doc !== "object" || doc.format !== LESSON_REVIEWS_FORMAT || !Array.isArray(doc.reviews)) {
    throw new Error(`审核记录不合格：format 应为 ${LESSON_REVIEWS_FORMAT}，reviews 应为数组`);
  }
  if (Object.keys(doc).sort().join() !== "format,reviews") throw new Error("审核记录：多余字段");
  const seen = new Set<string>();
  const reviews = (doc.reviews as Record<string, unknown>[]).map((r, i) => {
    const where = `reviews[${i}]`;
    if (!r || typeof r !== "object") throw new Error(`${where}：不是对象`);
    if (Object.keys(r).sort().join() !== "content_review,digest,lesson_id,lesson_version,persona_review") {
      throw new Error(`${where}：字段应为 lesson_id、lesson_version、digest、content_review、persona_review`);
    }
    const { lesson_id: id, lesson_version: ver, digest, content_review: c, persona_review: p } = r;
    if (typeof id !== "string" || typeof ver !== "string" || typeof digest !== "string" || !DIGEST_RE.test(digest)) {
      throw new Error(`${where}：编号、版本或 digest 不合格`);
    }
    if (!VERDICTS.includes(c as Verdict) || !VERDICTS.includes(p as Verdict)) throw new Error(`${where}：审核结论只能是 approved 或 rejected`);
    const key = lessonKey(id, ver);
    if (seen.has(key)) throw new Error(`${where}：${key} 重复`);
    seen.add(key);
    return { lesson_id: id, lesson_version: ver, digest, content_review: c as Verdict, persona_review: p as Verdict };
  });
  return { format: LESSON_REVIEWS_FORMAT, reviews };
}

/** 审核记录规范化后的摘要（按编号排序，与文件里的顺序、空白无关） */
export function reviewsDigest(file: LessonReviewFile): string {
  const rows = [...file.reviews]
    .sort((a, b) => (lessonKey(a.lesson_id, a.lesson_version) < lessonKey(b.lesson_id, b.lesson_version) ? -1 : 1))
    .map((r) => [r.lesson_id, r.lesson_version, r.digest, r.content_review, r.persona_review]);
  return `sha256:${createHash("sha256").update(JSON.stringify({ format: file.format, rows }), "utf8").digest("hex")}`;
}

/** 一节讲评课该不该发布 */
function outcomeOf(review: LessonReview | undefined, digest: string): LessonOutcome {
  if (!review) return "not_reviewed";
  if (review.digest !== digest) return "digest_mismatch";
  if (review.content_review !== "approved") return "content_rejected";
  if (review.persona_review !== "approved") return "persona_rejected";
  return "published";
}

/** 逐套词表扫描；结果条数、顺序、角色、词表版本对不上一律抛错（fail closed） */
function scanLessonTexts(texts: LessonText[], runnerFor: GuardRunnerFor): { hits: LessonScanHit[]; lexicon: string; guard: string } {
  const hits: LessonScanHit[] = [];
  const versions = new Set<string>();
  let lexicon = "", guard = "";
  // 阿鸦总要跑一次（哪怕没有文字），以便记下词表版本
  for (const persona of ["aya", "diandian"] as const) {
    const mine = texts.filter((t) => t.persona === persona);
    if (mine.length === 0 && persona !== "aya") continue;
    const out: GuardScanOutput = runnerFor(persona)(mine.map((t) => t.text));
    if (out.persona_id !== persona) throw new Error(`C9 词表检查的角色不是 ${persona}`);
    if (!Array.isArray(out.results) || out.results.length !== mine.length) throw new Error(`C9 词表检查（${persona}）的结果条数与文字条数不一致`);
    if (typeof out.lexicon_version !== "string" || out.lexicon_version === "" || typeof out.guard_version !== "string" || out.guard_version === "") {
      throw new Error("C9 词表检查没有给出词表版本");
    }
    versions.add(`${out.lexicon_version}|${out.guard_version}`);
    [lexicon, guard] = [out.lexicon_version, out.guard_version];
    out.results.forEach((r, i) => {
      if (r.source !== `stdin[${i}]`) throw new Error("C9 词表检查的结果顺序与输入不一致");
      const t = mine[i]!;
      for (const h of r.hits) hits.push({ lesson: t.lesson, part: t.part, persona, rule_id: h.rule_id, class: h.class });
    });
  }
  if (versions.size !== 1) throw new Error("C9 词表检查：两套词表的版本不一致");
  hits.sort((a, b) => (`${a.lesson} ${a.part} ${a.rule_id}` < `${b.lesson} ${b.part} ${b.rule_id}` ? -1 : 1));
  return { hits, lexicon, guard };
}

/**
 * 从草稿与审核记录生成已发布的讲评课内容包。草稿不合格、审核记录不合格或与草稿对不上、C9 有命中，都抛错（不产出内容包）。
 * 返回内容包与报告；内容包里只有通过审核的讲评课。
 */
export function buildLessonRelease(draft: unknown, reviewsRaw: unknown, content: LoadedPack,
                                   runnerFor: GuardRunnerFor = defaultRunnerFor): { pack: ReleasedLessonPack; report: LessonReleaseReport } {
  const doc = draft as { format?: unknown; lessons?: unknown };
  if (!doc || doc.format !== LESSON_PACK_FORMAT || !Array.isArray(doc.lessons)) {
    throw new Error(`讲评课草稿不合格：format 应为 ${LESSON_PACK_FORMAT}，lessons 应为数组`);
  }
  const loaded = indexLessons(doc, content, "draft");  // 确定性检查：整包按加载规则读入，任何一节不合格就失败
  const reviews = parseLessonReviews(reviewsRaw);
  const byKey = new Map(reviews.reviews.map((r) => [lessonKey(r.lesson_id, r.lesson_version), r]));
  const rawByKey = new Map((doc.lessons as Record<string, unknown>[]).map((l) => [lessonKey(String(l.lesson_id), String(l.lesson_version)), l]));
  const unknown = [...byKey.keys()].filter((k) => !rawByKey.has(k));
  if (unknown.length) throw new Error(`审核记录里有草稿中没有的讲评课：${unknown.sort().join("、")}`);

  const keys = [...rawByKey.keys()].sort();
  const outcomes = keys.map((k) => ({ lesson: k, outcome: outcomeOf(byKey.get(k), lessonDigest(rawByKey.get(k)!)) }));
  const published = outcomes.filter((o) => o.outcome === "published").map((o) => o.lesson);
  const texts = published.flatMap((k) => lessonTexts(k, loaded.byRef.get(k)!));
  const { hits, lexicon, guard } = scanLessonTexts(texts, runnerFor);
  const report: LessonReleaseReport = {
    format: LESSON_RELEASE_REPORT_FORMAT, lessons: outcomes, lexicon_version: lexicon, guard_version: guard, texts: countTexts(texts), hits,
  };
  if (hits.length > 0) {
    const list = hits.slice(0, 50).map((h) => `${h.lesson} ${h.part} ${h.rule_id}`);
    const err = new Error(`C9 词表检查（严格模式）：${new Set(hits.map((h) => h.lesson)).size} 节讲评课的文字命中禁语：\n  ${list.join("\n  ")}`);
    (err as Error & { report?: LessonReleaseReport }).report = report;
    throw err;
  }
  const lessons = published.map((k) => {
    const raw = rawByKey.get(k)!;
    const picked = Object.fromEntries(Object.entries(raw).filter(([f]) => f !== "digest"));
    return { ...picked, digest: lessonDigest(raw) };
  });
  const pack: ReleasedLessonPack = {
    format: LESSON_RELEASE_FORMAT,
    lessons,
    release: {
      reviews_digest: reviewsDigest(reviews),
      scan: { check: "C9", mode: "strict", lexicon_version: lexicon, guard_version: guard, texts: report.texts },
    },
  };
  return { pack, report };
}

/** 规范化序列化：键排序、一格缩进、末尾换行（与 content/pack.json 的 serializePack 同一口径） */
export function serializeLessonPack(pack: ReleasedLessonPack): string {
  const sortKeys = (v: unknown): unknown =>
    Array.isArray(v) ? v.map(sortKeys)
      : v && typeof v === "object" ? Object.fromEntries(Object.keys(v as object).sort().map((k) => [k, sortKeys((v as Record<string, unknown>)[k])]))
        : v;
  return JSON.stringify(sortKeys(pack), null, 1) + "\n";
}
