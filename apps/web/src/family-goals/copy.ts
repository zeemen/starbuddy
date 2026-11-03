// 家长诉求页的文案（PLT-14 第 1 步）：全部是 {{TODO_COPY:family_goals.*}} 占位，人写人审后替换（spec“文案全部模板渲染”）。
// 只描述家长要填什么，不对孩子作任何评价、不出现名次、录取概率、提分承诺、同伴比较（规则 11、12；spec 硬约束）。
// “偏科”不作为标签出现。占位键本身不是泄露（e2e/dom-leak.ts 先去掉占位再核对）。

export const FAMILY_GOALS = {
  entry: '{{TODO_COPY:family_goals.entry}} 我的诉求',
  heading: '{{TODO_COPY:family_goals.heading}} 我的诉求',
  intro: '{{TODO_COPY:family_goals.intro}} 这里填的是家长自己的想法，孩子看不到；只用来安排学习计划的先后。',
  focusLegend: '{{TODO_COPY:family_goals.focus_legend}} 最想关注的学科（最多 2 科）',
  focusNone: '{{TODO_COPY:family_goals.focus_none}} 孩子还没有在读的学科',
  targetLegend: '{{TODO_COPY:family_goals.target_legend}} 学校考试分科目标分（可不填）',
  targetHint: '{{TODO_COPY:family_goals.target_hint}} 按学校月考、期中、期末的分科分填写，不是中考总分',
  fullScore: '{{TODO_COPY:family_goals.full_score}} 满分',
  targetScore: '{{TODO_COPY:family_goals.target_score}} 目标分',
  schoolLabel: '{{TODO_COPY:family_goals.school_label}} 关注的高中学校',
  schoolNone: '{{TODO_COPY:family_goals.school_none}} 不选',
  schoolNotYet: '{{TODO_COPY:family_goals.school_not_yet}} 初三起可以选择关注的学校',
  schoolEmpty: '{{TODO_COPY:family_goals.school_empty}} 学校名单还没有发布',
  noteLabel: '{{TODO_COPY:family_goals.note_label}} 补充说明（200 字以内）',
  noteHint: '{{TODO_COPY:family_goals.note_hint}} 请写具体的学习安排，不写对孩子的评价',
  save: '{{TODO_COPY:family_goals.save}} 保存',
  current: (v: number) => `{{TODO_COPY:family_goals.current}} 当前是第 ${v} 版`,
  none: '{{TODO_COPY:family_goals.none}} 还没有填写',
  historyHeading: '{{TODO_COPY:family_goals.history_heading}} 以前的版本',
  historyItem: (v: number, day: string) => `{{TODO_COPY:family_goals.history_item}} 第 ${v} 版（${day}）`,
  unavailable: '{{TODO_COPY:family_goals.unavailable}} 暂时无法获取',
  back: '{{TODO_COPY:family_goals.back}} 返回孩子页',
} as const;

/** 提交结果码 → 提示（BFF 的问题码原样映射；不认识的走 INTERNAL） */
export const FAMILY_GOALS_RESULT: Record<string, string> = {
  saved: '{{TODO_COPY:family_goals.result_saved}} 已保存',
  INVALID: '{{TODO_COPY:family_goals.result_invalid}} 填写的内容不合要求，请检查',
  TEXT_NOT_ACCEPTED: '{{TODO_COPY:family_goals.result_text_not_accepted}} 补充说明里有不能保存的表述，请改写成具体的学习安排',
  SCHOOL_NOT_OPEN_FOR_GRADE: '{{TODO_COPY:family_goals.result_school_not_open}} 初三起才能选择关注的学校',
  GOAL_GRADE_NOT_SUPPORTED: '{{TODO_COPY:family_goals.result_grade}} 当前年级不能填写',
  TOO_MANY: '{{TODO_COPY:family_goals.result_too_many}} 今天修改次数较多，请明天再试',
  TOO_MANY_REQUESTS: '{{TODO_COPY:family_goals.result_too_many}} 操作太频繁，请稍后再试',
  CONFLICT: '{{TODO_COPY:family_goals.result_conflict}} 刚刚有另一次保存，请刷新后再试',
  CSRF: '{{TODO_COPY:family_goals.result_csrf}} 页面已过期，请刷新后再试',
  NOT_FOUND: '{{TODO_COPY:family_goals.result_not_found}} 没有找到',
  UNAVAILABLE: '{{TODO_COPY:family_goals.result_unavailable}} 暂时无法保存，请稍后再试',
  INTERNAL: '{{TODO_COPY:family_goals.result_internal}} 暂时无法保存，请稍后再试',
};
