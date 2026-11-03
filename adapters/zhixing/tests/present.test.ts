// present_item（契约 1.1.0 候选版，PLT-12：specs/present-item-contract/spec.md）的单测与泄露检查。
// 全部是合成数据（规则 8）：内容包里的题与材料都是虚构的。present_item 不调模型，适配器不配路由（route: null）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter, MAX_ANSWER_CHARS } from "../src/adapter.ts";
import { indexPack, type ContentPack, type PackItem } from "../src/content.ts";
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { contractPath, loadContract } from "../src/contract.ts";
import { presentationProblems, TEXT_RE } from "../src/present.ts";
import { ANSWERS, contextV1, contract, gradeBody, harness, leaves, realPack, STUDENT, taxonomy, type Json } from "./helpers.ts";

const make = (content = realPack, allowCandidate = true, defaultVersion?: "1.0.0" | "1.1.0") =>
  createAdapter({ content, taxonomy, contract: defaultVersion ? loadContract({ defaultVersion }) : contract, route: null, allowCandidate });
const adapter = make();
const CV = { ...realPack.pack.curriculum };
let n = 0;
/** 1.1.0 的 RequestContext（六个已有方法的上下文；题面请求用它应被拒绝） */
function requestCtx11(overrides: Record<string, unknown> = {}) {
  n++;
  const { usage_id: _drop, ...c } = contextV1({ contract_version: "1.1.0", idempotency_key: `5b000000-0000-4000-8000-${String(n).padStart(12, "0")}` });
  return { ...c, ...overrides };
}
/** 1.1.0 PresentItemContext：不带 student_ref、usage_id，幂等键可选（默认不带），L1 */
function ctx11(overrides: Record<string, unknown> = {}) {
  n++;
  return {
    request_id: `00000000-0000-4000-8000-5${String(n).padStart(11, "0")}`,
    contract_version: "1.1.0",
    subject: "zhixing",
    data_class: "L1",
    content_version: { ...CV },
    ...overrides,
  };
}
const body = (item: string, c: Record<string, unknown> = ctx11(), extra: Record<string, unknown> = {}) =>
  ({ context: c, item_ref: `zhixing:${item}`, content_version: { ...CV }, ...extra });
const call = (a: ReturnType<typeof make>, b: unknown) => a.handle("POST", "present_item", b);

/** 内容包里除题面之外的东西：采分点、关键词族、给分规则、常见失分、Rubric 版本、标题（不在材料与设问里出现的 3 个字符以上的串） */
function secretsOf(item: PackItem): string[] {
  const visible = `${item.material_text}\n${item.prompt_question}`;
  // rubric_version 不算：合成内容包的 Rubric 版本与内容包版本同名（zhixing-synthetic@0.1.0），字段名另由 FORBIDDEN_KEYS 查
  const raw = [item.title, ...item.points.flatMap((p) => [
    p.point_id, ...p.keyword_family.core, ...p.keyword_family.accepted, ...p.keyword_family.colloquial,
    p.required_logic ?? "", p.material_linkage ?? "", p.partial_credit_rule.description,
    ...p.common_errors.flatMap((e) => [e.pattern, e.note]),
  ])];
  return raw.filter((s) => s.length >= 3 && !visible.includes(s));
}
const FORBIDDEN_KEYS = ["points", "keyword_family", "partial_credit_rule", "common_errors", "rubric_version", "total_score", "title",
  "answer", "explanation", "analysis", "scoring_points", "score", "required_logic", "material_linkage"];

test("内容包全部道法题：题面完整（材料按换行分段 + 设问），简答、文字作答、合契约；不泄露采分点与 Rubric", async () => {
  const daofa = realPack.pack.items.filter((it) => it.discipline === "daofa");
  assert.ok(daofa.length >= 4);
  for (const item of daofa) {
    const r = await call(adapter, body(item.id));
    assert.equal(r.status, 200, `${item.id} ${JSON.stringify(r.body).slice(0, 200)}`);
    const b = r.body as Json;
    assert.deepEqual(contract.validate("PresentItemResponse", b, "1.1.0"), { ok: true });
    const res = b.result;
    assert.equal(res.item_ref, `zhixing:${item.id}`);
    assert.equal(res.item_type, "short_answer");
    assert.equal(res.content_state, "complete");
    assert.equal(res.license_state, "original"); // 合成内容包
    assert.equal(res.item_version, `${realPack.pack.pack_name}@${realPack.pack.pack_version}`);
    assert.deepEqual(res.answer_format, { kind: "text", max_length: MAX_ANSWER_CHARS });
    const paras = res.stem.map((p: Json) => p.runs.map((x: Json) => x.text).join(""));
    assert.equal(paras.at(-1), item.prompt_question);
    assert.equal(paras.slice(0, -1).join(""), item.material_text.split(/\r?\n+/u).map((s) => s.trim()).join(""));
    assert.ok(!("options" in res));
    assert.deepEqual(b.evidence_ids, []);
    assert.ok(!("evidence" in b));
    assert.equal(b.semantics, "practice"); // 全部 pending_review
    const keys = JSON.stringify(b);
    for (const k of FORBIDDEN_KEYS) assert.ok(!keys.includes(`"${k}"`), `${item.id}：响应里出现字段 ${k}`);
    const strings = leaves(b).map(([, v]) => v).filter((v): v is string => typeof v === "string");
    for (const s of secretsOf(item)) assert.ok(!strings.some((x) => x.includes(s)), `${item.id}：响应里出现了采分点或 Rubric 文字`);
  }
});

