// 讲解台词稿（specs/stage-dsl-v0.2）：台词与板书分开存。DSL 的步骤只引用台词编号（say），台词正文在这份稿里；
// 板书由教研审、台词由人设审，互不改写（CLAUDE.md 规则 3、7）。本文件只做确定性的格式检查，不替代人设审核：
//   - 稿：{ dsl_version, lines: [{ id, role, text }] }，版本与 DSL 相同；
//   - 每句 1–60 个码点、最多两个句末标点（。！？!?），与板书文字同一套安全检查（不可见字符、HTML、链接与脚本）；
//   - validateStagePair：DSL 已校验、稿合格，且引用的台词都在、没有多余台词、每句台词的角色等于说这句时持有讲解权的角色。
// 合格时返回重建、冻结并登记过的稿；captionAt 只从登记过的稿里取字幕，组件不拼接任何文字。
// v0.3（specs/stage-point-to-ask-answer PR ②）：稿可以带预制答疑 answers: [{ step, intent, line }]——学生指着某一步问某种问题时，
//   回答只能是稿里审过的那句台词（不调模型）；找不到就不答。validateStagePair 另核对：只在有台词的讲解里出现；步骤存在；
//   问法是三种之一（“再讲一遍”在前端本地处理）；同一步同一问法只有一句；答疑台词在稿里、不兼作字幕台词、一句只答一处；
//   答疑台词的角色等于这一步持有讲解权的角色；文字与字幕台词同一套长度、句数与安全检查。answerFor 只从登记过的稿里取。
import { DSL_VERSION, ID_RE, ROLES, type Role } from './dsl.ts';
import { QUESTION_INTENTS, type QuestionIntent } from './question.ts';
import { codePoints, textProblem } from './text.ts';
import { isValidated, type DslError, type ErrorCode, type ValidatedStageDsl } from './validate.ts';

export const SCRIPT_LIMITS = {
  /** 每份稿的台词数 */
  maxLines: 100,
  /** 每句台词按码点计 */
  maxSayChars: 60,
  /** 每句最多几个句末标点（“一两句短句”） */
  maxSentences: 2,
  /** 每份稿的预制答疑条数（v0.3） */
  maxAnswers: 100,
} as const;

export const SCRIPT_FIELDS = ['dsl_version', 'lines'] as const;
/** v0.3：可选的预制答疑 */
export const SCRIPT_OPTIONAL = ['answers'] as const;
export const LINE_FIELDS = ['id', 'role', 'text'] as const;
export const ANSWER_FIELDS = ['step', 'intent', 'line'] as const;
/** 预制答疑的问法：提问的三种（“再讲一遍”在前端本地重放，不需要答疑） */
export type AnswerIntent = Exclude<QuestionIntent, 'again'>;
export const ANSWER_INTENTS: readonly AnswerIntent[] = Object.freeze(QUESTION_INTENTS.filter((i): i is AnswerIntent => i !== 'again'));
const SENTENCE_END = /[。！？!?]/gu;

export interface ScriptLine { id: string; role: Role; text: string }
/** 一条预制答疑：第 step 步被问 intent 时，回答是台词 line */
export interface ScriptAnswer { step: string; intent: AnswerIntent; line: string }
export interface StageScript { dsl_version: typeof DSL_VERSION; lines: ScriptLine[]; answers?: ScriptAnswer[] }

declare const VALIDATED_SCRIPT: unique symbol;
export type ValidatedStageScript = StageScript & { readonly [VALIDATED_SCRIPT]: true };
export type PairResult = { ok: true; script: ValidatedStageScript } | { ok: false; errors: DslError[] };

const registry = new WeakSet<object>();
export const isValidatedScript = (v: unknown): v is ValidatedStageScript => typeof v === 'object' && v !== null && registry.has(v);

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) && Object.getPrototypeOf(v) === Object.prototype;

function deepFreeze<T>(v: T): T {
  if (typeof v === 'object' && v !== null) {
    for (const x of Object.values(v)) deepFreeze(x);
    Object.freeze(v);
  }
  return v;
}

function onlyFields(errors: DslError[], o: Record<string, unknown>, path: string, keys: readonly string[], optional: readonly string[] = []): boolean {
  let ok = true;
  for (const k of Object.keys(o)) if (!keys.includes(k) && !optional.includes(k)) { errors.push({ path: `${path}.${k}`, code: 'unknown_field' }); ok = false; }
  for (const k of keys) if (!Object.hasOwn(o, k)) { errors.push({ path: `${path}.${k}`, code: 'missing_field' }); ok = false; }
  return ok;
}

