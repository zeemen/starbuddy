// 占位图标（LT-07 决定记录第 3 条：设计提供前用占位）：代码生成的 PNG，不用任何第三方素材、不进二进制文件。
// 图案：鸦青底（src/lib/theme.ts 的 YAQING）上一颗宣白五角星（src/star/geometry.ts 的星形，按 24 格归一化坐标放大）；
// 星光金只在星图的星上，图标不用（tests/static.test.ts）。星的外接圆半径占边长约 0.3，在 maskable 安全区（直径 80%）以内。
// PNG：8 位 RGBA、每行滤波 0、zlib 压缩（node:zlib），3×3 超采样抗锯齿。只在服务端（/icons/[file] 路由）用，结果按尺寸记住。
import { deflateSync } from 'node:zlib';
import { STAR_CENTER, STAR_POINTS } from '../star/geometry.ts';
import { XUANBAI, YAQING } from '../lib/theme.ts';

/** 路由认的文件名 → 边长 */
export const ICON_FILES: Record<string, number> = {
  'icon-192.png': 192,
  'icon-512.png': 512,
  'maskable-512.png': 512,
  'apple-touch-icon.png': 180,
};

const rgb = (hex: string): [number, number, number] => {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) throw new Error(`bad color ${hex}`);
  return [parseInt(m[1]!, 16), parseInt(m[2]!, 16), parseInt(m[3]!, 16)];
};

const POLY: [number, number][] = STAR_POINTS.split(' ').map((p) => p.split(',').map(Number) as [number, number]);

function inside(x: number, y: number): boolean {
  let hit = false;
  for (let i = 0, j = POLY.length - 1; i < POLY.length; j = i++) {
    const [xi, yi] = POLY[i]!;
    const [xj, yj] = POLY[j]!;
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf: Uint8Array): number {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff]! ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Uint8Array): Buffer {
  const out = Buffer.alloc(12 + data.length);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, 'ascii');
  Buffer.from(data).copy(out, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
  return out;
}

/** 边长 size 的占位图标 PNG */
export function iconPng(size: number): Buffer {
  if (!Number.isInteger(size) || size < 16 || size > 1024) throw new Error('icon size out of range');
  const bg = rgb(YAQING);
  const fg = rgb(XUANBAI);
  const scale = (size * 0.6) / 24;  // 星形 24 格 → 边长的 60%
  const SS = 3;
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    const row = y * (size * 4 + 1);
    raw[row] = 0;  // 滤波方式 0
    for (let x = 0; x < size; x++) {
      let cover = 0;
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const px = (x + (sx + 0.5) / SS - size / 2) / scale + STAR_CENTER.x;
          const py = (y + (sy + 0.5) / SS - size / 2) / scale + STAR_CENTER.y;
          if (inside(px, py)) cover++;
        }
      }
      const a = cover / (SS * SS);
      const o = row + 1 + x * 4;
      raw[o] = Math.round(bg[0] + (fg[0] - bg[0]) * a);
      raw[o + 1] = Math.round(bg[1] + (fg[1] - bg[1]) * a);
      raw[o + 2] = Math.round(bg[2] + (fg[2] - bg[2]) * a);
      raw[o + 3] = 255;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;   // 位深
  ihdr[9] = 6;   // RGBA
  ihdr[10] = 0;  // 压缩
  ihdr[11] = 0;  // 滤波
  ihdr[12] = 0;  // 不隔行
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', new Uint8Array(0)),
  ]);
}

const memo = new Map<number, Buffer>();
export function iconFor(file: string): Buffer | null {
  if (!Object.hasOwn(ICON_FILES, file)) return null;
  const size = ICON_FILES[file]!;
  let png = memo.get(size);
  if (!png) { png = iconPng(size); memo.set(size, png); }
  return png;
}
