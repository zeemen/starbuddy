// 测试夹具：全部合成（节点名、题目文本都是编的），不读 legacy，不含任何学生数据。
import type { LegacyBank, LegacyQuestion } from "../src/legacy-items.ts";
import type { MapInput, MapNode } from "../src/map-version.ts";
import { parseSegmentConfig } from "../src/segments.ts";

export const MV = "11111111-2222-4333-8444-555555555555";

const node = (id: string, level: MapNode["level"], parentId: string | null, name: string, kind: string | null = level === "node" ? "kp" : null): MapNode =>
  ({ id, level, parentId, kind, name, includes: [], examples: [], cognitiveLevels: [] });

export function synthMap(): MapInput {
  const nodes: MapNode[] = [
    node("MATH.FUNC", "domain", null, "函数"),
    node("MATH.FUNC.FUN_CON", "topic", "MATH.FUNC", "（合成）概念"),
    node("MATH.FUNC.FUN_VTX", "topic", "MATH.FUNC", "（合成）顶点式"),
    node("MATH.FUNC.FUN_CON.K_FUN_0001", "node", "MATH.FUNC.FUN_CON", "二次函数的概念"),
    node("MATH.FUNC.FUN_CON.K_FUN_0005", "node", "MATH.FUNC.FUN_CON", "自变量取值范围"),
    node("MATH.FUNC.FUN_VTX.K_FUN_0002", "node", "MATH.FUNC.FUN_VTX", "顶点式"),
    node("MATH.FUNC.FUN_VTX.M_FUN_0006", "node", "MATH.FUNC.FUN_VTX", "配方法", "method"),
    node("MATH.FUNC.FUN_VTX.K_FUN_0007", "node", "MATH.FUNC.FUN_VTX", "对称轴"),
  ];
  nodes[3]!.cognitiveLevels = ["understand"];
  return {
    mapVersionId: MV, version: "0.1.0", subject: "aya", nodes: nodes.sort((a, b) => (a.id < b.id ? -1 : 1)),
    idMigration: new Map([
      ["K-FUN-0001", "MATH.FUNC.FUN_CON.K_FUN_0001"], ["K-FUN-0002", "MATH.FUNC.FUN_VTX.K_FUN_0002"],
      ["K-FUN-0005", "MATH.FUNC.FUN_CON.K_FUN_0005"], ["M-FUN-0006", "MATH.FUNC.FUN_VTX.M_FUN_0006"],
      ["K-FUN-0007", "MATH.FUNC.FUN_VTX.K_FUN_0007"],
    ]),
  };
}

const qq = (id: string, topic: string | null, mainNodes: string[], auxNodes: string[] = [], preNodes: string[] = [], text = "（合成题）"): LegacyQuestion =>
  ({ id, topic, unit: "U01", unitName: "（合成单元）", text, mainNodes, auxNodes, preNodes });

export function synthBank(): LegacyBank {
  return {
    topics: [
      { id: "fun", name: "（合成）二次函数", chapterLabel: "苏科版九下 · 第5章", nodes: [
        { id: "K-FUN-0001", name: "二次函数的概念", kind: "K", cluster: "FUN-CON" },
        { id: "K-FUN-0002", name: "顶点式", kind: "K", cluster: "FUN-VTX" },
        { id: "P-FUN-0003", name: "开口方向", kind: "P", cluster: "FUN-VTX" },
        { id: "C-FUN-0004", name: "（合成）能力子节点", kind: "C", cluster: "FUN-MOD" },
        { id: "K-FUN-0005", name: "自变量取值范围", kind: "K", cluster: "FUN-CON" },
        { id: "M-FUN-0006", name: "配方法", kind: "M", cluster: "FUN-VTX" },
        { id: "K-FUN-0007", name: "对称轴", kind: "K", cluster: "FUN-VTX" },
      ] },
      { id: "pyt", name: "（合成）勾股定理", chapterLabel: "苏科版八上 · 第3章", nodes: [
        { id: "K-PYT-0001", name: "勾股定理", kind: "K", cluster: "PYT-THM" },
      ] },
    ],
    questions: [
      qq("Q-FUN-U01-D-001", "fun", ["K-FUN-0001"], ["K-FUN-0002"], ["整式化简"], "二次函数的概念 判断"),
      qq("Q-FUN-U02-D-001", "fun", ["P-FUN-0003"]),
      qq("Q-FUN-U03-D-001", "fun", ["K-PYT-0001"]),
      qq("Q-FUN-U04-D-001", "fun", ["配方法"], [], ["勾股定理"]),
      qq("Q-FUN-U05-D-001", "fun", ["K-FUN-0001", "K-FUN-0002"], ["K-FUN-0005", "K-FUN-0007"]),
      qq("Q-FUN-U06-D-001", "fun", ["K-FUN-0002", "P-FUN-0003"]),
      qq("Q-FUN-U07-D-001", "fun", ["C-FUN-0004"]),
      qq("Q-PYT-U01-D-001", "pyt", ["K-PYT-0001"]),
      qq("Q-SPR-QF-S1", null, ["K-FUN-0002", "P-FUN-0003"]),
      qq("Q-SPR-QF-V1", null, ["K-FUN-0002"]),
    ],
    sprintCategories: [{ id: "qf", name: "（合成）二次函数综合", questionIds: ["Q-SPR-QF-S1", "Q-SPR-QF-V1"] }],
  };
}

export const SEG_YAML = `# 合成映射表
version: 1
defaults:
  region: nanjing
  exam_year: 2027
  subject: aya
  content_version: 0.1.0
chapters:
  fun:
    label: "苏科版九下 · 第5章"
    grade: 9
    term: lower
    chapter: 5
    textbook_version: suke-2024
  pyt:
    label: "苏科版八上 · 第3章"
    grade: 8
    term: upper
    chapter: 3
    textbook_version: suke-2024
`;
export const synthSegments = () => parseSegmentConfig(SEG_YAML);
