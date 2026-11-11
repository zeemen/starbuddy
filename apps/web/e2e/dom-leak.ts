// 运行时 DOM 的内部编码泄露核对（specs/sie-03-dispute-reconcile/lessons.md 第 10 条登记的后续）：静态扫描只看源码与文案，
// 这里看浏览器里真正渲染出来的页面——body 里的每个文本节点、可见的说明类属性（aria-label、title、alt、placeholder），以及每个 data-* 属性。
//   - 文本：先去掉 {{TODO_COPY:…}} 占位（占位键本身不是泄露），再核对
//       · 明令禁止的编码与内部字段名（异议类型、下一步、偏差方向、调整、删除范围与撤回原因、条目来源；模板键、证据 ID、版本、掌握等字段名），
//         以及调用方另给的桩数据（证据 ID、算法版本等）——任何位置都不许出现，含 data-value 里；
//       · 编码样式：snake_case、UPPER_SNAKE、小写点分的模板键与 @版本、UUID、0–1 之间的小数（掌握数值）——
//         data-value（数字、日期、节点编号、版本、表名，组件有意放的值）里不查；
//       · 大写点分的知识节点编号：只能在 data-value 里，判断句（data-claim-id）、固定文字（data-fixed-text）、题面（data-item-text）除外。
//   - data-* 属性：属性名必须登记在 ATTRS 里，值必须落在登记的枚举、UUID 或样式里；值同样不许含明令禁止的编码。
//     新增 data-* 属性或新的取值要在这里登记（有意为之：逼着评审看一眼它是不是内部编码）。
// 注意：下面以“待人确认”标出的属性目前带的是判断状态、异议状态、置信度分档、判断类别等编码（测试用它们定位元素），
// 它们该不该留在 DOM 里由人决定（lessons 第 10 条）；这里只把现有取值列成白名单，别的编码一律不放行。
import { expect, type Page } from '@playwright/test';
import { FAMILY_RESULT } from '../src/auth/copy.ts';
import { FAMILY_GOALS_RESULT } from '../src/family-goals/copy.ts';
import {
  ADJUSTMENT, CONFIDENCE_BAND, DATA_RESULT, DEVIATION_DIRECTION, DISPUTE_KIND, DISPUTE_NEXT_DOM, DISPUTE_RESULT, DISPUTE_STATUS, ERASURE_CANCEL_REASON, ERASURE_SCOPE,
  ERASURE_STATUS, EXAM_RESULT, EXAM_SUBJECT, FIRST_REPORT_SECTION, PRACTICE_ERROR, RESOURCE_LABEL, SUBJECT_LABEL, TASK_KIND, WEEKLY_SECTION, WEEKLY_SOURCE, WHY_STATUS,
} from '../src/shell/copy.ts';
import { TRUST_FEATURES } from '../src/shell/features.ts';
import { ENTRY_STATUSES, SOURCES, VERDICTS } from '../src/practice/view.ts';
import { ANSWER_FIELD_NAMES } from '../src/lib/rich-item.ts';

const keys = (o: Record<string, string>): string[] => Object.keys(o);
const coded = (xs: string[]): string[] => xs.filter((k) => k.includes('_'));

/** 只能映射成占位文案、不许原样出现在页面任何位置（文本与 data-* 属性）的编码 */
export const FORBIDDEN_CODES: readonly string[] = [...new Set([
  ...keys(DISPUTE_KIND),                                               // 异议类型 i_know / one_off_slip / data_error
  ...keys(DISPUTE_RESULT).filter((k) => k === k.toLowerCase()),        // 下一步 verification_scheduled 等
  ...keys(DEVIATION_DIRECTION),                                        // 偏差方向（含 calculation_slip）
  ...coded(keys(ADJUSTMENT)), ...coded(keys(ERASURE_SCOPE)), ...coded(keys(ERASURE_CANCEL_REASON)),
  ...coded([...SOURCES]),                                              // 再练条目来源 hint_followup
  // 内部字段名：模板、证据、版本、掌握、错因、原因码、复测与同族标记、计量
  'template_key', 'template_version', 'evidence_id', 'source_version', 'model_version', 'human_review_required', 'p_mastery', 'mastery_state',
  'error_attribution', 'error_cause', 'reason_code', 'empty_reason', 'retest_due', 'same_family_only', 'item_ref', 'usage_id', 'idempotency_key',
  // 答案类字段（PLT-12 题面 present_item：答案、解析、采分点、Rubric、提示阶梯、错因陷阱不得进页面；src/lib/rich-item.ts）
  ...ANSWER_FIELD_NAMES,
  // 复测与验证题（specs/retest-delivery-kge05）：作答用途、任务原因与空原因编码不进页面
  'transfer_check', 'verify_self_report', 'verify_in_platform', 'no_new_family', 'no_source_item',
])];

