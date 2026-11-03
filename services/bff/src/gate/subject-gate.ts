// 分学科并发闸门（PLT-16 第一步，specs/subject-concurrency-plt16/spec.md 范围第 1–4 条）：进程内信号量 + 有界 FIFO 队列 + 排队超时，
// 按学习服务的学科码（aya / zhixing / wenli）各自独立计数，一科堆积不占别的科的名额；另按学生限每科“在处理 + 排队”的个数
// （per_student_max，审查安全阻断：单个学生不能占满一科），超出直接拒、不进公共队列。学生 ID 只作进程内计数的键，不进日志、不进计数出口。
//
// 开关 STARBUDDY_SUBJECT_GATE=subject_gate，缺省关：关时不读配置、不建闸门（ShellDeps.subjectGate 为空），各接入点直接执行原来的代码，
// 响应与访问日志逐字节不变。dev / test 以外须在配置里显式声明 single_instance: true 才建闸门，否则不建并告警（同 rest.singleInstance 先例）。
//
// 接入点（src/diagnosis/service.ts、src/practice/service.ts、src/retest/service.ts）：排队与拒绝判断只在计量之前；已入闸请求持有名额直到处理结束
// （无论成功、失败、抛错、超时都在 finally 里释放）。被拒（本学生超额、队列满、排队超时、请求方已断开）回 503 SUBJECT_BUSY 带 retry-after：
// 本次请求没有新增计量；之前已计次或已预留的用同一请求重试不再计——这类重试（再练同票据已计次、复测已计次、诊断开始同幂等键重放）绕过闸门，不会被拒。
// 请求方断开（AbortSignal）时排队中的立即离队；入闸后、计量前再查一次，已断开就释放名额不再执行。
//
// 观测：经 ctx.note 记学科码、闸门结果（admitted / queued / rejected / bypassed）、排队毫秒分档、被拒原因（full / timeout / per_student / aborted），
// 按学科累加计数（stats()）；不含学生 ID、账号与任何请求内容。
// 配置 config/concurrency.json（照 config/shell.json 的约定：_doc、loadXxxConfig、上下界校验、只有 dev / test 可经 BFF_CONCURRENCY_CONFIG 换文件）。
// 数值是合成默认值，待人确认；多实例部署前须改为共享状态或按实例比例配置（spec 待人确认 #3）。
import { readFileSync } from 'node:fs';
import { isLocalEnv, type RunEnv } from '../http/config.ts';

export const SUBJECT_GATE_FEATURE = 'subject_gate';
/** 闸门按这三个学习服务学科码计数（诊断按 aya；再练、复测按前端学科码换成的学习服务学科码） */
export const GATE_SUBJECTS = ['aya', 'zhixing', 'wenli'] as const;
export type GateSubject = (typeof GATE_SUBJECTS)[number];

export interface SubjectLimits { maxInflight: number; maxQueue: number; queueTimeoutMs: number; retryAfterSec: number; perStudentMax: number }
export interface ConcurrencyConfig {
  subjects: Readonly<Record<GateSubject, SubjectLimits>>;
  /** 闸门状态在进程内：dev / test 以外须显式声明单实例部署才建闸门（缺省 false） */
  singleInstance?: boolean;
}

export const CONCURRENCY_CONFIG_PATH = new URL('../../config/concurrency.json', import.meta.url);

const int = (v: unknown, lo: number, hi: number, what: string): number => {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < lo || v > hi) throw new Error(`concurrency.json：${what} 应为 ${lo}–${hi} 的整数`);
  return v;
};

/** 读并校验 config/concurrency.json：三科都要有、不认识的学科与字段拒绝（写错时启动就失败） */
export function loadConcurrencyConfig(path: URL | string = CONCURRENCY_CONFIG_PATH): ConcurrencyConfig {
  const raw = JSON.parse(readFileSync(path, 'utf8')) as Record<string, any>;
  const subjects = raw['subjects'];
  if (!subjects || typeof subjects !== 'object' || Array.isArray(subjects)) throw new Error('concurrency.json：subjects 应为对象');
  for (const k of Object.keys(subjects)) {
    if (!(GATE_SUBJECTS as readonly string[]).includes(k)) throw new Error(`concurrency.json：不认识的学科码 ${k}`);
  }
  if (typeof raw['single_instance'] !== 'boolean') throw new Error('concurrency.json：single_instance 应为布尔');
  const FIELDS = ['max_inflight', 'max_queue', 'queue_timeout_ms', 'retry_after_s', 'per_student_max'];
  const out = {} as Record<GateSubject, SubjectLimits>;
  for (const s of GATE_SUBJECTS) {
    const v = subjects[s];
    if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error(`concurrency.json：缺少学科 ${s}`);
    for (const k of Object.keys(v)) if (!FIELDS.includes(k)) throw new Error(`concurrency.json：${s}.${k} 不是认识的字段`);
    out[s] = { maxInflight: int(v.max_inflight, 1, 1000, `${s}.max_inflight`), maxQueue: int(v.max_queue, 0, 10_000, `${s}.max_queue`),
               queueTimeoutMs: int(v.queue_timeout_ms, 10, 10_000, `${s}.queue_timeout_ms`),
               retryAfterSec: int(v.retry_after_s, 1, 300, `${s}.retry_after_s`),
               perStudentMax: int(v.per_student_max, 1, 100, `${s}.per_student_max`) };
  }
  return { subjects: out, singleInstance: raw['single_instance'] as boolean };
}

