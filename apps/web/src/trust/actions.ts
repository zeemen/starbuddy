'use server';
// 信任界面（冲刺 5-H）的写操作：提出异议、录入考试安排与成绩、家长导出与删除（申请、二次确认、撤回）。
// 每个操作先过前端开关（关时直接按“没有找到”返回，不发请求），再做双重提交核对（表单 CSRF = cookie，actionSession），
// 然后连同浏览器的 Origin 转交 BFF；授权在 BFF 与数据库。结果只回编码，页面按 copy.ts 的占位显示。
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import type { FormResult } from '../auth/ResultForm.tsx';
import { actionSession } from '../lib/session.ts';
import { cancelErasure, confirmErasure, createExport, raiseDispute, requestErasure, submitExamPlan, submitExamScore } from '../lib/trust-api.ts';
import { trustEnabled, type TrustFeature } from '../shell/features.ts';
import { CONFIRM_TOKEN, DISPUTE_KINDS, IDEMPOTENCY_KEY, NEXT_STEPS, parseExamPlan, parseScore, UUID } from './view.ts';

const text = (f: FormData, k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string).trim() : '');
/** 页面所在的一侧（只有两个固定取值，决定成功后回到哪里；不做开放跳转） */
const base = (f: FormData, student: string): string => (text(f, 'side') === 'family' ? `/family/${student}` : `/s/${student}`);

function fail(code: string): FormResult {
  if (code === 'UNAUTHENTICATED') redirect('/login');
  return { code };
}

async function begin(feature: TrustFeature, f: FormData): Promise<{ s: NonNullable<Awaited<ReturnType<typeof actionSession>>>; student: string } | FormResult> {
  if (!trustEnabled(feature)) return { code: 'NOT_FOUND' };
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = text(f, 'student');
  if (!UUID.test(student)) return { code: 'NOT_FOUND' };
  return { s, student };
}

// ---------------------------------------------------------------- 异议

export async function raiseDisputeAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const b = await begin('dispute', f);
  if ('code' in b) return b;
  const claim = text(f, 'claim');
  const kind = text(f, 'kind');
  const key = text(f, 'idempotency_key');
  if (!UUID.test(claim) || !(DISPUTE_KINDS as readonly string[]).includes(kind) || !IDEMPOTENCY_KEY.test(key)) return { code: 'INVALID' };
  const r = await raiseDispute(b.s, b.student, claim, kind, key);
  if (!r.ok) return fail(r.code);
  revalidatePath(`${base(f, b.student)}/disputes`);
  // 只回下一步的编码（页面映射成占位文案）；契约外的编码按出错处理
  return { code: (NEXT_STEPS as readonly string[]).includes(r.value.next_step) ? r.value.next_step : 'INTERNAL' };
}

// ---------------------------------------------------------------- 考试

export async function examPlanAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const b = await begin('reconcile', f);
  if ('code' in b) return b;
  const plan = parseExamPlan(f);
  if (!plan) return { code: 'INVALID' };
  const r = await submitExamPlan(b.s, b.student, { ...plan });
  if (!r.ok) return fail(r.code);
  if (!UUID.test(r.value.exam_id)) return { code: 'INTERNAL' };
  redirect(`${base(f, b.student)}/exams/${r.value.exam_id}`);
}

export async function examScoreAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const b = await begin('reconcile', f);
  if ('code' in b) return b;
  const exam = text(f, 'exam');
  const score = parseScore(f.get('raw_score'));
  if (!UUID.test(exam)) return { code: 'NOT_FOUND' };
  if (score === null) return { code: 'INVALID' };
  const r = await submitExamScore(b.s, b.student, exam, score);
  if (!r.ok) return fail(r.code);
  revalidatePath(`${base(f, b.student)}/exams/${exam}`);
  return { code: 'saved' };
}

// ---------------------------------------------------------------- 家长数据权利（只给家长；BFF 对学生会话 403）

export interface ExportResult extends FormResult { job?: string; ref?: string }

export async function exportAction(_: ExportResult | null, f: FormData): Promise<ExportResult> {
  const b = await begin('data_rights', f);
  if ('code' in b) return b;
  const r = await createExport(b.s, b.student);
  if (!r.ok) return fail(r.code);
  // 一次性下载凭据只交给本页的下载表单（页面 no-store）；兑换时 BFF 再核对凭据、任务、学生、发起账号与绑定
  if (!UUID.test(r.value.export_job_id) || !/^dl_[0-9a-f]{64}$/.test(r.value.download_ref)) return { code: 'INTERNAL' };
  return { code: 'ready', job: r.value.export_job_id, ref: r.value.download_ref };
}

export interface ErasureResult extends FormResult { request?: string; token?: string }

export async function erasureRequestAction(_: ErasureResult | null, f: FormData): Promise<ErasureResult> {
  const b = await begin('data_rights', f);
  if ('code' in b) return b;
  const r = await requestErasure(b.s, b.student);
  if (!r.ok) return fail(r.code);
  revalidatePath(`/family/${b.student}/data`);
  if (!UUID.test(r.value.request_id) || !CONFIRM_TOKEN.test(r.value.confirm_token)) return { code: 'INTERNAL' };
  return { code: 'requested', request: r.value.request_id, token: r.value.confirm_token };
}

export async function erasureConfirmAction(_: ErasureResult | null, f: FormData): Promise<ErasureResult> {
  const b = await begin('data_rights', f);
  if ('code' in b) return b;
  const request = text(f, 'request');
  const token = text(f, 'confirm_token');
  // 二次确认：必须勾选“已了解”（浏览器端 required 之外服务端再核一次）
  if (!UUID.test(request) || !CONFIRM_TOKEN.test(token) || text(f, 'understood') !== 'yes') return { code: 'INVALID' };
  const r = await confirmErasure(b.s, b.student, request, token);
  if (!r.ok) return fail(r.code);
  revalidatePath(`/family/${b.student}/data`);
  return { code: 'confirmed' };
}

export async function erasureCancelAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const b = await begin('data_rights', f);
  if ('code' in b) return b;
  const request = text(f, 'request');
  if (!UUID.test(request)) return { code: 'NOT_FOUND' };
  const r = await cancelErasure(b.s, b.student, request);
  if (!r.ok) return fail(r.code);
  revalidatePath(`/family/${b.student}/data`);
  return { code: 'cancelled' };
}
