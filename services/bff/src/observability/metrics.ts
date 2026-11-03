// BFF 的 Prometheus 指标（LT-05 范围第 1 条，specs/launch-lt05-observability；决定记录（续）第 4 条：不引入 prom-client，自写文本格式）。
// - 自己的注册表，只有三种指标：计数器、直方图、仪表（采集时现读）。输出 Prometheus 文本格式 0.0.4：每个指标族 # HELP / # TYPE 各一行，
//   HELP 转义反斜杠与换行，标签值转义反斜杠、双引号与换行；直方图输出累计的 _bucket{le=…}（含 +Inf）、_sum、_count。
// - 指标只经 counter / histogram / gauge 登记：名字必须 starbuddy_ 开头，标签名必须在 LABELS 白名单里（服务、路由模板、方法、状态码类别、
//   学科、任务类型、供应商、结果）；每次写入前标签值再过 labelValue——服务、方法、状态码类别、学科、任务类型、供应商、结果只收枚举集合
//   （枚举外的学科 / 任务类型 / 供应商 / 结果一律记 other），路由只收路由表模板形状（像 UUID、手机号、邮箱、长数字、原始路径的值拒绝）。
//   学生 / 家长 / 账号标识、会话、内容在任何标签里都不出现。
// - 计量：预留、结算、释放（回滚）的结果编码与冲突（对外 409），以及原有的计量事件；只在事务提交之后计数（meter.ts），计数出错不影响计量结果。
// - 连接池（pg.Pool 的 totalCount / idleCount / waitingCount / options.max）与就绪（最近一次 /readyz 检查的结果，不另查库）在采集时现读。
// GET /metrics 只在单独的指标端口上（决定记录（续）第 5 条，BFF_METRICS_PORT，src/main.ts）；业务端口不服务 /metrics（表外 404）；
// k8s NetworkPolicy 只给采集组件放行指标端口（deploy/k8s/staging.yaml）。
export const METRICS_PATH = '/metrics';
export const SERVICE = 'bff';
export const DEFAULT_METRICS_PORT = 9787;  // staging 的指标监听缺省端口（env-inventory 登记）

/** 指标监听端口（BFF_METRICS_PORT）：设了就用（1–65535 的规范整数，不能与业务端口相同）；没设时 staging 用缺省 9787、dev / test 不起 */
export function metricsPort(raw: string | undefined, env: string, mainPort: number): number | null {
  let port: number | null;
  if (raw === undefined || raw === '') port = env === 'staging' ? DEFAULT_METRICS_PORT : null;
  else if (!/^[1-9]\d{0,4}$/.test(raw) || Number(raw) > 65535) throw new Error('BFF_METRICS_PORT 必须是 1–65535 的整数');
  else port = Number(raw);
  if (port !== null && port === mainPort) throw new Error('BFF_METRICS_PORT 不能与业务端口 PORT 相同');
  return port;
}
export const CONTENT_TYPE = 'text/plain; version=0.0.4; charset=utf-8';
export const LABELS = ['service', 'route', 'method', 'status_class', 'subject', 'task', 'provider', 'outcome'] as const;
export type LabelName = (typeof LABELS)[number];
const METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'other']);
const STATUS_CLASSES = new Set(['1xx', '2xx', '3xx', '4xx', '5xx']);
const SERVICES = new Set(['bff']);
// 枚举（与 Python 各服务 obs.py 的 ENUMS 一致，deploy/tests/test_obs_shared.py 核对）；枚举外记 other
export const ENUMS: Record<'subject' | 'task' | 'provider' | 'outcome', ReadonlySet<string>> = {
  subject: new Set(['aya', 'zhixing', 'wenli', 'echo', 'other']),
  task: new Set(['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8', 'T9', 'meter-expire', 'meter-renew', 'erasures', 'freeze', 'sweep',
    'mastery-sweep', 'expire-affairs', 'spot-check', 'metrics', 'weekly', 'model-call-purge', 'other']),
  provider: new Set(['bailian', 'ark', 'fake-a', 'fake-b', 'fake-x', 'other']),
  outcome: new Set(['ok', 'other', 'unavailable', 'rejected', 'budget_exceeded', 'error', 'timeout', 'rate_limited', 'server', 'auth',
    'client', 'bad_response', 'skipped_breaker_open', 'allowed', 'sealed', 'partial', 'refused', 'crashed', 'bad_output', 'lock_error',
    'lock_lost', 'skipped_locked', 'skipped_running', 'dry_run', 'reserved', 'dedup', 'existing', 'insufficient', 'daily_cap_reached',
    'curfew_soon', 'conflict', 'dedup_in_progress', 'no_entitlement', 'retained_no_ai', 'curfew', 'settled', 'released', 'refunded',
    'unknown', 'reserve_conflict', 'reserve_dedup_in_progress', 'settle_conflict', 'release_conflict', 'late_success_after_expiry',
    'replay_after_expiry', 'sealed_after_expiry', 'success_after_release', 'result_unknown', 'learning_retry']),
};
const NAME = /^starbuddy_[a-z][a-z0-9_]{2,80}$/;
const ROUTE = /^(?:unmatched|\/[A-Za-z0-9_./{}-]{0,120})$/;
const IDISH = /[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}|\d{5,}|@/;
const LATENCY_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60];

