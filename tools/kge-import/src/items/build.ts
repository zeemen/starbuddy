// KGE-02 题库导入：阿鸦题目 → items.sql（kge.item_family、kge.item_tag 草稿、kge.tag_candidate）、worklist.md、tagging-report.json。
// 纯函数、确定性：同样的输入逐字节同样的输出（没有时间戳，全部排序）。不连库；SQL 由人在演练库执行。
// 题干、答案、解析不进库也不进报告（迁移 0011；spec 待人确认 #9）；不含任何学生数据（规则 8、13）。
import { stableUuid } from "../../../kge-inventory/src/import/build.ts";
import type { LegacyBank, LegacyQuestion } from "../legacy-items.ts";
import type { MapInput } from "../map-version.ts";
import { firstOccurrenceKey, type ChapterSegment, type SegmentConfig } from "../segments.ts";
import type { TagDecision } from "../tagging/merge.ts";
import type { Pipeline } from "../tagging/pipeline.ts";
import { buildRuleContext, routeItem, type RefResult, type Route } from "../tagging/rules.ts";
import { A_MIN_CONFIDENCE, cmp, MAX_NODES, RULE_DIRECT, RULE_INFERRED, type Candidate, type Layer } from "../tagging/types.ts";
import type { ReportCandidate, ReportFamily, ReportItem, ReportSixSegments, TaggingReport } from "./report-types.ts";
import { insert, ITEM_FAMILY_COLUMNS, ITEM_TAG_COLUMNS, nodeArray, num3, q, qn, TAG_CANDIDATE_COLUMNS } from "./sql.ts";

export interface BuildInput {
  bank: LegacyBank;
  map: MapInput;
  segments: SegmentConfig;
  /** 内容包里登记过的题目 ID（platform.item）；null 表示不核对 */
  registeredItems: ReadonlySet<string> | null;
  pipeline: Pipeline;
}
export interface BuildOutput { sql: string; worklistMd: string; report: TaggingReport; reportJson: string }

export class ItemsBuildError extends Error {}

interface ItemState {
  q: LegacyQuestion;
  route: Route;
  inScope: boolean;
  refs: RefResult[];
  decision: TagDecision | null;
  segment: ChapterSegment | null;
  segmentIssue: string | null;
  otherChapters: string[];
  familyCode: string | null;
  registered: boolean | null;
}

const layerCount = () => ({ rule: 0, vector: 0, model: 0 });

