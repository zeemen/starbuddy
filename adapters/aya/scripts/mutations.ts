// 阿鸦适配器的变异测试：把冲刺 4-D 四个方法（plan、teach、generate_variation、report）与冲刺 4-G 内容包格式 3
// （逐题来源与准入决定、提示原文的 C9 词表检查）的关键规则逐条改坏，断言单测能发现。
// 做法同仓库里其他变异脚本（services/learning/scripts/diagnosis_mutations.py、services/knowledge/tests/mutations.sh）：
// 把 adapters/aya 的 src、tests 复制到临时目录（保持仓库结构：content、conformance、scripts、node_modules 与仓库根的
// packages、docs 用软链接指回原件），每个变异改一处（或几处）源码，先过类型检查（不过 = 变异本身写错），再跑新方法的单测；
// 只有“测试失败”（node --test 退出码 1）算发现。先跑一次不变异的对照组，必须通过。
//
// 用法（在 adapters/aya 下）：npm run mutation；或在仓库根目录 make adapter-aya-mutation。
// 退出码：0 全部发现；1 有漏检、变异写错或对照组失败；3 没有安装依赖（Makefile 决定是否算失败）。
import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const HERE = resolve(new URL(".", import.meta.url).pathname, "..");
const REPO = resolve(HERE, "../..");
const TESTS = [
  "tests/plan.test.ts",
  "tests/variation.test.ts",
  "tests/report.test.ts",
  "tests/teach.test.ts",
  "tests/methods-v1.test.ts",
  "tests/pack.test.ts",
  "tests/pack-format3.test.ts",
  "tests/hint-scan.test.ts",
  "tests/present.test.ts",
];
const P = "src/methods/plan.ts";
const V = "src/methods/variation.ts";
const R = "src/methods/report.ts";
const T = "src/methods/teach.ts";
const S = "src/methods/shared.ts";
const A = "src/adapter.ts";
const C = "src/content.ts";
const AD = "src/pack/admission.ts";
const HS = "src/pack/hint-scan.ts";
const PR = "src/methods/present.ts";
const RT = "src/rich-text.ts";

