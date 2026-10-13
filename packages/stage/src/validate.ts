// 讲解 DSL v0.1.0 的确定性校验器（spec 范围 1）：字段白名单、步数与单行长度上限、拒绝 URL / HTML / 外链 / 脚本、坐标在画布内、
// 元素 id 唯一、高亮与擦除只指向白板上现有的元素、讲解权只给本节出场的角色。
// 合格时按白名单逐字段重建一份新对象（不带输入里的任何其他东西），深冻结并登记为“已校验”；渲染器只接收登记过的对象。
// 同一输入永远得到同一结果（错误按遍历顺序排列，不依赖时间、随机数或环境）。

import {
  ACTION_FIELDS, ACTION_TYPES, CANVAS_FIELDS, DSL_VERSION, FONT_SIZE, ID_RE, LIMITS, ROLES, STEP_FIELDS, TOP_FIELDS,
  type ActionType, type FontSize, type Role, type StageAction, type StageDsl, type StageStep,
} from './dsl.ts';
import { latexToText, LATEX_RE } from './latex.ts';
import { codePoints, textProblem, textWidthEm } from './text.ts';

export type ErrorCode =
  | 'not_object' | 'not_serializable' | 'unknown_field' | 'missing_field' | 'bad_type' | 'bad_version' | 'bad_id' | 'duplicate_id' | 'too_many'
  | 'empty' | 'too_long' | 'invisible_char' | 'html' | 'link_or_script' | 'bad_latex' | 'out_of_canvas' | 'bad_number'
  | 'unknown_target' | 'bad_role' | 'bad_enum';

export interface DslError { path: string; code: ErrorCode }

declare const VALIDATED: unique symbol;
/** 经过校验器的 DSL（类型上的标记；运行时以登记表为准，见 isValidated） */
export type ValidatedStageDsl = StageDsl & { readonly [VALIDATED]: true };

export type ValidationResult = { ok: true; dsl: ValidatedStageDsl } | { ok: false; errors: DslError[] };

/** 最多报告的错误数（超过后停止，避免畸形输入拖慢校验） */
export const MAX_ERRORS = 50;

const registry = new WeakSet<object>();

/** 是否是本进程里校验器产出的对象 */
export const isValidated = (v: unknown): v is ValidatedStageDsl => typeof v === 'object' && v !== null && registry.has(v);

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) && Object.getPrototypeOf(v) === Object.prototype;

function deepFreeze<T>(v: T): T {
  if (typeof v === 'object' && v !== null) {
    for (const x of Object.values(v)) deepFreeze(x);
    Object.freeze(v);
  }
  return v;
}

class Collector {
  errors: DslError[] = [];
  add(path: string, code: ErrorCode): void {
    if (this.errors.length < MAX_ERRORS) this.errors.push({ path, code });
  }
  get full(): boolean { return this.errors.length >= MAX_ERRORS; }
}

/** 字段白名单：多出的字段与缺的必填字段 */
function fields(c: Collector, o: Record<string, unknown>, path: string, required: readonly string[], optional: readonly string[]): boolean {
  let ok = true;
  for (const k of Object.keys(o)) {
    if (!required.includes(k) && !optional.includes(k)) { c.add(`${path}.${k}`, 'unknown_field'); ok = false; }
  }
  for (const k of required) {
    if (!Object.hasOwn(o, k)) { c.add(`${path}.${k}`, 'missing_field'); ok = false; }
  }
  return ok;
}

function int(c: Collector, v: unknown, path: string): v is number {
  if (typeof v !== 'number' || !Number.isInteger(v)) { c.add(path, 'bad_number'); return false; }
  return true;
}

function id(c: Collector, v: unknown, path: string): v is string {
  if (typeof v !== 'string' || !ID_RE.test(v)) { c.add(path, 'bad_id'); return false; }
  return true;
}

/** 文字内容：类型、非空、单行长度、不可见字符 / HTML / 链接与脚本 */
function line(c: Collector, v: unknown, path: string, max: number): v is string {
  if (typeof v !== 'string') { c.add(path, 'bad_type'); return false; }
  if (v.trim() === '') { c.add(path, 'empty'); return false; }
  if (codePoints(v) > max) { c.add(path, 'too_long'); return false; }
  const p = textProblem(v);
  if (p) { c.add(path, p); return false; }
  return true;
}

