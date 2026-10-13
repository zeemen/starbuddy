// KGE-08：规范化与指纹的单测（全部合成文本）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_CONFIG } from "../src/config.ts";
import { fmix64, fnv1a64, gramHash, hamming, hammingSplit, hex64, jaccard, ngrams, sha256, simhash64, split64 } from "../src/fingerprint.ts";
import { canonicalText, stripLatexWrappers, templateText } from "../src/normalize.ts";

const MASK = DEFAULT_CONFIG.mask;
const sim = (s: string) => simhash64(ngrams(canonicalText(s), DEFAULT_CONFIG.ngram));
const grams = (s: string) => new Set(ngrams(canonicalText(s), DEFAULT_CONFIG.ngram));

test("规范化：全半角、空白、标点统一", () => {
  assert.equal(canonicalText("（合成） 解方程：  ２ｘ＋３＝１１ ，求ｘ的值。"), "(合成)解方程:2x+3=11,求x的值.");
  assert.equal(canonicalText("（合成）解方程:2x+3=11,求x的值."), "(合成)解方程:2x+3=11,求x的值.");
  assert.equal(canonicalText("（合成）“甲”、【乙】《丙》——丁～"), '(合成)"甲",[乙]<丙>--丁~');
  assert.equal(canonicalText("（合成）\t第一行\n  第二行\r\n第三行 "), "(合成)第一行第二行第三行");
  assert.equal(canonicalText("（合成）．"), "(合成).");
});

test("规范化：LaTeX 只去无义包装，\\frac{a}{b} 与 a/b 不强行等同", () => {
  assert.equal(canonicalText("（合成）$\\left( x + 1 \\right)$"), canonicalText("（合成）(x+1)"));
  assert.equal(canonicalText("（合成）\\(\\displaystyle \\dfrac{1}{2}\\)"), canonicalText("（合成）\\frac{1}{2}"));
  assert.equal(canonicalText("（合成）\\[a\\,b\\quad c\\]"), canonicalText("（合成）abc"));
  assert.equal(canonicalText("（合成）\\Big[ x \\Big]"), canonicalText("（合成）[x]"));
  assert.notEqual(canonicalText("（合成）\\frac{a}{b}"), canonicalText("（合成）a/b"));
  // 不误伤以 left / right 开头的别的命令
  assert.equal(stripLatexWrappers("\\leftarrow \\rightarrow"), "\\leftarrow \\rightarrow");
  assert.equal(stripLatexWrappers("\\tfrac12"), "\\frac12");
});

test("模板文本：数字遮成 #、单个字母变量遮成 @；开关可关；命令名不遮", () => {
  assert.equal(templateText("（合成）长 12.5 厘米，宽 7 厘米", MASK), "(合成)长#厘米,宽#厘米");
  assert.equal(templateText("（合成）解方程 2x + 3y = 11，求 x", MASK), "(合成)解方程#@+#@=#,求@");
  assert.equal(templateText("（合成）\\frac{a}{b} + ab", MASK), "(合成)\\frac{@}{@}+ab");
  assert.equal(templateText("（合成）a b", MASK), "(合成)@@");
  assert.equal(templateText("（合成）\\P x", MASK), "(合成)\\P@"); // 单字母命令名不遮
  assert.equal(templateText("（合成）长 12 厘米，求 x", { numbers: false, letters: true }), "(合成)长12厘米,求@");
  assert.equal(templateText("（合成）长 12 厘米，求 x", { numbers: true, letters: false }), "(合成)长#厘米,求x");
  assert.equal(templateText("（合成）长 8 厘米", MASK), templateText("（合成）长 15 厘米", MASK));
});

test("FNV-1a 64 已知向量；fmix64 确定性；SHA-256 与 hex64 形状", () => {
  assert.equal(fnv1a64(""), 0xcbf29ce484222325n);
  assert.equal(fnv1a64("a"), 0xaf63dc4c8601ec8cn);
  assert.equal(fnv1a64("foobar"), 0x85944171f73967e8n);
  assert.equal(fmix64(0n), 0n);
  assert.equal(fmix64(12345n), fmix64(12345n));
  assert.notEqual(fmix64(1n), fmix64(2n));
  assert.match(sha256("（合成）"), /^[0-9a-f]{64}$/);
  assert.equal(hex64(0xabcn), "0000000000000abc");
});

