// 导出包的暂存（SIE-03 PR③，0035 platform.export_student_data）：库函数一次返回整包与一次性下载凭据（库里只存凭据的 sha256），
// 没有别的地方可以再取这份内容，所以 BFF 在下载前暂存它：
//   - 键是凭据的 sha256（与库里的摘要同一算法），内存里不存凭据原文；
//   - 绑定导出任务 ID、学生、发起的家长账号；下载时三者都要对上；
//   - 兑换分两步（specs/export-download-voucher，PR 28 增量审查 N8）：claim 只“占用”（同一凭据同时只放行一个），响应写完（commit）才作废；
//     写出失败或连接中途断开（release）解除占用，凭据在原有效期内可再用；每张凭据最多占用 MAX_CLAIMS 次，占用超过 CLAIM_HOLD_MS 视为已释放；
//   - 暂存时长取 min(1 小时, 库里给的 expires_at)（安全审查 H2）；条数、单包大小、总字节都有上限，超了不暂存（调用方回 503，不静默截断）；
//   - 删除请求已确认、学生已删除、家长解除绑定时，按学生（或学生 + 账号）清掉暂存（安全审查 M1）。
// 进程内：多实例部署时只有发起导出的实例能兑换；pilot 前须换成对象存储（新增存储服务，必停，lessons PR③ 待人确认）。
import { createHash } from 'node:crypto';

export interface ExportEntry { jobId: string; studentId: string; accountId: string; expiresAt: number; body: string }

/** 释放的结果：released 可再兑换；released_final 次数用完、已作废；stale 这次占用已不是当前占用（超时被接手、条目已过期或被清掉），什么也没改 */
export type ReleaseOutcome = 'released' | 'released_final' | 'stale';

/** 一次占用：commit / release 只有第一次调用生效 */
export interface ExportClaim {
  body: string;
  /** 这是这张凭据的第几次占用（1 起） */
  attempt: number;
  /** 响应已写完：作废凭据（即使这次占用已超时被接手，包已送达过一次） */
  commit(): void;
  /** 没写完：解除占用；次数用完时等于作废 */
  release(): ReleaseOutcome;
}

export interface ExportStore {
  /** 暂存；超过上限返回 false */
  put(downloadRef: string, entry: ExportEntry, now: number): boolean;
  /** 占用：凭据、任务、学生、账号都对上、未过期、当前没有有效占用、次数未用完才返回；否则 null（调用方一律同一个 404） */
  claim(downloadRef: string, match: { jobId: string; studentId: string; accountId: string }, now: number): ExportClaim | null;
  /** 清掉这个学生的暂存（给了账号时只清这个账号发起的）；返回清掉的条数 */
  purgeStudent(studentId: string, accountId?: string): number;
}

export const refDigest = (ref: string): string => createHash('sha256').update(ref, 'utf8').digest('hex');
/** 暂存最长 1 小时（库里的下载凭据 24 小时失效，BFF 不留那么久） */
export const MAX_HOLD_MS = 3600_000;
/** 每张凭据最多占用 3 次（首次 + 2 次重试；spec 待人确认 1，2026-11-22 按默认确认） */
export const MAX_CLAIMS = 3;
/** 占用超时 2 分钟（前端下载时限 60 秒；只兜底卡死的连接，惰性判断，不主动断开正在写的响应；spec 待人确认 2） */
export const CLAIM_HOLD_MS = 120_000;

interface Held extends ExportEntry { attempts: number; claimSeq: number; claimUntil: number }

export class MemoryExportStore implements ExportStore {
  readonly #entries = new Map<string, Held>();
  readonly maxEntries: number;
  readonly maxBytes: number;
  readonly maxTotalBytes: number;
  #total = 0;
  constructor(maxEntries = 200, maxBytes = 8 * 1024 * 1024, maxTotalBytes = 64 * 1024 * 1024) {
    this.maxEntries = maxEntries;
    this.maxBytes = maxBytes;
    this.maxTotalBytes = maxTotalBytes;
  }

  #drop(k: string): void {
    const e = this.#entries.get(k);
    if (!e) return;
    this.#total -= Buffer.byteLength(e.body, 'utf8');
    this.#entries.delete(k);
  }

  #sweep(now: number): void {
    for (const [k, e] of this.#entries) if (e.expiresAt <= now) this.#drop(k);
  }

  put(downloadRef: string, entry: ExportEntry, now: number): boolean {
    this.#sweep(now);
    const size = Buffer.byteLength(entry.body, 'utf8');
    if (size > this.maxBytes || this.#entries.size >= this.maxEntries || this.#total + size > this.maxTotalBytes) return false;
    this.#entries.set(refDigest(downloadRef), { ...entry, expiresAt: Math.min(entry.expiresAt, now + MAX_HOLD_MS), attempts: 0, claimSeq: 0, claimUntil: 0 });
    this.#total += size;
    return true;
  }

  claim(downloadRef: string, match: { jobId: string; studentId: string; accountId: string }, now: number): ExportClaim | null {
    this.#sweep(now);
    const k = refDigest(downloadRef);
    const e = this.#entries.get(k);
    if (!e || e.jobId !== match.jobId || e.studentId !== match.studentId || e.accountId !== match.accountId) return null;
    if (e.claimUntil > now) return null;  // 正被占用：同一凭据同时只放行一个
    if (e.attempts >= MAX_CLAIMS) {  // 最后一次占用超时也没写完：作废
      this.#drop(k);
      return null;
    }
    e.attempts += 1;
    const seq = ++e.claimSeq;
    e.claimUntil = now + CLAIM_HOLD_MS;
    const current = (): boolean => this.#entries.get(k) === e;
    let done = false;
    return {
      body: e.body,
      attempt: e.attempts,
      commit: () => {
        if (done) return;
        done = true;
        if (current()) this.#drop(k);  // 送达即作废
      },
      release: () => {
        if (done) return 'stale';
        done = true;
        if (!current() || e.claimSeq !== seq) return 'stale';  // 超时被接手：不解除新的占用
        if (e.attempts >= MAX_CLAIMS) {
          this.#drop(k);
          return 'released_final';
        }
        e.claimUntil = 0;
        return 'released';
      },
    };
  }

  purgeStudent(studentId: string, accountId?: string): number {
    let n = 0;
    for (const [k, e] of this.#entries) {
      if (e.studentId === studentId && (accountId === undefined || e.accountId === accountId)) {
        this.#drop(k);
        n += 1;
      }
    }
    return n;
  }

  get size(): number {
    return this.#entries.size;
  }

  get totalBytes(): number {
    return this.#total;
  }
}