test("材料里的换行分成段落；材料或设问含尖括号、控制字符的内容包拒绝启动", () => {
  const pack = structuredClone(realPack.pack) as ContentPack;
  pack.items[0]!.material_text = "（合成材料）第一段。\n\n（合成材料）第二段。";
  const loaded = indexPack(pack);
  return call(make(loaded), body(pack.items[0]!.id)).then((r) => {
    const stem = (r.body as Json).result.stem;
    assert.deepEqual(stem.map((p: Json) => p.runs[0].text), ["（合成材料）第一段。", "（合成材料）第二段。", pack.items[0]!.prompt_question]);
    for (const bad of ["<script>alert(1)</script>", "（合成）\u0007响铃", "（合成）x=1\u202e2=y", "（合成）a\u200bb", "（合成）a\u2028b",
      "（合成）a\ufeffb", "（合成）a\u0085b"]) {
      const p2 = structuredClone(realPack.pack) as ContentPack;
      p2.items[1]!.prompt_question = bad;
      assert.throws(() => indexPack(p2), /内容包不合格/, JSON.stringify(bad));
    }
    // 题面总字数：材料与设问合计超过 8000 字不合格，正好 8000 可以（内容包另限材料、设问各 2000 字，这里直接测题面核对）
    const long = (total: number): PackItem => {
      const it = structuredClone(realPack.pack.items[1]!);
      const rest = total - [...it.prompt_question].length;
      it.material_text = Array.from({ length: Math.ceil(rest / 2000) }, (_, i) => "合".repeat(Math.min(2000, rest - i * 2000))).join("\n");
      return it;
    };
    assert.deepEqual(presentationProblems(long(8000)), []);
    assert.equal(presentationProblems(long(8001)).length, 1);
  });
});

test("题面文字的正则与契约 v1.1 RichTextRun.text 原文一致（契约改了这里要一起改）", () => {
  const s = (parse(readFileSync(contractPath("1.1.0"), "utf8")) as Json).components.schemas;
  assert.equal(TEXT_RE.source.replaceAll("\\/", "/"), s.RichTextRun.properties.text.pattern);
});

test("非合成内容包的题：版权状态 unverified（PLT-12 不做版权核对）；已准入的题 semantics = formal", async () => {
  const pack = structuredClone(realPack.pack) as ContentPack;
  pack.synthetic = false;
  for (const it of pack.items) it.synthetic = false;
  pack.items[0]!.admission = "admitted";
  const r = (await call(make(indexPack(pack)), body(pack.items[0]!.id))).body as Json;
  assert.equal(r.result.license_state, "unverified");
  assert.equal(r.semantics, "formal");
  assert.deepEqual(contract.validate("PresentItemResponse", r, "1.1.0"), { ok: true });
});

test("只按 1.1.0 应答：1.0.0 请求 501；候选版没开时 400", async () => {
  const id = realPack.pack.items[0]!.id;
  const v10 = await call(adapter, body(id, contextV1()));
  assert.equal(v10.status, 501);
  assert.equal((v10.body as Json).code, "METHOD_NOT_IMPLEMENTED");
  const off = await call(make(realPack, false), body(id));
  assert.equal(off.status, 400);
  assert.equal((off.body as Json).code, "CONTRACT_VERSION_UNSUPPORTED");
});

