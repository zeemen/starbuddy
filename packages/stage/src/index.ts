// packages/stage 的对外接口（ADR 07 决定 7：其他模块只经这里使用舞台）。
// 讲解 DSL v0.2.0：台词稿（script.ts）、类型与上限（dsl.ts）、确定性校验器（validate.ts）、白板状态折叠（board.ts）、播放状态机（player.ts）、
// 舞台三套坐标令牌（layout.ts）、版本规则与迁移阶梯（version.ts，specs/stage-dsl-versioning）、“指着元素提问”的提问对象（question.ts，specs/stage-point-to-ask）。React 组件在 ./Whiteboard.tsx（react 是同伴依赖；纯逻辑部分不依赖 react）。
export {
  ACTION_FIELDS, ACTION_TYPES, BOARD_WRITE_TYPES, DSL_VERSION, FONT_SIZE, ID_RE, LIMITS, POINT_MODES, ROLES, SLOT_KINDS,
  type ActionType, type Clear, type DrawLine, type DrawShape, type FontSize, type Highlight, type Point, type PointMode, type Role, type Slot,
  type SlotKind, type Speaker, type StageAction, type StageDsl, type StageStep, type WriteFormula, type WriteText,
} from './dsl.ts';
export {
  captionAt, isValidatedScript, LINE_FIELDS, lineProblem, SCRIPT_FIELDS, SCRIPT_LIMITS, validateStagePair,
  type PairResult, type ScriptLine, type StageScript, type ValidatedStageScript,
} from './script.ts';
export { isValidated, MAX_ERRORS, validateStageDsl, type DslError, type ErrorCode, type ValidatedStageDsl, type ValidationResult } from './validate.ts';
export { bbox, boardAt, HIGHLIGHT_PAD, type BoardElement, type BoardState, type Box } from './board.ts';
export { initialPlayer, step, type PlayerEvent, type PlayerState, type PlayerStatus } from './player.ts';
export {
  BUDDY_SCALE, LAYOUT_NAMES, lineUnit, MIN_WHITEBOARD_RATIO, OUTLINE_LINES, SPEAKER_OUTLINE_LINES, STAGE_LAYOUTS, TEACHER_HEIGHT_RATIO,
  type LayoutName, type Rect, type StageLayout,
} from './layout.ts';
export { latexToText, LATEX_COMMANDS, LATEX_RE } from './latex.ts';
export {
  compareVersions, DSL_MIGRATIONS, dslTexts, formatSpec, ladderProblems, migrateStageDsl, TEXT_KEYS, type DslMigration, type MigrationResult,
} from './version.ts';
export {
  ANCHOR_FIELDS, ANCHOR_KINDS, askableAt, originsAt, QUESTION_FIELDS, QUESTION_INTENTS, replayCursor, validateStageQuestion,
  type AnchorKind, type QuestionError, type QuestionErrorCode, type QuestionIntent, type QuestionResult, type StageAnchor, type StageQuestion,
} from './question.ts';
