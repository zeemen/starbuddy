// 讲评课白板上的结构化提问（specs/stage-point-to-ask-answer PR ③a；契约 v1.1 草稿内追加的 TeachRequest.question）：
// 阿鸦只从讲评课内容包取台词稿里的预制答疑，不调模型、不读对话与证据。全部是合成数据（规则 8）：讲评课、台词、答疑都是“（合成）”占位；
// 题目与节点取自 legacy 内容包（学科内容，不含学生数据）。
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { createAdapter, type LogRecord } from "../src/adapter.ts";
import { indexLessons, LESSON_PACK_FORMAT, lessonKey, loadLessonsFrom, NO_LESSONS } from "../src/lessons.ts";
import { LESSON_ANSWER_ALGORITHM, LESSON_ANSWER_HINT_LEVEL } from "../src/methods/teach.ts";
import { contextV1, contractV1, realPack, taxonomy, type Json } from "./helpers.ts";

// 一道答案不止一个字符的填空题：用来核对“提交前不露答案”
const ITEM = realPack.pack.items.find((i) => i.format === "fill" && i.answer.some((a) => a.replace(/\s/g, "").length >= 2))!;
const NODE = ITEM.mainNodes[0]!;
const ANSWER = ITEM.answer.find((a) => a.replace(/\s/g, "").length >= 2)!;
const SECRET = "（合成答疑）LESSON-ANSWER-SECRET-7c1d";

const DSL = {
  dsl_version: "0.3.0", title: "（合成）讲评课", canvas: { width: 960, height: 440 }, roles: ["teacher", "buddy"], narrated: true,
  slots: [{ id: "fig", kind: "item_figure", x: 600, y: 40, w: 320, h: 200 }],
  steps: [
    { id: "s1", say: "l1", actions: [{ type: "speaker", role: "teacher" }, { type: "write_text", id: "t1", x: 40, y: 64, text: "（合成）第一行" }] },
    { id: "s2", say: "l2", actions: [{ type: "write_text", id: "t2", x: 40, y: 140, text: "（合成）第二行" }] },
    { id: "s3", say: "l3", actions: [{ type: "speaker", role: "buddy" }, { type: "write_text", id: "t3", x: 40, y: 220, text: "（合成）第三行" }] },
    { id: "s4", actions: [{ type: "speaker", role: "none" }] },
  ],
};
const SCRIPT = {
  dsl_version: "0.3.0",
  lines: [
    { id: "l1", role: "teacher", text: "（合成台词）先看第一行。" },
    { id: "l2", role: "teacher", text: "（合成台词）再看第二行。" },
    { id: "l3", role: "buddy", text: "（合成台词）第三行。" },
    { id: "a1", role: "teacher", text: SECRET },
    { id: "a2", role: "teacher", text: `（合成答疑）结果是${ANSWER}。` },
    { id: "a3", role: "buddy", text: "（合成答疑）这是第三步。" },
  ],
  answers: [
    { step: "s1", intent: "why_step", line: "a1" },
    { step: "s2", intent: "where_from", line: "a2" },
    { step: "s3", intent: "what_is", line: "a3" },
  ],
};
const LESSON = { lesson_id: "syn.lesson.aya_001", lesson_version: "0.1.0", skill_node: `aya:${NODE}`, item_ref: `aya:${ITEM.id}`, dsl: DSL, script: SCRIPT };
const PACK = { format: LESSON_PACK_FORMAT, lessons: [LESSON] };
const LESSONS = indexLessons(PACK, realPack, "syn00001");

function makeAdapter(logs?: LogRecord[], lessons = LESSONS) {
  return createAdapter({ content: realPack, taxonomy, contract: contractV1, allowCandidate: true, lessons, ...(logs ? { log: (r) => logs.push(r) } : {}) });
}
const adapter = makeAdapter();

function ask(question: Json = {}, extra: Json = {}): Json {
  return {
    context: contextV1({ contract_version: "1.1.0" }),
    mode: "lesson",
    skill_node: `aya:${NODE}`,
    item_ref: `aya:${ITEM.id}`,
    allowed_hint_level: 2,
    persona: { persona_id: "aya", prompt_version: "1.1.0" },
    question: {
      lesson_ref: { lesson_id: LESSON.lesson_id, lesson_version: LESSON.lesson_version },
      dsl_version: "0.3.0", cursor: 2, anchor: { kind: "element", id: "t1" }, intent: "why_step",
      ...question,
    },
    ...extra,
  };
}
const call = (body: Json, a = adapter) => a.handle("POST", "teach", body) as unknown as { status: number; body: Json };

test("指着元素问“为什么”：只回台词稿里那句预制答疑，一段 text，记第 1 级帮助；响应符合契约", () => {
  const r = call(ask());
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const result = r.body.result as Json;
  assert.deepEqual(result.segments, [{ type: "text", text: SECRET }]);
  assert.equal(result.hint_level_used, LESSON_ANSWER_HINT_LEVEL);
  assert.equal(result.ai_disclosure_shown, false);
  assert.equal(result.referral_triggered, false);
  assert.equal(r.body.model_version, `algo:${LESSON_ANSWER_ALGORITHM}`);
  assert.match(String(r.body.source_version), /items:aya-lessons@syn00001/);
  assert.deepEqual(r.body.evidence_ids, []);
  assert.deepEqual(contractV1.validate("TeachResponse", r.body, "1.1.0"), { ok: true });
});

