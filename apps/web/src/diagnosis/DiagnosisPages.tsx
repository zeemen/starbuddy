// 学生端数学诊断的入口与正式做题页（specs/diagnosis-student-entry 第二节第 2、3 条）：
//   - 入口 DiagnosisLink：只挂在学生端数学科目页；开关关、学科不是数学时返回 null（不渲染任何元素）。家长端不引用；
//   - 做题页 /s/{studentId}/learn/math/diagnosis：先过前端开关 STARBUDDY_DIAGNOSIS_UI（关时 404，不读会话、不发请求），
//     学科不是数学 404，再只认学生本人（requireOwnStudent，家长与别的学生 404）；BFF 再判断一次。正文是现成的诊断组件。
// 诊断中不显示任何评价，结束页不给结论（规则 11）；文案在 copy.ts（入口与返回是 {{TODO_COPY:diagnosis.*}} 占位，人写人审）。
import { notFound } from 'next/navigation';
import { requireOwnStudent } from '../shell/guard.ts';
import { DIAGNOSIS_COPY, DIAGNOSIS_ENTRY_COPY } from './copy.ts';
import { DiagnosisStudent } from './DiagnosisStudent.tsx';
import { diagnosisEnabled, isDiagnosisSubject } from './features.ts';

const pagePath = (studentId: string, subject: string) => `/s/${studentId}/learn/${subject}/diagnosis`;

/** 科目页上的入口：开关关、学科不在范围时返回 null。只在学生端科目页使用，家长端不引用 */
export function DiagnosisLink({ studentId, subject }: { studentId: string; subject: string }) {
  if (!diagnosisEnabled() || !isDiagnosisSubject(subject)) return null;
  return <p><a href={pagePath(studentId, subject)} data-testid="diagnosis-entry">{DIAGNOSIS_ENTRY_COPY.entry}</a></p>;
}

export async function DiagnosisStudentPage({ studentId, subject }: { studentId: string; subject: string }) {
  if (!diagnosisEnabled()) notFound();
  if (!isDiagnosisSubject(subject)) notFound();
  const ps = await requireOwnStudent(studentId);
  return (
    <main data-testid="diagnosis-student-page" data-subject={subject}>
      <h1>{DIAGNOSIS_COPY.heading}</h1>
      <p><a href={`/s/${studentId}/learn/${subject}`} data-testid="diagnosis-back">{DIAGNOSIS_ENTRY_COPY.back}</a></p>
      <DiagnosisStudent csrf={ps.session.csrf} student={studentId} />
    </main>
  );
}
