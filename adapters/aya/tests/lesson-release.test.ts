// 讲评课内容包的发布与加载（specs/stage-lesson-staging PR 1）：草稿 + 审核记录 → 已发布的讲评课内容包（aya-lesson-pack/2）。
// 全部是合成数据（规则 8）：讲评课、台词与审核结论都是合成的；题目与节点取自 legacy 内容包（学科内容，不含学生数据）。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  indexReleasedLessons, LESSON_PACK_FORMAT, LESSON_RELEASE_FORMAT, lessonDigest, lessonTexts, loadLessonsFrom, NO_LESSONS,
} from "../src/lessons.ts";
import type { GuardScanOutput } from "../src/pack/hint-scan.ts";
import {
  buildLessonRelease, LESSON_REVIEWS_FORMAT, parseLessonReviews, reviewsDigest, serializeLessonPack, type GuardRunnerFor,
} from "../src/pack/lesson-release.ts";
import { realPack } from "./helpers.ts";

const ITEM = realPack.pack.items.find((i) => i.format === "fill")!;
const NODE = ITEM.mainNodes[0]!;
const BAD = "（合成）含禁语-91f0";  // 假扫描器把含这个标记的文字判为命中

const lesson = (id: string, opts: { line2?: string; buddy?: string; board?: string } = {}) => ({
  lesson_id: id, lesson_version: "0.1.0", skill_node: `aya:${NODE}`, item_ref: `aya:${ITEM.id}`,
  dsl: {
    dsl_version: "0.3.0", title: "（合成）讲评课", canvas: { width: 960, height: 440 }, roles: ["teacher", "buddy"], narrated: true,
    steps: [
      { id: "s1", say: "l1", actions: [{ type: "speaker", role: "teacher" }, { type: "write_text", id: "t1", x: 40, y: 64, text: opts.board ?? "（合成）第一行" }] },
      { id: "s2", say: "l2", actions: [{ type: "speaker", role: "buddy" }] },
    ],
  },
  script: {
    dsl_version: "0.3.0",
    lines: [
      { id: "l1", role: "teacher", text: opts.line2 ?? "（合成台词）先看第一行。" },
      { id: "l2", role: "buddy", text: opts.buddy ?? "（合成台词）我也来看看。" },
      { id: "a1", role: "teacher", text: "（合成答疑）因为这样写清楚。" },
    ],
    answers: [{ step: "s1", intent: "why_step", line: "a1" }],
  },
});
const draftOf = (...lessons: ReturnType<typeof lesson>[]) => ({ format: LESSON_PACK_FORMAT, lessons });
const review = (l: ReturnType<typeof lesson>, c = "approved", p = "approved", digest = lessonDigest(l)) => ({
  lesson_id: l.lesson_id, lesson_version: l.lesson_version, digest, content_review: c, persona_review: p,
});
const reviewsOf = (...reviews: ReturnType<typeof review>[]) => ({ format: LESSON_REVIEWS_FORMAT, reviews });

/** 假扫描器：记下每套词表收到的文字；含 BAD 的判为命中 E-99 */
function fakeRunner(seen: Record<string, string[]> = {}, over: Partial<GuardScanOutput> = {}): GuardRunnerFor {
  return (persona) => (texts) => {
    (seen[persona] ??= []).push(...texts);
    return {
      lexicon_version: "0.3.0", guard_version: "0.1.0", persona_id: persona,
      results: texts.map((t, i) => ({ source: `stdin[${i}]`, hits: t.includes(BAD) ? [{ class: "E", rule_id: "E-99" }] : [] })),
      ...over,
    };
  };
}

