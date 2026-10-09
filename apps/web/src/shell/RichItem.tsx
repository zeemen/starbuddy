// 受限富文本题面的渲染（PLT-12，src/lib/rich-item.ts）：只产出 React 文本节点与 span / p，不插入原始 HTML，
// 文字与公式都按文本转义，没有 HTML 注入的路径。公式用纯文本排版（latexToText，KaTeX 的等价替代）；图片只显示图片说明。
// 诊断（src/diagnosis/Diagnosis.tsx）与再练（src/practice/PracticeItem.tsx）共用；没有 rich 时调用方照旧显示纯文字题面。
import { latexToText, type RichInline, type RichParagraph } from '../lib/rich-item.ts';

export function RichRuns({ runs }: { runs: readonly RichInline[] }) {
  return (
    <>
      {runs.map((r, i) => r.type === 'text' ? <span key={i}>{r.text}</span>
        : r.type === 'math' ? <span key={i} className="item-math">{latexToText(r.latex) ?? r.latex}</span>
          : <span key={i} className="item-figure">［{r.alt ?? '▢'}］</span>)}
    </>
  );
}

export function RichParagraphs({ stem }: { stem: readonly RichParagraph[] }) {
  return <>{stem.map((p, i) => <p key={i}><RichRuns runs={p.runs} /></p>)}</>;
}