/** 一个标签值能不能写：服务、方法、状态码类别只收枚举，路由只收模板形状（不合格抛错，不写入）；学科、任务类型、供应商、结果枚举外记 other */
export function labelValue(name: string, value: unknown): string {
  if (!(LABELS as readonly string[]).includes(name)) throw new Error(`标签 ${name} 不在白名单里`);
  const v = typeof value === 'string' ? value : '';
  if (name in ENUMS) return ENUMS[name as keyof typeof ENUMS].has(v) ? v : 'other';
  const ok = name === 'service' ? SERVICES.has(v)
    : name === 'method' ? METHODS.has(v)
    : name === 'status_class' ? STATUS_CLASSES.has(v)
    : ROUTE.test(v) && !IDISH.test(v);
  if (!ok) throw new Error(`标签 ${name} 的取值不合格（只收低基数编码）`);
  return v;
}

function check(name: string, labelNames: readonly string[]): string[] {
  if (!NAME.test(name)) throw new Error(`指标名 ${name} 必须以 starbuddy_ 开头`);
  const bad = labelNames.filter((n) => !(LABELS as readonly string[]).includes(n));
  if (bad.length) throw new Error(`标签 ${bad.join(',')} 不在白名单里`);
  if (families.has(name)) throw new Error(`指标 ${name} 重复登记`);
  return [...labelNames];
}

function safe(labelNames: readonly string[], labels: Record<string, string>): string[] {
  const keys = Object.keys(labels).sort();
  if (keys.join(',') !== [...labelNames].sort().join(',')) throw new Error('标签与登记的不一致');
  return labelNames.map((k) => labelValue(k, labels[k]));
}

// ---------------------------------------------------------------- 文本格式
export function escapeHelp(s: string): string {
  return s.replace(/\\/g, '\\\\').replace(/\n/g, '\\n');
}
export function escapeLabel(s: string): string {
  return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}
export function formatValue(v: number): string {
  if (Number.isNaN(v)) return 'NaN';
  if (v === Infinity) return '+Inf';
  if (v === -Infinity) return '-Inf';
  return String(v);
}
function labelText(names: readonly string[], values: readonly string[], extra?: [string, string]): string {
  const pairs = names.map((n, i) => `${n}="${escapeLabel(values[i]!)}"`);
  if (extra) pairs.push(`${extra[0]}="${escapeLabel(extra[1])}"`);
  return pairs.length ? `{${pairs.join(',')}}` : '';
}

type Kind = 'counter' | 'histogram' | 'gauge';
interface Family { name: string; help: string; kind: Kind; render(): string[]; sample(labels: Record<string, string>, suffix?: string): number | undefined }
const families = new Map<string, Family>();

export interface SafeCounter { inc(labels: Record<string, string>, by?: number): void }
export interface SafeHistogram { observe(labels: Record<string, string>, v: number): void }

export function counter(name: string, help: string, labelNames: readonly LabelName[]): SafeCounter {
  const names = check(name, labelNames);
  const values = new Map<string, { labels: string[]; v: number }>();
  families.set(name, {
    name, help, kind: 'counter',
    render: () => [...values.values()].map((s) => `${name}${labelText(names, s.labels)} ${formatValue(s.v)}`),
    sample: (labels) => values.get(JSON.stringify(names.map((n) => labels[n] ?? '')))?.v,
  });
  return {
    inc(labels, by = 1) {
      if (!(by >= 0) || !Number.isFinite(by)) throw new Error('计数器只能加非负有限数');
      const lv = safe(names, labels);
      const key = JSON.stringify(lv);
      const cur = values.get(key) ?? { labels: lv, v: 0 };
      cur.v += by;
      values.set(key, cur);
    },
  };
}

