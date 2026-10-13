// KGE-07 第一步的变异测试：把语文导入器（tools/kge-import/src/chinese）、语文覆盖报表（tools/kge-coverage/src/chinese.ts、main.ts）
// 与库上快照导出（tools/kge-coverage/sql/chinese-snapshot.sql）的关键检查逐条改坏，断言测试能发现。
// 做法：把 tools/kge-import 与 tools/kge-coverage 复制到临时目录（保持仓库结构；db、docs、adapters、tools/kge-inventory 与
// kge-coverage 的 node_modules 软链接回仓库），改一处源码，先过类型检查（不过 = 变异本身写错），再跑对应的测试：
// 只有“测试失败（退出码 1）”才算被发现；原文找不到或不唯一也算变异写错。先跑一次不变异的对照，必须全部通过。
// 库上一组（db）要本机 Postgres；没有时这一组跳过，最后以退出码 3 报“部分跳过”（严格模式下 Makefile 判失败）。
// 用法（仓库根目录）：node --no-warnings tools/kge-import/tests/chinese/mutations.ts
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("../../../../", import.meta.url));

type Group = "import" | "coverage" | "db";
interface Mutation { name: string; file: string; orig: string; repl: string; group: Group }

const IMP = "tools/kge-import/src/chinese";
const COV = "tools/kge-coverage";
const M: Mutation[] = [
  // ---------- 导入器：校验 ----------
  { group: "import", name: "素材可以进前置图", file: `${IMP}/map.ts`, orig: 'if (ka === "material" || kb === "material") {', repl: "if (false) {" },
  { group: "import", name: "方法可以进前置图", file: `${IMP}/map.ts`, orig: 'if (ka === "method" || kb === "method") throw', repl: "if (false) throw" },
  { group: "import", name: "层次递进可以跳级", file: `${IMP}/map.ts`, orig: "ladder.rungs.indexOf(y.rung) !== ladder.rungs.indexOf(x.rung) + 1",
    repl: "ladder.rungs.indexOf(y.rung) <= ladder.rungs.indexOf(x.rung)" },
  { group: "import", name: "层次递进可以跨能力", file: `${IMP}/map.ts`, orig: "if (x.ability !== y.ability || ladder", repl: "if (ladder" },
  { group: "import", name: "层次递进可以跨文本类型或题型", file: `${IMP}/map.ts`, orig: "if (x.textType !== y.textType || x.questionType !== y.questionType) {",
    repl: "if (false) {" },
  { group: "import", name: "合成样例的自由文本不查合成标记", file: `${IMP}/map.ts`, orig: 'if (synthetic && !value.includes("合成")) {', repl: "if (false) {" },
  { group: "import", name: "真实内容（synthetic: false）不拒", file: `${IMP}/map.ts`, orig: "if (!synthetic) {", repl: "if (false) {" },
  { group: "import", name: "层次递进落成强前置", file: `${IMP}/map.ts`, orig: 'strength: "weak", relation: r.type,', repl: 'strength: "strong", relation: r.type,' },
  { group: "import", name: "显式前置不要求显式标注", file: `${IMP}/map.ts`, orig: 'if (type === "prerequisite" && o.explicit_prerequisite !== true) {', repl: "if (false) {" },
  { group: "import", name: "层次递进与显式前置可以重复", file: `${IMP}/map.ts`, orig: "if (prev) throw new ChineseError(`边 ${r.from} → ${r.to}：这一对能力点",
    repl: "if (false) throw new ChineseError(`边 ${r.from} → ${r.to}：这一对能力点" },
  { group: "import", name: "前置图不查环", file: `${IMP}/map.ts`, orig: "if (cycles.length > 0) throw", repl: "if (false) throw" },
  { group: "import", name: "前置图不查传递冗余边", file: `${IMP}/map.ts`, orig: "if (trans.length > 0) throw", repl: "if (false) throw" },
  { group: "import", name: "不查孤点", file: `${IMP}/map.ts`, orig: "if (orphans.length > 0) throw", repl: "if (false) throw" },
  { group: "import", name: "投影不算孤点的连接（只看边）", file: `${IMP}/map.ts`, orig: "...edges.flatMap((e) => [e.from, e.to]), ...projections.map((p) => p.nodeId)",
    repl: "...edges.flatMap((e) => [e.from, e.to])" },
  { group: "import", name: "不拒教材注释类字段", file: `${IMP}/map.ts`, orig: "return FORBIDDEN_KEY.test(k) || CHINESE_FORBIDDEN_KEY.test(k);", repl: "return FORBIDDEN_KEY.test(k);" },
  { group: "import", name: "素材与方法可以带认知层次", file: `${IMP}/map.ts`, orig: 'if (kind !== "ability_kp" && levels.length > 0) {', repl: "if (false) {" },
  { group: "import", name: "合成样例不查合成标记", file: `${IMP}/map.ts`, orig: 'if (synthetic && !name.includes("合成"))', repl: "if (false)" },
  { group: "import", name: "合成样例领域 ID 不查 SYN", file: `${IMP}/map.ts`, orig: "if (synthetic && !domainId.split(\".\")[1]!.startsWith(SYNTHETIC_SEGMENT)) {", repl: "if (false) {" },
  { group: "import", name: "易混可以跨类型", file: `${IMP}/map.ts`, orig: 'if (r.type === "confusable" && (ka !== kb || ka === "method")) {', repl: "if (false) {" },
  { group: "import", name: "关联与易混不查重复", file: `${IMP}/map.ts`, orig: "if (prev) throw new ChineseError(`关联边", repl: "if (false) throw new ChineseError(`关联边" },
  { group: "import", name: "uses_method 起点不查", file: `${IMP}/map.ts`, orig: 'if (ka !== "ability_kp" || kb !== "method")', repl: 'if (kb !== "method")' },
  { group: "import", name: "能力点四要素组合不查重复", file: `${IMP}/map.ts`, orig: "if (dup) throw", repl: "if (false) throw" },
  { group: "import", name: "语文前缀不查", file: `${IMP}/map.ts`, orig: "if (v.split(\".\")[0] !== PREFIX) throw", repl: "if (false) throw" },
  { group: "import", name: "合成与真实可以混在一个版本", file: `${IMP}/map.ts`, orig: "if (new Set(domains.map((d) => d.synthetic)).size > 1) throw", repl: "if (false) throw" },
  // ---------- 导入器：种子 ----------
  { group: "import", name: "投影写进原文", file: `${IMP}/seed.ts`, orig: "${p.pageFrom}, ${p.pageTo}, NULL, 'unverified')", repl: "${p.pageFrom}, ${p.pageTo}, ${q(p.chapter)}, 'unverified')" },
  { group: "import", name: "合成种子不带挡板", file: `${IMP}/seed.ts`, orig: "...(map.synthetic ? [syntheticGuard()] : []),", repl: "" },
  { group: "import", name: "边不排序（输出依赖文件顺序）", file: `${IMP}/map.ts`, orig: "edges.sort((a, b) => cmp(a.from, b.from) || cmp(a.to, b.to) || cmp(a.type, b.type));", repl: "" },
  { group: "import", name: "报告漏算拓展范围", file: `${IMP}/seed.ts`, orig: 'n.level === "node" && n.scope !== "required"', repl: 'n.level === "node" && n.scope === "elective"' },
  // ---------- 覆盖报表 ----------
  { group: "coverage", name: "层次递进不分强弱", file: `${COV}/src/chinese.ts`, orig: 'e.type === "prerequisite" && e.strength === "weak"', repl: 'e.type === "prerequisite"' },
  { group: "coverage", name: "停用节点上的边也算", file: `${COV}/src/chinese.ts`, orig: "const edges = s.edges.filter((e) => activeIds.has(e.from_id) && activeIds.has(e.to_id));",
    repl: "const edges = s.edges;" },
  { group: "coverage", name: "停用节点进矩阵", file: `${COV}/src/chinese.ts`, orig: 'const nodes = active.filter((n) => n.level === "node")', repl: 'const nodes = s.nodes.filter((n) => n.level === "node")' },
  { group: "coverage", name: "拓展素材进缺口清单", file: `${COV}/src/chinese.ts`, orig: 'rows.filter((x) => inFocus(x.id) && x.scope === "required")', repl: "rows.filter((x) => inFocus(x.id))" },
  { group: "coverage", name: "未核对出处不报缺口", file: `${COV}/src/chinese.ts`, orig: 'else if (r.verified === 0) gaps.push', repl: "else if (false) gaps.push" },
  { group: "coverage", name: "快照不查 CHN 前缀", file: `${COV}/src/chinese.ts`, orig: ' || !n.id.startsWith("CHN.")', repl: "" },
  { group: "coverage", name: "快照不查边的强弱取值", file: `${COV}/src/chinese.ts`, orig: ' || !["strong", "weak"].includes(e.strength)', repl: "" },
  { group: "coverage", name: "快照放过非前置的弱边", file: `${COV}/src/chinese.ts`, orig: 'if (e.strength === "weak" && e.type !== "prerequisite") throw', repl: "if (false) throw" },
  { group: "coverage", name: "库上快照导出去掉 READ ONLY", file: `${COV}/sql/chinese-snapshot.sql`, orig: "BEGIN READ ONLY;", repl: "BEGIN;" },
  { group: "coverage", name: "库上快照导出改为提交", file: `${COV}/sql/chinese-snapshot.sql`, orig: "ROLLBACK;", repl: "COMMIT;" },
  { group: "coverage", name: "命令行不接 wenli", file: `${COV}/src/main.ts`, orig: 'if (values.subject === "wenli") return chineseMain(', repl: 'if (values.subject === "wenli_off") return chineseMain(' },
  { group: "coverage", name: "道法缺省关注领域被改（回归应发现）", file: `${COV}/src/main.ts`, orig: 'values["focus-domain"] ?? DEFAULT_FOCUS_DOMAIN', repl: 'values["focus-domain"] ?? "MORAL"' },
  // ---------- 库上快照导出 ----------
  { group: "db", name: "库上快照漏导边的强弱", file: `${COV}/sql/chinese-snapshot.sql`, orig: ", 'strength', e.strength)", repl: ")" },
  { group: "db", name: "库上快照导出停用的边", file: `${COV}/sql/chinese-snapshot.sql`, orig: "AND e.status = 'active'), '[]'::jsonb)", repl: "), '[]'::jsonb)" },
];

