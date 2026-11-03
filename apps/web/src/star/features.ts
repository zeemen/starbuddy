// H5 星图 v1 的前端功能开关（specs/star-map-v1-h5/spec.md 第 1 节与第 3 节“待验证虚线”；做法同 src/shell/features.ts）：
//   starmap_v1      星图页改读 BFF 的 v1 接口（GET /bff/v0/students/{id}/star-map/v1，星 = 知识节点，判断先于展示）；
//                   关闭时星图页保持 3-H v0 原样（数据源、三态映射、水印、DOM 与像素都不变）
//   starmap_dashed  BFF 返回的待验证星（display = dashed）画虚线并显示下一次验证日期；关闭时一律按中性底星画（不带 claim_id）。
//                   只在 starmap_v1 打开时有意义
// 默认全部关闭。打开方式：STARBUDDY_STARMAP=starmap_v1[,starmap_dashed]，只在 dev / test 生效；staging、pilot、生产一律关闭，
// 直到 ADR 11 签字、数学地图发布、判断生成接入调度（虚线另待 ADR 13 #2 签字）后由人改这里的规则。
// BFF 侧是同名开关（services/bff/src/star-map/features.ts）：前后端由部署环境各自设置；前端开、BFF 关时 v1 接口是 404，页面显示“没有找到”。
// 只在服务端（服务端组件）读，不进浏览器。

// LT-01 决定记录第 1、5 条：staging 只开 starmap_v1（虚线另待 ADR 13 #2 签字，staging 设了也按关）；pilot、生产仍一律关闭。
import { launchTier } from '../lib/launch-env.ts';

export const STAR_MAP_FEATURES = ['starmap_v1', 'starmap_dashed'] as const;
export type StarMapFeature = (typeof STAR_MAP_FEATURES)[number];
export const STAGING_STAR_MAP_FEATURES: readonly StarMapFeature[] = ['starmap_v1'];

type Env = Record<string, string | undefined>;

export function starMapFeatures(env: Env = process.env): ReadonlySet<StarMapFeature> {
  const tier = launchTier(env);
  if (tier === null) return new Set();
  const wanted = (env['STARBUDDY_STARMAP'] ?? '').split(',').map((s) => s.trim());
  if (!wanted.includes('starmap_v1')) return new Set();  // 只开虚线不开 v1：一律关闭
  const allowed = tier === 'local' ? STAR_MAP_FEATURES : STAGING_STAR_MAP_FEATURES;
  return new Set(allowed.filter((f) => wanted.includes(f)));
}

export const starMapEnabled = (f: StarMapFeature, env: Env = process.env): boolean => starMapFeatures(env).has(f);
