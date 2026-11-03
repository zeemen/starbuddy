// 家长端“月度计划”页的文案（PLT-14 第 3 步）：全部是 {{TODO_COPY:monthly_plan.page.*}} 占位，人写人审后替换。
// 只描述家长能看到什么、能做什么，不对孩子作评价；分差只有区间，与“下一步做什么”（本页的学习计划）同屏；
// 不出现名次、录取概率、提分承诺、同伴比较，“偏科”不作为标签（规则 11、12；spec 硬约束）。学生端不引用本文件。

export const MONTHLY_PLAN = {
  entry: '{{TODO_COPY:monthly_plan.page.entry}} 月度计划',
  heading: '{{TODO_COPY:monthly_plan.page.heading}} 月度计划',
  month: '{{TODO_COPY:monthly_plan.page.month}} 月份',
  generated: '{{TODO_COPY:monthly_plan.page.generated}} 生成日期',
  unavailable: '{{TODO_COPY:monthly_plan.page.unavailable}} 暂时无法获取',
  notEntitled: '{{TODO_COPY:monthly_plan.page.not_entitled}} 当前套餐不含月度计划（升级说明待定稿）',
  goalsHeading: '{{TODO_COPY:monthly_plan.page.goals_heading}} 本月诉求摘要',
  goalsIntro: '{{TODO_COPY:monthly_plan.page.goals_intro}} 只读；孩子看不到；只用来安排计划的先后',
  goalsEdit: '{{TODO_COPY:monthly_plan.page.goals_edit}} 修改我的诉求',
  goalsNone: '{{TODO_COPY:monthly_plan.page.goals_none}} 还没有填写诉求',
  goalsOff: '{{TODO_COPY:monthly_plan.page.goals_off}} 诉求功能还没有开放',
  focus: '{{TODO_COPY:monthly_plan.page.focus}} 关注学科',
  target: '{{TODO_COPY:monthly_plan.page.target}} 学校考试目标分',
  school: '{{TODO_COPY:monthly_plan.page.school}} 关注学校',
  reviewHeading: '{{TODO_COPY:monthly_plan.page.review_heading}} 上月回顾',
  reviewNone: '{{TODO_COPY:monthly_plan.page.review_none}} 没有上个月的计划',
  litHeading: '{{TODO_COPY:monthly_plan.page.lit_heading}} 上月计划里已点亮的知识点',
  gapsHeading: '{{TODO_COPY:monthly_plan.page.gaps_heading}} 目标分与最近一次成绩',
  gapsNone: '{{TODO_COPY:monthly_plan.page.gaps_none}} 没有可对照的目标分与成绩',
  gapBand: '{{TODO_COPY:monthly_plan.page.gap_band}} 相差（分，区间）',
  gapReached: '{{TODO_COPY:monthly_plan.page.gap_reached}} 已达到目标分',
  gapNext: '{{TODO_COPY:monthly_plan.page.gap_next}} 下一步见本页的学习计划',
  examDate: '{{TODO_COPY:monthly_plan.page.exam_date}} 考试日期',
  planHeading: '{{TODO_COPY:monthly_plan.page.plan_heading}} 孩子本月的学习计划',
  notesHeading: '{{TODO_COPY:monthly_plan.page.notes_heading}} 家长注意事项',
  notesNone: '{{TODO_COPY:monthly_plan.page.notes_none}} 本月没有注意事项',
  back: '{{TODO_COPY:monthly_plan.page.back}} 返回孩子页',
} as const;