type Edit = [file: string, from: string, to: string];
// 名字 → 改动（原文必须恰好出现，替换第一处）
const MUTATIONS: Record<string, Edit[]> = {
  // ---------- plan ----------
  "复测按时刻降序（或按字符串）排": [[P, "retests.sort((a, b) => a.at - b.at ||", "retests.sort((a, b) => b.at - a.at ||"]],
  "时刻不按时区解析（把本地时间当 UTC）": [[S, "  const t = Date.parse(ts);", "  const t = Date.parse(`${ts.slice(0, 19)}Z`);"]],
  "unseen 也排练习任务": [
    [P, 'const PRACTICE_STATES = new Set(["understand", "execute"]);', 'const PRACTICE_STATES = new Set(["understand", "execute", "unseen"]);'],
    [P, "{ understand: 0.7, execute: 0.5 }", "{ understand: 0.7, execute: 0.5, unseen: 0.9 }"],
  ],
  "时间预算不截断": [[P, "if (used + t.estimatedMinutes > budget) break;", "if (used > budget) break;"]],
  "没有预算时不限 8 个": [[P, "} else if (selected.length >= PLAN_MAX_TASKS) break;", "}"]],
  "前置方向反了": [[P, "if (edges.some((e) => from.has(e.from_id) && to.has(e.to_id))) b.prerequisites.push(ai);", "if (edges.some((e) => from.has(e.to_id) && to.has(e.from_id))) b.prerequisites.push(ai);"]],
  "没有用到 node_path 也写 graph 段": [[A, "const graph = out.usedGraph && content.pack.skill_links.map_ref ?", "const graph = content.pack.skill_links.map_ref ?"]],
  "复测优先级落进练习区间": [[P, "export const RETEST_PRIORITY_LOW = 0.85;", "export const RETEST_PRIORITY_LOW = 0.5;"]],
  "题型过滤失效": [[S, "return !codes || codes.length === 0 || codes.includes(QUESTION_TYPE_CODE[item.format]);", "return true;"]],
  "focus_skill_nodes 不生效": [[P, "if (focus.size > 0 && !focus.has(skill)) continue;", ""]],
  "task_id 不确定（掺随机数）": [[A, "`task:${t.kind}:${t.skill}`", "`task:${t.kind}:${t.skill}:${Math.random()}`"]],
  "引用未准入的题仍是 formal": [[A, 'semantics: admitted ? "formal" : "practice",', 'semantics: "formal",']],
  "引用未准入的题置信度不降": [[A, "confidence: admitted || !lowerConfidence ? 1 : UNADMITTED_CONTENT_CONFIDENCE,", "confidence: 1,"]],
  // ---------- generate_variation ----------
  "未准入的题也当候选": [[V, 'if (it.admission !== "admitted" || !formatAllowed(it, codes)) continue;', "if (!formatAllowed(it, codes)) continue;"]],
  "原题也当候选": [[V, "    if (source && it.id === source.id) continue;\n", ""]],
  "exclude_item_refs 不生效": [[V, "const eligible = matched.filter((p) => !excluded.has(p.item.id));", "const eligible = matched.filter((p) => p.item.id !== \"\" || excluded.size < 0);"]],
  "版权受限的族内题不排除": [[V, '      if (f.license_state === "restricted") continue;\n', ""]],
  "有 item_family 时仍从整个内容包选": [[V, "  if (input.item_family !== undefined) {\n    pool = [];", "  if (input.item_family !== undefined && input.count < 0) {\n    pool = [];"]],
  "全被排除时报 no_admitted_candidate": [[V, 'emptyReason: matched.length > 0 ? "all_excluded" : "no_admitted_candidate"', 'emptyReason: "no_admitted_candidate"']],
  "不输出 prior_difficulty": [[V, "...(prior !== undefined ? { difficulty: prior } : {})", "...(prior === -1 ? { difficulty: prior } : {})"]],
  "未知错因编码不拒绝": [[V, "if (input.subject_error_category !== undefined && !knownCodes.has(input.subject_error_category)) {", "if (input.subject_error_category === \"\" && !knownCodes.has(input.subject_error_category)) {"]],
  "排序不把同节点且同错因放前": [[V, "p.sameSkillNode && p.sameErrorCategory ? 0 : 1", "p.sameSkillNode && p.sameErrorCategory ? 1 : 1"]],
  // ---------- report ----------
  "窗外证据也计入": [[R, "    return t >= from && t < to;", "    return t >= from || t < to;"]],
  "恰在 to 的证据计入": [[R, "    return t >= from && t < to;", "    return t >= from && t <= to;"]],
  "attempts 不按 attempt_id 去重": [[R, "set.add(e.attempt_id ?? e.evidence_id);", "set.add(e.evidence_id);"]],
  "敏感类错因计入分布": [[R, "    if (sensitive.has(a.subject_category)) continue;\n", ""]],
  "窗前没有记录不报 new": [[R, 'if (before.length === 0) return "new";', 'if (before.length === 0) return "flat";']],
  "趋势方向反了": [[R, 'return last > base ? "up" : last < base ? "down" : "flat";', 'return last < base ? "up" : last > base ? "down" : "flat";']],
  "to ≤ from 不拒绝": [[R, "if (to <= from) throw", "if (to < from) throw"]],
  "别的学科的数据不拒绝": [[R, "if (refs.some((r) => !isAyaRef(r))) throw", "if (refs.some((r) => r === \"\")) throw"]],
  "置信度不取错因最小值": [[R, "confidence = Math.min(confidence, a.confidence);", "confidence = Math.max(confidence, a.confidence);"]],
  "no_activity 不看作答": [[R, 'if (![...windowed.values()].some((e) => RESPONSE_KINDS.has(e.kind))) highlightCodes.push("no_activity");', 'if (windowed.size === 0) highlightCodes.push("no_activity");']],
  // ---------- teach ----------
  // 封顶被绕过（作答提交前最多 3 级）
  "封顶被绕过：不看提交前上限": [[T, "Math.min(allowed, maxBeforeSubmit, MAX_HINT_LEVEL, item.hintPath.length - 1)", "Math.min(allowed, MAX_HINT_LEVEL, item.hintPath.length - 1)"]],
  "封顶被绕过：适配器不把配置传给 teach": [[A, "hint ? teachHint(content, r, teachConfig)", "hint ? teachHint(content, r, { max_hint_level_before_submit: 5 })"]],
  "封顶被绕过：配置允许超过 3": [[T, "m > DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT) {", "m > MAX_HINT_LEVEL) {"]],
  "封顶被绕过：默认上限改成 5": [[T, "export const DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT = 3;", "export const DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT = 5;"]],
  // 含答案的那一级被返回
  "含答案的级照样返回原文": [[T, '  if (item.format === "fill" && level <= ANSWER_GUARD_MAX_LEVEL && hintRevealsAnswer(item.hintPath[level]!, item.answer)) return -1;\n', ""]],
  "答案检查只看 0 级": [[T, "export const ANSWER_GUARD_MAX_LEVEL = 3;", "export const ANSWER_GUARD_MAX_LEVEL = 0;"]],
  "单字符答案不查": [[T, "${escapeRe(c)}(?![A-Za-z0-9.^_])`).test(h);", "${escapeRe(c)}(?![A-Za-z0-9.^_])`).test(h) && false;"]],
  "含答案时降级到低一级（旧做法）而不是只指回题目": [[T, "hintRevealsAnswer(item.hintPath[level]!, item.answer)) return -1;", "hintRevealsAnswer(item.hintPath[level]!, item.answer)) return level > 0 ? hintLevelFor(item, level - 1, maxBeforeSubmit) : -1;"]],
  "越级：连同低等级提示一起返回": [[T, 'return { segments: [{ type: "text", text: item.hintPath[level]! }], hintLevelUsed: level, item };', 'return { segments: item.hintPath.slice(0, level + 2).map((text) => ({ type: "text" as const, text })), hintLevelUsed: level, item };']],
  "页面直出加了话术": [[T, 'return { segments: parts.map((text) => ({ type: "text", text })), hintLevelUsed: 0 };', 'return { segments: [{ type: "text", text: "（合成话术）" }, ...parts.map((text) => ({ type: "text" as const, text }))], hintLevelUsed: 0 };']],
  "lesson 没有页面时改走提示阶梯": [[A, "const out = hint ? teachHint(content, r, teachConfig) : teachLesson(content, r);", "const out = hint || !r.knowledge_page ? teachHint(content, r, teachConfig) : teachLesson(content, r);"]],
  "skill_node 与题目不符不拒绝": [[T, "if (!item.mainNodes.includes(localId(req.skill_node))) {", "if (item.mainNodes.includes(\"\" + localId(req.skill_node).slice(99))) {"]],
  "身份披露置真（写了话术却不经人设层）": [[A, "          ai_disclosure_shown: false,", "          ai_disclosure_shown: true,"]],
  // ---------- 共同 ----------
  "0.9.0 请求也应答": [[A, 'if (version === "0.9.0") {\n      throw new ProblemError(501', 'if (version === "0.8.0" as string) {\n      throw new ProblemError(501']],
  "能力声明 teach 未实现": [[A, 'teach: v1Methods ? { implemented: true, async: false, notes_code: "deterministic_no_model" } : { implemented: false },', "teach: { implemented: false },"]],
  "新方法不做幂等冲突检测": [[A, "return idempotent(method, req.context.idempotency_key, fp, () => run(req, fp, version));", "return run(req, fp, version);"]],
  "内容包不检查提示阶梯": [[C, "problems.push(`题目 ${it.id} 的提示阶梯不合格`);", "void 0;"]],
  // ---------- 内容包格式 3：逐题来源与准入决定（冲刺 4-G） ----------
  "admitted 不要求授权已核": [[AD, 'source.source_type !== "unknown" && source.license_status === "cleared";', 'source.source_type !== "unknown" && source.license_status !== "denied";']],
  "admitted 的审核等级门槛降到 Q1": [[AD, 'export const MIN_ADMITTED_REVIEW_LEVEL: ReviewLevel = "Q2";', 'export const MIN_ADMITTED_REVIEW_LEVEL: ReviewLevel = "Q1";']],
  "pending_review 不要求来源已核": [[AD, "      if (!verifiedSource) out.push(`题目 ${id} 准入为 pending_review", "      if (!verifiedSource && id === \"\") out.push(`题目 ${id} 准入为 pending_review"]],
  "决定里内容包没有的题被静默接受": [[AD, 'if (typeof id !== "string" || !itemIds.has(id)) {', 'if (typeof id !== "string") {']],
  "决定里的重复题不拒绝": [[AD, "if (seen.has(id)) problems.push(", "if (seen.has(id) && id === \"\") problems.push("]],
  "六段只核对学科": [[AD, "if (c[k] !== curriculum[k]) problems.push(", 'if (c[k] !== curriculum[k] && k === "subject") problems.push(']],
  "重建不重置：旧决定残留": [[AD, 'if (!d) return { ...rest, source: { ...DEFAULT_SOURCE }, admission: "source_unverified" };', "if (!d) return it;"]],
  "版本号不带决定摘要": [[AD, "return digest === null ? base : `${base}.d", "return digest !== base ? base : `${base}.d"]],
  "决定摘要依赖排列顺序": [[AD, "    .sort((a, b) => (a.item_id < b.item_id ? -1 : a.item_id > b.item_id ? 1 : 0))\n    .map((d) => [d.item_id", "    .map((d) => [d.item_id"]],
  "序列化不排序键（构造顺序影响字节）": [[AD, "Object.fromEntries(Object.keys(v).sort().map(", "Object.fromEntries(Object.keys(v).map("]],
  "加载时不核对准入与来源": [[C, "else problems.push(...admissionProblems(it.id, it.admission, it.source, it.admission_review));", "else void 0;"]],
  // ---------- C9 词表检查（提示原文） ----------
  "C9 严格模式有命中也放行": [[HS, 'if (report.mode === "strict" && report.hits.length > 0) {', 'if (report.mode === "strict" && report.hits.length < 0) {']],
  "C9 命中映射到错的等级": [[HS, "({ item_id: it.id, level, text })", "({ item_id: it.id, level: level + 1, text })"]],
  "C9 不核对结果条数": [[HS, "if (!Array.isArray(out.results) || out.results.length !== entries.length) throw", "if (!Array.isArray(out.results)) throw"]],
  "C9 不核对结果顺序": [[HS, "if (r.source !== `stdin[${i}]`) throw", "if (r.source === \"\") throw"]],
  "C9 记录严格模式带标记也能加载": [[C, 'if (scan.mode === "strict" && scan.flagged_items.length > 0) problems.push(', 'if (scan.mode === "strict" && scan.flagged_items.length < 0) problems.push(']],
  // ---------- present_item（契约 1.1.0 候选版，PLT-12） ----------
  "题面不按白名单投影：选项原样带出（含陷阱码）": [[PR, "return runs ? { key: o.key, content: projectRuns(runs) } : { key: o.key };", "return runs ? { ...o, content: projectRuns(runs) } : { ...o };"]],
  "版权受限仍给题面": [[PR, 'const complete = item.stem !== undefined && license !== "restricted";', "const complete = item.stem !== undefined;"]],
  "多选上限取正确选项数（从答案推出）": [[PR, 'max_choices: item.format === "single" ? 1 : (item.options ?? []).length', 'max_choices: item.format === "single" ? 1 : item.answer.length']],
  "填空按标准答案的形状给 number": [[PR, '? { kind: "expression", max_length: FILL_MAX_LENGTH }', '? { kind: item.answer.every((a) => /^-?\\d+(\\.\\d+)?$/.test(a)) ? "number" : "expression", max_length: FILL_MAX_LENGTH }']],
  "授权已核的自编题报 licensed": [[PR, 's.source_type === "self_authored" ? "original" : "licensed"', '"licensed"']],
  "题面进了幂等缓存（挤掉 grade 的条目）": [[A, "    const item = findItem(req.item_ref);\n    return selfCheck(\"PresentItemResponse\", {",
    "    if (idempotency.size >= cacheSize) idempotency.delete(idempotency.keys().next().value!);\n    idempotency.set(`present_item\\0${req.item_ref}`, { fingerprint: req.item_ref, at: Date.now() });\n    const item = findItem(req.item_ref);\n    return selfCheck(\"PresentItemResponse\", {"]],
  "不设候选开关也读替代包": [[C, "  if (!src.candidateAllowed) {\n    throw new Error(`替代内容包", "  if (src.candidateAllowed === undefined) {\n    throw new Error(`替代内容包"]],
  "替代包的版本号不带摘要": [[C, "const versionTag = altDigest ? `${pack.pack_version}-${altDigest}` : pack.pack_version;", "const versionTag = pack.pack_version;"]],
  "叠加文件不计入摘要": [[C, "    hash.update(\"overlay\\0\").update(overlayText);\n", ""]],
  "题面不做答案词扫描": [[C, "problems.push(...(shape.length ? shape : presentationLeakProblems(it)));", "problems.push(...shape);"]],
  "填空题干写出答案也放行": [[C, 'if (it.format === "fill" && plain.some((s) => hintRevealsAnswer(s, answers))) out.push(', 'if (it.format === "fill" && plain.some((s) => s === answers[0])) out.push(']],
  "“解析式”之外的“解析”也放行": [[C, "解析(?!式)|", "解析式|"]],
  "题面不限总字数": [[C, "> MAX_TOTAL_CHARS) out.push(", "> MAX_TOTAL_CHARS * 2) out.push("]],
  "文字片段允许双向控制符与零宽字符": [[RT, "\\u007F-\\u009F\\u200B-\\u200F\\u2028\\u2029\\u202A-\\u202E\\u2066-\\u2069\\uFEFF]{1,2000}$/u;", "\\u007F]{1,2000}$/u;"]],
  "1.0.0 请求也应答题面": [[A, 'if (version !== "1.1.0") {\n      throw new ProblemError(501, "METHOD_NOT_IMPLEMENTED", "present_item', 'if (version === "0.9.0") {\n      throw new ProblemError(501, "METHOD_NOT_IMPLEMENTED", "present_item']],
  "两处内容版本不一致不拒绝": [[A, "if (req.context.content_version && canonical(req.context.content_version) !== canonical(req.content_version)) {", "if (req.context.content_version && canonical(req.context.content_version) === \"\") {"]],
  "顶层内容版本不核对": [[A, "checkContext({ ...req.context, content_version: req.content_version } as unknown as RequestContext, true);",
    "checkContext(req.context as unknown as RequestContext, false);"]],
  "内容包不检查题干": [[C, "if (it.stem !== undefined) out.push(...richTextProblems(", "if (it.stem !== undefined && it.id === \"\") out.push(...richTextProblems("]],
  "文字片段允许尖括号": [[RT, "export const TEXT_RE = /^[^<>\\u0000", "export const TEXT_RE = /^[^\\u0000"]],
  "公式允许 \\href": [[RT, "(?:href|url|", "(?:url|"]],
  "能力声明 1.1.0 不声明 present_item": [[A, '...(contract.version === "1.1.0" ? { present_item: { implemented: true, async: false } } : {}),', ""]],
};

