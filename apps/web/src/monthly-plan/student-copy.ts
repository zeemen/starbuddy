// 学习计划的文案（PLT-14 第 3 步）：学生端“本月计划”页与家长端页面里的“学习计划”一段共用。全部是 {{TODO_COPY:monthly_plan.*}} 占位，
// 人写人审后替换。只描述“做什么”（知识点、节奏、复测与小复评安排），不对孩子作评价，不出现名次、概率、承诺、同伴比较（规则 11、12）。
// 计划里的先后只按顺序排列展示，不编号、不写“第 N”。本文件不含任何家长诉求、注意事项、回顾与分差的文案（学生端只引用本文件）。

export const MY_PLAN = {
  entry: '{{TODO_COPY:monthly_plan.my.entry}} 本月计划',
  heading: '{{TODO_COPY:monthly_plan.my.heading}} 本月学习计划',
  back: '{{TODO_COPY:monthly_plan.my.back}} 返回今日',
  month: '{{TODO_COPY:monthly_plan.my.month}} 月份',
  empty: '{{TODO_COPY:monthly_plan.my.empty}} 本月的学习计划还没有生成',
  unavailable: '{{TODO_COPY:monthly_plan.my.unavailable}} 暂时无法获取',
} as const;

/** 学习计划一段（两端共用） */
export const PLAN_SECTION = {
  priorityHeading: '{{TODO_COPY:monthly_plan.plan.priority_heading}} 本月优先知识点',
  priorityNone: '{{TODO_COPY:monthly_plan.plan.priority_none}} 本月没有列出的知识点',
  rhythmHeading: '{{TODO_COPY:monthly_plan.plan.rhythm_heading}} 每周节奏',
  sessions: '{{TODO_COPY:monthly_plan.plan.sessions}} 每周次数',
  minutes: '{{TODO_COPY:monthly_plan.plan.minutes}} 每次分钟',
  retestsHeading: '{{TODO_COPY:monthly_plan.plan.retests_heading}} 本月复测',
  retestCount: '{{TODO_COPY:monthly_plan.plan.retest_count}} 次数',
  retestDates: '{{TODO_COPY:monthly_plan.plan.retest_dates}} 日期',
  miniHeading: '{{TODO_COPY:monthly_plan.plan.mini_heading}} 分科小复评',
  none: '{{TODO_COPY:monthly_plan.plan.none}} 暂无',
} as const;
