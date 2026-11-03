// 讲解 DSL v0.1.0 的类型与上限（specs/stage-whiteboard-step1；ADR 07 决定 4、7）。
// 这里只有类型与常量，没有运行时依赖；JSON Schema 见 ../schema/stage-dsl-0.1.0.schema.json（tests/schema.test.ts 核对两边一致）。
// 坐标是白板画布的逻辑单位（整数，原点在左上角，y 向下）；文字与公式的 y 是基线。
// DSL 只描述白板上画什么、谁持有讲解权，不含颜色、样式、链接、图片、脚本或任何关于学生的陈述。

export const DSL_VERSION = '0.1.0';

/** 白板上的元素 id、步骤 id：小写字母开头，最长 32 */
export const ID_RE = /^[a-z][a-z0-9_]{0,31}$/;

export const LIMITS = {
  /** 每份 DSL 的步数 */
  maxSteps: 100,
  /** 每步的动作数 */
  maxActionsPerStep: 20,
  /** 全部动作合计 */
  maxActions: 500,
  /** 单行文字（write_text 的 text，以及公式排版后的文字）按码点计 */
  maxLineChars: 60,
  /** write_formula 的 LaTeX 原文 */
  maxLatexChars: 200,
  /** 标题 */
  maxTitleChars: 40,
  /** clear 一次列出的目标数 */
  maxClearTargets: 50,
  /** 画布宽高（逻辑单位，整数） */
  canvasWidth: [320, 1920] as const,
  canvasHeight: [180, 1080] as const,
} as const;

/** 字号档位 → 画布逻辑单位 */
export const FONT_SIZE = { sm: 24, md: 32, lg: 44 } as const;
export type FontSize = keyof typeof FONT_SIZE;

export const ROLES = ['teacher', 'buddy'] as const;
export type Role = (typeof ROLES)[number];

export const ACTION_TYPES = ['write_text', 'write_formula', 'draw_line', 'draw_shape', 'highlight', 'clear', 'speaker'] as const;
export type ActionType = (typeof ACTION_TYPES)[number];

export interface WriteText { type: 'write_text'; id: string; x: number; y: number; text: string; size?: FontSize }
export interface WriteFormula { type: 'write_formula'; id: string; x: number; y: number; latex: string; size?: FontSize }
export interface DrawLine { type: 'draw_line'; id: string; x1: number; y1: number; x2: number; y2: number; dashed?: boolean }
export interface DrawShape { type: 'draw_shape'; id: string; shape: 'rect' | 'ellipse'; x: number; y: number; w: number; h: number }
/** 给已在白板上的元素加底色带；target 是元素 id */
export interface Highlight { type: 'highlight'; id: string; target: string }
/** 不带 targets：清空白板；带 targets：只擦这些元素（擦掉元素时连同指向它的高亮） */
export interface Clear { type: 'clear'; targets?: string[] }
/** 切换讲解权：主轮廓 1.25 线（视觉家族规范 v1.2 :170、:408）；none = 无人讲解 */
export interface Speaker { type: 'speaker'; role: Role | 'none' }

export type StageAction = WriteText | WriteFormula | DrawLine | DrawShape | Highlight | Clear | Speaker;

export interface StageStep { id: string; actions: StageAction[] }

export interface StageDsl {
  dsl_version: typeof DSL_VERSION;
  title: string;
  canvas: { width: number; height: number };
  /** 本节出场的角色；老师必有，点点只在 AI 同学环节出场 */
  roles: Role[];
  steps: StageStep[];
}

/** 每个动作允许的字段（白名单；validate.ts 与 schema 共用口径） */
export const ACTION_FIELDS: Record<ActionType, { required: readonly string[]; optional: readonly string[] }> = {
  write_text: { required: ['type', 'id', 'x', 'y', 'text'], optional: ['size'] },
  write_formula: { required: ['type', 'id', 'x', 'y', 'latex'], optional: ['size'] },
  draw_line: { required: ['type', 'id', 'x1', 'y1', 'x2', 'y2'], optional: ['dashed'] },
  draw_shape: { required: ['type', 'id', 'shape', 'x', 'y', 'w', 'h'], optional: [] },
  highlight: { required: ['type', 'id', 'target'], optional: [] },
  clear: { required: ['type'], optional: ['targets'] },
  speaker: { required: ['type', 'role'], optional: [] },
};

export const TOP_FIELDS = ['dsl_version', 'title', 'canvas', 'roles', 'steps'] as const;
export const STEP_FIELDS = ['id', 'actions'] as const;
export const CANVAS_FIELDS = ['width', 'height'] as const;
