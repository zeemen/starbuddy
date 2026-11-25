// “指着元素提问”的提问对象与校验（specs/stage-point-to-ask 第二节第 1–3 条；第一阶段：只做“指”和“问什么”，不做“答”）。
// 提问对象只含 DSL 里的 id、步数与意图编码，不含任何学生数据：指着学生作答槽位时只带槽位 id，槽位内容运行时在内存里，不进提问对象。
// 规则是“只能指已经出现的东西”：白板上此刻有的元素（高亮框不能单独被指）、文档声明过的槽位、已经播放过的步——不能借提问提前看后面的讲解。
// 纯函数，只收校验器产出的 DSL；本模块不联网、不存储。提问对象不是 DSL 序列化格式的一部分，DSL 版本不变（specs/stage-dsl-versioning）。

import { DSL_VERSION, ID_RE } from './dsl.ts';
import { isValidated, type ValidatedStageDsl } from './validate.ts';

/** 四种问题（固定编码，不收自由文字）：前三种交给回答通道（第二阶段），again 在本地重放这一步 */
export const QUESTION_INTENTS = ['why_step', 'what_is', 'where_from', 'again'] as const;
export type QuestionIntent = (typeof QUESTION_INTENTS)[number];
/** 能指的东西：白板元素、槽位（题图、学生作答）、整一步 */
export const ANCHOR_KINDS = ['element', 'slot', 'step'] as const;
export type AnchorKind = (typeof ANCHOR_KINDS)[number];

export const QUESTION_FIELDS = ['dsl_version', 'cursor', 'anchor', 'intent'] as const;
export const ANCHOR_FIELDS = ['kind', 'id'] as const;

export interface StageAnchor { readonly kind: AnchorKind; readonly id: string }
export interface StageQuestion {
  readonly dsl_version: string;
  /** 提问时已执行的步数（1..总步数） */
  readonly cursor: number;
  readonly anchor: StageAnchor;
  readonly intent: QuestionIntent;
}

export type QuestionErrorCode = 'not_object' | 'unknown_field' | 'bad_version' | 'bad_cursor' | 'unknown_anchor' | 'not_visible' | 'bad_intent';
export interface QuestionError { path: string; code: QuestionErrorCode }
export type QuestionResult = { ok: true; question: StageQuestion } | { ok: false; errors: QuestionError[] };

const isPlain = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) && Object.getPrototypeOf(v) === Object.prototype;

/**
 * 前 cursor 步之后、白板上每个可指元素是第几步写上去的（0 起算的步序号）。高亮框不在里面；擦掉的元素不在里面。
 * 元素 id 在整份 DSL 里唯一（校验器保证），所以“清屏后再写”的是新 id、记新的步。
 */
export function originsAt(dsl: ValidatedStageDsl, cursor: number): Map<string, number> {
  const n = Math.max(0, Math.min(dsl.steps.length, Math.trunc(cursor)));
  const alive = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    for (const a of dsl.steps[i]!.actions) {
      switch (a.type) {
        case 'write_text': case 'write_formula': case 'draw_line': case 'draw_shape':
          alive.set(a.id, i);
          break;
        case 'clear':
          if (a.targets === undefined) alive.clear();
          else for (const t of a.targets) alive.delete(t);
          break;
        default:
          break;
      }
    }
  }
  return alive;
}

/** 此刻能指的全部锚点：白板上的元素（按写上去的先后）、声明过的槽位、已播放过的步 */
export function askableAt(dsl: ValidatedStageDsl, cursor: number): StageAnchor[] {
  const n = Math.max(0, Math.min(dsl.steps.length, Math.trunc(cursor)));
  if (n === 0) return [];
  return [
    ...[...originsAt(dsl, n).keys()].map((id) => ({ kind: 'element' as const, id })),
    ...(dsl.slots ?? []).map((s) => ({ kind: 'slot' as const, id: s.id })),
    ...dsl.steps.slice(0, n).map((s) => ({ kind: 'step' as const, id: s.id })),
  ];
}

/** DSL 里出现过的可指元素 id（不论此刻在不在白板上）：用来区分“没有这个东西”和“还没出现 / 已擦掉” */
function everDrawn(dsl: ValidatedStageDsl): Set<string> {
  const ids = new Set<string>();
  for (const st of dsl.steps) {
    for (const a of st.actions) {
      if (a.type === 'write_text' || a.type === 'write_formula' || a.type === 'draw_line' || a.type === 'draw_shape') ids.add(a.id);
    }
  }
  return ids;
}