export function buildItems(input: BuildInput): BuildOutput {
  const { bank, map, segments, registeredItems, pipeline } = input;
  const ctx = buildRuleContext(map, bank.topics);
  const labelOf = new Map(bank.topics.map((t) => [t.id, t.chapterLabel]));

  // 1. 同一题目 ID 出现在多个章节时，只登记首次出现（年级、册次最早）的那个（spec 待人确认 #2）
  const byId = new Map<string, LegacyQuestion[]>();
  for (const x of bank.questions) byId.set(x.id, [...(byId.get(x.id) ?? []), x]);
  const familyOf = new Map<string, string>();
  for (const c of bank.sprintCategories) for (const id of c.questionIds) if (!familyOf.has(id)) familyOf.set(id, `aya-sprint-${c.id}`);

  const segmentFor = (topic: string | null): { seg: ChapterSegment | null; issue: string | null } => {
    if (topic === null) return { seg: null, issue: "压轴专项题没有章节，推不出年级与教材" };
    const seg = segments.chapters.get(topic);
    if (!seg) return { seg: null, issue: `章节 ${topic} 不在映射表里` };
    if (seg.label !== labelOf.get(topic)) return { seg: null, issue: `映射表的 label“${seg.label}”与 legacy chapterLabel“${labelOf.get(topic) ?? ""}”不一致` };
    return { seg, issue: null };
  };

  const states: ItemState[] = [];
  for (const id of [...byId.keys()].sort(cmp)) {
    const occ = byId.get(id)!;
    const withSeg = occ.map((x) => ({ x, ...segmentFor(x.topic) }));
    withSeg.sort((a, b) => (a.seg && b.seg ? cmp(firstOccurrenceKey(a.seg), firstOccurrenceKey(b.seg)) : a.seg ? -1 : b.seg ? 1 : 0));
    const chosen = withSeg[0]!;
    const route = routeItem(chosen.x, ctx);
    const inScope = route.chapter !== null && ctx.importedChapters.has(route.chapter);
    const r = pipeline.tag({ id, text: chosen.x.text, mainNodes: chosen.x.mainNodes, auxNodes: chosen.x.auxNodes, preNodes: chosen.x.preNodes }, inScope);
    states.push({
      q: chosen.x, route, inScope, refs: r.rule.refs, decision: r.decision, segment: chosen.seg, segmentIssue: chosen.issue,
      otherChapters: withSeg.slice(1).map((w) => w.x.topic ?? "sprint"), familyCode: familyOf.get(id) ?? null,
      registered: registeredItems ? registeredItems.has(id) : null,
    });
  }

  // 2. 题组（kge.item_family）：legacy 只有压轴专项有题组（母题切片 + 变式 + 挑战）；章节题的“单元”不是题组
  //    主考点 = 组内题目里规则直接命中的主考点中出现最多的（平手取节点 ID 小的）；推不出就不生成，进工作单
  const families: ReportFamily[] = [];
  const familyIssues: string[] = [];
  for (const c of [...bank.sprintCategories].sort((a, b) => cmp(a.id, b.id))) {
    const code = `aya-sprint-${c.id}`;
    const members = states.filter((s) => s.familyCode === code);
    const votes = new Map<string, number>();
    for (const s of members) {
      for (const cand of s.decision?.candidates ?? []) {
        if (cand.layer === "rule" && cand.role === "main" && cand.reasonCode === "rule_explicit_ref") votes.set(cand.nodeId, (votes.get(cand.nodeId) ?? 0) + 1);
      }
    }
    const main = [...votes.keys()].sort((a, b) => votes.get(b)! - votes.get(a)! || cmp(a, b))[0];
    if (!main) {
      familyIssues.push(`${code}（${c.name}）：组内题目没有落在已导入节点上的主测节点，未生成题组`);
      continue;
    }
    families.push({ code, id: stableUuid(`kge:aya:${map.version}:item_family:${code}`), main_node_id: main, item_ids: members.map((s) => s.q.id).sort(cmp) });
  }
  const familyByCode = new Map(families.map((f) => [f.code, f]));

  // 3. 每题的最终状态与 SQL 行
  const nodeLevels = new Map(map.nodes.map((n) => [n.id, n.cognitiveLevels]));
  const tagRows: string[][] = [];
  const candRows: string[][] = [];
  const items: ReportItem[] = [];
  const candRowsByLayer = layerCount();
  const candRowsByGrade = { A: 0, B: 0 };
  const allByLayer = layerCount();
  const worklist = { segments: [] as string[], outOfScope: new Map<string, string[]>(), unrouted: [] as string[], textRefs: [] as string[],
                     untaggable: [] as string[], aWithoutSix: [] as string[], overCap: [] as string[], notRegistered: [] as string[],
                     prefixMismatch: [] as string[], multiGrade: [] as string[] };
  const mv = map.mapVersionId;
  const legacyWhere = (old: string) => {
    const ch = ctx.legacyNodes.get(old)?.chapter ?? "?";
    return `${old}（${ch}${ctx.importedChapters.has(ch) ? "，章节已导入、该节点未导入" : "，章节未导入"}）`;
  };
  for (const s of states) {
    const id = s.q.id;
    const d = s.decision;
    if (s.segmentIssue) worklist.segments.push(`${id}：${s.segmentIssue}`);
    if (s.otherChapters.length > 0) worklist.multiGrade.push(`${id}：另见于 ${s.otherChapters.join("、")}，只登记首次出现的 ${s.q.topic ?? "sprint"}`);
    if (s.route.prefixMismatch) worklist.prefixMismatch.push(`${id}：ID 前缀与所在专题 ${s.q.topic ?? ""} 不一致`);
    if (s.registered === false) worklist.notRegistered.push(id);
    for (const r of s.refs) {
      if (r.outcome !== "text_unmatched" && !(r.outcome === "text_mapped" && r.field === "pre")) continue;
      const t = r.text!;
      const hint = t.mapNode ? `与地图节点 ${t.mapNode} 同名（前置不产生候选）`
        : t.mapAmbiguous.length ? `与多个地图节点同名：${t.mapAmbiguous.join("、")}`
        : t.legacyExact.length ? `对不上地图节点；legacy 同名节点 ${t.legacyExact.map((x) => legacyWhere(x)).join("、")}`
        : t.legacyPartial.length ? `对不上地图节点；legacy 名称相近 ${t.legacyPartial.join("、")}（仅供参考）` : "对不上任何节点";
      worklist.textRefs.push(`${id} · ${fieldName(r.field)}“${t.text}”：${hint}`);
    }
    const six: ReportSixSegments | null = s.segment ? { ...s.segment.segments, term: s.segment.term, chapter: s.segment.chapter_code } : null;
    let status: ReportItem["status"];
    if (!s.inScope || !d) {
      status = "out_of_scope";
      if (s.route.chapter === null) worklist.unrouted.push(id);
      else worklist.outOfScope.set(s.route.chapter, [...(worklist.outOfScope.get(s.route.chapter) ?? []), id]);
    } else if (d.status === "untaggable") {
      status = "untaggable";
      worklist.untaggable.push(`${id}：${describeRefs(s.refs)}`);
    } else {
      for (const c of d.candidates) allByLayer[c.layer]++;
      if (d.overCap) worklist.overCap.push(`${id}：合并后超过 ${MAX_NODES} 个节点，已截断为 ${d.nodeIds.join("、")}`);
      const canWrite = d.grade === "A" && six !== null && s.registered !== false;
      if (d.grade === "A" && !canWrite) {
        worklist.aWithoutSix.push(`${id}：A 级（${d.mainNodeId}），但${six === null ? "六段未定" : "未在内容包登记"}，只写候选`);
      }
      if (canWrite) {
        status = "item_tag";
        const levels = nodeLevels.get(d.mainNodeId!) ?? [];
        const family = s.familyCode ? familyByCode.get(s.familyCode) ?? null : null;
        tagRows.push([
          q(mv), "'aya'", q(id), q(d.mainNodeId!), nodeArray(d.nodeIds), qn(levels.length === 1 ? levels[0]! : null),
          family ? q(family.id) : "NULL", "'original'", "'unverified'", "'pending'", num3(d.confidence), q(d.source!),
          q(taggerOf(d)), qn(d.source === "model" ? pipeline.modelVersion : null),
        ]);
      } else {
        status = "candidate";
        if (s.registered !== false) {
          for (const c of d.candidates) {
            candRows.push([q(mv), "'aya'", q(id), q(c.nodeId), q(c.role), q(c.layer), num3(c.confidence), q(c.taggerVersion),
                           qn(c.modelVersion), q(c.reasonCode), q(d.grade!)]);
            candRowsByLayer[c.layer]++;
            candRowsByGrade[d.grade!]++;
          }
        }
      }
    }
    items.push({
      item_id: id, chapter: s.route.chapter, route: s.route.via, in_scope: s.inScope, status,
      grade: d?.grade ?? null, main_node_id: d?.mainNodeId ?? null, node_ids: d?.nodeIds ?? [], confidence: d?.confidence ?? null,
      layer: d?.source ?? null, over_cap: d?.overCap ?? false, b_reasons: d?.bReasons ?? [], six_segments: six,
      family_code: s.familyCode && familyByCode.has(s.familyCode) ? s.familyCode : null, registered: s.registered,
      candidates: (d?.candidates ?? []).map(reportCandidate),
    });
  }

  const textRefs = states.flatMap((s) => s.refs.filter((r) => r.text !== null));
  const count = (st: ReportItem["status"]) => items.filter((i) => i.status === st).length;
  const outByChapter = Object.fromEntries([...worklist.outOfScope].sort((a, b) => cmp(a[0], b[0])).map(([k, v]) => [k, v.length]));
  const report: TaggingReport = {
    format: "kge-tagging-report/1",
    format_doc: "tools/kge-import/src/items/report-types.ts（TaggingReport）",
    subject: "aya",
    map_version: { id: mv, version: map.version, imported_chapters: [...ctx.importedChapters].sort(cmp) },
    run: {
      layers: [...pipeline.layers].sort((a, b) => ORDER[a] - ORDER[b]), tagger_versions: pipeline.taggerVersions,
      model_version: pipeline.modelVersion, top_k: pipeline.topK, rules_only_a: pipeline.rulesOnlyA,
    },
    thresholds: { a_min_confidence: A_MIN_CONFIDENCE, rule_direct: RULE_DIRECT, rule_inferred: RULE_INFERRED, max_nodes: MAX_NODES },
    counts: {
      items_total: items.length, items_in_scope: items.filter((i) => i.in_scope).length, items_out_of_scope: count("out_of_scope"),
      out_of_scope_by_chapter: outByChapter, item_tag: count("item_tag"), candidate_items: count("candidate"), untaggable: count("untaggable"),
      grade: { A: items.filter((i) => i.grade === "A").length, B: items.filter((i) => i.grade === "B").length },
      candidate_rows: candRows.length, candidate_rows_by_layer: candRowsByLayer, candidate_rows_by_grade: candRowsByGrade,
      candidates_all_by_layer: allByLayer,
      six_segments_derived: items.filter((i) => i.six_segments !== null).length, six_segments_missing: items.filter((i) => i.six_segments === null).length,
      families: families.length, not_registered: worklist.notRegistered.length,
    },
    text_refs: {
      total: textRefs.length,
      map_matched: textRefs.filter((r) => r.text!.mapNode !== null).length,
      unmatched: textRefs.filter((r) => r.text!.mapNode === null).length,
      by_field: { main: textRefs.filter((r) => r.field === "main").length, aux: textRefs.filter((r) => r.field === "aux").length,
                  pre: textRefs.filter((r) => r.field === "pre").length },
    },
    families,
    items,
  };
  const sql = renderSql(map, families, tagRows, candRows, report);
  const worklistMd = renderWorklist(report, worklist, familyIssues);
  return { sql, worklistMd, report, reportJson: renderReportJson(report) };
}

