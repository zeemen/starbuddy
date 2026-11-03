// 修订发布预检（KGE-06，specs/kge06-weekly-revision 范围 2）：草稿与已发布版本相比是否结构性、应升的版本号段、要几人签、
// 版本号是否合发布规则、影响面与签字进度。只给建议：签字与发布仍在地图版本审核页走现有流程（发布函数为准）。
// 开关 STARBUDDY_REVIEW_FEATURES=revision_preflight（只在 dev / test）：关着时 404，在读会话之前判断，不发任何请求。
import { notFound, redirect } from 'next/navigation';
import { reviewFeatureEnabled } from '../../../../../lib/review-features.ts';
import { currentSession } from '../../../../../lib/review-session.ts';
import { BUMP_LABEL, OBJECT_LABEL, OP_LABEL, REASON_LABEL, STATUS_LABEL, getPreflight, isVersionId } from '../../../../../lib/review.ts';

export const dynamic = 'force-dynamic';

export default async function PreflightPage({ params }: { params: Promise<{ id: string }> }) {
  if (!reviewFeatureEnabled('revision_preflight')) notFound();
  const { id } = await params;
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  if (!isVersionId(id)) notFound();
  const r = await getPreflight(staff, id);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  if (!r.ok) {
    if (r.problem.status === 404 || r.problem.status === 422) notFound();
    return <main><h1>修订发布预检</h1><p className="review-result error">读取失败（{r.problem.code}）</p></main>;
  }
  const p = r.value;
  return (
    <main>
      <h1>修订发布预检 · {p.version.version}（{STATUS_LABEL[p.version.status] ?? p.version.status}）</h1>
      <p className="muted" data-testid="preflight-note">{p.note}</p>
      <p><a href={`/review/map-versions/${p.version.id}`}>回到审核页（签字与发布）</a></p>
      <dl className="review-summary" data-testid="preflight">
        <dt>与已发布版本相比</dt>
        <dd data-testid="preflight-structural" data-structural={String(p.structural)}>
          {p.structural ? '结构性变更' : '非结构性变更'}（基准：{p.base_version ?? '没有已发布版本'}）
        </dd>
        <dt>建议升的版本号段</dt>
        <dd data-testid="preflight-bump" data-bump={p.bump}>
          {BUMP_LABEL[p.bump] ?? p.bump}{p.suggested_version ? `；建议版本号 ${p.suggested_version}（同学科已有最大 ${p.latest_version}）` : ''}
        </dd>
        <dt>签字</dt>
        <dd data-testid="preflight-signers" data-required={p.required_signers}>
          需要 {p.required_signers} 名不同教研对当前内容批准；现有 {p.signoff.approvals} 名
          {p.signoff.enough ? '（人数与最近结论已满足）' : '（还不满足）'}
        </dd>
        <dt>版本号 {p.draft_version}</dt>
        <dd data-testid="preflight-version" data-ok={String(p.version_ok)}>
          {p.version_ok ? '合发布规则' : '发布时会被拒绝'}
          {p.problems.length > 0 ? <ul>{p.problems.map((x) => <li key={x.code} data-code={x.code}>{x.detail}</li>)}</ul> : null}
          {p.advice.length > 0 ? <ul className="muted">{p.advice.map((x) => <li key={x.code} data-code={x.code}>提示：{x.detail}</li>)}</ul> : null}
        </dd>
        <dt>影响面</dt>
        <dd>挂在变化节点上的题目标签 {p.impact.item_tags} 条、采分点 {p.impact.scoring_points} 个；校验阻断 {p.validation_errors} 个</dd>
      </dl>
      <h2>结构性的依据</h2>
      {p.structural_reasons.length === 0 ? (
        p.structural ? (
          <p data-testid="preflight-reasons-db-only">
            逐条差异里没有标出结构性条目，但库的判定（kge.structural_diff，与发布函数同一个）是结构性：以库判定为准
            （例如没有已发布版本时整张图都算新增）。
          </p>
        ) : (
          <p data-testid="preflight-reasons-empty">没有（只有非结构性的改动）</p>
        )
      ) : (
        <ul data-testid="preflight-reasons">
          {p.structural_reasons.map((x) => (
            <li key={`${x.source}-${x.key}-${x.op}`}>
              {x.source === 'change_request' ? '变更单' : (OBJECT_LABEL[x.object] ?? x.object)} <code>{x.key}</code> ·{' '}
              {OP_LABEL[x.op] ?? x.op} · {REASON_LABEL[x.reason] ?? x.reason}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
