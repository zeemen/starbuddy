// 首份报告开发演示页（SIE-02b 第二步 PR 2，开关 report）的纯函数：BFF 响应的前端再断言、待验证判断编号的解析。不引用 next/*，单测直接调用。
// 判断先于展示（规则 11）：前端对 BFF 给的报告再断言一次——受众对得上、每句带合法 claim_id 与非空文字、假设才标“初步判断”、
// 置信度分档只在家长版；任何一处不合整份不显示（与 BFF、洞察服务同一口径：不挑句子）。BFF 转来的报告里不该有信封外层的键
// （confidence、evidence_ids、source_version、model_version），出现了也整份不显示。
// 待验证的判断只取单条解释（pickWhy，带本页受众），必须是虚线且不带文字（BFF 已裁掉）；虚线不作为陈述，页面不显示它的文字。
import { pickWhy, UUID, type Audience, type Band } from './view.ts';

const obj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
export const BANDS: readonly string[] = ['few_evidence', 'moderate', 'strong'];
export const SECTION_KEYS = ['node_mastery', 'node_transfer'] as const;
const OUTER = ['confidence', 'evidence_ids', 'source_version', 'model_version', 'human_review_required', 'review_reasons'];
/** 演示页一次最多取几条待验证判断的解释 */
export const MAX_VERIFY = 5;

export interface ReportSentenceView { claim_id: string; status: 'hypothesis' | 'established'; text: string; preliminary: boolean; confidence_band: Band | null }
export interface ReportSectionView { key: (typeof SECTION_KEYS)[number]; connective: string; sentences: ReportSentenceView[] }
export interface FirstReportView { notice: string; title: string; sections: ReportSectionView[] }

const text = (v: unknown, max: number): v is string => typeof v === 'string' && v.trim() !== '' && v.length <= max;

function pickSentence(s: unknown, audience: Audience): ReportSentenceView | null {
  if (!obj(s) || OUTER.some((k) => k in s)) return null;
  const { claim_id: id, status, display, label, confidence_band: band } = s;
  if (typeof id !== 'string' || !UUID.test(id) || (status !== 'hypothesis' && status !== 'established') || display !== 'solid' || !text(s['text'], 300)) {
    return null;
  }
  if ((status === 'hypothesis') !== (label === 'preliminary') || (label !== undefined && label !== 'preliminary')) return null;
  if (audience === 'guardian' ? typeof band !== 'string' || !BANDS.includes(band) : band !== undefined) return null;
  return { claim_id: id, status, text: s['text'] as string, preliminary: status === 'hypothesis',
           confidence_band: audience === 'guardian' ? band as Band : null };
}

/**
 * BFF 的 { student_id, report }：report 为 null 返回 'none'（还没有报告）；形状不合返回 null（不显示，按“暂时无法获取”处理）。
 */
export function pickFirstReport(raw: unknown, audience: Audience): FirstReportView | 'none' | null {
  if (!obj(raw) || !('report' in raw)) return null;
  const r = raw['report'];
  if (r === null) return 'none';
  if (!obj(r) || OUTER.some((k) => k in r) || r['audience'] !== audience || r['format'] !== 'sie-first-report/0.1') return null;
  if (!text(r['notice'], 200) || !text(r['title'], 80) || !Array.isArray(r['sections']) || r['sections'].length < 1 || r['sections'].length > 2) return null;
  const sections: ReportSectionView[] = [];
  for (const sec of r['sections'] as unknown[]) {
    if (!obj(sec) || !(SECTION_KEYS as readonly unknown[]).includes(sec['key']) || sections.some((x) => x.key === sec['key'])
        || !text(sec['connective'], 60) || !Array.isArray(sec['sentences']) || sec['sentences'].length < 1 || sec['sentences'].length > 200) {
      return null;
    }
    const sentences: ReportSentenceView[] = [];
    for (const s of sec['sentences'] as unknown[]) {
      const p = pickSentence(s, audience);
      if (!p) return null;
      sentences.push(p);
    }
    sections.push({ key: sec['key'] as ReportSectionView['key'], connective: sec['connective'] as string, sentences });
  }
  const ids = sections.flatMap((s) => s.sentences.map((x) => x.claim_id));
  if (new Set(ids).size !== ids.length) return null;
  return { notice: r['notice'] as string, title: r['title'] as string, sections };
}

/** 查询参数 verify（逗号分隔的判断编号）：只收小写 UUID，去重，至多 MAX_VERIFY 条 */
export function parseVerifyIds(v: unknown): string[] {
  const raw = Array.isArray(v) ? v.join(',') : typeof v === 'string' ? v : '';
  return [...new Set(raw.split(',').map((x) => x.trim()).filter((x) => UUID.test(x)))].slice(0, MAX_VERIFY);
}

/**
 * 单条解释里的待验证判断：受众对得上、必须属于所问的判断、状态待验证、虚线、不带文字（BFF 已去掉；带了整条不显示）、
 * 学生受众不带置信度分档；成立与假设（实线）不进待验证区
 */
export function pickVerify(raw: unknown, claimId: string, audience: Audience): { claim_id: string } | null {
  const w = pickWhy(raw, claimId, audience);
  return w && w.statement.status === 'to_verify' && w.statement.display === 'dashed' && w.statement.text === null ? { claim_id: w.claim_id } : null;
}
