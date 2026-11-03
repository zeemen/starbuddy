// 复测与验证题的前端开关（specs/retest-delivery-kge05 范围第 4 条“学生端 UI 开关 STARBUDDY_RETEST_UI，默认关”；做法同 src/practice/features.ts）。
// 默认关闭：关闭时前端不渲染任何入口元素、不发任何请求，复测页面一律 404（页面在读会话之前判断）——与开关打开前零差异。
// BFF 侧是同名开关（services/bff/src/retest/features.ts）：开关关时 BFF 不注册三条路由。前后端的开关由部署环境各自设置。
// 打开方式：STARBUDDY_RETEST_UI=retest；只在 dev / test 生效，staging、pilot、生产一律关闭。只在服务端读，不进浏览器。

// LT-01 决定记录第 1、5、7 条：staging 也可开（学习服务在教研签字前回 501，开关开、待签字内容）；pilot、生产仍一律关闭。
import { launchTier } from '../lib/launch-env.ts';

type Env = Record<string, string | undefined>;

export const RETEST_FEATURE = 'retest';

export function retestEnabled(env: Env = process.env): boolean {
  if (launchTier(env) === null) return false;
  return (env['STARBUDDY_RETEST_UI'] ?? '').split(',').map((s) => s.trim()).includes(RETEST_FEATURE);
}

/** 有复测入口的学科（前端学科码）：与错题再练相同，数学、道法；语文学习服务默认不开 */
export const RETEST_SUBJECTS = ['math', 'politics'] as const;
export type RetestSubject = (typeof RETEST_SUBJECTS)[number];
export const isRetestSubject = (s: unknown): s is RetestSubject => typeof s === 'string' && (RETEST_SUBJECTS as readonly string[]).includes(s);
