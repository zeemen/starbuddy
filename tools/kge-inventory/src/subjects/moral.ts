// 道法 · 知行：大概念与页码、采分点 Rubric、时政、《学生读本》（spec 设计要点 3 通用项 + 道法项 13–16）。
// 题库里的 student_samples（演示样卷）不读取、不统计，避免任何作答文本进入报告。
import { findOrphans } from "../graph.ts";
import { extractBlock, importLegacy, listTextFiles, readText } from "../legacy.ts";
import { finding, pct, samples, type Answer, type BlockerCheck, type Group } from "../model.ts";
import { SHARED, wenliAppDir } from "./chinese.ts";
import type { SubjectResult } from "./math.ts";

const DOMAIN = `${SHARED}/domain`;
const NEWS_PAGE = "apps/daofa/app/news/page.tsx";

interface MoralNode {
  id: string;
  name: string;
  book?: string;
  pages?: string;
  keywords?: string[];
  phrases?: string[];
  source?: string;
  needs_review?: boolean;
  [k: string]: unknown;
}
interface RubricPoint {
  [k: string]: unknown;
}
interface MoralQuestion {
  id: string;
  knowledge_nodes?: string[];
  rubrics?: RubricPoint[];
  [k: string]: unknown;
}

const RUBRIC_FIELDS = [
  "point_id",
  "score_value",
  "subject_dimension",
  "core_keywords",
  "required_logic",
  "material_linkage",
  "partial_credit_rule",
  "common_errors",
] as const;
const EDITION_FIELDS = ["edition", "edition_year", "printing", "版次", "印次"];
const MOUNT_FIELDS: [string, string][] = [
  ["高频设问", "questions"],
  ["答题话术", "answer_phrases"],
  ["真题回链", "exam_refs"],
];
const PAGE_RANGE = /^\d+(\s*[-–~]\s*\d+)?$/;

