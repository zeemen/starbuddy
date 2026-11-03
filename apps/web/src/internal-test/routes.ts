// 问题反馈的固定取值（与 BFF 的 services/bff/src/http/internal-test-routes.ts 逐字一致，tests/internal-test.test.ts 核对）：
// 分类枚举、文本上限、可附的页面路由模板。路由模板只是页面类型（如 /s/[studentId]/today），不含学生编号等任何标识。

export const FEEDBACK_CATEGORIES = ['malfunction', 'wrong_content', 'display', 'slow', 'login', 'suggestion', 'other'] as const;
export type FeedbackCategory = (typeof FEEDBACK_CATEGORIES)[number];
export const isFeedbackCategory = (s: unknown): s is FeedbackCategory =>
  typeof s === 'string' && (FEEDBACK_CATEGORIES as readonly string[]).includes(s);

export const FEEDBACK_MAX_CHARS = 500;

export const FEEDBACK_ROUTES: readonly string[] = [
  '/student', '/s/[studentId]/today', '/s/[studentId]/star-map', '/s/[studentId]/learn', '/s/[studentId]/learn/[subject]',
  '/s/[studentId]/learn/[subject]/practice', '/s/[studentId]/learn/[subject]/retest', '/s/[studentId]/plan', '/s/[studentId]/weekly',
  '/s/[studentId]/disputes', '/s/[studentId]/exams', '/s/[studentId]/why',
  '/family', '/family/[studentId]', '/family/[studentId]/star-map', '/family/[studentId]/time', '/family/[studentId]/weekly',
  '/family/[studentId]/data', '/family/[studentId]/disputes', '/family/[studentId]/exams', '/family/[studentId]/goals',
  '/family/[studentId]/monthly-plan', '/family/[studentId]/why', '/onboarding/[studentId]',
];
export const isFeedbackRoute = (s: unknown): s is string => typeof s === 'string' && FEEDBACK_ROUTES.includes(s);

/**
 * 实际地址 → 路由模板：本学生的编号换成 [studentId]，学科段换成 [subject]，更深的编号段（判断、考试、错题）截掉；
 * 换完不在登记表里就返回 null（不附）。只用于反馈入口的链接参数，不含任何标识。
 */
export function routeTemplateOf(path: string, studentId: string): string | null {
  const segs = path.split('?')[0]!.split('/').filter((x) => x.length > 0);
  const out: string[] = [];
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i]!;
    if (i === 1 && s === studentId && (out[0] === 's' || out[0] === 'family' || out[0] === 'onboarding')) out.push('[studentId]');
    else if (out[0] === 's' && out[2] === 'learn' && i === 3) out.push('[subject]');
    else if (/^[0-9a-f]{8}-/.test(s)) break;
    else out.push(s);
  }
  const t = `/${out.join('/')}`;
  return isFeedbackRoute(t) ? t : null;
}
