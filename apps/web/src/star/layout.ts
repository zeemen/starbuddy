// 星空布局（spec 第 3 节）：纯函数，同一输入永远得到同一布局（截图测试的前提）。
// 平台没有分组或星座字段；前端不读适配器内容包（规则 1），所以原型按 local_id 排序排成紧凑网格，不做星座分组（待人确认 #8）。
// 星本身 16px，星位中心间距 24px。宽屏时排成接近正方形的网格（阿鸦 959 颗是 31 列 × 31 行，744px 见方）；
// 容器更窄时按容器宽度折行、纵向滚动（CSS auto-fill），不做缩放手势。
export const STAR_SIZE = 16;
export const STAR_PITCH = 24;

export interface Placed<T> {
  item: T;
  col: number;
  row: number;
  x: number;
  y: number;
}

/** 按 local_id（skill 的冒号之后）排序；排序只依赖编码本身，与返回顺序无关 */
export function orderStars<T extends { skill: string }>(stars: readonly T[]): T[] {
  const key = (s: T) => s.skill.slice(s.skill.indexOf(':') + 1);
  return [...stars].sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
}

/** 宽屏时的列数：接近正方形 */
export const squareColumns = (n: number): number => Math.max(1, Math.ceil(Math.sqrt(n)));
/** 星空最宽的列数：CSP 不允许内联样式，宽度靠 globals.css 里按 data-cols 写好的规则（1–64 列，星数上限 4096） */
export const MAX_COLS = 64;
/** globals.css 里 [data-cols] 规则的生成器（tests/unit.test.ts 核对 CSS 与它一致） */
export const skyColumnsCss = (): string =>
  Array.from({ length: MAX_COLS }, (_, i) => `.sky-field[data-cols="${i + 1}"] { max-width: ${(i + 1) * STAR_PITCH}px; }`).join('\n');

/** 给定列数时每颗星的网格坐标与像素中心（相对星空左上角） */
export function placeStars<T extends { skill: string }>(stars: readonly T[], columns: number): Placed<T>[] {
  const cols = Math.max(1, Math.floor(columns));
  return orderStars(stars).map((item, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    return { item, col, row, x: col * STAR_PITCH + STAR_PITCH / 2, y: row * STAR_PITCH + STAR_PITCH / 2 };
  });
}

/** 某个容器宽度下能放的列数（与 CSS auto-fill 一致） */
export const columnsForWidth = (widthPx: number): number => Math.max(1, Math.floor(widthPx / STAR_PITCH));
