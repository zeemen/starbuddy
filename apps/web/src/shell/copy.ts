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

/**
 * 信任界面（冲刺 5-H：“为什么”“我不同意”、考试录入与对账卡、家长数据权利）的全部文案。
 * 一律是明显占位 {{TODO_COPY:键}}（异议、对账卡、置信度等话术人写人审，必停）；少数界面标签在占位后附中性提示，便于走查。
 * 异议类型、下一步、异议状态、偏差方向、平台调整只按编码映射到占位键，不附任何提示文字：键名不含编码原文
 * （例如 one_off_slip 不出现在页面上），也不写任何评价性文字（规则 11、12）。
 * 关于学生的句子不在这里：只来自判断（RenderedStatement.text，带 data-claim-id）。数字、日期、节点编号不拼进文案，
 * 由组件放在 data-value 元素里（端到端逐个文本节点核对：要么是这里的文案，要么在 data-claim-id / data-value 里）。
 */
const T = (key: string, hint?: string): string => `{{TODO_COPY:${key}}}${hint ? ` ${hint}` : ''}`;

export const TRUST = {
  unavailable: T('trust.unavailable', '暂时无法获取'),
  entryWhy: T('trust.entry.why', '为什么'),
  entryExams: T('trust.entry.exams', '考试'),
  entryDisputes: T('trust.entry.disputes', '我提出的异议'),
  entryData: T('trust.entry.data', '数据查看、导出与删除'),
  navLabel: T('trust.nav', '信任界面'),
  back: T('trust.back', '返回'),
  when: T('trust.when', '时间'),
} as const;

export const WHY = {
  heading: T('why.heading', '为什么'),
  statusLabel: T('why.status', '状态'),
  evidenceHeading: T('why.evidence', '依据'),
  evidenceEmpty: T('why.evidence.empty', '没有可展示的依据'),
  colTime: T('why.col.time', '时间'),
  colKind: T('why.col.kind', '证据种类'),
  colLevel: T('why.col.level', '证据等级'),
  colNode: T('why.col.node', '知识点编号'),
  version: T('why.version', '依据版本'),
  dashedNote: T('why.dashed', '这一条还在验证中'),
} as const;

/** 判断状态（学生看“初步判断 / 待验证”，家长另附置信度说明 CONFIDENCE_BAND）：纯占位键，不附提示 */
export const WHY_STATUS: Record<string, string> = {
  hypothesis: T('why.status.1'), to_verify: T('why.status.2'), established: T('why.status.3'),
};
/** 证据种类（来源类型，规则 6） */
export const EVIDENCE_SOURCE: Record<string, string> = {
  objective: T('why.source.1'), self: T('why.source.2'), guardian: T('why.source.3'), inferred: T('why.source.4'),
};
export const EVIDENCE_LEVEL: Record<string, string> = { A: T('why.level.1'), B: T('why.level.2') };

export const DISPUTE = {
  heading: T('dispute.heading', '我不同意'),
  listHeading: T('dispute.list', '我提出的异议'),
  empty: T('dispute.empty', '还没有提出过异议'),
  colKind: T('dispute.col.kind', '类型'),
  colStatus: T('dispute.col.status', '状态'),
  colCreated: T('dispute.col.created', '提出时间'),
  colResolved: T('dispute.col.resolved', '结束时间'),
  colClaim: T('dispute.col.claim', '对应的判断'),
  openClaim: T('dispute.open_claim', '查看'),
  toList: T('dispute.to_list', '查看我提出的异议'),
} as const;
/** 异议三条路（编码 → 占位键；one_off_slip 只给学生本人） */
export const DISPUTE_KIND: Record<string, string> = {
  i_know: T('dispute.kind.1'), one_off_slip: T('dispute.kind.2'), data_error: T('dispute.kind.3'),
};
/** 提交后的下一步（BFF next_step 编码 → 占位键）与出错提示 */
export const DISPUTE_RESULT: Record<string, string> = {
  verification_scheduled: T('dispute.next.1'),
  observe_followups: T('dispute.next.2'),
  open_fact_for_correction: T('dispute.next.3'),
  NOT_FOUND: T('trust.error.not_found', '没有找到'),
  STATE_CONFLICT: T('dispute.error.conflict', '这一条现在不能提出异议'),
  CONFLICT: T('dispute.error.conflict', '这一条现在不能提出异议'),
  FORBIDDEN: T('trust.error.forbidden', '没有权限'),
  TOO_MANY: T('trust.error.too_many', '操作太频繁了，请稍后再试'),
  TOO_MANY_REQUESTS: T('trust.error.too_many', '操作太频繁了，请稍后再试'),
  CSRF: T('trust.error.csrf', '页面已过期，请刷新后再试'),
  INVALID: T('trust.error.invalid', '填写的内容不符合要求'),
  UNAVAILABLE: T('trust.unavailable', '暂时无法获取'),
  INTERNAL: T('trust.error.internal', '暂时无法完成，请稍后再试'),
};
export const DISPUTE_STATUS: Record<string, string> = {
  open: T('dispute.status.1'), verifying: T('dispute.status.2'), resolved_supported: T('dispute.status.3'),
  resolved_unsupported: T('dispute.status.4'), fact_corrected: T('dispute.status.5'), rejected: T('dispute.status.6'),
  withdrawn: T('dispute.status.7'),
};

