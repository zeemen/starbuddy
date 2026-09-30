// 导出包的暂存（SIE-03 PR③，0035 platform.export_student_data）：库函数一次返回整包与一次性下载凭据（库里只存凭据的 sha256），
// 没有别的地方可以再取这份内容，所以 BFF 在下载前暂存它：
//   - 键是凭据的 sha256（与库里的摘要同一算法），内存里不存凭据原文；
//   - 绑定导出任务 ID、学生、发起的家长账号；下载时三者都要对上，且只能下载一次（取出即删）；
//   - 暂存时长取 min(1 小时, 库里给的 expires_at)（安全审查 H2）；条数、单包大小、总字节都有上限，超了不暂存（调用方回 503，不静默截断）；
//   - 删除请求已确认、学生已删除、家长解除绑定时，按学生（或学生 + 账号）清掉暂存（安全审查 M1）。
// 进程内：多实例部署时只有发起导出的实例能兑换；pilot 前须换成对象存储（新增存储服务，必停，lessons PR③ 待人确认）。
import { createHash } from 'node:crypto';

export interface ExportEntry { jobId: string; studentId: string; accountId: string; expiresAt: number; body: string }

export interface ExportStore {
  /** 暂存；超过上限返回 false */
  put(downloadRef: string, entry: ExportEntry, now: number): boolean;
  /** 兑换（只一次）：凭据、任务、学生、账号都对上且未过期才返回内容，并立即删除 */
  take(downloadRef: string, match: { jobId: string; studentId: string; accountId: string }, now: number): string | null;
  /** 清掉这个学生的暂存（给了账号时只清这个账号发起的）；返回清掉的条数 */
  purgeStudent(studentId: string, accountId?: string): number;
}

export const refDigest = (ref: string): string => createHash('sha256').update(ref, 'utf8').digest('hex');
/** 暂存最长 1 小时（库里的下载凭据 24 小时失效，BFF 不留那么久） */
export const MAX_HOLD_MS = 3600_000;

export class MemoryExportStore implements ExportStore {
  readonly #entries = new Map<string, ExportEntry>();
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
    this.#entries.set(refDigest(downloadRef), { ...entry, expiresAt: Math.min(entry.expiresAt, now + MAX_HOLD_MS) });
    this.#total += size;
    return true;
  }

  take(downloadRef: string, match: { jobId: string; studentId: string; accountId: string }, now: number): string | null {
    this.#sweep(now);
    const k = refDigest(downloadRef);
    const e = this.#entries.get(k);
    if (!e || e.jobId !== match.jobId || e.studentId !== match.studentId || e.accountId !== match.accountId) return null;
    this.#drop(k);  // 一次性：兑换即删
    return e.body;
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
