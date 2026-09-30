// 符号样张页（spec 范围第 1 条）：三态加保持，16 / 24 / 32 / 48px，浅色与深色并排，以及点亮动效演示。
// 16px 像素测试与视觉回归都用这一页（数据是固定的，不连 BFF）。
import { headers } from 'next/headers';
import { notFound } from 'next/navigation';
import { devRuntimeAllowed } from '../../../lib/dev-pages.ts';
import type { Display } from '../../../lib/star-map.ts';
import { Star, StarDefs } from '../../../star/Star.tsx';

const STATES: { key: string; label: string; display: Display; retained: boolean }[] = [
  { key: 'unlit', label: '未亮', display: 'unlit', retained: false },
  { key: 'half', label: '半亮', display: 'half', retained: false },
  { key: 'lit', label: '亮', display: 'lit', retained: false },
  { key: 'retained', label: '亮·保持', display: 'lit', retained: true },
];
const SIZES = [16, 24, 32, 48];

function Panel({ theme }: { theme: 'light' | 'dark' }) {
  return (
    <div className="glyph-panel" data-theme-panel={theme} data-theme={theme}>
      <h2>{theme === 'light' ? '浅色' : '深色'}</h2>
      <table className="glyph-table">
        <thead>
          <tr>
            <th scope="col">尺寸</th>
            {STATES.map((s) => <th key={s.key} scope="col">{s.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {SIZES.map((size) => (
            <tr key={size}>
              <th scope="row">{size}px</th>
              {STATES.map((s) => (
                <td key={s.key} data-glyph={`${theme}-${s.key}-${size}`}>
                  <Star display={s.display} retained={s.retained} size={size} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 开发页（安全审查 S6）：文件名 page.dev.tsx，只在开发、测试构建里存在（构建期常量，4-H H2）；运行时再核对环境与本机 Host
export const dynamic = 'force-dynamic';

export default async function StarGlyphsPage() {
  if (!devRuntimeAllowed((await headers()).get('host'))) notFound();
  return (
    <main>
      <StarDefs />
      <h1>星形符号样张</h1>
      <p><span className="watermark">原型 · 按视觉规范图 2b 自绘，正式资产交付后替换</span></p>
      <Panel theme="light" />
      <Panel theme="dark" />
      <h2>点亮动效</h2>
      <p className="muted">描边外发光 0 → 6 线宽 → 0，1.2 秒，一次；减少动态效果时改为静态描边变色。</p>
      <div data-glyph="light-up">
        <Star display="lit" size={48} lightUp />
      </div>
    </main>
  );
}
