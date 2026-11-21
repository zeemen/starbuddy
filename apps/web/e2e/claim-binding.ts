// 陈述—判断一致性的全站核对（T21，specs/tech-debt-batch2 第一节 A；规则 11“判断先于展示”的端到端证据）。
// 与 dom-leak.ts（内部编码泄露）互补：这里只管“关于学生的句子是不是都挂着账本里一条可展示的判断”。真相来自一次性测试库的判断账本
// （insight.claim，经 psql 只读查询；端到端环境的 PG* 变量由 scripts/e2e.sh 给出），不信页面自己说的状态。
//   1. 页面上每个 data-claim-id 都是账本里这个学生的判断，状态是成立、假设或待验证，且没过期；待验证的只画虚线（ADR 13 待人确认 #2）：
//      元素（或最近的带 data-display 的祖先）若标了画法就必须是 dashed（不能画成亮、半亮等实心星），里面有结论句时必须在 dashed 里；
//      没标画法、也没有结论句的元素放行（例如星图虚线星旁“下一次验证”的日期行）；
//   2. 已推翻（refuted）、已撤回（withdrawn）的判断，以及别的学生的任何判断，ID 不出现在页面 HTML 的任何位置（含链接与属性）；
//   3. 合成判断模板的渲染句（库里的模板一律以“【占位模板”开头，文案文件里没有这个前缀）只出现在带 data-claim-id 的元素里：
//      不被标题、按钮、说明类属性或别的组件不带 ID 地转述；给了“句子 → 判断”的对照（例如库里周报的句子）时，句子必须挂在对应的那条判断上。
import { execFileSync } from 'node:child_process';
import { expect, type Page } from '@playwright/test';

export const STATEMENT_MARK = '【占位模板';
const SHOWN = new Set(['hypothesis', 'established', 'to_verify']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export interface LedgerClaim { id: string; student: string; status: string; live: boolean }

const psql = (q: string): string => execFileSync('psql', ['-X', '-At', '-F', '\t', '-v', 'ON_ERROR_STOP=1', '-c', q], { encoding: 'utf8' }).trim();

/** 账本里的全部判断（一次性测试库只有合成数据，量很小） */
export function ledger(): Map<string, LedgerClaim> {
  const out = new Map<string, LedgerClaim>();
  const raw = psql('SELECT id, student_id, status, (expires_at > now())::text FROM insight.claim ORDER BY id');
  for (const line of raw ? raw.split('\n') : []) {
    const [id, student, status, live] = line.split('\t');
    out.set(id!, { id: id!, student: student!, status: status!, live: live === 'true' });
  }
  return out;
}

/** 库里最近一周周报的句子 → 判断（按受众） */
export function weeklyTexts(student: string, audience: 'student' | 'guardian'): Map<string, string> {
  if (!UUID.test(student)) throw new Error('student 不是 UUID');
  const raw = psql(`SELECT s->>'text', s->>'claim_id' FROM (SELECT sentences FROM insight.weekly_report
                     WHERE student_id = '${student}' AND audience = '${audience}' AND status = 'generated'
                     ORDER BY week_start DESC LIMIT 1) w, jsonb_array_elements(w.sentences) s`);
  return new Map((raw ? raw.split('\n') : []).map((l) => l.split('\t') as [string, string]));
}

interface PageClaims {
  elements: { id: string; display: string | null; statement: boolean }[];
  html: string;
  unbound: string[];                       // 含模板前缀、却不在任何 data-claim-id 元素里的文字或属性
  bound: { text: string; id: string }[];  // 含模板前缀的文字与它最近的 data-claim-id
}

async function collect(page: Page): Promise<PageClaims> {
  return page.evaluate((mark) => {
    const owner = (n: Node | null): Element | null => {
      const el = n instanceof Element ? n : n?.parentElement ?? null;
      return el ? el.closest('[data-claim-id]') : null;
    };
    const elements = [...document.querySelectorAll('[data-claim-id]')].map((el) => ({
      id: el.getAttribute('data-claim-id') ?? '', display: el.closest('[data-display]')?.getAttribute('data-display') ?? null,
      statement: (el.textContent ?? '').includes(mark),
    }));
    // 不看 <script> / <style> / <template> / <noscript> 里的文字（Next.js 的服务端组件载荷在 <script> 里，不是渲染出来的文字；
    // 判断 ID 是否出现由下面的整页 HTML 另查，含这些标签）
    const hidden = (n: Node): boolean => !!n.parentElement?.closest('script, style, template, noscript');
    const unbound: string[] = [];
    const bound: { text: string; id: string }[] = [];
    if (document.title.includes(mark)) unbound.push(`<title>${document.title}`);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = (n.textContent ?? '').trim();
      if (!t.includes(mark) || hidden(n)) continue;
      const o = owner(n);
      if (o) bound.push({ text: t, id: o.getAttribute('data-claim-id') ?? '' });
      else unbound.push(t);
    }
    for (const el of document.querySelectorAll('*')) {
      for (const a of ['title', 'aria-label', 'alt', 'placeholder', 'aria-description', 'content', 'value']) {
        const v = el.getAttribute(a);
        if (v && v.includes(mark) && !owner(el)) unbound.push(`${el.tagName.toLowerCase()}[${a}]=${v}`);
      }
    }
    return { elements, html: document.documentElement.outerHTML, unbound, bound };
  }, STATEMENT_MARK);
}

