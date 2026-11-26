// 讲解 DSL v0.3.0 的类型与上限（specs/stage-whiteboard-step1、specs/stage-dsl-v0.2；ADR 07 决定 4、7）。
// 这里只有类型与常量，没有运行时依赖；JSON Schema 见 ../schema/stage-dsl-0.3.0.schema.json（tests/schema.test.ts 核对两边一致）。
// v0.3（specs/stage-point-to-ask-answer PR ②）：DSL 本身的结构不变，只是台词稿多了预制答疑 answers（见 script.ts），两者共用一个版本号。
// v0.2：每步可引用一句台词（say，台词正文在单独的台词稿里，见 script.ts）；narrated 为真时“有板书的步骤必须有台词”；
// 新动作 point（聚光 / 激光笔）指向白板元素或文档声明的槽位（题图、学生作答切片；内容运行时在内存填充，DSL 不含学生数据）。
// 坐标是白板画布的逻辑单位（整数，原点在左上角，y 向下）；文字与公式的 y 是基线。
// DSL 只描述白板上画什么、谁持有讲解权，不含颜色、样式、链接、图片、脚本或任何关于学生的陈述。

export const DSL_VERSION = '0.3.0';

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
  /** 槽位数（题图、学生作答切片） */
  maxSlots: 4,
  /** 画布宽高（逻辑单位，整数） */
  canvasWidth: [320, 1920] as const,
  canvasHeight: [180, 1080] as const,
} as const;

/** 字号档位 → 画布逻辑单位 */
export const FONT_SIZE = { sm: 24, md: 32, lg: 44 } as const;
export type FontSize = keyof typeof FONT_SIZE;

export const ROLES = ['teacher', 'buddy'] as const;
export type Role = (typeof ROLES)[number];

export const ACTION_TYPES = ['write_text', 'write_formula', 'draw_line', 'draw_shape', 'highlight', 'clear', 'speaker', 'point'] as const;
/** 会在白板上留下书写或作图的动作：narrated 为真时，带这些动作的步骤必须有台词（“说 / 画”配对） */
export const BOARD_WRITE_TYPES = ['write_text', 'write_formula', 'draw_line', 'draw_shape'] as const;
/** 指向的方式：spotlight 一整块（目标外接框加粗描边）、laser 单个元素（目标左侧的小三角）；只用形状与线宽，不靠颜色 */
export const POINT_MODES = ['spotlight', 'laser'] as const;
export type PointMode = (typeof POINT_MODES)[number];
/** 槽位类型：题图、学生作答切片（DSL 只声明区域，内容运行时在内存填充） */
export const SLOT_KINDS = ['item_figure', 'student_answer'] as const;
export type SlotKind = (typeof SLOT_KINDS)[number];
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

/** 指向：target 是白板上现存的元素（不含高亮）或文档声明的槽位；指着它直到这一步的台词说完；一步最多一个，且这一步必须有台词 */
export interface Point { type: 'point'; target: string; mode: PointMode }

export type StageAction = WriteText | WriteFormula | DrawLine | DrawShape | Highlight | Clear | Speaker | Point;

/** say：这一步说的台词编号（台词正文在台词稿里）；只在 narrated 为真时允许。有台词时 actions 可以为空（只说不写） */
export interface StageStep { id: string; actions: StageAction[]; say?: string }

/** 槽位：白板画布上的一块区域，渲染为空框；内容（题图、学生作答切片）由调用方运行时在内存里填充 */
export interface Slot { id: string; kind: SlotKind; x: number; y: number; w: number; h: number }

export interface StageDsl {
  dsl_version: typeof DSL_VERSION;
  title: string;
  canvas: { width: number; height: number };
  /** 本节出场的角色；老师必有，点点只在 AI 同学环节出场 */
  roles: Role[];
  /** 有台词的讲解（true）还是无声板书（false，与 v0.1 相同；v0.1 文档迁移过来就是 false） */
  narrated: boolean;
  slots?: Slot[];
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
  point: { required: ['type', 'target', 'mode'], optional: [] },
};

export const TOP_FIELDS = ['dsl_version', 'title', 'canvas', 'roles', 'narrated', 'steps'] as const;
export const TOP_OPTIONAL = ['slots'] as const;
export const STEP_FIELDS = ['id', 'actions'] as const;
export const STEP_OPTIONAL = ['say'] as const;
export const SLOT_FIELDS = ['id', 'kind', 'x', 'y', 'w', 'h'] as const;
export const CANVAS_FIELDS = ['width', 'height'] as const;
