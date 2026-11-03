// coverage-db.sh 的辅助脚本（只用合成题目 ID）：
//   node tests/db-fixture.ts sql <地图产物目录> <打标报告> <has0024:0|1>       → 把打标报告写成 platform.item + kge.item_tag 的 INSERT
//   node tests/db-fixture.ts expected <地图产物目录> <打标报告> <has0024:0|1>  → 报表计算出的、sql/coverage.sql 应输出的全文
// 报告里的“无处可标”与不在地图里的主考点进不了库（main_node_id 非空、有外键），这类夹具在这里直接拒绝。
import { computeCoverage } from "../src/coverage.ts";
import { loadMapDir } from "../src/map.ts";
import { csvField, toCsv } from "../src/render.ts";
import { loadTaggingReport } from "../src/tagging.ts";

const CV = "e0000000-0000-4000-8000-0000000000c1"; // 合成的六段标识
const q = (s: string) => `'${s.replaceAll("'", "''")}'`;
const arr = (xs: string[]) => `ARRAY[${xs.map(q).join(", ")}]::kge.node_id[]`;
const csv = (header: string[], rows: (string | number)[][]) =>
  [header.join(","), ...rows.map((r) => r.map(csvField).join(","))].map((l) => `${l}\n`).join("");

const [mode, mapDir, taggingPath, has0024] = process.argv.slice(2);
if (!mode || !mapDir || !taggingPath) throw new Error("用法：node tests/db-fixture.ts <sql|expected> <地图产物目录> <打标报告> <0|1>");
const map = loadMapDir(mapDir);
const tagging = loadTaggingReport(taggingPath);
const with0024 = has0024 === "1";
const r = computeCoverage(map, tagging, "item_tag"); // 库里只有写进 item_tag 的题
if (r.unplaceable.length + r.mainNotInMap.length + r.relatedNotInMap.length > 0) throw new Error("库上比对的夹具不能含无处可标或不在地图里的节点");

if (mode === "sql") {
  const out = [
    `INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version) VALUES (${q(CV)}, 'nanjing', 2027, 9, 'suke-2024', 'aya', '0.0.1');`,
  ];
  for (const it of tagging.items.filter((x) => x.status === null || x.status === "item_tag")) {
    out.push(`INSERT INTO platform.item (subject, item_id, curriculum_version_id) VALUES ('aya', ${q(it.item_id)}, ${q(CV)});`);
    const cols = ["map_version_id", "subject", "item_id", "main_node_id", "node_ids", "cognitive_level", "source_kind"];
    const vals = [q(map.mapVersionId), "'aya'", q(it.item_id), q(it.main_node_id!), arr(it.node_ids),
      it.cognitive_level === null ? "NULL" : q(it.cognitive_level), "'adapted'"];
    if (with0024 && it.layer !== null) {
      cols.push("tag_source", "tag_confidence", "tagger_version", "tag_model_version");
      vals.push(q(it.layer), String(it.confidence ?? 0), "'kge-tagger-syn@0.0.0'", it.layer === "model" ? "'model:fake/syn-tagger@0.0.0'" : "NULL");
    }
    out.push(`INSERT INTO kge.item_tag (${cols.join(", ")}) VALUES (${vals.join(", ")});`);
  }
  process.stdout.write(`${out.join("\n")}\n`);
} else if (mode === "expected") {
  let s = "## matrix\n" + toCsv(r);
  s += "## summary\n" + csv(["covered_nodes", "total_nodes", "items"], [[r.coveredCount, r.rows.length, r.itemCount]]);
  s += "## uncovered\n" + csv(["node_id", "name", "kind"], r.uncovered.map((n) => [n.id, n.name, n.kind ?? ""]));
  s += "## node_count\n" + csv(["nodes", "items"], r.nodeCountDistribution.map((x) => [x.key, x.count]));
  s += "## over_limit\n" + csv(["item_id", "nodes"], r.overLimitItems.map((x) => [x.item_id, x.nodes]));
  if (with0024) {
    s += "## item_layer\n" + csv(["layer", "items"], r.itemLayerShares.map((x) => [x.key, x.count]));
    s += "## candidate_layer\n" + csv(["layer", "candidates"], []); // 库上比对不写候选表
  } else {
    s += "## item_layer\n（跳过：库里还没有迁移 0024 的 tag_source 列）\n## candidate_layer\n（跳过：库里还没有迁移 0024 的 kge.tag_candidate 表）\n";
  }
  process.stdout.write(s);
} else {
  throw new Error(`未知模式 ${mode}`);
}
