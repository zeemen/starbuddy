// 编译页面（specs/release-gate-sprint4 冲刺 3 段，第 6 节）：页面改动在页面仓库托管平台的合并请求网页里看，这里
// 列出待审合并请求、已合并页面与审核记录的对账状态，链到托管平台网页（地址模板由审核台后端配置给出）；
// 冲刺 4 起审核人在这里对合并请求的头提交逐个签字（PR A 待人确认 1 的默认）。
// 只显示公共层内容（页面 ID、合并请求编号、提交号、风险、人数），不显示托管平台用户名与 staff 账号。
import { redirect } from 'next/navigation';
import { currentSession } from '../../../lib/review-session.ts';
import { LEVEL_LABEL, RESULT_LABEL, formatTime, getPages, pageProblemLabel, safeHttpsUrl, type PageEntry } from '../../../lib/review.ts';
import { ActionForm } from '../ActionForm.tsx';
import { signPageAction } from '../actions.ts';

export const dynamic = 'force-dynamic';

function MrLink({ e }: { e: PageEntry }) {
  const href = safeHttpsUrl(e.url);
  return href ? (
    <a href={href} rel="noopener noreferrer" target="_blank">合并请求 !{e.mr_id}（托管平台）</a>
  ) : (
    <span>合并请求 !{e.mr_id}</span>
  );
}

