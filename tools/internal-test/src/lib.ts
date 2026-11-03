// 内部成员测试的白名单运营工具（specs/launch-lt06-internal-test-ops 范围第 1 条）：纯函数部分。
// 条目文件格式、窗口 ≤ 24 小时、条目 id 规则、审计哈希链与手机号 HMAC 一律直接用 BFF 的实现（services/bff/src/http/staging-otp.ts、
// secrets.ts），不另抄规则：生成的文件先过 BFF 的 parseEntries 才输出，审计摘要用 BFF 的 replayAudit 核对哈希链。
// 安全口径：
//   - 手机号只在内存里：从标准输入读入，条目文件只写到标准输出（由管理员直接导入密钥管理），工具自己不把号码写进任何文件；
//     工具台账（--ledger）只记条目 id、批次、事件、时间与手机号 HMAC（给了 --key-file 时；否则为 null），不记号码与码；
//   - 验证码用 BFF 的 newCode（node:crypto 的 randomInt，CSPRNG）；
//   - 报错只带行号、批次名与原因编码，不带号码与码；
//   - 作废与 BFF 同一口径：作废后该条目永不再签发、id 不能复用。工具按批次作废 = 从条目文件里删掉该批次的条目（BFF 每次签发前重读
//     条目文件，删掉即不再签发）并在台账记 revoked；BFF 在家长退出测试时作废的条目记在 BFF 的审计文件里（事件同为 revoked）。
//     生成新批次前核对台账与审计文件里出现过的全部条目 id，用过的批次名不能再用。
//   - 台账里的手机号 HMAC 用从日志 HMAC 密钥派生的独立子密钥（ledgerKey，审查建议）：与 BFF 审计文件里的 phone_hmac 不同、互相对不上，
//     台账单独泄露也不能拿去比对 BFF 审计；核对 BFF 审计文件的哈希链仍用原密钥（与 BFF_LOG_HMAC_KEY 同一把）。
//   - 审计摘要贴工单时用 --for-ticket：去掉 phone_hmac，只剩条目 id、事件与时间。
//   - BFF 审计文件里登录成功的行另带 account_hmac（LT-06 决定记录（续）第 5 条，按账号作废）：这里只认格式，摘要里不输出。
import { createHmac } from 'node:crypto';
import { E164, newCode, phoneHash } from '../../../services/bff/src/http/secrets.ts';
import type { Keys } from '../../../services/bff/src/http/config.ts';
import { AUDIT_EVENTS, MAX_WINDOW_MS, parseEntries, replayAudit } from '../../../services/bff/src/http/staging-otp.ts';

/** 台账手机号 HMAC 的子密钥：从日志 HMAC 密钥派生（HMAC-SHA256，固定用途标签），不直接用原密钥 */
export const ledgerKey = (key: Buffer): Buffer => createHmac('sha256', key).update('starbuddy-internal-test-ledger-v1').digest();
const ledgerHmac = (key: Buffer | null | undefined, phone: string): string | null =>
  key ? phoneHash({ log: ledgerKey(key) } as Keys, phone) : null;

export class ToolError extends Error {}

/** 批次名：小写字母数字与连字符，1–31 个字符；条目 id = 批次名-三位序号（BFF 的 id 规则：^[a-z0-9][a-z0-9-]{0,39}$） */
export const BATCH = /^[a-z0-9][a-z0-9-]{0,30}$/;
export const MAX_PER_BATCH = 999;
export const MAX_HOURS = MAX_WINDOW_MS / 3600_000;
const SEQ = /-[0-9]{3}$/;

export interface RawEntry { id: string; phone: string; code: string; issued_at: string; not_after: string }
export type LedgerEvent = 'generated' | 'revoked';
export interface LedgerLine { at: string; event: LedgerEvent; entry: string; batch: string; phone_hmac: string | null }
export interface SummaryEvent { entry: string | null; event: string; at: string; phone_hmac: string | null }

export function checkBatch(batch: string): void {
  if (!BATCH.test(batch) || SEQ.test(batch)) throw new ToolError('批次名只能是 1–31 个小写字母、数字与连字符，且不能以“-三位数字”结尾');
}

const idsOf = (batch: string) => new RegExp(`^${batch.replace(/[-]/g, '\\-')}-[0-9]{3}$`);

/** 标准输入里的手机号：每行一个 E.164，空行忽略；重复的号码拒绝（报错只给行号） */
export function parsePhones(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  text.split(/\r?\n/).forEach((line, i) => {
    const p = line.trim();
    if (p === '') return;
    if (!E164.test(p)) throw new ToolError(`标准输入第 ${i + 1} 行不是 E.164 号码`);
    if (seen.has(p)) throw new ToolError(`标准输入第 ${i + 1} 行与前面的号码重复`);
    seen.add(p);
    out.push(p);
  });
  if (out.length === 0) throw new ToolError('标准输入里没有号码');
  if (out.length > MAX_PER_BATCH) throw new ToolError(`一个批次最多 ${MAX_PER_BATCH} 个号码`);
  return out;
}

