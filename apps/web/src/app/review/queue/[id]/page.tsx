// 复核任务详情（specs/review-queue-sprint2 设计要点 2）：任务字段、来源（判分的逐维分表，或判断的模板与状态），按状态给出领取、放回、结案。
// 可见范围：只有不透明学生指针（student_ref）与结构化判分、证据 ID 数；不显示作答原文（后端也不给）与任何可识别信息。
// 结案只写码，不改分、不改掌握、不改判断（设计要点 3）；是否本人领取、结案码是否适用都由库函数核对，前端只按状态显示操作。
import { notFound, redirect } from 'next/navigation';
import { currentSession } from '../../../../lib/review-session.ts';
import {
  JUDGED_BY_LABEL, QUEUE_RESULT_LABEL, QUEUE_STATUS_LABEL, SOURCE_KIND_LABEL, SUBJECTS, VERDICT_LABEL, assignmentLabel, formatTime,
  isTaskId, queueTask, reasonLabel, resolutionCodes, type ClaimSource, type DiagnosisStepSource, type GradeSource, type QueueTask,
  type ResolutionCode,
} from '../../../../lib/review.ts';
import { ActionForm } from '../../ActionForm.tsx';
import { releaseTaskAction, resolveTaskAction, takeTaskAction } from '../../actions.ts';

export const dynamic = 'force-dynamic';

const num = (v: number | null | undefined): string => (v === null || v === undefined ? '—' : String(v));

