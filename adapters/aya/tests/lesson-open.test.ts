// 打开讲评课（specs/stage-lesson-session ③b-2；契约 v1.1 草稿内追加的 TeachRequest.lesson_ref）：阿鸦从讲评课内容包下发讲解 DSL 与字幕台词，
// 预制答疑不随讲评课下发；作答提交前讲解里含填空题答案原文时不下发。全部是合成数据（规则 8）：讲评课与台词都是“（合成）”占位；
// 题目与节点取自 legacy 内容包（学科内容，不含学生数据）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { migrateStageDsl, migrateStageScript } from "../../../packages/stage/src/index.ts";
import { createAdapter, type LogRecord } from "../src/adapter.ts";
import { indexLessons, LESSON_PACK_FORMAT, NO_LESSONS } from "../src/lessons.ts";
import { LESSON_OPEN_ALGORITHM } from "../src/methods/teach.ts";
import { contextV1, contractV1, realPack, taxonomy, type Json } from "./helpers.ts";

const ITEM = realPack.pack.items.find((i) => i.format === "fill" && i.answer.some((a) => a.replace(/\s/g, "").length >= 2))!;
const NODE = ITEM.mainNodes[0]!;
const ANSWER = ITEM.answer.find((a) => a.replace(/\s/g, "").length >= 2)!;
const ANSWER_LINE = "（合成答疑）OPEN-ANSWER-SECRET-91f0";

const dsl = (board2 = "（合成）第二行") => ({
  dsl_version: "0.3.0", title: "（合成）讲评课", canvas: { width: 960, height: 440 }, roles: ["teacher", "buddy"], narrated: true,
  steps: [
    { id: "s1", say: "l1", actions: [{ type: "speaker", role: "teacher" }, { type: "write_text", id: "t1", x: 40, y: 64, text: "（合成）第一行" }] },
    { id: "s2", say: "l2", actions: [{ type: "write_text", id: "t2", x: 40, y: 140, text: board2 }] },
    { id: "s3", actions: [{ type: "speaker", role: "none" }] },
  ],
});
const SCRIPT = {
  dsl_version: "0.3.0",
  lines: [
    { id: "l1", role: "teacher", text: "（合成台词）先看第一行。" },
    { id: "l2", role: "teacher", text: "（合成台词）再看第二行。" },
    { id: "a1", role: "teacher", text: ANSWER_LINE },
  ],
  answers: [{ step: "s1", intent: "why_step", line: "a1" }],
};
const lesson = (id: string, board2?: string) => ({
  lesson_id: id, lesson_version: "0.1.0", skill_node: `aya:${NODE}`, item_ref: `aya:${ITEM.id}`, dsl: dsl(board2), script: SCRIPT,
});
const LESSONS = indexLessons({ format: LESSON_PACK_FORMAT, lessons: [lesson("syn.lesson.open_a"), lesson("syn.lesson.open_b", `（合成）答案是 ${ANSWER}`)] },
  realPack, "syn00002");

const makeAdapter = (logs?: LogRecord[], lessons = LESSONS) =>
  createAdapter({ content: realPack, taxonomy, contract: contractV1, allowCandidate: true, lessons, ...(logs ? { log: (r) => logs.push(r) } : {}) });
const adapter = makeAdapter();
const call = (body: Json, a = adapter) => a.handle("POST", "teach", body) as unknown as { status: number; body: Json };

function open(lessonId = "syn.lesson.open_a", extra: Json = {}): Json {
  return {
    context: contextV1({ contract_version: "1.1.0" }),
    mode: "lesson",
    skill_node: `aya:${NODE}`,
    item_ref: `aya:${ITEM.id}`,
    allowed_hint_level: 2,
    persona: { persona_id: "aya", prompt_version: "1.1.0" },
    lesson_ref: { lesson_id: lessonId, lesson_version: "0.1.0" },
    ...extra,
  };
}
const segment = (r: { body: Json }) => ((r.body.result as Json).segments as Json[])[0]!;

