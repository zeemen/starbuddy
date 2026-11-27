// 学生端讲评课页 /s/{studentId}/learn/{subject}/lesson/{lessonId}（specs/stage-lesson-session ③b-5）：只挂组件，组件里先过开关闸门。
// 普通页面（specs/stage-production-build）：生产构建里也有这个路由，但讲评课开关（STARBUDDY_LESSON）只在 dev / test 生效，
// staging、pilot、生产一律 404（LessonPage 先过开关，再读会话）；BFF 与学习服务的讲评课开关在 staging 照旧拒绝启动。
import { LessonPage } from '../../../../../../../lesson/LessonPage.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params, searchParams }: {
  params: Promise<{ studentId: string; subject: string; lessonId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { studentId, subject, lessonId } = await params;
  return <LessonPage studentId={studentId} subject={subject} lessonId={lessonId} search={await searchParams} />;
}
