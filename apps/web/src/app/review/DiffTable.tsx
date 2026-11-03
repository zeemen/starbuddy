// 内容版本与考情方案的差异表（冲刺 4，spec 第 4、7、8 节）：逐条列出旧值 → 新值与风险（风险由后端规则判定，判不出的按高风险）。
// 值原样显示为 JSON（与地图版本审核页同一做法）；颜色之外一定有文字（WCAG 1.4.1）。
import { LEVEL_LABEL, OBJECT_LABEL, OP_LABEL, POLICY_OBJECT_LABEL, REASON_LABEL, type Change } from '../../lib/review.ts';

export function show(v: unknown): string {
  if (v === null || v === undefined) return '—';
  return JSON.stringify(v);
}

export function DiffTable({ changes, caption, testId, labels = 'content' }: {
  changes: Omit<Change, 'issues'>[];
  caption: string;
  testId: string;
  labels?: 'content' | 'policy';
}) {
  if (changes.length === 0) return <p data-testid={`${testId}-empty`}>没有差异</p>;
  const objectLabel = labels === 'policy' ? POLICY_OBJECT_LABEL : OBJECT_LABEL;
  return (
    <div className="table-scroll">
      <table className="review-table" data-testid={testId}>
        <caption>{caption}</caption>
        <thead>
          <tr><th scope="col">对象</th><th scope="col">键</th><th scope="col">变化</th><th scope="col">旧值</th><th scope="col">新值</th>
            <th scope="col">风险</th></tr>
        </thead>
        <tbody>
          {changes.map((c) => (
            <tr key={`${c.object}|${c.key}|${c.field ?? ''}|${c.op}`} data-object={c.object} data-key={c.key} data-field={c.field ?? ''}
              data-level={c.risk.level}>
              <td>{objectLabel[c.object] ?? c.object}</td>
              <td><code>{c.key}</code></td>
              <td>{OP_LABEL[c.op] ?? c.op}{c.field ? ` · ${c.field}` : ''}</td>
              <td>{c.op === 'added' ? '—' : show(c.old)}</td>
              <td>{c.op === 'removed' ? '—' : show(c.new)}</td>
              <td><span className={`risk risk-${c.risk.level}`}>{LEVEL_LABEL[c.risk.level] ?? c.risk.level}风险</span>{' '}
                <span className="muted">{REASON_LABEL[c.risk.reason] ?? c.risk.reason}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
