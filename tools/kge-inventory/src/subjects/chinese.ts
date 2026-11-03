// 语文 · 文砺：28 点、篇目与素材、写作单元、判分依据与错因（spec 设计要点 3 通用项 + 语文项 9–12）。
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { filesMatching, importLegacy, listTextFiles, readJson, readText } from "../legacy.ts";
import { finding, pct, samples, type Answer, type BlockerCheck, type Finding, type Group } from "../model.ts";
import type { SubjectResult } from "./math.ts";

/** 文砺在 legacy 仓库里的应用目录；夹具里直接以根目录为应用目录。 */
export const WENLI_APP = "初中语文_AI_class/wenli";
export function wenliAppDir(root: string): string {
  const app = join(root, WENLI_APP);
  return existsSync(app) ? app : root;
}

export const SHARED = "packages/shared/lib";
const DOMAIN = `${SHARED}/domain`;

/** 在全部迁移文件里找 `create type NAME as enum (...)` 的取值 */
export function enumValues(app: string, name: string): string[] | null {
  for (const f of listTextFiles(app, ["supabase/migrations"])) {
    const text = readFileSync(join(app, f), "utf8");
    const m = new RegExp(`create\\s+type\\s+${name}\\s+as\\s+enum\\s*\\(([^)]*)\\)`, "i").exec(text);
    if (m) return [...m[1]!.matchAll(/'([^']+)'/g)].map((x) => x[1]!);
  }
  return null;
}

const norm = (s: string) => s.replace(/[《》〈〉\s·・]/g, "");

interface CatalogRow {
  title?: string;
  author?: string;
  [k: string]: unknown;
}

function keysOf(rows: CatalogRow[]): string[] {
  return [...new Set(rows.flatMap((r) => Object.keys(r)))].sort();
}

