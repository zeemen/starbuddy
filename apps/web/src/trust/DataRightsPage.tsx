// 家长数据权利页（冲刺 5-H 设计要点 5）：导出（发起、下载一次性凭据）、删除申请（二次确认、需要重新登录时引导、冷静期倒计时、撤回）、
// 删除回执（执行后只有“表名 → 行数”与范围）。只给家长、只对已绑定有效的孩子（规则 5：requireGuardian 只认会话里的已绑定孩子，
// BFF 与库函数再核一次）。学生会话打开本页转到自己的“今日”（guard.ts）。
// 倒计时是页面渲染时的剩余时间（天、小时），不在浏览器里跑计时器。
import { redirect } from 'next/navigation';
import { ResultForm } from '../auth/ResultForm.tsx';
import { fetchErasures } from '../lib/trust-api.ts';
import { DATA_RESULT, DATA_RIGHTS, ERASURE_CANCEL_REASON, ERASURE_SCOPE, ERASURE_STATUS, TRUST } from '../shell/copy.ts';
import { requireGuardian, requireTrust } from '../shell/guard.ts';
import { erasureCancelAction } from './actions.ts';
import { ErasurePanel, ExportPanel } from './DataPanels.tsx';
import { coolingLeft, pickErasures, shanghaiDay, type ErasureRow } from './view.ts';

export async function DataRightsPage({ studentId }: { studentId: string }) {
  requireTrust('data_rights');
  const ps = await requireGuardian(studentId);
  const r = await fetchErasures(ps.session, studentId);
  if (!r.ok && r.status === 401) redirect('/login');
  const rows = r.ok ? pickErasures(r.value) : null;
  const now = Date.now();
  const csrf = ps.session.csrf;
  return (
    <main data-testid="data-page" data-trust-root="data">
      <p><a href={`/family/${studentId}`}>{TRUST.back}</a></p>
      <h1>{DATA_RIGHTS.heading}</h1>

      <section aria-labelledby="export-heading">
        <h2 id="export-heading">{DATA_RIGHTS.exportHeading}</h2>
        <p className="muted">{DATA_RIGHTS.exportNote}</p>
        <ExportPanel studentId={studentId} csrf={csrf} />
      </section>

      <section aria-labelledby="erasure-heading">
        <h2 id="erasure-heading">{DATA_RIGHTS.erasureHeading}</h2>
        <p className="muted">{DATA_RIGHTS.erasureNote}</p>
        <ErasurePanel studentId={studentId} csrf={csrf} />
        <h3>{DATA_RIGHTS.requestsHeading}</h3>
        {!rows ? <p className="muted" data-unavailable="erasure">{TRUST.unavailable}</p>
          : rows.length === 0 ? <p className="muted" data-testid="erasure-none">{DATA_RIGHTS.none}</p>
            : <ul className="shell-list" data-testid="erasure-requests">{rows.map((x) => <ErasureItem key={x.request_id} row={x} studentId={studentId} csrf={csrf} now={now} />)}</ul>}
      </section>
    </main>
  );
}

function ErasureItem({ row, studentId, csrf, now }: { row: ErasureRow; studentId: string; csrf: string; now: number }) {
  const left = row.cooling_off_until ? coolingLeft(row.cooling_off_until, now) : null;
  return (
    <li className="erasure-item" data-state={row.state} data-request={row.request_id}>
      <p>
        <span>{ERASURE_STATUS[row.state]}</span>{' '}
        <span className="muted">{DATA_RIGHTS.colRequested}</span>{' '}<span data-value="date">{shanghaiDay(row.requested_at)}</span>
        {row.state === 'cancelled' && row.cancel_reason && Object.hasOwn(ERASURE_CANCEL_REASON, row.cancel_reason)
          ? <>{' '}<span className="muted">{ERASURE_CANCEL_REASON[row.cancel_reason]}</span></> : null}
      </p>
      {left ? (
        <p data-testid="cooling-left">
          <span className="muted">{DATA_RIGHTS.colCooling}</span>{' '}
          <span data-value="days">{left.days}</span>{' '}<span>{DATA_RIGHTS.days}</span>{' '}
          <span data-value="hours">{left.hours}</span>{' '}<span>{DATA_RIGHTS.hours}</span>
        </p>
      ) : null}
      {row.state === 'awaiting_confirm' || row.state === 'cooling_off' ? (
        <ResultForm action={erasureCancelAction} labels={DATA_RESULT} testId={`erasure-cancel-${row.request_id}`} className="shell-form inline-form">
          <input type="hidden" name="student" value={studentId} />
          <input type="hidden" name="request" value={row.request_id} />
          <input type="hidden" name="csrf" value={csrf} />
          <button type="submit">{DATA_RIGHTS.cancel}</button>
        </ResultForm>
      ) : null}
      {row.receipt ? (
        <div data-testid="erasure-receipt">
          <h4>{DATA_RIGHTS.receiptHeading}</h4>
          <table className="trust-table">
            <thead><tr><th scope="col">{DATA_RIGHTS.receiptTable}</th><th scope="col">{DATA_RIGHTS.receiptRows}</th></tr></thead>
            <tbody>
              {row.receipt.tables.map(([t, n]) => <tr key={t}><td><span data-value="table">{t}</span></td><td><span data-value="rows">{n}</span></td></tr>)}
            </tbody>
          </table>
          {row.receipt.scopes.length > 0 ? (
            <p><span className="muted">{DATA_RIGHTS.receiptScopes}</span>{' '}{row.receipt.scopes.map((s) => <span key={s} className="scope">{ERASURE_SCOPE[s]} </span>)}</p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
