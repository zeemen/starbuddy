// 生成一份**合成的**“补齐”覆盖文件，只用于证明：教研补齐工作单之后，导入的版本能通过发布前校验并发布（spec 验收第 3 条）。
// 内容全是占位文本，不是教研口径：易错点按“知识点”导入、边界字段填“（合成）”、孤点挂到一个已连通的节点下。
// 用法：node tests/import-complete-overrides.ts <阿鸦 legacy 根> <专题>，覆盖 JSON 写到 stdout。
import { build, type Overrides } from "../src/import/build.ts";
import { loadTopic } from "../src/import/main.ts";

const [aya, topic = "fun"] = process.argv.slice(2);
const legacy = loadTopic(aya!, topic);
const base: Overrides = { kind_map: { P: "kp" } };
const first = build(legacy, { topic, version: "0.2.0" }, base);
const oldOf = new Map(first.nodes.filter((n) => n.oldId).map((n) => [n.id, n.oldId!]));
const connected = new Set(first.edges.flat());
const root = first.edges[0]![0]; // 任取一个已连通的节点作为孤点的合成前置
const nodes: NonNullable<Overrides["nodes"]> = {};
for (const n of first.nodes) {
  if (!n.oldId) continue;
  const ov: NonNullable<Overrides["nodes"]>[string] = {};
  if (n.kind === "kp" || n.kind === "ability_kp") {
    Object.assign(ov, { includes: ["（合成）包含"], excludes: ["（合成）不包含"], examples: ["（合成）题例"], cognitive_levels: ["understand"] });
  }
  if (!connected.has(n.id)) ov.extra_prerequisites = [oldOf.get(root)!];
  nodes[n.oldId] = ov;
}
process.stdout.write(`${JSON.stringify({ ...base, nodes }, null, 2)}\n`);