/** 一句台词的格式问题（null = 合格） */
export function lineProblem(text: unknown): ErrorCode | null {
  if (typeof text !== 'string') return 'bad_type';
  if (text.trim() === '') return 'empty';
  if (codePoints(text) > SCRIPT_LIMITS.maxSayChars) return 'too_long';
  const p = textProblem(text);
  if (p) return p;
  if ((text.match(SENTENCE_END) ?? []).length > SCRIPT_LIMITS.maxSentences) return 'too_many_sentences';
  return null;
}

/** 每一步说话时持有讲解权的角色（执行完这一步的 speaker 动作之后） */
function speakersBySay(dsl: ValidatedStageDsl): Map<string, Role | 'none'> {
  const out = new Map<string, Role | 'none'>();
  let speaker: Role | 'none' = 'none';
  for (const st of dsl.steps) {
    for (const a of st.actions) if (a.type === 'speaker') speaker = a.role;
    if (st.say !== undefined) out.set(st.say, speaker);
  }
  return out;
}

/** 每一步执行完之后持有讲解权的角色（步骤 id → 角色）：预制答疑由这一步讲话的人来答 */
function speakersByStep(dsl: ValidatedStageDsl): Map<string, Role | 'none'> {
  const out = new Map<string, Role | 'none'>();
  let speaker: Role | 'none' = 'none';
  for (const st of dsl.steps) {
    for (const a of st.actions) if (a.type === 'speaker') speaker = a.role;
    out.set(st.id, speaker);
  }
  return out;
}

/** 核对预制答疑的结构；返回 答疑台词 id → 该步讲话的角色（只收结构合格的条目） */
function checkAnswers(dsl: ValidatedStageDsl, raw: unknown, errors: DslError[]): { byLine: Map<string, Role | 'none'>; answers: ScriptAnswer[] } | null {
  const byLine = new Map<string, Role | 'none'>();
  const answers: ScriptAnswer[] = [];
  if (!Array.isArray(raw)) { errors.push({ path: '$.answers', code: 'bad_type' }); return null; }
  if (raw.length > SCRIPT_LIMITS.maxAnswers) { errors.push({ path: '$.answers', code: 'too_many' }); return null; }
  if (raw.length > 0 && !dsl.narrated) { errors.push({ path: '$.answers', code: 'not_narrated' }); return null; }
  const steps = speakersByStep(dsl);
  const pairs = new Set<string>();
  for (const [i, a] of raw.entries()) {
    const path = `$.answers[${i}]`;
    if (!isRecord(a)) { errors.push({ path, code: 'not_object' }); continue; }
    if (!onlyFields(errors, a, path, ANSWER_FIELDS)) continue;
    const { step, intent, line } = a;
    if (typeof step !== 'string' || !steps.has(step)) { errors.push({ path: `${path}.step`, code: 'unknown_target' }); continue; }
    if (typeof intent !== 'string' || !(ANSWER_INTENTS as readonly string[]).includes(intent)) { errors.push({ path: `${path}.intent`, code: 'bad_enum' }); continue; }
    if (typeof line !== 'string' || !ID_RE.test(line)) { errors.push({ path: `${path}.line`, code: 'bad_id' }); continue; }
    if (pairs.has(`${step} ${intent}`)) { errors.push({ path, code: 'duplicate_id' }); continue; }   // 同一步同一问法只有一句
    if (byLine.has(line)) { errors.push({ path: `${path}.line`, code: 'duplicate_id' }); continue; } // 一句只答一处
    pairs.add(`${step} ${intent}`);
    byLine.set(line, steps.get(step)!);
    answers.push({ step, intent: intent as AnswerIntent, line });
  }
  return { byLine, answers };
}

