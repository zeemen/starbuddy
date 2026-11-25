// 最小 SVG 白板（spec 范围 1；ADR 07 选项 A 落地前的自研实现，对外接口不变）。
//   - 只接收校验器产出的 DSL（isValidated）；没校验过的对象一律不渲染（渲染器只接收已审核 DSL，ADR 07 决定 4）；
//   - 文字与公式都是文本节点（由 React 转义），不输出 HTML、链接、图片、外部字体或脚本；颜色只用 currentColor，
//     不用 style 属性（CSP 不允许内联样式）；
//   - 白板占舞台高度 ≥ 65%，角色锚点只放占位框（layout.ts 三套坐标令牌）；讲解权只用主轮廓 1.25 线表示；
//   - 无状态：画面只取决于 dsl 与 cursor，不读写任何存储、不发请求、不持久化。
// react 是同伴依赖，本文件不 import react：调用方把 React.createElement 传进 createWhiteboard，得到一个 React 函数组件。
// 这样本包在任何位置都不需要解析 react（apps/web 以相对路径引用本包，不改 npm 工作区；tests 用 react-dom/server 渲染）。
import { bbox, boardAt, type BoardElement, type Box } from './board.ts';
import type { Role } from './dsl.ts';
import { lineUnit, OUTLINE_LINES, SPEAKER_OUTLINE_LINES, STAGE_LAYOUTS, type LayoutName, type Rect } from './layout.ts';
import { originsAt, type StageAnchor } from './question.ts';
import { isValidated, type ValidatedStageDsl } from './validate.ts';

export interface WhiteboardProps {
  dsl: ValidatedStageDsl;
  /** 已执行的步数（0..步数；越界会被夹住） */
  cursor: number;
  layout?: LayoutName;
  className?: string;
  /**
   * 可提问模式（specs/stage-point-to-ask 第二节第 4 条）。缺省关闭：关闭时输出与以前逐字节相同。
   * 打开时白板上每个元素（高亮框除外）与每个槽位各盖一层透明点击区（role=button、可 Tab 聚焦，回车或空格选中），
   * 选中的锚点画虚线细框。组件无状态：选中谁、弹不弹菜单、暂停播放都由调用方决定；组件不联网、不存储。
   */
  askable?: AskableProps;
}

export interface AskableProps {
  /** 当前选中的锚点（没有选中为 null） */
  selected: StageAnchor | null;
  onSelect: (anchor: StageAnchor) => void;
  /** 点击区的读屏标签：由调用方从文案取（组件不念 DSL 里的文字与公式原文）。step 是元素写上去的步序号（1 起算），index 是此刻白板上的第几项 */
  label: (anchor: StageAnchor, step: number | null, index: number) => string;
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

/**
 * 点击区的最小边长（舞台逻辑单位）。各套布局的舞台宽度取该类设备的名义 CSS 宽度（桌面 1000、手机 400），按名义尺寸渲染时
 * 点击区 ≥ 52 CSS 像素；缩到名义宽度的 85% 时仍 ≥ 44（spec：换算到屏幕不小于 44 × 44；端到端按实际像素再核一次）
 */
export const ASK_MIN_STAGE_UNITS = 52;
/** 选中框：虚线细框，与老师的聚光（实线粗框）、激光笔（小三角）只用形状与线宽区分 */
const ASK_SELECTED_WIDTH = 2;
const ASK_PAD = 4;

/** 把外接框四周补到至少 min 宽高（居中扩展），再夹在画布内 */
function hitBox(b: Box, min: number, canvas: { width: number; height: number }): Box {
  const w = Math.max(b.w + 2 * ASK_PAD, min); const hh = Math.max(b.h + 2 * ASK_PAD, min);
  const x = Math.max(0, Math.min(canvas.width - w, b.x + b.w / 2 - w / 2));
  const y = Math.max(0, Math.min(canvas.height - hh, b.y + b.h / 2 - hh / 2));
  return { x: r3(x), y: r3(y), w: r3(Math.min(w, canvas.width)), h: r3(Math.min(hh, canvas.height)) };
}

function askLayer<N>(h: CreateElement<N>, dsl: ValidatedStageDsl, board: ReturnType<typeof boardAt>, wb: Rect, ask: AskableProps): N[] {
  if (board.cursor === 0) return [];
  // 舞台单位 / 画布单位（preserveAspectRatio meet：取两向较小的缩放）
  const scale = Math.min(wb.w / dsl.canvas.width, wb.h / dsl.canvas.height);
  const min = Math.ceil(ASK_MIN_STAGE_UNITS / scale);
  const origins = originsAt(dsl, board.cursor);
  const targets: { anchor: StageAnchor; box: Box; step: number | null }[] = [
    ...board.elements.filter((e) => e.kind !== 'highlight' && origins.has(e.id))
      .map((e) => ({ anchor: { kind: 'element' as const, id: e.id }, box: bbox(e), step: origins.get(e.id)! + 1 })),
    ...(dsl.slots ?? []).map((sl) => ({ anchor: { kind: 'slot' as const, id: sl.id }, box: { x: sl.x, y: sl.y, w: sl.w, h: sl.h }, step: null })),
  ];
  const out: N[] = [];
  targets.forEach((t, i) => {
    const key = `${t.anchor.kind}:${t.anchor.id}`;
    const hb = hitBox(t.box, min, dsl.canvas);
    const choose = (): void => ask.onSelect(t.anchor);
    out.push(h('rect', {
      key: `ask-${key}`, 'data-ask': key, role: 'button', tabIndex: 0, 'aria-label': ask.label(t.anchor, t.step, i + 1),
      'aria-pressed': ask.selected?.kind === t.anchor.kind && ask.selected.id === t.anchor.id ? 'true' : 'false',
      x: hb.x, y: hb.y, width: hb.w, height: hb.h, fill: 'transparent', pointerEvents: 'all', className: 'stage-ask',
      onClick: choose,
      onKeyDown: (e: { key: string; preventDefault: () => void }) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(); } },
    }));
  });
  const sel = ask.selected;
  const hit = sel && targets.find((t) => t.anchor.kind === sel.kind && t.anchor.id === sel.id);
  if (hit) {
    const b = hit.box;
    out.push(h('rect', { key: 'ask-selected', 'data-ask-selected': `${hit.anchor.kind}:${hit.anchor.id}`, 'aria-hidden': 'true',
      x: r3(Math.max(0, b.x - ASK_PAD)), y: r3(Math.max(0, b.y - ASK_PAD)), width: r3(b.w + 2 * ASK_PAD), height: r3(b.h + 2 * ASK_PAD), rx: 4,
      fill: 'none', stroke: 'currentColor', strokeWidth: ASK_SELECTED_WIDTH, strokeDasharray: '4 4', pointerEvents: 'none' }));
  }
  return out;
}

/** 白板的元素树（纯函数；未校验的 DSL 返回 null） */
export function renderWhiteboard<N>(h: CreateElement<N>, { dsl, cursor, layout = 'desktop', className, askable }: WhiteboardProps): N | null {
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
  board.point ? pointMark(h, board.point, dsl.canvas) : null,
  // 可提问模式的点击区与选中框（缺省关闭：不输出任何东西）
  ...(askable ? askLayer(h, dsl, board, wb, askable) : [])),
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