test("坏请求：带 usage_id、内容版本不是本包、两处内容版本不一致、别科题目、题目不存在、历史题", async () => {
  const id = realPack.pack.items[0]!.id;
  const noCv = () => {
    const { content_version: _d, ...c } = ctx11();
    return c;
  };
  const history = structuredClone(realPack.pack) as ContentPack;
  history.items[0]!.discipline = "history";
  const cases: [string, ReturnType<typeof make>, unknown, number, string][] = [
    ["带 usage_id", adapter, body(id, ctx11({ usage_id: "00000000-0000-4000-9000-0000000007fe" })), 400, "INVALID_REQUEST"],
    ["带学生 ID（题面与学生无关）", adapter, body(id, ctx11({ student_ref: STUDENT })), 400, "INVALID_REQUEST"],
    ["数据级别 L0", adapter, body(id, ctx11({ data_class: "L0" })), 400, "INVALID_REQUEST"],
    ["用六方法的上下文（RequestContext）", adapter, body(id, requestCtx11()), 400, "INVALID_REQUEST"],
    ["内容版本不是本包", adapter, body(id, noCv(), { content_version: { ...CV, exam_year: 2030 } }), 404, "CONTENT_VERSION_NOT_FOUND"],
    ["两处内容版本不一致", adapter, body(id, ctx11({ content_version: { ...CV, grade: 8 } })), 400, "INVALID_REQUEST"],
    ["别科题目", adapter, { ...body(id), item_ref: "aya:Q-ALG-U01-D-001" }, 400, "INVALID_REQUEST"],
    ["题目不存在", adapter, body("synthetic-m3-99"), 404, "ITEM_NOT_FOUND"],
    ["历史题（契约 v1 只接道法）", make(indexPack(history)), body(id), 422, "DISCIPLINE_NOT_SUPPORTED"],
  ];
  for (const [name, a, b, status, code] of cases) {
    const r = await call(a, b);
    assert.equal(r.status, status, `${name}：${JSON.stringify(r.body).slice(0, 200)}`);
    assert.equal((r.body as Json).code, code, name);
  }
});

test("只读、不进幂等缓存：同一道题结果相同（带不带键、换 request_id）；同 key 换题照常应答，不挤掉 grade 的幂等条目", async () => {
  const [a, b2] = realPack.pack.items.map((i) => i.id);
  const key = "5b000000-0000-4000-8000-0000000007bc";
  const r1 = await call(adapter, body(a!));
  const r2 = await call(adapter, body(a!, ctx11({ idempotency_key: key, data_class: "L2" })));
  assert.equal(JSON.stringify(r2.body), JSON.stringify(r1.body));
  const other = await call(adapter, body(b2!, ctx11({ idempotency_key: key })));
  assert.equal(other.status, 200, "同 key 换题：题面不做冲突检测");
  assert.equal((other.body as Json).result.item_ref, `zhixing:${b2}`);

  // 幂等缓存只有 1 条：grade 记下 key 后连取 5 道题面，grade 同 key 换请求体仍 409（题面没有挤掉 grade 的条目）
  const h = await harness({ idempotencyCacheSize: 1 });
  try {
    const g = gradeBody("synthetic-m3-01", ANSWERS.partial01) as Json;
    g.context.contract_version = "1.1.0";
    g.context.idempotency_key = "5b000000-0000-4000-8000-0000000007bd";
    g.attempt.attempt_id = "00000000-0000-4000-8000-0000000007be";
    assert.equal((await h.adapter.handle("POST", "grade", g)).status, 200);
    for (const it of realPack.pack.items.slice(0, 5)) {
      const r = await h.adapter.handle("POST", "present_item", body(it.id, ctx11({ idempotency_key: g.context.idempotency_key })));
      assert.equal(r.status, 200);
    }
    const changed = structuredClone(g);
    changed.attempt.response.text = ANSWERS.full01;
    const conflict = await h.adapter.handle("POST", "grade", changed);
    assert.equal(conflict.status, 409);
    assert.equal((conflict.body as Json).code, "IDEMPOTENCY_CONFLICT");
  } finally {
    await h.close();
  }
});

test("能力声明：默认版本 1.1.0 时声明 present_item；1.0.0 时不声明", () => {
  const c11 = make(realPack, true, "1.1.0").handle("GET", "capabilities", undefined);
  return c11.then((r) => {
    const caps = r.body as Json;
    assert.equal(caps.contract_version, "1.1.0");
    assert.deepEqual(caps.methods.present_item, { implemented: true, async: false });
    assert.deepEqual(contract.validate("Capabilities", caps, "1.1.0"), { ok: true });
    return make(realPack, true, "1.0.0").handle("GET", "capabilities", undefined);
  }).then((r) => assert.ok(!("present_item" in (r.body as Json).methods)));
});
