// 变异测试（spec 4-D“变异覆盖排序、准入过滤、report 不含自由文本”）：把 generate_variation、report 的关键逻辑逐条改坏，
// 复制到临时目录里跑对应的单测，断言单测能发现（失败）。写法同 services/learning/scripts/model_router_mutations.py。
// - 临时目录按仓库结构镜像（adapters/wenli 复制；packages、docs 用符号链接只读引用），契约与映射表的相对路径不变；
// - 先确认改坏的模块仍能加载（排除“把代码改成语法错误”这种不算数的变异），再跑单测，单测失败（退出码 1）才算发现；
// - 不改仓库里的任何文件，临时目录用完即删；子进程两路并行、最低优先级，尽量不影响同时运行的其他测试文件。
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync, mkdirSync } from "node:fs";
import { setPriority, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const ADAPTER = fileURLToPath(new URL("..", import.meta.url));
const REPO = join(ADAPTER, "..", "..");

interface Mutation {
  name: string;
  file: string;
  from: string;
  to: string;
  tests: string[];
}

const VARIATION = ["tests/variation.test.ts"];
const REPORT = ["tests/report.test.ts"];
export const MUTATIONS: Mutation[] = [
  // ---------- generate_variation：同能力点、同题型、已准入 ----------
  { name: "变式不查同题型", file: "src/adapter.ts", from: "(type === undefined || questionTypeOf(it) === type) &&", to: "", tests: VARIATION },
  { name: "变式不查准入（未准入的题也选）", file: "src/adapter.ts", from: 'it.admission === "admitted" &&', to: "", tests: VARIATION },
  { name: "变式不查同能力格", file: "src/adapter.ts", from: "it.cell === target &&", to: "", tests: VARIATION },
  { name: "变式不排除做过的题", file: "src/adapter.ts", from: ".filter((it) => !excluded.has(`wenli:${it.id}`))", to: '.filter((it) => it.id !== "")', tests: VARIATION },
  { name: "变式把源题算进候选", file: "src/adapter.ts", from: "it.id !== source?.id &&", to: "", tests: VARIATION },
  { name: "变式不按 count 截断", file: "src/adapter.ts", from: ".slice(0, req.count)", to: ".slice(0)", tests: VARIATION },
  { name: "变式空结果原因写死", file: "src/adapter.ts", from: 'emptyReason = matched.length > 0 ? "all_excluded" : "no_admitted_candidate";', to: 'emptyReason = "no_admitted_candidate";', tests: VARIATION },
  { name: "变式作文不分体裁", file: "src/content.ts", from: "`essay_${x.genre}`", to: '"essay"', tests: VARIATION },
  { name: "变式不限定同族候选", file: "src/adapter.ts", from: "(!family || family.has(`wenli:${it.id}`)),", to: "true,", tests: VARIATION },
  // ---------- report ----------
  { name: "report 窗口右端闭合", file: "src/core/report.ts", from: "x >= from && x < to", to: "x >= from && x <= to", tests: REPORT },
  { name: "report 窗外证据也计入", file: "src/adapter.ts", from: "      if (!inWindow(e.occurred_at, from, to)) continue;\n      if (ATTEMPT_KINDS.has(e.kind)) {\n        const local", to: "      if (ATTEMPT_KINDS.has(e.kind)) {\n        const local", tests: REPORT },
  { name: "report 不按 attempt_id 去重", file: "src/adapter.ts", from: "const attemptKey = e.attempt_id ?? e.evidence_id;\n        attemptsByCell", to: "const attemptKey = e.evidence_id;\n        attemptsByCell", tests: REPORT },
  { name: "report 夹带亮点编码（结论）", file: "src/adapter.ts", from: "result: { skill_summary: skillSummary, error_distribution: errorDistribution },", to: 'result: { skill_summary: skillSummary, error_distribution: errorDistribution, highlight_codes: ["needs_more_practice"] },', tests: REPORT },
  { name: "report 不核对错因映射表（道法编码混入）", file: "src/adapter.ts", from: "if (taxonomy.crossSubjectOf.get(a.subject_category) !== a.cross_subject_category) {", to: "if (a.confidence > 1) {", tests: REPORT },
  { name: "report trend 方向反了", file: "src/core/report.ts", from: 'return d > 0 ? "up" : d < 0 ? "down" : "flat";', to: 'return d > 0 ? "down" : d < 0 ? "up" : "flat";', tests: REPORT },
  { name: "report 未准入证据也报 formal", file: "src/adapter.ts", from: 'semantics: counted.every((e) => e.admission === "admitted") ? "formal" : "practice",', to: 'semantics: "formal",', tests: REPORT },
  { name: "report 置信度写死 1", file: "src/adapter.ts", from: "confidence: attributions.length > 0 ? round3(Math.min(...attributions.map((a) => a.confidence))) : 1,", to: "confidence: 1,", tests: REPORT },
  { name: "report 错因列出零计数", file: "src/adapter.ts", from: ".filter((d) => d.count > 0);", to: ".filter((d) => d.count >= 0);", tests: REPORT },
];

function run(cmd: string, args: string[], cwd: string): Promise<number> {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, { cwd, stdio: "ignore", env: { PATH: process.env.PATH ?? "/usr/bin:/bin" } });
    // 子进程降到最低优先级：与同一轮里其他测试文件并行时不挤占它们（有几条幂等测试对时序敏感）
    try {
      if (p.pid) setPriority(p.pid, 19);
    } catch {
      // 平台不支持时照常运行
    }
    p.on("close", (code) => resolve(code ?? -1));
  });
}