/** 报告 JSON：外层两空格缩进，items 每题一行（评审看差异时一题一行；仍是标准 JSON） */
export function renderReportJson(report: TaggingReport): string {
  const MARK = "\u0000items\u0000";
  const head = JSON.stringify({ ...report, items: MARK }, null, 2);
  const items = report.items.length === 0 ? "[]" : `[\n${report.items.map((i) => `    ${JSON.stringify(i)}`).join(",\n")}\n  ]`;
  return `${head.replace(JSON.stringify(MARK), () => items)}\n`;
}

const ORDER: Record<Layer, number> = { rule: 0, vector: 1, model: 2 };
const fieldName = (f: RefResult["field"]) => (f === "main" ? "主测节点" : f === "aux" ? "辅助节点" : "前置节点");

function taggerOf(d: TagDecision): string {
  const c = d.candidates.find((x) => x.nodeId === d.mainNodeId && x.layer === d.source && x.role === "main")
    ?? d.candidates.find((x) => x.nodeId === d.mainNodeId && x.layer === d.source)!;
  return c.taggerVersion;
}

function reportCandidate(c: Candidate): ReportCandidate {
  return { node_id: c.nodeId, role: c.role, layer: c.layer, confidence: c.confidence, reason_code: c.reasonCode,
           tagger_version: c.taggerVersion, model_version: c.modelVersion };
}

