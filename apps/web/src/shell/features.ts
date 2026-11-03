// 信任界面的功能开关（spec 第 12 节；冲刺 5-H spec 第 6 节：why / dispute / reconcile / weekly / data_rights）。
// 默认全部关闭：关闭时前端不渲染任何入口元素、不发任何请求，信任界面的页面一律 404（src/shell/guard.ts 的 requireTrust，
// 在读会话之前判断）。BFF 侧是另一个同名开关（services/bff/src/trust/features.ts，只认 dispute / reconcile / data_rights）：
// 开关关时 BFF 不注册对应路由（与表外路径同一个 404）；why、weekly 在 BFF 没有新路由（“为什么”的证据链读取接口待补，
// specs/trust-ui-sprint5/lessons.md）。前后端的开关由部署环境各自设置，两边都关时 4-H“预留验收”成立。
// 那条 BFF 读取路由上线之前，dev 环境也不要打开 why（打开只会得到“暂时无法获取”）；端到端的 trust-on 项目打开它只为核对这一点。
// SIE-02b 第二步 PR 2 起 BFF 有了这条读取路由（GET …/claims/{claim_id}/explain，挂在 BFF 的 why 开关上，经 insight.explain），
// 另加 report：首份报告的开发演示页（/dev/first-report/{studentId}，page.dev.tsx，只在开发、测试构建里存在），BFF 侧同名开关
// report（GET …/first-report）；演示页的待验证虚线取单条解释，所以 BFF 要同时开 report 与 why。report 不进壳导航、没有入口元素。
// 打开方式：STARBUDDY_TRUST_UI=why,dispute（逗号分隔）；只在 dev / test 生效，staging、pilot、生产一律关闭，
// 直到冲刺 5 的实现与文案签字后由人改这里的规则。只在服务端（服务端组件、server action、路由处理器）读，不进浏览器。

// LT-01 决定记录第 1、5 条：staging 可开上线测试范围内的块（STAGING_TRUST_FEATURES，与 BFF 一致）；pilot、生产仍一律关闭。
import { launchTier } from '../lib/launch-env.ts';

export const TRUST_FEATURES = ['why', 'dispute', 'reconcile', 'weekly', 'data_rights', 'report'] as const;
export type TrustFeature = (typeof TRUST_FEATURES)[number];
export const STAGING_TRUST_FEATURES: readonly TrustFeature[] = ['why', 'dispute', 'reconcile', 'weekly', 'data_rights', 'report'];

type Env = Record<string, string | undefined>;

export function trustFeatures(env: Env = process.env): ReadonlySet<TrustFeature> {
  const tier = launchTier(env);
  if (tier === null) return new Set();
  const allowed = tier === 'local' ? TRUST_FEATURES : STAGING_TRUST_FEATURES;
  const wanted = (env['STARBUDDY_TRUST_UI'] ?? '').split(',').map((s) => s.trim());
  return new Set(allowed.filter((f) => wanted.includes(f)));
}

export const trustEnabled = (f: TrustFeature, env: Env = process.env): boolean => trustFeatures(env).has(f);

/** 其中任一开关打开（壳导航、孩子页决定是否渲染信任界面的入口行） */
export const anyTrustEnabled = (fs: readonly TrustFeature[], env: Env = process.env): boolean => fs.some((f) => trustEnabled(f, env));
