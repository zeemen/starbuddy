// 审核台首页：按学科列出地图版本（草稿、已发布、已撤下），链到差异视图与版本记录。
import { redirect } from 'next/navigation';
import { currentSession } from '../../lib/review-session.ts';
import { SUBJECTS, STATUS_LABEL, isSubject, listVersions, type SubjectCode } from '../../lib/review.ts';

export const dynamic = 'force-dynamic';

export default async function ReviewHome({ searchParams }: { searchParams: Promise<{ subject?: string }> }) {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const raw = (await searchParams).subject ?? 'aya';
  const subject: SubjectCode = isSubject(raw) ? raw : 'aya';
  const r = await listVersions(staff, subject);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>地图版本</h1>
      <nav aria-label="学科" className="review-tabs">
        {SUBJECTS.map((s) => (
          <a key={s.code} href={`/review?subject=${s.code}`} aria-current={s.code === subject ? 'page' : undefined}>
            {s.name}
          </a>
        ))}
      </nav>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : r.value.items.length === 0 ? (
        <p data-testid="no-versions">这个学科还没有地图版本</p>
      ) : (
        <table className="review-table" data-testid="versions">
          <caption>{SUBJECTS.find((s) => s.code === subject)?.name}的地图版本（按建立时间）</caption>
          <thead>
            <tr><th scope="col">版本号</th><th scope="col">状态</th><th scope="col">发布时间</th><th scope="col">操作</th></tr>
          </thead>
          <tbody>
            {r.value.items.map((v) => (
              <tr key={v.id} data-version={v.version} data-status={v.status}>
                <td>{v.version}</td>
                <td>{STATUS_LABEL[v.status] ?? v.status}</td>
                <td>{v.published_at ? new Date(v.published_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—'}</td>
                <td><a href={`/review/map-versions/${v.id}`}>{v.status === 'draft' ? '审核' : '查看'} {v.version}</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p><a href={`/review/records?subject=${subject}`}>版本记录</a></p>
    </main>
  );
}