/** 解析开关；不认识的取值抛错（调用方拒绝启动），没设则关闭 */
export function parseSubjectGateFeature(_env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (raw.trim() !== SUBJECT_GATE_FEATURE) throw new Error(`STARBUDDY_SUBJECT_GATE 只认 ${SUBJECT_GATE_FEATURE}`);
  return true;
}

/** 开关打开后建不建闸门、启动时告警什么：dev / test 照建；其余环境须配置 single_instance: true，否则不建（同 rest.singleInstance 先例） */
export function decideSubjectGate(env: RunEnv, config: ConcurrencyConfig): { build: boolean; warning: Record<string, string> } {
  if (!isLocalEnv(env) && config.singleInstance !== true) {
    return { build: false, warning: { event: 'subject_gate_disabled', reason: 'in_process_state_requires_single_instance' } };
  }
  return { build: true, warning: { event: 'subject_gate_in_process', reason: 'in_process_state_requires_single_instance' } };
}

// ---------------------------------------------------------------- 闸门

export type RejectReason = 'full' | 'timeout' | 'per_student' | 'aborted';
export type GateResult =
  | { ok: true; result: 'admitted' | 'queued'; waitedMs: number; release: () => void }
  | { ok: false; reason: RejectReason; waitedMs: number; retryAfterSec: number };

export interface LaneStats {
  inflight: number; waiting: number; admitted: number; queued: number;
  rejected_full: number; rejected_timeout: number; rejected_per_student: number; rejected_aborted: number;
}

export interface AcquireOptions {
  /** 学生 ID：只作进程内“每生每科在处理 + 排队”计数的键，不记日志、不进 stats() */
  student: string;
  /** 请求方断开时触发：排队中的立即离队 */
  signal?: AbortSignal;
}

export interface SubjectGate {
  /** 取一个名额：本学生在这一科已到上限直接拒；有空位立即放行；没有空位且队列未满就排队（先到先得），超时或请求方断开按被拒；队列满直接拒 */
  acquire(subject: string, opts: AcquireOptions): Promise<GateResult>;
  /** 按学科的累加计数与当前占用（只有计数，不含任何请求或学生信息） */
  stats(): Record<GateSubject, LaneStats>;
}

/** 测试注入：单调时钟与定时器 */
export interface GateClock {
  now: () => number;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (t: unknown) => void;
}

const REAL_CLOCK: GateClock = {
  now: () => performance.now(),
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
};

interface Waiter { grant: () => void }
interface Lane {
  limits: SubjectLimits; inflight: number; waiting: Waiter[]; perStudent: Map<string, number>;
  counts: Omit<LaneStats, 'inflight' | 'waiting'>;
}

