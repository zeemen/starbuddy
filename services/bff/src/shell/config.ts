// 统一壳页面与读路由的显示规则（specs/unified-shell-sprint4/spec.md 第 6、8、9、10 节，H4）：config/shell.json。
// 推荐理由的内容类编码、对外模板白名单（画像可见范围，必停项）、每页取陈述的上限与超时、今日计划超时、连续使用提醒、宵禁临近阈值。
// 这里只有显示规则；单日上限、宵禁、已用分钟等计量口径全部来自数据库（platform.meter_guard_status），BFF 不重算。
import { readFileSync } from 'node:fs';

export interface ShellConfig {
  /** 内容类 rationale_code（只描述题目或知识点）：固定短句 */
  contentRationale: ReadonlySet<string>;
  /** 关于学生类：只有有判断时显示判断陈述。两张表都没有的编码不显示理由（宁可不显示） */
  studentRationale: ReadonlySet<string>;
  /** 对外模板白名单：`模板键@版本`（空 = 壳内不显示任何判断陈述） */
  externalTemplates: ReadonlySet<string>;
  /** 关于学生类编码 → 可用的模板（`模板键@版本`）；不在这里的判断即使在白名单里也不作为这个编码的理由（画像可见范围，必停） */
  rationaleTemplates: ReadonlyMap<string, ReadonlySet<string>>;
  /** 每页最多取几条陈述、单条超时、并发上限、整页总时限 */
  explain: { maxPerPage: number; timeoutMs: number; concurrency: number; pageDeadlineMs: number };
  plan: { timeoutMs: number };
  /** H5 星图 v1：调学习服务 star-map/nodes 的单次时限；白名单里哪些模板算“知识掌握”（画星、星数说明）、哪些算“知识迁移”（只用于摘要位）。
   *  两张表都是 externalTemplates 的子集、互不相交（画像可见范围，必停） */
  starMap: { timeoutMs: number; masteryTemplates: ReadonlySet<string>; transferTemplates: ReadonlySet<string> };
  /** singleInstance：提醒计时在进程内（见 rest.ts）；dev / test 以外只有明确声明单实例部署才启用 */
  rest: { continuousMinutes: number; breakMinutes: number; singleInstance: boolean };
  curfewNearMinutes: number;
  /** 计量缺省值的唯一出处（meter_guard_status 不返回；与 0015 一致，见 lessons） */
  meterDefaults: { curfewStart: string; curfewEnd: string; retainedDailyPractice: number };
}

export const SHELL_CONFIG_PATH = new URL('../../config/shell.json', import.meta.url);

const int = (v: unknown, lo: number, hi: number, what: string): number => {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < lo || v > hi) throw new Error(`shell.json：${what} 应为 ${lo}–${hi} 的整数`);
  return v;
};
const CODE = /^[a-z0-9_]{1,48}$/;
const TEMPLATE = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}@[1-9][0-9]{0,4}$/;

export function loadShellConfig(path: URL | string = SHELL_CONFIG_PATH): ShellConfig {
  const raw = JSON.parse(readFileSync(path, 'utf8')) as Record<string, any>;
  const codes = (k: string): string[] => {
    const v = raw['rationale_codes']?.[k];
    if (!Array.isArray(v) || !v.every((c) => typeof c === 'string' && CODE.test(c))) throw new Error(`shell.json：rationale_codes.${k} 应为编码数组`);
    return v as string[];
  };
  const content = codes('content');
  const student = codes('about_student');
  if (content.some((c) => student.includes(c))) throw new Error('shell.json：同一个编码不能既是内容类又是关于学生类');
  const templates = raw['external_claim_templates'];
  if (!Array.isArray(templates) || !templates.every((t) => typeof t === 'string' && TEMPLATE.test(t))) {
    throw new Error('shell.json：external_claim_templates 应为“模板键@版本”数组');
  }
  const cont = int(raw['rest_reminder']?.continuous_minutes, 1, 240, 'rest_reminder.continuous_minutes');
  const rt = raw['rationale_templates'];
  if (!rt || typeof rt !== 'object' || Array.isArray(rt)) throw new Error('shell.json：rationale_templates 应为对象');
  const rationaleTemplates = new Map<string, ReadonlySet<string>>();
  for (const [code, list] of Object.entries(rt as Record<string, unknown>)) {
    if (!student.includes(code)) throw new Error(`shell.json：rationale_templates 的 ${code} 不是关于学生类编码`);
    if (!Array.isArray(list) || !list.every((t) => typeof t === 'string' && TEMPLATE.test(t) && (templates as string[]).includes(t))) {
      throw new Error(`shell.json：rationale_templates.${code} 应为白名单里的“模板键@版本”`);
    }
    rationaleTemplates.set(code, new Set(list as string[]));
  }
  const md = raw['meter_defaults'];
  const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
  if (!md || typeof md.curfew_start !== 'string' || !HHMM.test(md.curfew_start) || typeof md.curfew_end !== 'string' || !HHMM.test(md.curfew_end)) {
    throw new Error('shell.json：meter_defaults 的宵禁应为 HH:MM');
  }
  if (typeof raw['rest_reminder']?.single_instance !== 'boolean') throw new Error('shell.json：rest_reminder.single_instance 应为布尔');
  const sm = raw['star_map'];
  const smList = (k: string): string[] => {
    const v = sm?.[k];
    if (!Array.isArray(v) || !v.every((t) => typeof t === 'string' && TEMPLATE.test(t) && (templates as string[]).includes(t))) {
      throw new Error(`shell.json：star_map.${k} 应为白名单里的“模板键@版本”数组`);
    }
    return v as string[];
  };
  const masteryTemplates = smList('mastery_templates');
  const transferTemplates = smList('transfer_templates');
  if (masteryTemplates.some((t) => transferTemplates.includes(t))) throw new Error('shell.json：同一个模板不能既算知识掌握又算知识迁移');
  return {
    contentRationale: new Set(content),
    studentRationale: new Set(student),
    externalTemplates: new Set(templates as string[]),
    rationaleTemplates,
    explain: { maxPerPage: int(raw['explain']?.max_per_page, 0, 20, 'explain.max_per_page'),
               timeoutMs: int(raw['explain']?.timeout_ms, 100, 10_000, 'explain.timeout_ms'),
               concurrency: int(raw['explain']?.concurrency, 1, 10, 'explain.concurrency'),
               pageDeadlineMs: int(raw['explain']?.page_deadline_ms, 100, 10_000, 'explain.page_deadline_ms') },
    plan: { timeoutMs: int(raw['plan']?.timeout_ms, 100, 30_000, 'plan.timeout_ms') },
    starMap: { timeoutMs: int(sm?.timeout_ms, 100, 10_000, 'star_map.timeout_ms'),
               masteryTemplates: new Set(masteryTemplates), transferTemplates: new Set(transferTemplates) },
    rest: { continuousMinutes: cont, breakMinutes: int(raw['rest_reminder']?.break_minutes, 1, cont, 'rest_reminder.break_minutes'),
            singleInstance: raw['rest_reminder'].single_instance as boolean },
    curfewNearMinutes: int(raw['curfew_near_minutes'], 1, 120, 'curfew_near_minutes'),
    meterDefaults: { curfewStart: md.curfew_start, curfewEnd: md.curfew_end,
                     retainedDailyPractice: int(md.retained_daily_practice, 0, 100, 'meter_defaults.retained_daily_practice') },
  };
}
