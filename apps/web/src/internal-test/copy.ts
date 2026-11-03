// 内部成员测试运营的页面文案（LT-06）：全部带 {{TODO_COPY:internal_test.*}} 占位，人写人审后替换（与 src/family-goals/copy.ts 同一做法）。
// 系统口吻，不是任何角色的话术；不对学生作任何陈述（规则 11、12）；不含同意书正文（同意文本由人另定，这里只说明退出会发生什么）。
// 反馈框的提示“不要填写姓名、电话等个人信息”是 spec 范围第 3 条要求的原句。

import type { FeedbackCategory } from './routes.ts';

export const FEEDBACK_CATEGORY_LABEL: Record<FeedbackCategory, string> = {
  malfunction: '{{TODO_COPY:internal_test.category_malfunction}} 功能出错',
  wrong_content: '{{TODO_COPY:internal_test.category_wrong_content}} 内容有误',
  display: '{{TODO_COPY:internal_test.category_display}} 显示问题',
  slow: '{{TODO_COPY:internal_test.category_slow}} 卡顿或很慢',
  login: '{{TODO_COPY:internal_test.category_login}} 登录问题',
  suggestion: '{{TODO_COPY:internal_test.category_suggestion}} 建议',
  other: '{{TODO_COPY:internal_test.category_other}} 其他',
};

export const FEEDBACK = {
  entry: '{{TODO_COPY:internal_test.feedback_entry}} 问题反馈',
  heading: '{{TODO_COPY:internal_test.feedback_heading}} 问题反馈',
  intro: '{{TODO_COPY:internal_test.feedback_intro}} 遇到问题或有建议，可以在这里告诉测试团队。',
  categoryLabel: '{{TODO_COPY:internal_test.feedback_category}} 问题类型',
  bodyLabel: '{{TODO_COPY:internal_test.feedback_body}} 具体情况（500 字以内）',
  privacyHint: '不要填写姓名、电话等个人信息',
  attachRoute: (route: string) => `{{TODO_COPY:internal_test.feedback_attach_route}} 附上刚才所在的页面类型（${route}，不含任何个人信息）`,
  submit: '{{TODO_COPY:internal_test.feedback_submit}} 提交',
  back: '{{TODO_COPY:internal_test.feedback_back}} 返回',
} as const;

export const FEEDBACK_RESULT: Record<string, string> = {
  recorded: '{{TODO_COPY:internal_test.feedback_recorded}} 已提交，谢谢',
  INVALID: '{{TODO_COPY:internal_test.feedback_invalid}} 请选择问题类型，并填写 1–500 字的说明',
  TOO_MANY_REQUESTS: '{{TODO_COPY:internal_test.feedback_too_many}} 今天提交次数较多，请明天再试',
  TEST_EXITED: '{{TODO_COPY:internal_test.feedback_exited}} 已经退出内部测试，不能再提交',
  CSRF: '{{TODO_COPY:internal_test.result_csrf}} 页面已过期，请刷新后再试',
  NOT_FOUND: '{{TODO_COPY:internal_test.result_not_found}} 没有找到',
  UNAVAILABLE: '{{TODO_COPY:internal_test.result_unavailable}} 暂时无法提交，请稍后再试',
  INTERNAL: '{{TODO_COPY:internal_test.result_internal}} 暂时无法提交，请稍后再试',
};

export const EXIT = {
  entry: '{{TODO_COPY:internal_test.exit_entry}} 退出内部测试',
  heading: '{{TODO_COPY:internal_test.exit_heading}} 退出内部测试',
  effects: [
    '{{TODO_COPY:internal_test.exit_effect_consent}} 撤回已签的同意',
    '{{TODO_COPY:internal_test.exit_effect_codes}} 家庭的内部测试登录码作废',
    '{{TODO_COPY:internal_test.exit_effect_stop}} 孩子的学生模式退出，之后不能再使用学习功能',
    '{{TODO_COPY:internal_test.exit_effect_keep}} 已有数据保留到测试结束，届时统一删除；之前仍可在“数据查看与删除”里查看、导出或申请删除',
  ],
  confirmLabel: '{{TODO_COPY:internal_test.exit_confirm_label}} 我已了解上面的内容，确认让孩子退出内部测试',
  submit: '{{TODO_COPY:internal_test.exit_submit}} 确认退出',
  exitedNotice: '{{TODO_COPY:internal_test.exited_notice}} 已退出内部测试：学习功能已停用，已有数据保留到测试结束统一删除。',
  back: '{{TODO_COPY:internal_test.exit_back}} 返回孩子页',
} as const;

export const EXIT_RESULT: Record<string, string> = {
  exited: '{{TODO_COPY:internal_test.exit_done}} 已退出内部测试',
  CONFIRM_MISSING: '{{TODO_COPY:internal_test.exit_confirm_missing}} 请先勾选确认',
  CONFIRMATION_REQUIRED: '{{TODO_COPY:internal_test.exit_confirm_missing}} 请先勾选确认',
  TOO_MANY_REQUESTS: '{{TODO_COPY:internal_test.exit_too_many}} 操作太频繁，请稍后再试',
  FORBIDDEN: '{{TODO_COPY:internal_test.exit_forbidden}} 只有家长可以操作',
  CSRF: '{{TODO_COPY:internal_test.result_csrf}} 页面已过期，请刷新后再试',
  NOT_FOUND: '{{TODO_COPY:internal_test.result_not_found}} 没有找到',
  UNAVAILABLE: '{{TODO_COPY:internal_test.result_unavailable}} 暂时无法提交，请稍后再试',
  INTERNAL: '{{TODO_COPY:internal_test.result_internal}} 暂时无法提交，请稍后再试',
};
