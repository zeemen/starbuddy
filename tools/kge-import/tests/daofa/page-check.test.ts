// 冲刺 4-K 页码抽样核验的单测：固定种子抽样可复现、核验单不含原文、回填校验、低于阈值时该版次不能标 verified（全部合成数据）。
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHECKLIST_COLUMNS, csvField, gateEditions, parseBackfill, parseCsv, renderBackfillSql, renderChecklist, samplePages, sampleSize, type BackfillRow,
} from "../../src/daofa/page-check.ts";
import type { SnapshotProjection } from "../../src/daofa/snapshot.ts";

const proj = (edition: string, n: number): SnapshotProjection[] => Array.from({ length: n }, (_, i) => ({
  id: `00000000-0000-4000-8000-${edition === "syn-ed-a" ? "a" : "b"}${String(i).padStart(11, "0")}`,
  node_id: `MORAL.LAW.SYN_BC.VP_${String(i % 7)}`, edition, edition_year: 2024, volume: "（合成）册甲", chapter: "（合成）章, 带逗号",
  page_from: 10 + i, page_to: 11 + i, mapping_state: "unverified" as const,
}));
const ALL = [...proj("syn-ed-a", 120), ...proj("syn-ed-b", 7)];

const fill = (rows: ReturnType<typeof samplePages>, fails: number, edition = "syn-ed-a"): string => {
  let k = 0;
  return [CHECKLIST_COLUMNS.join(","), ...rows.map((r) => {
    const bad = r.edition === edition && k++ < fails;
    return renderChecklist([r]).split("\n")[1]!.replace(/,,,,$/, `,${bad ? "fail" : "pass"},syn-checker-01,2026-09-30,${bad ? "（合成）差一页" : ""}`);
  })].join("\n") + "\n";
};

test("样本量：每版次 20%（向上取整）、至少 20 条、不超过总数", () => {
  assert.deepEqual([0, 5, 20, 21, 100, 101, 120, 200].map((n) => sampleSize(n)), [0, 5, 20, 20, 20, 21, 24, 40]);
  assert.equal(sampleSize(100, 0.5, 10), 50);
  assert.throws(() => sampleSize(10, 0), /抽样比例/);
  assert.throws(() => sampleSize(10, 0.2, 0), /最少抽样条数/);
});

test("固定种子抽样：可复现、与输入顺序无关、逐版次；换种子结果不同；核验单不含原文", () => {
  const a = samplePages(ALL);
  assert.deepEqual(samplePages([...ALL].reverse()), a);
  assert.equal(a.filter((r) => r.edition === "syn-ed-a").length, 24);
  assert.equal(a.filter((r) => r.edition === "syn-ed-b").length, 7);
  assert.equal(new Set(a.map((r) => r.projection_id)).size, a.length);
  assert.notDeepEqual(samplePages(ALL, { seed: "another-seed" }).map((r) => r.projection_id), a.map((r) => r.projection_id));
  assert.equal(a[0]!.sample_id, "syn-ed-a-001");
  const csv = renderChecklist(a);
  assert.equal(csv, renderChecklist(samplePages(ALL)));
  assert.equal(csv.split("\n")[0], CHECKLIST_COLUMNS.join(","));
  assert.ok(!/quote|原句|原文/i.test(csv));
  assert.ok(csv.includes('"（合成）章, 带逗号"'), "含逗号的字段加引号");
  assert.deepEqual(parseCsv('a,"b,""c"""\n1,2\n'), [["a", 'b,"c"'], ["1", "2"]]);
});

