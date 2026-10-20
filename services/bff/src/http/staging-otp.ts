// staging 白名单固定码 OTP（LT-01 决定记录第 2、6 条，specs/launch-lt01-deploy-packaging；安全审查 S1–S4、H2，契约审查 3 已处理）。
// 只在 STARBUDDY_ENV=staging、只给白名单号码：
//   - 条目来自运行时挂载的文件 BFF_STAGING_OTP_FILE（不进仓库、不进镜像）：{"entries":[{"id","phone","code","issued_at","not_after"}]}；
//     管理员按人一次性下发验证码。窗口 not_after − issued_at ≤ 24 小时；id 全局唯一（作废过的 id 不能复用）。
//     文件不能对属组或其他用户可写（否则拒绝启动）。
//   - 签发：号码有有效条目（在窗口内、未作废、未锁定、签发 < 3 次）时，用条目的码走原来的 platform.auth_otp_issue（库里仍只存 MAC，
//     有效期与尝试次数仍按 config/auth.json）；库写成功后才记签发账（markIssued）。其余号码签发随机码且不投递——响应、库里的行为与
//     “短信发送失败”完全相同。
//   - 核验：登录成功即作废该条目（先在内存里作废，再写审计；审计写失败只告警，仍作废）；失败累计 5 次锁定该条目。
//   - 审计：每个事件追加一行 JSON 到 BFF_STAGING_OTP_AUDIT_FILE（at、event、entry、phone_hmac、prev、mac；不记码、不记号码原文），
//     每行写完 fsync；mac 是本行正文与 prev 的带密钥摘要（BFF_LOG_HMAC_KEY），prev 是上一行的 mac（首行为 64 个 0），启动时逐行重算核对，
//     断链、改行或格式不对拒绝启动（fail closed）；截断末尾的行发现不了（已知局限，见 deploy/config/README.md）。
//     启动时从审计文件恢复签发次数、失败次数、作废、锁定与待核验状态。审计文件必须属于本进程用户、权限不宽于 0600；
//     审计文件不存在而条目文件里已有生效的条目时拒绝启动（防止删审计文件解除作废与锁定）；没有生效条目时新建空文件。
//   - 所有号码走同等的文件操作：请求验证码时每个号码都写一行审计（白名单内的签发在库写成功后写），核验时每个号码都写一行审计，
//     缩小白名单与非白名单之间的时序差（已知局限见 deploy/config/README.md）。
//   - 单实例：审计文件旁的锁文件 <审计文件>.lock 记录持有者（主机名、进程号、心跳时间），每 20 秒刷新心跳；启动时发现别的持有者
//     的心跳在 60 秒内就拒绝启动（安全审查 H2）。
//   - 应用日志不出现码与号码：告警只写事件名与原因编码；报错信息不带任何配置值。
import { closeSync, existsSync, fsyncSync, openSync, readFileSync, statSync, writeSync } from 'node:fs';
import { createHmac } from 'node:crypto';
import { hostname } from 'node:os';
import type { Keys, RunEnv } from './config.ts';
import { E164, phoneHash } from './secrets.ts';
import type { SmsSender } from './sms.ts';

export const MAX_ISSUES = 3;
export const MAX_FAILURES = 5;
export const MAX_WINDOW_MS = 24 * 3600 * 1000;
export const LOCK_HEARTBEAT_MS = 20_000;
export const LOCK_STALE_MS = 60_000;
export const GENESIS = '0'.repeat(64);
const ENTRY_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const CODE = /^[0-9]{6}$/;
const ISO_TZ = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?(Z|[+-]\d{2}:\d{2})$/;
export const AUDIT_EVENTS = ['issued', 'not_whitelisted', 'unavailable', 'consumed', 'verify_failed', 'verify_other', 'locked',
                             'config_invalid'] as const;
export type AuditEvent = (typeof AUDIT_EVENTS)[number];

