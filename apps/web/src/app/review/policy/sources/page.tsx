// 官方来源清单（specs/policy-decisions-v0.9.1 第 4 节，迁移 0048）：来源文件的链接只能落在清单里在用的主机名上。
// 只给有 staff 权限 policy_source_admin（DBA 授予）的教研：没有权限时后端 403，这里只显示“没有权限”，不列清单。
// 新增（已下线的重新启用）与下线经库函数核对权限、规范化主机名（小写、去结尾的点；拒 IP、通配符、端口、路径、只有公共后缀）；清单行只下线不删除。
// 国际化域名以 punycode（xn--…）登记，页面同时显示 Unicode 形式，便于核对同形字。说明（发布机关）写进公共层，不得填个人信息。
// 主机名一律放在 data-value 元素里（运行时 DOM 泄露检查：点分字符串只能出现在 data-value 里）。
import { redirect } from 'next/navigation';
import { hostView } from '../../../../lib/source-host.ts';
import { currentSession } from '../../../../lib/review-session.ts';
import { RESULT_LABEL, SOURCE_STATUS_LABEL, formatTime, listSourceDomains } from '../../../../lib/review.ts';
import { ActionForm } from '../../ActionForm.tsx';
import { addSourceAction, retireSourceAction } from '../../actions.ts';

export const dynamic = 'force-dynamic';

export default async function SourceDomainsPage() {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const r = await listSourceDomains(staff);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>官方来源清单</h1>
      <p className="muted">来源文件的链接只能落在清单里“在用”的主机名上。下线后，已挂在它下面的来源文件仍能更新核对日期或撤下，但不能再新建。清单只下线、不删除。</p>
      {!r.ok ? (
        r.problem.status === 403 ? (
          <p className="review-result error" data-testid="sources-forbidden">{RESULT_LABEL['SOURCE_ADMIN_REQUIRED']}</p>
        ) : (
          <p className="review-result error">读取失败（{RESULT_LABEL[r.problem.code] ?? RESULT_LABEL['INTERNAL']}）</p>
        )
      ) : (
        <>
          <table className="review-table" data-testid="sources">
            <caption>主机名（国际化域名另列 Unicode 形式，请核对是否有形近的字符）、发布机关、状态与登记时间</caption>
            <thead>
              <tr><th scope="col">主机名</th><th scope="col">Unicode 形式</th><th scope="col">发布机关</th><th scope="col">状态</th><th scope="col">登记时间</th><th scope="col">操作</th></tr>
            </thead>
            <tbody>
              {r.value.items.map((d, i) => {
                const v = hostView(d.host);
                return (
                  <tr key={d.host} data-testid={`source-${d.status === 'active' ? 'active' : 'retired'}-${i + 1}`}>
                    <td><code data-value="host">{d.host}</code></td>
                    <td>
                      {!v.idn ? '—' : (
                        <>
                          {v.unicode ? <code data-value="host">{v.unicode}</code> : <strong data-testid={`idn-undecodable-${i + 1}`}>无法解码</strong>}{' '}
                          <span className="muted">（国际化域名）</span>
                          {v.suspicious ? (
                            <strong className="review-result error" role="note" data-testid={`idn-warning-${i + 1}`}>
                              {' '}注意：含非汉字的非 ASCII 字符、混合书写系统或无法解码，可能是仿冒官网的同形字，请逐字核对发布机关官网
                            </strong>
                          ) : null}
                        </>
                      )}
                    </td>
                    <td>{d.issuer}</td>
                    <td>{SOURCE_STATUS_LABEL[d.status] ?? d.status}</td>
                    <td>{formatTime(d.added_at)}</td>
                    <td>
                      {d.status === 'active' ? (
                        <ActionForm action={retireSourceAction} labels={RESULT_LABEL} testId={`retire-source-${i + 1}`}>
                          <input type="hidden" name="host" value={d.host} />
                          <input type="checkbox" id={`retire-source-confirm-${i + 1}`} name="confirm" value="yes" required />{' '}
                          <label htmlFor={`retire-source-confirm-${i + 1}`}>我确认下线 <span data-value="host">{d.host}</span></label>{' '}
                          <button type="submit">下线 <span data-value="host">{d.host}</span></button>
                        </ActionForm>
                      ) : <span className="muted">已下线（可在下面重新加入）</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <h2>加入或重新启用</h2>
          <ActionForm action={addSourceAction} labels={RESULT_LABEL} testId="add-source">
            <p>
              <label htmlFor="source-host">主机名</label>{' '}
              <input id="source-host" name="host" required maxLength={253} autoComplete="off" spellCheck={false} aria-describedby="source-host-hint" />
            </p>
            <p id="source-host-hint" className="muted">只填主机名，不带协议、端口和路径；国际化域名填 punycode 形式（以 xn-- 开头的那一段）。不收 IP、通配符和只有公共后缀的主机。</p>
            <p>
              <label htmlFor="source-note">说明（发布机关）</label>{' '}
              <input id="source-note" name="note" required maxLength={64} autoComplete="off" aria-describedby="source-note-hint" />
            </p>
            <p id="source-note-hint" className="muted">公开信息，不得填写个人信息（只写发布机关名称，1–64 字）。</p>
            <button type="submit">加入清单</button>
          </ActionForm>
        </>
      )}
      <p><a href="/review/policy">返回考情政策</a></p>
    </main>
  );
}
