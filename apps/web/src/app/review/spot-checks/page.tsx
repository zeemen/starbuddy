// 低风险合并窗口的事后抽检（冲刺 4，spec 第 2 节）：当天（北京时间）合并的低风险页面改动按固定种子抽样，审核人逐个记抽检结果
// （审核记录依据 spot_check）；抽检驳回的页面在对账里扣下，合并窗口跳过它。抽检比例与窗口时间是配置位，待教研负责人定。
import { redirect } from 'next/navigation';
import { currentSession } from '../../../lib/review-session.ts';
import { DECISION_LABEL, RESULT_LABEL, getSpotChecks, isDay, safeHttpsUrl, signerTail } from '../../../lib/review.ts';
import { ActionForm } from '../ActionForm.tsx';
import { spotCheckAction } from '../actions.ts';

export const dynamic = 'force-dynamic';

export default async function SpotChecksPage({ searchParams }: { searchParams: Promise<{ day?: string }> }) {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const raw = (await searchParams).day ?? '';
  const r = await getSpotChecks(staff, isDay(raw) ? raw : undefined);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>事后抽检</h1>
      {!r.ok ? (
        <p className="review-result error" data-testid="spot-error">读取失败（{RESULT_LABEL[r.problem.code] ?? r.problem.code}）</p>
      ) : (
        <>
          <p className="muted" data-testid="spot-config">
            {r.value.day}（北京时间）· 抽检比例 {Math.round(r.value.ratio * 1000) / 10}% · 每日合并窗口 {r.value.window}（比例与窗口时间待教研负责人定）
          </p>
          {(r.value.warnings ?? []).includes('ratio_zero') ? (
            <p className="review-result error" data-testid="spot-ratio-zero">抽检比例配置为 0：一页都不会抽，请教研负责人确认配置</p>
          ) : null}
          <form method="get" className="inline-form">
            <label htmlFor="spot-day">日期</label>{' '}
            <input id="spot-day" name="day" type="date" defaultValue={r.value.day} />{' '}
            <button type="submit">查看</button>
          </form>
          {!r.value.configured ? (
            <p data-testid="spot-unconfigured">审核台后端没有配置页面仓库的合并记录导出，暂无可抽检的页面。</p>
          ) : (r.value.sampled ?? []).length === 0 ? (
            <p data-testid="spot-none">当天低风险合并 {r.value.candidates ?? 0} 页次，没有抽中的</p>
          ) : (
            <ul className="review-list" data-testid="spot-sampled">
              <li className="muted">当天低风险合并 {r.value.candidates} 页次，按固定种子抽中 {(r.value.sampled ?? []).length} 页次：</li>
              {(r.value.sampled ?? []).map((x) => {
                const href = safeHttpsUrl(x.url);
                return (
                  <li key={`${x.page_id}:${x.head_commit}`} data-page={x.page_id} data-checked={String(x.checks.length > 0)}>
                    <p><code>{x.page_id}</code> · 头提交 <code>{x.head_commit.slice(0, 12)}</code> ·{' '}
                      {href ? <a href={href} rel="noopener noreferrer" target="_blank">合并请求 !{x.mr_id}（托管平台）</a> : <>合并请求 !{x.mr_id}</>}</p>
                    {x.checks.length > 0 ? (
                      <p>已有抽检：{x.checks.map((c) => `${DECISION_LABEL[c.decision] ?? c.decision}（${signerTail(c.signer)}）`).join('；')}</p>
                    ) : null}
                    <ActionForm action={spotCheckAction} labels={RESULT_LABEL} testId={`spot-${x.page_id}`}>
                      <input type="hidden" name="page_id" value={x.page_id} />
                      <input type="hidden" name="page_commit" value={x.head_commit} />
                      <button type="submit" name="decision" value="approved">抽检通过</button>{' '}
                      <button type="submit" name="decision" value="rejected">抽检不通过（扣下该页）</button>
                    </ActionForm>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      <p><a href="/review/pages">编译页面对账</a></p>
    </main>
  );
}