type Rule = readonly string[] | 'uuid' | RegExp;
const KEBAB = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const BOOL = ['true', 'false'];
const FORBIDDEN_SET = new Set(FORBIDDEN_CODES);
/** 已登记的 data-* 属性（不含前缀 data-）及其允许的取值 */
export const ATTRS: Record<string, Rule> = {
  testid: KEBAB,                                                       // 测试定位用：只许 kebab-case（下划线编码进不来）
  'trust-root': ['why', 'disputes', 'exam-plan', 'exam', 'data', 'weekly', 'first-report'],
  'trust-nav': ['student', 'guardian'],
  audience: ['student', 'guardian'],
  'trust-link': TRUST_FEATURES,
  'trust-slot': TRUST_FEATURES,
  'practice-root': ['list', 'item'],
  'retest-root': ['list', 'item'],                                     // 复测与验证题（specs/retest-delivery-kge05）
  'claim-id': 'uuid', 'for-claim': 'uuid', request: 'uuid', 'entry-id': 'uuid',
  value: KEBAB,                                                        // 值的种类名（date、node、days…），值本身在元素文本里
  unavailable: KEBAB,
  'fixed-text': ['notice', 'title', 'connective'],
  hydrated: ['true'],
  marked: ['', 'done', 'skip'],                                        // 今日计划“完成”“先跳过”这次记下的是哪一个（PLT-15 PR2）
  // 待人确认（lessons 第 10 条）：以下带的是判断状态、异议状态、置信度分档、判断来源、周报 / 报告段落（判断类别）编码
  status: [...keys(WHY_STATUS), 'trial', 'trial-after', 'retained', 'ai-off', 'open', 'none', 'curfew', 'cap', 'minutes', 'curfew-near', 'settings'],
  'dispute-status': keys(DISPUTE_STATUS),
  confidence: keys(CONFIDENCE_BAND),
  'claim-source': keys(WEEKLY_SOURCE),
  'weekly-section': keys(WEEKLY_SECTION),
  'report-section': keys(FIRST_REPORT_SECTION),
  'source-type': ['objective', 'self'],                               // 周报：使用记录 objective、学生自己标记的计划栏 self（0057）
  'weekly-objective': [''], 'weekly-self-marked': [''], 'report-verify': [''],
  display: ['solid', 'dashed'],
  // 表单结果码（BFF 错误码与本地结果；异议的下一步编码不放行，只放行它的序号 next-1…3）
  // （家长诉求页的结果码 FAMILY_GOALS_RESULT，PLT-14 第 1 步）
  code: ['', ...new Set([...keys(DISPUTE_RESULT), ...keys(EXAM_RESULT), ...keys(DATA_RESULT), ...keys(FAMILY_RESULT),
                         ...keys(FAMILY_GOALS_RESULT), ...Object.values(DISPUTE_NEXT_DOM)])].filter((k) => !FORBIDDEN_SET.has(k)),
  state: [...keys(ERASURE_STATUS), 'trialing', 'active', 'grace', 'retained', 'none'],
  frozen: BOOL, within: BOOL, 'pending-review': BOOL, retained: BOOL,
  'direction-index': /^[1-4]$/,                                        // 偏差方向只给序号，编码不进 DOM
  subject: [...keys(SUBJECT_LABEL), ...keys(EXAM_SUBJECT)],             // 学科编码与产品命名空间（周报客观栏按 aya 等）
  'item-text': ['stem', 'option'],                                     // 题面与选项（合成）
  kind: keys(TASK_KIND),
  rationale: ['content', 'claim'],
  resource: keys(RESOURCE_LABEL),
  enrollment: ['active', 'paused', 'none'],
  'entry-status': [...ENTRY_STATUSES, 'done'],                          // done：复测条目作答后不再出题
  verdict: ['', ...VERDICTS.filter((v) => v !== 'needs_review')],
  error: ['', ...keys(PRACTICE_ERROR), 'not_found', 'login'],
  phase: ['loading', 'item', 'result', 'empty', 'stopped'],
  format: ['single', 'multi', 'fill'],
};