export const EXAM = {
  heading: T('exam.heading', '考试'),
  planHeading: T('exam.plan', '录入考试安排'),
  subject: T('exam.subject', '学科'),
  type: T('exam.type', '考试类型'),
  date: T('exam.date', '考试日期'),
  fullScore: T('exam.full_score', '满分'),
  fullScoreHint: T('exam.full_score.hint', '七八年级一般是 100 分，九年级一般是 120 分'),
  scope: T('exam.scope', '考试范围（知识点编号，用空格或逗号分隔）'),
  submitPlan: T('exam.submit_plan', '保存考试安排'),
  scoreHeading: T('exam.score', '录入成绩'),
  rawScore: T('exam.raw_score', '得分'),
  submitScore: T('exam.submit_score', '保存成绩'),
  toPlan: T('exam.to_plan', '录入新的考试安排'),
} as const;
/** 考试录入的学科（产品命名空间；英语不显示） */
export const EXAM_SUBJECT: Record<string, string> = {
  aya: T('exam.subject.math', '数学'), wenli: T('exam.subject.chinese', '语文'), zhixing: T('exam.subject.politics', '道法'),
};
export const EXAM_TYPE: Record<string, string> = {
  monthly: T('exam.type.monthly', '月考'), midterm: T('exam.type.midterm', '期中'), final: T('exam.type.final', '期末'),
  mock1: T('exam.type.mock1', '一模'), mock2: T('exam.type.mock2', '二模'), other: T('exam.type.other', '其他'),
};
export const EXAM_RESULT: Record<string, string> = {
  saved: T('exam.saved', '已保存'),
  NOT_FOUND: DISPUTE_RESULT['NOT_FOUND']!, CONFLICT: T('exam.error.conflict', '刚刚有人录入了同一场考试，请刷新后再看'),
  TOO_MANY: DISPUTE_RESULT['TOO_MANY']!, TOO_MANY_REQUESTS: DISPUTE_RESULT['TOO_MANY_REQUESTS']!, CSRF: DISPUTE_RESULT['CSRF']!,
  INVALID: DISPUTE_RESULT['INVALID']!, FORBIDDEN: DISPUTE_RESULT['FORBIDDEN']!, INTERNAL: DISPUTE_RESULT['INTERNAL']!,
};

export const PREDICTION = {
  heading: T('prediction.heading', '考前预测区间'),
  interval: T('prediction.interval', '区间'),
  to: T('prediction.to', '到'),
  frozen: T('prediction.frozen', '已冻结，只读'),
  notFrozen: T('prediction.not_frozen', '考试当天冻结'),
  levelNote: T('prediction.level_note', '置信度说明：区间水平'),
  none: T('prediction.none', '暂时没有可展示的预测'),
} as const;

export const RECONCILE = {
  heading: T('reconcile.heading', '对账卡'),
  actual: T('reconcile.actual', '实际得分'),
  within: T('reconcile.within'),
  outside: T('reconcile.outside'),
  nodes: T('reconcile.nodes', '偏差涉及的知识点编号'),
  directions: T('reconcile.directions', '偏差方向'),
  adjustments: T('reconcile.adjustments', '平台据此做的调整'),
  statements: T('reconcile.statements', '说明'),
  none: T('reconcile.none', '暂时没有对账卡'),
} as const;
/** 偏差方向（四值编码 → 占位键；不附提示） */
export const DEVIATION_DIRECTION: Record<string, string> = {
  item_type_transfer: T('reconcile.direction.1'), time_shortage: T('reconcile.direction.2'),
  calculation_slip: T('reconcile.direction.3'), diagnosis_optimistic: T('reconcile.direction.4'),
};
export const ADJUSTMENT: Record<string, string> = {
  claims_to_verify: T('reconcile.adjustment.1'), retest_scheduled: T('reconcile.adjustment.2'), interval_widened: T('reconcile.adjustment.3'),
  model_parameter_adjusted: T('reconcile.adjustment.4'), none: T('reconcile.adjustment.5'),
};

