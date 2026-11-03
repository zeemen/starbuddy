// 教材投影的页码抽样核验（冲刺 4-K，spec 设计要点 2；待人确认 #5 默认：每版次 20%、至少 20 条、通过率 ≥ 95%）。
// 流程：
//   1. 抽样（sample）：按固定种子从某地图版本的投影里逐版次抽样，生成核验单（投影 ID、节点 ID、版次、册、章、页码），**不含任何原文**；
//   2. 教研对照纸质教材核对后回填（backfill）：每行填 result（pass / fail）、checker（工号）、checked_on（日期），可写简短备注；
//   3. 判定（gate）：逐版次算通过率；低于阈值的版次**整体不能标 verified**（本文件的纯函数判定，生成 SQL 时再拦一次）；
//   4. 写回：只由知识服务（starbuddy_kge，审核台后端）在草稿版本上写 mapping_state / verified_by / verified_at，
//      再由 active staff 对整个地图版本签字（kge.sign_review，摘要含投影，签字后再改要重签），不新增写入方。
// 已知限制：0011 的约束 projection_verified_by 要求 verified 的投影 quote_text 非空，而原句在法务确认前不入库（decisions #5），
//   所以现有表上“verified”写不进去；见 specs/kge-daofa-slice-sprint4/lessons.md 待人确认（迁移草稿需求）。
import { createHash } from "node:crypto";
import { q } from "../items/sql.ts";
import { cmp, DaofaError, EDITION, parseDate, STAFF_CODE } from "./common.ts";
import type { SnapshotProjection } from "./snapshot.ts";

export const DEFAULT_RATIO = 0.2;
export const DEFAULT_MIN = 20;
export const DEFAULT_THRESHOLD = 0.95;
export const DEFAULT_SEED = "kge-daofa-page-check";

/** 核验单与回填文件的列（回填文件在核验单基础上填后三列与备注） */
export const CHECKLIST_COLUMNS = [
  "sample_id", "projection_id", "edition", "edition_year", "volume", "chapter", "page_from", "page_to", "node_id",
  "result", "checker", "checked_on", "note",
] as const;
const KEY_COLUMNS = ["sample_id", "projection_id", "edition", "edition_year", "volume", "chapter", "page_from", "page_to", "node_id"] as const;

export interface SampleRow {
  sample_id: string;
  projection_id: string;
  edition: string;
  edition_year: number;
  volume: string;
  chapter: string;
  page_from: number;
  page_to: number;
  node_id: string;
}

/** 每版次的样本量：N 条投影抽 max(ceil(N × ratio), min) 条，不超过 N */
export function sampleSize(n: number, ratio = DEFAULT_RATIO, min = DEFAULT_MIN): number {
  if (!(ratio > 0 && ratio <= 1)) throw new DaofaError("抽样比例应在 (0, 1]");
  if (!Number.isInteger(min) || min < 1) throw new DaofaError("最少抽样条数应为正整数");
  return Math.min(n, Math.max(Math.ceil(n * ratio - 1e-9), min));
}

const rankKey = (seed: string, id: string) => createHash("sha256").update(`${seed}\u0000${id}`).digest("hex");

/** 按固定种子抽样：每版次按 sha256(种子, 投影 ID) 排序取前 k 条；同一输入同一种子结果相同，与输入顺序无关 */
export function samplePages(projections: SnapshotProjection[], opts: { seed?: string; ratio?: number; min?: number } = {}): SampleRow[] {
  const seed = opts.seed ?? DEFAULT_SEED;
  if (seed.length === 0 || seed.length > 64) throw new DaofaError("种子应为 1–64 个字符");
  const byEdition = new Map<string, SnapshotProjection[]>();
  for (const p of projections) byEdition.set(p.edition, [...(byEdition.get(p.edition) ?? []), p]);
  const out: SampleRow[] = [];
  for (const edition of [...byEdition.keys()].sort(cmp)) {
    const all = byEdition.get(edition)!;
    const k = sampleSize(all.length, opts.ratio, opts.min);
    const picked = [...all].sort((a, b) => cmp(rankKey(seed, a.id), rankKey(seed, b.id))).slice(0, k)
      .sort((a, b) => cmp(a.volume, b.volume) || a.page_from - b.page_from || a.page_to - b.page_to || cmp(a.node_id, b.node_id) || cmp(a.id, b.id));
    picked.forEach((p, i) => out.push({
      sample_id: `${edition}-${String(i + 1).padStart(3, "0")}`, projection_id: p.id, edition, edition_year: p.edition_year,
      volume: p.volume, chapter: p.chapter, page_from: p.page_from, page_to: p.page_to, node_id: p.node_id,
    }));
  }
  return out;
}

