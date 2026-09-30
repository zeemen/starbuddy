// 统一壳（H4）的全部界面文案（spec 第 8、18 节：壳的文案收在一个文件里，不含关于学生的描述）。
// 系统口吻，不以点点或任何老师的身份说话；不比较、不评价、不催促、不推断（禁语 A–G 类自查，lessons H4）；学生端不出现购买、升级、营销。
// 关于学生的句子不在这里：它们只来自洞察服务的 RenderedStatement.text（带 data-claim-id）。
// {{TODO_COPY}}：人写人审的正文（置信度说明、同意文本、信任界面入口），签字前是明显占位。
// tests/shell.test.ts 逐条过禁语扫描（personas.guard）、比较 / 营销词表与禁用推断词表。

export const NAV = { label: '主导航', today: '今日', starMap: '星图', subjects: '科目', exit: '退出学生模式', exitNote: '退出后回到登录页；家长需要重新用短信验证码登录。' } as const;

export const SUBJECT_LABEL: Record<string, string> = { math: '数学', chinese: '语文', politics: '道法' };
export const subjectLabel = (code: string): string => SUBJECT_LABEL[code] ?? code;

export const TASK_KIND: Record<string, string> = {
  diagnose: '诊断', practice: '练习', review: '复习', lesson: '微课', retest: '复测', mock_exam: '模考',
};

/** 内容类推荐理由（只描述题目或知识点，不描述学生）：BFF 给编码，这里给固定短句 */
export const CONTENT_RATIONALE: Record<string, string> = {
  high_exam_weight: '考试常考',
  prerequisite_of_many: '后面很多知识点要用到它',
};

/** 家长端置信度说明（SIE 第八章：家长看不到无置信度的推断）。正文由人写人审 */
export const CONFIDENCE_BAND: Record<string, string> = {
  few_evidence: '{{TODO_COPY}} 置信度说明：证据较少',
  moderate: '{{TODO_COPY}} 置信度说明：证据中等',
  strong: '{{TODO_COPY}} 置信度说明：证据较多',
};

export const TODAY = {
  title: '今日',
  noSubjects: '还没有在读的科目。',
  nothingToday: '今天暂无安排',
  minutes: (n: number) => `约 ${n} 分钟`,
  neutralTitle: (subject: string) => `${subjectLabel(subject)} · 本周任务`,
  rationaleLabel: '推荐理由',
} as const;

export const SUBJECTS = {
  title: '科目',
  enter: (name: string) => `进入${name}`,
  comingTitle: '即将开放',
  comingBody: '这一科还在准备中，可以先回到星图或今日。',
  backToStarMap: '回到星图',
  starSky: (name: string) => `${name}星空`,
  todayTasks: '今天的任务',
  notEnrolled: '这一科还没有开通。',
} as const;

export const BALANCE = {
  heading: '额度与时长',
  trialDaysLeft: (n: number) => (n > 0 ? `试用还剩 ${n} 天` : '试用今天到期'),
  trialAfter: (n: number) => `试用结束后，可以继续查看星图和历史记录，每天可以做 ${n} 次题库自练。`,
  retained: (k: number | null) => `可以查看星图和历史记录；今天还能做 ${k ?? 0} 次题库自练`,
  aiUnavailable: '需要连接老师的功能暂时不可用。',
  needGuardianStudent: '需要家长开通',
  notOpenedGuardian: '尚未开通',
  openPlaceholder: '开通入口即将提供。',
  minutesLeft: (n: number) => `今天还剩 ${n} 分钟`,
  capReached: '今天的陪练到这里，可以继续做题库自练',
  curfew: (start: string, end: string) => `现在是休息时间（${start}–${end}），明天 ${end} 后可以继续`,
  curfewNear: (m: number) => `还有 ${m} 分钟到休息时间`,
  unavailable: '暂时无法获取。',
  adjustCap: '调整上限',
  adjustCurfew: '调整宵禁',
  capAndCurfew: (cap: number, start: string, end: string) => `单日上限 ${cap} 分钟；休息时间 ${start}–${end}`,
  remaining: (n: number, unit: string) => `剩余 ${n}${unit === 'minute' ? ' 分钟' : unit === 'byte' ? ' 字节' : ' 次'}`,
  periodEnd: (d: string) => `本周期到 ${d}`,
} as const;

