// 内容包格式 3：逐题来源与准入决定（specs/release-gate-sprint4 设计要点第 7 节、待人确认 #9、#15）。
// 覆盖：决定文件的解析与校验、按决定重建（合并、重置、版本号）、确定性（两次构建逐字节相同）、加载时的自洽检查、
// 适配器只对已准入题给 formal 与满置信度。全部合成数据：来源编号、审核记录编号、导出批次都是合成的。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { indexPack, type ContentPack } from "../src/content.ts";
import {
  applyAdmissionDecisions,
  decisionsDigest,
  parseAdmissionDecisions,
  serializePack,
  type AdmissionDecisionFile,
} from "../src/pack/admission.ts";
import { contextV1, gradeBody, makeAdapter, makeAdapterV1, realPack, synReviewId, syntheticDecisionFile, type Json } from "./helpers.ts";

const HERE = new URL("..", import.meta.url).pathname;
const PACK_PATH = join(HERE, "content/pack.json");
const base = realPack.pack;
const ids = new Set(base.items.map((i) => i.id));
const parse = (raw: unknown) => parseAdmissionDecisions(raw, base.curriculum, ids);

// 三道真实题：一道单选（判分不涉及验证器边界）、两道任选
const CHOICE = base.items.find((i) => i.format === "single" && i.mainNodes.length > 0)!;
const [A2, A3] = base.items.filter((i) => i.id !== CHOICE.id).slice(0, 2);
const THREE = [CHOICE, A2!, A3!].map((i) => ({ id: i.id, reviewStatus: "Q2" as const }));
/** CHOICE admitted，A2 pending_review（Q1），A3 rejected（授权 denied） */
const threeDecisions = () =>
  syntheticDecisionFile(THREE, [{}, { admission: "pending_review", review_level: "Q1" }, { admission: "rejected", license_status: "denied", review_level: null }]);

test("真实内容包：格式 3，721 道题全部来源待核（source_unverified、来源未登记），没有套用任何决定，C9 严格且无标记", () => {
  assert.equal(base.format, "aya-content-pack/3");
  assert.equal(base.admission_decisions, null);
  assert.equal(base.pack_version, `0.3.0-${base.legacy_commit.slice(0, 7)}`);
  assert.ok(!("source_verified" in base));
  for (const it of base.items) {
    assert.equal(it.admission, "source_unverified", it.id);
    assert.deepEqual(it.source, { source_ref: null, source_type: "unknown", license_status: "unverified" }, it.id);
    assert.ok(!("admission_review" in it), it.id);
  }
  assert.equal(base.hint_scan.check, "C9");
  assert.equal(base.hint_scan.persona, "aya");
  assert.equal(base.hint_scan.mode, "strict");
  assert.deepEqual(base.hint_scan.flagged_items, []);
  assert.equal(base.hint_scan.texts, base.items.reduce((n, i) => n + i.hintPath.length, 0));
});

test("决定文件：合成的合格文件通过；摘要与决定的排列顺序、审核记录编号顺序无关", () => {
  const f = parse(threeDecisions());
  assert.equal(f.decisions.length, 3);
  const reversed = structuredClone(threeDecisions());
  reversed.decisions.reverse();
  assert.equal(decisionsDigest(parse(reversed)), decisionsDigest(f));
  const shuffled = structuredClone(threeDecisions());
  shuffled.decisions.reverse();
  shuffled.decisions[0]!.review_ids = [synReviewId(9), synReviewId(8)];
  const same = structuredClone(threeDecisions());
  same.decisions.reverse();
  same.decisions[0]!.review_ids = [synReviewId(8), synReviewId(9)];
  assert.equal(decisionsDigest(parse(shuffled)), decisionsDigest(parse(same)));
  assert.notEqual(decisionsDigest(parse(shuffled)), decisionsDigest(f));
  assert.match(decisionsDigest(f), /^sha256:[0-9a-f]{64}$/);
});

