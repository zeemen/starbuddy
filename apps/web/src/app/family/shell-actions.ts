'use server';
// 家长端（H4）的写操作：建档、记录同意、选科（开通 / 暂停）、时长设置。
// 双重提交：表单里的 CSRF 令牌必须等于 CSRF cookie（actionSession），再连同浏览器的 Origin 转交 BFF；BFF 核对它与会话绑定、
// 按路由的 auth 拦截（学生会话一律 403）、授权交给数据库。前端不判断能不能选、上限是多少：结果码原样映射成提示（copy.ts）。
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import type { FormResult } from '../../auth/ResultForm.tsx';
import { consentVersions } from '../../lib/consent-version.ts';
import { actionSession } from '../../lib/session.ts';
import { createStudent, enrollSubject, pauseSubject, recordConsent, setTimeGuard } from '../../lib/shell-api.ts';
import { isUuid } from '../../lib/star-map.ts';

const text = (f: FormData, k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string).trim() : '');
const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const SUBJECT = /^(math|chinese|politics)$/;

// 同意文本版本号：两份文书分别配置（src/lib/consent-version.ts，LT-01 安全审查 S7）；合规轨交付前 dev / test 是合成版本
// （BFF 在 staging、pilot 与生产拒收 synthetic-*，待人确认 #13）。staging 没配齐时不写任何一条。

function fail(code: string): FormResult {
  if (code === 'UNAUTHENTICATED') redirect('/login');
  return { code };
}

export async function createStudentAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const grade = Number(text(f, 'grade'));
  const region = text(f, 'region') || 'nanjing';
  const birth = text(f, 'birth_year_month');
  if (![7, 8, 9].includes(grade) || (birth !== '' && !/^(19|20)[0-9]{2}-(0[1-9]|1[0-2])$/.test(birth))) return { code: 'INVALID' };
  const r = await createStudent(s, { grade, region, birth_year_month: birth === '' ? null : birth });
  if (!r.ok) return fail(r.code);
  redirect('/family');
}

export async function consentAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = text(f, 'student');
  if (!isUuid(student)) return { code: 'NOT_FOUND' };
  // 两条都先确认可用再写（只配了一份或是合成版本时一条也不写）；BFF 拒收时原样映射错误码
  const versions = consentVersions();
  if (versions === null) return { code: 'INVALID' };
  const [privacy, guardian] = versions;
  for (const [type, version] of [['privacy_policy', privacy], ['guardian_consent_under14', guardian]] as const) {
    const r = await recordConsent(s, student, type, version);
    if (!r.ok) return fail(r.code);
  }
  revalidatePath('/family');
  return { code: 'recorded' };
}

const subjectOp = (op: typeof enrollSubject) => async (_: FormResult | null, f: FormData): Promise<FormResult> => {
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = text(f, 'student');
  const subject = text(f, 'subject');
  if (!isUuid(student) || !SUBJECT.test(subject)) return { code: 'NOT_FOUND' };
  const r = await op(s, student, subject);
  if (!r.ok) return fail(r.code);
  revalidatePath(`/family/${student}`);
  return { code: r.value.outcome };
};
export const enrollAction = async (prev: FormResult | null, f: FormData): Promise<FormResult> => subjectOp(enrollSubject)(prev, f);
export const pauseAction = async (prev: FormResult | null, f: FormData): Promise<FormResult> => subjectOp(pauseSubject)(prev, f);

export async function timeGuardAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = text(f, 'student');
  if (!isUuid(student)) return { code: 'NOT_FOUND' };
  const capRaw = text(f, 'daily_cap_minutes');
  const start = text(f, 'curfew_start');
  const end = text(f, 'curfew_end');
  const cap = capRaw === '' ? null : Number(capRaw);
  if ((cap !== null && (!Number.isInteger(cap) || cap < 0 || cap > 1440)) || (start !== '' && !HHMM.test(start)) || (end !== '' && !HHMM.test(end))
      || (start === '') !== (end === '')) {
    return { code: 'INVALID' };
  }
  const r = await setTimeGuard(s, student, { daily_cap_minutes: cap, curfew_start: start || null, curfew_end: end || null });
  if (!r.ok) return fail(r.code);
  revalidatePath(`/family/${student}/time`);
  return { code: r.value.outcome };
}