export function histogram(name: string, help: string, labelNames: readonly LabelName[], buckets: readonly number[] = LATENCY_BUCKETS): SafeHistogram {
  const names = check(name, labelNames);
  const bs = [...buckets].sort((a, b) => a - b);
  const values = new Map<string, { labels: string[]; counts: number[]; sum: number; count: number }>();
  families.set(name, {
    name, help, kind: 'histogram',
    render: () => {
      const out: string[] = [];
      for (const s of values.values()) {
        bs.forEach((b, i) => out.push(`${name}_bucket${labelText(names, s.labels, ['le', formatValue(b)])} ${s.counts[i]}`));
        out.push(`${name}_bucket${labelText(names, s.labels, ['le', '+Inf'])} ${s.count}`);
        out.push(`${name}_sum${labelText(names, s.labels)} ${formatValue(s.sum)}`);
        out.push(`${name}_count${labelText(names, s.labels)} ${s.count}`);
      }
      return out;
    },
    sample: (labels, suffix = '_count') => {
      const s = values.get(JSON.stringify(names.map((n) => labels[n] ?? '')));
      return s === undefined ? undefined : suffix === '_sum' ? s.sum : s.count;
    },
  });
  return {
    observe(labels, v) {
      if (!Number.isFinite(v)) throw new Error('直方图只收有限数');
      const lv = safe(names, labels);
      const key = JSON.stringify(lv);
      const cur = values.get(key) ?? { labels: lv, counts: bs.map(() => 0), sum: 0, count: 0 };
      bs.forEach((b, i) => { if (v <= b) cur.counts[i]! += 1; });
      cur.sum += v;
      cur.count += 1;
      values.set(key, cur);
    },
  };
}

/** 采集时现读的仪表：read 返回（标签值 → 数值）；读不出来（抛错）这一轮不报 */
export function gauge(name: string, help: string, labelNames: readonly LabelName[], read: () => Array<[Record<string, string>, number]>): void {
  const names = check(name, labelNames);
  const current = (): Array<[string[], number]> => {
    try {
      return read().filter(([, v]) => Number.isFinite(v)).map(([l, v]) => [safe(names, l), v]);
    } catch {
      return [];
    }
  };
  families.set(name, {
    name, help, kind: 'gauge',
    render: () => current().map(([lv, v]) => `${name}${labelText(names, lv)} ${formatValue(v)}`),
    sample: (labels) => current().find(([lv]) => JSON.stringify(lv) === JSON.stringify(names.map((n) => labels[n] ?? '')))?.[1],
  });
}

/** Prometheus 文本格式（按指标名排序；没有样本的族也输出 HELP / TYPE） */
export function render(): string {
  const lines: string[] = [];
  for (const f of [...families.values()].sort((a, b) => a.name.localeCompare(b.name))) {
    lines.push(`# HELP ${f.name} ${escapeHelp(f.help)}`, `# TYPE ${f.name} ${f.kind}`, ...f.render());
  }
  return lines.join('\n') + '\n';
}

/** 测试与运维核对用：一个样本的当前值（直方图用 suffix 选 _count / _sum） */
export function sampleValue(name: string, labels: Record<string, string>, suffix?: string): number {
  return families.get(name)?.sample(labels, suffix) ?? 0;
}

/** 本进程注册的指标名（部署检查与登记清单核对用） */
export function catalog(): string[] {
  return [...families.keys()].sort();
}

export const httpRequests = counter('starbuddy_http_requests_total', 'HTTP 请求数（路由模板、方法、状态码类别）',
  ['service', 'route', 'method', 'status_class']);
