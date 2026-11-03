// 数学 · 阿鸦：知识节点、前置边、题库、陷阱码与根因、100 能力点（spec 设计要点 3 通用项 + 数学项 6–8）。
import { findCycles, findOrphans, findTransitiveEdges, type Edge } from "../graph.ts";
import { importLegacy, readText } from "../legacy.ts";
import {
  finding,
  pct,
  samples,
  type Answer,
  type BlockerCheck,
  type Finding,
  type Group,
  type SubjectReport,
} from "../model.ts";

interface Node {
  id: string;
  name: string;
  kind: string;
  cluster?: string;
  core?: string;
  prerequisites?: string[];
  abilities?: string[];
  evidence?: string;
  [k: string]: unknown;
}
interface Question {
  id: string;
  usage?: string;
  format?: string;
  board?: string;
  mainNodes?: string[];
  auxNodes?: string[];
  preNodes?: string[];
  abilities?: Record<string, number>;
  options?: { traps?: string[] }[];
  wrongAnswerTraps?: { traps?: string[] }[];
  difficulty?: Record<string, number>;
  reviewStatus?: string;
  [k: string]: unknown;
}
interface Topic {
  id: string;
  name: string;
  chapterLabel: string;
  board: string;
  nodes: Node[];
  questions: Question[];
}

/** KGE `knowledge_node` 对照口径（KGE V1.1 第八章）：legacy 节点上是否有同名或同义字段 */
const KGE_NODE_FIELDS: { kge: string; legacy: string[]; derivable?: string }[] = [
  { kge: "includes（包含什么）", legacy: ["includes"], derivable: "core（核心内容）可作参考，不是边界说明" },
  { kge: "excludes（不包含什么）", legacy: ["excludes"] },
  { kge: "examples（典型题例）", legacy: ["examples"], derivable: "可由“以该节点为主考点的题目”推出" },
  { kge: "scope（范围属性）", legacy: ["scope"] },
  { kge: "cognitive_levels（认知层次）", legacy: ["cognitive_levels", "cognitiveLevels", "level", "levels"] },
  { kge: "error_type（易错节点的错因类型）", legacy: ["error_type", "errorType"], derivable: "易错信息只在题目选项的陷阱码里，可反查" },
  { kge: "map_version（地图版本）", legacy: ["map_version", "mapVersion", "version"] },
];

/** 内容资产六段标识（KGE V1.1 第八章 item） */
export const SIX_SEGMENTS = ["region", "exam_year", "grade", "textbook_version", "subject", "content_version"] as const;

const ID_LIKE = /^[A-Z]{1,2}-[A-Z0-9]+-\d{2,}$/;
const GRADE_OF_LABEL = /(七|八|九)(上|下)/;

export interface SubjectResult {
  report: SubjectReport;
  answers: Answer[];
  blockers: BlockerCheck[];
}

function parseStringUnion(text: string | null, typeName: string): string[] | null {
  if (!text) return null;
  const m = new RegExp(`export type ${typeName}\\s*=([^;]+);`).exec(text);
  if (!m) return null;
  return [...m[1]!.matchAll(/"([^"]+)"/g)].map((x) => x[1]!).sort();
}

function parseCheckIn(sql: string | null, column: string): string[] | null {
  if (!sql) return null;
  const m = new RegExp(`${column}\\s+in\\s*\\(([^)]*)\\)`, "i").exec(sql);
  if (!m) return null;
  return [...m[1]!.matchAll(/'([^']+)'/g)].map((x) => x[1]!).sort();
}