export function createSubjectGate(config: ConcurrencyConfig, clock: GateClock = REAL_CLOCK): SubjectGate {
  const lanes = new Map<string, Lane>();
  for (const s of GATE_SUBJECTS) {
    lanes.set(s, { limits: config.subjects[s], inflight: 0, waiting: [], perStudent: new Map(),
                   counts: { admitted: 0, queued: 0, rejected_full: 0, rejected_timeout: 0, rejected_per_student: 0, rejected_aborted: 0 } });
  }

  /** 本学生在这一科的“在处理 + 排队”计数减一（到 0 就删掉键，不留学生 ID） */
  const leave = (lane: Lane, student: string): void => {
    const n = (lane.perStudent.get(student) ?? 1) - 1;
    if (n <= 0) lane.perStudent.delete(student);
    else lane.perStudent.set(student, n);
  };

  /** 每个名额只释放一次（重复调用无效）；有人排队就把名额直接交给队首（占用数不变），否则空出一个位置 */
  const releaser = (lane: Lane, student: string): (() => void) => {
    let released = false;
    return () => {
      if (released) return;
      released = true;
      leave(lane, student);
      const next = lane.waiting.shift();
      if (next) next.grant();
      else lane.inflight -= 1;
    };
  };

  return {
    acquire(subject, { student, signal }) {
      const lane = lanes.get(subject);
      if (!lane) return Promise.reject(new Error(`subject gate: ${subject} is not configured`));
      const reject = (reason: RejectReason, waitedMs: number): GateResult =>
        ({ ok: false, reason, waitedMs, retryAfterSec: lane.limits.retryAfterSec });
      if (signal?.aborted) {
        lane.counts.rejected_aborted += 1;
        return Promise.resolve(reject('aborted', 0));
      }
      const mine = lane.perStudent.get(student) ?? 0;
      if (mine >= lane.limits.perStudentMax) {
        lane.counts.rejected_per_student += 1;
        return Promise.resolve(reject('per_student', 0));
      }
      if (lane.inflight < lane.limits.maxInflight && lane.waiting.length === 0) {
        lane.inflight += 1;
        lane.counts.admitted += 1;
        lane.perStudent.set(student, mine + 1);
        return Promise.resolve({ ok: true, result: 'admitted', waitedMs: 0, release: releaser(lane, student) });
      }
      if (lane.waiting.length >= lane.limits.maxQueue) {
        lane.counts.rejected_full += 1;
        return Promise.resolve(reject('full', 0));
      }
      lane.perStudent.set(student, mine + 1);
      const t0 = clock.now();
      return new Promise<GateResult>((resolve) => {
        let timer: unknown;
        /** 离队（超时或断开）：从队列里拿掉、本学生计数减一 */
        const dequeue = (): boolean => {
          const i = lane.waiting.indexOf(w);
          if (i < 0) return false;  // 已经拿到名额
          lane.waiting.splice(i, 1);
          leave(lane, student);
          return true;
        };
        const onAbort = () => {
          if (!dequeue()) return;
          clock.clearTimer(timer);
          lane.counts.rejected_aborted += 1;
          resolve(reject('aborted', Math.max(0, clock.now() - t0)));
        };
        const w: Waiter = {
          grant: () => {
            clock.clearTimer(timer);
            signal?.removeEventListener('abort', onAbort);
            lane.counts.queued += 1;
            resolve({ ok: true, result: 'queued', waitedMs: Math.max(0, clock.now() - t0), release: releaser(lane, student) });
          },
        };
        timer = clock.setTimer(() => {
          if (!dequeue()) return;
          signal?.removeEventListener('abort', onAbort);
          lane.counts.rejected_timeout += 1;
          resolve(reject('timeout', Math.max(0, clock.now() - t0)));
        }, lane.limits.queueTimeoutMs);
        signal?.addEventListener('abort', onAbort, { once: true });
        lane.waiting.push(w);
      });
    },
    stats() {
      const out = {} as Record<GateSubject, LaneStats>;
      for (const s of GATE_SUBJECTS) {
        const l = lanes.get(s)!;
        out[s] = { inflight: l.inflight, waiting: l.waiting.length, ...l.counts };
      }
      return out;
    },
  };
}

/** 排队毫秒分档（访问日志只记分档，不记精确值） */
export function queueBucket(ms: number): string {
  if (ms <= 0) return '0';
  if (ms < 100) return 'lt100';
  if (ms < 500) return 'lt500';
  if (ms < 2000) return 'lt2000';
  return 'ge2000';
}

export type GateObserve = (k: string, v: string) => void;

/** 被拒响应的头：retry-after（秒，按配置） */
export const busyHeaders = (retryAfterSec: number): Record<string, string> => ({ 'retry-after': String(retryAfterSec) });

export interface SlotRequest {
  subject: string;
  /** 学生 ID（每生每科上限的计数键；不记日志） */
  student: string;
  signal?: AbortSignal | undefined;
  observe?: GateObserve | undefined;
  /** 已经计过量的重试（再练同票据已计次、复测已计次、诊断开始同幂等键已预留）：绕过闸门，不排队、不被拒 */
  bypass?: boolean;
}

/**
 * 在名额内执行 fn。gate 为空（开关关）时直接执行 fn，什么也不记。
 * 被拒时不执行 fn，回 busy(retry-after 秒)；放行时无论 fn 成功、失败还是抛错，都在 finally 里释放名额。
 * 入闸之后、执行 fn（其中才有计量）之前再查一次请求方是否已断开：已断开就释放名额、不执行。
 * 调用方保证：本函数只在计量调用之前进入（fn 里才有计量），所以被拒的请求本次没有新增计量。
 */
export async function withSubjectSlot<R>(gate: SubjectGate | undefined, req: SlotRequest,
                                         busy: (retryAfterSec: number) => R, fn: () => Promise<R>): Promise<R> {
  if (gate === undefined) return fn();
  const observe = req.observe;
  observe?.('gate_subject', req.subject);
  if (req.bypass === true) {
    observe?.('gate', 'bypassed');
    return fn();
  }
  const g = await gate.acquire(req.subject, { student: req.student, ...(req.signal ? { signal: req.signal } : {}) });
  if (g.ok && req.signal?.aborted) {
    g.release();
    observe?.('gate', 'rejected');
    observe?.('gate_queue_ms', queueBucket(g.waitedMs));
    observe?.('gate_reason', 'aborted');
    return busy(1);
  }
  observe?.('gate', g.ok ? g.result : 'rejected');
  observe?.('gate_queue_ms', queueBucket(g.waitedMs));
  if (!g.ok) {
    observe?.('gate_reason', g.reason);
    return busy(g.retryAfterSec);
  }
  try {
    return await fn();
  } finally {
    g.release();
  }
}
