// report（spec 4-D 知行部分，待人确认 #3 默认）：只给逐维作答计数、四类失分归因计数与证据 ID；不写结论句与评价（规则 11），
// 没有等级、没有 highlight_codes；trend 是契约必填编码，只比较平台给的 mastery_history 的状态序。不调模型。
// 全部是合成数据（规则 8）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter, REPORT_ALGORITHM } from "../src/adapter.ts";
import { ANSWERS, attemptId, context, contextV1, contract, gradeBodyV1, harness, leaves, realPack, taxonomy, type Json } from "./helpers.ts";

const make = () => createAdapter({ content: realPack, taxonomy, contract, route: null, allowCandidate: true });
const call = async (a: ReturnType<typeof make>, body: unknown) => {
  const r = await a.handle("POST", "report", body);
  return { status: r.status, body: r.body as Json };
};
const ctx = (v1 = true) => {
  const c = (v1 ? contextV1() : context()) as Json;
  delete c.usage_id; // 非消耗类方法
  return c;
};
const WINDOW = { from: "2026-10-05T00:00:00+08:00", to: "2026-10-12T00:00:00+08:00" };
const eid = (n: number) => `e1000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const text = (n: number, item: string, at: string, attempt?: number, admission = "pending_review") => ({
  evidence_id: eid(n),
  ...(attempt === undefined ? {} : { attempt_id: attemptId(attempt) }),
  kind: attempt === undefined ? "ocr_transcription" : "response_text",
  occurred_at: at,
  item_ref: `zhixing:${item}`,
  skill_node: "zhixing:syn-dim-zhuanhua",
  admission,
  payload: { text: `合成作答原文第${n}条` },
});
const attr = (code: string, cross: string, confidence: number, ids: string[]) => ({
  skill_node: "zhixing:syn-dim-dingwei",
  subject_category: code,
  cross_subject_category: cross,
  confidence,
  evidence_ids: ids,
  algorithm_version: "zhixing-m3-attr@0.1.0",
});
const summary = (b: Json) => Object.fromEntries(b.result.skill_summary.map((s: Json) => [s.skill_node.replace("zhixing:syn-dim-", ""), [s.trend, s.attempts]]));
const counts = (b: Json) => Object.fromEntries(b.result.error_distribution.map((d: Json) => [d.subject_category, d.count]));

test("正常：grade 产出的证据与错因 → 逐维作答计数（按题目涉及的维度）、四类失分逐类计数、证据 ID 全部可回溯", async () => {
  const h = await harness();
  try {
    const g = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
    const graded = (await h.adapter.handle("POST", "grade", g)).body as Json;
    const stubCalls = h.stub.calls.length;
    const body = { context: ctx(), window: WINDOW, audience: "parent", evidence: graded.evidence, error_attributions: graded.result.error_attributions };
    const r = await h.adapter.handle("POST", "report", body);
    const b = r.body as Json;
    assert.equal(r.status, 200, JSON.stringify(b));
    assert.equal(h.stub.calls.length, stubCalls, "report 不调模型");
    assert.deepEqual(contract.validate("ReportResponse", b, "1.0.0"), { ok: true });
    // synthetic-m3-01 涉及转化、定位、规范三个维度；一道作答 → 各记 1 次
    assert.deepEqual(summary(b), { dingwei: ["new", 1], zhuanhua: ["new", 1], guifan: ["new", 1] });
    // 桩：p2 可接受表述 → guifan_cuo；p3 没命中 → dingwei_cuo
    assert.deepEqual(counts(b), { shenti_cuo: 0, dingwei_cuo: 1, zhuanhua_cuo: 0, guifan_cuo: 1 });
    assert.deepEqual(b.result.error_distribution.map((d: Json) => d.cross_subject_category), ["reading_the_question", "locating_information", "thinking_method", "expression_convention"]);
    const requestIds = new Set([...graded.evidence.map((e: Json) => e.evidence_id), ...graded.result.error_attributions.flatMap((a: Json) => a.evidence_ids)]);
    assert.ok(b.evidence_ids.length > 0 && b.evidence_ids.every((id: string) => requestIds.has(id)), "证据 ID 只能来自请求");
    assert.deepEqual([...b.evidence_ids].sort(), [...graded.evidence.map((e: Json) => e.evidence_id)].sort());
    assert.equal(b.confidence, 0.4, "取错因记录置信度的最小值");
    assert.deepEqual([b.human_review_required, b.semantics], [false, "practice"], "证据未准入 → 只作练习效力；report 不判分，不置复核");
    assert.equal(b.model_version, `algo:${REPORT_ALGORITHM}`);
    assert.equal(b.source_version, `algo:${REPORT_ALGORITHM};items:zhixing-synthetic@0.1.0`);
    assert.ok(!JSON.stringify(b).includes(ANSWERS.partial01), "不回显作答原文");
  } finally {
    await h.close();
  }
});

test("时间窗 [from, to) 按时刻比较（+08:00 与 Z 混用）；窗外证据不计入也不进 evidence_ids；同一道作答的多条证据只算一次", async () => {
  const evidence = [
    text(1, "synthetic-m3-04", "2026-10-06T10:00:00+08:00", 1), // 窗内；04 涉及时政、规范
    text(2, "synthetic-m3-04", "2026-10-06T02:00:00Z", 1), // 同一道作答（attempt 1）的另一条证据 → 不重复计数
    text(3, "synthetic-m3-04", "2026-10-04T15:59:59Z", 2), // = 10-04 23:59:59 +08:00，窗前
    text(4, "synthetic-m3-04", "2026-10-11T16:00:00Z", 3), // = 10-12 00:00 +08:00，恰好是 to，窗外
    text(5, "synthetic-m3-03", "2026-10-11T15:59:59Z", 4), // 窗内最后一秒；03 涉及定位、转化
  ];
  const { status, body: b } = await call(make(), { context: ctx(), window: WINDOW, audience: "student", evidence });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(summary(b), { dingwei: ["new", 1], zhuanhua: ["new", 1], guifan: ["new", 1], shizheng: ["new", 1] });
  assert.deepEqual(b.evidence_ids, [eid(1), eid(2), eid(5)]);
  assert.deepEqual(counts(b), { shenti_cuo: 0, dingwei_cuo: 0, zhuanhua_cuo: 0, guifan_cuo: 0 });
  assert.equal(b.confidence, 1, "没有错因记录时是 1");
});

test("trend 只比较平台给的掌握状态序：窗前最后一条为基线；没有基线 → new；窗内没有记录 → flat", async () => {
  const hist = (node: string, state: string, at: string) => ({ skill_node: `zhixing:syn-dim-${node}`, state, at });
  const mastery_history = [
    hist("guifan", "understand", "2026-10-01T00:00:00+08:00"),
    hist("guifan", "stable", "2026-10-06T00:00:00+08:00"),
    hist("guifan", "execute", "2026-10-08T00:00:00+08:00"), // 窗内最后一条 execute > 基线 understand → up
    hist("dingwei", "stable", "2026-09-30T00:00:00+08:00"),
    hist("dingwei", "understand", "2026-10-07T00:00:00+08:00"), // down
    hist("zhuanhua", "execute", "2026-10-01T00:00:00+08:00"), // 窗内没有记录 → flat
    hist("shizheng", "execute", "2026-10-06T00:00:00+08:00"), // 窗前没有记录 → new
    hist("shenti", "understand", "2026-10-02T00:00:00+08:00"),
    hist("shenti", "understand", "2026-10-09T00:00:00+08:00"), // 同状态 → flat
    hist("shenti", "retain", "2026-10-12T00:00:00+08:00"), // 窗后（= to）不看
  ];
  const { status, body: b } = await call(make(), { context: ctx(false), window: WINDOW, audience: "teacher", mastery_history });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("ReportResponse", b), { ok: true });
  assert.deepEqual(summary(b), { shenti: ["flat", 0], dingwei: ["down", 0], zhuanhua: ["flat", 0], guifan: ["up", 0], shizheng: ["new", 0] });
  assert.deepEqual(b.evidence_ids, []);
  assert.equal(b.semantics, "formal");
});

test("低置信度：错因记录置信度低时整体置信度随之降低；只有已准入证据时为 formal", async () => {
  const evidence = [text(1, "synthetic-m3-01", "2026-10-06T10:00:00+08:00", 1, "admitted")];
  const error_attributions = [
    attr("dingwei_cuo", "locating_information", 0.4, [eid(1)]),
    attr("zhuanhua_cuo", "thinking_method", 0.1, [eid(1)]),
    attr("dingwei_cuo", "locating_information", 0.3, [eid(1)]),
  ];
  const { body: b } = await call(make(), { context: ctx(), window: WINDOW, audience: "parent", evidence, error_attributions });
  assert.equal(b.confidence, 0.1);
  assert.deepEqual(counts(b), { shenti_cuo: 0, dingwei_cuo: 2, zhuanhua_cuo: 1, guifan_cuo: 0 });
  assert.deepEqual(b.evidence_ids, [eid(1)]);
  assert.equal(b.semantics, "formal");
  assert.equal(b.human_review_required, false);
});

test("上游数据不合格：别科引用 → 400 SUBJECT_MISMATCH；语文错因编码或大类与映射表不符 → 422；时间窗倒置 → 422；都不回显原文", async () => {
  const good = text(1, "synthetic-m3-01", "2026-10-06T10:00:00+08:00", 1);
  const cases: [Json, number, string][] = [
    [{ evidence: [{ ...good, item_ref: "wenli:synthetic-q01" }] }, 400, "SUBJECT_MISMATCH"],
    [{ evidence: [{ ...good, skill_node: "aya:K-QF-0301" }] }, 400, "SUBJECT_MISMATCH"],
    [{ mastery_history: [{ skill_node: "wenli:CHN.SYN.D1_L1", state: "execute", at: "2026-10-06T00:00:00+08:00" }] }, 400, "SUBJECT_MISMATCH"],
    [{ error_attributions: [{ ...attr("dingwei_cuo", "locating_information", 0.4, [eid(1)]), skill_node: "wenli:CHN.SYN.D1_L1" }] }, 400, "SUBJECT_MISMATCH"],
    // 语文的“定位性”（CHN 口径 dingwei）不是道法的“定位错”（MORAL 口径 dingwei_cuo），不混算
    [{ error_attributions: [attr("dingwei", "locating_information", 0.4, [eid(1)])] }, 422, "INVALID_REQUEST"],
    [{ error_attributions: [attr("dingwei_cuo", "knowledge", 0.4, [eid(1)])] }, 422, "INVALID_REQUEST"],
    [{ window: { from: WINDOW.to, to: WINDOW.from } }, 422, "INVALID_REQUEST"],
    [{ window: { from: WINDOW.from, to: WINDOW.from } }, 422, "INVALID_REQUEST"],
    [{ context: { ...ctx(), content_version: { ...realPack.pack.curriculum, content_version: "9.9.9" } } }, 404, "CONTENT_VERSION_NOT_FOUND"],
    [{ highlight_codes: ["x"] }, 400, "INVALID_REQUEST"],
  ];
  for (const [patch, status, code] of cases) {
    const body = { context: ctx(), window: WINDOW, audience: "parent", evidence: [good], ...patch };
    const r = await call(make(), body);
    assert.equal(r.status, status, `${JSON.stringify(patch).slice(0, 80)} → ${JSON.stringify(r.body)}`);
    assert.equal(r.body.code, code);
    assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
    assert.ok(!JSON.stringify(r.body).includes("合成作答原文"));
  }
});

test("不含自由文本：三种受众输出同样的字段；所有字符串都是编码、ID 或版本；没有 highlight_codes；幂等 409", async () => {
  const a = make();
  const evidence = [text(1, "synthetic-m3-02", "2026-10-06T10:00:00+08:00", 1)];
  const error_attributions = [attr("guifan_cuo", "expression_convention", 0.4, [eid(1)])];
  const results: Json[] = [];
  for (const audience of ["student", "parent", "teacher"]) {
    const body = { context: ctx(), window: WINDOW, audience, evidence, error_attributions };
    const r = await call(a, body);
    assert.equal(r.status, 200);
    results.push(r.body);
    const again = await call(a, structuredClone(body));
    assert.deepEqual(again.body, r.body);
    assert.equal((await call(a, { ...structuredClone(body), audience: audience === "student" ? "parent" : "student" })).status, 409);
  }
  assert.deepEqual(results[1], results[0]);
  assert.deepEqual(results[2], results[0]);
  const b = results[0];
  assert.deepEqual(Object.keys(b.result).sort(), ["error_distribution", "skill_summary"]);
  const CODE = /^[a-z0-9_]{1,48}$|^zhixing:[A-Za-z0-9_.\-]+$|^[0-9a-f-]{36}$|^algo:[a-z0-9\-]+@[0-9.]+;items:[a-z0-9\-]+@[0-9.]+$|^algo:[a-z0-9\-]+@[0-9.]+$|^1\.0\.0$/;
  for (const [path, v] of leaves(b)) {
    if (typeof v === "string") assert.match(v, CODE, `${path} = ${v}`);
    // 不出现中文（任何结论句、评价都会是中文）
    if (typeof v === "string") assert.ok(!/[一-鿿]/.test(v), path);
  }
});
