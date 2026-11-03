// 一片星空（spec 第 3、5 节）：标题是学科中文名；星空整体是一个 role="img"，aria-label 是摘要，
// 959 颗星不逐个进入 Tab 顺序；旁边的“以列表查看”是真实的 <table>，读屏与键盘用户由它读取每颗星。
// 16px 的星不做单独点击（触控目标不到 44px），逐颗详情在列表里。
import { skySummaryLabel, statusText, subjectName } from '../lib/labels.ts';
import type { Sky } from '../lib/star-map.ts';
import { MAX_COLS, orderStars, squareColumns, STAR_SIZE } from './layout.ts';
import { Star } from './Star.tsx';

export function StarSky({ sky }: { sky: Sky }) {
  const stars = orderStars(sky.stars);
  const headingId = `sky-${sky.subject}`;
  return (
    <section className="sky" aria-labelledby={headingId} data-subject={sky.subject}>
      <h2 id={headingId}>{subjectName(sky.subject)}</h2>
      <div
        className="sky-field"
        role="img"
        aria-label={skySummaryLabel(sky)}
        data-summary-lit={sky.summary.lit}
        data-summary-half={sky.summary.half}
        data-summary-unlit={sky.summary.unlit}
        data-cols={Math.min(squareColumns(stars.length), MAX_COLS)}
      >
        {stars.map((s) => (
          <Star key={s.skill} display={s.display} retained={s.retained} size={STAR_SIZE} />
        ))}
      </div>
      <details className="sky-list">
        <summary>以列表查看</summary>
        <table>
          <caption>{subjectName(sky.subject)}：每个能力点的状态</caption>
          <thead>
            <tr>
              <th scope="col">能力点编码</th>
              <th scope="col">状态</th>
              <th scope="col">待验证</th>
            </tr>
          </thead>
          <tbody>
            {stars.map((s) => (
              <tr key={s.skill} data-display={s.display} data-retained={s.retained ? 'true' : 'false'}>
                <td>{s.label_code}</td>
                <td>{statusText(s)}</td>
                <td>{s.needs_verification ? '待验证' : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