export interface StagingOtpEntry { id: string; phone: string; code: string; issuedAt: number; notAfter: number }
interface EntryState { issues: number; failures: number; consumed: boolean; locked: boolean }

/** 审计行的带密钥摘要（BFF_LOG_HMAC_KEY 派生）：链上任何一行被改都对不上，改最后一行也一样；没有密钥伪造不了新行。
 *  已知局限：截掉末尾若干行（截断）本身发现不了——靠 S1（文件缺失拒绝启动）与卷的访问控制兜底，见 deploy/config/README.md */
const lineMac = (key: Buffer, body: string): string => createHmac('sha256', key).update(`staging-otp-audit\n${body}`).digest('hex');

/** 解析条目文件；任何一项不合格整份拒绝（错误信息不带取值） */
export function parseEntries(text: string): StagingOtpEntry[] {
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    throw new Error('staging OTP 条目文件不是合法 JSON');
  }
  const raw = (doc as { entries?: unknown })?.entries;
  if (typeof doc !== 'object' || doc === null || Object.keys(doc).some((k) => k !== 'entries') || !Array.isArray(raw)) {
    throw new Error('staging OTP 条目文件只能有 entries 数组');
  }
  const ids = new Set<string>();
  return raw.map((e: unknown, i: number) => {
    const o = e as Record<string, unknown>;
    const keys = ['id', 'phone', 'code', 'issued_at', 'not_after'];
    if (typeof o !== 'object' || o === null || Object.keys(o).length !== keys.length || !keys.every((k) => typeof o[k] === 'string')) {
      throw new Error(`staging OTP 第 ${i + 1} 条：只能且必须有 id、phone、code、issued_at、not_after（字符串）`);
    }
    const [id, phone, code, issued, notAfter] = keys.map((k) => o[k] as string) as [string, string, string, string, string];
    if (!ENTRY_ID.test(id) || ids.has(id)) throw new Error(`staging OTP 第 ${i + 1} 条：id 格式不对或重复`);
    ids.add(id);
    if (!E164.test(phone)) throw new Error(`staging OTP 第 ${i + 1} 条：phone 不是 E.164`);
    if (!CODE.test(code)) throw new Error(`staging OTP 第 ${i + 1} 条：code 必须是 6 位数字`);
    if (!ISO_TZ.test(issued) || !ISO_TZ.test(notAfter)) throw new Error(`staging OTP 第 ${i + 1} 条：时间必须是带时区的 ISO 8601`);
    const a = Date.parse(issued);
    const b = Date.parse(notAfter);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a || b - a > MAX_WINDOW_MS) {
      throw new Error(`staging OTP 第 ${i + 1} 条：not_after 必须晚于 issued_at，且窗口不超过 24 小时`);
    }
    return { id, phone, code, issuedAt: a, notAfter: b };
  });
}

interface AuditBody { at: string; event: AuditEvent; entry: string | null; phone_hmac: string | null; prev: string }
interface AuditLine extends AuditBody { mac: string }

/** 一行审计的文本：正文字段固定顺序 + mac（正文与上一行 mac 的带密钥摘要） */
export function auditLineText(key: Buffer, body: AuditBody): { text: string; mac: string } {
  const b = { at: body.at, event: body.event, entry: body.entry, phone_hmac: body.phone_hmac, prev: body.prev };
  const mac = lineMac(key, JSON.stringify(b));
  return { text: JSON.stringify({ ...b, mac }), mac };
}

/** 从审计文件恢复状态：核对哈希链（每行的 prev = 上一行的 mac，mac 按密钥重算）与格式（fail closed）；
 *  返回各条目状态、各号码（HMAC）待核验的条目、链尾 mac */