function sh(cmd: string, args: string[], cwd: string, timeoutMs = 240_000): Promise<number> {
  return new Promise((res) => {
    const p = spawn(cmd, args, { cwd, stdio: "ignore", env: { PATH: process.env.PATH ?? "/usr/bin:/bin", HOME: process.env.HOME ?? "/tmp" } });
    const t = setTimeout(() => p.kill("SIGKILL"), timeoutMs);
    p.on("exit", (code) => {
      clearTimeout(t);
      res(code ?? 124);
    });
  });
}

function copyTree(): { root: string; dst: string } {
  const root = mkdtempSync(join(tmpdir(), "aya-mut."));
  const dst = join(root, "repo/adapters/aya");
  mkdirSync(dst, { recursive: true });
  for (const d of ["src", "tests"]) cpSync(join(HERE, d), join(dst, d), { recursive: true });
  for (const f of ["tsconfig.json", "package.json"]) cpSync(join(HERE, f), join(dst, f));
  for (const d of ["content", "conformance", "scripts", "node_modules"]) symlinkSync(join(HERE, d), join(dst, d));
  for (const d of ["packages", "docs"]) symlinkSync(join(REPO, d), join(root, "repo", d));
  // 内容登记脚本已迁到 tools/content-register/（W13 T11）：复制进副本（不软链），它引用的内容包加载代码才是副本里改过的那份
  const reg = join(root, "repo/tools/content-register");
  mkdirSync(reg, { recursive: true });
  for (const f of ["aya.ts", "package.json"]) cpSync(join(REPO, "tools/content-register", f), join(reg, f));
  return { root, dst };
}

