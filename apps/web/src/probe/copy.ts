// “能做对吗”探针与演示页的全部文案（冲刺 2-L，spec 设计要点 3、待人确认 #7）。
// 面向学生的话术，属于 CLAUDE.md 必停项：定稿前需人设评审；tests/probe.test.ts 用点点禁语表
// （evals/persona/diandian/lexicon/forbidden.yaml）扫描这里的每一条。只问预测，不打分、不评价、不显示历史。
export type Prediction = 'yes' | 'no' | 'unsure';

export const PROBE_COPY = {
  question: '这道题你觉得能做对吗？',
  skip: '跳过',
} as const;

/** 三个选项；编码与 BFF / 库里的 prediction 取值一致 */
export const PREDICTION_OPTIONS: readonly { code: Prediction; label: string }[] = [
  { code: 'yes', label: '能' },
  { code: 'no', label: '不能' },
  { code: 'unsure', label: '不确定' },
];

/** 开发环境演示页（只在开发、测试构建里存在）的文案 */
export const DEMO_COPY = {
  title: '探针演示（开发环境）',
  note: '只用于本地开发与测试，数据全部是合成的；正式做题页随冲刺 4 的统一外壳接入。',
  itemHeading: '题目',
  itemPlaceholder: (itemId: string) => `合成题 ${itemId}（演示页不显示题干）`,
  end: '结束',
  ended: '本次已结束。',
  notSaved: '这次没有记下来，不影响做题。',
} as const;
