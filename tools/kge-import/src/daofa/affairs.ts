// 道法时政条目（冲刺 4-K，spec 设计要点 4；inventory-zhixing decisions #6）。
// 录入格式：tools/kge-import/templates/daofa-current-affairs.yaml。校验：
//   - 来源必须是 https 链接，主机名在 policy.source_domain 白名单里（离线按 --source-domains 清单核对；种子执行时在库里再核一次）；
//   - valid_from / valid_until 必填，valid_until 晚于 valid_from，且不超过 valid_from 起 12 个月（spec 待人确认 #6，默认上限）；
//   - 只存自写摘要，不存转载原文（原文、全文、转载一类字段一律拒绝；摘要不超过 200 字）；
//   - 挂接的节点必须是目标地图版本里的观点。
// 导入一律 pending（review_status 不由录入决定），经审核台签字后才 published；到期由只读角色的 RLS 自动不可见（0026）。
// 不自动抓取、不由模型生成核心政治判断内容（decisions #6）：本工具只校验与转写人工录入的条目。
// 摘要不入库：kge.current_affair（0011）没有摘要列，摘要留在内容仓（见 lessons 待人确认）。
import { nodeArray, q } from "../items/sql.ts";
import {
  addMonths, cmp, DaofaError, daysBetween, keys, obj, parseDate, scanForbidden, stableUuid, SUBJECT, text,
} from "./common.ts";
import { syntheticGuard } from "./seed.ts";
import type { SnapshotAffair } from "./snapshot.ts";
import { parseYamlDoc } from "./yaml-doc.ts";

export const AFFAIRS_FORMAT = "kge-daofa-current-affairs/1";
export const DEFAULT_MAX_MONTHS = 12;
export const DEFAULT_WARN_DAYS = 30;
const KEY = /^[a-z0-9][a-z0-9-]{1,47}$/;
const HOST = /^[a-z0-9][a-z0-9.-]{2,252}$/;
const URL_RE = /^https:\/\/([a-z0-9][a-z0-9.-]{2,252})(\/[A-Za-z0-9._~/+-]*)?$/;
// 摘要里出现这些字样多半是整段转载，拒绝（只存自写摘要）
const REPRINT_HINT = /(转载|原文|全文|以下为|来源：|据.{0,12}报道：)/;

export interface AffairEntry {
  key: string;
  source: string;
  host: string;
  nodeIds: string[];
  summary: string;
  validFrom: string;
  validUntil: string;
}
export interface AffairsFile { synthetic: boolean; notice: string; entries: AffairEntry[] }

