// 科目页上的讲评课列表（specs/lesson-catalog PR D，入口 E1）：列出这一科已发布的讲评课，每节一行（标题与步数），点进讲评课页面。
// 说的是内容本身，与学生无关（规格第二节：不需要判断账本）；不按掌握情况挑课（E3 另起，属于 SIE）。
// 开关关、学科没有讲评课时返回 null（不渲染任何元素，也不发请求）——与开关打开前零差异。只在学生端科目页使用，家长端不引用。
// 目录取不到时只在这一节显示“暂时无法获取”，科目页其余部分照常；节点与题从不出现在页面上（BFF 也不给）。
import type { BffSession } from '../lib/bff.ts';
import { fetchLessonCatalog } from '../lib/lesson-api.ts';
import { LESSON } from '../shell/copy.ts';
import { isLessonSubject, lessonEnabled } from './features.ts';
import { lessonHref, pickCatalog } from './view.ts';

export async function LessonList({ session, studentId, subject }: { session: BffSession; studentId: string; subject: string }) {
  if (!lessonEnabled() || !isLessonSubject(subject)) return null;
  const r = await fetchLessonCatalog(session, studentId, subject);
  if (!r.ok && (r.status === 404 || r.status === 401 || r.status === 403)) return null;
  const lessons = r.ok ? pickCatalog(r.value) : null;
  return (
    <section aria-labelledby="subject-lessons" data-testid="lesson-catalog">
      <h2 id="subject-lessons">{LESSON.catalogHeading}</h2>
      {!lessons ? <p className="muted" data-unavailable="lesson-catalog">{LESSON.catalogUnavailable}</p>
        : lessons.length === 0 ? <p className="muted" data-testid="lesson-catalog-empty">{LESSON.catalogEmpty}</p> : (
          <ul data-testid="lesson-catalog-list">
            {lessons.map((l) => (
              <li key={`${l.lessonId}@${l.lessonVersion}`}>
                <a href={lessonHref(studentId, subject, l)} data-testid="lesson-catalog-link">{l.title}</a>
                {' '}<span className="muted">{LESSON.catalogSteps}</span>{' '}<span data-value="step-count">{l.stepCount}</span>
                {' '}<span className="muted">{LESSON.catalogStepsUnit}</span>
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}
