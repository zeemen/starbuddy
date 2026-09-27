// 合成夹具：模拟阿鸦 legacy 的 lib/types.ts（全部是虚构数据）
export type NodeKind = "K" | "S";
export interface KnowledgeNode { id: string; name: string; kind: string; cluster: string; clusterName: string; core: string; prerequisites: string[]; abilities: string[]; evidence: string }
export const BOARDS = ["函数", "统计"] as const;
export const ABILITY_DIMS = ["运算能力", "数据分析"] as const;
export const ROOT_CAUSES = ["概念错误", "运算错误"] as const;
export interface NodeMastery { nodeId: string; p: number; gate: string }
export interface StudentProfile { nodeMastery: Record<string, NodeMastery>; rootCauseCounts: Record<string, number> }
