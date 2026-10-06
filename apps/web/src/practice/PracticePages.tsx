// 错题再练的学生端页面（specs/practice-loop-stage1 PR 4，spec 第 6 节）：
//   - 列表 /s/{studentId}/learn/{subject}/practice：这一科的错题条目（来源、知识点、层次、再练状态、轮次、下一轮时间）；
//   - 作答 /s/{studentId}/learn/{subject}/practice/{entryId}：取一道同类题、作答、只显示判分结论与条目新状态（PracticeItem）。
// 两页都先过前端开关（关时 404，不读会话、不发请求），再只认学生本人（requireOwnStudent：家长会话转到家长那一侧，那里没有再练入口；
// 别人的学生 404）；BFF 再按会话与数据库判断一次（家长 403、非本人 404）。
// 只陈述事实：不显示错因、掌握度、星态与任何评价；空原因编码、题目引用、复测与同族标记都不上页面（src/practice/view.ts 挑字段）。
// 条目来源只以文案占位出现，不另放进 DOM 属性；知识节点编号不合前端口径时这一格留空，其余照常。
import { notFound, redirect } from 'next/navigation';
import { fetchPracticeQueue } from '../lib/practice-api.ts';
import { PRACTICE, PRACTICE_LEVEL, PRACTICE_SOURCE, PRACTICE_STATUS, subjectLabel } from '../shell/copy.ts';
import { requireOwnStudent } from '../shell/guard.ts';
import { isPracticeSubject, practiceEnabled } from './features.ts';
import { PracticeItem } from './PracticeItem.tsx';
import { pickQueue, shanghaiMinute, UUID } from './view.ts';

const listPath = (studentId: string, subject: string) => `/s/${studentId}/learn/${subject}/practice`;

/** 科目页上的入口：开关关、学科不在再练范围时返回 null（不渲染任何元素）。只在学生端科目页使用，家长端不引用 */
export function PracticeLink({ studentId, subject }: { studentId: string; subject: string }) {
  if (!practiceEnabled() || !isPracticeSubject(subject)) return null;
  return <p><a href={listPath(studentId, subject)} data-testid="practice-entry">{PRACTICE.entry}</a></p>;
}

export async function PracticeListPage({ studentId, subject }: { studentId: string; subject: string }) {
  if (!practiceEnabled()) notFound();
  if (!isPracticeSubject(subject)) notFound();
  const ps = await requireOwnStudent(studentId);
  const r = await fetchPracticeQueue(ps.session, studentId, subject);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const entries = r.ok ? pickQueue(r.value) : null;
  return (
    <main data-testid="practice-list" data-practice-root="list" data-subject={subject}>
      <p><a href={`/s/${studentId}/learn/${subject}`} data-testid="practice-back">{PRACTICE.back}</a></p>
      <h1>{PRACTICE.heading}</h1>
      <p className="muted">{subjectLabel(subject)}</p>
      {!entries ? <p className="muted" data-unavailable="practice">{PRACTICE.unavailable}</p>
        : entries.length === 0 ? <p className="muted" data-testid="practice-list-empty">{PRACTICE.empty}</p> : (
          <div className="table-scroll">
            <table className="trust-table practice-table" data-testid="practice-entries">
              <thead>
                <tr>
                  <th scope="col">{PRACTICE.colSource}</th><th scope="col">{PRACTICE.colNode}</th><th scope="col">{PRACTICE.colLevel}</th>
                  <th scope="col">{PRACTICE.colStatus}</th><th scope="col">{PRACTICE.colRound}</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.entry_id} data-entry-id={e.entry_id} data-entry-status={e.status}>
                    <td>{PRACTICE_SOURCE[e.source]}</td>
                    <td>{e.node_id ? <span data-value="node">{e.node_id}</span> : null}</td>
                    <td>{PRACTICE_LEVEL[e.cognitive_level]}</td>
                    <td>
                      {e.status === 'due' ? (
                        <a href={`${listPath(studentId, subject)}/${e.entry_id}`} data-testid="practice-start">{PRACTICE.start}</a>
                      ) : <span>{PRACTICE_STATUS[e.status]}</span>}
                      {e.status === 'waiting' && e.next_round_at ? (
                        <>
                          <br /><span className="muted">{PRACTICE.nextRoundAt}</span>{' '}<span data-value="next-round-at">{shanghaiMinute(e.next_round_at)}</span>
                        </>
                      ) : null}
                    </td>
                    <td>
                      <span data-value="round">{e.round}</span>{' '}<span className="muted">{PRACTICE.roundOf}</span>{' '}<span data-value="max-rounds">{e.max_rounds}</span>
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

export async function PracticeItemPage({ studentId, subject, entryId }: { studentId: string; subject: string; entryId: string }) {
  if (!practiceEnabled()) notFound();
  if (!isPracticeSubject(subject) || !UUID.test(entryId)) notFound();
  const ps = await requireOwnStudent(studentId);
  return (
    <main data-testid="practice-page" data-practice-root="item" data-subject={subject}>
      <h1>{PRACTICE.heading}</h1>
      <p className="muted">{subjectLabel(subject)}</p>
      <PracticeItem csrf={ps.session.csrf} student={studentId} subject={subject} entry={entryId} listHref={listPath(studentId, subject)} />
    </main>
  );
}