test("决定文件：不合格时整份拒绝（格式、字段、编号、题目、六段、自洽规则）", () => {
  type Raw = ReturnType<typeof threeDecisions> & Record<string, unknown>;
  const cases: [string, (f: Raw) => void, RegExp][] = [
    ["格式号", (f) => ((f as { format: string }).format = "aya-admission-decisions/2"), /format 应为/],
    ["顶层多出字段（授权原文）", (f) => (f.license_text = "（合成）授权书全文"), /顶层字段只能是/],
    ["决定多出字段", (f) => ((f.decisions[0] as unknown as Record<string, unknown>).note = "（合成）备注"), /字段只能是/],
    ["来源编号是网址", (f) => (f.decisions[0]!.source_ref = "https://example.invalid/x"), /来源编号不符合编号格式/],
    ["来源编号含空白与中文", (f) => (f.decisions[0]!.source_ref = "合成 出版社"), /来源编号不符合编号格式/],
    ["导出批次编号", (f) => (f.export_ref = "批次 1"), /export_ref/],
    ["导出时刻", (f) => (f.exported_at = "2026-10-01"), /exported_at/],
    ["题目不在内容包", (f) => (f.decisions[0]!.item_id = "Q-NOT-IN-PACK"), /不在内容包里/],
    ["题目重复", (f) => (f.decisions[1]!.item_id = f.decisions[0]!.item_id), /重复/],
    ["六段不一致", (f) => (f.curriculum.content_version = "9.9.9"), /curriculum\.content_version 与内容包不一致/],
    ["六段缺一段", (f) => delete (f.curriculum as Partial<typeof f.curriculum>).grade, /六段标识/],
    ["admitted 但授权待核", (f) => (f.decisions[0]!.license_status = "unverified"), /admitted，但来源编号、来源类型或授权/],
    ["admitted 但来源类型未知", (f) => (f.decisions[0]!.source_type = "unknown"), /admitted，但来源编号、来源类型或授权/],
    ["admitted 但没有来源编号", (f) => (f.decisions[0]!.source_ref = null), /admitted，但来源编号、来源类型或授权/],
    ["admitted 但审核等级 Q1", (f) => (f.decisions[0]!.review_level = "Q1"), /审核等级低于 Q2/],
    ["admitted 但没有审核等级", (f) => (f.decisions[0]!.review_level = null), /审核等级低于 Q2/],
    ["admitted 但没有审核记录", (f) => (f.decisions[0]!.review_ids = []), /admitted，但没有审核记录编号/],
    ["审核记录不是 UUID", (f) => (f.decisions[0]!.review_ids = ["R-1"]), /不是 UUID/],
    ["审核记录重复", (f) => (f.decisions[0]!.review_ids = [synReviewId(1), synReviewId(1)]), /审核记录编号重复/],
    ["pending_review 但授权待核", (f) => (f.decisions[1]!.license_status = "unverified"), /pending_review，但来源编号/],
    ["rejected 但没有审核记录", (f) => (f.decisions[2]!.review_ids = []), /rejected，但没有审核记录编号/],
    ["source_unverified 却授权已核", (f) => (f.decisions[1]!.admission = "source_unverified"), /授权状态却不是 unverified/],
    ["准入状态未知", (f) => ((f.decisions[0] as { admission: string }).admission = "published"), /准入状态未知/],
    ["来源类型未知", (f) => ((f.decisions[0] as { source_type: string }).source_type = "internet"), /来源类型未知/],
    ["授权状态未知", (f) => ((f.decisions[0] as { license_status: string }).license_status = "ok"), /授权状态未知/],
  ];
  for (const [name, mutate, re] of cases) {
    const f = threeDecisions() as Raw;
    mutate(f);
    assert.throws(() => parse(f), re, name);
  }
  assert.throws(() => parse([]), /顶层必须是对象/);
});

test("按决定重建：只改文件里的题，其余回到默认；版本号带决定摘要；admission_decisions 记下批次与摘要", () => {
  const f = parse(threeDecisions());
  const p = applyAdmissionDecisions(base, f);
  const by = new Map(p.items.map((i) => [i.id, i]));
  assert.equal(by.get(CHOICE.id)!.admission, "admitted");
  assert.deepEqual(by.get(CHOICE.id)!.source, { source_ref: "SYN-SRC-0000", source_type: "self_authored", license_status: "cleared" });
  assert.deepEqual(by.get(CHOICE.id)!.admission_review, { review_level: "Q2", review_ids: [synReviewId(1)] });
  assert.equal(by.get(A2!.id)!.admission, "pending_review");
  assert.equal(by.get(A3!.id)!.admission, "rejected");
  assert.equal(by.get(A3!.id)!.source.license_status, "denied");
  assert.equal(p.items.filter((i) => i.admission === "source_unverified").length, 718);
  const digest = decisionsDigest(f);
  assert.equal(p.pack_version, `0.3.0-${base.legacy_commit.slice(0, 7)}.d${digest.slice(7, 15)}`);
  assert.deepEqual(p.admission_decisions, { format: "aya-admission-decisions/1", export_ref: "SYN-EXPORT-0001", exported_at: "2026-10-01T00:00:00Z", digest, decisions: 3 });
  const loaded = indexPack(p);
  assert.equal(loaded.itemsSourceVersion, `items:aya-pack@${p.pack_version}`);
  // 输入不被改动
  assert.equal(base.items.find((i) => i.id === CHOICE.id)!.admission, "source_unverified");
});

