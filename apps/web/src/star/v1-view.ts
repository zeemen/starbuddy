// 星图 v1 的显示整理（纯函数，tests/star-v1.test.ts 覆盖；组件见 StarMapV1.tsx）：
//   - 只显示数学、语文、道法三科（英语与其他编码一律不显示，即使 BFF 给了）；
//   - 虚线开关 starmap_dashed 关时，BFF 给的待验证星按中性底星画（去掉 claim_id 与日期）；
//   - 防御：没有 claim_id 的“画了的星”、不认识的 display 一律按中性底星（宁可不画，规则 11）；没有 claim_id 或文字的陈述不显示；
//   - 折叠（spec 待人确认 #8 默认）：一页的星数超过 120 颗时，各科按领域折叠（星座收起，点开看星）。
import type { DomainV1, PaintedDisplay, SkyV1, StarMapV1, StarV1, StatementV1 } from '../lib/star-map-v1.ts';

/** 星图 v1 显示的学科与顺序（与 BFF、选科同一顺序；英语搁置，不显示） */
export const SHOWN_SUBJECTS = ['math', 'chinese', 'politics'] as const;
/** 单屏星数上限：超过就按领域折叠 */
export const FOLD_THRESHOLD = 120;

const PAINTED: readonly string[] = ['unlit', 'half', 'lit'];

export type ViewStar =
  | { kind: 'neutral'; node_id: string; name: string }
  | { kind: 'painted'; node_id: string; name: string; display: PaintedDisplay; retained: boolean; claim_id: string }
  | { kind: 'dashed'; node_id: string; name: string; claim_id: string; due_on: string | null };

export interface ViewTopic { topic_id: string; name: string | null; stars: ViewStar[] }
export interface ViewDomain { domain_id: string; name: string | null; topics: ViewTopic[]; count: number }
export type ViewSky =
  | { subject: string; status: 'building' }
  | { subject: string; status: 'ok'; domains: ViewDomain[]; count: number; summary: StatementV1 | null; transfer: StatementV1 | null };
export interface ViewMap { skies: ViewSky[]; count: number; folded: boolean }

const neutral = (s: { node_id: string; name: string }): ViewStar => ({ kind: 'neutral', node_id: s.node_id, name: s.name });
const hasClaim = (s: object): s is { claim_id: string } =>
  typeof (s as { claim_id?: unknown }).claim_id === 'string' && (s as { claim_id: string }).claim_id !== '';

export function viewStar(s: StarV1, dashed: boolean): ViewStar {
  if (PAINTED.includes(s.display) && hasClaim(s)) {
    const p = s as Extract<StarV1, { display: PaintedDisplay }>;
    return { kind: 'painted', node_id: p.node_id, name: p.name, display: p.display, retained: p.display === 'lit' && p.retained === true,
             claim_id: p.claim_id };
  }
  if (s.display === 'dashed' && dashed && hasClaim(s)) {
    return { kind: 'dashed', node_id: s.node_id, name: s.name, claim_id: s.claim_id, due_on: s.next_check?.due_on ?? null };
  }
  return neutral(s);
}

const statement = (s: StatementV1 | undefined): StatementV1 | null =>
  s && typeof s.claim_id === 'string' && s.claim_id !== '' && typeof s.text === 'string' && s.text.trim() !== '' ? s : null;

function viewDomain(d: DomainV1, dashed: boolean): ViewDomain {
  const topics = d.topics.map((t) => ({ topic_id: t.topic_id, name: t.name, stars: t.stars.map((s) => viewStar(s, dashed)) }));
  return { domain_id: d.domain_id, name: d.name, topics, count: topics.reduce((n, t) => n + t.stars.length, 0) };
}

export function viewSky(sky: SkyV1, dashed: boolean): ViewSky {
  if (sky.status !== 'ok') return { subject: sky.subject, status: 'building' };
  const domains = sky.domains.map((d) => viewDomain(d, dashed));
  return { subject: sky.subject, status: 'ok', domains, count: domains.reduce((n, d) => n + d.count, 0),
           summary: statement(sky.summary), transfer: statement(sky.transfer_summary) };
}

/** 整页的显示结构：只留三科（按固定顺序），统计星数并决定是否按领域折叠 */
export function viewMap(map: Pick<StarMapV1, 'skies'>, opts: { dashed: boolean }): ViewMap {
  const order = SHOWN_SUBJECTS as readonly string[];
  const skies = map.skies.filter((s) => order.includes(s.subject))
    .sort((a, b) => order.indexOf(a.subject) - order.indexOf(b.subject))
    .map((s) => viewSky(s, opts.dashed));
  const count = skies.reduce((n, s) => n + (s.status === 'ok' ? s.count : 0), 0);
  return { skies, count, folded: count > FOLD_THRESHOLD };
}
