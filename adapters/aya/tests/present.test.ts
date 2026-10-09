// present_item（契约 1.1.0 候选版，PLT-12：specs/present-item-contract/spec.md）的单测与泄露检查。全部是合成数据（规则 8）：
// 真实内容包不收录题干，只用来测 withheld；带题面的情形用合成内容包（tests/helpers.ts syntheticPack）。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { createAdapter } from "../src/adapter.ts";
import { applyStemOverlay, DEFAULT_PACK_PATH, loadPackFrom, type PackItem } from "../src/content.ts";
import { contractPath, loadContract } from "../src/contract.ts";
import { FILL_MAX_LENGTH } from "../src/methods/present.ts";
import { ALT_RE, IMAGE_REF_RE, LATEX_RE, TEXT_RE } from "../src/rich-text.ts";
import { contextV1, contextV11, contract, keysOf, leaves, makeAdapter, presentContext, realPack, STUDENT, syntheticPack, taxonomy, type Json } from "./helpers.ts";

const adapter = makeAdapter();
const CV = { ...realPack.pack.curriculum };
const body = (item: string, ctx: Record<string, unknown> = presentContext(), extra: Record<string, unknown> = {}) =>
  ({ context: ctx, item_ref: `aya:${item}`, content_version: { ...CV }, ...extra });
const call = (a: ReturnType<typeof makeAdapter>, b: unknown) => a.handle("POST", "present_item", b);
/** 不带 context.content_version 的 1.1.0 上下文（只看请求顶层的内容版本） */
const noCv = () => {
  const { content_version: _drop, ...ctx } = presentContext();
  return ctx;
};

/** 题面里不许出现的东西：内容包题目对象里除题面之外的字段名（答案、陷阱、提示、审核、来源、节点…）与答案类字段名 */
const FORBIDDEN_KEYS = ["answer", "answers", "traps", "wrongAnswerTraps", "hintPath", "hint_path", "reviewStatus", "source", "source_ref",
  "admission_review", "mainNodes", "usage", "estMinutes", "option_content", "is_correct", "correct", "explanation", "analysis", "rubric",
  "rubric_version", "scoring_points", "solution"];

function assertNoLeak(resp: Json, item: PackItem) {
  const keys = new Set(keysOf(resp));
  for (const k of FORBIDDEN_KEYS) assert.ok(!keys.has(k), `${item.id}：响应里出现字段 ${k}`);
  // 版本串（名称@x.y.z）里的数字段会和数字答案偶然重合（0.1 ⊂ 0.1.0），版本串只按完全相等查
  const all = leaves(resp).map(([, v]) => v).filter((v): v is string => typeof v === "string");
  const strings = all.filter((s) => !s.includes("@"));
  if (item.format === "fill") for (const a of item.answer) assert.ok(!all.includes(a), `${item.id}：响应里有一项正好是答案`);
  // 标准答案（3 个字符以上按子串，更短的按完全相等——选项键 A、B 本来就在选项里，单字符答案只查“整串等于答案”的文字片段）
  for (const a of item.answer) {
    if (a.length >= 3) assert.ok(!strings.some((s) => s.includes(a)), `${item.id}：响应里出现了答案`);
  }
  for (const h of item.hintPath) assert.ok(!strings.some((s) => s.includes(h)), `${item.id}：响应里出现了提示原文`);
  const traps = [...(item.options ?? []).flatMap((o) => o.traps ?? []), ...(item.wrongAnswerTraps ?? []).flatMap((w) => [...w.traps, ...w.pattern])];
  for (const t of traps) if (t.length >= 3) assert.ok(!strings.some((s) => s.includes(t)), `${item.id}：响应里出现了陷阱码或典型错误答案`);
  if (item.source.source_ref) assert.ok(!strings.some((s) => s.includes(item.source.source_ref!)), `${item.id}：响应里出现了来源编号`);
}