/** 现有条目文件（缺省没有）：用 BFF 的 parseEntries 校验，原样保留其中的条目 */
export function readExisting(text: string | null): RawEntry[] {
  if (text === null) return [];
  parseEntries(text);
  return (JSON.parse(text) as { entries: RawEntry[] }).entries;
}

/** 台账与 BFF 审计文件里出现过的全部条目 id（只认格式，不核对哈希链；核对哈希链在 summary 里） */
export function usedIds(ledgerText: string, auditText: string | null): Set<string> {
  const out = new Set<string>();
  for (const l of parseLedger(ledgerText)) out.add(l.entry);
  for (const e of looseAudit(auditText ?? '')) if (e.entry !== null) out.add(e.entry);
  return out;
}

export interface GenerateInput {
  batch: string; phones: string[]; issuedAt: Date; hours: number; existing: RawEntry[]; used: ReadonlySet<string>;
  code?: () => string; key?: Buffer | null;
}

/** 生成一个批次的条目：id = 批次-001…；窗口 = issued_at 起 hours 小时（1–24）；码由 CSPRNG 生成；整份文件过 BFF 的 parseEntries */
export function generate(input: GenerateInput): { entries: RawEntry[]; added: RawEntry[]; ledger: LedgerLine[] } {
  checkBatch(input.batch);
  if (!Number.isInteger(input.hours) || input.hours < 1 || input.hours > MAX_HOURS) throw new ToolError(`--hours 必须是 1–${MAX_HOURS} 的整数（窗口 ≤ 24 小时）`);
  if (!Number.isFinite(input.issuedAt.getTime())) throw new ToolError('--issued-at 不是合法时间');
  const re = idsOf(input.batch);
  if ([...input.used].some((id) => re.test(id)) || input.existing.some((e) => re.test(e.id))) {
    throw new ToolError(`批次 ${input.batch} 已经用过（作废或用过的条目 id 不能复用，换一个批次名）`);
  }
  const code = input.code ?? newCode;
  const issued = input.issuedAt.toISOString();
  const notAfter = new Date(input.issuedAt.getTime() + input.hours * 3600_000).toISOString();
  const added = input.phones.map((phone, i) => ({ id: `${input.batch}-${String(i + 1).padStart(3, '0')}`, phone, code: code(),
                                                  issued_at: issued, not_after: notAfter }));
  const entries = [...input.existing, ...added];
  validate(entries);
  const at = new Date().toISOString();
  const ledger = added.map((e) => ({ at, event: 'generated' as const, entry: e.id, batch: input.batch,
                                     phone_hmac: ledgerHmac(input.key, e.phone) }));
  return { entries, added, ledger };
}

/** 按批次作废：从条目文件里删掉该批次的全部条目；批次在文件里没有条目时报错（避免拼错批次名以为作废了） */
export function revokeBatch(existing: RawEntry[], batch: string, key: Buffer | null = null): { entries: RawEntry[]; removed: RawEntry[]; ledger: LedgerLine[] } {
  checkBatch(batch);
  const re = idsOf(batch);
  const removed = existing.filter((e) => re.test(e.id));
  if (removed.length === 0) throw new ToolError(`条目文件里没有批次 ${batch} 的条目`);
  const entries = existing.filter((e) => !re.test(e.id));
  validate(entries);
  const at = new Date().toISOString();
  const ledger = removed.map((e) => ({ at, event: 'revoked' as const, entry: e.id, batch,
                                       phone_hmac: ledgerHmac(key, e.phone) }));
  return { entries, removed, ledger };
}

/** 条目文件正文（BFF 的格式 {"entries": [...]}）；输出前再过一遍 BFF 的 parseEntries */
export function renderEntries(entries: RawEntry[]): string {
  const text = `${JSON.stringify({ entries }, null, 2)}\n`;
  parseEntries(text);
  return text;
}

function validate(entries: RawEntry[]): void {
  try {
    parseEntries(JSON.stringify({ entries }));
  } catch (e) {
    throw new ToolError(`生成的条目不合 BFF 的格式：${(e as Error).message}`);  // parseEntries 的报错不带取值
  }
}

const LEDGER_KEYS = 'at,batch,entry,event,phone_hmac';

