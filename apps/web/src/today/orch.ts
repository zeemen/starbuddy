// 今日计划跨科编排（PLT-11）：跨科列表每条“去做”的地址。只在学生本人的今日页用（src/today/OrderedTaskList.tsx）。
// 复测条目 → 复测页、再练条目 → 再练页（对应开关打开、学科有入口、条目引用合格时）；其余（计划任务、引用为空、对应页面开关关）→ 科目页。
// 学科码与条目引用都按格式核对后才拼进地址。
import { isPracticeSubject, practiceEnabled } from '../practice/features.ts';
import { isRetestSubject, retestEnabled } from '../retest/features.ts';

type Env = Record<string, string | undefined>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SUBJECT = /^[a-z]{1,16}$/;

export interface GoTarget { source: 'retest' | 'practice' | 'plan'; subject: string; entry_id: string | null }

export function goHref(studentId: string, t: GoTarget, env: Env = process.env): string | null {
  if (!UUID.test(studentId) || !SUBJECT.test(t.subject)) return null;
  const subjectPage = `/s/${studentId}/learn/${t.subject}`;
  const entry = t.entry_id !== null && UUID.test(t.entry_id) ? t.entry_id : null;
  if (entry && t.source === 'retest' && retestEnabled(env) && isRetestSubject(t.subject)) return `${subjectPage}/retest/${entry}`;
  if (entry && t.source === 'practice' && practiceEnabled(env) && isPracticeSubject(t.subject)) return `${subjectPage}/practice/${entry}`;
  return subjectPage;
}