test("n-gram：按码点切；短文本整体一个；空文本没有", () => {
  assert.deepEqual(ngrams("合成题目", 3), ["合成题", "成题目"]);
  assert.deepEqual(ngrams("合成", 3), ["合成"]);
  assert.deepEqual(ngrams("", 3), []);
  assert.deepEqual(ngrams("a😀b", 2), ["a😀", "😀b"]);
});

test("SimHash：相同输入相同输出；不同写法的同一题相同", () => {
  const a = "（合成）甲从家出发步行去图书馆，先向东走了三个路口，再向北拐弯走了两个路口，问他一共走过了几个路口？";
  assert.equal(sim(a), sim(a));
  assert.equal(sim(a), sim(a.replace(/，/g, ",").replace("？", "?")));
  assert.equal(simhash64([]), 0n);
  assert.ok(sim(a) >= 0n && sim(a) < 1n << 64n);
});

test("指纹的固定值（改哈希、终混或投票规则都会变；变了要重新评估已有报告）", () => {
  assert.equal(hex64(gramHash("合成题")), "446b995d83eadcdf");
  assert.equal(hex64(sim("（合成）甲从家出发步行去图书馆，先向东走了三个路口。")), "08e862d1b55852f5");
  assert.equal(sha256(canonicalText("（合成）一")), sha256("(合成)一"));
});

test("SimHash：单字改动距离小（≤ 门一），无关文本距离大（≥ 20）", () => {
  const cases: [string, string][] = [
    ["（合成）甲从家出发步行去图书馆，先向东走了三个路口，再向北拐弯走了两个路口，问他一共走过了几个路口？",
      "（合成）甲从家出发步行去图书馆，先向东走了三个路口，再向南拐弯走了两个路口，问他一共走过了几个路口？"],
    ["（合成）抛掷一枚质地均匀的骰子，求朝上点数为偶数的概率，并写出所有可能的结果。",
      "（合成）抛掷一枚质地均匀的骰子，求朝上点数为奇数的概率，并写出所有可能的结果。"],
    ["（合成）把一个圆柱沿高剪开，侧面展开图是什么形状？请说明原因并画出示意图。",
      "（合成）把一个圆锥沿高剪开，侧面展开图是什么形状？请说明原因并画出示意图。"],
  ];
  for (const [x, y] of cases) {
    const d = hamming(sim(x), sim(y));
    assert.ok(d > 0 && d <= DEFAULT_CONFIG.near.maxHamming, `单字改动的距离应 ≤ ${String(DEFAULT_CONFIG.near.maxHamming)}，实际 ${String(d)}`);
    assert.ok(jaccard(grams(x), grams(y)) >= DEFAULT_CONFIG.near.minJaccard);
  }
  const unrelated: [string, string][] = [
    [cases[0]![0], "（合成）下列关于光合作用的说法中，正确的一项是哪一个选项，请说明理由。"],
    [cases[1]![0], "（合成）阅读短文，概括作者在第三段中表达的主要观点并加以评价。"],
    [cases[2]![0], "（合成）化简分式，并在给定的取值范围内选一个合适的数代入求值。"],
  ];
  for (const [x, y] of unrelated) {
    const d = hamming(sim(x), sim(y));
    assert.ok(d >= 20, `无关文本的距离应 ≥ 20，实际 ${String(d)}`);
    assert.ok(jaccard(grams(x), grams(y)) < 0.3);
  }
});

test("汉明距离：BigInt 版与成对扫描的拆分版一致", () => {
  const vals = [0n, 1n, (1n << 64n) - 1n, 0x8000000000000001n, 0x0123456789abcdefn, sim("（合成）一"), sim("（合成）二")];
  for (const a of vals) for (const b of vals) assert.equal(hammingSplit(split64(a), split64(b)), hamming(a, b));
  assert.equal(hamming(0n, (1n << 64n) - 1n), 64);
  assert.equal(hamming(0b1011n, 0b0001n), 2);
});

test("Jaccard：交并比；两个空集为 1", () => {
  assert.equal(jaccard(new Set(["a", "b"]), new Set(["b", "c"])), 1 / 3);
  assert.equal(jaccard(new Set(), new Set()), 1);
  assert.equal(jaccard(new Set(["a"]), new Set()), 0);
});
