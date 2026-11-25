// 最小 SVG 白板（spec 范围 1；ADR 07 选项 A 落地前的自研实现，对外接口不变）。
//   - 只接收校验器产出的 DSL（isValidated）；没校验过的对象一律不渲染（渲染器只接收已审核 DSL，ADR 07 决定 4）；
//   - 文字与公式都是文本节点（由 React 转义），不输出 HTML、链接、图片、外部字体或脚本；颜色只用 currentColor，
//     不用 style 属性（CSP 不允许内联样式）；
//   - 白板占舞台高度 ≥ 65%，角色锚点只放占位框（layout.ts 三套坐标令牌）；讲解权只用主轮廓 1.25 线表示；
//   - 无状态：画面只取决于 dsl 与 cursor，不读写任何存储、不发请求、不持久化。
// react 是同伴依赖，本文件不 import react：调用方把 React.createElement 传进 createWhiteboard，得到一个 React 函数组件。
// 这样本包在任何位置都不需要解析 react（apps/web 以相对路径引用本包，不改 npm 工作区；tests 用 react-dom/server 渲染）。
import { bbox, boardAt, type BoardElement } from './board.ts';
import type { Role } from './dsl.ts';
import { lineUnit, OUTLINE_LINES, SPEAKER_OUTLINE_LINES, STAGE_LAYOUTS, type LayoutName, type Rect } from './layout.ts';
import { isValidated, type ValidatedStageDsl } from './validate.ts';

export interface WhiteboardProps {
  dsl: ValidatedStageDsl;
  /** 已执行的步数（0..步数；越界会被夹住） */
  cursor: number;
  layout?: LayoutName;
  className?: string;
}

type Child<N> = N | string | null;
/** 与 React.createElement 兼容的元素工厂（只用到“标签名 + 属性 + 子节点”这一种调用） */
export type CreateElement<N> = (type: string, props: Record<string, unknown> | null, ...children: Child<N>[]) => N;

/** 保留三位小数（避免浮点尾数进入标记，快照稳定） */
const r3 = (n: number): number => +n.toFixed(3);

/** 线条、形状的描边（画布单位） */
const INK_WIDTH = 3;

/** 占位框的文字（只是开发占位，不是角色出场文案；正式母版交付后删除） */
const PLACEHOLDER: Record<Role, string> = { teacher: '老师（占位）', buddy: '点点（占位）' };

function anchor<N>(h: CreateElement<N>, role: Role, rect: Rect, speaking: boolean): N {
  const width = r3(lineUnit(rect) * (speaking ? SPEAKER_OUTLINE_LINES : OUTLINE_LINES));
  return h('g', { key: role, 'data-anchor': role, 'data-speaking': speaking ? 'true' : 'false' },
    h('rect', { x: rect.x, y: rect.y, width: rect.w, height: rect.h, rx: 8, fill: 'none', stroke: 'currentColor', strokeWidth: width,
      strokeDasharray: role === 'buddy' ? '6 4' : undefined, 'data-outline-width': width }),
    h('text', { x: r3(rect.x + rect.w / 2), y: r3(rect.y + rect.h / 2), textAnchor: 'middle', dominantBaseline: 'middle', fontSize: 14,
      fill: 'currentColor', fontWeight: speaking ? 700 : 400 }, PLACEHOLDER[role]));
}

function element<N>(h: CreateElement<N>, el: BoardElement, latest: boolean): N {
  const mark = { key: el.id, 'data-el': el.id, 'data-kind': el.kind, ...(latest ? { 'data-latest': 'true' } : {}) };
  switch (el.kind) {
    case 'text':
      return h('text', { ...mark, x: el.x, y: el.y, fontSize: el.fontSize, fill: 'currentColor',
        ...(el.formula ? { fontStyle: 'italic', fontFamily: 'serif', 'data-formula': 'true' } : {}) }, el.text);
    case 'line':
      return h('line', { ...mark, x1: el.x1, y1: el.y1, x2: el.x2, y2: el.y2, stroke: 'currentColor', strokeWidth: INK_WIDTH, strokeLinecap: 'round',
        strokeDasharray: el.dashed ? '12 8' : undefined });
    case 'shape':
      return el.shape === 'rect'
        ? h('rect', { ...mark, x: el.x, y: el.y, width: el.w, height: el.h, fill: 'none', stroke: 'currentColor', strokeWidth: INK_WIDTH })
        : h('ellipse', { ...mark, cx: el.x + el.w / 2, cy: el.y + el.h / 2, rx: el.w / 2, ry: el.h / 2, fill: 'none', stroke: 'currentColor',
          strokeWidth: INK_WIDTH });
    case 'highlight': {
      const b = bbox(el);
      return h('rect', { ...mark, 'data-target': el.target, x: b.x, y: b.y, width: b.w, height: b.h, rx: 4, fill: 'currentColor', fillOpacity: 0.12 });
    }
  }
}