export interface LeakOptions {
  /** 另外不许出现在页面任何位置的字符串（桩故意带上的证据 ID、算法版本、内部字段等） */
  forbidden?: readonly string[];
}

type WireRule = { name: string; values?: string[]; uuid?: boolean; re?: string };
const wire = (): WireRule[] => Object.entries(ATTRS).map(([name, r]) =>
  r === 'uuid' ? { name, uuid: true } : r instanceof RegExp ? { name, re: r.source } : { name, values: [...r] });

/** 扫描当前页面，返回每一处泄露的说明（空数组表示没有） */
export async function domLeaks(page: Page, opts: LeakOptions = {}): Promise<string[]> {
  return page.evaluate(({ rules, forbidden }) => {
    const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
    const PLACEHOLDER = /\{\{TODO_COPY(?::[^}]*)?\}\}/g;
    const CODE_LIKE: [string, RegExp][] = [
      ['snake_case 编码', /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/],
      ['UPPER_SNAKE 编码', /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/],
      ['模板键 / 点分键', /\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){2,}\b/],
      ['@版本', /@\d+(?:\.\d+)*\b/],
      ['UUID', UUID],
      ['掌握数值样式的小数', /(?<![\d.])[01]\.\d+(?![\d.])/],
    ];
    const NODE_ID: [string, RegExp] = ['知识节点编号（应放在 data-value 里）', /\b[A-Z][A-Z0-9]{1,9}(?:\.[A-Z0-9][A-Z0-9_]*){1,4}\b/];
    const byName = new Map(rules.map((r) => [r.name, r]));
    const out: string[] = [];
    const where = (el: Element): string => {
      const t = el.closest('[data-testid]')?.getAttribute('data-testid');
      return `<${el.tagName.toLowerCase()}>${t ? ` 在 [data-testid=${t}] 内` : ''}`;
    };
    const checkText = (raw: string, el: Element, label: string): void => {
      const t = raw.replace(PLACEHOLDER, ' ');
      for (const f of forbidden) if (t.includes(f)) out.push(`${label} 含禁止编码 ${f}：${raw.trim()}（${where(el)}）`);
      if (el.closest('[data-value]')) return;
      for (const [what, re] of CODE_LIKE) if (re.test(t)) out.push(`${label} 出现${what}：${raw.trim()}（${where(el)}）`);
      if (!el.closest('[data-claim-id], [data-fixed-text], [data-item-text]') && NODE_ID[1].test(t)) out.push(`${label} 出现${NODE_ID[0]}：${raw.trim()}（${where(el)}）`);
    };
    const SKIP = 'script, style, template, noscript';
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const el = n.parentElement;
      const t = n.textContent ?? '';
      if (!el || t.trim() === '' || el.closest(SKIP)) continue;
      checkText(t, el, '文本');
    }
    for (const el of [document.body, ...Array.from(document.body.querySelectorAll('*'))]) {
      if (el.closest(SKIP)) continue;
      for (const a of ['aria-label', 'title', 'alt', 'placeholder']) {
        const v = el.getAttribute(a);
        if (v && v.trim() !== '') checkText(v, el, a);
      }
      for (const name of el.getAttributeNames()) {
        if (!name.startsWith('data-')) continue;
        const v = el.getAttribute(name) ?? '';
        const shown = `${name}="${v}"（${where(el)}）`;
        for (const f of forbidden) if (v.includes(f)) out.push(`属性含禁止编码 ${f}：${shown}`);
        const r = byName.get(name.slice(5));
        if (!r) { out.push(`未登记的属性：${shown}`); continue; }
        const ok = r.uuid ? new RegExp(`^${UUID.source}$`).test(v) : r.re !== undefined ? new RegExp(r.re).test(v) : (r.values ?? []).includes(v);
        if (!ok) out.push(`属性取值不在白名单：${shown}`);
      }
    }
    return [...new Set(out)];
  }, { rules: wire(), forbidden: [...FORBIDDEN_CODES, ...(opts.forbidden ?? [])] });
}

/** 断言当前页面没有内部编码泄露 */
export async function expectNoDomLeak(page: Page, opts: LeakOptions = {}): Promise<void> {
  expect(await domLeaks(page, opts), `页面 ${page.url()} 的运行时 DOM 泄露内部编码`).toEqual([]);
}
