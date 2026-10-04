'use server';
// 审核台的写操作（server action）：在服务端取 staff 会话令牌、校验表单，再调审核台后端（令牌由后端经库函数解析）。
// Next 的 server action 自带同源校验（Origin 必须与 Host 一致），浏览器不直连后端。
// 结果只回传提示码与后端给的规则说明，由 ActionForm 显示；成功后重新渲染当前页，拿到新的摘要。
import { revalidatePath } from 'next/cache';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { currentSession, staffCookieName, staffCookieOptions } from '../../lib/review-session.ts';
import {
  ADMISSIONS, LICENSE_STATUSES, SOURCE_TYPES, applyAdmission, editNode, isBasis, isCommit, isDigest, isItemId, isNodeId, isResolutionCode,
  isReviewLevel, isSourceRef, isSubject, isTaskId, isVersionId, lines, loginRequest, logoutRequest, publishContentVersion, publishScheme,
  publishVersion, releaseTask, resolveTask, retireContentVersion, retireVersion, signAdmission, signChange, signContentVersion, signPage,
  signPolicyRecord, signScheme, signSpotCheck, signVersion, takeTask, type AdmissionSign, type Result,
} from '../../lib/review.ts';

export interface ActionState {
  code: string;
  detail?: string;
}

const INVALID: ActionState = { code: 'INVALID_PARAMETER' };
const text = (f: FormData, k: string): string => {
  const v = f.get(k);
  return typeof v === 'string' ? v : '';
};

const staff = currentSession;

/** 登录：成功写会话 cookie 并进审核台首页；失败只回一个统一的提示（不区分原因） */
export async function loginAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const login = text(f, 'login_name').trim();
  const password = text(f, 'password');
  const code = text(f, 'code').trim();
  // 口令最多 72 字节（bcrypt 只看前 72 字节，库里同样拦）
  if (!/^[a-z][a-z0-9_.-]{2,31}$/.test(login) || password.length < 1 || Buffer.byteLength(password, 'utf8') > 72 || !/^[0-9]{6}$/.test(code)) {
    return { code: 'LOGIN_FAILED' };
  }
  const r = await loginRequest(login, password, code);
  if (!r.ok) return { code: r.problem.status === 401 ? 'LOGIN_FAILED' : 'INTERNAL' };
  (await cookies()).set(staffCookieName(), r.value.token, staffCookieOptions());
  redirect('/review');
}

/**
 * 退出：撤销库里的会话，删 cookie，回登录页。
 * 删 cookie 要带与写入时相同的属性（__Host- 前缀要求 Secure、Path=/，否则浏览器不认这次删除，安全审查 PR 4 B-1）；
 * 后端撤销失败时 cookie 照样删掉，但登录页明确提示“退出未完成”（库里的会话可能还在，空闲 30 分钟后失效）。
 */
export async function logoutAction(): Promise<void> {
  const token = await currentSession();
  const r = token ? await logoutRequest(token) : null;
  (await cookies()).set(staffCookieName(), '', { ...staffCookieOptions(), maxAge: 0 });
  redirect(r && !r.ok ? '/review/login?logout=incomplete' : '/review/login');
}

function done<T>(r: Result<T>, okCode: string, version: string): ActionState {
  if (!r.ok) return { code: r.problem.code, detail: r.problem.detail };
  revalidatePath(`/review/map-versions/${version}`);
  return { code: okCode };
}

export async function editNodeAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const version = text(f, 'version');
  const node = text(f, 'node');
  const digest = text(f, 'expected_digest');
  if (!isVersionId(version) || !isNodeId(node) || !isDigest(digest)) return INVALID;
  const body = {
    expected_digest: digest,
    includes: lines(text(f, 'includes')),
    excludes: lines(text(f, 'excludes')),
    examples: lines(text(f, 'examples')),
  };
  return done(await editNode(who, version, node, body), 'saved', version);
}

