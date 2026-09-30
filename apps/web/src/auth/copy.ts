// 登录页与学生模式入口的全部界面文案（spec 第 18 节“文案收在一个文件里”）。系统口吻，不是人设文本；
// 不含比较、评价、推断类用词（tests/auth.test.ts 用 packages/personas 的禁语扫描与比较词表检查这里的每一条）。
// 界面上关于孩子只出现“孩子 N · 某年级”（档案没有姓名，待人确认 #12），不出现任何关于孩子的描述。
export const LOGIN_COPY = {
  title: '家长登录',
  intro: '用手机号和短信验证码登录。第一次登录会同时建立家长账号。',
  phoneLabel: '手机号',
  phoneHint: '11 位手机号',
  sendCode: '获取验证码',
  codeLabel: '验证码',
  codeHint: '短信里的 6 位数字，5 分钟内有效',
  submit: '登录',
  changePhone: '换一个手机号',
  sent: '如果这个号码可以接收短信，验证码已经发出。',
} as const;

/** server action 回来的结果码 → 提示（结果码不经地址栏，别人不能用链接伪造提示文字） */
export const LOGIN_RESULT: Record<string, string> = {
  INVALID_PHONE: '请填写正确的手机号。',
  LOGIN_FAILED: '验证码不对或已失效，请重新获取。',
  CONTACT_SUPPORT: '这个号码暂时不能在这里登录，请联系客服处理。',
  TOO_MANY_REQUESTS: '操作太频繁了，请稍后再试。',
  CSRF: '页面已过期，请刷新后再试。',
  INTERNAL: '暂时无法完成，请稍后再试。',
};

export const FAMILY_COPY = {
  title: '家长首页',
  childrenHeading: '孩子',
  noChildren: '还没有孩子的档案。',
  studentModeHeading: '在这台设备上打开学生模式',
  studentModeNote: '打开后，这台设备会切换成孩子的账号，家长登录随之退出；回到家长模式需要重新用短信验证码登录。',
  openStudentMode: '打开学生模式',
  starMap: '星图',
  logout: '退出登录',
} as const;

export const GRADE_LABEL: Record<number, string> = { 7: '七年级', 8: '八年级', 9: '九年级' };
export const childLabel = (ordinal: number, grade: number): string => `孩子 ${ordinal} · ${GRADE_LABEL[grade] ?? `${grade} 年级`}`;
export const openStudentModeFor = (label: string): string => `为${label}打开学生模式`;
export const starMapFor = (label: string): string => `${label}的星图`;

export const FAMILY_RESULT: Record<string, string> = {
  CONSENT_REQUIRED: '需要先完成隐私政策与监护人同意，才能打开学生模式。',
  NOT_FOUND: '没有找到这个孩子的档案。',
  TOO_MANY_REQUESTS: '操作太频繁了，请稍后再试。',
  CSRF: '页面已过期，请刷新后再试。',
  UNAUTHENTICATED: '登录已失效，请重新登录。',
  INTERNAL: '暂时无法完成，请稍后再试。',
};

export const STUDENT_COPY = {
  title: '学生模式',
  intro: '这台设备现在是学生模式。',
  starMap: '星图',
  exit: '退出学生模式',
  exitNote: '退出后回到登录页；家长需要重新用短信验证码登录。',
} as const;

/** 全部文案（静态检查逐条扫描） */
export const ALL_AUTH_COPY: string[] = [
  ...Object.values(LOGIN_COPY), ...Object.values(LOGIN_RESULT), ...Object.values(FAMILY_COPY), ...Object.values(FAMILY_RESULT),
  ...Object.values(STUDENT_COPY), ...Object.values(GRADE_LABEL), childLabel(1, 8), openStudentModeFor(childLabel(2, 9)),
  starMapFor(childLabel(1, 8)),
];