// ---------- CSV（RFC 4180 子集：逗号分隔，含逗号、引号、换行的字段加双引号，引号加倍） ----------
export function csvField(v: string | number): string {
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function renderChecklist(rows: SampleRow[]): string {
  return [CHECKLIST_COLUMNS.join(","), ...rows.map((r) => [...KEY_COLUMNS.map((k) => csvField(r[k])), "", "", "", ""].join(","))].join("\n") + "\n";
}

export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let i = 0;
  let quoted = false;
  const src = text.replace(/^﻿/, "");
  while (i < src.length) {
    const ch = src[i]!;
    if (quoted) {
      if (ch === '"') {
        if (src[i + 1] === '"') { cur += '"'; i += 2; continue; }
        quoted = false; i++; continue;
      }
      cur += ch; i++; continue;
    }
    if (ch === '"' && cur === "") { quoted = true; i++; continue; }
    if (ch === ",") { row.push(cur); cur = ""; i++; continue; }
    if (ch === "\r" && src[i + 1] === "\n") { i++; continue; }
    if (ch === "\n") { row.push(cur); rows.push(row); row = []; cur = ""; i++; continue; }
    cur += ch; i++;
  }
  if (quoted) throw new DaofaError("CSV 里的引号没有闭合");
  if (cur !== "" || row.length > 0) { row.push(cur); rows.push(row); }
  return rows.filter((r) => !(r.length === 1 && r[0] === ""));
}

export interface BackfillRow extends SampleRow { result: "pass" | "fail"; checker: string; checked_on: string; note: string }

/**
 * 读回填文件并与核验单逐行核对：表头固定、样本一一对应、关键列（投影 ID、版次、页码……）不许改、
 * result 只能 pass / fail、checker 是工号、checked_on 是日期、note 不超过 100 字（只写页码差异，不抄原文）。
 */
export function parseBackfill(text: string, checklist: SampleRow[]): BackfillRow[] {
  const rows = parseCsv(text);
  const head = rows.shift();
  if (!head || head.join(",") !== CHECKLIST_COLUMNS.join(",")) throw new DaofaError(`回填文件表头应为 ${CHECKLIST_COLUMNS.join(",")}`);
  const want = new Map(checklist.map((r) => [r.sample_id, r]));
  const seen = new Set<string>();
  const out = rows.map((cells, i): BackfillRow => {
    const where = `回填第 ${i + 2} 行`;
    if (cells.length !== CHECKLIST_COLUMNS.length) throw new DaofaError(`${where}：应有 ${CHECKLIST_COLUMNS.length} 列`);
    const r = Object.fromEntries(CHECKLIST_COLUMNS.map((c, k) => [c, cells[k]!.trim()])) as Record<(typeof CHECKLIST_COLUMNS)[number], string>;
    const s = want.get(r.sample_id);
    if (!s) throw new DaofaError(`${where}：样本 ${r.sample_id} 不在核验单里`);
    if (seen.has(r.sample_id)) throw new DaofaError(`${where}：样本 ${r.sample_id} 重复`);
    seen.add(r.sample_id);
    for (const k of KEY_COLUMNS) {
      if (r[k] !== String(s[k])) throw new DaofaError(`${where}：${k} 与核验单不一致（核验单的列不许改；页码有误写 fail 并在 note 说明）`);
    }
    if (r.result !== "pass" && r.result !== "fail") throw new DaofaError(`${where}：result 只能是 pass 或 fail`);
    if (!STAFF_CODE.test(r.checker)) throw new DaofaError(`${where}：checker 应为核验人工号（不写姓名）`);
    parseDate(`${where} checked_on`, r.checked_on);
    if ([...r.note].length > 100 || /[\u0000-\u001f\u007f]/.test(r.note)) throw new DaofaError(`${where}：note 不超过 100 字（只写页码差异，不抄原文）`);
    return { ...s, result: r.result, checker: r.checker, checked_on: r.checked_on, note: r.note };
  });
  const missing = checklist.filter((c) => !seen.has(c.sample_id)).map((c) => c.sample_id);
  if (missing.length > 0) throw new DaofaError(`回填文件缺 ${missing.length} 个样本：${missing.slice(0, 5).join("、")}${missing.length > 5 ? "……" : ""}`);
  return out.sort((a, b) => cmp(a.sample_id, b.sample_id));
}

export interface EditionDecision {
  edition: string;
  projections: number;
  sampled: number;
  passed: number;
  failed: number;
  pass_rate: number; // 保留 4 位小数
  threshold: number;
  can_verify: boolean;
}