export const RESOURCE_LABEL: Record<string, string> = {
  diagnosis_screen: '诊断（筛查）', diagnosis_full: '诊断（完整）', mock_exam: '模考', mistake_analysis: '错题分析',
  lesson_whiteboard: '讲评课', voice_realtime: '语音陪练', ai_active_time: '互动时长', upload: '上传', storage_bytes: '存储空间',
  deep_report: '深度报告',
};
export const resourceLabel = (code: string): string => RESOURCE_LABEL[code] ?? code;

export const SOURCE_LABEL: Record<string, string> = { trial: '试用', pilot_virtual: '试点', paid: '订阅', addon: '附加' };

export const REST = {
  reminder: (m: number, b: number) => `已经连续使用 ${m} 分钟，可以起来活动 ${b} 分钟。`,
  ack: '知道了',
} as const;

export const DIANDIAN = {
  entry: '点点',
  placeholder: '点点正在准备中。',
} as const;

/** 选科结果（BFF 的 outcome 一一映射） */
export const ENROLL_RESULT: Record<string, string> = {
  enrolled: '已开通。',
  already_active: '已开通。',
  limit_reached: '可开通的科目数已满。',
  change_next_period: '本周期已换过科目，下个周期可以开通。',
  no_subscription: '尚未开通。',
  not_launched: '暂未开放。',
  paused: '已暂停。',
  not_active: '这一科本来没有开通。',
  change_limit_reached: '本周期已换过 1 科。',
  NOT_FOUND: '没有找到这个孩子的档案。',
  CSRF: '页面已过期，请刷新后再试。',
  TOO_MANY_REQUESTS: '操作太频繁了，请稍后再试。',
  INVALID: '填写的内容不符合要求。',
  INTERNAL: '暂时无法完成，请稍后再试。',
};
export const slotText = (slot: string | undefined, limit: number | null | undefined): string =>
  `${slot === 'elective' ? '选修' : '核心'}${typeof limit === 'number' ? `（上限 ${limit} 科）` : ''}`;

export const ENROLLMENT = {
  heading: '选科',
  rulePickOne: '核心科目任选 1 科',
  ruleAllLaunched: '已上线核心科目全部可选',
  electiveSlots: (n: string) => (n === 'all_launched' ? '选修科目全部可选' : `选修槽 ${n} 个`),
  changeRule: '每个周期可换 1 科，换上的科目下个周期生效。',
  active: '已开通',
  paused: '已暂停',
  none: '未开通',
  notOpen: '暂未开放',
  enroll: (name: string) => `开通${name}`,
  pause: (name: string) => `暂停${name}`,
  studentReadOnly: '选科由家长确认。',
} as const;

export const TIME_GUARD = {
  heading: '时长设置',
  capLabel: '单日上限（分钟）',
  capHint: (plan: number) => `可以设为 0 到 ${plan} 分钟；不填表示按档位上限 ${plan} 分钟`,
  curfewStart: '休息时间开始',
  curfewEnd: '休息时间结束',
  curfewHint: (start: string, end: string) => `休息时间只能比默认的 ${start}–${end} 更长；两个都不填表示按默认时段`,
  save: '保存',
  saved: '已保存。',
  cap_above_plan: '单日上限不能超过档位上限。',
  curfew_must_cover_default: '休息时间需要覆盖默认时段。',
} as const;
export const TIME_RESULT: Record<string, string> = {
  saved: TIME_GUARD.saved, cap_above_plan: TIME_GUARD.cap_above_plan, curfew_must_cover_default: TIME_GUARD.curfew_must_cover_default,
  NOT_FOUND: ENROLL_RESULT['NOT_FOUND']!, CSRF: ENROLL_RESULT['CSRF']!, TOO_MANY_REQUESTS: ENROLL_RESULT['TOO_MANY_REQUESTS']!,
  INVALID: ENROLL_RESULT['INVALID']!, INTERNAL: ENROLL_RESULT['INTERNAL']!,
};

export const FAMILY_SHELL = {
  childPage: '孩子页',
  childPageFor: (label: string) => `${label}的孩子页`,
  createHeading: '新建孩子档案',
  gradeLabel: '年级',
  regionLabel: '地区',
  regionDefault: 'nanjing',
  birthLabel: '出生年月（可不填）',
  create: '建档',
  consentHeading: '同意',
  consentText: '{{TODO_COPY}} 隐私政策与监护人同意的正文（合规轨提供）',
  consentGrant: '同意并记录',
  consentFor: (label: string) => `为${label}记录同意`,
  onboarding: '完善学习档案',
  timeSettings: '时长设置',
  backToFamily: '回到家长首页',
} as const;
export const FAMILY_SHELL_RESULT: Record<string, string> = {
  created: '已建档。',
  recorded: '已记录。',
  NOT_FOUND: ENROLL_RESULT['NOT_FOUND']!,
  CSRF: ENROLL_RESULT['CSRF']!,
  TOO_MANY_REQUESTS: ENROLL_RESULT['TOO_MANY_REQUESTS']!,
  INVALID: ENROLL_RESULT['INVALID']!,
  INTERNAL: ENROLL_RESULT['INTERNAL']!,
};

