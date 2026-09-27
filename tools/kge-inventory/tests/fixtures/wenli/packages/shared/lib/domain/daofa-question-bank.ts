// 合成夹具：student_samples 里是虚构样卷，报告里不得出现其中任何文字
export const DAOFA_QUESTIONS = [
  { id: "q1", knowledge_nodes: ["l1"], rubrics: [
    { point_id: "p1", score_value: 2, subject_dimension: ["x"], core_keywords: ["k"], required_logic: true, material_linkage: true, partial_credit_rule: "r", common_errors: [] },
    { point_id: "p2", score_value: 2, subject_dimension: ["x"], core_keywords: ["k"], required_logic: false, partial_credit_rule: "r", common_errors: [] },
  ], student_samples: [{ name: "虚构样卷甲同学", answer_text: "这是一段虚构的样卷作答文字不应出现在报告" }] },
  { id: "q2", rubric_version: "1.0", knowledge_nodes: ["zz"], rubrics: [], student_samples: [] },
];
