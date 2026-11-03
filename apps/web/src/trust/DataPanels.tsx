'use client';
// 家长数据权利的两个交互面板（冲刺 5-H 设计要点 5）：
//   ExportPanel：生成导出文件 → 得到一次性下载凭据 → 点“下载”时由本页 fetch POST 到本前端的路由处理器（再转 BFF 兑换，no-store、附件），
//     拿到内容后在浏览器里存成文件。不用普通表单提交：全站 Referrer-Policy: no-referrer 下表单导航的 Origin 是 "null"，BFF 会拒绝；
//     fetch 总带真实 Origin（另显式 referrerPolicy 'same-origin'）。
//   ErasurePanel：申请删除 → 二次确认（勾选“已了解”后确认；确认凭据绑定本会话与这条申请）→ 进入冷静期；
//     BFF 要求本会话 10 分钟内经验证码登录过，否则提示重新登录（链接到登录页），重新登录后再申请并确认。
// 一次性下载凭据与删除确认凭据只在客户端内存里（安全审查 1）：按钮 onClick 在 startTransition 里直接调 server action，
// 不用 <form action>——没有 JS 时表单会整页提交、结果（含凭据）被服务端渲染进 HTML；这里水合之前按钮一律 disabled，没有 JS 就什么也不发生。
// 凭据不进地址栏、不进 DOM 属性、不写浏览器存储；兑换一次即作废，再点提示重新生成。文字只来自 src/shell/copy.ts（占位）。
import { useEffect, useState, useTransition } from 'react';
import { clearPwaCaches } from '../pwa/client.ts';
import { DATA_RESULT, DATA_RIGHTS } from '../shell/copy.ts';
import { erasureConfirmAction, erasureRequestAction, exportAction, type ErasureResult, type ExportResult } from './actions.ts';

const labelOf = (code: string | undefined): string => (code ? (Object.hasOwn(DATA_RESULT, code) ? DATA_RESULT[code]! : DATA_RESULT['INTERNAL']!) : '');

function useHydrated(): boolean {
  const [h, setH] = useState(false);
  useEffect(() => setH(true), []);
  return h;
}

const formOf = (fields: Record<string, string>): FormData => {
  const f = new FormData();
  for (const [k, v] of Object.entries(fields)) f.set(k, v);
  return f;
};

export function ExportPanel({ studentId, csrf }: { studentId: string; csrf: string }) {
  const hydrated = useHydrated();
  const [pending, startTransition] = useTransition();
  const [state, setState] = useState<ExportResult | null>(null);
  const [gone, setGone] = useState<string | null>(null);
  const job = state?.code === 'ready' ? state.job : undefined;
  const ref = state?.code === 'ready' ? state.ref : undefined;
  const start = () => startTransition(async () => {
    setState(await exportAction(null, formOf({ student: studentId, csrf })));
  });
  async function download() {
    if (!job || !ref) return;
    let r: Response | null = null;
    try {
      r = await fetch(`/family/${studentId}/data/download`, {
        method: 'POST', body: formOf({ csrf, job, download_ref: ref }), cache: 'no-store', credentials: 'same-origin', redirect: 'error',
        referrerPolicy: 'same-origin',
      });
    } catch { /* 网络错误按失效处理 */ }
    if (!r || !r.ok) {
      if (r?.status === 401) window.location.assign('/login');
      setGone(ref);
      return;
    }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = `starbuddy-export-${job}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  return (
    <div data-testid="export-panel">
      <div className="shell-form" data-testid="export-form" aria-busy={pending} data-hydrated={hydrated ? 'true' : undefined}>
        <button type="button" onClick={start} disabled={!hydrated || pending}>{DATA_RIGHTS.exportStart}</button>
        <p role="status" className="shell-result" data-code={state?.code ?? ''}>{labelOf(state?.code)}</p>
      </div>
      {job && ref ? (
        <p className="shell-form">
          <button type="button" onClick={download} data-testid="download">{DATA_RIGHTS.download}</button>
        </p>
      ) : null}
      {gone !== null && gone === ref ? <p role="status" data-testid="export-gone">{DATA_RIGHTS.exportGone}</p> : null}
    </div>
  );
}

export function ErasurePanel({ studentId, csrf }: { studentId: string; csrf: string }) {
  const hydrated = useHydrated();
  const [requesting, startRequest] = useTransition();
  const [confirming, startConfirm] = useTransition();
  const [req, setReq] = useState<ErasureResult | null>(null);
  const [conf, setConf] = useState<ErasureResult | null>(null);
  const [understood, setUnderstood] = useState(false);
  const request = () => startRequest(async () => {
    setConf(null);
    setUnderstood(false);
    setReq(await erasureRequestAction(null, formOf({ student: studentId, csrf })));
  });
  const confirm = () => startConfirm(async () => {
    if (!req?.request || !req.token || !understood) return;
    const r = await erasureConfirmAction(null, formOf({ student: studentId, csrf, request: req.request, confirm_token: req.token, understood: 'yes' }));
    // 删除已确认（LT-07）：顺带清掉本站 Service Worker 缓存（只有静态壳与构建产物，不含个人数据；与登出同一口径）
    if (r.code === 'confirmed') void clearPwaCaches();
    setConf(r);
  });
  const pendingConfirm = req?.code === 'requested' && req.request && req.token && conf?.code !== 'confirmed';
  return (
    <div data-testid="erasure-panel">
      <div className="shell-form" data-testid="erasure-request-form" aria-busy={requesting} data-hydrated={hydrated ? 'true' : undefined}>
        <button type="button" onClick={request} disabled={!hydrated || requesting}>{DATA_RIGHTS.erasureStart}</button>
        <p role="status" className="shell-result" data-code={req?.code ?? ''}>{labelOf(req?.code)}</p>
      </div>
      {pendingConfirm ? (
        <div className="shell-form" data-testid="erasure-confirm-form" aria-busy={confirming}>
          <p className="checkbox-row">
            <input id="erasure-understood" type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
            <label htmlFor="erasure-understood">{DATA_RIGHTS.confirmCheck}</label>
          </p>
          <button type="button" onClick={confirm} disabled={!understood || confirming}>{DATA_RIGHTS.confirm}</button>
          <p role="status" className="shell-result" data-code={conf?.code ?? ''}>{labelOf(conf?.code)}</p>
          {conf?.code === 'REAUTH_REQUIRED' ? <p><a href="/login" data-testid="relogin">{DATA_RIGHTS.relogin}</a></p> : null}
        </div>
      ) : null}
      {conf?.code === 'confirmed' ? <p role="status" className="shell-result" data-code="confirmed">{labelOf('confirmed')}</p> : null}
    </div>
  );
}
