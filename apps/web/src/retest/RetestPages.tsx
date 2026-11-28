// 复测与验证题的学生端页面（specs/retest-delivery-kge05 范围第 4、5 条）：
//   - 列表 /s/{studentId}/learn/{subject}/retest：这一科到期的复测与验证条目（类别占位、知识点、层次、状态）；
//   - 作答 /s/{studentId}/learn/{subject}/retest/{entryId}：取一道题、作答、只显示判分结论与条目新状态（RetestItem）。
// 两页都先过前端开关 STARBUDDY_RETEST_UI（关时 404，不读会话、不发请求），再只认学生本人（requireOwnStudent）；BFF 再判断一次。
// 只陈述事实：任务说明只用固定占位（类别 → 纯占位键），不显示任务原因、错因、掌握度、星态与任何评价（规则 11、12）。
import { notFound, redirect } from 'next/navigation';
import { isLessonSubject, lessonEnabled } from '../lesson/features.ts';
import { entryLessonRef, lessonHref } from '../lesson/view.ts';
import { fetchRetestQueue } from '../lib/retest-api.ts';
import { RETEST, RETEST_KIND, RETEST_LEVEL, RETEST_STATUS, subjectLabel } from '../shell/copy.ts';
import { requireOwnStudent } from '../shell/guard.ts';
import { isRetestSubject, retestEnabled } from './features.ts';
import { RetestItem } from './RetestItem.tsx';
import { pickRetestQueue, UUID } from './view.ts';

const listPath = (studentId: string, subject: string) => `/s/${studentId}/learn/${subject}/retest`;

/** 科目页上的入口：开关关、学科不在范围时返回 null（不渲染任何元素）。只在学生端科目页使用，家长端不引用 */
export function RetestLink({ studentId, subject }: { studentId: string; subject: string }) {
  if (!retestEnabled() || !isRetestSubject(subject)) return null;
  return <p><a href={listPath(studentId, subject)} data-testid="retest-entry">{RETEST.entry}</a></p>;
}

export async function RetestListPage({ studentId, subject }: { studentId: string; subject: string }) {
  if (!retestEnabled()) notFound();
  if (!isRetestSubject(subject)) notFound();
  const ps = await requireOwnStudent(studentId);
  const r = await fetchRetestQueue(ps.session, studentId, subject);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const entries = r.ok ? pickRetestQueue(r.value) : null;
  return (
    <main data-testid="retest-list" data-retest-root="list" data-subject={subject}>
      <p><a href={`/s/${studentId}/learn/${subject}`} data-testid="retest-back">{RETEST.back}</a></p>
      <h1>{RETEST.heading}</h1>
      <p className="muted">{subjectLabel(subject)}</p>
      {!entries ? <p className="muted" data-unavailable="retest">{RETEST.unavailable}</p>
        : entries.length === 0 ? <p className="muted" data-testid="retest-list-empty">{RETEST.empty}</p> : (
          <div className="table-scroll">
            {/* 手机宽度下按卡片排列（同错题再练列表，globals.css 的 .card-table）。specs/practice-retest-list-polish */}
            <table className="trust-table practice-table card-table" data-testid="retest-entries" role="table">
              <thead role="rowgroup">
                <tr role="row">
                  <th scope="col" role="columnheader">{RETEST.colKind}</th><th scope="col" role="columnheader">{RETEST.colNode}</th>
                  <th scope="col" role="columnheader">{RETEST.colLevel}</th><th scope="col" role="columnheader">{RETEST.colStatus}</th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {entries.map((e) => (
                  <tr key={e.entry_id} data-entry-id={e.entry_id} data-entry-status={e.status} role="row">
                    <td role="cell" data-label={RETEST.colKind}>{RETEST_KIND[e.kind]}</td>
                    <td role="cell" data-label={RETEST.colNode}>{e.node_name ? <span data-value="node-name">{e.node_name}</span>
                      : e.node_id ? <span data-value="node">{e.node_id}</span> : null}</td>
                    <td role="cell" data-label={RETEST.colLevel}>{RETEST_LEVEL[e.cognitive_level]}</td>
                    <td role="cell" data-label={RETEST.colStatus}>
                      {e.status === 'due' ? (
                        <a href={`${listPath(studentId, subject)}/${e.entry_id}`} data-testid="retest-start">{RETEST.start}</a>
                      ) : <span>{RETEST_STATUS[e.status]}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </main>
  );
}

export async function RetestItemPage({ studentId, subject, entryId }: { studentId: string; subject: string; entryId: string }) {
  if (!retestEnabled()) notFound();
  if (!isRetestSubject(subject) || !UUID.test(entryId)) notFound();
  const ps = await requireOwnStudent(studentId);
  // 讲评课入口（specs/lesson-catalog PR D，E2）：同再练。复测条目作答后会离开列表，所以在作答之前取引用
  const q = lessonEnabled() && isLessonSubject(subject) ? await fetchRetestQueue(ps.session, studentId, subject) : null;
  const ref = q?.ok ? entryLessonRef(q.value, entryId) : null;
  return (
    <main data-testid="retest-page" data-retest-root="item" data-subject={subject}>
      <h1>{RETEST.heading}</h1>
      <p className="muted">{subjectLabel(subject)}</p>
      <RetestItem csrf={ps.session.csrf} student={studentId} subject={subject} entry={entryId} listHref={listPath(studentId, subject)}
                  lessonHref={ref ? lessonHref(studentId, subject, ref) : null} />
    </main>
  );
}