const OUTCOME: Record<RefResult["outcome"], string> = {
  mapped: "已落到节点", topic_fallback: "退到主题", not_in_map: "节点与主题都未导入", other_chapter: "在未导入章节", dangling: "legacy 里不存在",
  text_mapped: "自然语言、与地图节点同名", text_unmatched: "自然语言、对不上节点", prerequisite: "前置",
};
function describeRefs(refs: RefResult[]): string {
  const rs = refs.filter((r) => r.field !== "pre");
  return rs.length === 0 ? "没有主测或辅助节点" : rs.map((r) => `${r.ref}（${OUTCOME[r.outcome]}）`).join("、");
}

function renderSql(map: MapInput, families: ReportFamily[], tagRows: string[][], candRows: string[][], r: TaggingReport): string {
  return [
    `-- KGE-02 阿鸦题库导入（生成文件，勿手改）：地图版本 aya ${map.version}（${map.mapVersionId}）上的题组、打标草稿与低置信候选`,
    "-- 由 tools/kge-import 生成；文件自带事务，在演练库用 psql -X -v ON_ERROR_STOP=1 执行。不含题干、答案、解析，不含任何学生数据。",
    "-- 前置：迁移 0011、0019、0024 已执行；地图版本种子（db/seeds/kge/aya-<专题>-<版本>/import.sql）已执行；",
    "--       题目已由 adapters/aya/scripts/register-content.ts 登记进 platform.item（item_tag / tag_candidate 对它有外键）。",
    "-- 六段标识不在这里写（platform.curriculum_version 由登记脚本负责）；每题推出的六段见同目录 tagging-report.json。",
    `-- 打标层：${r.run.layers.join(" + ")}；A 级 ${r.counts.grade.A} 题写 item_tag（待审草稿），候选 ${r.counts.candidate_rows} 行进低置信队列。`,
    "BEGIN;",
    "SET LOCAL standard_conforming_strings = on;  -- 反斜杠按字面处理，转义只靠单引号加倍",
    "SET LOCAL ROLE starbuddy_kge;  -- 以知识服务角色写入：只用它有的权限（最小权限，不是防注入的边界）",
    ...insert("kge.item_family", ITEM_FAMILY_COLUMNS, families.map((f) => [q(f.id), q(map.mapVersionId), q(f.main_node_id), q(f.code)])),
    ...insert("kge.item_tag", ITEM_TAG_COLUMNS, tagRows),
    ...insert("kge.tag_candidate", TAG_CANDIDATE_COLUMNS, candRows),
    "COMMIT;",
    "",
  ].join("\n");
}