test("打开讲评课：一段 whiteboard_dsl，带讲解 DSL 与字幕台词；记 0 级；响应符合契约", () => {
  const r = call(open());
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const seg = segment(r);
  assert.equal(seg.type, "whiteboard_dsl");
  assert.equal(seg.dsl_version, "0.3.0");
  assert.equal(((r.body.result as Json).segments as Json[]).length, 1);
  assert.equal((r.body.result as Json).hint_level_used, 0);
  assert.equal(r.body.model_version, `algo:${LESSON_OPEN_ALGORITHM}`);
  assert.match(String(r.body.source_version), /items:aya-lessons@syn00002/);
  assert.deepEqual(r.body.evidence_ids, []);
  assert.deepEqual(contractV1.validate("TeachResponse", r.body, "1.1.0"), { ok: true });
  // 下发的 DSL 与台词稿能被平台用 packages/stage 的同一套入口读回（配对核对通过）
  const d = migrateStageDsl(seg.dsl);
  assert.ok(d.ok, JSON.stringify(!d.ok && d.errors));
  const sc = migrateStageScript(d.dsl, seg.script);
  assert.ok(sc.ok, JSON.stringify(!sc.ok && sc.errors));
});

test("预制答疑不随讲评课下发：台词稿只有字幕台词，没有 answers，也没有只给答疑用的台词", () => {
  const r = call(open());
  const script = segment(r).script as Json;
  assert.deepEqual(Object.keys(script).sort(), ["dsl_version", "lines"]);
  assert.deepEqual((script.lines as Json[]).map((l) => l.id), ["l1", "l2"]);
  assert.ok(!JSON.stringify(r.body).includes("OPEN-ANSWER-SECRET"));
});

test("提交前不露答案：讲解里含这道填空题的标准答案原文时，提交前不下发（422），提交后下发", () => {
  for (const extra of [{}, { answer_submitted: false }]) {
    const r = call(open("syn.lesson.open_b", extra));
    assert.equal(r.status, 422, JSON.stringify(extra));
    assert.equal(r.body.code, "MODE_NOT_SUPPORTED");
    assert.ok(!JSON.stringify(r.body).includes(ANSWER));
  }
  const after = call(open("syn.lesson.open_b", { answer_submitted: true }));
  assert.equal(after.status, 200, JSON.stringify(after.body));
  assert.ok(JSON.stringify(segment(after).dsl).includes(ANSWER));
});

test("讲评课引用、节点与题的核对：不存在 404；节点或题不一致 422；没有讲评课内容包时 404", () => {
  for (const ref of [{ lesson_id: "syn.lesson.none", lesson_version: "0.1.0" }, { lesson_id: "syn.lesson.open_a", lesson_version: "0.2.0" }]) {
    const r = call({ ...open(), lesson_ref: ref });
    assert.equal(r.status, 404, JSON.stringify(ref));
    assert.equal(r.body.code, "CONTENT_VERSION_NOT_FOUND");
  }
  const otherNode = realPack.pack.nodes.find((n) => n.id !== NODE)!;
  assert.equal(call(open(undefined, { skill_node: `aya:${otherNode.id}` })).status, 422);
  const noItem = open();
  delete noItem.item_ref;
  assert.equal(call(noItem).status, 422);
  const bare = call(open(), makeAdapter(undefined, NO_LESSONS));
  assert.equal(bare.status, 404);
});

test("契约挡住的请求：提示模式带 lesson_ref、同时带提问、带对话、1.0.0 带 lesson_ref", () => {
  const turn = [{ turn_id: "00000000-0000-4000-8000-0000000000d1", role: "student", content: "（合成）开始" }];
  const q = { lesson_ref: { lesson_id: "syn.lesson.open_a", lesson_version: "0.1.0" }, dsl_version: "0.3.0", cursor: 1,
    anchor: { kind: "step", id: "s1" }, intent: "why_step" };
  for (const body of [open(undefined, { mode: "hint" }), open(undefined, { question: q }), open(undefined, { conversation: turn }),
    { ...open(), context: contextV1() }]) {
    const r = call(body);
    assert.equal(r.status, 400, JSON.stringify(body).slice(0, 120));
    assert.equal(r.body.code, "INVALID_REQUEST");
  }
});

test("日志不记讲评课文字；同一幂等键重放结果相同；不带 lesson_ref 的 lesson 照旧要知识页面", () => {
  const logs: LogRecord[] = [];
  const a = makeAdapter(logs);
  const body = open();
  assert.deepEqual(call(body, a).body, call(body, a).body);
  assert.ok(!JSON.stringify(logs).includes("合成"));
  const plain = open();
  delete plain.lesson_ref;
  const r = call(plain, a);
  assert.equal(r.status, 422);
  assert.equal(r.body.code, "MODE_NOT_SUPPORTED");
});