/** 信任界面预留位（冲刺 5 实现，功能开关默认关；开关关时不渲染）。入口文字由人写人审 */
export const TRUST_SLOT: Record<string, string> = {
  why: '{{TODO_COPY}} 为什么',
  dispute: '{{TODO_COPY}} 我不同意',
  reconcile: '{{TODO_COPY}} 对账卡',
  weekly: '{{TODO_COPY}} 周报',
  data_rights: '{{TODO_COPY}} 数据查看、导出与删除',
};

/** 全部文案（静态检查逐条扫描） */
export const ALL_SHELL_COPY: string[] = [
  ...Object.values(NAV), ...Object.values(SUBJECT_LABEL), ...Object.values(TASK_KIND), ...Object.values(CONTENT_RATIONALE),
  ...Object.values(CONFIDENCE_BAND), TODAY.title, TODAY.noSubjects, TODAY.nothingToday, TODAY.minutes(10), TODAY.neutralTitle('math'),
  TODAY.rationaleLabel, SUBJECTS.title, SUBJECTS.enter('数学'), SUBJECTS.comingTitle, SUBJECTS.comingBody, SUBJECTS.backToStarMap,
  SUBJECTS.starSky('数学'), SUBJECTS.todayTasks, SUBJECTS.notEnrolled,
  BALANCE.heading, BALANCE.trialDaysLeft(14), BALANCE.trialDaysLeft(0), BALANCE.trialAfter(3), BALANCE.retained(3), BALANCE.aiUnavailable,
  BALANCE.needGuardianStudent, BALANCE.notOpenedGuardian, BALANCE.openPlaceholder, BALANCE.minutesLeft(30), BALANCE.capReached,
  BALANCE.curfew('21:30', '06:30'), BALANCE.curfewNear(20), BALANCE.unavailable, BALANCE.adjustCap, BALANCE.adjustCurfew,
  BALANCE.capAndCurfew(90, '21:30', '06:30'),
  BALANCE.remaining(3, 'count'), BALANCE.periodEnd('2026-10-30'),
  ...Object.values(RESOURCE_LABEL), ...Object.values(SOURCE_LABEL), REST.reminder(40, 10), REST.ack, ...Object.values(DIANDIAN),
  ...Object.values(ENROLL_RESULT), slotText('core', 1), ENROLLMENT.heading, ENROLLMENT.rulePickOne, ENROLLMENT.ruleAllLaunched,
  ENROLLMENT.electiveSlots('2'), ENROLLMENT.changeRule, ENROLLMENT.active, ENROLLMENT.paused, ENROLLMENT.none, ENROLLMENT.notOpen,
  ENROLLMENT.enroll('数学'), ENROLLMENT.pause('数学'), ENROLLMENT.studentReadOnly,
  TIME_GUARD.heading, TIME_GUARD.capLabel, TIME_GUARD.capHint(90), TIME_GUARD.curfewStart, TIME_GUARD.curfewEnd, TIME_GUARD.curfewHint('21:30', '06:30'),
  TIME_GUARD.save, ...Object.values(TIME_RESULT),
  FAMILY_SHELL.childPage, FAMILY_SHELL.childPageFor('孩子 1 · 八年级'), FAMILY_SHELL.createHeading, FAMILY_SHELL.gradeLabel,
  FAMILY_SHELL.regionLabel, FAMILY_SHELL.birthLabel, FAMILY_SHELL.create, FAMILY_SHELL.consentHeading, FAMILY_SHELL.consentText,
  FAMILY_SHELL.consentGrant, FAMILY_SHELL.consentFor('孩子 1 · 八年级'), FAMILY_SHELL.onboarding, FAMILY_SHELL.timeSettings,
  FAMILY_SHELL.backToFamily, ...Object.values(FAMILY_SHELL_RESULT), ...Object.values(TRUST_SLOT),
];
