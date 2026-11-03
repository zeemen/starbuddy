// 内部成员测试的白名单运营工具入口（specs/launch-lt06-internal-test-ops 范围第 1 条；手册 docs/ops/internal-test-runbook.md 第 3 节）。
// 管理员在跳板机上执行；不连库、不联网、不读环境变量。用法（仓库根目录）：
//   node tools/internal-test/src/main.ts generate --batch <批次> --hours <1–24> --ledger <台账> [--issued-at <ISO 时间>]
//        [--existing <现有条目文件>] [--audit <BFF 审计文件>] [--key-file <HMAC 密钥文件>]   < 号码（每行一个 E.164）
//        [--out <新文件>]
//     → 标准输出（缺省）：合并后的完整条目文件（BFF_STAGING_OTP_FILE 的格式，含号码与码：管道直接导入凭据管家，不要落盘）；
//       给了 --out 时改写到这个新文件（只新建不覆盖，强制 0600）；
//       台账追加 generated（条目 id、批次、时间、手机号 HMAC：用从密钥派生的子密钥，没给密钥时为 null）；标准错误：条数与窗口（不含号码与码）
//   node tools/internal-test/src/main.ts revoke --batch <批次> --existing <现有条目文件> --ledger <台账> [--key-file <密钥文件>] [--out <新文件>]
//     → 标准输出（或 --out）：去掉该批次后的完整条目文件；台账追加 revoked
//   node tools/internal-test/src/main.ts summary --ledger <台账> [--audit <BFF 审计文件>] [--key-file <密钥文件>] [--for-ticket]
//     → 标准输出：审计摘要 JSON（只有条目 id、事件、时间、手机号 HMAC；给了密钥时核对审计文件的哈希链；--for-ticket 去掉手机号 HMAC）
// 文件：台账以 0600 追加（不存在则新建）；--out 新建 0600；密钥文件与现有条目文件不能对属组或其他用户可读（含号码与码 / 密钥）；
// 工具不写别的文件。含号码与码的命令请在 umask 077 的子 shell 里执行（手册第 3 节）。
// 退出码：0 成功；1 输入或校验失败；2 参数不对。报错只带原因，不带号码与码。
import { closeSync, existsSync, fchmodSync, fsyncSync, openSync, readFileSync, statSync, writeSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { generate, ledgerText, parseKey, parsePhones, readExisting, renderEntries, revokeBatch, summary, ToolError, usedIds } from './lib.ts';

export class ArgError extends Error {}

export interface Io { stdin: () => string; stdout: (s: string) => void; stderr: (s: string) => void; now?: () => Date }

function readPrivate(path: string, what: string): string {
  let mode: number;
  try {
    mode = statSync(path).mode;
  } catch {
    throw new ToolError(`${what}读不到`);
  }
  if (mode & 0o077) throw new ToolError(`${what}的权限不能宽于 0600（含号码与码或密钥）`);
  return readFileSync(path, 'utf8');
}

const readOptional = (path: string | undefined, what: string): string | null => {
  if (path === undefined) return null;
  try {
    return readFileSync(path, 'utf8');
  } catch {
    throw new ToolError(`${what}读不到`);
  }
};

function appendLedger(path: string, text: string): void {
  if (text === '') return;
  if (existsSync(path) && statSync(path).mode & 0o077) throw new ToolError('台账的权限不能宽于 0600');
  const fd = openSync(path, 'a', 0o600);
  try {
    writeSync(fd, text);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}

/** 含号码与码的输出文件（--out，审查阻断 4）：只新建、不覆盖（已存在即拒绝），创建时就是 0600，写完再 fchmod 一次 0600（不受 umask 影响）；
 *  缺省不用它——优先用管道直接导入凭据管家（手册第 3 节） */
export function writePrivateOut(path: string, text: string): void {
  let fd: number;
  try {
    fd = openSync(path, 'wx', 0o600);
  } catch {
    throw new ToolError('--out 文件已存在或不能新建（不覆盖已有文件）');
  }
  try {
    fchmodSync(fd, 0o600);
    writeSync(fd, text);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}

const OPTIONS = {
  batch: { type: 'string' }, hours: { type: 'string' }, 'issued-at': { type: 'string' }, existing: { type: 'string' },
  ledger: { type: 'string' }, audit: { type: 'string' }, 'key-file': { type: 'string' }, out: { type: 'string' },
  'for-ticket': { type: 'boolean' },
} as const;

export function run(argv: string[], io: Io): number {
  try {
    const [cmd, ...rest] = argv;
    let parsed;
    try {
      parsed = parseArgs({ args: rest, options: OPTIONS, strict: true, allowPositionals: false });
    } catch {
      throw new ArgError('参数不对（见文件头的用法）');
    }
    const v = parsed.values;
    const need = (k: keyof typeof v): string => {
      const x = v[k];
      if (typeof x !== 'string' || x === '') throw new ArgError(`缺少 --${k}`);
      return x;
    };
    const key = v['key-file'] ? parseKey(readPrivate(v['key-file'], '密钥文件')) : null;
    const now = io.now ?? (() => new Date());
    if (v.out !== undefined && cmd !== 'generate' && cmd !== 'revoke') throw new ArgError('--out 只用于 generate、revoke');
    if (v['for-ticket'] !== undefined && cmd !== 'summary') throw new ArgError('--for-ticket 只用于 summary');
    // 条目文件（含号码与码）：缺省写到标准输出（管道直接导入凭据管家）；给了 --out 时新建 0600 文件
    const emit = (text: string): void => {
      if (v.out !== undefined) writePrivateOut(v.out, text);
      else io.stdout(text);
    };
    // 先核对 --out 不存在（在追加台账之前，避免台账记了而输出失败）
    if (v.out !== undefined && existsSync(v.out)) throw new ToolError('--out 文件已存在（不覆盖已有文件）');
    if (cmd === 'generate') {
      const batch = need('batch');
      const hours = Number(need('hours'));
      const ledgerPath = need('ledger');
      const issuedAt = v['issued-at'] ? new Date(v['issued-at']) : now();
      if (v['issued-at'] && !/(Z|[+-]\d{2}:\d{2})$/.test(v['issued-at'])) throw new ArgError('--issued-at 必须带时区');
      const existing = readExisting(v.existing ? readPrivate(v.existing, '现有条目文件') : null);
      const used = usedIds(readOptional(existsSync(ledgerPath) ? ledgerPath : undefined, '台账') ?? '', readOptional(v.audit, '审计文件'));
      const phones = parsePhones(io.stdin());
      const r = generate({ batch, phones, issuedAt, hours, existing, used, key });
      const out = renderEntries(r.entries);
      appendLedger(ledgerPath, ledgerText(r.ledger));
      emit(out);
      io.stderr(`internal-test: 批次 ${batch} 生成 ${r.added.length} 条（窗口 ${r.added[0]!.issued_at} 至 ${r.added[0]!.not_after}），`
                + `条目文件共 ${r.entries.length} 条；台账已追加\n`);
      return 0;
    }
    if (cmd === 'revoke') {
      const batch = need('batch');
      const ledgerPath = need('ledger');
      const existing = readExisting(readPrivate(need('existing'), '现有条目文件'));
      const r = revokeBatch(existing, batch, key);
      const out = renderEntries(r.entries);
      appendLedger(ledgerPath, ledgerText(r.ledger));
      emit(out);
      io.stderr(`internal-test: 批次 ${batch} 作废 ${r.removed.length} 条，条目文件剩 ${r.entries.length} 条；台账已追加\n`);
      return 0;
    }
    if (cmd === 'summary') {
      const s = summary(readOptional(need('ledger'), '台账') ?? '', readOptional(v.audit, '审计文件'), key, v['for-ticket'] === true);
      io.stdout(`${JSON.stringify(s, null, 2)}\n`);
      return 0;
    }
    throw new ArgError('子命令只有 generate、revoke、summary');
  } catch (e) {
    if (e instanceof ArgError) {
      io.stderr(`internal-test: ${e.message}\n`);
      return 2;
    }
    if (e instanceof ToolError) {
      io.stderr(`internal-test: ${e.message}\n`);
      return 1;
    }
    io.stderr('internal-test: 意外错误（不打印细节，以免带出号码或码）\n');
    return 1;
  }
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const code = run(process.argv.slice(2), {
    stdin: () => {
      try {
        return readFileSync(0, 'utf8');
      } catch {
        return '';
      }
    },
    stdout: (s) => process.stdout.write(s),
    stderr: (s) => process.stderr.write(s),
  });
  process.exitCode = code;
}
