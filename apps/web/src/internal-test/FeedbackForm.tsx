// 问题反馈表单（学生端与家长端共用）：固定分类 + 文本框（提示“不要填写姓名、电话等个人信息”）+ 可选附上刚才所在的页面类型（缺省不附）。
// 不附截图、不采集设备信息。提交给 ./actions.ts 的 feedbackAction；来源由 BFF 按会话决定。
import { ResultForm } from '../auth/ResultForm.tsx';
import { feedbackAction } from './actions.ts';
import { FEEDBACK, FEEDBACK_CATEGORY_LABEL, FEEDBACK_RESULT } from './copy.ts';
import { FEEDBACK_CATEGORIES, FEEDBACK_MAX_CHARS } from './routes.ts';

export function FeedbackForm({ studentId, csrf, route }: { studentId: string; csrf: string; route: string | null }) {
  return (
    <ResultForm action={feedbackAction} labels={FEEDBACK_RESULT} testId="feedback-form">
      <input type="hidden" name="student" value={studentId} />
      <input type="hidden" name="csrf" value={csrf} />
      <p>
        <label htmlFor="feedback-category">{FEEDBACK.categoryLabel}</label>{' '}
        <select id="feedback-category" name="category" required defaultValue="">
          <option value="" disabled>—</option>
          {FEEDBACK_CATEGORIES.map((c) => <option key={c} value={c}>{FEEDBACK_CATEGORY_LABEL[c]}</option>)}
        </select>
      </p>
      <p>
        <label htmlFor="feedback-body">{FEEDBACK.bodyLabel}</label>
        <textarea id="feedback-body" name="body" required maxLength={FEEDBACK_MAX_CHARS} rows={5} aria-describedby="feedback-privacy" />
      </p>
      <p className="muted" id="feedback-privacy" data-testid="feedback-privacy">{FEEDBACK.privacyHint}</p>
      {route ? (
        <p>
          <input type="hidden" name="route_template" value={route} />
          <label className="inline-label">
            <input type="checkbox" name="attach_route" />{FEEDBACK.attachRoute(route)}
          </label>
        </p>
      ) : null}
      <button type="submit">{FEEDBACK.submit}</button>
    </ResultForm>
  );
}
