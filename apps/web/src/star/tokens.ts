// 星形符号的颜色与比例（角色视觉家族规范 v1.2 第三章、图 2b；specs/star-map-sprint3/spec.md 第 3 节）。
// 星光金是唯一奖励色，只用于半亮、亮星的填充：它只能出现在这个文件里，并且只被 Star.tsx 引用
// （tests/static.test.ts 检查）。正式的家族令牌文件交付后整体替换（待人确认 #5）。
export const STAR_GOLD = '#D9A441';
/** 半亮：右半填充的不透明度（图 2b）。16px 灰度测试不达标时改为 1，需视觉负责人确认（待人确认 #4） */
export const HALF_FILL_OPACITY = 0.55;
/** 描边：按图 2b 的比例（描边 2 / 星高 84）随尺寸缩放，下限 1.5 CSS px（待人确认 #4） */
export const strokePx = (size: number): number => Math.max(1.5, (size * 2) / 84);
/** 保持层外圈：1px 细描边圆，与星的间距 2px（待人确认 #6） */
export const RETAIN_RING_GAP_PX = 2;
export const RETAIN_RING_PX = 1;
