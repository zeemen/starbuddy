'use server';
// 审核台的写操作（server action）：在服务端读开发 staff 身份、校验表单，再调审核台后端。
// Next 的 server action 自带同源校验（Origin 必须与 Host 一致），浏览器不直连后端。
// 结果只回传提示码与后端给的规则说明，由 ActionForm 显示；成功后重新渲染当前页，拿到新的摘要。
import { revalidatePath } from 'next/cache';
import { currentStaff } from '../../lib/review-session.ts';
import {
  editNode, isBasis, isDigest, isNodeId, isVersionId, lines, publishVersion, retireVersion, signChange, signVersion,
  type Result,
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

const staff = currentStaff;

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