function size(c: Collector, v: unknown, path: string): v is FontSize | undefined {
  if (v === undefined) return true;
  if (typeof v !== 'string' || !Object.hasOwn(FONT_SIZE, v)) { c.add(path, 'bad_enum'); return false; }
  return true;
}

interface Scope {
  canvas: { width: number; height: number };
  roles: Role[];
  /** 全部用过的元素 id（擦掉后也不能再用） */
  used: Set<string>;
  /** 白板上现有的元素 id → 是否是高亮 */
  alive: Map<string, boolean>;
}

/** 文字落在画布内：基线 y 以上留一个字号，右边按估算宽度不越界 */
function textInCanvas(c: Collector, s: Scope, path: string, x: number, y: number, text: string, fs: number): void {
  const { width, height } = s.canvas;
  if (x < 0 || y - fs < 0 || y > height || x + Math.ceil(textWidthEm(text) * fs) > width) c.add(path, 'out_of_canvas');
}

function action(c: Collector, s: Scope, v: unknown, path: string): StageAction | null {
  if (!isRecord(v)) { c.add(path, 'not_object'); return null; }
  const type = v['type'];
  if (typeof type !== 'string' || !(ACTION_TYPES as readonly string[]).includes(type)) { c.add(`${path}.type`, 'bad_enum'); return null; }
  const t = type as ActionType;
  if (!fields(c, v, path, ACTION_FIELDS[t].required, ACTION_FIELDS[t].optional)) return null;
  const { width, height } = s.canvas;
  const newId = (): string | null => {
    const x = v['id'];
    if (!id(c, x, `${path}.id`)) return null;
    if (s.used.has(x)) { c.add(`${path}.id`, 'duplicate_id'); return null; }
    return x;
  };

  switch (t) {
    case 'write_text':
    case 'write_formula': {
      const eid = newId();
      const okX = int(c, v['x'], `${path}.x`);
      const okY = int(c, v['y'], `${path}.y`);
      const okSize = size(c, v['size'], `${path}.size`);
      let text: string | null = null;
      if (t === 'write_text') {
        if (line(c, v['text'], `${path}.text`, LIMITS.maxLineChars)) text = v['text'] as string;
      } else {
        const latex = v['latex'];
        if (typeof latex !== 'string') c.add(`${path}.latex`, 'bad_type');
        else if (codePoints(latex) > LIMITS.maxLatexChars) c.add(`${path}.latex`, 'too_long');
        else if (!LATEX_RE.test(latex)) c.add(`${path}.latex`, 'bad_latex');
        else {
          const p = textProblem(latex);
          const rendered = p ? null : latexToText(latex);
          if (p) c.add(`${path}.latex`, p);
          else if (rendered === null) c.add(`${path}.latex`, 'bad_latex');
          else if (line(c, rendered, `${path}.latex`, LIMITS.maxLineChars)) text = rendered;
        }
      }
      if (eid === null || !okX || !okY || !okSize || text === null) return null;
      const sz = v['size'] as FontSize | undefined;
      textInCanvas(c, s, path, v['x'] as number, v['y'] as number, text, FONT_SIZE[sz ?? 'md']);
      s.used.add(eid);
      s.alive.set(eid, false);
      const base = { id: eid, x: v['x'] as number, y: v['y'] as number, ...(sz !== undefined ? { size: sz } : {}) };
      return t === 'write_text'
        ? { type: 'write_text', ...base, text: v['text'] as string }
        : { type: 'write_formula', ...base, latex: v['latex'] as string };
    }
    case 'draw_line': {
      const eid = newId();
      const nums = (['x1', 'y1', 'x2', 'y2'] as const).map((k) => int(c, v[k], `${path}.${k}`));
      const dashed = v['dashed'];
      const okDashed = dashed === undefined || typeof dashed === 'boolean';
      if (!okDashed) c.add(`${path}.dashed`, 'bad_type');
      if (eid === null || !nums.every(Boolean) || !okDashed) return null;
      const [x1, y1, x2, y2] = [v['x1'], v['y1'], v['x2'], v['y2']] as number[];
      if ([x1!, x2!].some((x) => x < 0 || x > width) || [y1!, y2!].some((y) => y < 0 || y > height)) c.add(path, 'out_of_canvas');
      if (x1 === x2 && y1 === y2) c.add(path, 'empty');
      s.used.add(eid);
      s.alive.set(eid, false);
      return { type: 'draw_line', id: eid, x1: x1!, y1: y1!, x2: x2!, y2: y2!, ...(dashed !== undefined ? { dashed: dashed as boolean } : {}) };
    }
    case 'draw_shape': {
      const eid = newId();
      const shape = v['shape'];
      const okShape = shape === 'rect' || shape === 'ellipse';
      if (!okShape) c.add(`${path}.shape`, 'bad_enum');
      const nums = (['x', 'y', 'w', 'h'] as const).map((k) => int(c, v[k], `${path}.${k}`));
      if (eid === null || !okShape || !nums.every(Boolean)) return null;
      const [x, y, w, h] = [v['x'], v['y'], v['w'], v['h']] as number[];
      if (w! < 1 || h! < 1) c.add(path, 'empty');
      else if (x! < 0 || y! < 0 || x! + w! > width || y! + h! > height) c.add(path, 'out_of_canvas');
      s.used.add(eid);
      s.alive.set(eid, false);
      return { type: 'draw_shape', id: eid, shape: shape as 'rect' | 'ellipse', x: x!, y: y!, w: w!, h: h! };
    }
    case 'highlight': {
      const eid = newId();
      const target = v['target'];
      const okTarget = id(c, target, `${path}.target`);
      if (okTarget && s.alive.get(target) !== false) c.add(`${path}.target`, 'unknown_target');
      if (eid === null || !okTarget || s.alive.get(target) !== false) return null;
      s.used.add(eid);
      s.alive.set(eid, true);
      return { type: 'highlight', id: eid, target };
    }
    case 'clear': {
      const targets = v['targets'];
      if (targets === undefined) {
        s.alive.clear();
        return { type: 'clear' };
      }
      if (!Array.isArray(targets)) { c.add(`${path}.targets`, 'bad_type'); return null; }
      if (targets.length === 0) { c.add(`${path}.targets`, 'empty'); return null; }
      if (targets.length > LIMITS.maxClearTargets) { c.add(`${path}.targets`, 'too_many'); return null; }
      const out: string[] = [];
      for (const [i, x] of targets.entries()) {
        if (!id(c, x, `${path}.targets[${i}]`)) continue;
        if (!s.alive.has(x) || out.includes(x)) { c.add(`${path}.targets[${i}]`, 'unknown_target'); continue; }
        out.push(x);
      }
      if (out.length !== targets.length) return null;
      for (const x of out) {
        s.alive.delete(x);
      }
      // 擦掉元素时，指向它的高亮一起消失（与 board.ts 同一口径）
      return { type: 'clear', targets: out };
    }
    case 'speaker': {
      const role = v['role'];
      if (role !== 'none' && !(typeof role === 'string' && (s.roles as string[]).includes(role))) { c.add(`${path}.role`, 'bad_role'); return null; }
      return { type: 'speaker', role: role as Role | 'none' };
    }
  }
}

