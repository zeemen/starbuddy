// 数学自适应诊断组件与演示页的全部文案（SIE-02a，spec 设计要点 5、待人确认 #8）。
// 面向学生的话术，属于 CLAUDE.md 必停项：定稿前需人设评审；tests/diagnosis.test.ts 用点点禁语表
// （evals/persona/diandian/lexicon/forbidden.yaml）扫描这里的每一条，另查一遍不评价、不给结论的用词。
// 诊断中不显示对错之外的任何评价（本组件连对错也不显示），结束页只说“完成了，老师会看你的过程”，不给结论（规则 11）。

export const DIAGNOSIS_COPY = {
  heading: '数学：二次函数',
  intro: '接下来有一组二次函数的题，按自己的节奏做，可以随时暂停，下次接着做。',
  starting: '正在准备题目……',
  timerLabel: '已用时间',
  timerOf: (used: string, budget: string) => `${used}，共 ${budget}`,
  stepLabel: (seq: number) => `第 ${seq} 步`,
  stemHeading: '题目',
  choiceLegend: '选一个答案',
  choiceLegendMulti: '选出所有合适的答案',
  answerLabel: '答案',
  processHint: '这道题请把解题过程一步一步写下来，最后写出答案。',
  processLabel: '解题过程',
  explainHint: '这道题请用自己的话讲给老师听：你是怎么想的，为什么这样做。最后写出答案。',
  explainLabel: '讲给老师听',
  submit: '提交',
  submitting: '正在提交……',
  pause: '暂停',
  paused: '已暂停，暂停期间不计时。',
  resume: '继续',
  finished: '完成了，老师会看你的过程。',
  notSaved: '这次没有提交上，请再点一次。',
  unavailable: '现在还不能开始，请稍后再来。',
  needAnswer: '先写好答案再提交。',
  needWork: '先写好过程再提交。',
} as const;

/** 开发环境演示页（只在开发、测试构建里存在）的文案 */
export const DIAGNOSIS_DEMO_COPY = {
  title: '数学诊断演示（开发环境）',
  note: '只用于本地开发与测试：题面是合成的占位文字，数据全部是合成的；正式做题页随冲刺 4 的统一外壳接入。',
} as const;

/** 全部面向学生的文案（禁语扫描用） */
export function allDiagnosisCopy(): string[] {
  const c = DIAGNOSIS_COPY;
  return [
    c.heading, c.intro, c.starting, c.timerLabel, c.timerOf('03:20', '35:00'), c.stepLabel(3), c.stemHeading, c.choiceLegend,
    c.choiceLegendMulti, c.answerLabel, c.processHint, c.processLabel, c.explainHint, c.explainLabel, c.submit, c.submitting, c.pause,
    c.paused, c.resume, c.finished, c.notSaved, c.unavailable, c.needAnswer, c.needWork,
    DIAGNOSIS_DEMO_COPY.title, DIAGNOSIS_DEMO_COPY.note,
  ];
}

/** mm:ss（不到一小时）；超过一小时 h:mm:ss */
export function formatDuration(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const two = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${two(m)}:${two(r)}` : `${two(m)}:${two(r)}`;
}
