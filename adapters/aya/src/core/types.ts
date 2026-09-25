// 摘自 legacy/aya/lib/types.ts（ayaheshui 提交 0ed2fe4）。legacy 只读，本文件是副本。
// 改动：只保留判分需要的类型；Question 只保留 grading.ts 用到的字段（题干、解析、提示等不进适配器）。

/** 十二大错误根因 */
export const ROOT_CAUSES = [
  "概念错误",
  "知识缺失",
  "审题错误",
  "表征错误",
  "方法错误",
  "推理错误",
  "运算错误",
  "分类遗漏",
  "表达错误",
  "检验错误",
  "时间问题",
  "心理与习惯",
] as const;
export type RootCause = (typeof ROOT_CAUSES)[number];

/** 作答形式（MVP 支持三种） */
export type QuestionFormat = "single" | "multi" | "fill";

export interface QuestionOption {
  key: string; // A/B/C/D
  /** 选中该项触发的错误陷阱代码（错误选项才有） */
  traps?: string[];
}

export interface Question {
  id: string; // Q-EQU-U01-D-001
  format: QuestionFormat;
  options?: QuestionOption[];
  /** fill 题的标准答案（多个等价写法）；single/multi 为选项 key 集合 */
  answer: string[];
  mainNodes: string[]; // 主测节点
  /** fill 题：典型错误答案 → 陷阱代码 */
  wrongAnswerTraps?: { pattern: string[]; traps: string[] }[];
  /** 题库质量分级 Q0-Q5（11.3 生成题质量控制） */
  reviewStatus: "Q0" | "Q1" | "Q2" | "Q3" | "Q4" | "Q5";
}

/** 错误陷阱（章节级），归入十二大错误根因 */
export interface ErrorTrap {
  code: string; // E-CON-001
  rootCause: RootCause;
}
