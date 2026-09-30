// 变异测试（spec 4-D“变异覆盖排序、准入过滤、report 不含自由文本”）：把 plan、report 的关键逻辑逐条改坏，
// 复制到临时目录里跑对应的单测，断言单测能发现（失败）。写法同 services/learning/scripts/model_router_mutations.py。
// - 临时目录按仓库结构镜像（adapters/zhixing 复制；packages、docs 用符号链接只读引用），契约与映射表的相对路径不变；
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

const PLAN = ["tests/plan.test.ts"];
const REPORT = ["tests/report.test.ts"];
export const MUTATIONS: Mutation[] = [
  // ---------- plan：排序 ----------
  { name: "plan 不乘领域权重", file: "src/core/plan.ts", from: "const raw = u.weight * loss * gain;", to: "const raw = loss * gain;", tests: PLAN },
  { name: "plan 不乘可提升（已星亮的也排）", file: "src/core/plan.ts", from: "const raw = u.weight * loss * gain;", to: "const raw = u.weight * loss;", tests: PLAN },
  { name: "plan 失分忽略 p_mastery", file: "src/core/plan.ts", from: "m?.p_mastery !== undefined ? 1 - m.p_mastery :", to: "false ? 0 :", tests: PLAN },
  { name: "plan 不排序（按节点顺序输出）", file: "src/core/plan.ts", from: "return ranked.sort(", to: "return ranked; void (", tests: PLAN },
  { name: "plan 节点领域权重不按分值加权（取第一个领域）", file: "src/core/plan.ts", from: "a.scored += p.score * weightOf(domainOfPoint(p));", to: "if (a.total === 0) a.scored += p.score * weightOf(domainOfPoint(p));", tests: PLAN },
  // ---------- plan：准入过滤、截断 ----------
  { name: "plan 给学生未准入的题", file: "src/adapter.ts", from: 'u.items.filter((it) => it.admission === "admitted")', to: "u.items.filter((it) => it.admission !== undefined)", tests: PLAN },
  { name: "plan 时间预算不截断", file: "src/adapter.ts", from: "if (budget !== undefined && used + minutes > budget) break;", to: "if (budget === -1) break;", tests: PLAN },
  // ---------- plan：前置 ----------
  { name: "plan 按排名硬凑前置（related 也成链）", file: "src/adapter.ts", from: "...(prereq.has(i) ? { prerequisite_task_ids: prereq.get(i)!.map((j) => taskIds[j]!) } : {}),", to: "...(i > 0 ? { prerequisite_task_ids: [taskIds[i - 1]!] } : {}),", tests: PLAN },
  { name: "plan 前置不查成环", file: "src/core/plan.ts", from: "if (reaches(i, j)) return;", to: "", tests: PLAN },
  // ---------- report ----------
  { name: "report 窗口右端闭合", file: "src/core/report.ts", from: "x >= from && x < to", to: "x >= from && x <= to", tests: REPORT },
  { name: "report 窗外证据也计入", file: "src/adapter.ts", from: "if (!inWindow(e.occurred_at, from, to)) continue;", to: "", tests: REPORT },
  { name: "report 不按 attempt_id 去重", file: "src/adapter.ts", from: "const attemptKey = e.attempt_id ?? e.evidence_id;", to: "const attemptKey = e.evidence_id;", tests: REPORT },
  { name: "report 夹带亮点编码（结论）", file: "src/adapter.ts", from: "result: { skill_summary: skillSummary, error_distribution: errorDistribution },", to: 'result: { skill_summary: skillSummary, error_distribution: errorDistribution, highlight_codes: ["needs_more_practice"] },', tests: REPORT },
  { name: "report 不核对错因映射表（语文编码混入）", file: "src/adapter.ts", from: "if (!(ATTRIBUTION_CODES as readonly string[]).includes(code) || taxonomy.crossSubjectOf.get(code) !== a.cross_subject_category) {", to: "if (a.confidence > 1) {", tests: REPORT },
  { name: "report trend 方向反了", file: "src/core/report.ts", from: 'return d > 0 ? "up" : d < 0 ? "down" : "flat";', to: 'return d > 0 ? "down" : d < 0 ? "up" : "flat";', tests: REPORT },
  { name: "report 未准入证据也报 formal", file: "src/adapter.ts", from: 'semantics: counted.every((e) => e.admission === "admitted") ? "formal" : "practice",', to: 'semantics: "formal",', tests: REPORT },
  { name: "report 置信度写死 1", file: "src/adapter.ts", from: "confidence: attributions.length > 0 ? round3(Math.min(...attributions.map((a) => a.confidence))) : 1,", to: "confidence: 1,", tests: REPORT },
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
  const root = mkdtempSync(join(tmpdir(), "zhixing-mut."));
  const dst = join(root, "adapters", "zhixing");
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
    const dir = join(root, "adapters", "zhixing");
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

test(`变异：plan、report 的 ${MUTATIONS.length} 个关键改动都被单测发现`, { timeout: 300_000 }, async () => {
  assert.ok(MUTATIONS.filter((m) => m.tests === PLAN).length >= 6 && MUTATIONS.filter((m) => m.tests === REPORT).length >= 6);
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