/** 工具台账：一行一个 JSON（at、event、entry、batch、phone_hmac）；格式不对整份拒绝 */
export function parseLedger(text: string): LedgerLine[] {
  return text.split('\n').filter((l) => l.trim() !== '').map((line, i) => {
    let o: LedgerLine;
    try {
      o = JSON.parse(line) as LedgerLine;
    } catch {
      throw new ToolError(`台账第 ${i + 1} 行不是合法 JSON`);
    }
    if (typeof o !== 'object' || o === null || Object.keys(o).sort().join(',') !== LEDGER_KEYS || !['generated', 'revoked'].includes(o.event)
        || typeof o.entry !== 'string' || !/^[a-z0-9][a-z0-9-]{0,39}$/.test(o.entry) || typeof o.batch !== 'string' || !BATCH.test(o.batch)
        || typeof o.at !== 'string' || !Number.isFinite(Date.parse(o.at))
        || (o.phone_hmac !== null && (typeof o.phone_hmac !== 'string' || !/^[0-9a-f]{16}$/.test(o.phone_hmac)))) {
      throw new ToolError(`台账第 ${i + 1} 行格式不对`);
    }
    return o;
  });
}

export const ledgerText = (lines: LedgerLine[]): string =>
  lines.map((l) => `${JSON.stringify({ at: l.at, event: l.event, entry: l.entry, batch: l.batch, phone_hmac: l.phone_hmac })}\n`).join('');

/** BFF 审计文件的宽松读取（不核对哈希链，只核对字段与事件名）：给“用过的 id”与没给密钥时的摘要用 */
function looseAudit(text: string): SummaryEvent[] {
  return text.split('\n').filter((l) => l !== '').map((line, i) => {
    let a: Record<string, unknown>;
    try {
      a = JSON.parse(line) as Record<string, unknown>;
    } catch {
      throw new ToolError(`审计文件第 ${i + 1} 行不是合法 JSON`);
    }
    const keys = typeof a === 'object' && a !== null ? Object.keys(a).sort().join(',') : '';
    if (typeof a !== 'object' || a === null
        || (keys !== 'at,entry,event,mac,phone_hmac,prev' && !(keys === 'account_hmac,at,entry,event,mac,phone_hmac,prev' && a['event'] === 'consumed'))
        || !(AUDIT_EVENTS as readonly string[]).includes(String(a['event'])) || typeof a['at'] !== 'string'
        || (a['entry'] !== null && typeof a['entry'] !== 'string') || (a['phone_hmac'] !== null && typeof a['phone_hmac'] !== 'string')) {
      throw new ToolError(`审计文件第 ${i + 1} 行格式不对`);
    }
    return { entry: a['entry'] as string | null, event: String(a['event']), at: a['at'], phone_hmac: a['phone_hmac'] as string | null };
  });
}

/**
 * 审计摘要：工具台账（generated / revoked）与 BFF 审计文件（issued、consumed、revoked 等）合并，按时间排序。
 * 每条只有条目 id、事件、时间、手机号 HMAC（不含码、号码与账号）。给了密钥（与 BFF_LOG_HMAC_KEY 同一把）时用 BFF 的 replayAudit 核对审计文件的
 * 哈希链（断链、改行一律报错，chain_verified = true）；没给时只核对格式（chain_verified = false）。
 * forTicket：贴工单用，去掉 phone_hmac（只剩条目 id、事件与时间）。
 */
export function summary(ledger: string, audit: string | null, key: Buffer | null, forTicket = false): {
  chain_verified: boolean; counts: Record<string, number>; events: Array<SummaryEvent | Omit<SummaryEvent, 'phone_hmac'>>;
} {
  if (audit !== null && key !== null) {
    try {
      replayAudit(audit, key);
    } catch (e) {
      throw new ToolError(`审计文件核对失败：${(e as Error).message}`);
    }
  }
  const events: SummaryEvent[] = [
    ...parseLedger(ledger).map((l) => ({ entry: l.entry, event: l.event, at: l.at, phone_hmac: l.phone_hmac })),
    ...looseAudit(audit ?? ''),
  ].sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  const counts: Record<string, number> = {};
  for (const e of events) counts[e.event] = (counts[e.event] ?? 0) + 1;
  return { chain_verified: audit !== null && key !== null, counts,
           events: forTicket ? events.map(({ entry, event, at }) => ({ entry, event, at })) : events };
}

/** 密钥文件：一行十六进制（与 BFF_LOG_HMAC_KEY 同一格式，≥ 32 字节）；报错不带取值 */
export function parseKey(text: string): Buffer {
  const v = text.trim();
  if (!/^[0-9a-f]+$/i.test(v) || v.length % 2 !== 0 || v.length < 64) throw new ToolError('密钥文件必须是一行至少 32 字节的十六进制串');
  return Buffer.from(v, 'hex');
}