function renderWorklist(r: TaggingReport, w: {
  segments: string[]; outOfScope: Map<string, string[]>; unrouted: string[]; textRefs: string[]; untaggable: string[]; aWithoutSix: string[];
  overCap: string[]; notRegistered: string[]; prefixMismatch: string[]; multiGrade: string[];
}, familyIssues: string[]): string {
  const cell = (s: string) => s.replace(/[\r\n]/g, " ");
  const section = (title: string, lines: string[], note?: string) => [
    `## ${title}（${lines.length}）`, "", ...(note ? [note, ""] : []), ...(lines.length ? lines.map((l) => `- ${cell(l)}`) : ["无"]), "",
  ];
  const c = r.counts;
  const outLines = [...w.outOfScope].sort((a, b) => cmp(a[0], b[0])).map(([ch, ids]) => `${ch}（${ids.length} 题）：${ids.join("、")}`);
  return [
    `# KGE-02 教研工作单：阿鸦题库 → 地图版本 ${r.map_version.version}`,
    "",
    "生成文件，勿手改。题干、答案不在这里，按题目 ID 到 legacy 或内容包里查；补充说明只写合成例子或题目 ID，**不得粘贴学生作答**（规则 13）。",
    "",
    `共 ${c.items_total} 题；已导入主题（${r.map_version.imported_chapters.join("、") || "无"}）里 ${c.items_in_scope} 题：`
      + `A 级 ${c.grade.A}、B 级 ${c.grade.B}、无处可标 ${c.untaggable}；写 item_tag ${c.item_tag} 题，候选 ${c.candidate_rows} 行。`,
    `打标层：${r.run.layers.join(" + ")}${r.run.rules_only_a ? "（--rules-only-a：只跑规则层时规则直接命中算 A）" : ""}。`
      + (r.run.layers.length === 1 && !r.run.rules_only_a ? "只跑规则层时按 spec 默认一律 B 级，全部进候选队列。" : ""),
    "",
    ...section("推不出六段标识的题", w.segments, "按 config/aya-chapter-segments.yaml（待教研核对）推年级、册次、教材；推不出的题请教研给出年级与教材。"),
    ...section("落在未导入主题上的题（不打标）", outLines, "这些章节的地图骨架还没有导入（spec 设计要点 6），题目只计数、列 ID，不虚构节点。"),
    ...section("归不到任何章节的题", w.unrouted),
    ...section("自然语言引用对不上节点", w.textRefs, "legacy 用名称而不是节点编码引用节点（KGE-00：97 处）。请映射到节点编码；“同名”“相近”只是建议，不自动采用。"),
    ...section("无处可标的题（已导入主题里，三层都没有候选）", w.untaggable),
    ...section("A 级但不能写 item_tag 的题", w.aWithoutSix),
    ...section("节点数超过上限的题", w.overCap, `小题 1–${r.thresholds.max_nodes} 个节点（KGE V1.1 :745）；超出的已截断，一律 B 级。`),
    ...section("未在内容包登记的题（platform.item 没有，不写库）", w.notRegistered),
    ...section("题目 ID 前缀与专题不一致", w.prefixMismatch),
    ...section("一题多章节（只登记首次出现的年级）", w.multiGrade),
    ...section("未生成的题组", familyIssues),
  ].join("\n");
}
