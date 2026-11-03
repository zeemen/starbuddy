// 月度计划的 BFF 部分（specs/family-goals-monthly-plan/step3-spec.md 范围 1，PLT-14 第 3 步；快照由洞察服务生成，迁移 0054 草稿）：
//   - 开关 STARBUDDY_MONTHLY_PLAN=monthly_plan：只在 dev / test 生效；其余环境设了就拒绝启动（main.ts）；关时两条路由不注册；
//   - 洞察服务生成接口的客户端（services/insight/openapi/monthly-plan-internal.v0.yaml）：caller = bff，带 actor = 当前家长账号，
//     洞察服务在库内再核（0044 insight.verify_actor）；不计量（自动月报不计次）；
//   - 模板库读取：与洞察服务同一份文件 services/insight/templates/monthly_plan.v0.yaml（BFF 的制品要带上它，lessons 登记）；
//     按固定写法逐行解析（不引入 YAML 依赖），规则与洞察服务 monthly.parse_templates 一致：未签字只收与模板键一致的 {{TODO_COPY:…}} 占位、
//     只允许 dev / test 加载；文案本身过禁用推断词表与“名次 / 概率 / 承诺”词表；
//   - 渲染（纯函数，不连库）：快照只有模板键与变量，BFF 把它们渲染成文字。占位文案（未签字）渲染为“占位 + ｜变量值…”，签字文案按 {变量名} 填空；
//     成品句子再过两张词表，命中的那一条不出、只记条数（不记内容）。关于学生的句子（优先知识点、上月点亮、分差）带 claim_id，
//     所引判断的模板须在对外白名单里（与周报、单条解释同一口径），否则不出；
//   - 版本核对：模板库指纹 = 原文字节的 SHA-256（与洞察服务同一算法）；两边加载时都与钉死文件 monthly_plan.v0.lock（版本号 + 指纹）核对，
//     不一致拒绝加载——所以同一个 template_version 只对应一份内容。家长视图带 template_version，须与 BFF 读到的一致，不一致整份不可用；
//     0054 的视图不返回快照里记的指纹（input_refs 不出库），学生视图连版本号都没有（已知缺口，待迁移补上），学生端只能核模板键；
//   - 签字文案的 {变量} 集合须恰好等于声明的 vars，注意事项的变量只能是计划事实（与洞察服务同一规则），不一致拒绝加载。
// 本模块不产生任何关于学生的陈述：文字只来自模板库，关于学生的句子只在快照引用了“成立 / 假设”判断时出现（规则 11）；
// 不渲染计划里的先后（position）、不渲染成绩与目标分（分差只有 5 分一档的区间），不出名次、概率、承诺（spec 硬约束）。
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { isLocalEnv, type RunEnv } from '../http/config.ts';
import { templateRef } from '../shell/statements.ts';
import { readCapped } from '../trust/report.ts';
import { forbiddenHits, type Lexicon } from '../trust/lexicon.ts';

export const MONTHLY_PLAN_FEATURE = 'monthly_plan';