// ---------- 真实内容包：全部 withheld ----------
test("真实内容包的全部 721 道题：题面 withheld，只有题型、选项键与作答格式；合契约，不泄露答案、陷阱、提示", () => {
  for (const item of realPack.pack.items) {
    const r = call(adapter, body(item.id));
    assert.equal(r.status, 200, `${item.id} ${JSON.stringify(r.body).slice(0, 200)}`);
    const b = r.body as Json;
    assert.deepEqual(contract.validate("PresentItemResponse", b, "1.1.0"), { ok: true });
    const res = b.result;
    assert.equal(res.item_ref, `aya:${item.id}`);
    assert.equal(res.content_state, "withheld");
    assert.equal(res.license_state, "unverified");
    assert.equal(res.item_version, `${realPack.pack.pack_name}@${realPack.pack.pack_version}`);
    assert.ok(!("stem" in res));
    assert.deepEqual(b.evidence_ids, []);
    assert.ok(!("evidence" in b));
    assert.equal(b.semantics, "practice"); // 全部未准入
    assert.equal(b.human_review_required, false);
    if (item.format === "fill") {
      assert.equal(res.item_type, "fill_blank");
      assert.ok(!("options" in res));
      // 填空的作答格式一律 expression：不按标准答案像不像数字给
      assert.deepEqual(res.answer_format, { kind: "expression", max_length: FILL_MAX_LENGTH });
    } else {
      assert.equal(res.item_type, item.format === "single" ? "single_choice" : "multi_choice");
      assert.deepEqual(res.options, (item.options ?? []).map((o) => ({ key: o.key })));
      // 多选的可选上限取选项数，不取正确选项数（不能从作答格式推出答案个数）
      const max = item.format === "single" ? 1 : (item.options ?? []).length;
      assert.deepEqual(res.answer_format, { kind: "choice", min_choices: 1, max_choices: max });
    }
    assertNoLeak(b, item);
  }
});

// ---------- 合成内容包：完整题面 ----------
const STEM_FILL = [{ type: "paragraph", runs: [{ type: "text", text: "（合成题面）计算 " }, { type: "math", latex: "3\\times 4" }, { type: "text", text: "。" }] }];
const STEM_CHOICE = [
  { type: "paragraph", runs: [{ type: "text", text: "（合成题面）下面哪一项等于 " }, { type: "math", latex: "\\frac{1}{2}" }, { type: "text", text: "？" }] },
  { type: "paragraph", runs: [{ type: "image", image_ref: "objstore://synthetic/aya/fig-01.png", alt: "（合成）示意图" }] },
];
const OPTIONS = ["A", "B", "C", "D"].map((key) => ({ key }));
const OPTION_CONTENT = { A: [{ type: "math", latex: "0.5" }], B: [{ type: "text", text: "（合成）二" }], C: [{ type: "math", latex: "2" }], D: [{ type: "text", text: "（合成）零点二" }] };
const SYN_ITEMS: Partial<PackItem>[] = [
  { format: "fill", answer: ["12"], stem: STEM_FILL as PackItem["stem"] },
  { format: "single", answer: ["A"], options: OPTIONS, stem: STEM_CHOICE as PackItem["stem"], option_content: OPTION_CONTENT as PackItem["option_content"] },
  { format: "multi", answer: ["A", "C"], options: OPTIONS, stem: STEM_CHOICE as PackItem["stem"], option_content: OPTION_CONTENT as PackItem["option_content"] },
  { format: "fill", answer: ["2x-2"] }, // 没有题干
];

test("合成内容包：有题干的题给完整题面，原样投影受限富文本；没有题干的题 withheld；多选上限取选项数", () => {
  const pack = syntheticPack(SYN_ITEMS);
  const a = makeAdapter(pack);
  const [fill, single, multi, bare] = ["SYN-0", "SYN-1", "SYN-2", "SYN-3"].map((id) => call(a, body(id)).body as Json);
  for (const b of [fill, single, multi, bare]) assert.deepEqual(contract.validate("PresentItemResponse", b, "1.1.0"), { ok: true });
  assert.equal(fill.result.content_state, "complete");
  assert.deepEqual(fill.result.stem, STEM_FILL);
  assert.equal(fill.result.item_type, "fill_blank");
  assert.deepEqual(single.result.stem, STEM_CHOICE);
  assert.deepEqual(single.result.options, OPTIONS.map((o) => ({ key: o.key, content: (OPTION_CONTENT as Json)[o.key] })));
  assert.deepEqual(single.result.answer_format, { kind: "choice", min_choices: 1, max_choices: 1 });
  assert.deepEqual(multi.result.answer_format, { kind: "choice", min_choices: 1, max_choices: 4 }, "不取正确选项数 2");
  assert.equal(bare.result.content_state, "withheld");
  assert.ok(!("stem" in bare.result));
  pack.pack.items.forEach((it, i) => assertNoLeak([fill, single, multi, bare][i], it));
  // 答案 12 只在作答里，不在题面里（题面写的是 3×4）
  assert.ok(!JSON.stringify(fill).includes("\"12\""));
});

