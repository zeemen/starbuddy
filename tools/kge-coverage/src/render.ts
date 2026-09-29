// 覆盖矩阵报表的 Markdown 与 CSV 输出。确定性：不写时间、路径、主机信息；同一输入逐字节相同。
// CSV 引号规则与 Postgres COPY ... (FORMAT csv) 相同（只在含逗号、双引号、换行时加引号），便于与 sql/coverage.sql 的输出逐字节比对。
import { COVERAGE_THRESHOLD, type CoverageResult, LEVEL_COLUMNS, MAX_NODES_PER_ITEM, pct, type Share } from "./coverage.ts";

const RED = "**【红】**";
const LEVEL_LABEL: Record<string, string> = {
  know: "知道", understand: "理解", apply: "运用", synthesize: "综合", unlabeled: "未标",
};
const LAYER_LABEL: Record<string, string> = { rule: "规则", vector: "向量", model: "模型", human: "人工", none: "无（无处可标）" };
const GRADE_LABEL: Record<string, string> = { A: "A 级", B: "B 级", none: "未分级" };

export function csvField(v: string | number): string {
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

export const CSV_HEADER = ["node_id", "name", "kind", ...LEVEL_COLUMNS, "main_total", "related_total", "covered"];

export function toCsv(r: CoverageResult): string {
  const lines = [CSV_HEADER.join(",")];
  for (const row of r.rows) {
    lines.push([row.node.id, row.node.name, row.node.kind ?? "", ...LEVEL_COLUMNS.map((c) => row.byLevel[c]),
      row.mainTotal, row.relatedTotal, row.mainTotal > 0 ? "yes" : "no"].map(csvField).join(","));
  }
  return `${lines.join("\n")}\n`;
}

// Markdown 表格单元：竖线与换行转义
const md = (s: string) => s.replaceAll("|", "\\|").replace(/\r?\n/g, " ");

function shareTable(title: string, shares: Share[], label: Record<string, string>): string[] {
  const total = shares.reduce((a, s) => a + s.count, 0);
  const out = [`| ${title} | 条数 | 占比 |`, "|---|---:|---:|"];
  for (const s of shares) out.push(`| ${md(label[s.key] ?? s.key)} | ${String(s.count)} | ${pct(s.count, total)} |`);
  out.push(`| 合计 | ${String(total)} | ${total === 0 ? "—" : "100.0%"} |`);
  return out;
}

export function toMarkdown(r: CoverageResult): string {
  const L: string[] = [];
  const rows = r.rows.length;
  L.push(`# 覆盖矩阵报表 · 地图版本 ${r.version}${r.topic ? `（专题 ${r.topic}）` : ""}`);
  L.push("");
  L.push("由 `tools/kge-coverage` 生成（生成文件，勿手改）。输入：KGE-01 地图导入产物与 KGE-02 打标报告；只读，不连库。");
  L.push(`地图版本 ID：\`${r.mapVersionId}\`；矩阵行（知识节点层与细目层的有效节点）${String(rows)} 个。`);
  L.push(r.scope === "all"
    ? `统计范围：已导入主题里的全部打标结果（A 级与 B 级候选的主考点都算），${String(r.itemCount)} 道题。`
    : `统计范围：只算写进 kge.item_tag 的题（与 sql/coverage.sql 在库里的口径一致），${String(r.itemCount)} 道题；其余 ${String(r.excludedByScope)} 道不计。`);
  L.push(`未导入主题、不打标的题：${String(r.outOfScope)} 道（不计入任何统计）。`);
  L.push(`标记说明：${RED} 表示低于门槛或超出上限。门槛只做标注，不作为本任务的通过条件（内容缺口靠教研补题，spec 设计要点 4）。`);
  L.push("");

  L.push("## 1. 门槛（KGE V1.1 :747 覆盖矩阵 100%）");
  L.push("");
  L.push(`- 有主考点题的节点：${String(r.coveredCount)} / ${String(rows)}（${pct(r.coveredCount, rows)}），门槛 ${String(COVERAGE_THRESHOLD * 100)}%：` +
    (r.meetsThreshold ? "达标" : `${RED} 未达标`));
  L.push(`- 小题节点数超过 ${String(MAX_NODES_PER_ITEM)} 个的题（KGE :746，含合并前超限后截断的）：${String(r.overLimitItems.length)} 道` + (r.overLimitItems.length > 0 ? ` ${RED}` : ""));
  L.push(`- 无处可标（没有主考点）：${String(r.unplaceable.length)} 道（${pct(r.unplaceable.length, r.itemCount)}）`);
  L.push("");

  L.push("## 2. 节点 × 认知层次（主考点题数）");
  L.push("");
  L.push("格子是以该节点为主考点的题数；“关联”是节点出现在 node_ids 里但不是主考点的题数。");
  L.push("");
  L.push(`| 节点 | 名称 | 类型 | ${LEVEL_COLUMNS.map((c) => LEVEL_LABEL[c]).join(" | ")} | 主考点合计 | 关联 | 覆盖 |`);
  L.push(`|---|---|---|${LEVEL_COLUMNS.map(() => "---:").join("|")}|---:|---:|---|`);
  for (const row of r.rows) {
    L.push(`| \`${row.node.id}\` | ${md(row.node.name)} | ${row.node.kind ?? ""} | ${LEVEL_COLUMNS.map((c) => String(row.byLevel[c])).join(" | ")} | ` +
      `${String(row.mainTotal)} | ${String(row.relatedTotal)} | ${row.mainTotal > 0 ? "是" : `${RED} 否`} |`);
  }
  L.push("");

  L.push(`## 3. 没有主考点题的节点（${String(r.uncovered.length)} 个）`);
  L.push("");
  if (r.uncovered.length === 0) L.push("无。");
  for (const n of r.uncovered) L.push(`- ${RED} \`${n.id}\` ${md(n.name)}（${n.kind ?? ""}）`);
  L.push("");

  L.push("## 4. 题目节点数分布");
  L.push("");
  L.push("节点数 = {主考点} ∪ node_ids 去重后的个数；0 表示无处可标且没有任何节点。");
  L.push("");
  L.push("| 节点数 | 题数 | 占比 |");
  L.push("|---:|---:|---:|");
  for (const s of r.nodeCountDistribution) {
    L.push(`| ${s.key}${Number(s.key) > MAX_NODES_PER_ITEM ? ` ${RED}` : ""} | ${String(s.count)} | ${pct(s.count, r.itemCount)} |`);
  }
  L.push("");
  L.push(`超过 ${String(MAX_NODES_PER_ITEM)} 个节点的题，含打标器合并前超限后截断的（${String(r.overLimitItems.length)} 道）：`);
  L.push("");
  if (r.overLimitItems.length === 0) L.push("无。");
  for (const x of r.overLimitItems) {
    L.push(`- ${RED} \`${x.item_id}\`：${String(x.nodes)} 个节点${x.overCap ? "（合并前超过上限，node_ids 已截断）" : ""}`);
  }
  L.push("");

  L.push("## 5. 候选来源与分级");
  L.push("");
  L.push(...shareTable("各层候选（全部候选）", r.candidateShares, LAYER_LABEL));
  L.push("");
  if (r.candidateRowShares) {
    L.push(...shareTable("写进 kge.tag_candidate 的候选", r.candidateRowShares, LAYER_LABEL));
    L.push("");
  }
  L.push(...shareTable("题目标签的来源层", r.itemLayerShares, LAYER_LABEL));
  L.push("");
  L.push(...shareTable("分级", r.gradeShares, GRADE_LABEL));
  L.push("");

  L.push("## 6. 需要核对的题");
  L.push("");
  const pairList = (title: string, xs: { item_id: string; node_id: string }[]) => {
    L.push(`- ${title}：${String(xs.length)} 道` + (xs.length > 0 ? `（${xs.map((x) => `\`${x.item_id}\` → \`${x.node_id}\``).join("；")}）` : ""));
  };
  L.push(`- 无处可标：${String(r.unplaceable.length)} 道` + (r.unplaceable.length > 0 ? `（${r.unplaceable.map((x) => `\`${x}\``).join("、")}）` : ""));
  pairList("主考点不在本地图版本", r.mainNotInMap);
  pairList("主考点落在领域或主题层", r.mainNotNodeLevel);
  pairList("主考点已停用", r.mainDeprecated);
  pairList("关联节点不在本地图版本", r.relatedNotInMap);
  L.push("");
  return `${L.join("\n")}\n`;
}