type Outcome = "passed" | "failed" | "bad_edit" | "type_error" | { exit: number };

/** 在副本里改源码、类型检查、跑单测 */
async function evaluate(edits: Edit[]): Promise<Outcome> {
  const { root, dst } = copyTree();
  try {
    for (const [file, from, to] of edits) {
      const path = join(dst, file);
      const text = readFileSync(path, "utf8");
      if (!text.includes(from)) return "bad_edit";
      writeFileSync(path, text.replace(from, to));
    }
    if ((await sh(join(dst, "node_modules/.bin/tsc"), ["-p", "tsconfig.json"], dst)) !== 0) return "type_error";
    const rc = await sh(process.execPath, ["--no-warnings", "--test", "--test-reporter=dot", ...TESTS], dst);
    return rc === 0 ? "passed" : rc === 1 ? "failed" : { exit: rc };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

/** 变异被发现返回 null，否则返回说明 */
async function runOne(name: string, edits: Edit[]): Promise<string | null> {
  const o = await evaluate(edits);
  if (o === "failed") return null;
  if (o === "passed") return `漏检：${name}`;
  if (o === "bad_edit") return `变异本身写错（找不到原文）：${name}`;
  if (o === "type_error") return `变异本身写错（类型检查不过）：${name}`;
  return `不是被断言发现（退出码 ${o.exit}）：${name}`;
}

async function main(): Promise<number> {
  if (!existsSync(join(HERE, "node_modules/ajv")) || !existsSync(join(HERE, "node_modules/.bin/tsc"))) {
    console.log("adapter-aya-mutation: 跳过（没有安装依赖；cd adapters/aya && npm ci）");
    return 3;
  }
  const control = await evaluate([]);
  if (control !== "passed") {
    console.log(`adapter-aya-mutation: 失败（对照组没有通过：${JSON.stringify(control)}）`);
    return 1;
  }
  const entries = Object.entries(MUTATIONS);
  const results: (string | null)[] = new Array(entries.length).fill(null);
  let next = 0;
  const worker = async () => {
    while (next < entries.length) {
      const i = next++;
      results[i] = await runOne(...entries[i]!);
    }
  };
  await Promise.all(Array.from({ length: 4 }, worker));
  const missed = results.filter((r): r is string => r !== null);
  for (const m of missed) console.log(`  ${m}`);
  if (missed.length > 0) {
    console.log(`adapter-aya-mutation: 失败，${missed.length}/${entries.length} 个变异没有被发现`);
    return 1;
  }
  console.log(`adapter-aya-mutation: 通过，${entries.length}/${entries.length} 个变异都被单测发现`);
  return 0;
}

process.exit(await main());