export const DATA_RIGHTS = {
  heading: T('data.heading', '数据查看、导出与删除'),
  exportHeading: T('data.export', '导出'),
  exportNote: T('data.export.note', '导出文件只能下载一次，过一段时间会失效'),
  exportStart: T('data.export.start', '生成导出文件'),
  download: T('data.export.download', '下载导出文件'),
  exportGone: T('data.export.gone', '下载凭据已失效，请重新生成'),
  erasureHeading: T('data.erasure', '删除'),
  erasureNote: T('data.erasure.note', '申请后需要再确认一次；确认后有冷静期，冷静期内可以撤回'),
  erasureStart: T('data.erasure.start', '申请删除'),
  confirmCheck: T('data.erasure.confirm_check', '我已了解删除后无法恢复'),
  confirm: T('data.erasure.confirm', '确认删除'),
  reauth: T('data.erasure.reauth', '为了安全，请先重新登录，再重新申请并确认'),
  relogin: T('data.erasure.relogin', '重新登录'),
  requestsHeading: T('data.erasure.requests', '删除申请'),
  none: T('data.erasure.none', '没有删除申请'),
  colStatus: T('data.col.status', '状态'),
  colRequested: T('data.col.requested', '申请时间'),
  colCooling: T('data.col.cooling', '冷静期剩余'),
  days: T('data.days', '天'),
  hours: T('data.hours', '小时'),
  cancel: T('data.erasure.cancel', '撤回'),
  receiptHeading: T('data.receipt', '删除回执'),
  receiptTable: T('data.receipt.table', '数据表'),
  receiptRows: T('data.receipt.rows', '删除行数'),
  receiptScopes: T('data.receipt.scopes', '范围'),
} as const;
/** 删除申请状态（pending 按是否已确认分两种显示） */
export const ERASURE_STATUS: Record<string, string> = {
  awaiting_confirm: T('data.status.1'), cooling_off: T('data.status.2'), cancelled: T('data.status.3'), executed: T('data.status.4'),
};
export const ERASURE_CANCEL_REASON: Record<string, string> = {
  guardian: T('data.cancel_reason.1'), link_revoked: T('data.cancel_reason.2'), confirm_expired: T('data.cancel_reason.3'),
};
export const ERASURE_SCOPE: Record<string, string> = {
  claim_ledger: T('data.scope.1'), facts: T('data.scope.2'), evidence: T('data.scope.3'), inference: T('data.scope.4'),
  reports: T('data.scope.5'), uploads: T('data.scope.6'),
};
export const DATA_RESULT: Record<string, string> = {
  ready: T('data.export.ready', '导出文件已生成'),
  requested: T('data.erasure.requested', '已申请，请再确认一次'),
  confirmed: T('data.erasure.confirmed', '已确认，进入冷静期'),
  cancelled: T('data.erasure.cancelled', '已撤回'),
  REAUTH_REQUIRED: T('data.erasure.reauth', '为了安全，请先重新登录，再重新申请并确认'),
  CONFIRMATION_REQUIRED: T('data.erasure.confirm_required', '确认已失效，请重新申请'),
  STATE_CONFLICT: T('data.error.conflict', '现在不能进行这项操作'),
  CONFLICT: T('data.error.conflict', '现在不能进行这项操作'),
  NOT_FOUND: DISPUTE_RESULT['NOT_FOUND']!, FORBIDDEN: DISPUTE_RESULT['FORBIDDEN']!, TOO_MANY: DISPUTE_RESULT['TOO_MANY']!,
  TOO_MANY_REQUESTS: DISPUTE_RESULT['TOO_MANY_REQUESTS']!, CSRF: DISPUTE_RESULT['CSRF']!, INVALID: DISPUTE_RESULT['INVALID']!,
  UNAVAILABLE: DISPUTE_RESULT['UNAVAILABLE']!, INTERNAL: DISPUTE_RESULT['INTERNAL']!,
};

/** 信任界面的全部文案（静态检查逐条扫描；端到端的“每个文本节点”核对也用它） */
export const ALL_TRUST_COPY: string[] = [
  ...Object.values(TRUST), ...Object.values(WHY), ...Object.values(WHY_STATUS), ...Object.values(EVIDENCE_SOURCE),
  ...Object.values(EVIDENCE_LEVEL), ...Object.values(DISPUTE), ...Object.values(DISPUTE_KIND), ...Object.values(DISPUTE_RESULT),
  ...Object.values(DISPUTE_STATUS), ...Object.values(EXAM), ...Object.values(EXAM_SUBJECT), ...Object.values(EXAM_TYPE),
  ...Object.values(EXAM_RESULT), ...Object.values(PREDICTION), ...Object.values(RECONCILE), ...Object.values(DEVIATION_DIRECTION),
  ...Object.values(ADJUSTMENT), ...Object.values(DATA_RIGHTS), ...Object.values(ERASURE_STATUS), ...Object.values(ERASURE_CANCEL_REASON),
  ...Object.values(ERASURE_SCOPE), ...Object.values(DATA_RESULT), ...Object.values(CONFIDENCE_BAND), ...Object.values(TRUST_SLOT),
];

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
  ...ALL_TRUST_COPY,
];