test("合成内容包：授权已核的自编题 → original；授权被拒（rejected）的题即使有题干也 withheld（restricted 不给题面）", () => {
  const pack = syntheticPack(SYN_ITEMS.slice(0, 2), {
    sourceVerified: true,
    decisionOverrides: [{}, { admission: "rejected", license_status: "denied", source_type: "exam_paper" }],
  });
  const a = makeAdapter(pack);
  const ok = call(a, body("SYN-0")).body as Json;
  const denied = call(a, body("SYN-1")).body as Json;
  assert.equal(ok.result.license_state, "original");
  assert.equal(ok.result.content_state, "complete");
  assert.equal(ok.semantics, "formal"); // Q2、来源已核 → admitted
  assert.equal(denied.result.license_state, "restricted");
  assert.equal(denied.result.content_state, "withheld");
  assert.ok(!("stem" in denied.result));
  assert.deepEqual(denied.result.options, OPTIONS);
  assert.equal(denied.semantics, "practice");
  assert.deepEqual(contract.validate("PresentItemResponse", denied, "1.1.0"), { ok: true });
});

test("内容包加载：题干不是受限富文本、选项内容与键对不上、没有题干却有选项内容，一律拒绝启动", () => {
  const bad: [string, Partial<PackItem>][] = [
    ["文字含 HTML", { stem: [{ type: "paragraph", runs: [{ type: "text", text: "<img src=x onerror=alert(1)>" }] }] as Json }],
    ["公式含 \\href", { stem: [{ type: "paragraph", runs: [{ type: "math", latex: "\\href{https://x.invalid}{a}" }] }] as Json }],
    ["片段类型未知", { stem: [{ type: "paragraph", runs: [{ type: "html", html: "<b>x</b>" }] }] as Json }],
    ["片段夹带答案字段", { stem: [{ type: "paragraph", runs: [{ type: "text", text: "（合成）", answer: "12" }] }] as Json }],
    ["段落夹带解析", { stem: [{ type: "paragraph", runs: [{ type: "text", text: "（合成）" }], analysis: "（合成）" }] as Json }],
    ["图片是公网地址", { stem: [{ type: "paragraph", runs: [{ type: "image", image_ref: "https://x.invalid/a.png" }] }] as Json }],
    ["空题干", { stem: [] }],
    ["选择题有题干没有选项内容", { format: "single", options: OPTIONS, stem: STEM_CHOICE as Json }],
    ["选项内容缺一个键", { format: "single", options: OPTIONS, stem: STEM_CHOICE as Json, option_content: { A: OPTION_CONTENT.A } as Json }],
    ["没有题干却有选项内容", { format: "single", options: OPTIONS, option_content: OPTION_CONTENT as Json }],
  ];
  for (const [name, it] of bad) assert.throws(() => syntheticPack([{ format: "fill", answer: ["1"], ...it }]), /内容包不合格/, name);
});

test("题面字段的正则与契约 v1.1 原文一致（契约改了这里要一起改）", () => {
  const s = (parse(readFileSync(contractPath("1.1.0"), "utf8")) as Json).components.schemas;
  const unslash = (re: RegExp) => re.source.replaceAll("\\/", "/");
  assert.equal(unslash(TEXT_RE), s.RichTextRun.properties.text.pattern);
  assert.equal(unslash(ALT_RE), s.RichImageRun.properties.alt.pattern);
  assert.equal(unslash(LATEX_RE), s.RichMathRun.properties.latex.pattern);
  assert.equal(unslash(IMAGE_REF_RE), s.RichImageRun.properties.image_ref.pattern);
});

// ---------- 请求：版本、计量、内容版本、题目、幂等 ----------
test("只按 1.1.0 应答：0.9.0 / 1.0.0 请求 501；候选版没开时 400 CONTRACT_VERSION_UNSUPPORTED", () => {
  const item = realPack.pack.items[0]!.id;
  const v10 = call(adapter, body(item, contextV1()));
  assert.equal(v10.status, 501);
  assert.equal((v10.body as Json).code, "METHOD_NOT_IMPLEMENTED");
  const off = call(makeAdapter(realPack, undefined, false), body(item));
  assert.equal(off.status, 400);
  assert.equal((off.body as Json).code, "CONTRACT_VERSION_UNSUPPORTED");
});

