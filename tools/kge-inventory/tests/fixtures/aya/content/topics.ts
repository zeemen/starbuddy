// 合成夹具：两个专题。结构刻意包含环、传递边、悬空边、自然语言前置、孤点、未登记类型 C。
import type { KnowledgeNode } from "@/lib/types";

const n = (id: string, kind: string, prerequisites: string[], abilities: string[] = ["C-OPR-01"]): KnowledgeNode => ({
  id, name: `节点${id}`, kind, cluster: "FIX", clusterName: "夹具簇", core: "核心", prerequisites, abilities, evidence: "证据",
});
const q = (id: string, mainNodes: string[], extra: Record<string, unknown> = {}) => ({
  id, usage: "D", format: "single", board: "函数", mainNodes, auxNodes: [], preNodes: [],
  abilities: { "C-OPR-01": 2 }, options: [{ key: "A", traps: ["E-FIX-001"] }], difficulty: { familiarity: 1 },
  reviewStatus: "Q1", ...extra,
});

export const TOPICS = [
  {
    id: "fun", name: "二次函数", chapterLabel: "苏科版九下 · 第5章", board: "函数",
    nodes: [
      n("K-FIX-0901", "K", []),
      n("K-FIX-0902", "K", ["K-FIX-0901"]),
      n("K-FIX-0903", "S", ["K-FIX-0902", "K-FIX-0901"]), // 0901→0903 是传递边
      n("K-FIX-0904", "S", ["K-FIX-0905"]), // 0904↔0905 成环
      n("K-FIX-0905", "S", ["K-FIX-0904", "一元一次方程"]), // 自然语言前置
      n("K-FIX-0906", "C", ["K-FIX-0999"]), // 悬空边；类型 C 不在 NodeKind
      n("K-FIX-0907", "K", []), // 孤点
    ],
    questions: [
      q("Q-1", ["K-FIX-0903"]),
      q("Q-2", ["K-FIX-0903"]),
      q("Q-3", ["K-FIX-0903"], { region: "nanjing" }),
      q("Q-4", ["K-FIX-0902", "不存在的节点"]),
    ],
  },
  {
    id: "sta", name: "统计", chapterLabel: "苏科版八上 · 第3章", board: "统计",
    nodes: [n("K-FIX-0801", "K", [], ["C-DAT-01"])],
    questions: [q("Q-5", ["K-FIX-0801"], { board: "统计", abilities: { "C-DAT-01": 1 } })],
  },
];
