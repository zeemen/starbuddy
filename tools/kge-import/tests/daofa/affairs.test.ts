// 冲刺 4-K 时政录入格式、校验、种子与即将到期报表的单测（全部合成数据，来源为虚构的 .example 主机）。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  affairIdOf, expiryReport, type AffairsOptions, parseAffairs, parseSourceDomains, renderAffairsSql, renderExpiry, urlHost,
} from "../../src/daofa/affairs.ts";
import { addMonths, daysBetween } from "../../src/daofa/common.ts";

const TOOL = fileURLToPath(new URL("../../", import.meta.url));
const HOSTS = parseSourceDomains(readFileSync(`${TOOL}samples/daofa/source-domains.txt`, "utf8"));
const VPS = new Set(["MORAL.LAW.SYN_BC_A.VP_A1", "MORAL.LAW.SYN_BC_A.VP_A2", "MORAL.LAW.SYN_BC_B.VP_B2", "MORAL.SYN.LAW.MINOR_ONLINE_PROTECTION"]);
const opts: AffairsOptions = { viewpointIds: VPS, sourceDomains: HOSTS };
const entry = (over: Record<string, string> = {}, extra = "") => {
  const e = { key: "syn-x", source: "https://news.synthetic-source.example/a/b", node_ids: "[MORAL.LAW.SYN_BC_A.VP_A1]", summary: "（合成占位）自写摘要",
    valid_from: "2026-01-31", valid_until: "2026-07-31", ...over };
  return "format: kge-daofa-current-affairs/1\nsynthetic: true\nnotice: 合成、非真实时政\nsubject: zhixing\nentries:\n"
    + `  - key: ${e.key}\n    source: ${e.source}\n    node_ids: ${e.node_ids}\n    summary: ${e.summary}\n    valid_from: ${e.valid_from}\n    valid_until: ${e.valid_until}\n${extra}`;
};

test("日期运算：加月取月末、天数差", () => {
  assert.equal(addMonths("2026-01-31", 1), "2026-02-28");
  assert.equal(addMonths("2027-12-15", 12), "2028-12-15");
  assert.equal(addMonths("2028-02-29", 12), "2029-02-28");
  assert.equal(daysBetween("2026-09-30", "2026-10-30"), 30);
  assert.equal(daysBetween("2026-09-30", "2026-09-01"), -29);
  assert.equal(urlHost("https://News.Example.org/x?y"), "news.example.org");
  assert.equal(urlHost("https://news.example.org"), "news.example.org");
  assert.equal(urlHost("https://news.example.org:443/x"), null);  // 与 policy.url_host 一致：带显式端口不认（迁移 0049）
});

test("合成样例能过校验；来源白名单、有效期、只存自写摘要、挂接观点逐条拦", () => {
  const f = parseAffairs(readFileSync(`${TOOL}samples/daofa/current-affairs.yaml`, "utf8"), opts);
  assert.equal(f.entries.length, 3);
  assert.equal(parseAffairs(entry(), opts).entries[0]!.host, "news.synthetic-source.example");
  // 正好 12 个月（1 月 31 日 → 次年 1 月 31 日）可以；多一天不行
  assert.equal(parseAffairs(entry({ valid_until: "2027-01-31" }), opts).entries.length, 1);
  const rejects = (yaml: string, re: RegExp, why: string, o = opts) => assert.throws(() => parseAffairs(yaml, o), re, why);
  rejects(entry({ valid_until: "2027-02-01" }), /不能超过 12 个月/, "超过 12 个月");
  rejects(entry({ valid_until: "2026-10-31" }), /不能超过 6 个月/, "上限可以调小", { ...opts, maxMonths: 6 });
  rejects(entry({ source: "https://rogue.synthetic-source.example/a" }), /不在来源白名单/, "非白名单主机");
  rejects(entry({ source: "http://news.synthetic-source.example/a" }), /https 链接/, "http");
  rejects(entry({ source: "https://news.synthetic-source.example/a?q=1" }), /https 链接/, "带查询串");
  rejects(entry({ valid_until: "" }), /缺“valid_until”/, "缺失效日期");
  rejects(entry({ valid_from: "" }), /缺“valid_from”/, "缺生效日期");
  rejects(entry({ valid_until: "2026-01-31" }), /必须晚于/, "失效不晚于生效");
  rejects(entry({ valid_until: "2026-02-30" }), /不是有效日期/, "日期不存在");
  rejects(entry({}, "    original_text: 占位\n"), /原句或原文字段/, "转载原文字段");
  rejects(entry({}, "    full_text: 占位\n"), /原句或原文字段/, "全文字段");
  rejects(entry({ summary: "（合成）据某虚构报社报道：占位" }), /疑似转载原文/, "摘要像转载");
  rejects(entry({ summary: "x".repeat(201) }), /1–200 字/, "摘要过长");
  rejects(entry({}, "    review_status: published\n"), /不认识的键“review_status”/, "录入不能自定审核状态");
  rejects(entry({ node_ids: "[MORAL.LAW.SYN_BC_B.VP_B9]" }), /不是目标地图版本里的观点/, "挂接不存在的观点");
  rejects(entry({ node_ids: "[]" }), /1–16 个/, "没有挂接观点");
  rejects(entry().replace("synthetic: true", "synthetic: false").replace("notice: 合成、非真实时政", "notice: 教研录入").replace(/entries:[\s\S]*/, "entries: []\n"),
    /entries 必须是 1–500 条/, "空条目");
  assert.throws(() => parseSourceDomains("# 只有注释\n"), /空的/);
  assert.throws(() => parseSourceDomains("https://x.example\n"), /不是主机名/);
});

