// 生成已发布的讲评课内容包 content/lessons.json（specs/stage-lesson-staging PR 1；做法见 src/pack/lesson-release.ts）。
//
// 用法（在 adapters/aya 下）：
//   npm run build:lessons -- --draft <讲评课草稿 aya-lesson-pack/1> --reviews <审核记录 aya-lesson-reviews/1> [--out <文件>] [--report <文件>]
// 草稿与审核记录不进仓库以外的地方、不联网；C9 词表检查调用本仓 packages/personas 的 guard（需要 Python，PYTHON 可指定解释器）。
// 草稿不合格、审核记录与草稿对不上、C9 有命中：不写文件，退出码 1。报告（--report）只有讲评课编号、结论、文字位置与规则编号，没有讲解文字。
// 同一草稿、同一审核记录、同一词表：两次生成逐字节相同。
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { loadPack } from "../src/content.ts";
import { indexReleasedLessons, PUBLISHED_LESSONS } from "../src/lessons.ts";
import { buildLessonRelease, serializeLessonPack, type LessonReleaseReport } from "../src/pack/lesson-release.ts";

const { values } = parseArgs({
  options: { draft: { type: "string" }, reviews: { type: "string" }, out: { type: "string" }, report: { type: "string" } },
  strict: true,
});
if (!values.draft || !values.reviews) {
  console.error("build-lesson-pack: 需要 --draft <讲评课草稿> 与 --reviews <审核记录>");
  process.exit(1);
}
const out = values.out ? resolve(values.out) : PUBLISHED_LESSONS;
const writeReport = (r: LessonReleaseReport | undefined) => {
  if (values.report && r) writeFileSync(resolve(values.report), JSON.stringify(r, null, 1) + "\n");
};
try {
  const content = loadPack();
  const read = (p: string) => JSON.parse(readFileSync(resolve(p), "utf8")) as unknown;
  const { pack, report } = buildLessonRelease(read(values.draft), read(values.reviews), content);
  const text = serializeLessonPack(pack);
  indexReleasedLessons(JSON.parse(text), content);  // 写出前按加载时同样的规则校验一遍
  writeFileSync(out, text);
  writeReport(report);
  const count = (o: string) => report.lessons.filter((l) => l.outcome === o).length;
  console.log(`build-lesson-pack: 发布 ${pack.lessons.length} 节讲评课 → ${out}`);
  console.log(`build-lesson-pack: 未发布：没有审核 ${count("not_reviewed")}、审核后内容改过 ${count("digest_mismatch")}、` +
              `教研退回 ${count("content_rejected")}、人设退回 ${count("persona_rejected")}`);
  console.log(`build-lesson-pack: C9 词表检查（严格，词表 ${report.lexicon_version}）阿鸦 ${report.texts.aya} 条、点点 ${report.texts.diandian} 条，没有命中`);
} catch (e) {
  writeReport((e as Error & { report?: LessonReleaseReport }).report);
  console.error(`build-lesson-pack: ${(e as Error).message}`);
  process.exit(1);
}
