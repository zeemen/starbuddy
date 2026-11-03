// KGE-08 第一步的变异测试：把规范化、指纹、两道门、分段索引、族候选、同卷判定、输入守卫、资源上限、报告与写盘守卫的关键检查
// 逐条改坏，断言测试能发现。
// 做法（与 tools/kge-import/tests/chinese/mutations.ts 同一形式）：把 tools/kge-dedupe 复制到临时目录（保持仓库结构；
// tools/kge-inventory 与 tools/kge-import 软链接回仓库），改一处源码，先过类型检查（不过 = 变异本身写错），再跑全部单测：
// 只有“测试失败（退出码 1）”才算被发现；原文找不到或不唯一也算变异写错。先跑一次不变异的对照，必须全部通过。
// 用法（仓库根目录）：node --no-warnings tools/kge-dedupe/tests/mutations.ts
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("../../../", import.meta.url));
const TOOL = "tools/kge-dedupe";
const TSC = join(REPO, "tools/kge-inventory/node_modules/.bin/tsc");

interface Mutation { name: string; file: string; orig: string; repl: string }
const N = "src/normalize.ts", F = "src/fingerprint.ts", D = "src/dedupe.ts", P = "src/paper.ts", I = "src/input.ts", R = "src/report.ts",
  C = "src/config.ts", MAIN = "src/main.ts";

