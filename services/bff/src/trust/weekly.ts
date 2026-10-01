// 冲刺 5-H 跨科周报 v0 的 BFF 纯函数（不连库）：库里周报快照（0036 insight.weekly_report）的句子与客观栏再挑一次形状。
// 句子只保留要转给前端的字段；没有合法 claim_id、段落不属于这个受众、家长版缺置信度分档（或学生版带了）的一律丢弃——
// 丢弃后再由路由在库里核对每句的判断（trust-routes.ts 的 weeklyClaims）。客观栏固定标来源 objective，形状对不上就不显示。
import type { Audience } from '../shell/insight-client.ts';

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TEMPLATE_KEY = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$/;
/** 周报的固定段落（与 0036 insight.valid_weekly_sentences 一致）：学生版成长手记、家长版周报 */
export const WEEKLY_SECTIONS: Record<Audience, readonly string[]> = {
  student: ['lit', 'working_on', 'next'], guardian: ['progress', 'stuck', 'suggestion', 'prediction'],
};
const BANDS = ['few_evidence', 'moderate', 'strong'];
const OBJECTIVE_SUBJECTS = ['aya', 'zhixing', 'wenli'];

export interface WeeklySentence { claim_id: string; template_key: string; template_version: number; section: string; text: string;
                                  claim_revision: string; confidence_band?: string }
/** 判断修订标记（0036 守卫盖上的 insight.claim.updated_at，JSON 里的 timestamptz 文本） */
const REVISION = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}(:?\d{2})?)$/;

/** 库里的句子再挑一次形状（没有 claim_id 或修订标记、段落不属于这个受众、家长版缺置信度分档的都丢弃）；只留下路由要用的字段 */
export function pickWeeklySentence(raw: unknown, audience: Audience): WeeklySentence | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const s = raw as Record<string, unknown>;
  const text = s['text'];
  const version = s['template_version'];
  if (typeof s['claim_id'] !== 'string' || !UUID.test(s['claim_id']) || typeof s['template_key'] !== 'string' || !TEMPLATE_KEY.test(s['template_key'])
      || typeof version !== 'number' || !Number.isInteger(version) || version < 1
      || typeof s['section'] !== 'string' || !WEEKLY_SECTIONS[audience].includes(s['section'])
      || typeof text !== 'string' || text.trim() === '' || text.length > 300
      || typeof s['claim_revision'] !== 'string' || !REVISION.test(s['claim_revision'])) {
    return null;
  }
  const band = s['confidence_band'];
  if (audience === 'guardian' ? typeof band !== 'string' || !BANDS.includes(band) : band !== undefined) return null;
  // “初步判断”标注不转：路由按库里判断的当前状态推导（安全审查 F1）
  return { claim_id: s['claim_id'], template_key: s['template_key'], template_version: version, section: s['section'], text,
           claim_revision: s['claim_revision'], ...(audience === 'guardian' ? { confidence_band: band as string } : {}) };
}

/** 客观栏：固定标来源 objective；形状对不上就不显示（null） */
export function pickObjective(raw: unknown): Record<string, unknown> | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const int = (v: unknown, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= hi;
  if (o['source_type'] !== 'objective' || !int(o['usage_minutes'], 10080) || !int(o['plan_done'], 9999) || !int(o['plan_skipped'], 9999)
      || !Array.isArray(o['subjects'])) {
    return null;
  }
  const subjects = [];
  for (const x of o['subjects'].slice(0, 3)) {
    const r = x as Record<string, unknown>;
    if (!r || typeof r !== 'object' || typeof r['subject'] !== 'string' || !OBJECTIVE_SUBJECTS.includes(r['subject'])
        || !int(r['practice_minutes'], 10080) || !int(r['attempts'], 99999)) {
      return null;
    }
    subjects.push({ subject: r['subject'], practice_minutes: r['practice_minutes'], attempts: r['attempts'] });
  }
  return { source_type: 'objective', usage_minutes: o['usage_minutes'], plan_done: o['plan_done'], plan_skipped: o['plan_skipped'], subjects };
}

