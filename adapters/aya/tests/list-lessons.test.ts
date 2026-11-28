// 讲评课目录 list_lessons（契约 v1.1 草稿内追加，specs/lesson-catalog PR A）。全部是合成数据（规则 8）：讲评课与台词都是“（合成）”占位；
// 题目与节点取自 legacy 内容包（学科内容，不含学生数据）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter } from "../src/adapter.ts";
import { loadContract } from "../src/contract.ts";
import { indexLessons, LESSON_PACK_FORMAT, NO_LESSONS, type LoadedLessons } from "../src/lessons.ts";
import { LESSON_LIST_ALGORITHM, listLessons } from "../src/methods/list-lessons.ts";
import { contextV1, contract, keysOf, leaves, makeAdapterV1, presentContext, realPack, STUDENT, taxonomy, type Json } from "./helpers.ts";

const ITEM = realPack.pack.items.find((i) => i.format === "fill")!;
const NODE = ITEM.mainNodes[0]!;
const SCRIPT_SECRET = "（合成台词）LIST-SCRIPT-SECRET-5c1e";
const ANSWER_SECRET = "（合成答疑）LIST-ANSWER-SECRET-5c1e";
const BOARD_SECRET = "（合成板书）LIST-BOARD-SECRET-5c1e";

const lesson = (id: string, opts: { version?: string; item?: boolean; title?: string } = {}) => ({
  lesson_id: id, lesson_version: opts.version ?? "0.1.0", skill_node: `aya:${NODE}`, ...(opts.item === false ? {} : { item_ref: `aya:${ITEM.id}` }),
  dsl: {
    dsl_version: "0.3.0", title: opts.title ?? "（合成）讲评课", canvas: { width: 960, height: 440 }, roles: ["teacher"], narrated: true,
    steps: [
      { id: "s1", say: "l1", actions: [{ type: "speaker", role: "teacher" }, { type: "write_text", id: "t1", x: 40, y: 64, text: BOARD_SECRET }] },
      { id: "s2", actions: [{ type: "speaker", role: "none" }] },
    ],
  },
  script: {
    dsl_version: "0.3.0",
    lines: [{ id: "l1", role: "teacher", text: SCRIPT_SECRET }, { id: "a1", role: "teacher", text: ANSWER_SECRET }],
    answers: [{ step: "s1", intent: "why_step", line: "a1" }],
  },
});
const LESSONS = indexLessons({ format: LESSON_PACK_FORMAT, lessons: [
  lesson("syn.lesson.b"), lesson("syn.lesson.a", { version: "0.2.0", item: false, title: "（合成）不针对某道题" }), lesson("syn.lesson.a"),
] }, realPack, "r0a1b2c3d");

const adapterWith = (lessons: LoadedLessons = LESSONS) => createAdapter({ content: realPack, lessons, taxonomy, contract, allowCandidate: true });
const CV = { ...realPack.pack.curriculum };
const body = (ctx: Record<string, unknown> = presentContext(), extra: Record<string, unknown> = {}) => ({ context: ctx, content_version: { ...CV }, ...extra });
const call = (b: unknown, lessons?: LoadedLessons) => adapterWith(lessons).handle("POST", "list_lessons", b);

test("目录只有引用、节点、题、标题与步数；按编号与版本排序；合契约、不产生证据", () => {
  const r = call(body());
  assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
  const b = r.body as Json;
  assert.deepEqual(contract.validate("ListLessonsResponse", b, "1.1.0"), { ok: true });
  assert.deepEqual(b.result.lessons, [
    { lesson_ref: { lesson_id: "syn.lesson.a", lesson_version: "0.1.0" }, skill_node: `aya:${NODE}`, item_ref: `aya:${ITEM.id}`, title: "（合成）讲评课", step_count: 2 },
    { lesson_ref: { lesson_id: "syn.lesson.a", lesson_version: "0.2.0" }, skill_node: `aya:${NODE}`, title: "（合成）不针对某道题", step_count: 2 },
    { lesson_ref: { lesson_id: "syn.lesson.b", lesson_version: "0.1.0" }, skill_node: `aya:${NODE}`, item_ref: `aya:${ITEM.id}`, title: "（合成）讲评课", step_count: 2 },
  ]);
  assert.deepEqual(b.evidence_ids, []);
  assert.equal(b.semantics, "practice");
  assert.equal(b.human_review_required, false);
  assert.equal(b.model_version, `algo:${LESSON_LIST_ALGORITHM}`);
  assert.match(b.source_version, /^algo:aya-lesson-list@0\.1\.0;items:aya-lessons@r0a1b2c3d;items:/);
});

