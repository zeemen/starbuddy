// 学生端科目页 /s/{studentId}/learn/{subject}（spec 第 18 节）：按清单挂各科的入口组件；英语与清单外的学科 404。
// 每个科目页都能一步回到星图（顶栏常驻“星图”，页内另有一个）。只调 BFF，不调原产品，不用 iframe。
import { notFound, redirect } from 'next/navigation';
import { fetchToday } from '../../../../../lib/shell-api.ts';
import { subjectLabel, SUBJECTS } from '../../../../../shell/copy.ts';
import { requireStudent } from '../../../../../shell/guard.ts';
import { ChineseEntry } from '../../../../../subjects/chinese/Entry.tsx';
import { MathEntry } from '../../../../../subjects/math/Entry.tsx';
import { PoliticsEntry } from '../../../../../subjects/politics/Entry.tsx';
import { subjectModule } from '../../../../../subjects/registry.ts';

export const dynamic = 'force-dynamic';

export default async function SubjectPage({ params }: { params: Promise<{ studentId: string; subject: string }> }) {
  const { studentId, subject } = await params;
  const m = subjectModule(subject);
  if (!m) notFound();
  const ps = await requireStudent(studentId);
  let body;
  if (m.code === 'math') {
    const r = await fetchToday(ps.session, studentId);
    if (!r.ok) {
      if (r.status === 401) redirect('/login');
      notFound();
    }
    body = <MathEntry section={r.value.subjects.find((s) => s.subject === 'math') ?? null} audience="student" studentId={studentId} />;
  } else {
    body = m.code === 'politics' ? <PoliticsEntry /> : <ChineseEntry />;
  }
  const name = subjectLabel(m.code);
  return (
    <main data-testid="subject-page" data-subject={m.code} data-status={m.status}>
      <h1>{name}</h1>
      <p><a href={`/s/${studentId}/star-map`} data-testid="back-to-star-map">{SUBJECTS.backToStarMap}</a></p>
      {body}
    </main>
  );
}