test("只发布教研与人设都通过、且审核的就是这一版内容的讲评课；其余在报告里列出原因", () => {
  const a = lesson("syn.lesson.a"), b = lesson("syn.lesson.b"), c = lesson("syn.lesson.c"), d = lesson("syn.lesson.d"), e = lesson("syn.lesson.e");
  const old = lesson("syn.lesson.c", { line2: "（合成台词）审核时的旧台词。" });
  const { pack, report } = buildLessonRelease(draftOf(a, b, c, d, e),
    reviewsOf(review(a), review(c, "approved", "approved", lessonDigest(old)), review(d, "rejected"), review(e, "approved", "rejected")),
    realPack, fakeRunner());
  assert.deepEqual(report.lessons, [
    { lesson: "syn.lesson.a@0.1.0", outcome: "published" },
    { lesson: "syn.lesson.b@0.1.0", outcome: "not_reviewed" },
    { lesson: "syn.lesson.c@0.1.0", outcome: "digest_mismatch" },
    { lesson: "syn.lesson.d@0.1.0", outcome: "content_rejected" },
    { lesson: "syn.lesson.e@0.1.0", outcome: "persona_rejected" },
  ]);
  assert.equal(pack.format, LESSON_RELEASE_FORMAT);
  assert.deepEqual(pack.lessons.map((l) => l.lesson_id), ["syn.lesson.a"]);
  assert.equal(pack.lessons[0]!.digest, lessonDigest(a));
  assert.deepEqual(pack.release.scan, { check: "C9", mode: "strict", lexicon_version: "0.3.0", guard_version: "0.1.0", texts: { aya: 4, diandian: 1 } });
  // 报告不含讲解文字
  assert.doesNotMatch(JSON.stringify(report), /合成台词|合成答疑|第一行/);
  // 发布包按加载规则读得回来
  const loaded = indexReleasedLessons(JSON.parse(serializeLessonPack(pack)), realPack, "r0000000");
  assert.deepEqual([...loaded.byRef.keys()], ["syn.lesson.a@0.1.0"]);
});

test("文字按谁说的过哪套词表：老师台词、答疑与板书用阿鸦，点点台词用点点；有命中就失败，只报编号、位置与规则", () => {
  const seen: Record<string, string[]> = {};
  const a = lesson("syn.lesson.a");
  buildLessonRelease(draftOf(a), reviewsOf(review(a)), realPack, fakeRunner(seen));
  assert.deepEqual(seen.aya, ["（合成台词）先看第一行。", "（合成答疑）因为这样写清楚。", "（合成）讲评课", "（合成）第一行"]);
  assert.deepEqual(seen.diandian, ["（合成台词）我也来看看。"]);
  for (const [opts, part] of [[{ buddy: `${BAD}。` }, "line:l2"], [{ board: BAD }, "board:1"], [{ line2: BAD }, "line:l1"]] as const) {
    const l = lesson("syn.lesson.bad", opts);
    assert.throws(() => buildLessonRelease(draftOf(l), reviewsOf(review(l)), realPack, fakeRunner()), (e: Error & { report?: { hits: unknown[] } }) => {
      assert.match(e.message, new RegExp(`严格模式[\\s\\S]*syn\\.lesson\\.bad@0\\.1\\.0 ${part} E-99`));
      assert.doesNotMatch(e.message, /含禁语/);
      assert.equal(e.report?.hits.length, 1);
      return true;
    }, part);
  }
  // 没通过审核的讲评课不扫（也不因它失败）
  const bad = lesson("syn.lesson.bad", { line2: BAD });
  assert.equal(buildLessonRelease(draftOf(a, bad), reviewsOf(review(a)), realPack, fakeRunner()).pack.lessons.length, 1);
});

test("扫描器的结果对不上一律失败（不当成没有命中）", () => {
  const a = lesson("syn.lesson.a");
  const go = (r: GuardRunnerFor) => () => buildLessonRelease(draftOf(a), reviewsOf(review(a)), realPack, r);
  assert.throws(go(fakeRunner({}, { persona_id: "miaobi" })), /角色不是/);
  assert.throws(go(fakeRunner({}, { results: [] })), /条数/);
  assert.throws(go(fakeRunner({}, { lexicon_version: "" })), /词表版本/);
  assert.throws(go((p) => (t) => ({ ...fakeRunner()(p)(t), results: fakeRunner()(p)(t).results.map((r) => ({ ...r, source: "x" })) })), /顺序/);
  assert.throws(go((p) => (t) => ({ ...fakeRunner()(p)(t), lexicon_version: p === "aya" ? "0.3.0" : "0.4.0" })), /版本不一致/);
});

