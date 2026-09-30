// 从 legacy 阿鸦题库生成服务端只读内容包 content/pack.json（spec 范围第 3 条）。
//
// 用法（在 adapters/aya 下）：
//   AYA_LEGACY_ROOT=/path/to/ayaheshui npm run build:pack
// 默认 legacy 根目录是仓库里的 legacy/aya（只读）。legacy 工作区有未提交改动时拒绝生成，保证可追溯到提交号。
//
// 格式 1 只含判分需要的字段：题型、选项 key 与陷阱、标准答案、典型错误答案、主测节点、审核等级。
// 格式 2（specs/adapter-aya-methods-sprint4 设计要点第 0 节）另加：题目用途 usage、预估用时 estMinutes、提示阶梯 hintPath；
// 节点板块 board；板块中考占比（Q1 推导值，待教研校准）；“阿鸦节点 → KGE 知识节点”对应表（KGE-01 之前为空）。
// 格式 3（specs/release-gate-sprint4 设计要点第 7 节）另加：逐题来源与准入依据（按平台导出的准入决定文件重建）、
// 提示原文的 C9 词表检查。收尾与 rebuild-content-pack.ts 共用 scripts/pack-cli.ts（参数见那里：--decisions、--c9、--report、--out）。
// 题干、解析、前置关系（prerequisites）仍不进内容包。答案只在适配器进程内使用，任何接口都不返回（decisions #4）。
// 同一 legacy 提交、同一决定文件、同一词表：两次生成逐字节相同（规范化序列化，不读时钟）。
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { PACK_FORMAT, type ContentPack, type PackItem, type SkillLinks } from "../src/content.ts";
import { DEFAULT_SOURCE, packVersionFor } from "../src/pack/admission.ts";
import { finishPack, packCliOptions, runCli } from "./pack-cli.ts";

const HERE = new URL(".", import.meta.url).pathname;
const ROOT = process.env.AYA_LEGACY_ROOT ?? resolve(HERE, "../../../legacy/aya");
process.env.AYA_LEGACY_ROOT = ROOT;

const opts = packCliOptions();
const git = (...args: string[]) => execFileSync("git", ["-C", ROOT, ...args], { encoding: "utf8" }).trim();
const commit = git("rev-parse", "HEAD");
if (git("status", "--porcelain") !== "") {
  console.error(`build-content-pack: legacy 工作区 ${ROOT} 有未提交改动，拒绝生成（内容包必须能追溯到提交号）`);
  process.exit(1);
}

type LegacyQuestion = PackItem & Record<string, unknown>;
const topics = await import(pathToFileURL(`${ROOT}/content/topics.ts`).href);
const abilities = await import(pathToFileURL(`${ROOT}/content/abilities.ts`).href);
const examweights = await import(pathToFileURL(`${ROOT}/content/examweights.ts`).href);
const questions = [...(topics.allQuestionById as Map<string, LegacyQuestion>).values()];
const nodes = [...(topics.allNodeById as Map<string, { id: string; kind: string; cluster: string }>).values()];
const boardOfNode = topics.boardOfNode as Map<string, string>;

// 阿鸦节点 → KGE 知识节点：平台导出的 kge.skill_link（带地图版本）。KGE-01 导入阿鸦图谱之前没有，保持为空（spec 待人确认 #2）。
// 有导出文件时用 AYA_SKILL_LINKS=/path/to/export.json 指定，格式同 SkillLinks。
const skillLinks: SkillLinks = process.env.AYA_SKILL_LINKS
  ? (JSON.parse((await import("node:fs")).readFileSync(process.env.AYA_SKILL_LINKS, "utf8")) as SkillLinks)
  : { map_ref: null, links: [] };

const pack: ContentPack = {
  format: PACK_FORMAT,
  pack_name: "aya-pack",
  // 格式 3 起 0.3.0（题目与答案未变，六段标识的 content_version 不变）；套用准入决定时由 finishPack 加 .d<摘要>
  pack_version: packVersionFor(commit, null),
  legacy_commit: commit,
  // 六段标识。textbook_version、grade 待人确认（specs/adapter-aya-sprint1/lessons.md）
  curriculum: {
    region: "nanjing",
    exam_year: 2027,
    grade: 9,
    textbook_version: "suke-2024",
    subject: "aya",
    content_version: "0.1.0",
  },
  // 来源待核（decisions #3）：legacy 里没有来源信息，逐题来源只来自平台导出的准入决定文件
  admission_decisions: null,
  nodes: nodes
    .map((n) => ({ id: n.id, kind: n.kind, cluster: n.cluster, board: boardOfNode.get(n.id) ?? "综合实践" }))
    .sort((a, b) => a.id.localeCompare(b.id)),
  items: questions
    .map((q): PackItem => {
      const item: PackItem = {
        id: q.id,
        format: q.format,
        answer: q.answer,
        mainNodes: q.mainNodes,
        reviewStatus: q.reviewStatus,
        admission: "source_unverified",
        source: { ...DEFAULT_SOURCE },
        usage: q.usage as PackItem["usage"],
        estMinutes: q.estMinutes as number,
        hintPath: [...(q.hintPath as string[])],
      };
      if (q.options) item.options = q.options.map((o) => (o.traps ? { key: o.key, traps: o.traps } : { key: o.key }));
      if (q.wrongAnswerTraps) item.wrongAnswerTraps = q.wrongAnswerTraps;
      return item;
    })
    .sort((a, b) => a.id.localeCompare(b.id)),
  traps: (abilities.ERROR_TRAPS as { code: string; rootCause: string }[])
    .map((t) => ({ code: t.code, rootCause: t.rootCause }) as ContentPack["traps"][number])
    .sort((a, b) => a.code.localeCompare(b.code)),
  board_exam_share: { status: "q1_derived_uncalibrated", shares: { ...(examweights.BOARD_EXAM_SHARE as Record<string, number>) } },
  skill_links: skillLinks,
  // 占位：finishPack 做完 C9 后写入
  hint_scan: { check: "C9", persona: "aya", lexicon_version: "", guard_version: "", mode: "strict", texts: 0, flagged_items: [] },
};

runCli(() => {
  console.log(`build-content-pack: ${pack.items.length} 道题、${pack.nodes.length} 个节点、${pack.traps.length} 个陷阱码（legacy ${commit.slice(0, 7)}）`);
  finishPack(pack, opts, "build-content-pack");
}, "build-content-pack");
