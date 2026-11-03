// 反馈表单 → 请求体（只做格式检查；授权与库里的取值约束由 BFF 与数据库判断）。不合格返回 null（提示 INVALID，不发请求）。
// 字数按 Unicode 码位计数（与 BFF、库里 char_length 一致）；不收控制字符与零宽、方向覆盖等格式字符（BFF 与库里同口径再查一次）。
import type { FeedbackInput } from './api.ts';
import { FEEDBACK_MAX_CHARS, isFeedbackCategory, isFeedbackRoute } from './routes.ts';

const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
const ALLOWED_CONTROLS = /[\t\n\r]/g;

export function feedbackFromForm(f: FormData): FeedbackInput | null {
  const category = f.get('category');
  const raw = f.get('body');
  const route = f.get('attach_route') === 'on' ? f.get('route_template') : null;
  if (!isFeedbackCategory(category) || typeof raw !== 'string') return null;
  const body = raw.replace(/\r\n/g, '\n');
  if (body.trim() === '' || [...body].length > FEEDBACK_MAX_CHARS || FORBIDDEN.test(body.replace(ALLOWED_CONTROLS, ''))) return null;
  if (route !== null && !isFeedbackRoute(route)) return null;
  return { category, body, route_template: route };
}
