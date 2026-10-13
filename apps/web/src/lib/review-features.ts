// 审核台 KGE-06 封测周修订支持的前端开关（specs/kge06-weekly-revision；做法同 src/monthly-plan/features.ts）。
// 默认关闭：关闭时导航不出现入口、两个页面一律 404（页面在读会话之前判断），不发任何请求——与开关打开前零差异。
// 后端是同名功能（services/learning/review_console/config.py 的 REVIEW_CONSOLE_FEATURES）：关着时接口不注册。前后端的开关由部署环境各自设置。
// 打开方式：STARBUDDY_REVIEW_FEATURES=tag_worklist,revision_preflight；只能在 dev / test 设置（别的环境设了即报错拒绝，与后端一致）。只在服务端读。
type Env = Record<string, string | undefined>;

export const REVIEW_FEATURES = ['tag_worklist', 'revision_preflight'] as const;
export type ReviewFeature = (typeof REVIEW_FEATURES)[number];

/**
 * 与后端 parse_features 一致：没设（或为空）= 全关；设了但环境不是 dev / test、或有不认识的名字 → 抛错（拒绝运行，开发期显式报错，
 * 不静默忽略拼错或部署错的开关）。
 */
export function reviewFeatures(env: Env = process.env): ReadonlySet<ReviewFeature> {
  const asked = (env['STARBUDDY_REVIEW_FEATURES'] ?? '').split(',').map((s) => s.trim()).filter((s) => s.length > 0);
  if (asked.length === 0) return new Set();
  const run = env['STARBUDDY_ENV'] ?? '';
  if (run !== 'dev' && run !== 'test') throw new Error('STARBUDDY_REVIEW_FEATURES 只能在 dev / test 打开');
  const unknown = asked.filter((s) => !(REVIEW_FEATURES as readonly string[]).includes(s));
  if (unknown.length > 0) throw new Error(`STARBUDDY_REVIEW_FEATURES 有不认识的功能（只认 ${REVIEW_FEATURES.join(', ')}）`);
  return new Set(asked as ReviewFeature[]);
}

export const reviewFeatureEnabled = (f: ReviewFeature, env: Env = process.env): boolean => reviewFeatures(env).has(f);