/** 指向的描边（画布单位）：聚光是目标外接框四周留白后的粗描边；激光笔是目标左侧中线上的小三角。只用形状与线宽，不靠颜色 */
const POINT_WIDTH = 6;
const POINT_PAD = 10;
const LASER_SIZE = 14;

function pointMark<N>(h: CreateElement<N>, p: NonNullable<ReturnType<typeof boardAt>['point']>, canvas: { width: number; height: number }): N {
  const mark = { key: 'point', 'data-point': p.mode, 'data-target': p.target };
  if (p.mode === 'spotlight') {
    const x = Math.max(0, p.box.x - POINT_PAD); const y = Math.max(0, p.box.y - POINT_PAD);
    const w = Math.min(canvas.width, p.box.x + p.box.w + POINT_PAD) - x; const hh = Math.min(canvas.height, p.box.y + p.box.h + POINT_PAD) - y;
    return h('rect', { ...mark, x, y, width: w, height: hh, rx: 8, fill: 'none', stroke: 'currentColor', strokeWidth: POINT_WIDTH });
  }
  const cy = r3(p.box.y + p.box.h / 2); const tip = Math.max(LASER_SIZE, p.box.x - 4);
  return h('polygon', { ...mark, points: `${tip},${cy} ${tip - LASER_SIZE},${r3(cy - LASER_SIZE / 2)} ${tip - LASER_SIZE},${r3(cy + LASER_SIZE / 2)}`,
    fill: 'currentColor' });
}

/** 白板的元素树（纯函数；未校验的 DSL 返回 null） */
export function renderWhiteboard<N>(h: CreateElement<N>, { dsl, cursor, layout = 'desktop', className }: WhiteboardProps): N | null {
  if (!isValidated(dsl)) return null;
  const L = STAGE_LAYOUTS[layout];
  const board = boardAt(dsl, cursor);
  const latest = new Set(board.latest);
  const wb = L.whiteboard;
  // 高亮画在最底层，其余按出现先后
  const ordered = [...board.elements.filter((e) => e.kind === 'highlight'), ...board.elements.filter((e) => e.kind !== 'highlight')];
  return h('svg', {
    viewBox: `0 0 ${L.stage.width} ${L.stage.height}`, role: 'group', 'aria-label': `白板：${dsl.title}`, className,
    'data-stage-layout': layout, 'data-stage-cursor': board.cursor, 'data-stage-total': dsl.steps.length, 'data-stage-speaker': board.speaker,
  },
  h('rect', { 'data-stage-part': 'whiteboard', x: wb.x, y: wb.y, width: wb.w, height: wb.h, rx: 6, fill: 'currentColor', fillOpacity: 0.04,
    stroke: 'currentColor', strokeOpacity: 0.35, strokeWidth: 1 }),
  h('svg', { 'data-stage-part': 'board', x: wb.x, y: wb.y, width: wb.w, height: wb.h, viewBox: `0 0 ${dsl.canvas.width} ${dsl.canvas.height}`,
    preserveAspectRatio: 'xMidYMid meet', overflow: 'hidden' },
  // 槽位：空框（内容由调用方运行时在内存里填充，DSL 不含学生数据）；没有槽位、没有指向时输出与 v0.1 相同
  ...(dsl.slots ?? []).map((sl) => h('rect', { key: `slot-${sl.id}`, 'data-slot': sl.id, 'data-slot-kind': sl.kind, x: sl.x, y: sl.y, width: sl.w, height: sl.h,
    rx: 4, fill: 'none', stroke: 'currentColor', strokeOpacity: 0.5, strokeWidth: 2, strokeDasharray: '8 6' })),
  ...ordered.map((el) => element(h, el, latest.has(el.id))),
  board.point ? pointMark(h, board.point, dsl.canvas) : null),
  h('g', { 'data-stage-part': 'anchors', 'aria-hidden': 'true' },
    h('rect', { 'data-stage-part': 'dialogue', x: L.dialogue.x, y: L.dialogue.y, width: L.dialogue.w, height: L.dialogue.h, rx: 6,
      fill: 'none', stroke: 'currentColor', strokeOpacity: 0.35, strokeWidth: 1 }),
    dsl.roles.includes('buddy') ? anchor(h, 'buddy', L.buddy, board.speaker === 'buddy') : null,
    anchor(h, 'teacher', L.teacher, board.speaker === 'teacher')));
}

/**
 * 白板 React 组件：const Whiteboard = createWhiteboard(React.createElement)；用法 <Whiteboard dsl={已校验} cursor={n} layout="desktop" />。
 */
export function createWhiteboard<N>(h: CreateElement<N>): (props: WhiteboardProps) => N | null {
  const Whiteboard = (props: WhiteboardProps): N | null => renderWhiteboard(h, props);
  return Whiteboard;
}
