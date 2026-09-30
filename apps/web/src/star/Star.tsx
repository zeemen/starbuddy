// 星形符号（spec 第 3 节）：纯 SVG；三态靠星内部被填充的面积区分（约 0、约 1/2、约 1），不靠颜色深浅。
//   未亮：只有描边；半亮：右半星光金（clipPath 裁出右半）；亮：星光金全填充；保持：亮 + 外圈细描边圆。
// 描边色来自 CSS 变量（随主题切换），填充只用星光金。
import { STAR_CENTER, STAR_OUTER_RADIUS, STAR_POINTS } from './geometry.ts';
import { HALF_FILL_OPACITY, RETAIN_RING_GAP_PX, RETAIN_RING_PX, STAR_GOLD, strokePx } from './tokens.ts';
import type { Display } from '../lib/star-map.ts';

export interface StarProps {
  display: Display;
  retained?: boolean;
  size?: number;
  /** 符号样张页演示用：一次性“点亮”发光 */
  lightUp?: boolean;
}

/** 半亮的右半裁剪区：objectBoundingBox 单位，任何尺寸都裁出星的右半。每页放一次 */
export function StarDefs() {
  return (
    <svg width="0" height="0" aria-hidden="true" focusable="false" className="star-defs">
      <defs>
        <clipPath id="star-right-half" clipPathUnits="objectBoundingBox">
          <rect x="0.5" y="0" width="0.5" height="1" />
        </clipPath>
      </defs>
    </svg>
  );
}

export function Star({ display, retained = false, size = 16, lightUp = false }: StarProps) {
  const unit = 24 / size;  // 1 CSS px 对应的 viewBox 单位
  const ringR = STAR_OUTER_RADIUS + (RETAIN_RING_GAP_PX + RETAIN_RING_PX / 2) * unit;
  const stroke = display === 'unlit' ? 'var(--star-stroke-unlit)' : 'var(--star-stroke)';
  return (
    <svg
      className={lightUp ? 'star star-light-up' : 'star'}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      overflow="visible"
      data-display={display}
      data-retained={retained ? 'true' : 'false'}
      aria-hidden="true"
      focusable="false"
    >
      {display === 'half' ? (
        <polygon points={STAR_POINTS} fill={STAR_GOLD} fillOpacity={HALF_FILL_OPACITY} clipPath="url(#star-right-half)" />
      ) : null}
      <polygon
        points={STAR_POINTS}
        fill={display === 'lit' ? STAR_GOLD : 'none'}
        stroke={stroke}
        strokeWidth={strokePx(size) * unit}
        strokeLinejoin="round"
      />
      {retained && display === 'lit' ? (
        <circle cx={STAR_CENTER.x} cy={STAR_CENTER.y} r={ringR} fill="none" stroke="var(--star-stroke)"
                strokeWidth={RETAIN_RING_PX * unit} />
      ) : null}
    </svg>
  );
}
