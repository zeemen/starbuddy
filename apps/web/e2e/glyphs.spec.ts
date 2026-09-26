// 16px 可辨（spec 验收）：DPR 1 与 DPR 2、浅色与深色下各渲染三态与保持态（符号样张页，16px），
// 转成灰度后计算星内部的填充像素比例：未亮 ≤ 0.1，半亮 0.35–0.65，亮 ≥ 0.85；任意两态之间的灰度差异都超过阈值。
// 星内部 = 像素中心在五角星多边形内、且离边的距离大于半个描边加 0.5 CSS px（按几何算，不从截图猜）。
// 阈值记录在 specs/star-map-sprint3/lessons.md。
import { expect, test, type Page } from '@playwright/test';

const POINTS = '12,1.8 14.42,9.26 22.27,9.26 15.92,13.87 18.35,21.34 12,16.73 5.65,21.34 8.08,13.87 1.73,9.26 9.58,9.26'
  .split(' ').map((p) => p.split(',').map(Number) as [number, number]);
const SIZE = 16;
const STROKE_PX = 1.5;
const MARGIN = 5;              // 截图时星四周各留 5px（保持层外圈在星框外）
const FILLED_DIFF = 12;        // 与底色的灰度差超过它，算“被填充”
const PAIR_MIN_PIXELS = 20;     // 任意两态之间，灰度差超过 FILLED_DIFF 的像素至少这么多（按 DPR 1 计，DPR 2 乘 4）

const STATES = ['unlit', 'half', 'lit', 'retained'] as const;
const RATIO: Record<(typeof STATES)[number], [number, number]> = {
  unlit: [0, 0.1], half: [0.35, 0.65], lit: [0.85, 1], retained: [0.85, 1],
};

function inside(x: number, y: number): boolean {
  let c = false;
  for (let i = 0, j = POINTS.length - 1; i < POINTS.length; j = i++) {
    const [xi, yi] = POINTS[i]!; const [xj, yj] = POINTS[j]!;
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}
function edgeDistance(x: number, y: number): number {
  let best = Infinity;
  for (let i = 0, j = POINTS.length - 1; i < POINTS.length; j = i++) {
    const [ax, ay] = POINTS[j]!; const [bx, by] = POINTS[i]!;
    const t = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)));
    best = Math.min(best, Math.hypot(x - (ax + t * (bx - ax)), y - (ay + t * (by - ay))));
  }
  return best;
}

/** 截取一颗星（含四周边距），在浏览器里解码成灰度数组 */
async function grab(page: Page, selector: string, dpr: number) {
  const box = (await page.locator(selector).boundingBox())!;
  const clip = { x: box.x - MARGIN, y: box.y - MARGIN, width: SIZE + 2 * MARGIN, height: SIZE + 2 * MARGIN };
  const png = await page.screenshot({ clip, animations: 'disabled' });
  const gray: number[] = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext('2d')!;
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    const out: number[] = [];
    for (let i = 0; i < d.length; i += 4) out.push(0.299 * d[i]! + 0.587 * d[i + 1]! + 0.114 * d[i + 2]!);
    return out;
  }, png.toString('base64'));
  const w = Math.round(clip.width * dpr);
  return { gray, w, h: gray.length / w };
}

function fillRatio(img: { gray: number[]; w: number }, dpr: number): { ratio: number; interior: number } {
  // 半个描边，再加 0.5 CSS px 的抗锯齿带（DPR 1 为半个设备像素，DPR 2 为 1 个设备像素）
  const inset = (STROKE_PX / 2 + 0.5) * (24 / SIZE);
  const bg = img.gray[0]!;  // 左上角：星框外的底色
  let interior = 0; let filled = 0;
  for (let py = 0; py < img.gray.length / img.w; py++) {
    for (let px = 0; px < img.w; px++) {
      const u = ((px + 0.5) / dpr - MARGIN) * (24 / SIZE);
      const v = ((py + 0.5) / dpr - MARGIN) * (24 / SIZE);
      if (!inside(u, v) || edgeDistance(u, v) <= inset) continue;
      interior += 1;
      if (Math.abs(img.gray[py * img.w + px]! - bg) > FILLED_DIFF) filled += 1;
    }
  }
  return { ratio: filled / interior, interior };
}

for (const dpr of [1, 2]) {
  test.describe(`DPR ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr });
    for (const theme of ['light', 'dark'] as const) {
      test(`16px 三态与保持态可辨（${theme}）`, async ({ page }) => {
        await page.goto('/dev/star-glyphs');
        const images: Record<string, { gray: number[]; w: number }> = {};
        const ratios: Record<string, { ratio: number; interior: number }> = {};
        const report: string[] = [];
        for (const s of STATES) {
          images[s] = await grab(page, `[data-glyph="${theme}-${s}-16"] svg`, dpr);
          ratios[s] = fillRatio(images[s]!, dpr);
          report.push(`${s}=${ratios[s]!.ratio.toFixed(2)}（内部 ${ratios[s]!.interior} 像素）`);
        }
        const pairs: [string, string, number][] = [];
        for (let i = 0; i < STATES.length; i++) {
          for (let j = i + 1; j < STATES.length; j++) {
            const a = images[STATES[i]!]!.gray; const b = images[STATES[j]!]!.gray;
            const n = a.filter((x, k) => Math.abs(x - b[k]!) > FILLED_DIFF).length;
            pairs.push([STATES[i]!, STATES[j]!, n]);
            report.push(`${STATES[i]}↔${STATES[j]}=${n} 像素`);
          }
        }
        console.log(`[16px] ${theme} DPR${dpr}: ${report.join('；')}`);
        for (const s of STATES) {
          const [lo, hi] = RATIO[s];
          expect(ratios[s]!.interior, `${theme} ${s} 内部像素（太少则比例没有意义）`).toBeGreaterThanOrEqual(10 * dpr * dpr);
          expect(ratios[s]!.ratio, `${theme} DPR${dpr} ${s} 填充比例`).toBeGreaterThanOrEqual(lo);
          expect(ratios[s]!.ratio, `${theme} DPR${dpr} ${s} 填充比例`).toBeLessThanOrEqual(hi);
        }
        for (const [a, b, n] of pairs) {
          expect(n, `${theme} DPR${dpr} ${a} 与 ${b} 之间明显不同的像素数`).toBeGreaterThanOrEqual(PAIR_MIN_PIXELS * dpr * dpr);
        }
        test.info().annotations.push({ type: 'fill-ratio', description: report.join('；') });
      });
    }
  });
}