/** 解析开关；非 dev / test 设了直接抛错（调用方拒绝启动），没设则关闭 */
export function parseMonthlyPlanFeature(env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (!isLocalEnv(env)) throw new Error('STARBUDDY_MONTHLY_PLAN 只在 dev / test 可用（迁移 0054 演练与文案定稿之前 staging 以上一律关闭）');
  if (raw.trim() !== MONTHLY_PLAN_FEATURE) throw new Error(`STARBUDDY_MONTHLY_PLAN 只认 ${MONTHLY_PLAN_FEATURE}`);
  return true;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MONTH = /^20[2-9][0-9]-(0[1-9]|1[0-2])$/;

// ---------------------------------------------------------------- 洞察服务生成接口

/** ready：已生成或本月已有（200）；not_available：洞察服务统一的“不可用”回答（404 CONTENT_VERSION_NOT_FOUND：actor 再核不通过、档案不存在
 *  或已删除、没有权益——BFF 已先确认有效绑定家长，所以这里按“没有权益”处理）；unavailable：其余（不可达、超时、409、5xx、形状不对） */
export type GenerateOutcome = 'ready' | 'not_available' | 'unavailable';

export interface MonthlyPlanClient {
  generate(studentRef: string, month: string, actorRef: string): Promise<GenerateOutcome>;
}

export interface HttpMonthlyPlanOptions { baseUrl: string; token: string; timeoutMs?: number; fetchImpl?: typeof fetch }
/** 生成一次的时限（读输入、生成、落库都在洞察服务一侧；只在本月没有快照时调一次） */
export const GENERATE_TIMEOUT_MS = 10_000;
const MAX_BYTES = 16_384;

export function httpMonthlyPlanClient(opts: HttpMonthlyPlanOptions): MonthlyPlanClient {
  const doFetch = opts.fetchImpl ?? fetch;
  const url = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/monthly-plan/generate`;
  return {
    async generate(studentRef, month, actorRef) {
      if (!UUID.test(studentRef) || !UUID.test(actorRef) || !MONTH.test(month)) return 'unavailable';
      try {
        const res = await doFetch(url, {
          method: 'POST',
          headers: { authorization: `Bearer ${opts.token}`, 'content-type': 'application/json' },
          body: JSON.stringify({ student_id: studentRef, month, actor: { role: 'guardian', actor_ref: actorRef } }),
          signal: AbortSignal.timeout(opts.timeoutMs ?? GENERATE_TIMEOUT_MS),
          redirect: 'error',
        });
        const raw = await readCapped(res, MAX_BYTES);
        if (raw === null) return 'unavailable';
        const body = JSON.parse(raw) as Record<string, unknown>;
        if (res.status === 404) return body?.['code'] === 'CONTENT_VERSION_NOT_FOUND' ? 'not_available' : 'unavailable';
        if (res.status !== 200) return 'unavailable';
        const ok = (body['status'] === 'generated' || body['status'] === 'exists') && typeof body['plan_id'] === 'string'
          && UUID.test(body['plan_id']) && body['month'] === month;
        return ok ? 'ready' : 'unavailable';
      } catch {
        return 'unavailable';  // 超时、断连、非 JSON：不记消息（可能带出数据）
      }
    },
  };
}

// ---------------------------------------------------------------- 模板库

export const TEMPLATE_PATH = new URL('../../../insight/templates/monthly_plan.v0.yaml', import.meta.url);
export const TEMPLATE_LOCK_PATH = new URL('../../../insight/templates/monthly_plan.v0.lock', import.meta.url);
/** 注意事项模板可用的变量：计划事实（与洞察服务 monthly.NOTE_FACTS 一致；tests/monthly-plan-units.test.ts 核对两边相同） */
export const NOTE_FACTS = ['retest_count', 'sessions_per_week', 'minutes_per_session', 'exam_in_month', 'exam_count', 'mini_review_count',
                           'priority_count'] as const;
const SLOT = /\{([a-z][a-z0-9_]*)\}/g;
const TEMPLATE_FORMAT = 'sie-monthly-plan-templates/0.1';
const VERSION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$/;
const KEY = /^[a-z][a-z0-9_]{1,47}$/;
const PLACEHOLDER = /^\{\{TODO_COPY:(monthly_plan\.[a-z_]+\.[a-z_]+)\}\}$/;
/** 固定位置的模板（与洞察服务 monthly.COPY、0054 的形状校验一致） */
export const FIXED: Record<'student' | 'review', readonly string[]> = {
  student: ['priority_node', 'weekly_rhythm', 'retests', 'mini_review'],
  review: ['last_month', 'lit', 'score_gap'],
};

export interface Template { copy: string; vars: readonly string[] }
export interface TemplateLibrary {
  version: string;
  signed: boolean;
  /** 模板库原文字节的 SHA-256（十六进制 64 位；与洞察服务 monthly.template_fingerprint 同一算法），加载时与钉死文件核对 */
  fingerprint: string;
  /** 模板键（monthly_plan.student.priority_node、monthly_plan.guardian.<key> …）→ 文案与变量 */
  byKey: ReadonlyMap<string, Template>;
}

const scalar = (raw: string): string => {
  const v = raw.trim();
  return v.startsWith('"') ? (JSON.parse(v) as string) : v;
};
const list = (raw: string): string[] => {
  const m = /^\[(.*)\]$/.exec(raw.trim());
  if (!m) throw new Error('模板库的 vars 必须是 [a, b] 写法');
  return m[1]!.split(',').map((s) => s.trim()).filter((s) => s !== '');
};
const need = (ok: boolean, msg: string): void => {
  if (!ok) throw new Error(msg);
};

/**
 * 按固定写法解析模板库（不认识的写法一律拒绝）。signed = status: signed 且有签字人与时间、不是合成；未签字只允许 dev / test。
 * clean：文案过两张词表（命中即拒绝加载）。
 */
export function parseTemplates(raw: Buffer | string, env: RunEnv, clean: (t: string) => boolean): TemplateLibrary {
  const bytes = typeof raw === 'string' ? Buffer.from(raw, 'utf8') : raw;
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  const top: Record<string, string> = {};
  const fixed = new Map<string, { copy?: string; vars?: string[] }>();
  const notes: { key: string; copy?: string; vars?: string[] }[] = [];
  let section: '' | 'student' | 'review' | 'guardian' = '';
  let current: { copy?: string; vars?: string[] } | null = null;
  for (const line of text.split('\n')) {
    if (/^\s*(#.*)?$/.test(line)) continue;
    let m = /^([a-z_]+):\s*(.*)$/.exec(line);
    if (m) {
      current = null;
      if (m[1] === 'student' || m[1] === 'review' || m[1] === 'guardian') {
        need(m[2]!.trim() === '', `模板库 ${m[1]} 必须是分节`);
        section = m[1];
      } else {
        section = '';
        top[m[1]!] = scalar(m[2]!);
      }
      continue;
    }
    if (section === 'student' || section === 'review') {
      m = /^ {2}([a-z_]+):\s*$/.exec(line);
      if (m) {
        const k = `monthly_plan.${section}.${m[1]}`;
        need(FIXED[section].includes(m[1]!) && !fixed.has(k), `模板库 ${section}.${m[1]} 不认识或重复`);
        current = {};
        fixed.set(k, current);
        continue;
      }
    } else if (section === 'guardian') {
      m = /^ {2}- key:\s*(\S+)\s*$/.exec(line);
      if (m) {
        const n: { key: string; copy?: string; vars?: string[] } = { key: scalar(m[1]!) };
        notes.push(n);
        current = n;
        continue;
      }
      if (/^ {4}when:/.test(line) && current) continue;  // 选取条件只在洞察服务一侧用
    }
    m = /^ {4}(copy|vars):\s*(.+)$/.exec(line);
    if (m && current) {
      need(current[m[1] as 'copy' | 'vars'] === undefined, '模板库同一条模板的 copy / vars 重复');
      if (m[1] === 'copy') current.copy = scalar(m[2]!);
      else current.vars = list(m[2]!);
      continue;
    }
    throw new Error('模板库有不认识的行');
  }
  need(top['format'] === TEMPLATE_FORMAT, `模板库 format 必须是 ${TEMPLATE_FORMAT}`);
  const version = top['template_version'] ?? '';
  need(VERSION.test(version), '模板库 template_version 格式不对');
  need(top['status'] === 'draft' || top['status'] === 'signed', '模板库 status 只能是 draft / signed');
  need(top['synthetic'] === 'true' || top['synthetic'] === 'false', '模板库 synthetic 必须是布尔值');
  const signed = top['status'] === 'signed' && (top['signed_by'] ?? '').trim() !== '' && (top['signed_at'] ?? '').trim() !== ''
    && top['synthetic'] === 'false';
  if (!signed && !isLocalEnv(env)) throw new Error('未签字（含合成占位）的月度计划模板库只允许 dev / test 加载（文案人写人审）');
  const byKey = new Map<string, Template>();
  const add = (key: string, t: { copy?: string; vars?: string[] }): void => {
    need(typeof t.copy === 'string' && Array.isArray(t.vars), `模板 ${key} 必须恰好有 copy 与 vars`);
    need(t.vars!.length <= 8 && t.vars!.every((v) => KEY.test(v)) && new Set(t.vars).size === t.vars!.length, `模板 ${key} 的 vars 不对`);
    const ph = PLACEHOLDER.exec(t.copy!);
    if (ph) need(ph[1] === key, `模板 ${key} 的占位键不一致`);
    else {
      need(signed && t.copy!.trim().length >= 1 && [...t.copy!].length <= 120 && !t.copy!.includes('{{'), `模板 ${key}：未签字的模板库只收占位`);
      // 签字文案里的 {变量} 集合必须恰好等于声明的 vars，除此之外不含花括号（与洞察服务 monthly._copy_ok 同一规则）
      const slots = new Set([...t.copy!.matchAll(SLOT)].map((x) => x[1]!));
      need(slots.size === t.vars!.length && t.vars!.every((v) => slots.has(v)) && !/[{}]/.test(t.copy!.replace(SLOT, '')),
           `模板 ${key}：签字文案里的 {变量} 必须恰好是声明的 vars`);
    }
    need(clean(t.copy!), `模板 ${key} 的文案命中词表`);
    byKey.set(key, { copy: t.copy!, vars: t.vars! });
  };
  for (const sec of ['student', 'review'] as const) {
    for (const n of FIXED[sec]) {
      const t = fixed.get(`monthly_plan.${sec}.${n}`);
      need(t !== undefined, `模板库缺 ${sec}.${n}`);
      add(`monthly_plan.${sec}.${n}`, t!);
    }
  }
  need(notes.length >= 1 && notes.length <= 12, '模板库 guardian 必须是 1–12 条');
  for (const n of notes) {
    need(KEY.test(n.key), '注意事项模板的 key 格式不对');
    need(!byKey.has(`monthly_plan.guardian.${n.key}`), '注意事项模板的 key 重复');
    // 注意事项的变量只能是计划事实（与洞察服务 monthly.NOTE_FACTS 同一份白名单）
    need(Array.isArray(n.vars) && n.vars.every((v) => (NOTE_FACTS as readonly string[]).includes(v)), `注意事项模板 ${n.key} 的变量只能是计划事实`);
    add(`monthly_plan.guardian.${n.key}`, n);
  }
  const fingerprint = createHash('sha256').update(bytes).digest('hex');
  return { version, signed, fingerprint, byKey };
}

/** 钉死文件（services/insight/templates/monthly_plan.v0.lock）：恰好 template_version 与 sha256（可带 $comment），与读到的模板库逐项一致，
 *  否则拒绝加载（与洞察服务 monthly.check_lock 同一规则）——内容改了而版本号或钉死文件没跟着改都加载不了 */
export function checkLock(lib: TemplateLibrary, lockText: string): TemplateLibrary {
  const lock = JSON.parse(lockText) as unknown;
  need(typeof lock === 'object' && lock !== null && !Array.isArray(lock), '模板库钉死文件不是对象');
  const keys = Object.keys(lock as object).filter((k) => k !== '$comment').sort();
  need(keys.join(',') === 'sha256,template_version', '模板库钉死文件只能有 template_version 与 sha256');
  const l = lock as Record<string, unknown>;
  need(l['template_version'] === lib.version && l['sha256'] === lib.fingerprint, '模板库与钉死文件不一致');
  return lib;
}

/** 跨语言一致性核对用：与洞察服务 monthly.export_library 同形（tests/monthly-plan-units.test.ts 比对同一份夹具） */
export function exportLibrary(lib: TemplateLibrary): Record<string, unknown> {
  const templates = Object.fromEntries([...lib.byKey.entries()].sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([k, t]) => [k, { copy: t.copy, vars: [...t.vars] }]));
  return { template_version: lib.version, signed: lib.signed, fingerprint: lib.fingerprint, templates };
}

const cachedLibs = new Map<RunEnv, TemplateLibrary>();
/** 读模板库（按环境缓存）并核对钉死文件；读不到、写法不对、命中词表、与钉死文件不一致时抛错，调用方按不可用处理（fail closed） */
export function loadTemplates(env: RunEnv, clean: (t: string) => boolean): TemplateLibrary {
  let lib = cachedLibs.get(env);
  if (!lib) {
    lib = checkLock(parseTemplates(readFileSync(TEMPLATE_PATH), env, clean), readFileSync(TEMPLATE_LOCK_PATH, 'utf8'));
    cachedLibs.set(env, lib);
  }
  return lib;
}

/** 两张词表都过才算干净（任何一张读不到由调用方在拿到词表之前处理） */
export const cleanWith = (forbidden: Lexicon, noPromise: Lexicon) => (text: string): boolean =>
  forbiddenHits(text, forbidden).length === 0 && forbiddenHits(text, noPromise).length === 0;

/**
 * 渲染一条模板：未签字的占位渲染为“占位｜值1｜值2…”（按模板声明的变量顺序）；签字文案把 {变量名} 换成值（必须恰好用到声明的变量，
 * 不能留下花括号）。模板不存在、变量缺值返回 null（那一条不出）。
 */
export function renderCopy(lib: TemplateLibrary, copyKey: string, values: Readonly<Record<string, string>>): string | null {
  const t = lib.byKey.get(copyKey);
  if (!t) return null;
  if (!t.vars.every((v) => typeof values[v] === 'string' && values[v] !== '')) return null;
  if (PLACEHOLDER.test(t.copy)) return t.copy + t.vars.map((v) => `｜${values[v]}`).join('');
  let out = t.copy;
  for (const v of t.vars) out = out.split(`{${v}}`).join(values[v]!);
  return /[{}]/.test(out) ? null : out;
}

// ---------------------------------------------------------------- 快照（视图）→ 前端

const SUBJECTS: readonly string[] = ['aya', 'zhixing', 'wenli', 'echo'];
const NODE = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const LEVELS: readonly string[] = ['know', 'understand', 'apply', 'synthesize'];
const TEMPLATE_KEY = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$/;
const DAY = /^20[2-9][0-9]-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/;
const EXAM_TYPES: readonly string[] = ['monthly', 'midterm', 'final', 'mock1', 'mock2', 'other'];
/** 注意事项变量名（与 0054 insight.valid_monthly_notes 同一口径） */
const VAR_NAME = /^[a-z][a-z0-9_]{0,31}$/;
const STAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;

type Obj = Record<string, unknown>;
const obj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const int = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;
const isSubject = (v: unknown): v is string => typeof v === 'string' && SUBJECTS.includes(v);
const isDay = (v: unknown): v is string => typeof v === 'string' && DAY.test(v);

export interface RenderCtx {
  lib: TemplateLibrary;
  /** 成品句子过两张词表 */
  clean: (text: string) => boolean;
  /** 对外模板白名单（“键@版本”）：关于学生的句子所引判断的模板必须在里面 */
  external: ReadonlySet<string>;
}

/** 渲染计数，只记条数不记内容：dropped = 丢掉的全部条数（形状不对、模板不在、变量缺值、超长、所引判断模板不在白名单、命中词表）；
 *  lexicon_hits = 其中因成品句子命中两张词表之一而丢掉的条数 */
export interface Tally { dropped: number; lexicon_hits: number }
export const newTally = (): Tally => ({ dropped: 0, lexicon_hits: 0 });
/** 成品句子的长度上限（码位数；与 openapi/monthly-plan.v0.yaml 的 maxLength 一致）：超长的那一条不出，不截断 */
export const MAX_TEXT = 400;

/** 成品句子的最后两道：超长丢弃（不截断）、过两张词表（命中另记 lexicon_hits） */
function finish(ctx: RenderCtx, t: Tally, text: string | null): string | null {
  if (text === null || [...text].length > MAX_TEXT) {
    t.dropped++;
    return null;
  }
  if (!ctx.clean(text)) {
    t.dropped++;
    t.lexicon_hits++;
    return null;
  }
  return text;
}

export interface ClaimLine { claim_id: string; subject: string; text: string }
export interface StudentPlanOut {
  priority_nodes: ClaimLine[];
  weekly_rhythm: { sessions_per_week: number; minutes_per_session: number; text: string } | null;
  retests: { count: number; dates: string[]; by_subject: { subject: string; count: number }[]; text: string } | null;
  mini_reviews: { subject: string; date: string; text: string }[];
}
export type GapBand = { reached: true } | { reached: false; low: number; high: number };
export interface GuardianPlanOut {
  generated_at: string;
  student_plan: StudentPlanOut;
  guardian_notes: { text: string }[];
  review: {
    last_month: { month: string; planned_nodes: number; text: string; lit: ClaimLine[] } | null;
    score_gaps: { claim_id: string; subject: string; exam_type: string; exam_date: string; gap_band: GapBand; text: string }[];
  };
}

/** 一条关于学生的句子：判断引用合格、模板在白名单、文字过两张词表才出 */
function claimLine(ctx: RenderCtx, t: Tally, e: unknown, copyKey: string, values: (e: Obj) => Record<string, string> | null): ClaimLine | null {
  if (!obj(e) || typeof e['claim_id'] !== 'string' || !UUID.test(e['claim_id']) || !isSubject(e['subject'])
      || typeof e['template_key'] !== 'string' || !TEMPLATE_KEY.test(e['template_key']) || !int(e['template_version'], 1, 99999)
      || e['copy_key'] !== copyKey || !ctx.external.has(templateRef(e['template_key'], e['template_version']))) {
    t.dropped++;
    return null;
  }
  const v = values(e);
  const text = finish(ctx, t, v === null ? null : renderCopy(ctx.lib, copyKey, v));
  return text === null ? null : { claim_id: e['claim_id'], subject: e['subject'], text };
}

/** 一条计划事实的句子（不关于学生“是什么”，不带判断）：模板在、变量齐、不超长、过两张词表才出 */
function factText(ctx: RenderCtx, t: Tally, copyKey: string, values: Record<string, string> | null): string | null {
  return finish(ctx, t, values === null ? null : renderCopy(ctx.lib, copyKey, values));
}

const arr = (v: unknown, max: number): unknown[] | null => (Array.isArray(v) && v.length <= max ? v : null);

/** 学习计划（学生与家长都看得到）：优先知识点（不渲染 position，只按数组顺序）、每周节奏、本月复测、分科小复评。整段形状不对返回 null */
export function renderStudentPlan(raw: unknown, ctx: RenderCtx, t: Tally): StudentPlanOut | null {
  if (!obj(raw)) return null;
  const nodes = arr(raw['priority_nodes'], 20);
  const minis = arr(raw['mini_reviews'], 8);
  const w = raw['weekly_rhythm'];
  const r = raw['retests'];
  if (nodes === null || minis === null || !obj(w) || !obj(r)) return null;
  const priority_nodes = nodes.map((e) => claimLine(ctx, t, e, 'monthly_plan.student.priority_node', (x) =>
    typeof x['node_id'] === 'string' && NODE.test(x['node_id']) && typeof x['cognitive_level'] === 'string' && LEVELS.includes(x['cognitive_level'])
      ? { subject: x['subject'] as string, node_id: x['node_id'], cognitive_level: x['cognitive_level'] } : null))
    .filter((x): x is ClaimLine => x !== null);
  let weekly_rhythm: StudentPlanOut['weekly_rhythm'] = null;
  if (w['copy_key'] === 'monthly_plan.student.weekly_rhythm' && int(w['sessions_per_week'], 1, 7) && int(w['minutes_per_session'], 1, 120)) {
    const text = factText(ctx, t, 'monthly_plan.student.weekly_rhythm',
                          { sessions_per_week: String(w['sessions_per_week']), minutes_per_session: String(w['minutes_per_session']) });
    if (text !== null) weekly_rhythm = { sessions_per_week: w['sessions_per_week'], minutes_per_session: w['minutes_per_session'], text };
  } else {
    t.dropped++;
  }
  let retests: StudentPlanOut['retests'] = null;
  const dates = arr(r['dates'], 31);
  const by = arr(r['by_subject'], 8);
  if (r['copy_key'] === 'monthly_plan.student.retests' && int(r['count'], 0, 999) && dates !== null && dates.every(isDay)
      && by !== null && by.every((x) => obj(x) && isSubject(x['subject']) && int(x['count'], 1, 999))) {
    const text = factText(ctx, t, 'monthly_plan.student.retests', { count: String(r['count']) });
    if (text !== null) {
      retests = { count: r['count'], dates: dates as string[],
                  by_subject: (by as Obj[]).map((x) => ({ subject: x['subject'] as string, count: x['count'] as number })), text };
    }
  } else {
    t.dropped++;
  }
  const mini_reviews: StudentPlanOut['mini_reviews'] = [];
  for (const e of minis) {
    if (!obj(e) || e['copy_key'] !== 'monthly_plan.student.mini_review' || !isSubject(e['subject']) || !isDay(e['date'])) {
      t.dropped++;
      continue;
    }
    const text = factText(ctx, t, 'monthly_plan.student.mini_review', { subject: e['subject'], date: e['date'] });
    if (text !== null) mini_reviews.push({ subject: e['subject'], date: e['date'], text });
  }
  return { priority_nodes, weekly_rhythm, retests, mini_reviews };
}

/** 分差区间（视图读时现算的 5 分一档）：已达到，或 low–high（high = low + 5，非负整数） */
function gapBand(v: unknown): GapBand | null {
  if (!obj(v)) return null;
  if (v['reached'] === true && Object.keys(v).length === 1) return { reached: true };
  if (v['reached'] === false && Object.keys(v).length === 3 && int(v['low'], 0, 1000) && int(v['high'], 5, 1005) && v['high'] === v['low'] + 5 && v['low'] % 5 === 0) {
    return { reached: false, low: v['low'], high: v['high'] };
  }
  return null;
}
const bandText = (b: GapBand): string => (b.reached ? '≤0' : `${b.low}–${b.high}`);

/** 家长视图（0054 insight.monthly_plan_guardian_view）→ 前端。整份形状不对返回 null（不可用） */
export function renderGuardianPlan(raw: unknown, ctx: RenderCtx, t: Tally): GuardianPlanOut | null {
  if (!obj(raw) || typeof raw['generated_at'] !== 'string' || !STAMP.test(raw['generated_at'])) return null;
  const student_plan = renderStudentPlan(raw['student_plan'], ctx, t);
  const notes = arr(raw['guardian_notes'], 3);
  const review = raw['review'];
  if (student_plan === null || notes === null || !obj(review)) return null;
  const guardian_notes: { text: string }[] = [];
  for (const n of notes) {
    if (!obj(n) || typeof n['key'] !== 'string' || !KEY.test(n['key']) || n['copy_key'] !== `monthly_plan.guardian.${n['key']}` || !obj(n['vars'])) {
      t.dropped++;
      continue;
    }
    const vars = n['vars'];
    const values: Record<string, string> = {};
    let ok = Object.keys(vars).length <= 8;
    for (const [k, v] of Object.entries(vars)) {
      if (!VAR_NAME.test(k)) ok = false;
      else if (typeof v === 'boolean') values[k] = v ? '1' : '0';
      else if (int(v, 0, 9999)) values[k] = String(v);
      else ok = false;
    }
    const text = factText(ctx, t, n['copy_key'] as string, ok ? values : null);
    if (text !== null) guardian_notes.push({ text });
  }
  let last_month: GuardianPlanOut['review']['last_month'] = null;
  const lm = review['last_month'];
  if (obj(lm)) {
    const lit = arr(lm['lit'], 20);
    if (lm['copy_key'] === 'monthly_plan.review.last_month' && isDay(lm['month']) && int(lm['planned_nodes'], 0, 20) && lit !== null) {
      const text = factText(ctx, t, 'monthly_plan.review.last_month', { planned_nodes: String(lm['planned_nodes']) });
      if (text !== null) {
        last_month = {
          month: lm['month'].slice(0, 7), planned_nodes: lm['planned_nodes'], text,
          lit: lit.map((e) => claimLine(ctx, t, e, 'monthly_plan.review.lit', (x) =>
            typeof x['node_id'] === 'string' && NODE.test(x['node_id']) ? { subject: x['subject'] as string, node_id: x['node_id'] } : null))
            .filter((x): x is ClaimLine => x !== null),
        };
      }
    } else {
      t.dropped++;
    }
  } else if (lm !== null && lm !== undefined) {
    t.dropped++;
  }
  const gaps = arr(review['score_gaps'], 16);
  if (gaps === null) return null;
  const score_gaps: GuardianPlanOut['review']['score_gaps'] = [];
  for (const e of gaps) {
    const band = obj(e) ? gapBand(e['gap_band']) : null;
    if (!obj(e) || band === null || typeof e['exam_type'] !== 'string' || !EXAM_TYPES.includes(e['exam_type']) || !isDay(e['exam_date'])) {
      t.dropped++;
      continue;
    }
    const line = claimLine(ctx, t, e, 'monthly_plan.review.score_gap', (x) => ({ subject: x['subject'] as string, gap_band: bandText(band) }));
    if (line !== null) score_gaps.push({ ...line, exam_type: e['exam_type'], exam_date: e['exam_date'], gap_band: band });
  }
  return { generated_at: raw['generated_at'], student_plan, guardian_notes, review: { last_month, score_gaps } };
}

/** 学生视图（0054 insight.monthly_plan_student_view：month、generated_at、student_plan）→ 前端。generated_at 用与家长端同一个完整时间戳校验；
 *  整份形状不对返回 null（不可用）。已知缺口：学生视图不带 template_version，这里只能按模板键核对（待迁移补上） */
export function renderStudentView(raw: unknown, ctx: RenderCtx, t: Tally): { generated_at: string; student_plan: StudentPlanOut } | null {
  if (!obj(raw) || typeof raw['generated_at'] !== 'string' || !STAMP.test(raw['generated_at'])) return null;
  const student_plan = renderStudentPlan(raw['student_plan'], ctx, t);
  return student_plan === null ? null : { generated_at: raw['generated_at'], student_plan };
}