export default async function PagesPage() {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const r = await getPages(staff);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>编译页面</h1>
      <p className="muted">
        页面在托管平台的合并请求里审核；这里只列待审合并请求与对账状态（托管平台合并记录 × 审核记录）。对账失败的页面，合并窗口跳过，直到人处理。
      </p>
      {!r.ok ? (
        <p className="review-result error" data-testid="pages-error">读取失败（{r.problem.code}）</p>
      ) : !r.value.configured ? (
        <p data-testid="pages-unconfigured">审核台后端没有配置页面仓库的合并记录导出，暂无页面数据。</p>
      ) : (
        <>
          <p data-testid="pages-summary" data-ok={r.value.ok ? 'true' : 'false'}>
            合并记录导出于 {formatTime(r.value.exported_at ?? null)}（{r.value.repository} · {r.value.branch}）；
            {r.value.ok ? '对账通过' : `对账失败，合并窗口跳过 ${r.value.held_pages?.length ?? 0} 页：${(r.value.held_pages ?? []).join('、')}`}
          </p>

          <h2>待审合并请求</h2>
          {(r.value.pending ?? []).length === 0 ? (
            <p data-testid="pages-no-pending">没有待审的合并请求</p>
          ) : (
            <table className="review-table" data-testid="pages-pending">
              <caption>待审合并请求（审核人数含审核台已记录的签字）</caption>
              <thead>
                <tr>
                  <th scope="col">页面</th><th scope="col">风险</th><th scope="col">所需人数</th><th scope="col">托管平台已批准</th>
                  <th scope="col">审核记录已签</th><th scope="col">提交</th><th scope="col">去审核</th>
                </tr>
              </thead>
              <tbody>
                {(r.value.pending ?? []).map((e) => (
                  <tr key={`${e.mr_id}:${e.page_id}`} data-page={e.page_id}>
                    <td><code>{e.page_id}</code></td>
                    <td>{LEVEL_LABEL[e.risk] ?? e.risk}</td>
                    <td>{e.required}</td>
                    <td>{e.approvals_platform}</td>
                    <td>{e.approvals_review}</td>
                    <td><code>{e.head_commit.slice(0, 12)}</code></td>
                    <td><MrLink e={e} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {(() => {
            // 页面签字入口（PR A 待人确认 1 的默认：审核人在审核台逐个签）：只能签导出里合并请求的头提交，风险取合并请求标注
            const signable = [...(r.value.pending ?? []), ...(r.value.merged ?? []).filter((e) => e.status === 'failed' && e.risk !== 'low')];
            return signable.length === 0 ? null : (
              <section aria-labelledby="h-page-sign">
                <h2 id="h-page-sign">在审核台签字</h2>
                <p className="muted">先在托管平台网页上核对改了哪几句，再在这里签字；签的是合并请求的头提交（看到的就是签的）。同一账号对同一页面同一提交只能签一次。</p>
                <ul className="review-list" data-testid="page-sign">
                  {signable.map((e) => (
                    <li key={`sign:${e.mr_id}:${e.page_id}`} data-page={e.page_id}>
                      <p><code>{e.page_id}</code> · 头提交 <code>{e.head_commit.slice(0, 12)}</code> · {LEVEL_LABEL[e.risk] ?? e.risk}风险 · 需 {e.required} 人，已签 {e.approvals_review}</p>
                      <ActionForm action={signPageAction} labels={RESULT_LABEL} testId={`page-sign-${e.page_id}`}>
                        <input type="hidden" name="page_id" value={e.page_id} />
                        <input type="hidden" name="page_commit" value={e.head_commit} />
                        <button type="submit" name="decision" value="approved">批准这个提交</button>{' '}
                        <button type="submit" name="decision" value="rejected">驳回</button>
                      </ActionForm>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })()}

          <h2>已合并页面的对账</h2>
          {(r.value.merged ?? []).length === 0 ? (
            <p>没有已合并的页面改动</p>
          ) : (
            <table className="review-table" data-testid="pages-merged">
              <caption>已合并页面：每页按最近一次合并，托管平台批准人与审核记录逐页比对</caption>
              <thead>
                <tr>
                  <th scope="col">页面</th><th scope="col">风险</th><th scope="col">所需人数</th><th scope="col">托管平台批准</th>
                  <th scope="col">审核记录批准</th><th scope="col">头提交 → 合并提交</th><th scope="col">对账</th><th scope="col">合并请求</th>
                </tr>
              </thead>
              <tbody>
                {(r.value.merged ?? []).map((e) => (
                  <tr key={`${e.mr_id}:${e.page_id}`} data-page={e.page_id} data-status={e.status}>
                    <td><code>{e.page_id}</code></td>
                    <td>{LEVEL_LABEL[e.risk] ?? e.risk}</td>
                    <td>{e.required}</td>
                    <td>{e.approvals_platform}</td>
                    <td>{e.approvals_review}</td>
                    <td><code>{e.head_commit.slice(0, 12)}</code> → <code>{(e.merge_commit ?? '').slice(0, 12)}</code></td>
                    <td>{e.status === 'ok' ? '通过' : `失败：${(e.problems ?? []).map(pageProblemLabel).join('；')}`}</td>
                    <td><MrLink e={e} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {(r.value.orphans ?? []).length > 0 ? (
            <>
              <h2>对不上合并请求的审核记录</h2>
              <table className="review-table" data-testid="pages-orphans">
                <caption>审核记录里有、托管平台合并记录里没有的页面提交</caption>
                <thead>
                  <tr>
                    <th scope="col">页面</th><th scope="col">提交</th><th scope="col">记录数</th><th scope="col">问题</th><th scope="col">是否扣页</th>
                  </tr>
                </thead>
                <tbody>
                  {(r.value.orphans ?? []).map((o) => (
                    <tr key={`${o.page_id}:${o.page_commit}`} data-page={o.page_id} data-blocking={o.blocking ? 'true' : 'false'}>
                      <td><code>{o.page_id}</code></td>
                      <td><code>{o.page_commit.slice(0, 12)}</code></td>
                      <td>{o.records}</td>
                      <td>{o.problems.map(pageProblemLabel).join('；')}</td>
                      <td>{o.blocking ? '扣页（对最新头提交重新签字即解除）' : '只列出（已在最新头提交上重签，或不在本次导出范围）'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </>
      )}
      <p><a href="/review/spot-checks">低风险事后抽检</a> · <a href="/review/records?subject=aya">版本记录（含页面行）</a></p>
    </main>
  );
}
