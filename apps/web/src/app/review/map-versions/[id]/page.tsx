// 地图版本审核页（spec 设计要点 4“审核界面的核心是差异对比”）：风险摘要、差异（附风险与校验问题）、变更单、
// 节点编辑（草稿）、签字、发布、撤下、签字记录。所有表单都带当前内容摘要：页面显示的就是签的、发布的。
import { cookies } from 'next/headers';
import { notFound, redirect } from 'next/navigation';
import { DEV_STAFF_COOKIE, devAuthEnabled, devStaffFromCookie } from '../../../../lib/dev-auth.ts';
import {
  ACTION_LABEL, CHANGE_STATUS_LABEL, DECISION_LABEL, LEVEL_LABEL, OBJECT_LABEL, OP_LABEL, RESULT_LABEL, RETIRE_BASIS, STATUS_LABEL,
  getDiff, isVersionId, type Change, type NodeRow,
} from '../../../../lib/review.ts';
import { ActionForm } from '../../ActionForm.tsx';
import { editNodeAction, publishAction, retireAction, signChangeAction, signVersionAction } from '../../actions.ts';

export const dynamic = 'force-dynamic';

/** 差异里的值原样显示：数组用顿号连，对象显示为紧凑 JSON，空值显示为破折号 */
function show(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (Array.isArray(v)) return v.length === 0 ? '（空）' : v.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join('、');
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function RiskBadge({ level, structural }: { level: string; structural: boolean }) {
  // 颜色之外一定有文字（不只靠颜色区分，WCAG 1.4.1）
  return (
    <span className={`risk risk-${level}`}>
      {LEVEL_LABEL[level] ?? level}风险{structural ? ' · 结构性' : ''}
    </span>
  );
}

function ChangeRow({ c }: { c: Change }) {
  const values = c.op === 'changed' || c.op === 'deprecated' || c.op === 'reactivated'
    ? <><td>{show(c.old)}</td><td>{show(c.new)}</td></>
    : c.op === 'added' ? <><td>—</td><td>{show(c.new)}</td></> : <><td>{show(c.old)}</td><td>—</td></>;
  return (
    <tr data-object={c.object} data-key={c.key} data-field={c.field ?? ''} data-level={c.risk.level}>
      <td>{OBJECT_LABEL[c.object] ?? c.object}</td>
      <td><code>{c.key}</code>{c.rubric_version ? <span className="muted">（评分版本 {c.rubric_version}）</span> : null}</td>
      <td>{OP_LABEL[c.op] ?? c.op}{c.field ? ` · ${c.field}` : ''}</td>
      {values}
      <td><RiskBadge level={c.risk.level} structural={c.risk.structural} />
        {c.risk.level !== c.risk.review_level ? <span className="muted">（阶段 0 按{LEVEL_LABEL[c.risk.review_level]}审）</span> : null}</td>
      <td>{c.issues.length === 0 ? '—' : c.issues.map((i) => `${i.severity === 'error' ? '阻断' : '提示'}：${i.detail}`).join('；')}</td>
    </tr>
  );
}

function NodeEditor({ versionId, digest, node }: { versionId: string; digest: string; node: NodeRow }) {
  const fid = (k: string) => `n-${node.id}-${k}`.replace(/\./g, '_');
  return (
    <details className="node-editor" data-node={node.id}>
      <summary>{node.id} · {node.name}{node.status !== 'active' ? '（已停用）' : ''}</summary>
      <ActionForm action={editNodeAction} labels={RESULT_LABEL} testId={`edit-${node.id}`}>
        <input type="hidden" name="version" value={versionId} />
        <input type="hidden" name="node" value={node.id} />
        <input type="hidden" name="expected_digest" value={digest} />
        {(['includes', 'excludes', 'examples'] as const).map((k) => (
          <p key={k}>
            <label htmlFor={fid(k)}>{{ includes: '包含', excludes: '不包含', examples: '典型题例' }[k]}（一行一条）</label>
            <textarea id={fid(k)} name={k} rows={3} defaultValue={node[k].join('\n')} />
          </p>
        ))}
        <button type="submit">保存修改</button>
      </ActionForm>
    </details>
  );
}

export default async function MapVersionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const staff = devStaffFromCookie((await cookies()).get(DEV_STAFF_COOKIE)?.value);
  if (!staff) {
    if (devAuthEnabled()) redirect('/dev/login');
    return <main><h1>审核</h1><p>请先以教研账号登录。</p></main>;
  }
  if (!isVersionId(id)) notFound();
  const r = await getDiff(staff, id);
  if (!r.ok) {
    if (r.problem.status === 404 || r.problem.status === 422) notFound();
    return <main><h1>审核</h1><p className="review-result error">读取失败（{r.problem.code}）</p></main>;
  }
  const d = r.value;
  const v = d.version;
  const isDraft = v.status === 'draft';
  const errors = d.validation.filter((i) => i.severity === 'error');
  const approvals = new Set(d.signoffs.filter((s) => s.action === 'review' && s.decision === 'approved' && s.digest === d.digest).map((s) => s.signer));
  return (
    <main>
      <h1>地图版本 {v.version}（{STATUS_LABEL[v.status] ?? v.status}）</h1>
      <p className="muted">
        学科 {v.subject} · 对比基准：{d.base_version_id ? '当前已发布版本' : '没有已发布版本，全部按新增列出'} · 内容摘要 <code data-testid="digest">{d.digest.slice(0, 12)}</code>
      </p>

      <section aria-labelledby="h-summary" data-testid="summary" data-structural={String(d.summary.structural)} data-signers={d.summary.required_signers}>
        <h2 id="h-summary">风险摘要</h2>
        <ul>
          <li>最高风险：<RiskBadge level={d.summary.level} structural={d.summary.structural} /></li>
          <li>需要签字：{d.summary.required_signers} 名不同教研（对当前内容批准的已有 {approvals.size} 名）</li>
          <li>差异 {d.changes.length} 条：高 {d.summary.counts['high'] ?? 0}，中 {d.summary.counts['medium'] ?? 0}，低 {d.summary.counts['low'] ?? 0}</li>
          <li>影响（公共层）：题目标签 {d.impact.item_tags} 个，采分点 {d.impact.scoring_points} 个</li>
          <li>发布前校验：阻断 {errors.length} 条，提示 {d.validation.length - errors.length} 条</li>
        </ul>
      </section>

      <section aria-labelledby="h-diff">
        <h2 id="h-diff">差异</h2>
        {d.changes.length === 0 ? (
          <p data-testid="no-changes">与基准没有差异</p>
        ) : (
          <div className="table-scroll">
            <table className="review-table" data-testid="changes">
              <caption>与基准版本相比的变化（旧值 → 新值）</caption>
              <thead>
                <tr><th scope="col">对象</th><th scope="col">键</th><th scope="col">变化</th><th scope="col">旧值</th><th scope="col">新值</th>
                  <th scope="col">风险</th><th scope="col">校验</th></tr>
              </thead>
              <tbody>{d.changes.map((c) => <ChangeRow key={`${c.object}|${c.key}|${c.field ?? ''}|${c.op}`} c={c} />)}</tbody>
            </table>
          </div>
        )}
        {d.unattached_issues.length > 0 ? (
          <>
            <h3>其他校验问题</h3>
            <ul data-testid="other-issues">
              {d.unattached_issues.map((i, k) => <li key={k}>{i.severity === 'error' ? '阻断' : '提示'}：<code>{i.object_id}</code> {i.detail}</li>)}
            </ul>
          </>
        ) : null}
      </section>

      {d.change_requests.length > 0 ? (
        <section aria-labelledby="h-cr">
          <h2 id="h-cr">变更单</h2>
          <ul className="review-list">
            {d.change_requests.map((c) => (
              <li key={c.id} data-change={c.id} data-status={c.status}>
                <p>{c.change_type} · {CHANGE_STATUS_LABEL[c.status] ?? c.status} · <RiskBadge level={c.risk.level} structural={c.risk.structural} />
                  {c.risk.structural ? '（需两名不同教研）' : ''}</p>
                <pre className="payload">{JSON.stringify(c.payload, null, 2)}</pre>
                {c.status === 'proposed' ? (
                  <ActionForm action={signChangeAction} labels={RESULT_LABEL} testId={`sign-change-${c.id}`}>
                    <input type="hidden" name="version" value={v.id} />
                    <input type="hidden" name="change" value={c.id} />
                    <input type="hidden" name="expected_digest" value={c.digest} />
                    <button type="submit" name="decision" value="approved">批准变更单</button>{' '}
                    <button type="submit" name="decision" value="rejected">驳回变更单</button>
                  </ActionForm>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {isDraft ? (
        <section aria-labelledby="h-edit">
          <h2 id="h-edit">修改节点边界</h2>
          <p className="muted">v0 只能改包含、不包含、典型题例；新增、停用节点与改边走变更单。保存后摘要会变，已有签字需要重签。</p>
          {d.nodes.filter((n) => n.level === 'node' || n.level === 'detail').map((n) => (
            <NodeEditor key={n.id} versionId={v.id} digest={d.digest} node={n} />
          ))}
        </section>
      ) : null}

      <section aria-labelledby="h-sign">
        <h2 id="h-sign">签字与发布</h2>
        {isDraft ? (
          <>
            <ActionForm action={signVersionAction} labels={RESULT_LABEL} testId="sign">
              <input type="hidden" name="version" value={v.id} />
              <input type="hidden" name="expected_digest" value={d.digest} />
              <p>
                <label htmlFor="basis">依据编码（可不填）</label>{' '}
                <input id="basis" name="basis_code" pattern="[a-z0-9_]{1,48}" />
              </p>
              <p>我已核对上面列出的全部差异与校验结果（摘要 <code>{d.digest.slice(0, 12)}</code>）。</p>
              <button type="submit" name="decision" value="approved">批准</button>{' '}
              <button type="submit" name="decision" value="rejected">驳回</button>
            </ActionForm>
            <ActionForm action={publishAction} labels={RESULT_LABEL} testId="publish">
              <input type="hidden" name="version" value={v.id} />
              <input type="hidden" name="expected_digest" value={d.digest} />
              <button type="submit">发布这个版本</button>
            </ActionForm>
          </>
        ) : null}
        {!isDraft ? (
          <p data-testid="status-note">
            {v.status === 'published' ? '本版本已发布' : '本版本已撤下'}
            {v.published_at ? `（发布于 ${new Date(v.published_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}）` : ''}，不能再签字或修改。
          </p>
        ) : null}
        {v.status !== 'retired' ? (
          <ActionForm action={retireAction} labels={RESULT_LABEL} testId="retire">
            <input type="hidden" name="version" value={v.id} />
            <p>
              <label htmlFor="retire-basis">撤下依据</label>{' '}
              <select id="retire-basis" name="basis_code" defaultValue="">
                <option value="" disabled>请选择</option>
                {RETIRE_BASIS.map((b) => <option key={b.code} value={b.code}>{b.label}</option>)}
              </select>{' '}
              <button type="submit">撤下这个版本</button>
            </p>
          </ActionForm>
        ) : null}
      </section>

      <section aria-labelledby="h-signoffs">
        <h2 id="h-signoffs">签字记录</h2>
        {d.signoffs.length === 0 ? <p>还没有签字</p> : (
          <table className="review-table" data-testid="signoffs">
            <caption>这个版本的审核、发布、撤下记录</caption>
            <thead><tr><th scope="col">动作</th><th scope="col">结论</th><th scope="col">签字人</th><th scope="col">摘要</th><th scope="col">是否对应当前内容</th></tr></thead>
            <tbody>
              {d.signoffs.map((s, k) => (
                <tr key={k} data-action={s.action} data-current={String(s.digest === d.digest)}>
                  <td>{ACTION_LABEL[s.action] ?? s.action}</td>
                  <td>{DECISION_LABEL[s.decision] ?? s.decision}</td>
                  <td><code>{s.signer.slice(0, 8)}</code></td>
                  <td><code>{s.digest ? s.digest.slice(0, 12) : '—'}</code></td>
                  <td>{s.digest === d.digest ? '是' : '否（签字后内容改过）'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <p><a href={`/review?subject=${v.subject}`}>返回地图版本</a> · <a href={`/review/records?subject=${v.subject}`}>版本记录</a></p>
    </main>
  );
}
