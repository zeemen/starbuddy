// H5 星图 v1 的功能开关（specs/star-map-v1-h5/spec.md 第 1 节与第 3 节“待验证虚线”；做法同 src/trust/features.ts）：
//   starmap_v1      注册 GET /students/{student_id}/star-map/v1（星 = 知识节点，判断先于展示）；关闭时不注册（表外 404），
//                   v0 星图（GET /students/{student_id}/star-map）的行为与响应逐字节不变
//   starmap_dashed  待验证（to_verify）判断画虚线与下一次验证任务（不带结论文字）；关闭时按中性底星。只在 starmap_v1 打开时有意义
// 默认全部关闭。打开方式：STARBUDDY_STARMAP=starmap_v1[,starmap_dashed]，只在 dev / test 生效；其余环境设了就拒绝启动（main.ts）。
// 生产启用以 ADR 11 签字、数学地图发布、判断生成接入调度三者满足为准（spec #1），虚线另待 ADR 13 #2 签字，由人改这里的规则。
// LT-01 决定记录第 1 条：staging 可开 starmap_v1（上线测试范围“星图”）；starmap_dashed 另待 ADR 13 #2 签字，staging 仍拒绝；pilot、生产仍拒绝。
import { isLocalEnv, launchFeatureEnv, type RunEnv } from '../http/config.ts';

export const STAR_MAP_FEATURES = ['starmap_v1', 'starmap_dashed'] as const;
export type StarMapFeature = (typeof STAR_MAP_FEATURES)[number];
export const STAGING_STAR_MAP_FEATURES: readonly StarMapFeature[] = ['starmap_v1'];

/** 解析开关；dev / test 以外（staging 只收 starmap_v1）设了开关直接抛错（调用方拒绝启动），没设则一律关闭 */
export function parseStarMapFeatures(env: RunEnv, raw: string | undefined): ReadonlySet<StarMapFeature> {
  if (raw === undefined || raw.trim() === '') return new Set();
  if (!launchFeatureEnv(env)) throw new Error('STARBUDDY_STARMAP 只在 dev / test / staging 可用（ADR 11 签字、地图发布、判断调度接入之前 pilot 以上一律关闭）');
  const wanted = raw.split(',').map((s) => s.trim()).filter((s) => s !== '');
  const unknown = wanted.filter((w) => !(STAR_MAP_FEATURES as readonly string[]).includes(w));
  if (unknown.length > 0) throw new Error(`STARBUDDY_STARMAP 含不认识的开关：${unknown.join(', ')}`);
  const outside = isLocalEnv(env) ? [] : wanted.filter((w) => !(STAGING_STAR_MAP_FEATURES as readonly string[]).includes(w));
  if (outside.length > 0) throw new Error(`STARBUDDY_STARMAP：${outside.join(', ')} 待签字，staging 不可打开`);
  if (wanted.includes('starmap_dashed') && !wanted.includes('starmap_v1')) throw new Error('STARBUDDY_STARMAP：starmap_dashed 需要同时打开 starmap_v1');
  return new Set(STAR_MAP_FEATURES.filter((f) => wanted.includes(f)));
}
