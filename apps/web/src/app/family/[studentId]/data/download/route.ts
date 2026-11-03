// 导出包下载（冲刺 5-H，开关 data_rights）：数据权利页以 fetch 提交（CSRF、导出任务 ID、一次性下载凭据）→ 本路由核对 CSRF（表单 = cookie）
// 与会话（家长、且路径里的孩子在会话的已绑定孩子里）→ 转 BFF 兑换（BFF 再核凭据、任务、学生、发起账号、有效绑定与删除状态，
// 兑换一次即作废）→ 原样返回导出包：no-store、按附件下载、nosniff。凭据失效、已用过、不是本人、不是已绑定家长：404
// （页面提示重新生成）；会话失效 401。开关关时 404（不读会话、不发请求）。只接受 POST：其他方法一律 404 + no-store，凭据不进地址栏与日志。
import { actionSession, pageSession } from '../../../../../lib/session.ts';
import { downloadExport } from '../../../../../lib/trust-api.ts';
import { trustEnabled } from '../../../../../shell/features.ts';
import { DOWNLOAD_REF, UUID } from '../../../../../trust/view.ts';

export const dynamic = 'force-dynamic';

const NO_STORE = { 'cache-control': 'no-store' };
const gone = (status = 404): Response => new Response(null, { status, headers: NO_STORE });

export async function POST(req: Request, { params }: { params: Promise<{ studentId: string }> }): Promise<Response> {
  if (!trustEnabled('data_rights')) return gone();
  const { studentId } = await params;
  if (!UUID.test(studentId)) return gone();
  let f: FormData;
  try {
    f = await req.formData();
  } catch {
    return gone(400);
  }
  const s = await actionSession(f.get('csrf'));
  if (!s) return gone(403);
  // 只给家长、只对会话里已绑定的孩子（规则 5；BFF 与库函数再核一次）；学生会话、别家孩子与不存在同一个 404
  const ps = await pageSession();
  if (!ps) return gone(401);
  if (ps.me.account_kind !== 'guardian' || !ps.me.children.some((c) => c.student_id === studentId)) return gone();
  const job = f.get('job');
  const ref = f.get('download_ref');
  if (typeof job !== 'string' || !UUID.test(job) || typeof ref !== 'string' || !DOWNLOAD_REF.test(ref)) return gone();
  const r = await downloadExport(s, studentId, job, ref);
  if (!r.ok) return gone(r.status === 401 ? 401 : 404);
  return new Response(r.body, {
    status: 200,
    headers: {
      ...NO_STORE,
      'content-type': 'application/json; charset=utf-8',
      'content-disposition': `attachment; filename="starbuddy-export-${job}.json"`,
      'x-content-type-options': 'nosniff',
    },
  });
}

/** 其他方法一律 404（不是 405：与不存在的路径一样，不暴露这里有下载接口） */
const notHere = async (): Promise<Response> => gone();
export { notHere as GET, notHere as HEAD, notHere as PUT, notHere as PATCH, notHere as DELETE, notHere as OPTIONS };