test("锚点落在哪一步：元素是它写上去的那一步，整一步是那一步，槽位是当前这一步", () => {
  // t3 是第 3 步写的，问“是什么” → s3 的答疑（点点答）
  assert.equal(((call(ask({ cursor: 4, anchor: { kind: "element", id: "t3" }, intent: "what_is" })).body.result as Json).segments as Json[])[0]!.text,
    "（合成答疑）这是第三步。");
  // 整一步 s1
  assert.equal(((call(ask({ cursor: 3, anchor: { kind: "step", id: "s1" } })).body.result as Json).segments as Json[])[0]!.text, SECRET);
  // 槽位：当前这一步（cursor 1 → s1）
  assert.equal(((call(ask({ cursor: 1, anchor: { kind: "slot", id: "fig" } })).body.result as Json).segments as Json[])[0]!.text, SECRET);
});

test("没有预制答疑、提示等级上限为 0：422 MODE_NOT_SUPPORTED（不答，平台给兜底）", () => {
  for (const q of [{ intent: "what_is" }, { cursor: 3, anchor: { kind: "element", id: "t2" }, intent: "why_step" }]) {
    const r = call(ask(q));
    assert.equal(r.status, 422, JSON.stringify(q));
    assert.equal(r.body.code, "MODE_NOT_SUPPORTED");
  }
  const zero = call(ask({}, { allowed_hint_level: 0 }));
  assert.equal(zero.status, 422);
  assert.equal(zero.body.code, "MODE_NOT_SUPPORTED");
});

test("提交前不露答案：答疑含这道填空题的标准答案原文时，提交前不给、提交后给", () => {
  const before = call(ask({ cursor: 2, anchor: { kind: "element", id: "t2" }, intent: "where_from" }));
  assert.equal(before.status, 422);
  assert.equal(before.body.code, "MODE_NOT_SUPPORTED");
  assert.ok(!JSON.stringify(before.body).includes(ANSWER), "拒绝信息里也不出现答案");
  const unset = call(ask({ cursor: 2, anchor: { kind: "element", id: "t2" }, intent: "where_from" }, { answer_submitted: false }));
  assert.equal(unset.status, 422);
  const after = call(ask({ cursor: 2, anchor: { kind: "element", id: "t2" }, intent: "where_from" }, { answer_submitted: true }));
  assert.equal(after.status, 200, JSON.stringify(after.body));
  assert.equal(((after.body.result as Json).segments as Json[])[0]!.text, `（合成答疑）结果是${ANSWER}。`);
});

test("只能指已经出现的东西：未来的元素、未来的步、高亮以外不存在的东西 → 422 INVALID_REQUEST", () => {
  for (const q of [
    { cursor: 1, anchor: { kind: "element", id: "t2" } },     // 第 2 步才写
    { cursor: 2, anchor: { kind: "step", id: "s3" } },        // 还没播放到
    { cursor: 2, anchor: { kind: "element", id: "zz" } },
    { cursor: 2, anchor: { kind: "slot", id: "t1" } },
    { cursor: 5, anchor: { kind: "step", id: "s1" } },        // 超过这一课的步数
  ]) {
    const r = call(ask(q));
    assert.equal(r.status, 422, JSON.stringify(q));
    assert.equal(r.body.code, "INVALID_REQUEST", JSON.stringify(q));
  }
});

test("讲评课引用：不存在、版本不对、讲解 DSL 版本不对 → 404；节点或题与讲评课不一致 → 422", () => {
  for (const q of [
    { lesson_ref: { lesson_id: "syn.lesson.none", lesson_version: "0.1.0" } },
    { lesson_ref: { lesson_id: LESSON.lesson_id, lesson_version: "0.2.0" } },
    { dsl_version: "0.2.0" },
  ]) {
    const r = call(ask(q));
    assert.equal(r.status, 404, JSON.stringify(q));
    assert.equal(r.body.code, "CONTENT_VERSION_NOT_FOUND");
  }
  const other = realPack.pack.items.find((i) => i.id !== ITEM.id && i.mainNodes.includes(NODE));
  if (other) assert.equal(call(ask({}, { item_ref: `aya:${other.id}` })).status, 422);
  const noItem = ask();
  delete noItem.item_ref;
  assert.equal(call(noItem).status, 422);
  const otherNode = realPack.pack.nodes.find((n) => n.id !== NODE)!;
  assert.equal(call(ask({}, { skill_node: `aya:${otherNode.id}` })).status, 422);
});

