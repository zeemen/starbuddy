// 低置信打标候选工作单（KGE-06，specs/kge06-weekly-revision 范围 1）：待处理的打标候选按置信度升序（最没把握的在前），游标翻页；
// 每条可接受、驳回或改标（换成同题同角色的另一条候选）。结论只经签字身份调库函数 kge.decide_tag_candidate，处理人由库记下。
// 可见范围：只有公共层内容（地图版本、题目 ID、节点、打标层与置信度），没有任何学生数据（规则 13）。
// 开关 STARBUDDY_REVIEW_FEATURES=tag_worklist（只在 dev / test）：关着时 404，在读会话之前判断，不发任何请求。
import { notFound, redirect } from 'next/navigation';
import { reviewFeatureEnabled } from '../../../lib/review-features.ts';
import { currentSession } from '../../../lib/review-session.ts';
import {
  LAYER_LABEL, ROLE_LABEL, STATUS_LABEL, SUBJECTS, TAG_RESULT_LABEL, isConfidence, isSubject, isVersionId, listTagCandidates,
  worklistQuery, type SubjectCode,
} from '../../../lib/review.ts';
import { ActionForm } from '../ActionForm.tsx';
import { decideTagAction } from '../actions.ts';

export const dynamic = 'force-dynamic';

type Search = { subject?: string; map_version_id?: string; after_confidence?: string; after_id?: string };

export default async function TagCandidatesPage({ searchParams }: { searchParams: Promise<Search> }) {
  if (!reviewFeatureEnabled('tag_worklist')) notFound();
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const sp = await searchParams;
  // 地址栏参数只认白名单里的值，其余回到默认
  const subject: SubjectCode = sp.subject && isSubject(sp.subject) ? sp.subject : 'aya';
  const mapVersion = sp.map_version_id && isVersionId(sp.map_version_id) ? sp.map_version_id : undefined;
  const paged = !!sp.after_confidence && isConfidence(sp.after_confidence) && !!sp.after_id && isVersionId(sp.after_id);
  const filter = { subject, map_version_id: mapVersion, after_confidence: paged ? sp.after_confidence : undefined,
    after_id: paged ? sp.after_id : undefined };
  const r = await listTagCandidates(staff, filter);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  const link = (f: Partial<typeof filter>) => `/review/tag-candidates?${worklistQuery({ ...filter, ...f })}`;
  return (
    <main>
      <h1>低置信打标</h1>
      <p className="muted">待处理的打标候选，置信度从低到高。接受主考点会写回草稿版本的题目标签；已发布版本只记结论（打标变更走审核记录）。
        改标只能换成同一题、同一角色的另一条候选。</p>
      <nav aria-label="学科" className="review-tabs">
        {SUBJECTS.map((s) => (
          <a key={s.code} href={`/review/tag-candidates?${worklistQuery({ subject: s.code })}`} aria-current={s.code === subject ? 'page' : undefined}>
            {s.name}
          </a>
        ))}
      </nav>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : r.value.items.length === 0 ? (
        <p data-testid="tag-empty">没有待处理的打标候选</p>
      ) : (
        <>
          <p data-testid="tag-total">待处理共 {r.value.open_total} 条</p>
          <div className="table-scroll">
            <table className="review-table" data-testid="tag-candidates">
              <caption>{SUBJECTS.find((s) => s.code === subject)?.name}的待处理打标候选（置信度从低到高）</caption>
              <thead>
                <tr>
                  <th scope="col">置信度</th><th scope="col">地图版本</th><th scope="col">题目</th><th scope="col">角色</th>
                  <th scope="col">候选节点</th><th scope="col">打标层</th><th scope="col">处理</th>
                </tr>
              </thead>
              <tbody>
                {r.value.items.map((c) => (
                  <tr key={c.id} data-candidate={c.id} data-item={c.item_id} data-node={c.node_id}>
                    <td>{c.confidence}</td>
                    <td>{c.version}（{STATUS_LABEL[c.map_status] ?? c.map_status}）</td>
                    <td><code>{c.item_id}</code></td>
                    <td>{ROLE_LABEL[c.role] ?? c.role}</td>
                    <td><code>{c.node_id}</code>{c.node_name ? ` · ${c.node_name}` : ''}</td>
                    <td>{LAYER_LABEL[c.layer] ?? c.layer}<span className="muted">（{c.tagger_version}{c.model_version ? `；${c.model_version}` : ''}）</span></td>
                    <td>
                      <ActionForm action={decideTagAction} labels={TAG_RESULT_LABEL} testId={`decide-${c.id}`}>
                        <input type="hidden" name="candidate" value={c.id} />
                        <button type="submit" name="decision" value="accept">接受 {c.node_id}</button>{' '}
                        <button type="submit" name="decision" value="reject">驳回 {c.node_id}</button>
                        {c.alternatives.length > 0 ? (
                          <>
                            {' '}<label htmlFor={`alt-${c.id}`}>改标为</label>{' '}
                            <select id={`alt-${c.id}`} name="target_id" defaultValue={c.alternatives[0]!.id}>
                              {c.alternatives.map((a) => (
                                <option key={a.id} value={a.id}>{a.node_id}{a.node_name ? ` · ${a.node_name}` : ''}（{a.confidence}）</option>
                              ))}
                            </select>{' '}
                            <button type="submit" name="decision" value="relabel">改标</button>
                          </>
                        ) : null}
                      </ActionForm>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <p>
        {paged ? <><a href={link({ after_confidence: undefined, after_id: undefined })}>回到第一页</a>{' '}</> : null}
        {r.ok && r.value.next ? (
          <a href={link({ after_confidence: r.value.next.after_confidence, after_id: r.value.next.after_id })} data-testid="tag-next">下一页</a>
        ) : null}
      </p>
    </main>
  );
}