test("时政种子：一律 pending、库里再核白名单、确定性 ID；摘要不入库", () => {
  const f = parseAffairs(readFileSync(`${TOOL}samples/daofa/current-affairs.yaml`, "utf8"), opts);
  const sql = renderAffairsSql(f, { mapVersionId: "00000000-0000-4000-8000-000000000001", version: "0.0.1", source: "t" });
  assert.equal(sql, renderAffairsSql(f, { mapVersionId: "00000000-0000-4000-8000-000000000001", version: "0.0.1", source: "t" }));
  assert.equal((sql.match(/'pending'\)/g) ?? []).length, 3);
  assert.doesNotMatch(sql, /'published'/);
  assert.match(sql, /policy\.source_domain d WHERE d\.host = h AND d\.status = 'active'/);
  assert.match(sql, /^SET LOCAL ROLE starbuddy_kge;$/m);
  for (const e of f.entries) {
    assert.ok(!sql.includes(e.summary), "摘要不写进种子");
    assert.ok(sql.includes(affairIdOf("0.0.1", e.key)));
  }
});

test("即将到期报表：提前 30 天、已过期单列、与手算一致", () => {
  const f = parseAffairs(readFileSync(`${TOOL}samples/daofa/current-affairs.yaml`, "utf8"), opts);
  // 样例：syn-ca-001 到 2027-06-30；syn-ca-002 到 2026-10-15；syn-ca-003 到 2026-09-01
  // 今天 2026-09-30：003 已过期（−29 天），002 剩 15 天（≤ 30，即将到期），001 剩 273 天（不列）
  const rows = expiryReport(f, "0.0.1", "2026-09-30");
  assert.deepEqual(rows.map((r) => [r.key, r.state, r.days_left]), [["syn-ca-003", "expired", -29], ["syn-ca-002", "expiring", 15]]);
  // 边界：到期当天算已过期（0026：current_date < valid_until 才可见）；正好第 30 天算即将到期，第 31 天不列
  assert.deepEqual(expiryReport(f, "0.0.1", "2026-10-15").map((r) => [r.key, r.state]), [["syn-ca-003", "expired"], ["syn-ca-002", "expired"]]);
  assert.deepEqual(expiryReport(f, "0.0.1", "2026-09-15").map((r) => r.key), ["syn-ca-003", "syn-ca-002"]);
  assert.deepEqual(expiryReport(f, "0.0.1", "2026-09-14").map((r) => r.key), ["syn-ca-003"]);
  assert.deepEqual(expiryReport(f, "0.0.1", "2027-05-31").map((r) => r.key), ["syn-ca-003", "syn-ca-002", "syn-ca-001"]);
  assert.deepEqual(expiryReport(f, "0.0.1", "2027-05-30").map((r) => r.key), ["syn-ca-003", "syn-ca-002"]);
  const md = renderExpiry(rows, "2026-09-30", 30);
  assert.match(md, /已过期 1 条，即将到期 1 条/);
  for (const e of f.entries) assert.ok(!md.includes(e.summary) && !md.includes(e.source), "报表只含 ID，不含摘要与来源");
  assert.throws(() => expiryReport(f, "0.0.1", "2026-9-30"), /YYYY-MM-DD/);
});