test("坏请求：带 usage_id、带学生 ID、L0、缺 content_version、内容版本不是本包、两处内容版本不一致、别科题目、题目不存在", () => {
  const item = realPack.pack.items[0]!.id;
  const cases: [string, unknown, number, string][] = [
    ["带 usage_id（题面不计量）", body(item, presentContext({ usage_id: "00000000-0000-4000-9000-0000000007ff" })), 400, "INVALID_REQUEST"],
    ["带学生 ID（题面与学生无关）", body(item, presentContext({ student_ref: STUDENT })), 400, "INVALID_REQUEST"],
    ["数据级别 L0", body(item, presentContext({ data_class: "L0" })), 400, "INVALID_REQUEST"],
    ["用六方法的上下文（RequestContext）", body(item, contextV11()), 400, "INVALID_REQUEST"],
    ["缺 content_version", (() => { const b = body(item) as Json; delete b.content_version; return b; })(), 400, "INVALID_REQUEST"],
    ["内容版本不是本包", body(item, noCv(), { content_version: { ...CV, content_version: "9.9.9" } }), 404, "CONTENT_VERSION_NOT_FOUND"],
    ["两处内容版本不一致", body(item, presentContext({ content_version: { ...CV, grade: 8 } })), 400, "INVALID_REQUEST"],
    ["别科题目", { ...body(item), item_ref: "zhixing:synthetic-m3-01" }, 400, "INVALID_REQUEST"],
    ["题目不存在", body("Q-NOT-IN-PACK"), 404, "ITEM_NOT_FOUND"],
    ["要求带答案", body(item, presentContext(), { include_answer: true }), 400, "INVALID_REQUEST"],
  ];
  for (const [name, b, status, code] of cases) {
    const r = call(adapter, b);
    assert.equal(r.status, status, `${name}：${JSON.stringify(r.body).slice(0, 200)}`);
    assert.equal((r.body as Json).code, code, name);
  }
  // 两处内容版本相同则照常应答；L2、带幂等键也照常应答
  assert.equal(call(adapter, body(item, presentContext({ content_version: { ...CV } }))).status, 200);
  assert.equal(call(adapter, body(item, presentContext({ data_class: "L2", idempotency_key: "4e000000-0000-4000-8000-0000000007a1" }))).status, 200);
});

test("只读、不进幂等缓存：同一道题逐字节相同（带不带键、换 request_id、进程重启）；同 key 换题照常应答，不挤掉 grade 的幂等条目", () => {
  const [i1, i2] = realPack.pack.items.map((i) => i.id);
  const key = "4e000000-0000-4000-8000-0000000007a2";
  const r1 = call(adapter, body(i1!));
  const r2 = call(adapter, body(i1!, presentContext({ idempotency_key: key })));
  assert.equal(JSON.stringify(r2.body), JSON.stringify(r1.body));
  const other = call(adapter, body(i2!, presentContext({ idempotency_key: key })));
  assert.equal(other.status, 200, "同 key 换题：题面不做冲突检测");
  assert.equal((other.body as Json).result.item_ref, `aya:${i2}`);
  // 另一个适配器实例（进程重启）对同一道题给出逐字节相同的题面：平台可按（item_ref, item_version）缓存
  assert.equal(JSON.stringify(call(makeAdapter(), body(i1!)).body), JSON.stringify(r1.body));

  // 幂等缓存只有 1 条时：grade 记下 key 后连取 5 道题面，grade 同 key 换请求体仍 409（题面没有挤掉 grade 的条目）
  const small = createAdapter({ content: realPack, taxonomy, contract, allowCandidate: true, idempotencyCacheSize: 1 });
  const fill = realPack.pack.items.find((i) => i.format === "fill")!;
  const gradeCtx = { ...contextV11(), usage_id: "00000000-0000-4000-9000-0000000007a3" };
  const grade = (text: string) => small.handle("POST", "grade", {
    context: gradeCtx,
    attempt: { attempt_id: "00000000-0000-4000-8000-0000000007a4", item_ref: `aya:${fill.id}`, occurred_at: "2026-10-09T19:30:00+08:00", response: { text } },
    rubric_version: "aya-symbolic-ts@0.1.0",
  });
  assert.equal(grade(fill.answer[0]!).status, 200);
  for (const it of realPack.pack.items.slice(0, 5)) {
    assert.equal(call(small, body(it.id, presentContext({ idempotency_key: gradeCtx.idempotency_key }))).status, 200);
  }
  const conflict = grade(`${fill.answer[0]!}+1`);
  assert.equal(conflict.status, 409);
  assert.equal((conflict.body as Json).code, "IDEMPOTENCY_CONFLICT");
});