export async function signVersionAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const version = text(f, 'version');
  const digest = text(f, 'expected_digest');
  const decision = text(f, 'decision');
  const basis = text(f, 'basis_code');
  if (!isVersionId(version) || !isDigest(digest) || !['approved', 'rejected'].includes(decision) || (basis !== '' && !isBasis(basis))) {
    return INVALID;
  }
  const body = { decision, expected_digest: digest, ...(basis ? { basis_code: basis } : {}) };
  return done(await signVersion(who, version, body), 'signed', version);
}

export async function signChangeAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const version = text(f, 'version');
  const change = text(f, 'change');
  const digest = text(f, 'expected_digest');
  const decision = text(f, 'decision');
  if (!isVersionId(version) || !isVersionId(change) || !isDigest(digest) || !['approved', 'rejected'].includes(decision)) return INVALID;
  return done(await signChange(who, change, { decision, expected_digest: digest }), 'change_signed', version);
}

export async function publishAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const version = text(f, 'version');
  const digest = text(f, 'expected_digest');
  if (!isVersionId(version) || !isDigest(digest)) return INVALID;
  return done(await publishVersion(who, version, { expected_digest: digest }), 'published', version);
}

export async function retireAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const version = text(f, 'version');
  const basis = text(f, 'basis_code');
  // 撤下必须勾选确认（契约审查 PR 3 S8）
  if (!isVersionId(version) || !isBasis(basis) || text(f, 'confirm') !== 'yes') return INVALID;
  return done(await retireVersion(who, version, { basis_code: basis }), 'retired', version);
}

// ---------- 统一复核队列（specs/review-queue-sprint2 设计要点 2）：领取、放回、结案 ----------
// 前端只校验格式（任务 id 是 UUID、结案码合格式）；是否 active staff、是否本人领取、结案码是否在白名单且适用，都由库函数核对。
function queueDone<T>(r: Result<T>, okCode: string, task: string): ActionState {
  if (!r.ok) return { code: r.problem.code, detail: r.problem.detail };
  revalidatePath(`/review/queue/${task}`);
  revalidatePath('/review/queue');
  return { code: okCode };
}

export async function takeTaskAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const task = text(f, 'task');
  if (!isTaskId(task)) return INVALID;
  return queueDone(await takeTask(who, task), 'taken', task);
}

export async function releaseTaskAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const task = text(f, 'task');
  if (!isTaskId(task)) return INVALID;
  return queueDone(await releaseTask(who, task), 'released', task);
}

export async function resolveTaskAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const task = text(f, 'task');
  const code = text(f, 'resolution_code');
  if (!isTaskId(task) || !isResolutionCode(code)) return INVALID;
  return queueDone(await resolveTask(who, task, code), 'resolved', task);
}

// ---------- 冲刺 4：内容版本与准入、考情、页面签字与事后抽检 ----------
// 前端只校验格式；是否 active staff、摘要是否一致、人数与发布规则，都由库函数核对（看到的就是签的：每个表单带当前摘要）。
function doneAt<T>(r: Result<T>, okCode: string, ...paths: string[]): ActionState {
  if (!r.ok) return { code: r.problem.code, detail: r.problem.detail };
  for (const p of paths) revalidatePath(p);
  return { code: okCode };
}
const DECISIONS = ['approved', 'rejected'];

export async function signContentAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const id = text(f, 'cv');
  const digest = text(f, 'expected_digest');
  const decision = text(f, 'decision');
  const basis = text(f, 'basis_code');
  if (!isVersionId(id) || !isDigest(digest) || !DECISIONS.includes(decision) || (basis !== '' && !isBasis(basis))) return INVALID;
  const body = { decision, expected_digest: digest, ...(basis ? { basis_code: basis } : {}) };
  return doneAt(await signContentVersion(who, id, body), 'signed', `/review/content/${id}`);
}

export async function publishContentAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const id = text(f, 'cv');
  const digest = text(f, 'expected_digest');
  if (!isVersionId(id) || !isDigest(digest)) return INVALID;
  return doneAt(await publishContentVersion(who, id, { expected_digest: digest }), 'published', `/review/content/${id}`);
}

export async function retireContentAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const id = text(f, 'cv');
  const basis = text(f, 'basis_code');
  if (!isVersionId(id) || !isBasis(basis) || text(f, 'confirm') !== 'yes') return INVALID;
  return doneAt(await retireContentVersion(who, id, { basis_code: basis }), 'retired', `/review/content/${id}`);
}

