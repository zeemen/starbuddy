// KGE-08：输入守卫的单测——只收题目层字段；作答、学生、手写、批改类字段一律拒绝（规则 13）；合成标记；资源上限；不回显输入。
// 全部合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import { FORMAT, fromLegacy, InputError, looksPersonal, MAX_FIXTURE_BYTES, MAX_ITEMS, MAX_TOTAL_CHARS, NODE_SYSTEM, parseFixture } from "../src/input.ts";
import { FIXTURE_RAW } from "./helpers.ts";

const ITEM = { id: "SYN-1", text: "（合成）一道题", main_nodes: ["SYN.A"] };
const doc = (patch: Record<string, unknown> = {}, items: unknown[] = [ITEM]) =>
  JSON.stringify({ format: FORMAT, synthetic: true, notice: "（合成）样例：单测", items, ...patch });
const rejects = (raw: string, re: RegExp, why: string) =>
  assert.throws(() => parseFixture(raw), (e: unknown) => e instanceof InputError && re.test(e.message), why);
const messageOf = (raw: string): string => {
  try {
    parseFixture(raw);
  } catch (e) {
    return (e as Error).message;
  }
  throw new Error("应被拒绝");
};

test("合成样例可过：62 题，都以“（合成）”开头，synthetic: true，坐标系是 fixture", () => {
  const inp = parseFixture(FIXTURE_RAW);
  assert.equal(inp.synthetic, true);
  assert.equal(inp.source, "fixture");
  assert.equal(inp.subject, "syn");
  assert.equal(inp.nodeSystem, NODE_SYSTEM.fixture);
  assert.equal(inp.items.length, 62);
  assert.ok(inp.items.every((x) => x.text.startsWith("（合成）")));
  assert.equal(inp.legacyFamilies.size, 0);
  assert.equal(parseFixture(doc()).items.length, 1);
});

test("作答、学生、手写、批改类字段一律拒绝（题目层与顶层都查，大小写不敏感）", () => {
  for (const k of ["student_answer", "answer", "response", "student_id", "learner", "handwriting", "handwritten_image", "grading",
    "graded_by", "score", "correction", "Student_Answer", "HANDWRITING", "Grading", "作答", "答案", "学生", "手写", "批改", "评分", "得分"]) {
    rejects(doc({}, [{ ...ITEM, [k]: "（合成）x" }]), /作答、学生、手写或批改类字段/, `题目层的 ${k} 应被拒`);
  }
  rejects(doc({ student_list: [] }), /作答、学生、手写或批改类字段/, "顶层的 student_list 应被拒");
  rejects(doc({ STUDENT_LIST: [] }), /作答、学生、手写或批改类字段/, "顶层的大写 STUDENT_LIST 应被拒");
  rejects(doc({}, [{ ...ITEM, stem_image: "x" }]), /键不认识/, "题目层不认识的键应被拒");
  rejects(doc({ extra: 1 }), /键不认识/, "顶层不认识的键应被拒");
});

test("不回显输入：坏 JSON 只报“不是合法 JSON”；不认识的键只报序号，不报键名", () => {
  assert.equal(messageOf('{"format": "x", "secret_payload_zzz": '), "样例不是合法 JSON");
  const m = messageOf(doc({}, [{ ...ITEM, secret_payload_zzz: "x" }]));
  assert.match(m, /第 4 个键不认识/);
  assert.ok(!m.includes("secret_payload_zzz"));
  const long = messageOf(doc({}, [{ ...ITEM, student_answer_with_a_very_long_name: "x" }]));
  assert.ok(!long.includes("very_long_name"), "禁用键名要截断");
});

test("合成标记：synthetic 不是 true 一律拒；notice 与题目文本都要以“（合成）”开头", () => {
  rejects(doc({ synthetic: false }), /只收合成样例/, "synthetic: false");
  rejects(JSON.stringify({ format: FORMAT, notice: "（合成）", items: [ITEM] }), /只收合成样例/, "缺 synthetic");
  rejects(doc({ synthetic: "true" }), /只收合成样例/, "synthetic 是字符串");
  rejects(doc({ notice: "单测样例" }), /必须以“（合成）”开头/, "notice 无合成标记");
  rejects(doc({ notice: "单测（合成）样例" }), /必须以“（合成）”开头/, "notice 合成标记不在开头");
  rejects(doc({}, [{ ...ITEM, text: "一道题" }]), /必须以“（合成）”开头/, "题目文本无合成标记");
  rejects(doc({}, [{ ...ITEM, text: "合成：一道题" }]), /必须以“（合成）”开头/, "题目文本合成标记写法不对");
});

test("个人信息：先 NFKC、去空白再匹配（手机号、全角与带空格手机号、身份证号、邮箱、某某同学）", () => {
  const bad = ["（合成）联系 13812345678", "（合成）联系 １３８１２３４５６７８", "（合成）联系 138 1234 5678", "（合成）编号 11010120000101123X",
    "（合成）编号 １１０１０１２００００１０１１２３４", "（合成）邮箱 syn@example.com", "（合成）邮箱 ｓｙｎ＠ｅｘａｍｐｌｅ．ｃｏｍ", "（合成）张三同学"];
  for (const t of bad) {
    assert.ok(looksPersonal(t), t);
    rejects(doc({}, [{ ...ITEM, text: t }]), /个人信息/, t);
  }
  rejects(doc({ notice: "（合成）联系 syn@example.com" }), /个人信息/, "notice 里的邮箱");
  assert.ok(!looksPersonal("（合成）求 12 与 18 的最小公倍数"));
});

