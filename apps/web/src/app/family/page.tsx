// 家长首页（spec 第 13 节）：已绑定孩子的列表（“孩子 N · 某年级”，不收姓名）、孩子页入口、星图、记录同意、在本设备打开学生模式、
// 新建孩子档案、退出登录。身份只来自会话：没登录回登录页，学生会话去学生页。家长端没有点点入口、没有任何角色形象。
// 关于孩子的描述不在这里出现（孩子页里只显示判断账本渲染的陈述）。
import { redirect } from 'next/navigation';
import { childLabel, FAMILY_COPY, FAMILY_RESULT, GRADE_LABEL, openStudentModeFor, starMapFor } from '../../auth/copy.ts';
import { ResultForm } from '../../auth/ResultForm.tsx';
import { currentSession, whoAmI } from '../../lib/shell-auth.ts';
import { InstallHint } from '../../pwa/InstallHint.tsx';
import { FAMILY_SHELL, FAMILY_SHELL_RESULT } from '../../shell/copy.ts';
import { logoutAction, openStudentModeAction } from './actions.ts';
import { consentAction, createStudentAction } from './shell-actions.ts';

export const dynamic = 'force-dynamic';

export default async function FamilyPage() {
  const s = await currentSession();
  if (!s) redirect('/login');
  const me = await whoAmI(s.token);
  if (!me.ok) redirect('/login');
  if (me.value.account_kind === 'student') redirect('/student');
  const children = me.value.children;
  const csrf = s.csrf ?? '';
  return (
    <main>
      <h1>{FAMILY_COPY.title}</h1>
      <h2>{FAMILY_COPY.childrenHeading}</h2>
      {children.length === 0 ? <p className="muted">{FAMILY_COPY.noChildren}</p> : null}
      {children.length > 0 ? (
        <ul className="shell-list">
          {children.map((c, i) => {
            const label = childLabel(i + 1, c.grade);
            return (
              <li key={c.student_id} className="child-row">
                <a href={`/family/${c.student_id}`} aria-label={FAMILY_SHELL.childPageFor(label)} data-testid={`child-page-${i + 1}`}>{label}</a>{' '}
                <a href={`/family/${c.student_id}/star-map`} aria-label={starMapFor(label)} data-testid={`star-map-${i + 1}`}>{FAMILY_COPY.starMap}</a>
                <ResultForm action={consentAction} labels={FAMILY_SHELL_RESULT} testId={`consent-${i + 1}`}>
                  <p className="muted">{FAMILY_SHELL.consentText}</p>
                  <input type="hidden" name="student" value={c.student_id} />
                  <input type="hidden" name="csrf" value={csrf} />
                  <button type="submit" aria-label={FAMILY_SHELL.consentFor(label)}>{FAMILY_SHELL.consentGrant}</button>
                </ResultForm>
              </li>
            );
          })}
        </ul>
      ) : null}
      {children.length > 0 ? (
        <section aria-labelledby="student-mode-heading">
          <h3 id="student-mode-heading">{FAMILY_COPY.studentModeHeading}</h3>
          <p className="muted">{FAMILY_COPY.studentModeNote}</p>
          <ul className="shell-list">
            {children.map((c, i) => {
              const label = childLabel(i + 1, c.grade);
              return (
                <li key={c.student_id} data-testid="child">
                  <ResultForm action={openStudentModeAction} labels={FAMILY_RESULT} testId={`open-student-${i + 1}`}>
                    <span>{label}</span>{' '}
                    <input type="hidden" name="student" value={c.student_id} />
                    <input type="hidden" name="csrf" value={csrf} />
                    <button type="submit" aria-label={openStudentModeFor(label)}>{FAMILY_COPY.openStudentMode}</button>
                  </ResultForm>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      <section aria-labelledby="create-heading">
        <h3 id="create-heading">{FAMILY_SHELL.createHeading}</h3>
        <ResultForm action={createStudentAction} labels={FAMILY_SHELL_RESULT} testId="create-student">
          <label htmlFor="grade">{FAMILY_SHELL.gradeLabel}</label>
          <select id="grade" name="grade" defaultValue="8" aria-describedby="grade-hint">
            {[7, 8, 9].map((g) => <option key={g} value={g}>{GRADE_LABEL[g]}</option>)}
          </select>
          <p id="grade-hint" className="muted">{FAMILY_SHELL.gradeHint}</p>
          <input type="hidden" name="region" value={FAMILY_SHELL.regionDefault} />
          <label htmlFor="birth">{FAMILY_SHELL.birthLabel}</label>
          <input id="birth" name="birth_year_month" type="month" />
          <input type="hidden" name="csrf" value={csrf} />
          <button type="submit">{FAMILY_SHELL.create}</button>
        </ResultForm>
      </section>
      <InstallHint />
      <ResultForm action={logoutAction} labels={FAMILY_RESULT} testId="logout" clearCachesOnSubmit>
        <input type="hidden" name="csrf" value={csrf} />
        <button type="submit">{FAMILY_COPY.logout}</button>
      </ResultForm>
    </main>
  );
}
