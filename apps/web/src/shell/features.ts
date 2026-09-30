// 信任界面的功能开关（spec 第 12 节；冲刺 5-H spec 第 6 节：why / dispute / reconcile / weekly / data_rights）。
// 默认全部关闭：关闭时预留位不渲染任何元素、不发任何请求；BFF 路由表里也没有对应路由（services/bff/tests/shell-pages.integration.test.ts）。
// 打开方式：STARBUDDY_TRUST_UI=why,dispute（逗号分隔）；只在 dev / test 生效，staging、pilot、生产一律关闭，
// 直到冲刺 5 的实现与文案签字后由人改这里的规则。服务端组件里读，不进浏览器。

export const TRUST_FEATURES = ['why', 'dispute', 'reconcile', 'weekly', 'data_rights'] as const;
export type TrustFeature = (typeof TRUST_FEATURES)[number];

type Env = Record<string, string | undefined>;

export function trustFeatures(env: Env = process.env): ReadonlySet<TrustFeature> {
  const run = env['STARBUDDY_ENV'] ?? '';
  if (run !== 'dev' && run !== 'test') return new Set();
  const wanted = (env['STARBUDDY_TRUST_UI'] ?? '').split(',').map((s) => s.trim());
  return new Set(TRUST_FEATURES.filter((f) => wanted.includes(f)));
}

export const trustEnabled = (f: TrustFeature, env: Env = process.env): boolean => trustFeatures(env).has(f);