function GradeView({ g }: { g: GradeSource }) {
  return (
    <section aria-labelledby="h-grade" data-testid="grade-source">
      <h2 id="h-grade">判分</h2>
      <ul>
        <li>结论：{VERDICT_LABEL[g.verdict] ?? g.verdict} · 总分 {num(g.score_total)} / {g.max_total} · 置信度 {g.confidence}
          {g.calibrated ? '（已校准）' : '（未校准）'}{g.superseded ? ' · 已被新判分取代' : ''}</li>
        <li>判分时的复核原因：{g.review_reasons.length ? g.review_reasons.map(reasonLabel).join('、') : '—'}</li>
        <li>评分版本 <code>{g.rubric_version}</code> · 来源版本 <code>{g.source_version}</code> · 模型版本 <code>{g.model_version}</code></li>
        <li>证据 ID {g.evidence_ids.length} 个 · 判分时间 {formatTime(g.created_at)}</li>
      </ul>
      {g.dimensions.length === 0 ? <p>没有逐维分</p> : (
        <div className="table-scroll">
          <table className="review-table" data-testid="dimensions">
            <caption>逐维分</caption>
            <thead>
              <tr><th scope="col">维度</th><th scope="col">分数 / 满分</th><th scope="col">判定方式</th><th scope="col">主观</th>
                <th scope="col">置信度</th><th scope="col">证据 ID 数</th></tr>
            </thead>
            <tbody>
              {g.dimensions.map((d) => (
                <tr key={d.dimension_id} data-dimension={d.dimension_id}>
                  <td><code>{d.dimension_id}</code>{d.rule_ref ? <span className="muted">（{d.rule_ref}）</span> : null}</td>
                  <td>{num(d.score)} / {d.max_score}</td>
                  <td>{JUDGED_BY_LABEL[d.judged_by] ?? d.judged_by}</td>
                  <td>{d.subjective ? '是' : '否'}</td>
                  <td>{d.confidence}</td>
                  <td>{d.evidence_ids.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ClaimView({ c }: { c: ClaimSource }) {
  return (
    <section aria-labelledby="h-claim" data-testid="claim-source">
      <h2 id="h-claim">判断（画像异议）</h2>
      <ul>
        <li>模板 <code>{c.template_key}@{c.template_version}</code> · 对象类型 {c.target_type}</li>
        <li>状态 {c.status} · 置信度 {c.confidence} · 来源 {c.source_type}{c.model_version ? <> · 模型版本 <code>{c.model_version}</code></> : null}</li>
        <li>证据 ID {c.evidence_ids.length} 个 · 建立时间 {formatTime(c.created_at)}</li>
      </ul>
    </section>
  );
}

function DiagnosisStepView({ d }: { d: DiagnosisStepSource }) {
  return (
    <section aria-labelledby="h-diagnosis" data-testid="diagnosis-step-source">
      <h2 id="h-diagnosis">诊断步骤（过程观察）</h2>
      <ul>
        <li>任务 {d.kind === 'process' ? '写出全过程' : '讲给老师听'} · 知识节点 <code>{d.node_id}</code> · 题目 <code>{d.item_id ?? '—'}</code></li>
        <li>最终答案的判分：{d.verdict ? VERDICT_LABEL[d.verdict] ?? d.verdict : '—'} · 证据 ID {d.evidence_ids?.length ?? 0} 个
          · 作答时间 {formatTime(d.answered_at)}</li>
      </ul>
      <p className="muted">过程部分不自动评分；结案只记“已看过程并记录”或“无需处理”，不改分、不改掌握。</p>
    </section>
  );
}

function Actions({ t, codes }: { t: QueueTask; codes: ResolutionCode[] | null }) {
  if (t.status === 'resolved') {
    const label = codes?.find((c) => c.code === t.resolution_code)?.label;
    return (
      <p data-testid="resolved-note">
        已结案：{label ?? t.resolution_code}（<code>{t.resolution_code}</code>）· {formatTime(t.resolved_at)} ·
        {t.resolved_by_me ? ' 由我结案' : ' 由他人结案'}
      </p>
    );
  }
  if (!t.assigned) {
    return (
      <ActionForm action={takeTaskAction} labels={QUEUE_RESULT_LABEL} testId="take">
        <input type="hidden" name="task" value={t.id} />
        <button type="submit">领取</button>
      </ActionForm>
    );
  }
  if (!t.assigned_to_me) {
    return <p data-testid="assigned-other">这条任务已被他人领取（{formatTime(t.assigned_at)}），只有领取人能放回或结案。</p>;
  }
  const applicable = (codes ?? []).filter((c) => c.applies_to.includes(t.source_kind));
  return (
    <>
      <ActionForm action={resolveTaskAction} labels={QUEUE_RESULT_LABEL} testId="resolve">
        <input type="hidden" name="task" value={t.id} />
        <p>
          <label htmlFor="resolution-code">结案码</label>{' '}
          <select id="resolution-code" name="resolution_code" defaultValue="" required>
            <option value="" disabled>请选择</option>
            {applicable.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
          </select>
        </p>
        <p className="muted">结案只记结案码，不改分、不改掌握、不改判断；需要改判时由判分侧另写一条判分。</p>
        <button type="submit">结案</button>
      </ActionForm>
      {codes === null ? <p className="review-result error">结案码读取失败，请刷新</p> : null}
      <ActionForm action={releaseTaskAction} labels={QUEUE_RESULT_LABEL} testId="release">
        <input type="hidden" name="task" value={t.id} />
        <button type="submit">放回</button>
      </ActionForm>
    </>
  );
}

export default async function QueueTaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  if (!isTaskId(id)) notFound();
  const [r, rc] = await Promise.all([queueTask(staff, id), resolutionCodes(staff)]);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  if (!r.ok) {
    if (r.problem.status === 404 || r.problem.status === 422) notFound();
    return <main><h1>复核任务</h1><p className="review-result error">读取失败（{r.problem.code}）</p></main>;
  }
  const t = r.value;
  const codes = rc.ok ? rc.value.items : null;
  const subjectName = SUBJECTS.find((s) => s.code === t.subject)?.name ?? t.subject;
  const src = t.source;
  return (
    <main>
      <h1>复核任务（{QUEUE_STATUS_LABEL[t.status] ?? t.status}）</h1>
      <section aria-labelledby="h-task" data-testid="task" data-status={t.status} data-assigned-to-me={String(t.assigned_to_me)}>
        <h2 id="h-task">任务</h2>
        <ul>
          <li>学科 {subjectName} · 学生指针 <code data-testid="student-ref">{t.student_ref}</code></li>
          <li>类型 {SOURCE_KIND_LABEL[t.source_kind] ?? t.source_kind} · 对象 <code>{t.source_id}</code></li>
          <li>原因：{t.reasons.map(reasonLabel).join('、')}</li>
          <li>状态 <span data-testid="task-status">{QUEUE_STATUS_LABEL[t.status] ?? t.status}</span>
            {t.status !== 'resolved' ? <> · <span data-testid="assignment">{assignmentLabel(t)}</span></> : null}
            {' '}· 创建于 {formatTime(t.created_at)}{t.assigned_at ? ` · 领取于 ${formatTime(t.assigned_at)}` : ''}</li>
        </ul>
      </section>
      {src === null ? <p className="muted">来源对象暂无可显示的结构化内容</p>
        : t.source_kind === 'grade_result' ? <GradeView g={src as GradeSource} />
        : t.source_kind === 'claim' ? <ClaimView c={src as ClaimSource} />
        : t.source_kind === 'diagnosis_step' ? <DiagnosisStepView d={src as DiagnosisStepSource} /> : null}
      <section aria-labelledby="h-actions">
        <h2 id="h-actions">处理</h2>
        <Actions t={t} codes={codes} />
      </section>
      <p><a href={`/review/queue?subject=${t.subject}`}>返回复核队列</a></p>
    </main>
  );
}