/** 逐版次判定：通过率 = pass / 样本数，达到阈值（含等于）才能整体标 verified；样本为 0 的版次不能标 */
export function gateEditions(projections: SnapshotProjection[], backfill: BackfillRow[], threshold = DEFAULT_THRESHOLD): EditionDecision[] {
  if (!(threshold > 0 && threshold <= 1)) throw new DaofaError("通过率阈值应在 (0, 1]");
  const editions = [...new Set(projections.map((p) => p.edition))].sort(cmp);
  return editions.map((edition) => {
    const rows = backfill.filter((b) => b.edition === edition);
    const passed = rows.filter((b) => b.result === "pass").length;
    // 整数比较，避免浮点误差：passed / sampled ≥ threshold ⇔ passed × 10000 ≥ round(threshold × 10000) × sampled
    const can = rows.length > 0 && passed * 10000 >= Math.round(threshold * 10000) * rows.length;
    return {
      edition, projections: projections.filter((p) => p.edition === edition).length, sampled: rows.length, passed, failed: rows.length - passed,
      pass_rate: rows.length === 0 ? 0 : Math.round((passed / rows.length) * 10000) / 10000, threshold, can_verify: can,
    };
  });
}

/**
 * 回填 SQL（以 starbuddy_kge 在草稿地图版本上执行；写完由 active staff 对整个版本签字）：
 *   - 所有版次：核验不通过的样本 → disputed（页码有误，待教研更正）；
 *   - 只有判定可以标 verified 的版次：通过的样本记核验人，未抽到的投影记批准人（approver），mapping_state → verified。
 * 请求把判定为不能标的版次标 verified 时直接报错（不生成）。
 */
export function renderBackfillSql(opts: {
  mapVersionId: string; projections: SnapshotProjection[]; backfill: BackfillRow[]; decisions: EditionDecision[];
  approver: string; approvedOn: string; verifyEditions?: string[];
}): string {
  if (!STAFF_CODE.test(opts.approver)) throw new DaofaError("approver 应为批准人工号");
  parseDate("approved_on", opts.approvedOn);
  const decided = new Map(opts.decisions.map((d) => [d.edition, d]));
  const verify = opts.verifyEditions ?? opts.decisions.filter((d) => d.can_verify).map((d) => d.edition);
  for (const e of verify) {
    if (!EDITION.test(e)) throw new DaofaError(`版次编码不合法：${e}`);
    const d = decided.get(e);
    if (!d || !d.can_verify) {
      throw new DaofaError(`版次 ${e} 的页码核验通过率 ${d ? `${(d.pass_rate * 100).toFixed(1)}%` : "（无样本）"} 低于阈值，整体不能标 verified`);
    }
  }
  const mv = q(opts.mapVersionId);
  const lines = [
    "-- 冲刺 4-K 教材投影页码核验回填（生成文件，勿手改）。只写映射状态、核验人工号与核验时间，不写任何原文。",
    `-- 判定：${opts.decisions.map((d) => `${d.edition} ${d.passed}/${d.sampled}（${d.can_verify ? "可标 verified" : "不能标 verified"}）`).join("；")}`,
    "-- 以知识服务角色（审核台后端）在草稿地图版本上执行；写完由 active staff 对整个地图版本签字（kge.sign_review，摘要含投影）。",
    "BEGIN;",
    "SET LOCAL standard_conforming_strings = on;",
    "SET LOCAL ROLE starbuddy_kge;",
  ];
  const failed = opts.backfill.filter((b) => b.result === "fail").sort((a, b) => cmp(a.projection_id, b.projection_id));
  if (failed.length > 0) {
    lines.push(`UPDATE kge.textbook_projection SET mapping_state = 'disputed' WHERE map_version_id = ${mv} AND id IN (${failed.map((b) => q(b.projection_id)).join(", ")});`);
  }
  for (const edition of [...verify].sort(cmp)) {
    const passed = opts.backfill.filter((b) => b.edition === edition && b.result === "pass").sort((a, b) => cmp(a.projection_id, b.projection_id));
    for (const b of passed) {
      lines.push(`UPDATE kge.textbook_projection SET mapping_state = 'verified', verified_by = ${q(b.checker)}, verified_at = ${q(`${b.checked_on} 00:00:00+00`)} `
        + `WHERE map_version_id = ${mv} AND id = ${q(b.projection_id)};`);
    }
    const sampled = new Set(opts.backfill.filter((b) => b.edition === edition).map((b) => b.projection_id));
    const rest = opts.projections.filter((p) => p.edition === edition && !sampled.has(p.id)).map((p) => p.id).sort(cmp);
    if (rest.length > 0) {
      lines.push(`UPDATE kge.textbook_projection SET mapping_state = 'verified', verified_by = ${q(opts.approver)}, verified_at = ${q(`${opts.approvedOn} 00:00:00+00`)} `
        + `WHERE map_version_id = ${mv} AND id IN (${rest.map(q).join(", ")});`);
    }
  }
  lines.push("COMMIT;", "");
  return lines.join("\n");
}