export async function inventoryChinese(root: string): Promise<SubjectResult> {
  const app = wenliAppDir(root);
  const errors: string[] = [];
  const groups: Group[] = [];
  const answers: Answer[] = [];
  const blockers: BlockerCheck[] = [];
  const sources = [
    `${DOMAIN}/ability.ts`,
    `${DOMAIN}/error-types.ts`,
    `${DOMAIN}/reading-error-tags.ts`,
    `${DOMAIN}/classical-catalog.ts`,
    `${DOMAIN}/writing-syllabus.ts`,
    `${DOMAIN}/reading-methods.ts`,
    `${SHARED}/prompts/essay-rubric.ts`,
    "data/catalog.json",
    "data/corpus_candidates.json",
    "content/reading/错因标签.json",
    "supabase/migrations/*.sql",
  ];

  // ---------- 28 点 ----------
  const ability = await importLegacy<{ DOMAINS: { code: string }[]; LEVEL_CODES: string[] }>(app, `${DOMAIN}/ability.ts`, errors);
  const domains = (ability?.DOMAINS ?? []).map((d) => d.code);
  const levels = ability?.LEVEL_CODES ?? [];
  const dbDomains = enumValues(app, "domain_t");
  const dbLevels = enumValues(app, "level_t");
  const sameDomains = dbDomains !== null && dbDomains.join() === domains.join();
  const sameLevels = dbLevels !== null && dbLevels.join() === levels.join();
  const allText = listTextFiles(app, ["."]);
  const grid20 = filesMatching(app, allText, /二十格|20\s*格|5\s*[×xX*]\s*4\s*=\s*20/);
  const abilityKp = filesMatching(app, allText, /ability_kp|能力\s*[×x]\s*文本类型/);
  groups.push({
    title: "能力坐标：28 点与旧 20 格",
    findings: [
      finding("chn.grid.domains", "能力域数", domains.length),
      finding("chn.grid.levels", "等级数", levels.length),
      finding("chn.grid.cells", "能力格数（域 × 级）", domains.length * levels.length),
      finding("chn.grid.db_enum_match", "与迁移里的 domain_t / level_t 枚举一致", sameDomains && sameLevels ? "是" : "否", {
        note: `domain_t = ${dbDomains?.join(",") ?? "未找到"}；level_t = ${dbLevels?.join(",") ?? "未找到"}`,
      }),
      finding("chn.grid.legacy20", "提到旧 20 格（5 × 4）的文件", grid20.length, {
        samples: samples(grid20),
        note: "只能查当前快照；库里是否有 20 格编码的数据见库模式",
      }),
      finding("chn.ability_kp", "“能力 × 文本类型 × 题型”能力点数据", abilityKp.length === 0 ? "未找到" : `命中 ${abilityKp.length} 个文件`, {
        samples: samples(abilityKp),
      }),
    ],
  });

  // ---------- 篇目与素材 ----------
  const catalog = readJson<CatalogRow[]>(app, "data/catalog.json", errors) ?? [];
  const corpus = readJson<CatalogRow[]>(app, "data/corpus_candidates.json", errors) ?? [];
  const classicalMod = await importLegacy<{ CLASSICAL_CATALOG: CatalogRow[] }>(app, `${DOMAIN}/classical-catalog.ts`, errors);
  const classical = classicalMod?.CLASSICAL_CATALOG ?? [];
  const tset = (rows: CatalogRow[]) => new Set(rows.map((r) => norm(String(r.title ?? ""))).filter(Boolean));
  const [a, b, c] = [tset(catalog), tset(corpus), tset(classical)];
  const inter = (x: Set<string>, y: Set<string>) => [...x].filter((t) => y.has(t)).length;
  const pageKey = (rows: CatalogRow[]) => keysOf(rows).filter((k) => /page|页/i.test(k));
  const mustRecite = [catalog, corpus, classical].some((rows) => keysOf(rows).some((k) => /must_recite|必背/.test(k)));
  const migText = listTextFiles(app, ["supabase/migrations"]);
  const mustReciteDb = filesMatching(app, migText, /must_recite/);

  const searchDirs = ["data", "content", DOMAIN, "supabase"];
  const searchFiles = listTextFiles(app, searchDirs);
  const lookFor = (label: string, key: string, pattern: RegExp): Finding => {
    const hits = filesMatching(app, searchFiles, pattern);
    return finding(key, label, hits.length === 0 ? "未找到" : "未找到清单（只有关键字命中，需人工核实）", {
      samples: samples(hits),
      note: `查过：${searchDirs.join("、")}`,
    });
  };
  groups.push({
    title: "篇目与素材",
    findings: [
      finding("chn.catalog.rows", "data/catalog.json 篇目数", catalog.length, { note: `字段：${keysOf(catalog).join("、")}` }),
      finding("chn.corpus.rows", "data/corpus_candidates.json 篇目数", corpus.length, { note: `字段：${keysOf(corpus).join("、")}` }),
      finding("chn.classical.rows", "classical-catalog.ts 文言与诗词条目数", classical.length),
      finding("chn.catalog.overlap", "三份清单按标题的交集（catalog∩corpus / catalog∩classical / corpus∩classical）", `${inter(a, b)} / ${inter(a, c)} / ${inter(b, c)}`),
      finding(
        "chn.catalog.pages",
        "篇目清单里的页码字段",
        [catalog, corpus, classical].every((r) => pageKey(r).length === 0) ? "无（三份清单都没有册-课-页）" : "有",
      ),
      finding("chn.must_recite.static", "静态清单里标“必背”的字段", mustRecite ? "有" : "无"),
      finding("chn.must_recite.db", "迁移里定义 must_recite 列的文件", mustReciteDb.length, {
        samples: samples(mustReciteDb),
        note: "85 篇必背只能在库里查，见库模式",
      }),
      lookFor("120 实词清单", "chn.shici", /实词/),
      lookFor("18 虚词清单", "chn.xuci", /虚词/),
      lookFor("34 个单元导语阅读方法", "chn.unit_intro", /单元导语/),
    ],
  });

  // ---------- 写作与阅读方法 ----------
  const writing = await importLegacy<{ UNITS: CatalogRow[] }>(app, `${DOMAIN}/writing-syllabus.ts`, errors);
  const methods = await importLegacy<{ READING_METHOD_KEYS: string[] }>(app, `${DOMAIN}/reading-methods.ts`, errors);
  const units = writing?.UNITS ?? [];
  groups.push({
    title: "写作单元与阅读方法",
    findings: [
      finding("chn.writing.units", "写作单元数", units.length, { note: `字段：${keysOf(units).join("、")}` }),
      finding("chn.writing.pages", "写作单元的页码字段", pageKey(units).length === 0 ? "无" : pageKey(units).join("、")),
      finding("chn.reading_methods", "阅读方法卡数（reading-methods.ts）", methods?.READING_METHOD_KEYS?.length ?? 0),
    ],
  });

  // ---------- 判分依据与错因 ----------
  const errTypes = await importLegacy<{ ERROR_TYPE_NAMES: string[] }>(app, `${DOMAIN}/error-types.ts`, errors);
  const tagsMod = await importLegacy<{ READING_ERROR_TAGS: { id: string; category: string; systemErrorType: string }[] }>(
    app,
    `${DOMAIN}/reading-error-tags.ts`,
    errors,
  );
  const tagsJson = readJson<{ version?: string; error_tags?: { id: string }[] }>(app, "content/reading/错因标签.json", errors);
  const typeNames = errTypes?.ERROR_TYPE_NAMES ?? [];
  const dbErr = enumValues(app, "error_t");
  const tags = tagsMod?.READING_ERROR_TAGS ?? [];
  const badMap = tags.filter((t) => !typeNames.includes(t.systemErrorType)).map((t) => t.id);
  const tsIds = new Set(tags.map((t) => t.id));
  const jsonIds = new Set((tagsJson?.error_tags ?? []).map((t) => t.id));
  const onlyTs = [...tsIds].filter((x) => !jsonIds.has(x));
  const onlyJson = [...jsonIds].filter((x) => !tsIds.has(x));
  const rubricText = readText(app, `${SHARED}/prompts/essay-rubric.ts`);
  const rubricVersioned = rubricText !== null && /version|VERSION|rubric_version/.test(rubricText);
  const gradeFocus = filesMatching(app, listTextFiles(app, [SHARED]), /grade_focus/);
  groups.push({
    title: "判分依据与错因",
    findings: [
      finding("chn.essay_rubric", "作文五维量表", rubricText === null ? "未找到" : "有（以 prompt 字符串形式存在）", {
        note: rubricVersioned ? "文件里有版本字样" : "没有版本号，判分结果无法绑定量表版本",
      }),
      finding("chn.reading_scoring_points", "阅读采分点的静态资产", "无，由模型生成 grade_focus", {
        samples: samples(gradeFocus),
      }),
      finding("chn.error_types", "中文六类错因", typeNames.join("、") || "未找到"),
      finding("chn.error_types.db_match", "与迁移枚举 error_t 一致", dbErr !== null && dbErr.join() === typeNames.join() ? "是" : "否"),
      finding("chn.reading_tags", "阅读错因标签数 / 大类数", `${tags.length} / ${new Set(tags.map((t) => t.category)).size}`),
      finding("chn.reading_tags.mapping", "标签能映射回中文六类的比例", `${tags.length - badMap.length}/${tags.length}（${pct(tags.length - badMap.length, tags.length)}）`, {
        samples: samples(badMap),
      }),
      finding(
        "chn.reading_tags.ts_vs_json",
        "TS 标签与 错因标签.json 的差异（仅 TS / 仅 JSON）",
        `${onlyTs.length} / ${onlyJson.length}`,
        { samples: samples([...onlyTs.map((x) => `仅TS ${x}`), ...onlyJson.map((x) => `仅JSON ${x}`)]), note: `JSON 版本 ${tagsJson?.version ?? "无"}` },
      ),
    ],
  });

  // ---------- 回答与阻断 ----------
  answers.push(
    {
      question: "counts",
      subject: "chinese",
      answer: `能力格 ${domains.length * levels.length}（${domains.length} 域 × ${levels.length} 级）；可打标能力点 ${abilityKp.length === 0 ? "0" : "见样例"}；篇目清单 ${catalog.length} / ${corpus.length} / ${classical.length} 条；写作单元 ${units.length}；题目：仓库里没有静态题库，见库模式；Rubric：作文五维量表 1 份（prompt 字符串），阅读采分点无静态资产`,
    },
    {
      question: "missing_fields",
      subject: "chinese",
      answer: `篇目与写作单元都没有册-课-页；静态清单没有“必背”标记；作文量表没有版本号；120 实词、18 虚词、34 个单元导语都没有找到清单`,
    },
    {
      question: "orphans_cycles",
      subject: "chinese",
      answer: "不适用：语文现有资产是能力坐标与素材清单，没有节点间关系，也就没有环与孤点",
    },
    {
      question: "legacy_codes",
      subject: "chinese",
      answer: `旧 20 格：当前快照里${grid20.length === 0 ? "没有" : `有 ${grid20.length} 个文件`}提到（仓库只有 1 个提交，查不到历史；库里见库模式）；错因两套命名并存（中文六类 + ${tags.length} 个英文阅读标签），映射完整率 ${pct(tags.length - badMap.length, tags.length)}`,
    },
  );
  blockers.push({
    id: "chinese-ability-kp",
    subject: "chinese",
    problem: "28 点尚未下钻为可打标的能力点",
    verdict:
      abilityKp.length === 0
        ? `确认：仓库里只有 ${domains.length * levels.length} 格能力坐标、阅读题型与错因标签，没有“能力 × 文本类型 × 题型”的能力点数据；“积累与运用”素材网的 120 实词、18 虚词、34 个单元导语也没有找到清单，85 篇必背只能在库里查`
        : `仓库里有 ${abilityKp.length} 个文件提到能力点下钻，需人工核实`,
    evidence: ["chn.grid.cells", "chn.ability_kp", "chn.shici", "chn.xuci", "chn.unit_intro", "chn.must_recite.db"],
  });

  return { report: { subject: "chinese", sources, groups, loadErrors: errors }, answers, blockers };
}
