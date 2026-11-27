// 学生端讲评课页 /s/{studentId}/learn/{subject}/lesson/{lessonId}（specs/stage-lesson-session ③b-5）：只挂组件，组件里先过开关闸门。
// 开发页（文件名 page.dev.tsx）：讲评课开关只在 dev / test，页面又以相对路径引用 packages/stage（Turbopack 根目录只在含开发页的构建里
// 放到仓库根，next.config.ts），所以只在开发、测试构建里存在；运行时再核对环境与本机 Host，否则 404（与 /dev/stage 同一口径）。
import { headers } from 'next/headers';
import { notFound } from 'next/navigation';
import { devRuntimeAllowed } from '../../../../../../../lib/dev-pages.ts';
import { LessonPage } from '../../../../../../../lesson/LessonPage.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params, searchParams }: {
  params: Promise<{ studentId: string; subject: string; lessonId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (!devRuntimeAllowed((await headers()).get('host'))) notFound();
  const { studentId, subject, lessonId } = await params;
  return <LessonPage studentId={studentId} subject={subject} lessonId={lessonId} search={await searchParams} />;
}