/** 校验一个提问对象（来自白板选取与菜单，也可能来自别处）。通过时返回冻结的新对象，不保留调用方的引用 */
export function validateStageQuestion(dsl: ValidatedStageDsl, raw: unknown): QuestionResult {
  if (!isValidated(dsl)) throw new TypeError('validateStageQuestion 只收校验器产出的 DSL');
  const errors: QuestionError[] = [];
  if (!isPlain(raw)) return { ok: false, errors: [{ path: '$', code: 'not_object' }] };
  for (const k of Object.keys(raw)) if (!(QUESTION_FIELDS as readonly string[]).includes(k)) errors.push({ path: `$.${k}`, code: 'unknown_field' });
  if (raw['dsl_version'] !== DSL_VERSION) errors.push({ path: '$.dsl_version', code: 'bad_version' });
  const cursor = raw['cursor'];
  const total = dsl.steps.length;
  const cursorOk = typeof cursor === 'number' && Number.isInteger(cursor) && cursor >= 1 && cursor <= total;
  if (!cursorOk) errors.push({ path: '$.cursor', code: 'bad_cursor' });
  const intent = raw['intent'];
  if (!(typeof intent === 'string' && (QUESTION_INTENTS as readonly string[]).includes(intent))) errors.push({ path: '$.intent', code: 'bad_intent' });
  const anchor = raw['anchor'];
  let kind: AnchorKind | null = null;
  let id: string | null = null;
  if (!isPlain(anchor)) {
    errors.push({ path: '$.anchor', code: 'unknown_anchor' });
  } else {
    for (const k of Object.keys(anchor)) if (!(ANCHOR_FIELDS as readonly string[]).includes(k)) errors.push({ path: `$.anchor.${k}`, code: 'unknown_field' });
    const k = anchor['kind'];
    const v = anchor['id'];
    if (typeof k === 'string' && (ANCHOR_KINDS as readonly string[]).includes(k) && typeof v === 'string' && ID_RE.test(v)) {
      kind = k as AnchorKind;
      id = v;
    } else {
      errors.push({ path: '$.anchor', code: 'unknown_anchor' });
    }
  }
  if (kind !== null && id !== null) {
    if (kind === 'element') {
      if (!everDrawn(dsl).has(id)) errors.push({ path: '$.anchor.id', code: 'unknown_anchor' });   // 高亮框、不存在的 id
      else if (cursorOk && !originsAt(dsl, cursor as number).has(id)) errors.push({ path: '$.anchor.id', code: 'not_visible' });
    } else if (kind === 'slot') {
      if (!(dsl.slots ?? []).some((s) => s.id === id)) errors.push({ path: '$.anchor.id', code: 'unknown_anchor' });
    } else {
      const idx = dsl.steps.findIndex((s) => s.id === id);
      if (idx < 0) errors.push({ path: '$.anchor.id', code: 'unknown_anchor' });
      else if (cursorOk && idx >= (cursor as number)) errors.push({ path: '$.anchor.id', code: 'not_visible' });
    }
  }
  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    question: Object.freeze({
      dsl_version: DSL_VERSION, cursor: cursor as number, anchor: Object.freeze({ kind: kind!, id: id! }), intent: intent as QuestionIntent,
    }),
  };
}

/**
 * “再讲一遍”跳到哪里：返回播放器要 seek 到的步数（= 该步之前已执行的步数），调用方 seek 后再 play，就从这一步的开头重放。
 * 元素 → 它写上去的那一步；整一步 → 那一步；槽位（不属于哪一步）→ 当前这一步。
 */
export function replayCursor(dsl: ValidatedStageDsl, q: StageQuestion): number {
  if (q.anchor.kind === 'element') return originsAt(dsl, q.cursor).get(q.anchor.id) ?? q.cursor - 1;
  if (q.anchor.kind === 'step') {
    const idx = dsl.steps.findIndex((s) => s.id === q.anchor.id);
    return idx >= 0 ? idx : q.cursor - 1;
  }
  return q.cursor - 1;
}