/** 高亮的目标被擦掉后，高亮本身也不在白板上了：按 board.ts 的口径同步 alive 表 */
function dropOrphanHighlights(s: Scope, highlights: Map<string, string>): void {
  for (const [hid, target] of highlights) {
    if (s.alive.get(hid) === true && !s.alive.has(target)) s.alive.delete(hid);
  }
}

const UNSERIALIZABLE = Symbol('unserializable');

/**
 * 纯数据快照：JSON 序列化再解析。getter、Proxy 与 toJSON 只在这里各被调用一次，之后的全部检查与重建只读快照，
 * 不会出现“检查时一个值、重建时另一个值”。不可序列化的输入（循环引用、BigInt、抛错的 getter 等）返回 UNSERIALIZABLE。
 */
function snapshot(raw: unknown): unknown {
  try {
    const s = JSON.stringify(raw);
    return s === undefined ? UNSERIALIZABLE : JSON.parse(s);
  } catch {
    return UNSERIALIZABLE;
  }
}

/** 校验一份讲解 DSL。合格时返回重建、冻结并登记过的副本 */
export function validateStageDsl(raw: unknown): ValidationResult {
  const c = new Collector();
  // 顶层先按原对象判断“是不是普通对象”（类实例、Map 等直接拒绝），再取快照
  if (!isRecord(raw)) return { ok: false, errors: [{ path: '$', code: 'not_object' }] };
  const input = snapshot(raw);
  if (input === UNSERIALIZABLE) return { ok: false, errors: [{ path: '$', code: 'not_serializable' }] };
  if (!isRecord(input)) return { ok: false, errors: [{ path: '$', code: 'not_object' }] };
  if (!fields(c, input, '$', TOP_FIELDS, [])) return { ok: false, errors: c.errors };

  if (input['dsl_version'] !== DSL_VERSION) c.add('$.dsl_version', 'bad_version');
  line(c, input['title'], '$.title', LIMITS.maxTitleChars);

  const canvas = input['canvas'];
  let cw = 0; let ch = 0;
  if (!isRecord(canvas)) c.add('$.canvas', 'not_object');
  else if (fields(c, canvas, '$.canvas', CANVAS_FIELDS, [])) {
    const [wMin, wMax] = LIMITS.canvasWidth; const [hMin, hMax] = LIMITS.canvasHeight;
    if (int(c, canvas['width'], '$.canvas.width')) {
      if (canvas['width'] < wMin || canvas['width'] > wMax) c.add('$.canvas.width', 'bad_number'); else cw = canvas['width'];
    }
    if (int(c, canvas['height'], '$.canvas.height')) {
      if (canvas['height'] < hMin || canvas['height'] > hMax) c.add('$.canvas.height', 'bad_number'); else ch = canvas['height'];
    }
  }

  const roles = input['roles'];
  let okRoles = false;
  if (!Array.isArray(roles) || roles.length === 0 || roles.length > ROLES.length) c.add('$.roles', 'bad_role');
  else if (!roles.every((r) => typeof r === 'string' && (ROLES as readonly string[]).includes(r)) || new Set(roles).size !== roles.length
    || !roles.includes('teacher')) c.add('$.roles', 'bad_role');
  else okRoles = true;

  const steps = input['steps'];
  if (!Array.isArray(steps)) { c.add('$.steps', 'bad_type'); return { ok: false, errors: c.errors }; }
  if (steps.length === 0) c.add('$.steps', 'empty');
  if (steps.length > LIMITS.maxSteps) { c.add('$.steps', 'too_many'); return { ok: false, errors: c.errors }; }
  const totalActions = steps.reduce<number>((n, st) => n + (isRecord(st) && Array.isArray(st['actions']) ? st['actions'].length : 0), 0);
  if (totalActions > LIMITS.maxActions) { c.add('$.steps', 'too_many'); return { ok: false, errors: c.errors }; }
  // 画布或角色不合格时，坐标与讲解权无从核对：先报这些错误
  if (cw === 0 || ch === 0 || !okRoles) return { ok: false, errors: c.errors };

  const scope: Scope = { canvas: { width: cw, height: ch }, roles: roles as Role[], used: new Set(), alive: new Map() };
  const highlights = new Map<string, string>();
  const stepIds = new Set<string>();
  const outSteps: StageStep[] = [];
  for (const [i, st] of steps.entries()) {
    if (c.full) break;
    const path = `$.steps[${i}]`;
    if (!isRecord(st)) { c.add(path, 'not_object'); continue; }
    if (!fields(c, st, path, STEP_FIELDS, [])) continue;
    if (id(c, st['id'], `${path}.id`)) {
      if (stepIds.has(st['id'])) c.add(`${path}.id`, 'duplicate_id');
      stepIds.add(st['id']);
    }
    const actions = st['actions'];
    if (!Array.isArray(actions)) { c.add(`${path}.actions`, 'bad_type'); continue; }
    if (actions.length === 0) { c.add(`${path}.actions`, 'empty'); continue; }
    if (actions.length > LIMITS.maxActionsPerStep) { c.add(`${path}.actions`, 'too_many'); continue; }
    const out: StageAction[] = [];
    for (const [j, a] of actions.entries()) {
      if (c.full) break;
      const r = action(c, scope, a, `${path}.actions[${j}]`);
      if (r?.type === 'highlight') highlights.set(r.id, r.target);
      if (r?.type === 'clear') dropOrphanHighlights(scope, highlights);
      if (r) out.push(r);
    }
    outSteps.push({ id: st['id'] as string, actions: out });
  }
  if (c.errors.length > 0) return { ok: false, errors: c.errors };

  const dsl: StageDsl = {
    dsl_version: DSL_VERSION,
    title: input['title'] as string,
    canvas: { width: cw, height: ch },
    roles: [...(roles as Role[])],
    steps: outSteps,
  };
  deepFreeze(dsl);
  registry.add(dsl);
  return { ok: true, dsl: dsl as ValidatedStageDsl };
}