test("回填校验：样本一一对应、核验单的列不许改、result / checker / checked_on / note 格式", () => {
  const s = samplePages(ALL);
  assert.equal(parseBackfill(fill(s, 0), s).length, s.length);
  const lines = fill(s, 0).trimEnd().split("\n");
  const rejects = (text: string, re: RegExp, why: string) => assert.throws(() => parseBackfill(text, s), re, why);
  rejects(lines.slice(0, -1).join("\n"), /缺 1 个样本/, "少一行");
  rejects([...lines, lines[1]].join("\n"), /重复/, "多一行重复");
  rejects([lines[0]!.replace("note", "quote"), ...lines.slice(1)].join("\n"), /表头/, "表头改了");
  const cells = parseCsv(lines[1]!)[0]!;
  cells[6] = "9999";
  rejects([lines[0], cells.map(csvField).join(","), ...lines.slice(2)].join("\n"), /page_from 与核验单不一致/, "改页码");
  rejects([lines[0], lines[1]!.replace(",pass,", ",ok,"), ...lines.slice(2)].join("\n"), /result 只能是 pass 或 fail/, "result 取值");
  rejects([lines[0], lines[1]!.replace("syn-checker-01", "张老师"), ...lines.slice(2)].join("\n"), /工号/, "checker 写姓名");
  rejects([lines[0], lines[1]!.replace("2026-09-30", "2026/09/30"), ...lines.slice(2)].join("\n"), /checked_on/, "日期格式");
  rejects([lines[0], lines[1]!.replace(/,$/, `,${"字".repeat(101)}`), ...lines.slice(2)].join("\n"), /note 不超过 100 字/, "备注过长");
  rejects([lines[0], lines[1]!.replace("syn-ed-a-001", "syn-ed-a-999"), ...lines.slice(2)].join("\n"), /不在核验单里/, "样本编号不在核验单");
});

test("判定：通过率 ≥ 95% 才能整体标 verified（等于算过）；低于阈值拒绝生成标 verified 的 SQL", () => {
  const s = samplePages(ALL); // syn-ed-a 24 条，syn-ed-b 7 条
  // 24 条里 1 条不通过：23/24 = 95.83% ≥ 95%，可以；2 条不通过：22/24 = 91.67%，不行
  const d1 = gateEditions(ALL, parseBackfill(fill(s, 1), s));
  assert.deepEqual(d1.map((d) => [d.edition, d.passed, d.sampled, d.can_verify]), [["syn-ed-a", 23, 24, true], ["syn-ed-b", 7, 7, true]]);
  const b2 = parseBackfill(fill(s, 2), s);
  const d2 = gateEditions(ALL, b2);
  assert.deepEqual(d2.map((d) => [d.edition, d.pass_rate, d.can_verify]), [["syn-ed-a", 0.9167, false], ["syn-ed-b", 1, true]]);
  // 恰好 95%：20 条里 19 条通过
  const twenty = proj("syn-ed-a", 20);
  const s20 = samplePages(twenty);
  assert.equal(gateEditions(twenty, parseBackfill(fill(s20, 1), s20))[0]!.can_verify, true, "19/20 = 95% 算达标");
  assert.equal(gateEditions(twenty, parseBackfill(fill(s20, 2), s20))[0]!.can_verify, false, "18/20 = 90% 不达标");
  assert.equal(gateEditions(twenty, [])[0]!.can_verify, false, "没有样本的版次不能标");
  assert.equal(gateEditions(twenty, parseBackfill(fill(s20, 2), s20), 0.9)[0]!.can_verify, true, "阈值可调");

  const base = { mapVersionId: "00000000-0000-4000-8000-000000000001", projections: ALL, backfill: b2, decisions: d2, approver: "syn-approver-01", approvedOn: "2026-09-30" };
  assert.throws(() => renderBackfillSql({ ...base, verifyEditions: ["syn-ed-a"] }), /低于阈值，整体不能标 verified/);
  const sql = renderBackfillSql(base);
  const aIds = ALL.filter((p) => p.edition === "syn-ed-a").map((p) => p.id);
  const failedIds = b2.filter((b: BackfillRow) => b.result === "fail").map((b) => b.projection_id);
  assert.ok(failedIds.every((id) => sql.includes(id)), "不通过的样本标 disputed");
  const verifiedLines = sql.split("\n").filter((l) => l.includes("mapping_state = 'verified'"));
  assert.ok(verifiedLines.length > 0 && verifiedLines.every((l) => !aIds.some((id) => l.includes(id))), "低于阈值的版次不出现在标 verified 的语句里");
  // 达标版次：通过的样本记核验人，未抽到的记批准人
  const ok = renderBackfillSql({ ...base, backfill: parseBackfill(fill(s, 1), s), decisions: d1 });
  assert.match(ok, /verified_by = 'syn-checker-01'/);
  assert.match(ok, /verified_by = 'syn-approver-01'/);
  assert.equal(ok.split("\n").filter((l) => l.includes("'disputed'")).length, 1);
  assert.ok(!/quote_text/.test(ok), "回填不写原句");
  assert.throws(() => renderBackfillSql({ ...base, approver: "张老师" }), /批准人工号/);
});
