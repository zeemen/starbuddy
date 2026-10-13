// 舞台布局的三套坐标令牌（角色视觉家族规范 v1.2 第六章 :363-410，交付物“舞台布局：桌面 / 平板 / 手机三套坐标” :497）。
// 单位是舞台 SVG 的逻辑单位。规则（tests/layout.test.ts 逐条核对）：
//   - 白板占舞台高度 ≥ 65%；
//   - 老师锚定右下，高 = 舞台高 22%（桌面、平板）/ 18%（手机）；点点在左下，尺寸 = 老师的 0.85 倍；
//   - 角色与对话条都不压白板（“永不遮挡白板当前书写行”的最简做法：白板与角色区不重叠）；全部在舞台内。
// 本步只放占位框，不放角色形象；正式母版与 character-tokens.json 交付后替换（家族令牌由视觉组出）。

export interface Rect { x: number; y: number; w: number; h: number }

export interface StageLayout {
  stage: { width: number; height: number };
  whiteboard: Rect;
  teacher: Rect;
  buddy: Rect;
  dialogue: Rect;
}

export const LAYOUT_NAMES = ['desktop', 'tablet', 'mobile'] as const;
export type LayoutName = (typeof LAYOUT_NAMES)[number];

type DeepReadonly<T> = { readonly [K in keyof T]: T[K] extends object ? DeepReadonly<T[K]> : T[K] };

/** 深冻结（令牌是全局常量，任何一层都不允许运行时改动） */
function deepFreeze<T>(v: T): DeepReadonly<T> {
  if (typeof v === 'object' && v !== null) {
    for (const x of Object.values(v)) deepFreeze(x);
    Object.freeze(v);
  }
  return v as DeepReadonly<T>;
}

export const STAGE_LAYOUTS: DeepReadonly<Record<LayoutName, StageLayout>> = deepFreeze<Record<LayoutName, StageLayout>>({
  // 桌面 / 平板横屏（宽屏）
  desktop: {
    stage: { width: 1000, height: 650 },
    whiteboard: { x: 20, y: 20, w: 960, h: 440 },
    teacher: { x: 860, y: 487, w: 120, h: 143 },
    buddy: { x: 20, y: 508.45, w: 102, h: 121.55 },
    dialogue: { x: 138, y: 540, w: 706, h: 72 },
  },
  // 平板（4:3）
  tablet: {
    stage: { width: 1000, height: 750 },
    whiteboard: { x: 20, y: 20, w: 960, h: 500 },
    teacher: { x: 842, y: 565, w: 138, h: 165 },
    buddy: { x: 20, y: 589.75, w: 117, h: 140.25 },
    dialogue: { x: 153, y: 630, w: 673, h: 80 },
  },
  // 手机竖屏
  mobile: {
    stage: { width: 400, height: 720 },
    whiteboard: { x: 12, y: 12, w: 376, h: 480 },
    teacher: { x: 280, y: 578.4, w: 108, h: 129.6 },
    buddy: { x: 12, y: 597.84, w: 92, h: 110.16 },
    dialogue: { x: 12, y: 504, w: 376, h: 60 },
  },
});

/** 白板最少占舞台高度的比例 */
export const MIN_WHITEBOARD_RATIO = 0.65;
/** 老师高度占舞台高度的比例 */
export const TEACHER_HEIGHT_RATIO: Readonly<Record<LayoutName, number>> = deepFreeze({ desktop: 0.22, tablet: 0.22, mobile: 0.18 });
/** 点点相对老师的尺寸 */
export const BUDDY_SCALE = 0.85;

/** 基准线宽（1 线）= 角色身高的 1/48（视觉家族规范 v1.2 :168）；占位框以框高当身高 */
export const lineUnit = (anchor: Rect): number => anchor.h / 48;
/** 主轮廓 1 线；讲话者临时加粗到 1.25 线（:170、:408），不用颜色、光晕、箭头 */
export const OUTLINE_LINES = 1;
export const SPEAKER_OUTLINE_LINES = 1.25;