test("能力声明：默认版本 1.1.0 时声明 present_item（七个方法）；1.0.0、0.9.0 时不声明", () => {
  const v11 = createAdapter({ content: realPack, taxonomy, contract: loadContract({ defaultVersion: "1.1.0" }), allowCandidate: true });
  const c11 = v11.handle("GET", "capabilities", undefined).body as Json;
  assert.equal(c11.contract_version, "1.1.0");
  assert.deepEqual(c11.methods.present_item, { implemented: true, async: false });
  assert.deepEqual(contract.validate("Capabilities", c11, "1.1.0"), { ok: true });
  const v10 = createAdapter({ content: realPack, taxonomy, contract: loadContract({ defaultVersion: "1.0.0" }), allowCandidate: true });
  assert.ok(!("present_item" in (v10.handle("GET", "capabilities", undefined).body as Json).methods));
  assert.ok(!("present_item" in (adapter.handle("GET", "capabilities", undefined).body as Json).methods));
});

test("1.1.0 下六个已有方法照旧应答（grade 示例）：契约版本回 1.1.0", () => {
  const item = realPack.pack.items.find((i) => i.format === "fill")!;
  const r = adapter.handle("POST", "grade", {
    context: { ...contextV11(), usage_id: "00000000-0000-4000-9000-0000000007ab" },
    attempt: { attempt_id: "00000000-0000-4000-8000-0000000007ac", item_ref: `aya:${item.id}`, occurred_at: "2026-10-09T19:30:00+08:00", response: { text: item.answer[0] } },
    rubric_version: "aya-symbolic-ts@0.1.0",
  });
  assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
  assert.equal((r.body as Json).contract_version, "1.1.0");
  assert.deepEqual(contract.validate("GradeResponse", r.body, "1.1.0"), { ok: true });
});

// ---------- 替代内容包与题面叠加文件（审查阻断 3、建议 14） ----------
const OVERLAY = fileURLToPath(new URL("../conformance/stems.synthetic.json", import.meta.url));
const REAL_PACK = fileURLToPath(DEFAULT_PACK_PATH);

test("替代内容包、题面叠加文件只在候选版开关打开时读；开关没开却设了就拒绝（抛错）", () => {
  assert.throws(() => loadPackFrom({ packPath: REAL_PACK, candidateAllowed: false }), /候选版开关/);
  assert.throws(() => loadPackFrom({ overlayPath: OVERLAY, candidateAllowed: false }), /候选版开关/);
  // 都不设：真实内容包，版本号不变
  const plain = loadPackFrom({ candidateAllowed: false });
  assert.equal(plain.versionTag, realPack.pack.pack_version);
  assert.equal(plain.itemsSourceVersion, realPack.itemsSourceVersion);
});