test("个人信息匹配不回溯成平方级：3 万字无 @ 的字母串在 300 ms 内查完", () => {
  const t0 = Date.now();
  assert.equal(looksPersonal(`（合成）${"a".repeat(30000)}`), false);
  assert.ok(Date.now() - t0 < 300, `用时 ${String(Date.now() - t0)} ms`);
});

test("形状：format、ID、主节点 ID 形状、paper_id 与 seq 成对、ID 与卷内序号不重复", () => {
  rejects(doc({ format: "x" }), /format/, "format");
  rejects(doc({}, []), /items 必须是/, "空 items");
  rejects(doc({}, [{ ...ITEM, id: "坏 ID" }]), /skill_local_id/, "ID 形状");
  rejects(doc({}, [{ ...ITEM, main_nodes: "SYN.A" }]), /main_nodes/, "主节点不是数组");
  rejects(doc({}, [{ ...ITEM, main_nodes: ["坏 节点"] }]), /main_nodes/, "主节点 ID 形状");
  rejects(doc({}, [{ ...ITEM, main_nodes: [".SYN"] }]), /main_nodes/, "主节点 ID 开头");
  rejects(doc({}, [{ ...ITEM, paper_id: "SYN-P" }]), /要么都给/, "只给 paper_id");
  rejects(doc({}, [{ ...ITEM, seq: 1 }]), /要么都给/, "只给 seq");
  rejects(doc({}, [{ ...ITEM, paper_id: "SYN-P", seq: 0 }]), /seq 必须/, "seq 0");
  rejects(doc({}, [ITEM, ITEM]), /重复/, "ID 重复");
  rejects(doc({}, [{ ...ITEM, paper_id: "SYN-P", seq: 1 }, { ...ITEM, id: "SYN-2", paper_id: "SYN-P", seq: 1 }]), /重复/, "卷内序号重复");
  rejects(doc({}, [{ ...ITEM, text: "（合成）\u0007" }]), /控制字符/, "控制字符");
});

test("资源上限：样例字节数、题数、题目文本总字数", () => {
  rejects(" ".repeat(MAX_FIXTURE_BYTES + 1), /字节/, "超过字节上限");
  const many = Array.from({ length: MAX_ITEMS + 1 }, (_, i) => ({ id: `SYN-${String(i)}`, text: "（合成）题", main_nodes: [] }));
  rejects(doc({}, many), /items 必须是 1–20000 条/, "超过题数上限");
  assert.equal(parseFixture(doc({}, many.slice(0, MAX_ITEMS))).items.length, MAX_ITEMS);
  const per = 20000;
  const n = Math.floor(MAX_TOTAL_CHARS / per) + 1;
  const big = Array.from({ length: n }, (_, i) => ({ id: `SYN-${String(i)}`, text: `（合成）${"a".repeat(per - 4)}`, main_nodes: [] }));
  rejects(doc({}, big), /总字数/, "超过总字数上限");
  assert.equal(parseFixture(doc({}, big.slice(0, n - 1))).items.length, n - 1);
});

const legacyQ = (id: string, mainNodes: string[], text = "（合成）题干") =>
  ({ id, topic: "syn", unit: "U1", unitName: "（合成）单元", text: `（合成）单元\n${text}`, mainNodes, auxNodes: [], preNodes: [] });

test("legacy 转换：只取 ID、文本、主节点；去掉单元名；读旧题组；主节点 ID 不合规拒绝", () => {
  const inp = fromLegacy({
    topics: [],
    sprintCategories: [{ id: "dp", name: "（合成）专项", questionIds: ["q2", "q9"] }, { id: "qf", name: "（合成）专项二", questionIds: ["q2"] }],
    questions: [
      { ...legacyQ("q1", ["K-SYN-01"], "（合成）题干\n选项"), auxNodes: ["K-SYN-02"] },
      { id: "q2", topic: null, unit: "", unitName: "", text: "（合成）题干二", mainNodes: [], auxNodes: [], preNodes: [] },
    ],
  });
  assert.equal(inp.synthetic, false);
  assert.equal(inp.subject, "aya");
  assert.equal(inp.nodeSystem, NODE_SYSTEM.aya);
  assert.deepEqual(inp.items, [{ id: "q1", text: "（合成）题干\n选项", mainNodes: ["K-SYN-01"] }, { id: "q2", text: "（合成）题干二", mainNodes: [] }]);
  assert.deepEqual([...inp.legacyFamilies], [["q2", "aya-sprint-dp"], ["q9", "aya-sprint-dp"]]);
  assert.throws(() => fromLegacy({ topics: [], sprintCategories: [], questions: [legacyQ("q1", ["坏 节点"])] }), /主节点 ID 不合规/);
  assert.throws(() => fromLegacy({ topics: [], sprintCategories: [], questions: [legacyQ("q1", ["K-1"]), legacyQ("q1", ["K-1"])] }), /重复/);
});
