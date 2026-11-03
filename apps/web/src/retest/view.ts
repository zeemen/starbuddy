// 复测与验证题的视图挑选与错误映射（纯函数，单测在 tests/retest.test.ts）。
// BFF 已按白名单只转条目 ID、节点与层次、类别、状态、题面、判分结论与条目新状态；这里再按同一份白名单挑一遍：
// 不渲染掌握、置信度、证据 ID、错因、任务原因、空原因编码、题目引用（规则 3、11）。形状不对整页按“暂时无法获取”处理；
// 唯一的例外是知识节点编号：不合前端口径时只隐藏这一行的编号。题面的挑选与再练同一份（src/practice/view.ts 的 pickNext 口径）。
import type { RetestApi } from '../lib/retest-api.ts';
import { pickRich, type RichItem } from '../lib/rich-item.ts';
import { LEVELS, NODE_ID, uiError as practiceUiError, UUID, VERDICTS, type ItemView, type UiError } from '../practice/view.ts';

export { UUID };
const TICKET = /^sbr1\.[A-Za-z0-9_-]+$/;

export const KINDS = ['retest', 'verify'] as const;
export const ENTRY_STATUSES = ['due', 'unavailable'] as const;
export const AFTER_STATUSES = ['due', 'done'] as const;

export interface RetestEntryView {
  entry_id: string; node_id: string | null; cognitive_level: (typeof LEVELS)[number]; kind: (typeof KINDS)[number];
  status: (typeof ENTRY_STATUSES)[number];
}
export interface RetestReadyView { entry_id: string; kind: (typeof KINDS)[number]; item: ItemView; ticket: string }
export interface RetestAnswerView {
  /** 待复核时为 null：页面只显示“等待复核” */
  verdict: Exclude<(typeof VERDICTS)[number], 'needs_review'> | null;
  pending_review: boolean;
  entry: { entry_id: string; status: (typeof AFTER_STATUSES)[number] };
}

const isRecord = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const oneOf = <T extends string>(xs: readonly T[], v: unknown): v is T => typeof v === 'string' && (xs as readonly string[]).includes(v);

function pickEntry(e: unknown): RetestEntryView | null {
  if (!isRecord(e)) return null;
  const { entry_id, node_id, cognitive_level, kind, status } = e;
  if (typeof entry_id !== 'string' || !UUID.test(entry_id) || !oneOf(LEVELS, cognitive_level) || !oneOf(KINDS, kind) || !oneOf(ENTRY_STATUSES, status)) {
    return null;
  }
  const node = typeof node_id === 'string' && NODE_ID.test(node_id) ? node_id : null;
  return { entry_id, node_id: node, cognitive_level, kind, status };
}

/** 列表：每条只取这些字段 */
export function pickRetestQueue(raw: unknown): RetestEntryView[] | null {
  if (!isRecord(raw) || !Array.isArray(raw['entries']) || raw['entries'].length > 200) return null;
  const out = raw['entries'].map(pickEntry);
  return out.every((e): e is RetestEntryView => e !== null) ? out : null;
}

export type RetestNextView = { kind: 'ready'; ready: RetestReadyView } | { kind: 'empty' };

/** 取题：一道题（题面只有 format、stem、options、rich）、类别与不透明票据，或这一条暂时没有题 */
export function pickRetestNext(raw: unknown, entry: string): RetestNextView | null {
  if (!isRecord(raw)) return null;
  if (raw['status'] === 'unavailable') return { kind: 'empty' };
  if (raw['status'] !== 'ready') return null;
  const { entry_id, kind, item, ticket } = raw;
  if (entry_id !== entry || !oneOf(KINDS, kind) || typeof ticket !== 'string' || ticket.length > 2400 || !TICKET.test(ticket) || !isRecord(item)) return null;
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
  let rich: RichItem | undefined;
  if (item['rich'] !== undefined) {
    rich = pickRich(item['rich'], (opts ?? []).map((o) => o.key)) ?? undefined;
  }
  return { kind: 'ready', ready: { entry_id: entry, kind, ticket,
                                   item: { format, stem, ...(opts && opts.length > 0 ? { options: opts } : {}), ...(rich ? { rich } : {}) } } };
}

/** 作答结果：判分结论（契约枚举）与条目新状态（due / done）；待复核时不给结论 */
export function pickRetestAnswer(raw: unknown, entry: string): RetestAnswerView | null {
  if (!isRecord(raw) || !oneOf(VERDICTS, raw['verdict']) || !isRecord(raw['entry'])) return null;
  if (raw['pending_review'] !== undefined && raw['pending_review'] !== true) return null;
  const { entry_id, status } = raw['entry'];
  if (entry_id !== entry || !oneOf(AFTER_STATUSES, status)) return null;
  const pending = raw['pending_review'] === true || raw['verdict'] === 'needs_review';
  const verdict = pending ? null : (raw['verdict'] as RetestAnswerView['verdict']);
  return { verdict, pending_review: pending, entry: { entry_id, status } };
}

/** BFF 的失败 → 页面错误编码：与错题再练同一套（RETEST_NOT_DUE 对应再练的 PRACTICE_NOT_DUE） */
export function retestUiError(r: Extract<RetestApi<unknown>, { ok: false }>, op: 'next' | 'answer'): UiError {
  if (r.status === 409 && r.code === 'RETEST_NOT_DUE') return 'not_due';
  return practiceUiError(r, op);
}