test("草稿与审核记录：不合格就失败；审核记录里有草稿中没有的讲评课也失败", () => {
  const a = lesson("syn.lesson.a");
  assert.throws(() => buildLessonRelease({ format: "x", lessons: [] }, reviewsOf(), realPack, fakeRunner()), /草稿不合格/);
  assert.throws(() => buildLessonRelease(draftOf({ ...a, skill_node: "aya:NO-SUCH" }), reviewsOf(), realPack, fakeRunner()), /skill_node/);
  assert.throws(() => buildLessonRelease(draftOf(a), reviewsOf(review(lesson("syn.lesson.z"))), realPack, fakeRunner()), /草稿中没有.*syn\.lesson\.z/);
  for (const bad of [{ format: "x", reviews: [] }, { ...reviewsOf(), extra: 1 }, reviewsOf({ ...review(a), content_review: "ok" }),
                     reviewsOf({ ...review(a), digest: "sha256:abc" }), reviewsOf(review(a), review(a)), reviewsOf({ ...review(a), reviewer: "某老师" } as never)]) {
    assert.throws(() => parseLessonReviews(bad), /审核记录|reviews\[/, JSON.stringify(bad).slice(0, 80));
  }
});

test("同一草稿、同一审核记录、同一词表两次生成逐字节相同；审核记录的顺序不影响摘要", () => {
  const a = lesson("syn.lesson.a"), b = lesson("syn.lesson.b");
  const one = serializeLessonPack(buildLessonRelease(draftOf(b, a), reviewsOf(review(a), review(b)), realPack, fakeRunner()).pack);
  const two = serializeLessonPack(buildLessonRelease(draftOf(a, b), reviewsOf(review(b), review(a)), realPack, fakeRunner()).pack);
  assert.equal(one, two);
  assert.equal(reviewsDigest(parseLessonReviews(reviewsOf(review(a), review(b)))), reviewsDigest(parseLessonReviews(reviewsOf(review(b), review(a)))));
  assert.notEqual(reviewsDigest(parseLessonReviews(reviewsOf(review(a)))), reviewsDigest(parseLessonReviews(reviewsOf(review(a, "approved", "rejected")))));
});

test("加载已发布的包：发布后改过内容、文字条数对不上、不是严格模式、缺发布记录、格式不对，一律拒绝", () => {
  const a = lesson("syn.lesson.a");
  const good = JSON.parse(serializeLessonPack(buildLessonRelease(draftOf(a), reviewsOf(review(a)), realPack, fakeRunner()).pack));
  const clone = () => JSON.parse(JSON.stringify(good));
  const load = (doc: unknown) => () => indexReleasedLessons(doc, realPack);
  assert.doesNotThrow(load(good));
  const edited = clone(); edited.lessons[0].script.lines[0].text = "（合成台词）发布后改的。";
  assert.throws(load(edited), /digest 与内容不一致/);
  // 改了内容还重算了 digest：多出一条答疑，文字条数对不上
  const added = clone(); added.lessons[0].script.lines.push({ id: "l9", role: "teacher", text: "（合成答疑）多一行。" });
  added.lessons[0].script.answers.push({ step: "s1", intent: "where_from", line: "l9" });
  delete added.lessons[0].digest; added.lessons[0].digest = lessonDigest(added.lessons[0]);
  assert.throws(load(added), /文字条数/);
  for (const [mutate, re] of [
    [(d: any) => { d.release.scan.mode = "flag"; }, /严格模式/],
    [(d: any) => { delete d.release; }, /发布记录/],
    [(d: any) => { d.release.reviews_digest = "x"; }, /发布记录/],
    [(d: any) => { d.release.scan.lexicon_version = ""; }, /词表版本/],
    [(d: any) => { d.format = LESSON_PACK_FORMAT; }, /format 应为/],
    [(d: any) => { d.extra = 1; }, /多余字段/],
    [(d: any) => { delete d.lessons[0].digest; }, /缺少 digest/],
    [(d: any) => { d.release.scan.texts = { aya: 3 }; }, /文字条数/],
  ] as const) {
    const d = clone(); mutate(d);
    assert.throws(load(d), re);
  }
});

test("启动时读哪一份：设了 AYA_LESSON_PACK 只认合成包（要候选版开关）；否则读已发布的包，不要开关；都没有为空", () => {
  const dir = mkdtempSync(join(tmpdir(), "aya-lessons-"));
  try {
    const a = lesson("syn.lesson.a");
    const published = join(dir, "lessons.json");
    const synthetic = join(dir, "synthetic.json");
    writeFileSync(published, serializeLessonPack(buildLessonRelease(draftOf(a), reviewsOf(review(a)), realPack, fakeRunner()).pack));
    writeFileSync(synthetic, JSON.stringify(draftOf(lesson("syn.lesson.s"))));
    assert.equal(loadLessonsFrom(undefined, realPack, false, join(dir, "none.json")), NO_LESSONS);
    const pub = loadLessonsFrom(undefined, realPack, false, published);
    assert.deepEqual([...pub.byRef.keys()], ["syn.lesson.a@0.1.0"]);
    assert.match(pub.version, /^r[0-9a-f]{8}$/);
    assert.throws(() => loadLessonsFrom(synthetic, realPack, false, published), /候选版开关/);
    assert.deepEqual([...loadLessonsFrom(synthetic, realPack, true, published).byRef.keys()], ["syn.lesson.s@0.1.0"]);
    // 已发布的路径上放了合成包（格式 1）：拒绝
    assert.throws(() => loadLessonsFrom(undefined, realPack, false, synthetic), /format 应为 aya-lesson-pack\/2/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  // 仓库里还没有真实讲评课，不带已发布的包
  assert.equal(loadLessonsFrom(undefined, realPack, false), NO_LESSONS);
});

test("文字清单覆盖台词稿每一行与板书原文", () => {
  const l = lesson("syn.lesson.a");
  const loaded = indexReleasedLessons(JSON.parse(serializeLessonPack(buildLessonRelease(draftOf(l), reviewsOf(review(l)), realPack, fakeRunner()).pack)), realPack);
  const texts = lessonTexts("k", loaded.byRef.get("syn.lesson.a@0.1.0")!);
  assert.deepEqual(texts.map((t) => `${t.part}:${t.persona}`), ["line:l1:aya", "line:l2:diandian", "line:a1:aya", "board:0:aya", "board:1:aya"]);
});

const HERE = fileURLToPath(new URL("..", import.meta.url));
const PY = process.env.PYTHON ?? "python3";
const guardOk = spawnSync(PY, ["-c", "import personas.guard"], { cwd: join(HERE, "../.."), env: { ...process.env, PYTHONPATH: join(HERE, "../../packages") } }).status === 0;
const skip = !guardOk && process.env.CONTRACT_STRICT !== "1" ? "没有可用的 Python 或 packages/personas" : false;

test("接真实词表：老师台词里的禁语被阿鸦的词表拦下；干净的讲评课能发布", { skip }, () => {
  const clean = lesson("syn.lesson.a");
  const { pack } = buildLessonRelease(draftOf(clean), reviewsOf(review(clean)), realPack);
  assert.equal(pack.lessons.length, 1);
  assert.equal(pack.release.scan.mode, "strict");
  const bad = lesson("syn.lesson.b", { line2: "（合成）这题你粗心了，再看看。" });
  assert.throws(() => buildLessonRelease(draftOf(bad), reviewsOf(review(bad)), realPack), /syn\.lesson\.b@0\.1\.0 line:l1 (F-aya-01|PLAT-01)/);
});