function mirror(): string {
  const root = mkdtempSync(join(tmpdir(), "wenli-mut."));
  const dst = join(root, "adapters", "wenli");
  mkdirSync(dst, { recursive: true });
  for (const d of ["src", "tests", "content"]) cpSync(join(ADAPTER, d), join(dst, d), { recursive: true });
  for (const f of ["package.json", "tsconfig.json"]) cpSync(join(ADAPTER, f), join(dst, f));
  symlinkSync(join(ADAPTER, "node_modules"), join(dst, "node_modules"));
  symlinkSync(join(REPO, "packages"), join(root, "packages"));
  symlinkSync(join(REPO, "docs"), join(root, "docs"));
  return root;
}

/** 返回 null = 被发现；否则是没被发现的原因 */
async function check(m: Mutation): Promise<string | null> {
  const root = mirror();
  try {
    const dir = join(root, "adapters", "wenli");
    const target = join(dir, m.file);
    const src = readFileSync(target, "utf8");
    if (src.split(m.from).length !== 2) return "变异本身写错（原文不存在或不唯一）";
    writeFileSync(target, src.replace(m.from, m.to));
    const loads = await run(process.execPath, ["--no-warnings", "--input-type=module", "-e", 'await import("./src/adapter.ts")'], dir);
    if (loads !== 0) return `改坏后的模块加载失败（退出码 ${loads}），不算被断言发现`;
    const code = await run(process.execPath, ["--no-warnings", "--test", "--test-reporter=dot", ...m.tests], dir);
    return code === 1 ? null : code === 0 ? "漏检" : `不是被断言发现（退出码 ${code}）`;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test(`变异：generate_variation、report 的 ${MUTATIONS.length} 个关键改动都被单测发现`, { timeout: 300_000 }, async () => {
  assert.ok(MUTATIONS.filter((m) => m.tests === VARIATION).length >= 6 && MUTATIONS.filter((m) => m.tests === REPORT).length >= 6);
  const missed: string[] = [];
  const queue = [...MUTATIONS];
  const workers = Array.from({ length: 2 }, async () => {
    for (let m = queue.shift(); m; m = queue.shift()) {
      const why = await check(m);
      if (why) missed.push(`${m.name}：${why}`);
    }
  });
  await Promise.all(workers);
  assert.deepEqual(missed, []);
});