const M: Mutation[] = [
  // ---------- 规范化 ----------
  { file: N, name: "不做 NFKC（全半角不统一）", orig: 'let t = s.normalize("NFKC");', repl: "let t = s;" },
  { file: N, name: "中文标点不统一", orig: "t = Array.from(t, (ch) => PUNCT.get(ch) ?? ch).join(\"\");", repl: "" },
  { file: N, name: "不去 \\left \\right 等包装", orig: "left|right|bigg|Bigg|big|Big", repl: "zzleftzz" },
  { file: N, name: "去包装误伤 \\leftarrow（缺词尾断言）", orig: 'Big)(?![A-Za-z])/g, "")', repl: 'Big)/g, "")' },
  { file: N, name: "\\dfrac 不折成 \\frac", orig: "[dt]frac(?!", repl: "[x]frac(?!" },
  { file: N, name: "不去 $ 定界符", orig: String.raw`.replace(/\$/g, " ");`, repl: ";" },
  { file: N, name: "空白不折叠", orig: 'return t.replace(/\\s+/g, " ").trim();', repl: "return t;" },
  { file: N, name: "规范文本保留空格", orig: 'return normalizeSpaced(s).replace(/ /g, "");', repl: "return normalizeSpaced(s);" },
  { file: N, name: "模板不遮数字", orig: "if (mask.numbers) t =", repl: "if (false) t =" },
  { file: N, name: "模板不遮字母变量", orig: "if (mask.letters) t =", repl: "if (false) t =" },
  { file: N, name: "命令名的单字母也被遮", orig: String.raw`(?<![A-Za-z\\])`, repl: "(?<![A-Za-z])" },
  { file: N, name: "多字母词也被遮", orig: '[A-Za-z](?![A-Za-z])/g, "@"', repl: '[A-Za-z]/g, "@"' },
  // ---------- 指纹 ----------
  { file: F, name: "FNV 素数写错", orig: "const FNV_PRIME = 0x100000001b3n;", repl: "const FNV_PRIME = 0x100000001b5n;" },
  { file: F, name: "去掉 fmix64 终混", orig: "export const gramHash = (g: string): bigint => fmix64(fnv1a64(g));", repl: "export const gramHash = (g: string): bigint => fnv1a64(g);" },
  { file: F, name: "SimHash 平票算 1", orig: "if (v[bit]! > 0) out", repl: "if (v[bit]! >= 0) out" },
  { file: F, name: "SimHash 只用低 32 位投票", orig: "v[bit + 32] += (hi >>> bit) & 1 ? 1 : -1;", repl: "v[bit + 32] += (lo >>> bit) & 1 ? 1 : -1;" },
  { file: F, name: "n-gram 按 UTF-16 切（不按码点）", orig: "const cp = Array.from(s);", repl: 'const cp = s.split("");' },
  { file: F, name: "短文本没有 gram", orig: 'if (cp.length < n) return [cp.join("")];', repl: "if (cp.length < n) return [];" },
  { file: F, name: "汉明距离数成位长", orig: "x &= x - 1n;", repl: "x >>= 1n;" },
  { file: F, name: "快速汉明距离漏掉高 32 位", orig: "popcount32(a.hi ^ b.hi) + popcount32(a.lo ^ b.lo)", repl: "popcount32(a.lo ^ b.lo)" },
  { file: F, name: "Jaccard 分母用和不用并", orig: "return inter / (a.size + b.size - inter);", repl: "return inter / (a.size + b.size);" },
  // ---------- 去重：两道门、分段索引、上限 ----------
  { file: D, name: "近似少了门一（SimHash）", orig: "if (d > cfg.near.maxHamming) continue; // 门一", repl: "" },
  { file: D, name: "近似少了门二（Jaccard）", orig: "if (jac < cfg.near.minJaccard) continue; // 门二", repl: "" },
  { file: D, name: "门一边界：距离等于阈值不算", orig: "if (d > cfg.near.maxHamming) continue; // 门一", repl: "if (d >= cfg.near.maxHamming) continue; // 门一" },
  { file: D, name: "门二边界：Jaccard 等于阈值不算", orig: "if (jac < cfg.near.minJaccard) continue; // 门二", repl: "if (jac <= cfg.near.minJaccard) continue; // 门二" },
  { file: D, name: "分段只分 k 段（鸽笼不成立，会漏）", orig: "const nb = Math.max(1, k + 1);", repl: "const nb = Math.max(1, k);" },
  { file: D, name: "分段索引同一对重复给出", orig: "for (let e = 0; e < b && !earlier; e++) earlier = bands[i]![e] === bands[j]![e];", repl: "" },
  { file: D, name: "k = 64 时仍分段", orig: "if (k >= 64) return null;", repl: "if (k > 64) return null;" },
  { file: D, name: "近似对不设上限（静默放行）", orig: "if (out.length > cfg.limits.maxNearPairs) {", repl: "if (false) {" },
  { file: D, name: "近似对上限差一（恰好等于上限就报错）", orig: "if (out.length > cfg.limits.maxNearPairs) {", repl: "if (out.length >= cfg.limits.maxNearPairs) {" },
  { file: D, name: "近似对不排序（输出依赖分段顺序）", orig: "return out.sort((x, y) => cmp(x.a, y.a) || cmp(x.b, y.b));", repl: "return out;" },
  { file: D, name: "单独一题也成完全重复组", orig: ".filter((list) => list.length >= 2)", repl: ".filter((list) => list.length >= 1)" },
  { file: D, name: "规范题候选不取最小 ID", orig: "canonical_candidate: list[0]!.id", repl: "canonical_candidate: list[list.length - 1]!.id" },
  { file: D, name: "指纹不按 ID 排序（输出依赖输入顺序）", orig: "    .sort((a, b) => cmp(a.id, b.id));\n}", repl: ";\n}" },
  // ---------- 题目族 ----------
  { file: D, name: "族不分主节点（跨节点成族）", orig: "for (const f of members) pushTo(byNode, nodeKey(f.mainNodes), f);", repl: 'for (const f of members) pushTo(byNode, "ALL", f);' },
  { file: D, name: "多主节点题也成族", orig: "if (!single(k)) continue; // 只认单一主节点", repl: "" },
  { file: D, name: "“单一主节点”放宽为至少一个", orig: "byNode.get(k)![0]!.mainNodes.length === 1;", repl: "byNode.get(k)![0]!.mainNodes.length >= 1;" },
  { file: D, name: "规范文本全相同也成族", orig: "      if (new Set(list.map((f) => f.sha)).size < 2) continue;\n", repl: "" },
  { file: D, name: "待看不列出", orig: "if (reasons.length > 0) {", repl: "if (false) {" },
  { file: D, name: "待看漏标跨主节点", orig: 'if (keys.filter(single).length > 1) reasons.push("cross_node");', repl: "" },
  { file: D, name: "待看漏标多主节点", orig: 'if (keys.some((k) => byNode.get(k)![0]!.mainNodes.length > 1)) reasons.push("multi_node");', repl: "" },
  { file: D, name: "待看漏标无主节点", orig: 'if (keys.some((k) => byNode.get(k)![0]!.mainNodes.length === 0)) reasons.push("no_node");', repl: "" },
  { file: D, name: "主节点集合不排序（顺序不同算不同节点）", orig: "mainNodes: [...new Set(it.mainNodes)].sort(cmp),", repl: "mainNodes: [...new Set(it.mainNodes)]," },
  { file: D, name: "族码不带学科前缀", orig: "`FAM_${subject}_${templateSha.slice(0, 12)}", repl: "`FAM_${templateSha.slice(0, 12)}" },
  { file: D, name: "族码不带主节点短哈希", orig: "_${sha256(mainNode).slice(0, 8)}`", repl: "`" },
  // ---------- 同卷 ----------
  { file: P, name: "不查题数差", orig: 'if (Math.abs(a.length - b.length) > cfg.paper.maxCountDiff) failed.push("count_diff");', repl: "" },
  { file: P, name: "不查按序一致占比", orig: 'if (ratio < cfg.paper.minOrderedRatio) failed.push("ordered_ratio");', repl: "" },
  { file: P, name: "占比门边界：等于阈值不算", orig: "if (ratio < cfg.paper.minOrderedRatio)", repl: "if (ratio <= cfg.paper.minOrderedRatio)" },
  { file: P, name: "不查首题", orig: 'if (cfg.paper.requireFirstLast && !first) failed.push("first_item");', repl: "" },
  { file: P, name: "不查尾题", orig: 'if (cfg.paper.requireFirstLast && !last) failed.push("last_item");', repl: "" },
  { file: P, name: "不查换序", orig: 'if (outOfOrder > cfg.paper.maxOutOfOrder) failed.push("out_of_order");', repl: "" },
  { file: P, name: "相似卷不设下限（零星命中也报）", orig: "looseRatio >= cfg.paper.similarMinRatio && looseLcs > 0", repl: "looseLcs > 0" },
  { file: P, name: "占比分母用较小题数", orig: "const denom = Math.max(a.length, b.length);", repl: "const denom = Math.min(a.length, b.length);" },
  { file: P, name: "同卷也认近似题", orig: "const lcs = orderedMatches(a, b, exactMatch);", repl: "const lcs = orderedMatches(a, b, loose);" },
  { file: P, name: "首题近似也算一致", orig: "exactMatch(a[0]!, b[0]!)", repl: "loose(a[0]!, b[0]!)" },
  { file: P, name: "相似卷不认近似题", orig: "const looseLcs = orderedMatches(a, b, loose);", repl: "const looseLcs = orderedMatches(a, b, exactMatch);" },
  { file: P, name: "试卷内不按序号排", orig: ".sort((x, y) => x.seq! - y.seq!)", repl: "" },
  { file: P, name: "近似匹配不查近似对", orig: "return pairs.has(`${rx}\\u0000${ry}`) || pairs.has(`${ry}\\u0000${rx}`);", repl: "return false;" },
  { file: P, name: "两两比较也列出不相干的卷", orig: 'if (c.verdict !== "none") out.push(c);', repl: "out.push(c);" },
  // ---------- 输入守卫 ----------
  { file: I, name: "不拒作答、学生、手写、批改类字段（只当不认识的键）", orig: "if (FORBIDDEN_KEY.test(k)) throw", repl: "if (false) throw" },
  { file: I, name: "禁用键大小写敏感", orig: "订正)/i;", repl: "订正)/;" },
  { file: I, name: "禁用键漏掉中文作答类", orig: "作答|答案|答题|", repl: "" },
  { file: I, name: "禁用键漏掉 student", orig: "|student|", repl: "|" },
  { file: I, name: "不查白名单（收不认识的键）", orig: "if (!allowed.includes(k)) throw", repl: "if (false) throw" },
  { file: I, name: "题目层不查键", orig: "    checkKeys(`items[${String(i)}]`, o, ITEM_KEYS);\n", repl: "" },
  { file: I, name: "不认识的键回显键名", orig: "第 ${String(i + 1)} 个键不认识", repl: "第 ${String(i + 1)} 个键不认识：${k}" },
  { file: I, name: "禁用键名不截断", orig: "字段“${clip(k)}”", repl: "字段“${k}”" },
  { file: I, name: "坏 JSON 回显输入", orig: 'throw new InputError("样例不是合法 JSON");', repl: 'throw new InputError(`样例不是合法 JSON：${raw.slice(0, 40)}`);' },
  { file: I, name: "收真实内容（synthetic 不是 true）", orig: "if (root.synthetic !== true) throw", repl: "if (false) throw" },
  { file: I, name: "合成标记只查包含、不查开头", orig: "if (!v.startsWith(SYNTHETIC_PREFIX)) throw", repl: 'if (!v.includes("合成")) throw' },
  { file: I, name: "不查个人信息", orig: "if (looksPersonal(v)) throw", repl: "if (false) throw" },
  { file: I, name: "个人信息匹配前不做 NFKC（全角漏检）", orig: 'const t = v.normalize("NFKC").replace(/\\s+/g, "");', repl: 'const t = v.replace(/\\s+/g, "");' },
  { file: I, name: "个人信息匹配前不去空白", orig: 'const t = v.normalize("NFKC").replace(/\\s+/g, "");', repl: 'const t = v.normalize("NFKC");' },
  { file: I, name: "不查邮箱", orig: String.raw`/(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.]+/, `, repl: "" },
  { file: I, name: "邮箱模式去掉起点断言（平方级回溯）", orig: String.raw`/(?<![\w.+-])[\w.+-]+@`, repl: String.raw`/[\w.+-]+@` },
  { file: I, name: "不查身份证号", orig: String.raw`/\d{17}[\dXx]/, `, repl: "" },
  { file: I, name: "不设题数上限", orig: "root.items.length > MAX_ITEMS", repl: "false" },
  { file: I, name: "不设样例字节上限", orig: 'if (Buffer.byteLength(raw, "utf8") > MAX_FIXTURE_BYTES) throw', repl: "if (false) throw" },
  { file: I, name: "不设总字数上限", orig: "if (total > MAX_TOTAL_CHARS) throw", repl: "if (false) throw" },
  { file: I, name: "样例主节点不查 ID 形状", orig: 'typeof n === "string" && NODE_ID.test(n)', repl: 'typeof n === "string"' },
  { file: I, name: "legacy 主节点不查 ID 形状", orig: "if (!q.mainNodes.every((n) => NODE_ID.test(n))) throw", repl: "if (false) throw" },
  { file: I, name: "旧题组一题多组时取最后一个", orig: "if (!legacyFamilies.has(id)) legacyFamilies.set", repl: "legacyFamilies.set" },
  { file: I, name: "paper_id 与 seq 不必成对", orig: "if (hasPaper !== hasSeq) throw", repl: "if (false) throw" },
  { file: I, name: "题目 ID 不查重复", orig: "if (ids.has(it.id)) throw", repl: "if (false) throw" },
  { file: I, name: "legacy 不去单元名", orig: 'prefix !== "" && q.text.startsWith(prefix) ? q.text.slice(prefix.length) : q.text', repl: "q.text" },
  // ---------- 报告 ----------
  { file: R, name: "题干进报告", orig: 'notice: "只出候选', repl: 'notice: input.items.map((x) => x.text).join("") + "只出候选' },
  { file: R, name: "JSON 键不排序", orig: "Object.keys(x).sort(cmp).map", repl: "Object.keys(x).map" },
  { file: R, name: "清单漏列同卷", orig: "for (const p of r.paper_pairs) {", repl: "for (const p of r.paper_pairs.filter((x) => x.verdict !== \"same\")) {" },
  { file: R, name: "报告不写真实坐标系", orig: "node_system: input.nodeSystem,", repl: 'node_system: "fixture",' },
  { file: R, name: "清单不提示按 KGE 节点重算", orig: "落库须按 KGE 节点（kge.knowledge_node）重算主节点与题目族", repl: "落库前请核对" },
  { file: R, name: "清单不提示 repeat_family", orig: "记为 repeat_family（不算新证据）", repl: "记为重复（不算新证据）" },
  { file: R, name: "清单不提示完全重复会被当成新族", orig: "掌握计算会把重复题当成新族", repl: "可能有影响" },
  { file: R, name: "冲突漏掉只有近似的题", orig: "if (g !== null || f !== null || n.length > 0) out.push", repl: "if (g !== null || f !== null) out.push" },
  { file: R, name: "清单不列冲突节", orig: 'if (r.source === "aya") {\n    L.push("", "## 六', repl: 'if (false) {\n    L.push("", "## 六' },
  // ---------- 写盘守卫 ----------
  { file: MAIN, name: "--out 不拒符号链接", orig: "if (st.isSymbolicLink()) throw", repl: "if (false) throw" },
  { file: MAIN, name: "--out 不拒文件", orig: "if (!st.isDirectory()) throw", repl: "if (false) throw" },
  { file: MAIN, name: "--out 不查是否为空", orig: "if (readdirSync(abs).length > 0) throw", repl: "if (false) throw" },
  { file: MAIN, name: "--out 只拒仓库根本身、不拒其下", orig: "if (real === root || real.startsWith(`${root}${sep}`)) throw", repl: "if (real === root) throw" },
  { file: MAIN, name: "--out 不解析真实路径（符号链接可绕进仓库）", orig: "const real = join(realpathSync(probe), ...rest);", repl: "const real = abs;" },
  { file: MAIN, name: "--out 不查 --aya 之下", orig: "ayaRoot === null ? [REPO_ROOT] : [REPO_ROOT, ayaRoot]", repl: "[REPO_ROOT]" },
  { file: MAIN, name: "写盘覆盖已有正式文件", orig: "if (lstatOrNull(dst) !== null) throw", repl: "if (false) throw" },
  { file: MAIN, name: "临时文件不用 wx", orig: '{ flag: "wx" }', repl: '{ flag: "w" }' },
  { file: MAIN, name: "不经临时文件直接写", orig: 'writeFileSync(tmp, data, { flag: "wx" });\n    renameSync(tmp, dst);', repl: 'writeFileSync(dst, data, { flag: "wx" });' },
  // ---------- 配置 ----------
  { file: C, name: "默认不要求首尾匹配", orig: "requireFirstLast: true", repl: "requireFirstLast: false" },
  { file: C, name: "默认容许一题换序", orig: "maxOutOfOrder: 0", repl: "maxOutOfOrder: 1" },
  { file: C, name: "默认占比门放松到 0.8", orig: "minOrderedRatio: 0.9", repl: "minOrderedRatio: 0.8" },
];