export const httpDuration = histogram('starbuddy_http_request_duration_seconds', 'HTTP 请求耗时（秒）', ['service', 'route', 'method']);
export const meterReserve = counter('starbuddy_meter_reserve_total', '计量预留（meter_reserve 的结果编码；事务提交之后计数）', ['outcome']);
export const meterSettle = counter('starbuddy_meter_settle_total', '计量结算（meter_settle 的结果编码；事务提交之后计数）', ['outcome']);
export const meterRelease = counter('starbuddy_meter_release_total', '计量释放即回滚（meter_release 的结果编码；事务提交之后计数）', ['outcome']);
export const meterConflict = counter('starbuddy_meter_conflict_total', '计量冲突（对外 409；outcome 为哪一步的哪种冲突）', ['outcome']);
/** LT-06：退出测试作废白名单条目时审计写失败（内存里已作废，但重启从审计恢复时会丢失这次作废）；告警 StarbuddyStagingOtpRevokeAuditFailed */
export const stagingOtpRevokeAuditFailed = counter('starbuddy_staging_otp_revoke_audit_failed_total',
  'staging 白名单固定码：退出测试作废条目时审计写失败的次数（内存已作废，重启后会丢失这次作废，需管理员按手册用工具作废）', ['service']);
export const meterEvents = counter('starbuddy_meter_events_total', '计量事件（少扣、结果未知、重试等，metering/metrics.ts 的事件名）', ['outcome']);

export function statusClass(code: number): string {
  return code >= 100 && code <= 599 ? `${Math.floor(code / 100)}xx` : '5xx';
}

/** 一次 HTTP 请求：路由模板（表外 unmatched）、方法、状态码、耗时（毫秒）。出任何错都只丢这一条，不影响请求 */
export function observeHttp(route: string, method: string | undefined, status: number, ms: number): void {
  try {
    const m = (method ?? '').toUpperCase();
    const labels = { service: SERVICE, route, method: METHODS.has(m) ? m : 'other' };
    httpRequests.inc({ ...labels, status_class: statusClass(status) });
    httpDuration.observe(labels, Math.max(0, ms) / 1000);
  } catch {
    // 标签不合格等：不记这一条
  }
}

// 测试钩子：模拟计数出错、记录计数时机（只在测试里设置；生产不设）
export const meterTesting: { fail: boolean; onObserve?: (step: string) => void } = { fail: false };

/** 计量函数的结果编码 → 计数（冲突另计一次）。调用方在事务提交之后调用；任何异常都吞掉，不影响计量结果 */
export function observeMeter(step: 'reserve' | 'settle' | 'release', outcome: unknown): void {
  try {
    meterTesting.onObserve?.(step);
    if (meterTesting.fail) throw new Error('synthetic metrics failure');
    const o = labelValue('outcome', outcome);
    (step === 'reserve' ? meterReserve : step === 'settle' ? meterSettle : meterRelease).inc({ outcome: o });
    if (o === 'conflict' || o === 'dedup_in_progress') meterConflict.inc({ outcome: `${step}_${o}` });
  } catch {
    // 计数失败只丢这一次计数（LT-05 决定记录（续）第 6 条）
  }
}

// ---------------------------------------------------------------- 采集时现读：连接池、就绪
export interface PoolStats { totalCount: number; idleCount: number; waitingCount: number; options?: { max?: number } }
let poolSource: (() => PoolStats | undefined) | undefined;
let readySource: (() => boolean | undefined) | undefined;
export function trackPool(fn: () => PoolStats | undefined): void { poolSource = fn; }
export function trackReady(fn: () => boolean | undefined): void { readySource = fn; }

function poolGauge(name: string, help: string, pick: (p: PoolStats) => number | undefined): void {
  gauge(name, help, ['service'], () => {
    const p = poolSource?.();
    const v = p === undefined ? undefined : pick(p);
    return typeof v === 'number' ? [[{ service: SERVICE }, v]] : [];
  });
}
poolGauge('starbuddy_db_pool_size', '连接池当前连接数', (p) => p.totalCount);
poolGauge('starbuddy_db_pool_idle', '连接池空闲连接数', (p) => p.idleCount);
poolGauge('starbuddy_db_pool_waiting', '等待连接的请求数', (p) => p.waitingCount);
poolGauge('starbuddy_db_pool_max', '连接池上限', (p) => p.options?.max);
gauge('starbuddy_ready', '最近一次就绪检查（1 就绪、0 未就绪或还没检查）', ['service'],
  () => [[{ service: SERVICE }, readySource?.() === true ? 1 : 0]]);
