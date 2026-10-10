// 今日任务列表（一科）：任务类型用固定中文；标题是知识节点名，拿不到时用“数学 · 本周任务”这类中性标题，不显示编码。
// 推荐理由（规则 11）：内容类是固定短句（copy.ts）；关于学生的句子只来自 BFF 转交的 RenderedStatement.text，渲染为带 data-claim-id 的元素，
// 家长端旁边显示置信度说明；没有判断就不显示理由。理由旁是“为什么”入口（冲刺 5-H，开关 why 默认关，关时不渲染；
// 需要 studentId 才能拼出入口地址，没给就不渲染）。
// 跨科有序列表（PLT-11，src/today/OrderedTaskList.tsx，开关 today_orch 默认关）用同一个 Reason：关于学生的句子仍只在本文件渲染。
import type { Rationale, TodaySection } from '../lib/shell-api.ts';
import { CONFIDENCE_BAND, CONTENT_RATIONALE, TASK_KIND, TODAY } from './copy.ts';
import { ClaimLinks } from '../trust/links.tsx';

export function Reason({ r, audience, studentId }: { r: Rationale; audience: 'student' | 'guardian'; studentId?: string }) {
  return (
    <>
      {r && r.kind === 'content' && CONTENT_RATIONALE[r.code] ? (
        <p className="task-reason" data-rationale="content">{CONTENT_RATIONALE[r.code]}</p>
      ) : null}
      {r && r.kind === 'claim' ? (
        <p className="task-reason" data-rationale="claim">
          <span data-claim-id={r.claim_id}>{r.text}</span>
          {audience === 'guardian' ? <span className="muted confidence" data-confidence={r.confidence_band}> {CONFIDENCE_BAND[r.confidence_band]}</span> : null}
          {studentId ? <>{' '}<ClaimLinks studentId={studentId} claimId={r.claim_id} audience={audience} /></> : null}
        </p>
      ) : null}
    </>
  );
}

export function TaskList({ section, audience, studentId }: { section: TodaySection; audience: 'student' | 'guardian'; studentId?: string }) {
  return (
    <ol className="task-list">
      {section.tasks.map((t, i) => (
        <li key={i} className="task" data-kind={t.kind}>
          <span className="task-kind">{TASK_KIND[t.kind] ?? TASK_KIND['practice']}</span>{' '}
          <span className="task-title">{t.node_name ?? TODAY.neutralTitle(section.subject)}</span>{' '}
          <span className="muted">{TODAY.minutes(t.estimated_minutes)}</span>
          <Reason r={t.rationale} audience={audience} studentId={studentId} />
        </li>
      ))}
    </ol>
  );
}