test("题面叠加文件：给两道题加合成题面；版本号接文件 sha256 前 8 位（items 段与 item_version 都带），与真实内容包区分", () => {
  const pack = loadPackFrom({ overlayPath: OVERLAY, candidateAllowed: true });
  const digest = createHash("sha256").update("overlay\0").update(readFileSync(OVERLAY, "utf8")).digest("hex").slice(0, 8);
  assert.equal(pack.versionTag, `${realPack.pack.pack_version}-${digest}`);
  assert.equal(pack.itemsSourceVersion, `items:${realPack.pack.pack_name}@${realPack.pack.pack_version}-${digest}`);
  const a = makeAdapter(pack);
  const fill = call(a, body("Q-ALG-U04-D-003")).body as Json;
  assert.deepEqual(contract.validate("PresentItemResponse", fill, "1.1.0"), { ok: true });
  assert.equal(fill.result.content_state, "complete");
  assert.equal(fill.result.item_version, `${realPack.pack.pack_name}@${pack.versionTag}`);
  assert.ok(fill.source_version.endsWith(pack.itemsSourceVersion));
  assertNoLeak(fill, pack.itemById.get("Q-ALG-U04-D-003")!);
  // 替代包本身也计入摘要：同一份内容包作替代包时版本号与真实包不同
  const alt = loadPackFrom({ packPath: REAL_PACK, candidateAllowed: true });
  assert.match(alt.versionTag, new RegExp(`^${realPack.pack.pack_version.replaceAll(".", "\\.")}-[0-9a-f]{8}$`));
  assert.throws(() => applyStemOverlay(realPack.pack, { format: "aya-stem-overlay/1", items: { "Q-NOT-IN-PACK": { stem: STEM_FILL as Json } } }), /内容包没有的题/);
});

test("启动入口：设了 AYA_CONTENT_PACK 却没开候选版开关 → 拒绝启动（退出码 1）", () => {
  const main = fileURLToPath(new URL("../src/main.ts", import.meta.url));
  const env = { PATH: process.env.PATH ?? "", AYA_ADAPTER_SERVICE_TOKEN: "synthetic-test-credential-0123456789abcdef", PORT: "0", AYA_CONTENT_PACK: REAL_PACK };
  const r = spawnSync(process.execPath, ["--no-warnings", main], { env, encoding: "utf8", timeout: 20_000 });
  assert.equal(r.status, 1, r.stderr.slice(0, 300));
  assert.match(r.stderr, /候选版开关/);
  const o = spawnSync(process.execPath, ["--no-warnings", main, "--stem-overlay", OVERLAY], { env: { ...env, AYA_CONTENT_PACK: "" }, encoding: "utf8", timeout: 20_000 });
  assert.equal(o.status, 1, o.stderr.slice(0, 300));
  assert.match(o.stderr, /候选版开关/);
});

test("内容包加载：题面写出标准答案、带答案或解析标记、总字数超过 8000、含双向控制符或零宽字符，一律拒绝启动", () => {
  const para = (text: string) => [{ type: "paragraph", runs: [{ type: "text", text }] }] as Json;
  const bad: [string, Partial<PackItem>][] = [
    ["填空题干写出答案", { answer: ["4n+16"], stem: [{ type: "paragraph", runs: [{ type: "text", text: "（合成）" }, { type: "math", latex: "4n + 16" }] }] as Json }],
    ["题干有“答案”", { stem: para("（合成）计算 3+4，答案是 7") }],
    ["题干有“解析”", { stem: para("（合成）先看解析再作答") }],
    ["选项有“故选”", { format: "single", options: OPTIONS, stem: STEM_CHOICE as Json, option_content: { ...OPTION_CONTENT, D: [{ type: "text", text: "（合成）故选 A" }] } as Json }],
    ["总字数超过 8000", { stem: [1, 2, 3, 4, 5].map(() => ({ type: "paragraph", runs: [{ type: "text", text: "合".repeat(1700) }] })) as Json }],
    ["双向控制符", { stem: para("（合成）x=1\u202e2=y") }],
    ["零宽字符", { stem: para("（合成）a\u200bb") }],
    ["C1 控制字符", { stem: para("（合成）a\u0085b") }],
  ];
  for (const [name, it] of bad) assert.throws(() => syntheticPack([{ format: "fill", answer: ["1"], ...it }]), /内容包不合格/, name);
  // 正好 8000 字可以；单字符答案不查（题号、系数里本来就有）；“求解析式”是数学名词，不算解析标记
  assert.doesNotThrow(() => syntheticPack([{ format: "fill", answer: ["1"], stem: [1, 2, 3, 4].map(() => ({ type: "paragraph", runs: [{ type: "text", text: "合".repeat(2000) }] })) as Json }]));
  assert.doesNotThrow(() => syntheticPack([{ format: "fill", answer: ["2"], stem: [{ type: "paragraph", runs: [{ type: "text", text: "（合成）第 2 题" }, { type: "math", latex: "x^2-2x+1" }] }] as Json }]));
  assert.doesNotThrow(() => syntheticPack([{ format: "fill", answer: ["4"], stem: para("（合成）求解析式 · 第 4 题") }]));
});
