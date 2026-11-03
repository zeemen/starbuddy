// 星图 v1 的数据：类型与 BFF 客户端（只在服务端调用；浏览器不直连 BFF）。响应结构见 services/bff/openapi/star-map.v1.yaml。
// 星 = 已发布地图里的知识节点，按领域（星座）、主题（子群）分组；判断先于展示——前端只用 BFF 给的 display / retained / claim_id，
// 不自己推算掌握状态，也不补任何关于学生的文字（规则 2、11）。只在开关 starmap_v1 打开时调用（src/star/features.ts）。
import { bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';
import { isUuid } from './star-map.ts';

export type PaintedDisplay = 'unlit' | 'half' | 'lit';

export type StarV1 =
  | { node_id: string; name: string; display: 'neutral' }
  | { node_id: string; name: string; display: PaintedDisplay; retained: boolean; claim_id: string }
  | { node_id: string; name: string; display: 'dashed'; claim_id: string; next_check: { due_on: string } | null };

export interface TopicV1 { topic_id: string; name: string | null; stars: StarV1[] }
export interface DomainV1 { domain_id: string; name: string | null; topics: TopicV1[] }

/** 洞察服务渲染的一句陈述（RenderedStatement 的三个字段，star-map.v1.yaml 的 Statement：三者都必填）：页面上渲染为带 data-claim-id 的元素；
 *  置信度说明只在家长端显示 */
export interface StatementV1 { claim_id: string; text: string; confidence_band: 'few_evidence' | 'moderate' | 'strong' }

/** star-map.v1.yaml 的 SubjectCode（英语不出现）。类型与接口定义一致；运行时 v1-view.ts 仍按这张表过滤（防御，不信任响应） */
export type SubjectV1 = 'math' | 'chinese' | 'politics';

export type SkyV1 =
  | { subject: SubjectV1; status: 'building' }
  | { subject: SubjectV1; status: 'ok'; map_version: string; domains: DomainV1[]; summary?: StatementV1; transfer_summary?: StatementV1 };

export interface StarMapV1 {
  student_id: string;
  generated_at: string;
  skill_view: { status: 'coming_soon' };
  skies: SkyV1[];
}

export type StarMapV1Result = { kind: 'ok'; map: StarMapV1 } | { kind: 'unauthenticated' } | { kind: 'not-found' };

/** BFF 的 v1 路径（开关 starmap_v1 关时 BFF 不注册，与表外路径同一个 404） */
export const starMapV1Path = (studentId: string): string => `/bff/v0/students/${studentId}/star-map/v1`;

/** 以会话身份读星图 v1（会话无效 401 → 去登录；未授权、不存在、BFF 开关关都是 404） */
export async function fetchStarMapV1(studentId: string, session: BffSession | null): Promise<StarMapV1Result> {
  if (!session) return { kind: 'unauthenticated' };
  if (!isUuid(studentId)) return { kind: 'not-found' };
  const r = await fetch(`${bffBaseUrl()}${starMapV1Path(studentId)}`, {
    headers: await bffHeaders(session, false),
    cache: 'no-store',
  });
  if (r.status === 401) return { kind: 'unauthenticated' };
  if (r.status === 404) return { kind: 'not-found' };
  if (!r.ok) throw new Error(`star map v1 request failed: ${r.status}`);
  return { kind: 'ok', map: (await r.json()) as StarMapV1 };
}