/** 核对一份已校验的 DSL 与它的台词稿。DSL 必须是校验器产出的对象；稿合格时返回重建、冻结、登记过的副本 */
export function validateStagePair(dsl: ValidatedStageDsl, raw: unknown): PairResult {
  const errors: DslError[] = [];
  if (!isValidated(dsl)) return { ok: false, errors: [{ path: '$dsl', code: 'not_object' }] };
  let input: unknown;
  try { input = JSON.parse(JSON.stringify(raw) ?? 'null'); } catch { return { ok: false, errors: [{ path: '$', code: 'not_serializable' }] }; }
  if (!isRecord(input) || !isRecord(raw)) return { ok: false, errors: [{ path: '$', code: 'not_object' }] };
  if (!onlyFields(errors, input, '$', SCRIPT_FIELDS, SCRIPT_OPTIONAL)) return { ok: false, errors };
  if (input['dsl_version'] !== dsl.dsl_version) errors.push({ path: '$.dsl_version', code: 'bad_version' });
  const lines = input['lines'];
  if (!Array.isArray(lines)) return { ok: false, errors: [...errors, { path: '$.lines', code: 'bad_type' }] };
  if (lines.length > SCRIPT_LIMITS.maxLines) return { ok: false, errors: [...errors, { path: '$.lines', code: 'too_many' }] };
  const speakers = speakersBySay(dsl);
  const checked = Object.hasOwn(input, 'answers') ? checkAnswers(dsl, input['answers'], errors) : { byLine: new Map<string, Role | 'none'>(), answers: [] };
  if (checked === null) return { ok: false, errors };
  for (const id of checked.byLine.keys()) {
    if (speakers.has(id)) errors.push({ path: '$.answers', code: 'duplicate_id' });   // 答疑台词不兼作字幕台词
  }
  const seen = new Set<string>();
  const out: ScriptLine[] = [];
  for (const [i, l] of lines.entries()) {
    const path = `$.lines[${i}]`;
    if (!isRecord(l)) { errors.push({ path, code: 'not_object' }); continue; }
    if (!onlyFields(errors, l, path, LINE_FIELDS)) continue;
    const { id, role, text } = l;
    if (typeof id !== 'string' || !ID_RE.test(id)) { errors.push({ path: `${path}.id`, code: 'bad_id' }); continue; }
    if (seen.has(id)) { errors.push({ path: `${path}.id`, code: 'duplicate_id' }); continue; }
    seen.add(id);
    if (!speakers.has(id) && !checked.byLine.has(id)) { errors.push({ path: `${path}.id`, code: 'unused_line' }); continue; }
    if (typeof role !== 'string' || !(ROLES as readonly string[]).includes(role) || !dsl.roles.includes(role as Role)) {
      errors.push({ path: `${path}.role`, code: 'bad_role' }); continue;
    }
    // 说这句时，讲解权必须在这句台词的角色手里（不在任何人手里也不行）；答疑台词由那一步讲话的人来答
    if ((speakers.get(id) ?? checked.byLine.get(id)) !== role) { errors.push({ path: `${path}.role`, code: 'bad_role' }); continue; }
    const p = lineProblem(text);
    if (p) { errors.push({ path: `${path}.text`, code: p }); continue; }
    out.push({ id, role: role as Role, text: text as string });
  }
  for (const say of speakers.keys()) if (!seen.has(say)) errors.push({ path: '$.lines', code: 'unknown_line' });
  for (const [i, a] of checked.answers.entries()) if (!seen.has(a.line)) errors.push({ path: `$.answers[${i}].line`, code: 'unknown_line' });
  if (errors.length > 0) return { ok: false, errors: errors.slice(0, 50) };
  const script: StageScript = { dsl_version: dsl.dsl_version, lines: out, ...(Object.hasOwn(input, 'answers') ? { answers: checked.answers } : {}) };
  deepFreeze(script);
  registry.add(script);
  return { ok: true, script: script as ValidatedStageScript };
}

/** 第 cursor 步（1 起）的字幕：只从登记过的稿里按台词编号取；没有台词或稿未校验时为 null */
export function captionAt(dsl: ValidatedStageDsl, script: ValidatedStageScript | null, cursor: number): ScriptLine | null {
  if (!isValidated(dsl) || !script || !isValidatedScript(script)) return null;
  const n = Math.trunc(cursor);
  if (n < 1 || n > dsl.steps.length) return null;
  const say = dsl.steps[n - 1]!.say;
  return say === undefined ? null : script.lines.find((l) => l.id === say) ?? null;
}

/**
 * 预制答疑（v0.3）：学生在第 stepId 步问 intent 时的回答，只从登记过的稿里按步骤与问法取；没有这一条、稿未校验、
 * 或问的是“再讲一遍”时为 null（调用方给兜底，不由组件拼接文字）
 */
export function answerFor(dsl: ValidatedStageDsl, script: ValidatedStageScript | null, stepId: string, intent: QuestionIntent): ScriptLine | null {
  if (!isValidated(dsl) || !script || !isValidatedScript(script) || intent === 'again') return null;
  const a = script.answers?.find((x) => x.step === stepId && x.intent === intent);
  return a ? script.lines.find((l) => l.id === a.line) ?? null : null;
}