function copyTree(dst: string): void {
  mkdirSync(join(dst, "tools"), { recursive: true });
  const noModules = (src: string) => !src.split(sep).includes("node_modules");
  cpSync(join(REPO, "tools/kge-import"), join(dst, "tools/kge-import"), { recursive: true, filter: noModules });
  cpSync(join(REPO, COV), join(dst, COV), { recursive: true, filter: noModules });
  symlinkSync(join(REPO, COV, "node_modules"), join(dst, COV, "node_modules"));
  symlinkSync(join(REPO, "tools/kge-inventory"), join(dst, "tools/kge-inventory"));
  for (const d of ["db", "docs", "adapters"]) symlinkSync(join(REPO, d), join(dst, d));
}

function run(cmd: string, args: string[], cwd: string, timeoutMs = 600_000): number {
  const r = spawnSync(cmd, args, { cwd, stdio: "ignore", timeout: timeoutMs, env: { ...process.env, NODE_NO_WARNINGS: "1" } });
  if (r.error || r.status === null) return 124;
  return r.status;
}

function typecheck(root: string, group: Group): number {
  if (group === "db") return 0;
  const dir = join(root, group === "import" ? "tools/kge-import" : COV);
  const tsc = join(REPO, group === "import" ? "tools/kge-inventory/node_modules/.bin/tsc" : `${COV}/node_modules/.bin/tsc`);
  return run(tsc, ["-p", "tsconfig.json"], dir);
}

