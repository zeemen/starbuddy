// 讲评课页面（specs/stage-lesson-session ③b-5）：/s/{studentId}/learn/{subject}/lesson/{lessonId}?v={版本}。
// 讲评课目录（specs/lesson-catalog PR D）起地址只带编号与版本：节点与题由学习服务按目录补全；以前带 node、item 的地址一律 404。
// 先过前端开关（关时 404，不读会话、不发请求），再只认学生本人（requireOwnStudent：家长会话转到家长那一侧；别人的学生 404）；
// BFF 再按会话与数据库判断一次（家长 403、非本人 404）。入口：科目页的讲评课列表（LessonList）、再练与复测判分之后的“看这道题的讲评课”。
// 打开讲评课会计一次（lesson_whiteboard），所以页面载入时不打开，要学生点“开始”（LessonPlayer）。
import { notFound } from 'next/navigation';
import { LESSON, subjectLabel } from '../shell/copy.ts';
import { requireOwnStudent } from '../shell/guard.ts';
import { isLessonSubject, lessonEnabled } from './features.ts';
import { LessonPlayer } from './LessonPlayer.tsx';
import { parseLessonRef, UUID } from './view.ts';

export async function LessonPage({ studentId, subject, lessonId, search }: {
  studentId: string; subject: string; lessonId: string; search: Record<string, string | string[] | undefined>;
}) {
  if (!lessonEnabled()) notFound();
  if (!isLessonSubject(subject) || !UUID.test(studentId)) notFound();
  const one = (k: string) => (typeof search[k] === 'string' ? search[k] : undefined);
  if ('node' in search || 'item' in search) notFound();
  const lesson = parseLessonRef(lessonId, one('v'));
  if (!lesson) notFound();
  const ps = await requireOwnStudent(studentId);
  return (
    <main data-testid="lesson-page" data-subject={subject}>
      <p><a href={`/s/${studentId}/learn/${subject}`} data-testid="lesson-back">{LESSON.back}</a></p>
      <h1>{LESSON.heading}</h1>
      <p className="muted">{subjectLabel(subject)}</p>
      <LessonPlayer csrf={ps.session.csrf} student={studentId} subject={subject} lesson={lesson} />
    </main>
  );
}