/**
 * 核对当前页面。student：这个页面属于哪个学生；claims：ledger() 的结果（同一轮爬取共用一份）；
 * expected：可选的“句子 → 判断 ID”对照（给了就要求每句都挂在对应的判断上）。返回页面上出现的判断 ID（调用方可再核对数量）
 */
export async function expectClaimBinding(page: Page, student: string, claims: Map<string, LedgerClaim>,
                                         expected?: Map<string, string>): Promise<string[]> {
  const where = new URL(page.url()).pathname;
  const got = await collect(page);
  for (const e of got.elements) {
    const c = claims.get(e.id);
    expect(c, `${where}：data-claim-id ${e.id} 不在判断账本里`).toBeDefined();
    expect(c!.student, `${where}：判断 ${e.id} 不属于这个学生`).toBe(student);
    expect(SHOWN.has(c!.status) && c!.live, `${where}：判断 ${e.id} 的状态 ${c!.status}${c!.live ? '' : '（已过期）'} 不能展示`).toBe(true);
    if (c!.status === 'to_verify') {
      expect(e.display === null || e.display === 'dashed', `${where}：待验证的判断 ${e.id} 画成了 ${e.display}（只能画虚线）`).toBe(true);
      expect(!e.statement || e.display === 'dashed', `${where}：待验证的判断 ${e.id} 显示了结论句`).toBe(true);
    }
  }
  for (const c of claims.values()) {
    if (c.student === student && SHOWN.has(c.status) && c.live) continue;
    expect(got.html.includes(c.id), `${where}：${c.student === student ? `状态 ${c.status}` : '别的学生'}的判断 ${c.id} 出现在页面里`).toBe(false);
  }
  expect(got.unbound, `${where}：关于学生的句子没有挂判断（不在 data-claim-id 元素里）`).toEqual([]);
  if (expected) {
    for (const b of got.bound) {
      const want = expected.get(b.text);
      if (want !== undefined) expect(b.id, `${where}：句子“${b.text}”挂错了判断`).toBe(want);
    }
  }
  return [...new Set(got.elements.map((e) => e.id))];
}

/** 逐页打开并核对（导航后等网络空闲；状态码由调用方另验） */
export async function crawlClaimBinding(page: Page, student: string, paths: string[], claims: Map<string, LedgerClaim>,
                                        expected?: Map<string, string>): Promise<Set<string>> {
  const seen = new Set<string>();
  for (const p of paths) {
    await page.goto(p);
    await page.waitForLoadState('networkidle');
    for (const id of await expectClaimBinding(page, student, claims, expected)) seen.add(id);
  }
  return seen;
}
