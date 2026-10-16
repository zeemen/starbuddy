// 错题再练的视图挑选与错误映射（纯函数，单测在 tests/practice.test.ts）。
// BFF 已按白名单只转条目状态、轮次、节点与层次、题面、判分结论与条目新状态；这里再按同一份白名单挑一遍（防止以后多给了什么被原样带进页面）：
// 不渲染掌握、置信度、证据 ID、错因、题目引用、复测标记、同族限定标记、空原因编码（规则 3、11）。
// 任何一条形状不对，整页按“暂时无法获取”处理（不挑着显示一部分）；唯一的例外是知识节点编号：不合前端口径时只隐藏这一行的编号（node_id 置 null），
// 其余照常显示（编号只是给人看的标签，不参与任何判断）。
import type { PracticeApi } from '../lib/practice-api.ts';
import { pickRich, type RichItem } from '../lib/rich-item.ts';

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** 公共知识层的节点编号（与 BFF 今日计划同一口径）；不合的不显示 */
export const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const TICKET = /^sbp1\.[A-Za-z0-9_-]+$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;

export const ENTRY_STATUSES = ['due', 'waiting', 'closed', 'exhausted', 'unavailable'] as const;
export const AFTER_STATUSES = ['due', 'waiting', 'closed', 'exhausted'] as const;
export const LEVELS = ['know', 'understand', 'apply', 'synthesize'] as const;
export const SOURCES = ['wrong', 'hint_followup'] as const;
export const VERDICTS = ['correct', 'incorrect', 'partial', 'needs_review', 'not_applicable'] as const;

export type EntryStatus = (typeof ENTRY_STATUSES)[number];
export type AfterStatus = (typeof AFTER_STATUSES)[number];

export interface EntryView {
  /** 不合 NODE_ID 时为 null：页面只隐藏这一行的编号 */
  entry_id: string; node_id: string | null; cognitive_level: (typeof LEVELS)[number]; source: (typeof SOURCES)[number]; status: EntryStatus;
  round: number; max_rounds: number; next_round_at: string | null;
}
/** rich：PLT-12 受限富文本题面（只在学习服务开了 present_item 题面来源时有；src/lib/rich-item.ts） */
export interface ItemView { format: 'single' | 'multi' | 'fill'; stem: string; options?: { key: string; text: string }[]; rich?: RichItem }
export interface ReadyView { entry_id: string; round: number; item: ItemView; ticket: string }
export interface AnswerView {
  /** 待复核时为 null：页面只显示“等待复核”，不显示结论 */
  verdict: Exclude<(typeof VERDICTS)[number], 'needs_review'> | null;
  pending_review: boolean;
  entry: { entry_id: string; status: AfterStatus; round: number; next_round_at: string | null };
}

const isRecord = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 1 && v <= 1000;
const isTime = (v: unknown): v is string => typeof v === 'string' && RFC3339.test(v) && Number.isFinite(Date.parse(v));
const oneOf = <T extends string>(xs: readonly T[], v: unknown): v is T => typeof v === 'string' && (xs as readonly string[]).includes(v);

function pickEntry(e: unknown): EntryView | null {
  if (!isRecord(e)) return null;
  const { entry_id, node_id, cognitive_level, source, status, round, max_rounds, next_round_at } = e;
  if (typeof entry_id !== 'string' || !UUID.test(entry_id)
      || !oneOf(LEVELS, cognitive_level) || !oneOf(SOURCES, source) || !oneOf(ENTRY_STATUSES, status) || !isInt(round) || !isInt(max_rounds)
      || (next_round_at !== null && !isTime(next_round_at))) {
    return null;
  }
  const node = typeof node_id === 'string' && NODE_ID.test(node_id) ? node_id : null;
  return { entry_id, node_id: node, cognitive_level, source, status, round, max_rounds, next_round_at: next_round_at as string | null };
}

/** 列表：每条只取这些字段；empty_reason 等编码不上页面 */
export function pickQueue(raw: unknown): EntryView[] | null {
  if (!isRecord(raw) || !Array.isArray(raw['entries']) || raw['entries'].length > 200) return null;
  const out = raw['entries'].map(pickEntry);
  return out.every((e): e is EntryView => e !== null) ? out : null;
}

export type NextView = { kind: 'ready'; ready: ReadyView } | { kind: 'empty' };

/** 取题：一道题（题面只有 format、stem、options）与不透明票据，或这一轮不可用（空原因编码不上页面） */
export function pickNext(raw: unknown, entry: string): NextView | null {
  if (!isRecord(raw)) return null;
  if (raw['status'] === 'unavailable') return { kind: 'empty' };
  if (raw['status'] !== 'ready') return null;
  const { entry_id, round, item, ticket } = raw;
  if (entry_id !== entry || !isInt(round) || typeof ticket !== 'string' || ticket.length > 2400 || !TICKET.test(ticket) || !isRecord(item)) return null;
  const { format, stem, options } = item;
  if (!oneOf(['single', 'multi', 'fill'] as const, format) || typeof stem !== 'string' || stem === '' || stem.length > 4000) return null;
  let opts: { key: string; text: string }[] | undefined;
  if (options !== undefined) {
    if (!Array.isArray(options) || options.length > 26
        || !options.every((o) => isRecord(o) && typeof o['key'] === 'string' && /^[A-Za-z0-9]{1,8}$/.test(o['key']) && typeof o['text'] === 'string')) {
      return null;
    }
    opts = options.map((o) => ({ key: (o as { key: string }).key, text: (o as { text: string }).text }));
  }
  if ((format === 'fill') !== (opts === undefined || opts.length === 0)) return null;
  // PLT-12：富文本题面按白名单重建；不合格就丢掉 rich，退回纯文字题面照常出题（与 BFF、诊断同一口径，审查建议 10）
  let rich: RichItem | undefined;
  if (item['rich'] !== undefined) {
    rich = pickRich(item['rich'], (opts ?? []).map((o) => o.key)) ?? undefined;
  }
  return { kind: 'ready', ready: { entry_id: entry, round, ticket,
                                   item: { format, stem, ...(opts && opts.length > 0 ? { options: opts } : {}), ...(rich ? { rich } : {}) } } };
}