test("重建先重置：新的决定文件里没有的题回到默认，旧决定不残留；空决定重建回原包", () => {
  const first = applyAdmissionDecisions(base, parse(threeDecisions()));
  const only = syntheticDecisionFile([THREE[1]!]);
  const second = applyAdmissionDecisions(first, parse(only));
  const by = new Map(second.items.map((i) => [i.id, i]));
  assert.equal(by.get(CHOICE.id)!.admission, "source_unverified");
  assert.deepEqual(by.get(CHOICE.id)!.source, { source_ref: null, source_type: "unknown", license_status: "unverified" });
  assert.ok(!("admission_review" in by.get(CHOICE.id)!));
  assert.equal(by.get(A2!.id)!.admission, "admitted");
  assert.equal(second.admission_decisions!.decisions, 1);
  assert.equal(serializePack(applyAdmissionDecisions(second, null)), serializePack(base));
});

test("确定性：同样的输入两次构建逐字节相同；构造顺序不同也相同；已提交的内容包按空决定重建逐字节不变", () => {
  const f = threeDecisions();
  const a = serializePack(applyAdmissionDecisions(base, parse(f)));
  const b = serializePack(applyAdmissionDecisions(structuredClone(base), parse(structuredClone(f))));
  assert.equal(a, b);
  // 键的插入顺序反过来，字节仍相同
  const reversedKeys = (v: unknown): unknown =>
    Array.isArray(v) ? v.map(reversedKeys) : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).reverse().map(([k, x]) => [k, reversedKeys(x)])) : v;
  assert.equal(serializePack(reversedKeys(applyAdmissionDecisions(base, parse(f))) as ContentPack), a);
  const committed = readFileSync(PACK_PATH, "utf8");
  assert.equal(serializePack(applyAdmissionDecisions(JSON.parse(committed) as ContentPack, null)), committed);
});

test("加载时的自洽检查：准入与来源、依据与决定记录、C9 记录不一致时拒绝加载", () => {
  const withDecisions = applyAdmissionDecisions(base, parse(threeDecisions()));
  const cases: [string, (p: ContentPack) => void, RegExp][] = [
    ["直接把题改成 admitted（没有来源与依据）", (p) => (p.items[5]!.admission = "admitted"), /admitted，但来源编号/],
    ["来源材料原文写进来源编号", (p) => (p.items[0]!.source.source_ref = "（合成）某教辅第 3 页"), /来源编号不符合编号格式/],
    ["缺来源记录", (p) => delete (p.items[0] as Partial<ContentPack["items"][number]>).source, /没有来源记录/],
    ["没有决定文件却有依据", (p) => (p.items[0]!.admission_review = { review_level: "Q1", review_ids: [synReviewId(1)] }), /与 admission_decisions 不一致/],
    ["缺 admission_decisions", (p) => delete (p as Partial<ContentPack>).admission_decisions, /缺少 admission_decisions/],
    ["缺 C9 记录", (p) => delete (p as Partial<ContentPack>).hint_scan, /C9 词表检查记录/],
    ["严格模式却有标记", (p) => (p.hint_scan = { ...p.hint_scan, flagged_items: [p.items[0]!.id] }), /严格模式却有标记/],
    ["标记了不存在的题", (p) => (p.hint_scan = { ...p.hint_scan, mode: "flag", flagged_items: ["Q-NOT-IN-PACK"] }), /不在内容包里/],
  ];
  for (const [name, mutate, re] of cases) {
    const p = structuredClone(base);
    mutate(p);
    assert.throws(() => indexPack(p), re, name);
  }
  const miscount = structuredClone(withDecisions);
  miscount.admission_decisions!.decisions = 2;
  assert.throws(() => indexPack(miscount), /与 admission_decisions 不一致/);
  const lost = structuredClone(withDecisions);
  delete lost.items.find((i) => i.id === CHOICE.id)!.admission_review;
  assert.throws(() => indexPack(lost), /admitted，但/);
  const flagged = structuredClone(base);
  flagged.hint_scan = { ...flagged.hint_scan, mode: "flag", flagged_items: [flagged.items[0]!.id] };
  assert.doesNotThrow(() => indexPack(flagged));
});

