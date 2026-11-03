// C9 词表检查：内容包里全部提示阶梯原文逐条过禁语扫描（packages/personas 的 guard，persona aya：A–F，F 取数学口径）。
// 正例、反例用合成句子（取自禁语表的合成示例写法，不是人设语料）；映射与失败即关闭用注入的扫描器，不依赖 Python。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { test } from "node:test";
import { indexPack, type ContentPack, type PackItem } from "../src/content.ts";
import { enforceHintScan, guardRunner, hintTexts, scanHints, type GuardRunner } from "../src/pack/hint-scan.ts";
import { realPack, syntheticPack } from "./helpers.ts";

const HERE = new URL("..", import.meta.url).pathname;
const PY = process.env.PYTHON ?? "python3";
const guardOk =
  spawnSync(PY, ["-c", "import personas.guard"], { cwd: join(HERE, "../.."), env: { ...process.env, PYTHONPATH: join(HERE, "../../packages") } }).status === 0;
// 严格模式（CONTRACT_STRICT=1）下缺 Python 或词表直接失败，不允许“跳过”变绿
const skip = !guardOk && process.env.CONTRACT_STRICT !== "1" ? "没有可用的 Python 或 packages/personas" : false;

const CLEAN = ["（合成）先圈出已知条件。", "（合成）把第二步代回原式检验。", "（合成）这一步移项时符号要变。"];
const itemsWith = (hints: string[][]): Pick<ContentPack, "items"> => syntheticPack(hints.map((hintPath) => ({ hintPath }))).pack;

test("提示原文按题号、等级展开，覆盖每一级（含 4–6 级）", () => {
  const texts = hintTexts(realPack.pack);
  assert.equal(texts.length, realPack.pack.items.reduce((n, i) => n + i.hintPath.length, 0));
  assert.ok(texts.some((t) => t.level === 6));
  const sorted = [...texts].sort((a, b) => (a.item_id < b.item_id ? -1 : a.item_id > b.item_id ? 1 : a.level - b.level));
  assert.deepEqual(texts, sorted);
});

test("C9 正例：命中的题与等级列进报告；严格模式构建失败并列出题号，非严格模式只标记", { skip }, () => {
  const pack = itemsWith([CLEAN, [CLEAN[0]!, CLEAN[1]!, "（合成）这题你粗心了，再看看。"], [CLEAN[2]!, "（合成）你总是这样"]]);
  const strict = scanHints(pack, "strict");
  assert.equal(strict.texts, 8);
  assert.equal(strict.check, "C9");
  assert.equal(strict.persona, "aya");
  assert.deepEqual(
    strict.hits.map((h) => [h.item_id, h.level, h.rule_id]),
    [
      ["SYN-1", 2, "F-aya-01"],
      ["SYN-1", 2, "PLAT-01"], // “粗心”同时是全平台禁语（七科规范第三章合同第 5 条）
      ["SYN-2", 1, "E-12"],
    ],
  );
  assert.deepEqual(strict.flagged_items, ["SYN-1", "SYN-2"]);
  assert.throws(() => enforceHintScan(strict), /严格模式.*2 道题[\s\S]*SYN-1\[2\] F-aya-01[\s\S]*SYN-2\[1\] E-12/);
  const flagged = enforceHintScan(scanHints(pack, "flag"));
  assert.equal(flagged.mode, "flag");
  assert.deepEqual(flagged.flagged_items, ["SYN-1", "SYN-2"]);
  // 报告与记录里只有题号、等级、规则编号，没有提示原文
  assert.doesNotMatch(JSON.stringify([strict, flagged]), /粗心|总是这样|合成/);
});

test("C9 反例：干净的提示没有命中；已提交内容包的 4447 条提示没有命中", { skip }, () => {
  const clean = scanHints(itemsWith([CLEAN, CLEAN]), "strict");
  assert.deepEqual(clean.hits, []);
  assert.deepEqual(enforceHintScan(clean).flagged_items, []);
  const real = scanHints(realPack.pack, "strict");
  assert.equal(real.texts, 4447);
  assert.deepEqual(real.hits, [], JSON.stringify(real.hits.slice(0, 10)));
  // 内容包记下的词表版本与当前一致（不一致说明词表更新过，按 README 重建内容包）
  if (real.lexicon_version !== realPack.pack.hint_scan.lexicon_version) {
    console.log(`hint-scan: 内容包的 C9 记录用的是词表 ${realPack.pack.hint_scan.lexicon_version}，当前为 ${real.lexicon_version}，请重建内容包`);
  }
});