export function replayAudit(text: string, key: Buffer): { states: Map<string, EntryState>; pending: Map<string, string>; last: string } {
  const out = new Map<string, EntryState>();
  const pending = new Map<string, string>();
  let last = GENESIS;
  const lines = text.split('\n').filter((l) => l !== '');
  lines.forEach((line, i) => {
    let a: AuditLine;
    try {
      a = JSON.parse(line) as AuditLine;
    } catch {
      throw new Error(`staging OTP 审计文件第 ${i + 1} 行不是合法 JSON`);
    }
    if (typeof a !== 'object' || a === null || !(AUDIT_EVENTS as readonly string[]).includes(a.event)
        || Object.keys(a).sort().join(',') !== 'at,entry,event,mac,phone_hmac,prev' || typeof a.at !== 'string'
        || (a.entry !== null && (typeof a.entry !== 'string' || !ENTRY_ID.test(a.entry)))
        || (a.phone_hmac !== null && (typeof a.phone_hmac !== 'string' || !/^[0-9a-f]{16}$/.test(a.phone_hmac)))) {
      throw new Error(`staging OTP 审计文件第 ${i + 1} 行格式不对`);
    }
    if (a.prev !== last || auditLineText(key, a).text !== line) throw new Error(`staging OTP 审计文件第 ${i + 1} 行哈希链断开（审计文件被改过）`);
    last = a.mac;
    if (a.phone_hmac !== null) {
      if (a.event === 'issued' && a.entry !== null) pending.set(a.phone_hmac, a.entry);
      if (['consumed', 'locked', 'not_whitelisted', 'unavailable'].includes(a.event)) pending.delete(a.phone_hmac);
    }
    if (a.entry === null) return;
    const s = out.get(a.entry) ?? { issues: 0, failures: 0, consumed: false, locked: false };
    if (a.event === 'issued') s.issues += 1;
    if (a.event === 'verify_failed') s.failures += 1;
    if (a.event === 'consumed') s.consumed = true;
    if (a.event === 'locked') s.locked = true;
    out.set(a.entry, s);
  });
  return { states: out, pending, last };
}

/** 文件权限核对（安全审查 S2）：条目文件不能对属组或其他用户可写；审计文件属于本进程用户且权限不宽于 0600 */
export function checkFileModes(entriesFile: string, auditFile: string, uid: number | undefined = process.getuid?.()): void {
  const em = statSync(entriesFile).mode;
  if (em & 0o022) throw new Error('staging OTP 条目文件不能对属组或其他用户可写');
  if (existsSync(auditFile)) {
    const st = statSync(auditFile);
    if (uid !== undefined && st.uid !== uid) throw new Error('staging OTP 审计文件必须属于本进程用户');
    if (st.mode & 0o177) throw new Error('staging OTP 审计文件的权限不能宽于 0600');
  }
}

export interface LockInfo { host: string; pid: number; at: number }

/** 单实例锁（安全审查 H2）：别的持有者心跳在 LOCK_STALE_MS 内就拒绝；否则接管并写入自己 */
export function takeLock(lockFile: string, now: number, me: { host: string; pid: number }): void {
  if (existsSync(lockFile)) {
    let held: LockInfo | null = null;
    try {
      held = JSON.parse(readFileSync(lockFile, 'utf8')) as LockInfo;
    } catch {
      held = null;
    }
    const fresh = held !== null && typeof held.at === 'number' && now - held.at < LOCK_STALE_MS;
    if (fresh && !(held!.host === me.host && held!.pid === me.pid)) {
      throw new Error('staging OTP 审计文件正被另一个 BFF 实例使用（白名单固定码只能单实例运行）');
    }
  }
  writeDurable(lockFile, JSON.stringify({ ...me, at: now }), 'w');
}