const opt = (f: FormData, k: string): string | undefined => {
  const v = text(f, k).trim();
  return v === '' ? undefined : v;
};

export async function signAdmissionAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const cv = text(f, 'cv');
  const subject = text(f, 'subject');
  const item = text(f, 'item_id');
  const to = text(f, 'to_admission');
  const decision = text(f, 'decision');
  const digest = text(f, 'expected_digest');
  const level = opt(f, 'review_level');
  const ref = opt(f, 'source_ref');
  const type = opt(f, 'source_type');
  const license = opt(f, 'license_status');
  if (!isVersionId(cv) || !isSubject(subject) || !isItemId(item) || !(ADMISSIONS as readonly string[]).includes(to)
      || !DECISIONS.includes(decision) || !isDigest(digest) || (level !== undefined && !isReviewLevel(level))
      || (ref !== undefined && !isSourceRef(ref)) || (type !== undefined && !(SOURCE_TYPES as readonly string[]).includes(type))
      || (license !== undefined && !(LICENSE_STATUSES as readonly string[]).includes(license))) {
    return INVALID;
  }
  const body: AdmissionSign = { subject, item_id: item, to_admission: to, decision, expected_digest: digest,
    ...(level ? { review_level: level } : {}), ...(ref ? { source_ref: ref } : {}), ...(type ? { source_type: type } : {}),
    ...(license ? { license_status: license } : {}) };
  return doneAt(await signAdmission(who, body), 'signed', `/review/content/${cv}`);
}

export async function applyAdmissionAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const cv = text(f, 'cv');
  const ids = f.getAll('review_id').filter((x): x is string => typeof x === 'string');
  if (!isVersionId(cv) || ids.length === 0 || ids.length > 200 || !ids.every(isVersionId)) return INVALID;
  return doneAt(await applyAdmission(who, ids), 'applied', `/review/content/${cv}`);
}

export async function signSchemeAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const id = text(f, 'scheme');
  const digest = text(f, 'expected_digest');
  const decision = text(f, 'decision');
  if (!isVersionId(id) || !isDigest(digest) || !DECISIONS.includes(decision)) return INVALID;
  return doneAt(await signScheme(who, id, { decision, expected_digest: digest }), 'signed', `/review/policy/${id}`);
}

export async function publishSchemeAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const id = text(f, 'scheme');
  const digest = text(f, 'expected_digest');
  if (!isVersionId(id) || !isDigest(digest)) return INVALID;
  return doneAt(await publishScheme(who, id, { expected_digest: digest }), 'published', `/review/policy/${id}`);
}

export async function signPolicyRecordAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const id = text(f, 'object_id');
  const type = text(f, 'object_type');
  const digest = text(f, 'expected_digest');
  const decision = text(f, 'decision');
  if (!isVersionId(id) || !['source_document', 'admission_line', 'school'].includes(type) || !isDigest(digest) || !DECISIONS.includes(decision)) {
    return INVALID;
  }
  return doneAt(await signPolicyRecord(who, id, { object_type: type, decision, expected_digest: digest }), 'signed', '/review/policy');
}

export async function signPageAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const page = text(f, 'page_id');
  const commit = text(f, 'page_commit');
  const decision = text(f, 'decision');
  if (!isNodeId(page) || !isCommit(commit) || !DECISIONS.includes(decision)) return INVALID;
  return doneAt(await signPage(who, { page_id: page, page_commit: commit, decision }), 'signed', '/review/pages');
}

export async function spotCheckAction(_: ActionState | null, f: FormData): Promise<ActionState> {
  const who = await staff();
  if (!who) return { code: 'UNAUTHENTICATED' };
  const page = text(f, 'page_id');
  const commit = text(f, 'page_commit');
  const decision = text(f, 'decision');
  if (!isNodeId(page) || !isCommit(commit) || !DECISIONS.includes(decision)) return INVALID;
  return doneAt(await signSpotCheck(who, { page_id: page, page_commit: commit, decision }), 'recorded', '/review/spot-checks', '/review/pages');
}