/** 作答结果：判分结论（契约枚举）与条目新状态；待复核（pending_review 或 needs_review）时不给结论 */
export function pickAnswer(raw: unknown, entry: string): AnswerView | null {
  if (!isRecord(raw) || !oneOf(VERDICTS, raw['verdict']) || !isRecord(raw['entry'])) return null;
  if (raw['pending_review'] !== undefined && raw['pending_review'] !== true) return null;
  const { entry_id, status, round, next_round_at } = raw['entry'];
  if (entry_id !== entry || !oneOf(AFTER_STATUSES, status) || !isInt(round) || (next_round_at !== null && !isTime(next_round_at))) return null;
  const pending = raw['pending_review'] === true || raw['verdict'] === 'needs_review';
  const verdict = pending ? null : (raw['verdict'] as AnswerView['verdict']);
  return { verdict, pending_review: pending, entry: { entry_id, status, round, next_round_at: next_round_at as string | null } };
}

/** 页面上的错误编码（PRACTICE_ERROR 的键）；not_found 页面按“没有找到”，login 去登录页 */
export type UiError =
  | 'curfew' | 'no_entitlement' | 'daily_limit_reached' | 'not_due' | 'ticket_used' | 'expired' | 'invalid_answer' | 'retry' | 'rate_limited'
  | 'unavailable' | 'not_found' | 'login';

/**
 * BFF 的失败 → 页面错误编码（services/bff/openapi/practice.v0.yaml）：
 *   402（宵禁、没有权益、今天的自练次数用完）、409（条目不开、票据已用）、410（票据过期）、422（作答形状不对、票据无效）、
 *   429（作答时被限流：请求没进 BFF 的处理，票据还能用；稍等之后用同一张票据、原样的作答再提交一次）、
 *   503（UPSTREAM_UNAVAILABLE / 断连：作答时结果未知，用同一张票据再提交一次，BFF 不再计次；PRACTICE_UNAVAILABLE：确定失败；
 *   SUBJECT_BUSY：分学科并发闸门满了，本次没有新增计量；作答时按 429 同样处理（留票据、原样重发），取题时按“稍后再试”）。
 */
export function uiError(r: Extract<PracticeApi<unknown>, { ok: false }>, op: 'next' | 'answer'): UiError {
  const { status, code, reason } = r;
  if (status === 401) return 'login';
  if (status === 404) return 'not_found';
  if (status === 402) return reason === 'curfew' || reason === 'no_entitlement' || reason === 'daily_limit_reached' ? reason : 'unavailable';
  if (status === 409 && code === 'PRACTICE_NOT_DUE') return 'not_due';
  if (status === 409 && (code === 'TICKET_USED' || code === 'KEY_SEALED')) return 'ticket_used';
  if (status === 410) return 'expired';
  if (status === 422 && code === 'TICKET_INVALID') return 'ticket_used';
  if (status === 422 && code === 'INVALID_ANSWER') return 'invalid_answer';
  // PLT-16 分学科并发闸门：这一科暂时满了（本次请求没有新增计量）。作答按限流处理：留着票据、原样重发首次作答
  // （之前若有一次结果未知的提交已计次，同一张票据重试绕过闸门、不再计次）；取题沿用“暂时无法完成，请稍后再试”。不另加文案
  if (status === 503 && code === 'SUBJECT_BUSY') return op === 'answer' ? 'rate_limited' : 'unavailable';
  if (op === 'answer' && status === 503 && (code === 'UPSTREAM_UNAVAILABLE' || code === 'UNAVAILABLE')) return 'retry';
  if (op === 'answer' && status === 429) return 'rate_limited';
  return 'unavailable';
}

/** 这张票据还能不能再用：结果未知（retry）、被限流（rate_limited）与作答形状不对（invalid_answer）留着票据；其余要换一道题或回列表 */
export const keepsTicket = (e: UiError): boolean => e === 'retry' || e === 'rate_limited' || e === 'invalid_answer';
/**
 * 再提交时必须原样发首次提交的作答快照（作答控件禁用）：幂等键在票据里，同一张票据换了作答会被 BFF 判 TICKET_USED。
 * 限流时分不清之前有没有一次结果未知的提交，按同样处理。
 */
export const resubmitsSnapshot = (e: UiError): boolean => e === 'retry' || e === 'rate_limited';
/** 需要换一道题（重新取题）的错误 */
export const needsNewItem = (e: UiError): boolean => e === 'ticket_used' || e === 'expired';

/** 北京时间 YYYY-MM-DD HH:mm（下一轮开放时间；放在 data-value 元素里，不拼进文案） */
export function shanghaiMinute(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  return new Date(t + 8 * 3600_000).toISOString().slice(0, 16).replace('T', ' ');
}