export async function inventoryMath(root: string): Promise<SubjectResult> {
  const errors: string[] = [];
  const sources = ["content/topics.ts", "content/sprint/index.ts", "content/abilities.ts", "lib/types.ts", "db/schema.sql"];
  const topicsMod = await importLegacy<{ TOPICS: Topic[] }>(root, "content/topics.ts", errors);
  const abilitiesMod = await importLegacy<{
    ABILITY_CODES: { code: string; dim: string }[];
    ERROR_TRAPS: { code: string; rootCause: string }[];
  }>(root, "content/abilities.ts", errors);
  const typesMod = await importLegacy<{ BOARDS: string[]; ABILITY_DIMS: string[]; ROOT_CAUSES: string[] }>(
    root,
    "lib/types.ts",
    errors,
  );
  const sprintMod = await importLegacy<{ SPRINT_QUESTIONS: Question[] }>(root, "content/sprint/index.ts", errors);
  const typesText = readText(root, "lib/types.ts");
  const schemaSql = readText(root, "db/schema.sql");

  const groups: Group[] = [];
  const answers: Answer[] = [];
  const blockers: BlockerCheck[] = [];
  const topics = topicsMod?.TOPICS ?? [];

  // ---------- 节点 ----------
  const rawNodes = topics.flatMap((t) => t.nodes.map((n) => ({ n, t })));
  const nodeById = new Map<string, Node>();
  const topicOfNode = new Map<string, Topic>();
  const dupIds: string[] = [];
  for (const { n, t } of rawNodes) {
    if (nodeById.has(n.id)) dupIds.push(n.id);
    else {
      nodeById.set(n.id, n);
      topicOfNode.set(n.id, t);
    }
  }
  const nodeIds = [...nodeById.keys()].sort();
  const byKind = new Map<string, number>();
  for (const n of nodeById.values()) byKind.set(n.kind, (byKind.get(n.kind) ?? 0) + 1);
  const kindsFound = [...byKind.keys()].sort();
  const kindsInType = parseStringUnion(typesText, "NodeKind");
  const kindsInDb = parseCheckIn(schemaSql, "kind");

  const nodeFindings: Finding[] = [
    finding("math.topics", "专题（章）数", topics.length),
    finding("math.nodes.raw", "节点定义数（未去重）", rawNodes.length),
    finding("math.nodes", "节点数（按 ID 去重）", nodeById.size),
    finding("math.nodes.duplicate_ids", "重复定义的节点 ID", dupIds.length, { samples: samples(dupIds) }),
    finding(
      "math.nodes.by_kind",
      "按类型计数",
      kindsFound.map((k) => `${k} ${byKind.get(k)}`).join("，"),
    ),
  ];
  groups.push({ title: "节点", findings: nodeFindings });

  // 字段覆盖：KGE 口径 + legacy 自有字段非空率
  const nodes = [...nodeById.values()];
  const fieldFindings: Finding[] = KGE_NODE_FIELDS.map((f) => {
    const has = nodes.filter((n) => f.legacy.some((k) => n[k] !== undefined && n[k] !== "" && n[k] !== null)).length;
    const status = has === 0 ? (f.derivable ? "无，可推导" : "无") : has === nodes.length ? "有" : "部分";
    return finding(`math.kge_field.${f.legacy[0]}`, `KGE 字段 ${f.kge}`, `${status}（${has}/${nodes.length}）`, {
      note: f.derivable,
    });
  });
  for (const k of ["name", "cluster", "core", "prerequisites", "abilities", "evidence"]) {
    const empty = nodes.filter((n) => {
      const v = n[k];
      return v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);
    });
    fieldFindings.push(
      finding(`math.field_empty.${k}`, `legacy 字段 ${k} 为空的节点`, `${empty.length}（${pct(empty.length, nodes.length)}）`, {
        samples: samples(empty.map((n) => n.id)),
      }),
    );
  }
  groups.push({ title: "节点字段（对照 KGE knowledge_node）", findings: fieldFindings });

  // ---------- 前置边 ----------
  const idEdges: Edge[] = [];
  const textPrereqs: string[] = [];
  const dangling: string[] = [];
  let prereqTotal = 0;
  for (const n of nodes) {
    for (const p of n.prerequisites ?? []) {
      prereqTotal++;
      if (nodeById.has(p)) idEdges.push([p, n.id]);
      else if (ID_LIKE.test(p)) dangling.push(`${n.id} ← ${p}`);
      else textPrereqs.push(p);
    }
  }
  const cycles = findCycles(nodeIds, idEdges);
  const transitive = findTransitiveEdges(nodeIds, idEdges);

  // ---------- 题目 ----------
  const topicQuestions = topics.flatMap((t) => t.questions.map((q) => ({ q, t })));
  const sprintQuestions = (sprintMod?.SPRINT_QUESTIONS ?? []).map((q) => ({
    q,
    t: null as Topic | null,
  }));
  const allQ = [...topicQuestions, ...sprintQuestions];
  const qById = new Map<string, { q: Question; t: Topic | null }>();
  const dupQ: string[] = [];
  for (const x of allQ) {
    if (qById.has(x.q.id)) dupQ.push(x.q.id);
    else qById.set(x.q.id, x);
  }
  const questions = [...qById.values()];
  const referenced = new Set<string>();
  const mainCount = new Map<string, number>();
  const unresolvedRefs: string[] = [];
  let refTotal = 0;
  for (const { q } of questions) {
    for (const key of ["mainNodes", "auxNodes", "preNodes"] as const) {
      for (const id of q[key] ?? []) {
        refTotal++;
        if (nodeById.has(id)) referenced.add(id);
        else unresolvedRefs.push(`${q.id} → ${id}`);
        if (key === "mainNodes" && nodeById.has(id)) mainCount.set(id, (mainCount.get(id) ?? 0) + 1);
      }
    }
  }
  const orphans = findOrphans(nodeIds, idEdges, referenced);
  const lt3 = nodeIds.filter((id) => (mainCount.get(id) ?? 0) < 3);
  const zeroMain = nodeIds.filter((id) => (mainCount.get(id) ?? 0) === 0);

  groups.push({
    title: "前置边与图结构",
    findings: [
      finding("math.prereq.total", "前置条目总数", prereqTotal),
      finding("math.prereq.id_edges", "可解析为节点 ID 的前置边", idEdges.length),
      finding("math.prereq.text", "自然语言前置（不能自动连边）", textPrereqs.length, {
        samples: samples(textPrereqs),
        note: "按默认只列出，不做候选映射（spec 待人确认 #6）",
      }),
      finding("math.prereq.dangling", "形似 ID 但不存在的前置（悬空边）", dangling.length, { samples: samples(dangling) }),
      finding("math.prereq.strength", "前置是否区分强弱", "否（字段只有 ID 或文字列表）"),
      finding("math.graph.cycles", "环（强连通分量，样例列出环内节点）", cycles.length, {
        samples: samples(cycles.map((c) => c.join("、"))),
      }),
      finding("math.graph.transitive", "传递边（可由其他路径推出的直连前置）", transitive.length, {
        samples: samples(transitive.map(([a, b]) => `${a} → ${b}`)),
      }),
      finding("math.graph.orphans", "孤点（无可解析边、也没有题目引用）", orphans.length, { samples: samples(orphans) }),
    ],
  });

  // 六段标识
  const segFindings: Finding[] = SIX_SEGMENTS.map((s) => {
    const has = questions.filter(({ q }) => q[s] !== undefined && q[s] !== null && q[s] !== "").length;
    return finding(`math.item.six.${s}`, `题目字段 ${s}`, `${has}/${questions.length}`);
  });
  const gradeDerivable = questions.filter(({ t }) => t && GRADE_OF_LABEL.test(t.chapterLabel)).length;
  segFindings.push(
    finding(
      "math.item.six.derivable",
      "可从所属专题的章节标签推出年级与教材版本的题目",
      `${gradeDerivable}/${questions.length}`,
      { note: "章节标签形如“苏科版九上 · 第1章”；压轴专项题不属于任何专题" },
    ),
  );
  for (const k of ["license", "copyright", "source", "sourceKind"]) {
    const has = questions.filter(({ q }) => q[k] !== undefined).length;
    segFindings.push(finding(`math.item.field.${k}`, `题目字段 ${k}（版权 / 来源）`, `${has}/${questions.length}`));
  }
  const byReview = new Map<string, number>();
  for (const { q } of questions) byReview.set(q.reviewStatus ?? "（无）", (byReview.get(q.reviewStatus ?? "（无）") ?? 0) + 1);
  segFindings.push(
    finding(
      "math.item.review_status",
      "审核状态分布",
      [...byReview.keys()].sort().map((k) => `${k} ${byReview.get(k)}`).join("，"),
    ),
  );

  const countBy = (f: (q: Question) => string | undefined) => {
    const m = new Map<string, number>();
    for (const { q } of questions) {
      const k = f(q) ?? "（无）";
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return [...m.keys()].sort().map((k) => `${k} ${m.get(k)}`).join("，");
  };
  const withDifficulty = questions.filter(({ q }) => q.difficulty && Object.keys(q.difficulty).length > 0).length;
  groups.push({
    title: "题库",
    findings: [
      finding("math.items", "题目数（按 ID 去重）", questions.length),
      finding("math.items.topic", "其中属于专题的题目", topicQuestions.length),
      finding("math.items.sprint", "其中压轴专项题（不属于任何专题）", sprintQuestions.length),
      finding("math.items.duplicate_ids", "重复定义的题目 ID", dupQ.length, { samples: samples(dupQ) }),
      finding("math.items.by_usage", "按用途计数（D 诊断 / P 探针 / L 训练 / T 迁移 / R 复测）", countBy((q) => q.usage)),
      finding("math.items.by_format", "按作答形式计数", countBy((q) => q.format)),
      finding("math.items.node_refs", "题目引用节点次数（主 / 辅 / 前置）", refTotal),
      finding("math.items.node_refs_unresolved", "引用了不存在节点的次数", unresolvedRefs.length, {
        samples: samples(unresolvedRefs),
      }),
      finding("math.items.difficulty", "带八维难度向量的题目", `${withDifficulty}/${questions.length}`, {
        note: "KGE 建议改为各因子分级后加权求和作为先验难度",
      }),
      finding("math.nodes.main_zero", "没有任何题目以它为主考点的节点", `${zeroMain.length}（${pct(zeroMain.length, nodeIds.length)}）`),
      finding(
        "math.nodes.main_lt3",
        "以它为主考点的题目少于 3 道的节点（粒度“可独立诊断”初筛信号）",
        `${lt3.length}（${pct(lt3.length, nodeIds.length)}）`,
        { note: "只作信号，不判断粒度是否合格（spec 待人确认 #2）" },
      ),
    ],
  });
  groups.push({ title: "六段标识、版权与审核状态", findings: segFindings });

  // 九年级“函数”主题（封测切片起点）
  const g9fn = topics.filter((t) => t.board === "函数" && /九(上|下)/.test(t.chapterLabel));
  const g9Nodes = g9fn.flatMap((t) => t.nodes.map((n) => n.id));
  const g9Lt3 = g9Nodes.filter((id) => (mainCount.get(id) ?? 0) < 3);
  const g9Items = g9fn.reduce((a, t) => a + t.questions.length, 0);
  const allFn = topics.filter((t) => t.board === "函数");
  groups.push({
    title: "九年级“函数”主题（封测切片起点）",
    findings: [
      finding("math.g9fn.topics", "九年级且属于“函数”板块的专题", g9fn.map((t) => `${t.id} ${t.name}（${t.chapterLabel}）`).join("；") || "无"),
      finding("math.g9fn.nodes", "节点数", g9Nodes.length),
      finding("math.g9fn.items", "题目数", g9Items),
      finding("math.g9fn.main_lt3", "主考点题目少于 3 道的节点", `${g9Lt3.length}/${g9Nodes.length}`, { samples: samples(g9Lt3) }),
      finding(
        "math.fn_all.topics",
        "“函数”板块全部专题（七至九年级）",
        allFn.map((t) => `${t.id} ${t.name}（${t.chapterLabel}）`).join("；"),
      ),
    ],
  });

  // ---------- 陷阱码与根因 ----------
  const traps = abilitiesMod?.ERROR_TRAPS ?? [];
  const rootCauses = new Set(typesMod?.ROOT_CAUSES ?? []);
  const badRoot = traps.filter((t) => !rootCauses.has(t.rootCause)).map((t) => t.code);
  const trapCodes = new Set(traps.map((t) => t.code));
  const usedTraps = new Set<string>();
  const unknownTraps: string[] = [];
  for (const { q } of questions) {
    const refs = [...(q.options ?? []).flatMap((o) => o.traps ?? []), ...(q.wrongAnswerTraps ?? []).flatMap((w) => w.traps ?? [])];
    for (const c of refs) {
      if (trapCodes.has(c)) usedTraps.add(c);
      else unknownTraps.push(`${q.id} → ${c}`);
    }
  }
  groups.push({
    title: "错因：十二类根因与陷阱码",
    findings: [
      finding("math.root_causes", "根因类数", rootCauses.size),
      finding("math.traps", "陷阱码数", traps.length),
      finding("math.traps.bad_root", "根因不在十二类里的陷阱码", badRoot.length, { samples: samples(badRoot) }),
      finding("math.traps.used", "被题目引用的陷阱码", `${usedTraps.size}/${traps.length}（${pct(usedTraps.size, traps.length)}）`),
      finding("math.traps.unknown_refs", "题目引用了不存在的陷阱码", unknownTraps.length, { samples: samples(unknownTraps) }),
      finding("math.pitfall_nodes", "易错节点（KGE pitfall 类型）", 0, {
        note: "legacy 节点类型里没有易错节点；易错信息只在陷阱码上",
      }),
    ],
  });

  // ---------- 100 能力点 ----------
  const boards = typesMod?.BOARDS ?? [];
  const dims = typesMod?.ABILITY_DIMS ?? [];
  const dimOfCode = new Map((abilitiesMod?.ABILITY_CODES ?? []).map((a) => [a.code, a.dim]));
  const cellNodes = new Map<string, number>();
  const cellItems = new Map<string, number>();
  const unknownAbility: string[] = [];
  for (const n of nodes) {
    const board = topicOfNode.get(n.id)?.board;
    for (const code of n.abilities ?? []) {
      const dim = dimOfCode.get(code);
      if (!dim) {
        unknownAbility.push(`${n.id} → ${code}`);
        continue;
      }
      const k = `${board}×${dim}`;
      cellNodes.set(k, (cellNodes.get(k) ?? 0) + 1);
    }
  }
  for (const { q } of questions) {
    for (const [code, w] of Object.entries(q.abilities ?? {})) {
      const dim = dimOfCode.get(code);
      if (!dim || !(w > 0)) continue;
      const k = `${q.board}×${dim}`;
      cellItems.set(k, (cellItems.get(k) ?? 0) + 1);
    }
  }
  const cells = boards.flatMap((b) => dims.map((d) => `${b}×${d}`));
  const emptyCells = cells.filter((c) => !cellNodes.has(c) && !cellItems.has(c));
  groups.push({
    title: "100 能力点（能力视图 skill_node 的来源）",
    findings: [
      finding("math.grid.boards", "板块数 × 能力维度数", `${boards.length} × ${dims.length} = ${cells.length}`),
      finding("math.grid.ability_codes", "章节能力代码数（映射到能力维度）", dimOfCode.size),
      finding("math.grid.standalone_data", "是否有独立的 100 点数据", "否（由两个常量相乘，节点与题目经能力代码映射）"),
      finding("math.grid.cells_with_nodes", "有节点挂靠的格子", `${cells.filter((c) => cellNodes.has(c)).length}/${cells.length}`),
      finding("math.grid.cells_with_items", "有题目挂靠的格子", `${cells.filter((c) => cellItems.has(c)).length}/${cells.length}`),
      finding("math.grid.empty", "既无节点也无题目的格子", emptyCells.length, { samples: samples(emptyCells) }),
      finding("math.grid.unknown_codes", "节点上不存在的能力代码", unknownAbility.length, { samples: samples(unknownAbility) }),
    ],
  });

  // ---------- 旧编码并存 ----------
  const idLike = nodeIds.filter((id) => ID_LIKE.test(id)).length;
  const kgeStyle = nodeIds.filter((id) => /^MATH\./.test(id)).length;
  const abilityCollide = nodeIds.filter((id) => dimOfCode.has(id));
  const kindNotInType = kindsInType ? kindsFound.filter((k) => !kindsInType.includes(k)) : [];
  const kindNotInDb = kindsInDb ? kindsFound.filter((k) => !kindsInDb.includes(k)) : [];
  groups.push({
    title: "旧编码并存",
    findings: [
      finding("math.codes.node_id_style", "节点 ID 形如“类型-簇-编号”（如 K-FUN-0901）", `${idLike}/${nodeIds.length}`),
      finding("math.codes.kge_style", "已是 KGE 命名“学科.领域.主题.节点”的节点", `${kgeStyle}/${nodeIds.length}`, {
        note: "全部要进 id_migration 迁移表",
      }),
      finding("math.codes.kind_vs_type", "实际节点类型不在 lib/types.ts NodeKind 里", kindNotInType.join("、") || "无", {
        note: kindsInType ? `NodeKind = ${kindsInType.join(" | ")}` : "未找到 NodeKind 定义",
      }),
      finding("math.codes.kind_vs_db", "实际节点类型不在 db/schema.sql 的 kind 约束里", kindNotInDb.join("、") || "无", {
        note: kindsInDb ? `约束 = ${kindsInDb.join(", ")}（该表代码未使用）` : "未找到约束",
      }),
      finding("math.codes.node_vs_ability", "与能力代码同名的节点 ID", abilityCollide.length, { samples: samples(abilityCollide) }),
      finding("math.codes.levels", "“能力五层级”定义", "未找到；最接近的是掌握五门槛 MASTERY_GATES", {
        note: "KGE：题目层用课标四级，五道门槛保留为掌握状态",
      }),
    ],
  });

  // ---------- 盘点项 6 的回答 ----------
  answers.push(
    {
      question: "counts",
      subject: "math",
      answer: `节点 ${nodeById.size}（${kindsFound.map((k) => `${k} ${byKind.get(k)}`).join(" / ")}）；题目 ${questions.length}；Rubric：无独立 Rubric，判分靠答案与 ${traps.length} 个陷阱码`,
    },
    {
      question: "missing_fields",
      subject: "math",
      answer: `KGE 节点字段 includes / excludes / scope / cognitive_levels / map_version 全部缺失；题目六段标识 ${SIX_SEGMENTS.map((s) => `${s} ${questions.filter(({ q }) => q[s] !== undefined).length}/${questions.length}`).join("、")}；前置不分强弱`,
    },
    {
      question: "orphans_cycles",
      subject: "math",
      answer: `环 ${cycles.length} 个；孤点 ${orphans.length} 个；传递边 ${transitive.length} 条；另有自然语言前置 ${textPrereqs.length} 条、悬空边 ${dangling.length} 条无法参与计算；${zeroMain.length} 个节点没有任何以它为主考点的题目`,
    },
    {
      question: "legacy_codes",
      subject: "math",
      answer: `没有节点使用 KGE 命名（${kgeStyle}/${nodeIds.length}），全部要进迁移表；其中 ${idLike} 个形如 K-FUN-0901，其余 ${nodeIds.length - idLike} 个另有格式；题目里有 ${unresolvedRefs.length} 处用自然语言而不是节点 ID 引用节点；节点类型 ${kindNotInType.join("、") || "无"} 不在 NodeKind 定义中、${kindNotInDb.join("、") || "无"} 不在未使用的 knowledge_nodes 表约束中；没有“能力五层级”数据`,
    },
  );

  blockers.push({
    id: "math-node-list",
    subject: "math",
    problem: "知识图谱节点清单的实际完成度未知，不能以 100 能力点代替诊断单元",
    verdict: `节点清单是实物：${topics.length} 个专题、${nodeById.size} 个节点、${idEdges.length} 条可解析前置边，覆盖七至九年级苏科版；但前置边有 ${textPrereqs.length} 条自然语言、${transitive.length} 条传递边，且节点缺少包含 / 不包含 / 范围 / 认知层次字段。全部节点中 ${zeroMain.length} 个没有以它为主考点的题目；九年级“函数”主题 ${g9Nodes.length} 个节点中 ${g9Lt3.length} 个主考点题目少于 3 道。可作为 KGE-01 导入的起点，拆解、必要性检验与试标注仍需教研完成`,
    evidence: ["math.nodes", "math.prereq.text", "math.graph.transitive", "math.nodes.main_zero", "math.g9fn.main_lt3"],
  });

  return {
    report: { subject: "math", sources, groups, loadErrors: errors },
    answers,
    blockers,
  };
}
