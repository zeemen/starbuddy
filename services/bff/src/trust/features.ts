// 信任界面的功能开关（BFF 侧；与 apps/web/src/shell/features.ts 同一口径，冲刺 5-H spec 第 6 节、4-H spec 第 12 节）。
// SIE-03 PR③ 的读写接口按块挂在开关后面：
//   dispute      异议（提出、查看自己提出的异议与状态）
//   reconcile    考试录入（考试安排、成绩）、预测与对账卡读取
//   weekly       跨科周报 v0 读取（冲刺 5-H：学生版成长手记、家长版周报，0036）
//   data_rights  家长的导出与删除（申请、二次确认、撤回、状态与回执）
//   profile      学情档案页的学生 / 家长视图读取（KGE-04 第二步 PR 4：经 0037 陈述视图读引用、经 insight.explain 取文字；
//                前端展示随 H5 / 信任界面另做，apps/web 的开关表暂未加 profile，见 lessons）
//   why          单条判断的解释读取（SIE-02b 第二步 PR 2：GET …/claims/{claim_id}/explain，经 insight.explain；前端“为什么”页与
//                首份报告演示页的待验证虚线用它）
//   report       首份画像报告读取（SIE-02b 第二步 PR 2：GET …/first-report，经 insight.report；前端只有开发演示页，同名开关）
// 两条都只转发洞察服务的 result（信封外层的置信度、证据 ID、版本不给前端）。
// 默认全部关闭：关闭时这些路由不注册（表外路径，统一的 404），4-H 的“信任界面预留”验收继续成立。
// 打开方式：STARBUDDY_TRUST_UI=dispute,reconcile（逗号分隔），只在 dev / test 生效；其余环境设了就拒绝启动（main.ts），
// 直到实现与文案签字后由人改这里的规则。
// LT-01 决定记录第 1 条：staging 可开上线测试范围内的块（STAGING_TRUST_FEATURES：为什么 / 我不同意、对账卡、周报、授权与删除、首份报告）；
// profile（学情档案页）不在上线测试范围，staging 设了照旧拒绝；pilot、生产仍一律拒绝。
import { isLocalEnv, launchFeatureEnv, type RunEnv } from '../http/config.ts';

export const TRUST_FEATURES = ['why', 'dispute', 'reconcile', 'weekly', 'data_rights', 'profile', 'report'] as const;
export type TrustFeature = (typeof TRUST_FEATURES)[number];
export const STAGING_TRUST_FEATURES: readonly TrustFeature[] = ['why', 'dispute', 'reconcile', 'weekly', 'data_rights', 'report'];

/** 解析开关；dev / test 以外（staging 只收 STAGING_TRUST_FEATURES）设了开关直接抛错（调用方拒绝启动），没设则一律关闭 */
export function parseTrustFeatures(env: RunEnv, raw: string | undefined): ReadonlySet<TrustFeature> {
  if (raw === undefined || raw.trim() === '') return new Set();
  if (!launchFeatureEnv(env)) throw new Error('STARBUDDY_TRUST_UI 只在 dev / test / staging 可用（信任界面签字前 pilot 以上一律关闭）');
  const wanted = raw.split(',').map((s) => s.trim()).filter((s) => s !== '');
  const unknown = wanted.filter((w) => !(TRUST_FEATURES as readonly string[]).includes(w));
  if (unknown.length > 0) throw new Error(`STARBUDDY_TRUST_UI 含不认识的开关：${unknown.join(', ')}`);
  const outside = isLocalEnv(env) ? [] : wanted.filter((w) => !(STAGING_TRUST_FEATURES as readonly string[]).includes(w));
  if (outside.length > 0) throw new Error(`STARBUDDY_TRUST_UI：${outside.join(', ')} 不在上线测试范围，staging 不可打开`);
  return new Set(TRUST_FEATURES.filter((f) => wanted.includes(f)));
}
