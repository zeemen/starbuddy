// 星图 v1 的渲染（specs/star-map-v1-h5/spec.md 第 3、4 节；统一壳 spec 第 7.2、8 节）。只在开关 starmap_v1 打开时由 StarMapPage 使用。
// 每科一片星空：领域是星座、主题是子群（spec #5，排版待设计确认）；一页超过 120 颗星时各领域折叠（spec #8，v1-view.ts）。
// 判断先于展示（规则 11）：
//   - 画了的星（BFF 给了 claim_id）按 3-H 三态与“保持”外圈画，星位 <li> 带 data-claim-id，读屏的状态词只在它里面；
//   - 中性底星：与“没有证据”同一外观，没有可见文字；读屏读“暂无可展示的结论”（spec #9，文案在 src/shell/copy.ts）；
//   - 虚线星（开关 starmap_dashed）：只显示下一次验证日期，不带结论文字；
//   - 星数说明、迁移摘要只在 BFF 给了 RenderedStatement 时出现，文字在带 data-claim-id 的元素里；置信度说明只在家长端显示
//     （SIE 第八章；与周报、今日计划同一做法。BFF 两端都下发 confidence_band，学生端前端不显示）。
// 能力视图侧栏只有占位（统一壳 #8）：不发任何数据请求。界面文案全部取自 src/shell/copy.ts（本文件不写中文）。
import { CONFIDENCE_BAND, STAR_DISPLAY_V1, STAR_MAP_V1, subjectLabel } from '../shell/copy.ts';
import { ClaimLinks } from '../trust/links.tsx';
import type { StatementV1 } from '../lib/star-map-v1.ts';
import { STAR_SIZE } from './layout.ts';
import { Star } from './Star.tsx';
import type { ViewDomain, ViewMap, ViewSky, ViewStar, ViewTopic } from './v1-view.ts';

type Audience = 'student' | 'guardian';

const slug = (s: string): string => s.replace(/[^A-Za-z0-9_-]/g, '-');

function StarCount({ n }: { n: number }) {
  return <span className="star-count"><span data-value="count">{n}</span> {STAR_MAP_V1.starUnit}</span>;
}

/** 一句关于学生的陈述（星数说明 / 迁移摘要）：文字只来自 RenderedStatement.text，带 data-claim-id；置信度说明是文案文件里的固定句 */
function Statement({ s, kind, audience, studentId }: { s: StatementV1; kind: 'summary' | 'transfer'; audience: Audience; studentId: string }) {
  return (
    <p className="sky-statement" data-statement={kind}>
      <span data-claim-id={s.claim_id}>{s.text}</span>
      {audience === 'guardian' && CONFIDENCE_BAND[s.confidence_band] ? (
        <span className="muted confidence" data-confidence={s.confidence_band}> {CONFIDENCE_BAND[s.confidence_band]}</span>
      ) : null}
      {' '}<ClaimLinks studentId={studentId} claimId={s.claim_id} audience={audience} />
    </p>
  );
}

/** 一颗星的星位：16px 的星放在 24px 的格子里；读屏文字是“节点名：状态”（中性底星是“暂无可展示的结论”） */
function StarSlot({ star }: { star: ViewStar }) {
  const name = <span data-node-name="">{star.name}</span>;
  if (star.kind === 'painted') {
    const word = star.retained ? STAR_DISPLAY_V1.retained : STAR_DISPLAY_V1[star.display];
    return (
      <li className="star-slot" data-node-id={star.node_id} data-display={star.display} data-retained={star.retained ? 'true' : 'false'}
          data-claim-id={star.claim_id}>
        <Star display={star.display} retained={star.retained} size={STAR_SIZE} />
        <span className="visually-hidden">{name}{STAR_MAP_V1.sep}{word}</span>
      </li>
    );
  }
  if (star.kind === 'dashed') {
    return (
      <li className="star-slot" data-node-id={star.node_id} data-display="dashed" data-claim-id={star.claim_id}>
        <Star display="dashed" size={STAR_SIZE} />
        <span className="visually-hidden">{name}{STAR_MAP_V1.sep}<NextCheck dueOn={star.due_on} /></span>
      </li>
    );
  }
  return (
    <li className="star-slot" data-node-id={star.node_id} data-display="neutral">
      <Star display="neutral" size={STAR_SIZE} />
      <span className="visually-hidden">{name}{STAR_MAP_V1.sep}{STAR_MAP_V1.neutral}</span>
    </li>
  );
}

