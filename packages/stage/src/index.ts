// packages/stage 的对外接口（ADR 07 决定 7：其他模块只经这里使用舞台）。
// 讲解 DSL v0.1.0：类型与上限（dsl.ts）、确定性校验器（validate.ts）、白板状态折叠（board.ts）、播放状态机（player.ts）、
// 舞台三套坐标令牌（layout.ts）。React 组件在 ./Whiteboard.tsx（react 是同伴依赖；纯逻辑部分不依赖 react）。
export {
  ACTION_FIELDS, ACTION_TYPES, DSL_VERSION, FONT_SIZE, ID_RE, LIMITS, ROLES,
  type ActionType, type Clear, type DrawLine, type DrawShape, type FontSize, type Highlight, type Role, type Speaker,
  type StageAction, type StageDsl, type StageStep, type WriteFormula, type WriteText,
} from './dsl.ts';
export { isValidated, MAX_ERRORS, validateStageDsl, type DslError, type ErrorCode, type ValidatedStageDsl, type ValidationResult } from './validate.ts';
export { bbox, boardAt, HIGHLIGHT_PAD, type BoardElement, type BoardState, type Box } from './board.ts';
export { initialPlayer, step, type PlayerEvent, type PlayerState, type PlayerStatus } from './player.ts';
export {
  BUDDY_SCALE, LAYOUT_NAMES, lineUnit, MIN_WHITEBOARD_RATIO, OUTLINE_LINES, SPEAKER_OUTLINE_LINES, STAGE_LAYOUTS, TEACHER_HEIGHT_RATIO,
  type LayoutName, type Rect, type StageLayout,
} from './layout.ts';
export { latexToText, LATEX_COMMANDS, LATEX_RE } from './latex.ts';