export async function inventoryMoral(root: string): Promise<SubjectResult> {
  const app = wenliAppDir(root);
  const errors: string[] = [];
  const groups: Group[] = [];
  const answers: Answer[] = [];
  const blockers: BlockerCheck[] = [];
  const sources = [`${DOMAIN}/daofa-knowledge-map.ts`, `${DOMAIN}/daofa-question-bank.ts`, NEWS_PAGE];

  const mapMod = await importLegacy<{ DAOFA_KNOWLEDGE_MAP: { key: string; title: string; nodes: MoralNode[] }[] }>(
    app,
    `${DOMAIN}/daofa-knowledge-map.ts`,
    errors,
  );
  const qMod = await importLegacy<{ DAOFA_QUESTIONS: MoralQuestion[] }>(app, `${DOMAIN}/daofa-question-bank.ts`, errors);
  const domains = mapMod?.DAOFA_KNOWLEDGE_MAP ?? [];
  const nodes = domains.flatMap((d) => d.nodes);
  const isReader = (n: MoralNode) => n.source === "读本" || n.book === "读本";
  const textbook = nodes.filter((n) => !isReader(n));
  const reader = nodes.filter(isReader);

  // ---------- 大概念与页码 ----------
  const withPages = textbook.filter((n) => typeof n.pages === "string" && n.pages.trim() !== "");
  const parsable = textbook.filter((n) => typeof n.pages === "string" && PAGE_RANGE.test(n.pages.trim()));
  const pending = nodes.filter((n) => typeof n.pages === "string" && /待核/.test(n.pages));
  const needsReview = nodes.filter((n) => n.needs_review === true);
  const bound = nodes.filter((n) => EDITION_FIELDS.some((k) => n[k] !== undefined));
  const withBook = textbook.filter((n) => typeof n.book === "string" && n.book !== "");
  const emptyPhrases = nodes.filter((n) => (n.phrases ?? []).length === 0);
  const mapText = readText(app, `${DOMAIN}/daofa-knowledge-map.ts`) ?? "";
  const approxNoted = /近似/.test(mapText);
  groups.push({
    title: "大概念节点与页码",
    findings: [
      finding("moral.domains", "领域数（含读本）", domains.length, { note: domains.map((d) => `${d.title} ${d.nodes.length}`).join("；") }),
      finding("moral.nodes", "节点数", nodes.length),
      finding("moral.nodes.textbook", "其中统编教材节点", textbook.length),
      finding("moral.nodes.reader", "其中《学生读本》节点", reader.length),
      finding("moral.pages.present", "统编节点有页码", `${withPages.length}/${textbook.length}`),
      finding("moral.pages.parsable", "页码能解析为页码区间", `${parsable.length}/${textbook.length}`),
      finding("moral.pages.approx", "源文件注明页码是近似值", approxNoted ? "是" : "否"),
      finding("moral.pages.pending", "页码为“待核”的节点", pending.length, { samples: samples(pending.map((n) => n.id)) }),
      finding("moral.pages.book", "统编节点标了册别（七上…九下）", `${withBook.length}/${textbook.length}`),
      finding("moral.pages.edition_bound", "绑定教材版次 / 印次的节点", `${bound.length}/${nodes.length}`, {
        note: "KGE：页码与原句一律进教材投影表 textbook_projection，按版次核对",
      }),
      finding("moral.needs_review", "标记 needs_review（待特级教师审校）的节点", needsReview.length, {
        samples: samples(needsReview.map((n) => n.id)),
      }),
      finding("moral.phrases.empty", "教材规范表述为空的节点", emptyPhrases.length, { samples: samples(emptyPhrases.map((n) => n.id)) }),
      ...MOUNT_FIELDS.map(([label, key]) =>
        finding(`moral.mount.${key}`, `节点挂载：${label}`, nodes.some((n) => n[key] !== undefined) ? "有" : "无"),
      ),
      finding("moral.edges", "节点间关系（前置 / 关联）", nodes.some((n) => n.prerequisites || n.related) ? "有" : "无"),
    ],
  });

  // ---------- 采分点 Rubric ----------
  const questions = qMod?.DAOFA_QUESTIONS ?? [];
  const points = questions.flatMap((q) => q.rubrics ?? []);
  const nodeIds = new Set(nodes.map((n) => n.id));
  const refs = questions.flatMap((q) => q.knowledge_nodes ?? []);
  const badRefs = questions.flatMap((q) => (q.knowledge_nodes ?? []).filter((id) => !nodeIds.has(id)).map((id) => `${q.id} → ${id}`));
  const orphans = findOrphans([...nodeIds], [], new Set(refs));
  const versioned = questions.filter((q) => q.rubric_version !== undefined || (q.rubrics ?? []).some((p) => p.rubric_version !== undefined));
  groups.push({
    title: "采分点 Rubric",
    findings: [
      finding("moral.items", "带 Rubric 的静态题目数", questions.length),
      finding("moral.points", "采分点数", points.length),
      ...RUBRIC_FIELDS.map((k) => {
        const has = points.filter((p) => p[k] !== undefined && p[k] !== null && p[k] !== "").length;
        return finding(`moral.points.${k}`, `采分点字段 ${k}`, `${has}/${points.length}`);
      }),
      finding("moral.rubric_version", "带 rubric_version 的题目", `${versioned.length}/${questions.length}`),
      finding("moral.items.node_refs_unresolved", "题目引用了不存在的节点", badRefs.length, { samples: samples(badRefs) }),
      finding("moral.nodes.unreferenced", "没有任何静态题目引用的节点", `${orphans.length}/${nodeIds.size}（${pct(orphans.length, nodeIds.size)}）`, {
        samples: samples(orphans),
        note: "节点间没有边，这里的“孤点”只看题目引用；AI 生成题在库表 daofa_item，见库模式",
      }),
    ],
  });

  // ---------- 时政 ----------
  const newsText = readText(app, NEWS_PAGE);
  const newsBlock = newsText ? extractBlock(newsText, /const\s+SEED_NEWS\s*=/) : null;
  const newsCode = newsBlock?.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "") ?? null;
  const newsCount = newsCode ? [...newsCode.matchAll(/\btitle\s*:/g)].length : 0;
  const newsValidity = newsCode ? /valid_from|valid_until|validFrom|validUntil|生效|失效|expire/i.test(newsCode) : false;
  groups.push({
    title: "时政条目",
    findings: [
      finding("moral.news", "时政种子条目数（news 页常量 SEED_NEWS）", newsBlock === null ? "未找到" : newsCount),
      finding("moral.news.validity", "时政条目有生效 / 失效字段", newsValidity ? "有" : "无"),
      finding("moral.news.stored", "时政条目是否入库", "否（写在页面常量里）"),
    ],
  });

  // ---------- 《学生读本》 ----------
  const readerFiles = listTextFiles(app, ["."], /读本/);
  const readerPages = reader.filter((n) => typeof n.pages === "string" && PAGE_RANGE.test(n.pages.trim()));
  const readerPhrases = reader.filter((n) => (n.phrases ?? []).length > 0);
  const readerVerdict =
    reader.length === 0 ? "未入库" : readerPages.length === 0 && readerPhrases.length === 0 ? "未入库（只有脚手架节点，没有原文、页码与规范表述）" : "部分入库";
  groups.push({
    title: "《学生读本》",
    findings: [
      finding("moral.reader.nodes", "读本节点数", reader.length, { samples: samples(reader.map((n) => `${n.id} ${n.name}`)) }),
      finding("moral.reader.pages", "读本节点有可用页码", `${readerPages.length}/${reader.length}`),
      finding("moral.reader.phrases", "读本节点有规范表述", `${readerPhrases.length}/${reader.length}`),
      finding("moral.reader.files", "文件名含“读本”的文件（任意类型）", readerFiles.length, { samples: samples(readerFiles) }),
      finding("moral.reader.verdict", "结论", readerVerdict),
    ],
  });

  // ---------- 回答与阻断 ----------
  answers.push(
    {
      question: "counts",
      subject: "moral",
      answer: `节点 ${nodes.length}（统编 ${textbook.length}，读本 ${reader.length}，${domains.length} 个领域）；静态题目 ${questions.length}；采分点 ${points.length}；AI 生成题与其 Rubric 在库表 daofa_item，见库模式`,
    },
    {
      question: "missing_fields",
      subject: "moral",
      answer: `没有版次 / 印次（${bound.length}/${nodes.length}）；没有高频设问、答题话术、真题回链挂载；Rubric 没有 rubric_version（${versioned.length}/${questions.length}）；时政没有生效 / 失效日期；${emptyPhrases.length} 个节点的规范表述为空`,
    },
    {
      question: "orphans_cycles",
      subject: "moral",
      answer: `节点间没有任何边，不存在环；没有静态题目引用的节点 ${orphans.length}/${nodeIds.size}`,
    },
    {
      question: "legacy_codes",
      subject: "moral",
      answer: `节点 ID 是短编码（如 ${samples(nodes.map((n) => n.id)).slice(0, 3).join("、")}），${nodes.filter((n) => /^MORAL\./.test(n.id)).length}/${nodes.length} 个使用 KGE 的 MORAL.* 命名，需要迁移表；失分归因四类（审题 / 定位 / 转化 / 规范）与平台七大类的映射见 docs/mapping/error-taxonomy.md`,
    },
    {
      question: "moral_pages_edition",
      subject: "moral",
      answer: `否：${nodes.length} 个节点里绑定版次 / 印次的 ${bound.length} 个；统编节点有页码 ${withPages.length}/${textbook.length}，源文件注明为近似值${approxNoted ? "" : "（未见注明）"}；读本节点页码“待核” ${pending.length} 个`,
    },
    { question: "reader_ingested", subject: "moral", answer: `${readerVerdict}；读本节点 ${reader.length} 个` },
  );
  blockers.push(
    {
      id: "moral-pages-edition",
      subject: "moral",
      problem: "页码索引未绑定教材版次与印次",
      verdict: `确认：${bound.length}/${nodes.length} 个节点绑定版次；页码是写在节点上的字符串，${approxNoted ? "源文件注明为近似值" : "未注明精度"}，能解析为区间的 ${parsable.length}/${textbook.length}。迁移时须进入 textbook_projection 并由教研抽样核对`,
      evidence: ["moral.pages.edition_bound", "moral.pages.parsable", "moral.pages.approx"],
    },
    {
      id: "moral-reader",
      subject: "moral",
      problem: "《学生读本》属于考查范围但尚未入库",
      verdict: `确认：${readerVerdict}。仓库里文件名含“读本”的文件 ${readerFiles.length} 个（见 moral.reader.files 样例，需人工确认其中有无原文）`,
      evidence: ["moral.reader.nodes", "moral.reader.pages", "moral.reader.files"],
    },
  );

  return { report: { subject: "moral", sources, groups, loadErrors: errors }, answers, blockers };
}