function copyTree(dst: string): void {
  mkdirSync(join(dst, "tools"), { recursive: true });
  cpSync(join(REPO, TOOL), join(dst, TOOL), { recursive: true, filter: (src) => !src.split(sep).includes("node_modules") });
  symlinkSync(join(REPO, "tools/kge-inventory"), join(dst, "tools/kge-inventory"));
  symlinkSync(join(REPO, "tools/kge-import"), join(dst, "tools/kge-import"));
}

function run(cmd: string, args: string[], cwd: string, timeoutMs = 300_000): number {
  const r = spawnSync(cmd, args, { cwd, stdio: "ignore", timeout: timeoutMs, env: { ...process.env, NODE_NO_WARNINGS: "1" } });
  if (r.error || r.status === null) return 124;
  return r.status;
}

const typecheck = (root: string): number => run(TSC, ["-p", "tsconfig.json"], join(root, TOOL));
function test(root: string): number {
  const dir = join(root, TOOL);
  const files = readdirSync(join(dir, "tests")).filter((f) => f.endsWith(".test.ts")).sort().map((f) => `tests/${f}`);
  return run(process.execPath, ["--no-warnings", "--test", "--test-reporter=dot", ...files], dir);
}

function main(work: string): number {
  const control = join(work, "control");
  copyTree(control);
  if (typecheck(control) !== 0 || test(control) !== 0) {
    console.log("kge-dedupe-mutation: 对照（未变异）没有通过，变异结果不可信");
    return 1;
  }
  let missed = 0;
  M.forEach((m, i) => {
    const dst = join(work, `m${String(i)}`);
    copyTree(dst);
    const path = join(dst, TOOL, m.file);
    const src = readFileSync(path, "utf8");
    if (src.split(m.orig).length !== 2) {
      console.log(`  变异本身写错（原文找不到或不唯一）：${m.name}`);
      missed++;
    } else {
      writeFileSync(path, src.replace(m.orig, () => m.repl));
      if (typecheck(dst) !== 0) {
        console.log(`  变异本身写错（类型检查不过）：${m.name}`);
        missed++;
      } else {
        const rc = test(dst);
        if (rc === 0) {
          console.log(`  漏检：${m.name}`);
          missed++;
        } else if (rc !== 1) {
          console.log(`  ${rc === 124 ? "超时（算漏检）" : `测试异常退出（${String(rc)}）`}：${m.name}`);
          missed++;
        }
      }
    }
    rmSync(dst, { recursive: true, force: true });
  });
  if (missed > 0) {
    console.log(`kge-dedupe-mutation: ${String(missed)} / ${String(M.length)} 条没有被测试发现`);
    return 1;
  }
  console.log(`kge-dedupe-mutation: ${String(M.length)} 条变异全部被测试发现`);
  return 0;
}

if (!existsSync(TSC)) {
  console.log("kge-dedupe-mutation: 跳过（没有安装 tools/kge-inventory 的依赖）");
  process.exitCode = 3;
} else {
  const work = mkdtempSync(join(tmpdir(), "kge-dedupe-mut-"));
  try {
    process.exitCode = main(work);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}