function test(root: string, group: Group): number {
  if (group === "import") return run("node", ["--no-warnings", "--test", "--test-reporter=dot", "tests/chinese/map.test.ts"], join(root, "tools/kge-import"));
  if (group === "coverage") {
    return run("node", ["--no-warnings", "--test", "--test-reporter=dot", "tests/chinese.test.ts", "tests/daofa.test.ts", "tests/coverage.test.ts"], join(root, COV));
  }
  return run("bash", [join(root, "tools/kge-import/tests/chinese/chinese-db.sh")], root);
}

function main(work: string): number {
  const control = join(work, "control");
  copyTree(control);
  for (const g of ["import", "coverage"] as const) {
    if (typecheck(control, g) !== 0 || test(control, g) !== 0) {
      console.log(`kge-chinese-mutation: 对照（未变异）的 ${g} 组没有通过，变异结果不可信`);
      return 1;
    }
  }
  const dbRc = test(control, "db");
  if (dbRc !== 0 && dbRc !== 3) {
    console.log(`kge-chinese-mutation: 对照（未变异）的库上测试没有通过（退出码 ${dbRc}），变异结果不可信`);
    return 1;
  }
  const haveDb = dbRc === 0;
  const todo = haveDb ? M : M.filter((m) => m.group !== "db");
  if (!haveDb) console.log(`kge-chinese-mutation: 本机没有 Postgres，库上的 ${M.length - todo.length} 条变异跳过`);
  let missed = 0;
  todo.forEach((m, i) => {
    const dst = join(work, `m${i}`);
    copyTree(dst);
    const path = join(dst, m.file);
    const src = readFileSync(path, "utf8");
    if (src.split(m.orig).length !== 2) {
      console.log(`  变异本身写错（原文找不到或不唯一）：${m.name}`);
      missed++;
    } else {
      writeFileSync(path, src.replace(m.orig, m.repl));
      if (typecheck(dst, m.group) !== 0) {
        console.log(`  变异本身写错（类型检查不过）：${m.name}`);
        missed++;
      } else {
        const rc = test(dst, m.group);
        if (rc === 0) {
          console.log(`  漏检：${m.name}`);
          missed++;
        } else if (rc !== 1) {
          console.log(`  ${rc === 124 ? "超时（算漏检）" : `测试异常退出（${rc}）`}：${m.name}`);
          missed++;
        }
      }
    }
    rmSync(dst, { recursive: true, force: true });
  });
  if (missed > 0) {
    console.log(`kge-chinese-mutation: ${missed} / ${todo.length} 条没有被测试发现`);
    return 1;
  }
  console.log(`kge-chinese-mutation: ${todo.length} 条变异全部被测试发现`);
  return haveDb ? 0 : 3;
}

const haveTsc = existsSync(join(REPO, "tools/kge-inventory/node_modules/.bin/tsc")) && existsSync(join(REPO, `${COV}/node_modules/.bin/tsc`));
if (!haveTsc) {
  console.log("kge-chinese-mutation: 跳过（没有安装 tools/kge-inventory 或 tools/kge-coverage 的依赖）");
  process.exitCode = 3;
} else {
  const work = mkdtempSync(join(tmpdir(), "kge-chn-mut-"));
  try {
    process.exitCode = main(work);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}
