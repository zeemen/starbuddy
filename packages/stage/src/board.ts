// 白板状态：把前 cursor 步的动作依次折叠出“此刻白板上有什么、谁持有讲解权”。纯函数，只在内存里算，不持久化（ADR 07 决定 4）。
// 后退与跳转都是从头重算，所以任何一步的画面只取决于 DSL 与步数。

import { FONT_SIZE, type Role, type StageDsl, type StageAction } from './dsl.ts';
import { latexToText } from './latex.ts';
import { textWidthEm } from './text.ts';

export interface Box { x: number; y: number; w: number; h: number }

export type BoardElement =
  | { kind: 'text'; id: string; x: number; y: number; text: string; fontSize: number; formula: boolean }
  | { kind: 'line'; id: string; x1: number; y1: number; x2: number; y2: number; dashed: boolean }
  | { kind: 'shape'; id: string; shape: 'rect' | 'ellipse'; x: number; y: number; w: number; h: number }
  | { kind: 'highlight'; id: string; target: string; box: Box };

export interface BoardState {
  /** 已执行的步数（0 = 空白板） */
  cursor: number;
  /** 按出现先后排列（高亮在渲染时画在最底层） */
  elements: BoardElement[];
  speaker: Role | 'none';
  /** 最近一步新写上的元素 id（渲染器用来标“当前书写行”，角色不得遮挡） */
  latest: string[];
}

/** 高亮带四周留白（画布单位） */
export const HIGHLIGHT_PAD = 6;

/** 元素的外接框（文字按 textWidthEm 估算；基线下留 0.25 em） */
export function bbox(el: BoardElement): Box {
  switch (el.kind) {
    case 'text':
      return { x: el.x, y: el.y - el.fontSize, w: Math.ceil(textWidthEm(el.text) * el.fontSize), h: Math.ceil(el.fontSize * 1.25) };
    case 'line':
      return { x: Math.min(el.x1, el.x2), y: Math.min(el.y1, el.y2), w: Math.abs(el.x2 - el.x1), h: Math.abs(el.y2 - el.y1) };
    case 'shape':
      return { x: el.x, y: el.y, w: el.w, h: el.h };
    case 'highlight':
      return el.box;
  }
}

/** 高亮带：目标外接框四周加留白，再夹在画布内 */
export function highlightBox(target: Box, canvas: { width: number; height: number }): Box {
  const x = Math.max(0, target.x - HIGHLIGHT_PAD);
  const y = Math.max(0, target.y - HIGHLIGHT_PAD);
  const x2 = Math.min(canvas.width, target.x + target.w + HIGHLIGHT_PAD);
  const y2 = Math.min(canvas.height, target.y + target.h + HIGHLIGHT_PAD);
  return { x, y, w: x2 - x, h: y2 - y };
}

function apply(state: { elements: BoardElement[]; speaker: Role | 'none'; latest: string[] }, a: StageAction, canvas: StageDsl['canvas']): void {
  switch (a.type) {
    case 'write_text':
      state.elements.push({ kind: 'text', id: a.id, x: a.x, y: a.y, text: a.text, fontSize: FONT_SIZE[a.size ?? 'md'], formula: false });
      state.latest.push(a.id);
      return;
    case 'write_formula':
      state.elements.push({ kind: 'text', id: a.id, x: a.x, y: a.y, text: latexToText(a.latex) ?? '', fontSize: FONT_SIZE[a.size ?? 'md'], formula: true });
      state.latest.push(a.id);
      return;
    case 'draw_line':
      state.elements.push({ kind: 'line', id: a.id, x1: a.x1, y1: a.y1, x2: a.x2, y2: a.y2, dashed: a.dashed === true });
      state.latest.push(a.id);
      return;
    case 'draw_shape':
      state.elements.push({ kind: 'shape', id: a.id, shape: a.shape, x: a.x, y: a.y, w: a.w, h: a.h });
      state.latest.push(a.id);
      return;
    case 'highlight': {
      const target = state.elements.find((e) => e.id === a.target && e.kind !== 'highlight');
      if (target) state.elements.push({ kind: 'highlight', id: a.id, target: a.target, box: highlightBox(bbox(target), canvas) });
      return;
    }
    case 'clear': {
      if (a.targets === undefined) {
        state.elements = [];
        state.latest = [];
        return;
      }
      const gone = new Set(a.targets);
      state.elements = state.elements.filter((e) => !gone.has(e.id) && !(e.kind === 'highlight' && gone.has(e.target)));
      state.latest = state.latest.filter((id) => !gone.has(id));
      return;
    }
    case 'speaker':
      state.speaker = a.role;
      return;
  }
}

/** 前 cursor 步之后的白板（cursor 夹在 0..步数） */
export function boardAt(dsl: StageDsl, cursor: number): BoardState {
  const n = Math.max(0, Math.min(dsl.steps.length, Math.trunc(cursor)));
  const state = { elements: [] as BoardElement[], speaker: 'none' as Role | 'none', latest: [] as string[] };
  for (let i = 0; i < n; i++) {
    state.latest = [];
    for (const a of dsl.steps[i]!.actions) apply(state, a, dsl.canvas);
  }
  return { cursor: n, elements: state.elements, speaker: state.speaker, latest: state.latest };
}