// ---------- 注入扫描器：映射与失败即关闭（不依赖 Python） ----------
const fake = (hitAt: Record<number, string[]>, over: Partial<ReturnType<GuardRunner>> = {}): GuardRunner => (texts) => ({
  lexicon_version: "0.0.0-syn",
  guard_version: "0.0.0-syn",
  persona_id: "aya",
  results: texts.map((_, i) => ({ source: `stdin[${i}]`, hits: (hitAt[i] ?? []).map((rule_id) => ({ class: rule_id.slice(0, 1), rule_id })) })),
  ...over,
});

test("命中按输入序号映射回（题号, 等级）；同一题多处命中只标记一次", () => {
  const pack = itemsWith([["a0", "a1", "a2"], ["b0", "b1"], ["c0"]]);
  const r = scanHints(pack, "flag", fake({ 1: ["A-01"], 3: ["B-02", "A-03"], 4: ["C-01"] }));
  assert.deepEqual(
    r.hits.map((h) => `${h.item_id}[${h.level}]${h.rule_id}`),
    ["SYN-0[1]A-01", "SYN-1[0]A-03", "SYN-1[0]B-02", "SYN-1[1]C-01"],
  );
  assert.deepEqual(r.flagged_items, ["SYN-0", "SYN-1"]);
  assert.equal(r.lexicon_version, "0.0.0-syn");
  const rec = enforceHintScan(r);
  const p = { ...(pack as ContentPack), hint_scan: rec };
  assert.doesNotThrow(() => indexPack(p));
  assert.throws(() => indexPack({ ...p, hint_scan: { ...rec, mode: "strict" } }), /严格模式却有标记/);
});

test("失败即关闭：扫描器报错、结果条数或顺序对不上、角色不对、没有词表版本，都不当成“没有命中”", () => {
  const pack = itemsWith([["a0", "a1"]]);
  assert.throws(() => scanHints(pack, "strict", guardRunner({ python: "/nonexistent/python-for-test" })), /无法运行/);
  assert.throws(() => scanHints(pack, "strict", () => ({ ...fake({})(["x"]) })), /条数/);
  assert.throws(() => scanHints(pack, "strict", (t) => ({ ...fake({})(t), results: fake({})(t).results.reverse() })), /顺序/);
  assert.throws(() => scanHints(pack, "strict", fake({}, { persona_id: "platform" })), /角色/);
  assert.throws(() => scanHints(pack, "strict", fake({}, { lexicon_version: undefined as unknown as string })), /词表版本/);
  const r = scanHints(pack, "strict", fake({}));
  assert.deepEqual(enforceHintScan(r).flagged_items, []);
});

test("guard 命令行出错（退出码非 0）时构建失败", () => {
  // 用一个一定失败的“解释器”：node 执行 -m 参数会报错退出
  const run = guardRunner({ python: process.execPath });
  assert.throws(() => run(["x"]), /C9 词表检查(失败|无法运行)/);
});

test("严格模式：没有命中时放行，记录词表版本与扫描条数", () => {
  const items: Partial<PackItem>[] = [{ hintPath: ["h0", "h1"] }];
  const r = enforceHintScan(scanHints(syntheticPack(items).pack, "strict", fake({})));
  assert.deepEqual(r, { check: "C9", persona: "aya", lexicon_version: "0.0.0-syn", guard_version: "0.0.0-syn", mode: "strict", texts: 2, flagged_items: [] });
  // 有命中时严格模式失败（不依赖 Python 的同一断言）
  assert.throws(() => enforceHintScan(scanHints(syntheticPack(items).pack, "strict", fake({ 1: ["A-01"] }))), /SYN-0\[1\] A-01/);
});
