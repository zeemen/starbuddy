// 学生端数学诊断的前端开关（specs/diagnosis-student-entry；做法同 src/practice/features.ts、src/retest/features.ts）。
// 默认关闭：关闭时数学科目页不渲染入口、诊断页 404（页面在读会话之前判断）、正式 server action 一律不发请求——与开关打开前零差异。
// BFF 侧没有诊断开关：配了学习服务地址就注册诊断路由（deploy/flags/staging.yaml 的 diagnosis 项），所以入口只由这个前端开关决定。
// 打开方式：STARBUDDY_DIAGNOSIS_UI=diagnosis；dev / test / staging 可开（LT-01 决定记录第 1、5、7 条：学习服务在教研签字、
// present_item 签字前回 501，页面显示“现在还不能开始”），pilot、生产一律关闭。只在服务端读，不进浏览器。
// 开发演示页（/dev/diagnosis，page.dev.tsx）不受这个开关影响，仍只靠构建期与运行时的开发环境关卡。
import { launchTier } from '../lib/launch-env.ts';

type Env = Record<string, string | undefined>;

export const DIAGNOSIS_FEATURE = 'diagnosis';

export function diagnosisEnabled(env: Env = process.env): boolean {
  if (launchTier(env) === null) return false;
  return (env['STARBUDDY_DIAGNOSIS_UI'] ?? '').split(',').map((s) => s.trim()).includes(DIAGNOSIS_FEATURE);
}

/** 有诊断入口的学科（前端学科码）：只有数学（阿鸦，九年级二次函数，SIE-02a） */
export const DIAGNOSIS_SUBJECTS = ['math'] as const;
export const isDiagnosisSubject = (s: unknown): s is (typeof DIAGNOSIS_SUBJECTS)[number] =>
  typeof s === 'string' && (DIAGNOSIS_SUBJECTS as readonly string[]).includes(s);