/** 写一段文本并 fsync（审计行与锁文件） */
function writeDurable(path: string, text: string, flag: 'a' | 'w'): void {
  const fd = openSync(path, flag, 0o600);
  try {
    writeSync(fd, text);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}

export interface StagingOtpOptions {
  env: RunEnv;
  entriesFile: string;
  auditFile: string;
  keys: Keys;
  now?: () => number;
  /** 告警（缺省 console.warn 一行 JSON；只含事件名与原因编码） */
  warn?: (line: Record<string, string>) => void;
  /** 单实例锁的持有者标识（测试注入）；缺省主机名与进程号 */
  holder?: { host: string; pid: number };
  /** 心跳定时器（测试可关）；缺省开 */
  heartbeat?: boolean;
}

export interface Picked { code: string; entry: string }

export class StagingOtp {
  readonly #entriesFile: string;
  readonly #auditFile: string;
  readonly #lockFile: string;
  readonly #keys: Keys;
  readonly #now: () => number;
  readonly #warn: (line: Record<string, string>) => void;
  readonly #holder: { host: string; pid: number };
  #entries: StagingOtpEntry[];
  readonly #state: Map<string, EntryState>;
  /** 号码 HMAC → 最近一次用条目签发的条目 id（核验时据此记成功或失败；启动时从审计恢复） */
  readonly #pending: Map<string, string>;
  #last: string;

  constructor(o: StagingOtpOptions) {
    if (o.env !== 'staging') throw new Error('白名单固定码 OTP 只能在 staging 使用');
    if (!o.entriesFile || !o.auditFile) throw new Error('staging 需要 BFF_STAGING_OTP_FILE 与 BFF_STAGING_OTP_AUDIT_FILE');
    if (o.entriesFile === o.auditFile) throw new Error('BFF_STAGING_OTP_FILE 与 BFF_STAGING_OTP_AUDIT_FILE 不能是同一个文件');
    this.#entriesFile = o.entriesFile;
    this.#auditFile = o.auditFile;
    this.#lockFile = `${o.auditFile}.lock`;
    this.#keys = o.keys;
    this.#now = o.now ?? Date.now;
    this.#warn = o.warn ?? ((l) => console.warn(JSON.stringify(l)));
    this.#holder = o.holder ?? { host: hostname(), pid: process.pid };
    this.#entries = parseEntries(this.#read(o.entriesFile, '条目文件'));
    try {
      checkFileModes(o.entriesFile, o.auditFile);
    } catch (e) {
      throw e instanceof Error ? e : new Error('staging OTP 文件权限核对失败');
    }
    const now = this.#now();
    if (!existsSync(o.auditFile)) {
      if (this.#entries.some((e) => e.issuedAt <= now)) {
        throw new Error('staging OTP 审计文件不存在而条目已生效：拒绝启动（审计文件丢失会解除作废与锁定；由人确认后新建空文件）');
      }
      try {
        writeDurable(o.auditFile, '', 'a');
      } catch {
        throw new Error('staging OTP 审计文件不可写');
      }
    }
    const replayed = replayAudit(this.#read(o.auditFile, '审计文件'), o.keys.log);
    this.#state = replayed.states;
    this.#pending = replayed.pending;
    this.#last = replayed.last;
    try {
      takeLock(this.#lockFile, now, this.#holder);
    } catch (e) {
      throw e instanceof Error && e.message.startsWith('staging OTP') ? e : new Error('staging OTP 锁文件不可写');
    }
    if (o.heartbeat !== false) {
      const t = setInterval(() => {
        try {
          takeLock(this.#lockFile, this.#now(), this.#holder);
        } catch {
          this.#warn({ event: 'staging_otp_lock_heartbeat_failed' });
        }
      }, LOCK_HEARTBEAT_MS);
      t.unref();
    }
  }

  #read(path: string, what: string): string {
    try {
      return readFileSync(path, 'utf8');
    } catch {
      throw new Error(`staging OTP ${what}读不到`);
    }
  }

  /** 追加一行审计（带哈希链，fsync；写不了抛错，由调用方决定） */
  #audit(event: AuditEvent, entry: string | null, phone: string | null): void {
    const { text, mac } = auditLineText(this.#keys.log, { at: new Date(this.#now()).toISOString(), event, entry,
                                                          phone_hmac: phone === null ? null : phoneHash(this.#keys, phone), prev: this.#last });
    writeDurable(this.#auditFile, text + '\n', 'a');
    this.#last = mac;
  }

  /** 写审计，失败只告警（不改响应、不改已做的内存状态） */
  #auditQuiet(event: AuditEvent, entry: string | null, phone: string | null): void {
    try {
      this.#audit(event, entry, phone);
    } catch {
      this.#warn({ event: 'staging_otp_audit_write_failed', audit_event: event });
    }
  }

  /** 每次签发前重读条目文件（管理员追加条目不用重启）；文件坏了保留上一份并记 config_invalid */
  #reload(): void {
    try {
      this.#entries = parseEntries(readFileSync(this.#entriesFile, 'utf8'));
    } catch {
      this.#auditQuiet('config_invalid', null, null);
    }
  }

  #stateOf(id: string): EntryState {
    let s = this.#state.get(id);
    if (!s) {
      s = { issues: 0, failures: 0, consumed: false, locked: false };
      this.#state.set(id, s);
    }
    return s;
  }

  /** 签发前挑条目：白名单内有有效条目返回码与条目 id（尚未记账，库写成功后调 markIssued），否则 null（调用方用随机码）。
   *  非白名单与无可用条目的号码在这里写一行审计；白名单内可用的在 markIssued 写——每个号码都有一次文件写入 */
  pick(phone: string): Picked | null {
    this.#reload();
    const now = this.#now();
    const key = phoneHash(this.#keys, phone);
    const mine = this.#entries.filter((e) => e.phone === phone);
    if (mine.length === 0) {
      this.#pending.delete(key);
      this.#auditQuiet('not_whitelisted', null, phone);
      return null;
    }
    const usable = mine
      .filter((e) => { const s = this.#stateOf(e.id); return !s.consumed && !s.locked && s.issues < MAX_ISSUES && e.issuedAt <= now && now < e.notAfter; })
      .sort((a, b) => a.notAfter - b.notAfter);
    const e = usable[0];
    if (!e) {
      this.#pending.delete(key);
      this.#auditQuiet('unavailable', null, phone);
      return null;
    }
    return { code: e.code, entry: e.id };
  }

  /** 库写（auth_otp_issue）成功之后记签发账：先改内存，再写审计（写失败告警） */
  markIssued(phone: string, entry: string): void {
    this.#stateOf(entry).issues += 1;
    this.#pending.set(phoneHash(this.#keys, phone), entry);
    this.#auditQuiet('issued', entry, phone);
  }

  /** 核验结果：只对最近一次用条目签发的号码记账；成功即作废（先内存后审计），失败累计到上限即锁定。
   *  没有待核验条目的号码也写一行审计（同等文件操作）。不抛错：审计写失败只告警，不改登录响应 */
  onVerify(phone: string, ok: boolean): void {
    const key = phoneHash(this.#keys, phone);
    const id = this.#pending.get(key);
    if (id === undefined) {
      this.#auditQuiet('verify_other', null, phone);
      return;
    }
    const s = this.#stateOf(id);
    if (ok) {
      s.consumed = true;
      this.#pending.delete(key);
      this.#auditQuiet('consumed', id, phone);
      return;
    }
    s.failures += 1;
    const lockNow = s.failures >= MAX_FAILURES && !s.locked;
    if (lockNow) {
      s.locked = true;
      this.#pending.delete(key);
    }
    this.#auditQuiet('verify_failed', id, phone);
    if (lockNow) this.#auditQuiet('locked', id, phone);
  }

  /** 测试用：某条目的状态 */
  stateOf(id: string): Readonly<EntryState> | undefined {
    return this.#state.get(id);
  }
}

/** staging 的“短信发送方”：什么都不发（固定码由管理员线下按人下发；随机码不投递） */
export class NoDeliverySender implements SmsSender {
  async sendLoginCode(_phone: string, _code: string): Promise<void> {
    /* 不投递、不记录 */
  }
}
