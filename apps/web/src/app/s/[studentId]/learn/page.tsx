// 学生端“科目”（spec 第 18 节）：清单里 live 与 coming 的学科各一个入口；英语 hidden，不出现。
import { requireStudent } from '../../../../shell/guard.ts';
import { subjectLabel, SUBJECTS } from '../../../../shell/copy.ts';
import { visibleSubjects } from '../../../../subjects/registry.ts';

export const dynamic = 'force-dynamic';

export default async function LearnPage({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  await requireStudent(studentId);
  return (
    <main data-testid="learn">
      <h1>{SUBJECTS.title}</h1>
      <ul className="shell-list subject-list">
        {visibleSubjects().map((m) => (
          <li key={m.code} data-status={m.status}>
            <a href={`/s/${studentId}/learn/${m.code}`} data-testid={`subject-${m.code}`}>{SUBJECTS.enter(subjectLabel(m.code))}</a>
            {m.status === 'coming' ? <span className="muted"> · {SUBJECTS.comingTitle}</span> : null}
          </li>
        ))}
      </ul>
    </main>
  );
}