/** policy.url_host 的同一规则：https:// 之后到第一个 / : ? # 为止，小写 */
export const urlHost = (url: string): string | null => /^https:\/\/([^/:?#]+)/.exec(url)?.[1]?.toLowerCase() ?? null;

/** 白名单清单：每行一个主机名（由人从 policy.source_domain 导出），# 开头为注释 */
export function parseSourceDomains(txt: string): Set<string> {
  const out = new Set<string>();
  txt.split(/\r?\n/).forEach((line, i) => {
    const h = line.replace(/#.*/, "").trim();
    if (h === "") return;
    if (!HOST.test(h)) throw new DaofaError(`来源白名单第 ${i + 1} 行不是主机名`);
    out.add(h);
  });
  if (out.size === 0) throw new DaofaError("来源白名单是空的");
  return out;
}

export interface AffairsOptions { viewpointIds: ReadonlySet<string>; sourceDomains: ReadonlySet<string>; maxMonths?: number }

export function parseAffairs(yamlText: string, opts: AffairsOptions, file = "current-affairs.yaml"): AffairsFile {
  let raw;
  try {
    raw = parseYamlDoc(yamlText);
  } catch (e) {
    throw new DaofaError(`${file}：${(e as Error).message}`);
  }
  scanForbidden(file, raw);
  keys(file, raw, ["format", "synthetic", "notice", "subject", "entries"], ["format", "synthetic", "notice", "subject", "entries"]);
  if (raw.format !== AFFAIRS_FORMAT) throw new DaofaError(`${file}：format 应为 ${AFFAIRS_FORMAT}`);
  if (raw.subject !== SUBJECT) throw new DaofaError(`${file}：subject 必须是 ${SUBJECT}`);
  if (typeof raw.synthetic !== "boolean") throw new DaofaError(`${file}：synthetic 必须是 true 或 false`);
  const notice = text(`${file}.notice`, raw.notice, 400);
  if (raw.synthetic && !notice.includes("合成")) throw new DaofaError(`${file}：合成样例的 notice 必须写明“合成”`);
  if (!Array.isArray(raw.entries) || raw.entries.length === 0 || raw.entries.length > 500) throw new DaofaError(`${file}：entries 必须是 1–500 条`);
  const maxMonths = opts.maxMonths ?? DEFAULT_MAX_MONTHS;
  const seen = new Set<string>();
  const entries = raw.entries.map((e, i): AffairEntry => {
    const where = `${file}.entries[${i}]`;
    const o = obj(where, e);
    keys(where, o, ["key", "source", "node_ids", "summary", "valid_from", "valid_until"], ["key", "source", "node_ids", "summary", "valid_from", "valid_until"]);
    if (typeof o.key !== "string" || !KEY.test(o.key)) throw new DaofaError(`${where}.key 应为小写编码（字母数字与连字符）`);
    if (seen.has(o.key)) throw new DaofaError(`${where}.key ${o.key} 重复`);
    seen.add(o.key);
    if (typeof o.source !== "string" || !URL_RE.test(o.source)) throw new DaofaError(`${where}.source 必须是 https 链接（只含主机名与路径）`);
    const host = urlHost(o.source)!;
    if (!opts.sourceDomains.has(host)) throw new DaofaError(`${where}.source 的主机名 ${host} 不在来源白名单（policy.source_domain）里`);
    if (!Array.isArray(o.node_ids) || o.node_ids.length === 0 || o.node_ids.length > 16) throw new DaofaError(`${where}.node_ids 必须有 1–16 个`);
    const nodeIds = o.node_ids.map((n, k) => {
      if (typeof n !== "string" || !opts.viewpointIds.has(n)) throw new DaofaError(`${where}.node_ids[${k}] 不是目标地图版本里的观点：${String(n)}`);
      return n;
    });
    if (new Set(nodeIds).size !== nodeIds.length) throw new DaofaError(`${where}.node_ids 重复`);
    const summary = text(`${where}.summary`, o.summary, 200);
    if (REPRINT_HINT.test(summary)) throw new DaofaError(`${where}.summary 疑似转载原文：只存自写摘要`);
    const validFrom = parseDate(`${where}.valid_from`, o.valid_from);
    const validUntil = parseDate(`${where}.valid_until`, o.valid_until);
    if (validUntil <= validFrom) throw new DaofaError(`${where}：valid_until 必须晚于 valid_from`);
    if (validUntil > addMonths(validFrom, maxMonths)) throw new DaofaError(`${where}：有效期不能超过 ${maxMonths} 个月（valid_until 最晚 ${addMonths(validFrom, maxMonths)}）`);
    return { key: o.key, source: o.source, host, nodeIds: [...nodeIds].sort(cmp), summary, validFrom, validUntil };
  });
  entries.sort((a, b) => cmp(a.key, b.key));
  return { synthetic: raw.synthetic, notice, entries };
}

export const affairIdOf = (version: string, key: string): string => stableUuid(`kge:${SUBJECT}:${version}:current_affair:${key}`);

/** 时政种子：一律 pending；执行时在库里再核一次来源主机名在 policy.source_domain（active）里 */
export function renderAffairsSql(f: AffairsFile, opts: { mapVersionId: string; version: string; source: string }): string {
  const rows = f.entries.map((e) => `  (${q(affairIdOf(opts.version, e.key))}, ${q(opts.mapVersionId)}, ${nodeArray(e.nodeIds)}, ${q(e.source)}, `
    + `${q(e.validFrom)}, ${q(e.validUntil)}, 'pending')`);
  const hosts = [...new Set(f.entries.map((e) => e.host))].sort(cmp);
  return [
    `-- 冲刺 4-K 道法时政（生成文件，勿手改）：${opts.source} → kge.current_affair（${f.entries.length} 条，一律 pending，经审核台签字后发布）`,
    "-- 只存来源链接、挂接观点与有效期；摘要（自写）留在内容仓，不入库；不存任何转载原文。不含任何学生数据。",
    ...(f.synthetic ? ["-- 合成样例（合成、非真实时政）：来源主机为虚构的 .example 域名，只用于在一次性测试库证明流程。"] : []),
    "BEGIN;",
    "SET LOCAL standard_conforming_strings = on;",
    ...(f.synthetic ? [syntheticGuard()] : []),
    "SET LOCAL ROLE starbuddy_kge;",
    "-- 来源白名单在库里再核一次（policy.source_domain 由人维护，应用角色只读）",
    "DO $whitelist$ BEGIN",
    `  IF EXISTS (SELECT 1 FROM unnest(ARRAY[${hosts.map(q).join(", ")}]::text[]) h`,
    "              WHERE NOT EXISTS (SELECT 1 FROM policy.source_domain d WHERE d.host = h AND d.status = 'active')) THEN",
    "    RAISE EXCEPTION '时政来源的主机名不在 policy.source_domain 白名单里' USING ERRCODE = 'check_violation';",
    "  END IF;",
    "END $whitelist$;",
    "INSERT INTO kge.current_affair (id, map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES",
    rows.join(",\n") + ";",
    "COMMIT;",
    "",
  ].join("\n");
}

export const snapshotAffairs = (f: AffairsFile, version: string): SnapshotAffair[] =>
  f.entries.map((e) => ({ id: affairIdOf(version, e.key), node_ids: e.nodeIds, valid_from: e.validFrom, valid_until: e.validUntil, review_status: "pending" }));

// ---------- 即将到期报表 ----------
export interface ExpiryRow { key: string; id: string; node_ids: string[]; valid_until: string; days_left: number; state: "expired" | "expiring" }

/** 今天起 warnDays 天内到期（含当天之后第 warnDays 天）的为“即将到期”；valid_until ≤ 今天的为“已过期”（0026：current_date < valid_until 才可见） */
export function expiryReport(f: AffairsFile, version: string, today: string, warnDays = DEFAULT_WARN_DAYS): ExpiryRow[] {
  parseDate("today", today);
  if (!Number.isInteger(warnDays) || warnDays < 1 || warnDays > 366) throw new DaofaError("提前提醒天数应为 1–366");
  return f.entries
    .map((e) => ({ e, left: daysBetween(today, e.validUntil) }))
    .filter(({ left }) => left <= warnDays)
    .map(({ e, left }): ExpiryRow => ({ key: e.key, id: affairIdOf(version, e.key), node_ids: e.nodeIds, valid_until: e.validUntil, days_left: left,
      state: left <= 0 ? "expired" : "expiring" }))
    .sort((a, b) => cmp(a.valid_until, b.valid_until) || cmp(a.key, b.key));
}

export function renderExpiry(rows: ExpiryRow[], today: string, warnDays: number): string {
  const lines = [
    `# 道法时政即将到期报表（${today}，提前 ${warnDays} 天）`,
    "",
    "只列条目编码、ID、挂接观点与失效日期（不含摘要与来源正文）。已过期的条目对只读角色已不可见（0026），仍需在审核台下架（retired）。",
    "",
    `已过期 ${rows.filter((r) => r.state === "expired").length} 条，即将到期 ${rows.filter((r) => r.state === "expiring").length} 条。`,
    "",
    "| 状态 | 条目 | ID | 挂接观点 | 失效日期 | 剩余天数 |",
    "|---|---|---|---|---|---|",
    ...rows.map((r) => `| ${r.state === "expired" ? "已过期" : "即将到期"} | ${r.key} | ${r.id} | ${r.node_ids.join("、")} | ${r.valid_until} | ${r.days_left} |`),
    "",
  ];
  return lines.join("\n");
}