test("适配器只对已准入题给 formal 与满置信度：admitted → formal、无 content_not_admitted；pending / rejected → practice + 复核", () => {
  const loaded = indexPack(applyAdmissionDecisions(base, parse(threeDecisions())));
  const a = makeAdapter(loaded);
  const ok = a.handle("POST", "grade", gradeBody(CHOICE.id, { choice: [...CHOICE.answer] })).body as Json;
  assert.equal(ok.semantics, "formal");
  assert.equal(ok.human_review_required, false);
  assert.equal(ok.review_reasons, undefined);
  assert.ok(ok.evidence.every((e: Json) => e.admission === "admitted"));
  assert.equal(ok.source_version, `verifier:aya-symbolic-ts@0.1.0;items:aya-pack@${loaded.pack.pack_version}`);
  for (const other of [A2!, A3!]) {
    const r = (other.format === "fill" ? { text: other.answer[0]! } : { choice: [...other.answer] }) as { text: string } | { choice: string[] };
    const b = a.handle("POST", "grade", gradeBody(other.id, r)).body as Json;
    assert.equal(b.semantics, "practice", other.id);
    assert.ok(b.review_reasons.includes("content_not_admitted"), other.id);
  }
  // 没有决定时同一道题仍是 practice（与现有 content_not_admitted 规则一致）
  const before = makeAdapter().handle("POST", "grade", gradeBody(CHOICE.id, { choice: [...CHOICE.answer] })).body as Json;
  assert.equal(before.semantics, "practice");
  // generate_variation 只选已准入的题
  const gv = makeAdapterV1(loaded).handle("POST", "generate_variation", { context: contextV1(), count: 10, skill_node: `aya:${CHOICE.mainNodes[0]}` });
  assert.equal(gv.status, 200);
  // 同节点的其他题都未准入（source_unverified，或 A2、A3 的 pending_review / rejected），候选只剩已准入的 CHOICE
  assert.deepEqual((gv.body as Json).result.candidates.map((c: Json) => c.item_ref), [`aya:${CHOICE.id}`]);
});

// ---------- 命令行：按准入决定重建（需要 C9 用的 Python 与 packages/personas；没有时非严格模式跳过） ----------
const PY = process.env.PYTHON ?? "python3";
const guardOk =
  spawnSync(PY, ["-c", "import personas.guard"], { cwd: join(HERE, "../.."), env: { ...process.env, PYTHONPATH: join(HERE, "../../packages") } }).status === 0;
const cliSkip = !guardOk && process.env.CONTRACT_STRICT !== "1" ? "没有可用的 Python 或 packages/personas（C9 依赖）" : false;

test("命令行 rebuild：同一决定文件两次重建逐字节相同；--no-decisions 重建出已提交的内容包；决定不合格时不写文件", { skip: cliSkip }, () => {
  const dir = mkdtempSync(join(tmpdir(), "aya-rebuild."));
  try {
    const dec = join(dir, "decisions.json");
    writeFileSync(dec, JSON.stringify(threeDecisions()));
    const run = (...args: string[]) =>
      spawnSync(process.execPath, ["--no-warnings", "scripts/rebuild-content-pack.ts", ...args], { cwd: HERE, encoding: "utf8" });
    const r1 = run("--decisions", dec, "--out", join(dir, "a.json"), "--report", join(dir, "c9.json"));
    assert.equal(r1.status, 0, r1.stderr);
    const r2 = run("--decisions", dec, "--out", join(dir, "b.json"));
    assert.equal(r2.status, 0, r2.stderr);
    const a = readFileSync(join(dir, "a.json"), "utf8");
    assert.equal(a, readFileSync(join(dir, "b.json"), "utf8"));
    const pa = JSON.parse(a) as ContentPack;
    assert.equal(pa.items.find((i) => i.id === CHOICE.id)!.admission, "admitted");
    assert.equal(pa.admission_decisions!.digest, decisionsDigest(parse(threeDecisions())));
    const report = JSON.parse(readFileSync(join(dir, "c9.json"), "utf8")) as Json;
    assert.equal(report.format, "aya-hint-scan/1");
    assert.deepEqual(report.hits, []);
    const r3 = run("--no-decisions", "--pack", join(dir, "a.json"), "--out", join(dir, "c.json"));
    assert.equal(r3.status, 0, r3.stderr);
    assert.equal(readFileSync(join(dir, "c.json"), "utf8"), readFileSync(PACK_PATH, "utf8"));
    const bad = threeDecisions() as AdmissionDecisionFile;
    bad.decisions[0]!.license_status = "unverified";
    writeFileSync(dec, JSON.stringify(bad));
    const r4 = run("--decisions", dec, "--out", join(dir, "d.json"));
    assert.equal(r4.status, 1);
    assert.match(r4.stderr, /准入决定文件不合格/);
    assert.throws(() => readFileSync(join(dir, "d.json")));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
