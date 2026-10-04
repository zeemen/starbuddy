// 内容版本列表（冲刺 4，spec 第 7 节）：按学科列出内容版本（六段标识、状态、所指地图版本、登记计数），链到差异视图。
import { redirect } from 'next/navigation';
import { currentSession } from '../../../lib/review-session.ts';
import { SUBJECTS, STATUS_LABEL, formatTime, isSubject, listContentVersions, type SubjectCode } from '../../../lib/review.ts';

export const dynamic = 'force-dynamic';

export default async function ContentVersionsPage({ searchParams }: { searchParams: Promise<{ subject?: string }> }) {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const raw = (await searchParams).subject ?? 'aya';
  const subject: SubjectCode = isSubject(raw) ? raw : 'aya';
  const r = await listContentVersions(staff, subject);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>内容版本</h1>
      <nav aria-label="学科" className="review-tabs">
        {SUBJECTS.map((s) => (
          <a key={s.code} href={`/review/content?subject=${s.code}`} aria-current={s.code === subject ? 'page' : undefined}>{s.name}</a>
        ))}
      </nav>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : r.value.items.length === 0 ? (
        <p data-testid="no-content-versions">这个学科还没有登记内容版本</p>
      ) : (
        <table className="review-table" data-testid="content-versions">
          <caption>{SUBJECTS.find((s) => s.code === subject)?.name}的内容版本（六段标识：地区 / 届别 / 年级 / 教材 / 学科 / 版本号）</caption>
          <thead>
            <tr><th scope="col">六段标识</th><th scope="col">状态</th><th scope="col">能力视图 / 题目</th><th scope="col">发布时间</th><th scope="col">操作</th></tr>
          </thead>
          <tbody>
            {r.value.items.map((v) => (
              <tr key={v.id} data-cv={v.content_version} data-status={v.status}>
                <td><code>{v.six_segment}</code></td>
                <td>{STATUS_LABEL[v.status] ?? v.status}</td>
                <td>{v.skill_nodes} / {v.items}</td>
                <td>{formatTime(v.published_at)}</td>
                <td><a href={`/review/content/${v.id}`}>{v.status === 'draft' ? '审核' : '查看'} {v.content_version}</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p><a href={`/review/records?subject=${subject}`}>版本记录</a></p>
    </main>
  );
}