test("没有讲评课内容包（缺省）：带提问的请求一律 404；不带提问的 lesson / hint 照旧", () => {
  const bare = makeAdapter(undefined, NO_LESSONS);
  const r = call(ask(), bare);
  assert.equal(r.status, 404);
  assert.equal(r.body.code, "CONTENT_VERSION_NOT_FOUND");
});

test("契约挡住的请求：提示模式带提问、带对话、1.0.0 带提问、“再讲一遍”", () => {
  const turn = [{ turn_id: "00000000-0000-4000-8000-0000000000c9", role: "student", content: "（合成）为什么" }];
  for (const body of [ask({}, { mode: "hint" }), ask({}, { conversation: turn }), ask({ intent: "again" }),
    { ...ask(), context: contextV1() }]) {
    const r = call(body);
    assert.equal(r.status, 400, JSON.stringify(body).slice(0, 120));
    assert.equal(r.body.code, "INVALID_REQUEST");
  }
});

test("日志不记讲评课文字与答疑；同一幂等键重放结果相同", () => {
  const logs: LogRecord[] = [];
  const a = makeAdapter(logs);
  const body = ask();
  const r1 = call(body, a); const r2 = call(body, a);
  assert.deepEqual(r1.body, r2.body);
  call(ask({ intent: "what_is" }), a);
  assert.ok(!JSON.stringify(logs).includes("合成"), JSON.stringify(logs));
});

test("讲评课内容包的读入：格式、编号、节点、题、DSL 与台词稿都按 packages/stage 同一套核对；不合格整包拒绝", () => {
  assert.ok(LESSONS.byRef.has(lessonKey(LESSON.lesson_id, LESSON.lesson_version)));
  const bad = (mutate: (l: Json) => void) => {
    const l = structuredClone(LESSON) as Json;
    mutate(l);
    return () => indexLessons({ format: LESSON_PACK_FORMAT, lessons: [l] }, realPack);
  };
  assert.throws(() => indexLessons({ format: "x", lessons: [] }, realPack));
  assert.throws(() => indexLessons({ format: LESSON_PACK_FORMAT, lessons: [LESSON, LESSON] }, realPack), /重复/);
  assert.throws(bad((l) => { l.lesson_id = "Bad Id"; }));
  assert.throws(bad((l) => { l.lesson_version = "v1"; }));
  assert.throws(bad((l) => { l.skill_node = "aya:NO-SUCH-NODE"; }), /skill_node 不在内容包里/);
  assert.throws(bad((l) => { l.item_ref = "aya:NO-SUCH-ITEM"; }), /item_ref 不在内容包里/);
  // 节点在内容包里，但不是这道题的主节点
  const notMain = realPack.pack.nodes.find((n) => !ITEM.mainNodes.includes(n.id))!;
  assert.throws(bad((l) => { l.skill_node = `aya:${notMain.id}`; }), /不是这道题的主节点/);
  // 不针对某道题的讲评课：节点在内容包里即可
  assert.equal(indexLessons({ format: LESSON_PACK_FORMAT, lessons: [{ ...structuredClone(LESSON), item_ref: undefined, skill_node: `aya:${notMain.id}` }] }, realPack).byRef.size, 1);
  assert.throws(bad((l) => { l.note = "（合成）"; }), /多余字段/);
  assert.throws(bad((l) => { (l.dsl as Json).steps = []; }), /讲解 DSL 不合格/);
  assert.throws(bad((l) => { ((l.script as Json).answers as Json[]).push({ step: "s1", intent: "why_step", line: "a3" }); }), /台词稿不合格/);
  assert.throws(bad((l) => { ((l.script as Json).lines as Json[])[3]!.text = "（合成）见 https://example.invalid"; }), /台词稿不合格/);
  // 0.2.0 的旧讲评课沿迁移阶梯读入（没有答疑）
  const old = structuredClone(LESSON) as Json;
  (old.dsl as Json).dsl_version = "0.2.0";
  old.script = { dsl_version: "0.2.0", lines: SCRIPT.lines.slice(0, 3) };
  assert.ok(indexLessons({ format: LESSON_PACK_FORMAT, lessons: [old] }, realPack).byRef.size === 1);
  // 错误信息不带讲解文字
  try { bad((l) => { ((l.script as Json).lines as Json[])[3]!.text = `${SECRET}${"长".repeat(80)}`; })(); assert.fail(); } catch (e) {
    assert.ok(!(e as Error).message.includes("LESSON-ANSWER-SECRET"));
  }
});

test("讲评课内容包文件：只在候选版开关打开时读；没设路径就是没有讲评课", () => {
  const dir = mkdtempSync(join(tmpdir(), "aya-lessons-"));
  try {
    const p = join(dir, "lessons.json");
    writeFileSync(p, JSON.stringify(PACK));
    assert.equal(loadLessonsFrom(undefined, realPack, false), NO_LESSONS);
    assert.throws(() => loadLessonsFrom(p, realPack, false), /候选版开关/);
    const l = loadLessonsFrom(p, realPack, true);
    assert.equal(l.byRef.size, 1);
    assert.match(l.version, /^[0-9a-f]{8}$/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