function NextCheck({ dueOn }: { dueOn: string | null }) {
  return dueOn ? <>{STAR_MAP_V1.nextCheck} <span data-value="date">{dueOn}</span></> : <>{STAR_MAP_V1.nextCheckPending}</>;
}

function Topic({ t, idBase }: { t: ViewTopic; idBase: string }) {
  const id = `${idBase}-${slug(t.topic_id)}`;
  const dashed = t.stars.filter((s): s is Extract<ViewStar, { kind: 'dashed' }> => s.kind === 'dashed');
  return (
    <div className="subgroup" data-topic-id={t.topic_id}>
      <h4 id={id}>{t.name ? <span data-node-name="">{t.name}</span> : STAR_MAP_V1.unnamedTopic}</h4>
      <ul className="topic-field" aria-labelledby={id}>
        {t.stars.map((s) => <StarSlot key={s.node_id} star={s} />)}
      </ul>
      {dashed.length > 0 ? (
        // 虚线星的下一次验证日期（可见；读屏已在星位里读过，这里不重复）
        <ul className="next-checks" aria-hidden="true">
          {dashed.map((s) => (
            <li key={s.node_id} data-claim-id={s.claim_id} data-node-id={s.node_id}>
              <span data-node-name="">{s.name}</span>{STAR_MAP_V1.sep}<NextCheck dueOn={s.due_on} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Constellation({ d, subject, folded }: { d: ViewDomain; subject: string; folded: boolean }) {
  const idBase = `dom-${subject}-${slug(d.domain_id)}`;
  const name = d.name ? <span data-node-name="">{d.name}</span> : STAR_MAP_V1.unnamedDomain;
  const topics = d.topics.map((t) => <Topic key={t.topic_id} t={t} idBase={idBase} />);
  if (folded) {
    return (
      <details className="constellation" data-domain-id={d.domain_id} data-folded="true">
        <summary><h3 id={idBase} className="constellation-name">{name} <StarCount n={d.count} /></h3></summary>
        {topics}
      </details>
    );
  }
  return (
    <section className="constellation" data-domain-id={d.domain_id} data-folded="false" aria-labelledby={idBase}>
      <h3 id={idBase}>{name} <StarCount n={d.count} /></h3>
      {topics}
    </section>
  );
}

function SkyV1({ sky, folded, audience, studentId }: { sky: ViewSky; folded: boolean; audience: Audience; studentId: string }) {
  const headingId = `sky-v1-${sky.subject}`;
  return (
    <section className="sky-v1" aria-labelledby={headingId} data-subject={sky.subject} data-status={sky.status}>
      <h2 id={headingId}>{subjectLabel(sky.subject)}</h2>
      {sky.status === 'building' ? (
        <p className="sky-building" data-testid={`building-${sky.subject}`}>{STAR_MAP_V1.building}</p>
      ) : (
        <>
          {sky.summary ? <Statement s={sky.summary} kind="summary" audience={audience} studentId={studentId} /> : null}
          {sky.transfer ? <Statement s={sky.transfer} kind="transfer" audience={audience} studentId={studentId} /> : null}
          {sky.domains.map((d) => <Constellation key={d.domain_id} d={d} subject={sky.subject} folded={folded} />)}
        </>
      )}
    </section>
  );
}

/** 能力视图侧栏占位：签字前为空，不发数据请求（统一壳待人确认 #8） */
export function SkillViewAside() {
  return (
    <aside className="skill-view" aria-labelledby="skill-view-title" data-testid="skill-view" data-status="coming_soon">
      <h2 id="skill-view-title">{STAR_MAP_V1.skillViewTitle}</h2>
      <p className="muted">{STAR_MAP_V1.skillViewComing}</p>
    </aside>
  );
}

export function StarMapV1View({ view, audience, studentId }: { view: ViewMap; audience: Audience; studentId: string }) {
  return (
    <div className="starmap-v1" data-starmap="v1" data-audience={audience} data-folded={view.folded ? 'true' : 'false'}>
      <div className="starmap-v1-skies">
        {view.folded ? <p className="muted" data-testid="folded-hint">{STAR_MAP_V1.folded}</p> : null}
        {view.skies.length === 0 ? (
          <p data-testid="empty">{STAR_MAP_V1.noSubjects}</p>
        ) : (
          view.skies.map((sky) => <SkyV1 key={sky.subject} sky={sky} folded={view.folded} audience={audience} studentId={studentId} />)
        )}
      </div>
      <SkillViewAside />
    </div>
  );
}