test("目录不泄露讲解内容：没有 DSL、台词、答疑、板书原文，也没有答案", () => {
  const b = call(body()).body as Json;
  const keys = new Set(keysOf(b));
  for (const k of ["dsl", "dsl_version", "script", "lines", "answers", "steps", "say", "actions", "answer", "hintPath"]) assert.ok(!keys.has(k), k);
  const strings = leaves(b).map(([, v]) => v).filter((v): v is string => typeof v === "string");
  for (const secret of [SCRIPT_SECRET, ANSWER_SECRET, BOARD_SECRET, "LIST-"]) assert.ok(!strings.some((s) => s.includes(secret)), secret);
  for (const a of ITEM.answer) if (a.length >= 3) assert.ok(!strings.some((s) => s.includes(a)), "答案");
});

test("没有讲评课时是空数组；同一请求两次逐字节相同；不进幂等缓存（带不带幂等键结果一样）", () => {
  const empty = call(body(), NO_LESSONS);
  assert.equal(empty.status, 200);
  assert.deepEqual((empty.body as Json).result, { lessons: [] });
  const a = adapterWith();
  const r1 = a.handle("POST", "list_lessons", body());
  const r2 = a.handle("POST", "list_lessons", body(presentContext({ idempotency_key: "00000000-0000-4000-8000-00000000c7ff" })));
  assert.deepEqual(r1.body, r2.body);
  assert.deepEqual(listLessons(LESSONS), listLessons(LESSONS));
});

test("请求不合格：带学生、带计量、多余字段、内容版本不是本适配器的、两处内容版本不一致、学科不对、v1.0 请求", () => {
  const bad = (b: unknown, status: number, code: string) => {
    const r = call(b);
    assert.equal(r.status, status, JSON.stringify(r.body).slice(0, 200));
    assert.equal((r.body as Json).code, code);
  };
  bad(body(presentContext({ student_ref: STUDENT })), 400, "INVALID_REQUEST");
  bad(body(presentContext({ usage_id: "00000000-0000-4000-8000-00000000b7ff" })), 400, "INVALID_REQUEST");
  bad(body(presentContext(), { include_script: true }), 400, "INVALID_REQUEST");
  const { content_version: _drop, ...noCv } = presentContext();
  bad({ context: noCv, content_version: { ...CV, content_version: "9.9.9" } }, 404, "CONTENT_VERSION_NOT_FOUND");
  bad(body(presentContext({ content_version: { ...CV, grade: 8 } })), 400, "INVALID_REQUEST");
  bad(body(presentContext({ subject: "zhixing" })), 400, "SUBJECT_MISMATCH");
  bad({ context: contextV1(), content_version: { ...CV } }, 501, "METHOD_NOT_IMPLEMENTED");
});

test("能力声明：1.1.0 声明 list_lessons（只读、同步）；1.0.0 的能力声明没有这一项", () => {
  const v11Default = createAdapter({ content: realPack, lessons: LESSONS, taxonomy, contract: loadContract({ defaultVersion: "1.1.0" }), allowCandidate: true });
  const caps = v11Default.handle("GET", "capabilities", undefined).body as Json;
  assert.deepEqual(contract.validate("Capabilities", caps, "1.1.0"), { ok: true });
  const v11 = caps.methods;
  assert.deepEqual(v11.list_lessons, { implemented: true, async: false });
  const v10 = (makeAdapterV1().handle("GET", "capabilities", undefined).body as Json).methods;
  assert.equal(v10.list_lessons, undefined);
});
