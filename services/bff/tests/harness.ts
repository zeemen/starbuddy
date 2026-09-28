// 集成测试夹具：连接 db/tests/pg-ephemeral.sh 起的一次性 Postgres（PGHOST 等环境变量），全部合成数据（CLAUDE.md 规则 8）。
//   - 登录角色 bff_app（只继承 starbuddy_bff）、scheduler_app（只继承 starbuddy_scheduler）
//   - 计量时钟：把 platform.meter_now() 换成读 bfftest.clock 的版本，只在测试库里做
//   - 已发布的测试额度配置 bfftest-plan@1（照抄种子 ch8-hypothesis@1）
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import type { LearningCall, LearningOutcome, LearningReplay } from '../src/metering/learning-client.ts';
import type { MeteringMetric, Metrics } from '../src/metering/metrics.ts';

export const HAS_DB = Boolean(process.env['PGHOST']);
export const CONFIG = 'bfftest-plan@1';
const T0 = Date.parse('2026-10-15T00:00:00Z');

export interface Harness {
  su: pg.Pool;
  bff: pg.Pool;
  scheduler: pg.Pool;
  clockMs: () => number;
  setClock: (ms: number) => Promise<void>;
  advance: (ms: number) => Promise<void>;
  close: () => Promise<void>;
}

let clock = T0;

export async function setupHarness(): Promise<Harness> {
  const su = new pg.Pool({ user: 'postgres', max: 5 });
  await su.query(`
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN
        CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;
        CREATE ROLE scheduler_app LOGIN IN ROLE starbuddy_scheduler;
      END IF;
    END $$;
    CREATE SCHEMA IF NOT EXISTS bfftest;
    GRANT USAGE ON SCHEMA bfftest TO starbuddy_authz;
    CREATE TABLE IF NOT EXISTS bfftest.clock (now timestamptz NOT NULL);
    GRANT SELECT ON bfftest.clock TO starbuddy_authz;
    CREATE OR REPLACE FUNCTION platform.meter_now() RETURNS timestamptz
      LANGUAGE sql STABLE SET search_path = pg_catalog AS $f$ SELECT now FROM bfftest.clock $f$;
  `);
  await su.query('DELETE FROM bfftest.clock');
  await su.query('INSERT INTO bfftest.clock VALUES ($1)', [new Date(T0)]);
  clock = T0;
  const { rows } = await su.query('SELECT 1 FROM platform.plan_config WHERE version = $1', [CONFIG]);
  if (rows.length === 0) {
    await su.query(`INSERT INTO platform.plan_config (version, note_code) VALUES ($1, 'bff_integration_test')`, [CONFIG]);
    await su.query(`INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
                    SELECT $1, tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'ch8-hypothesis@1'`, [CONFIG]);
    await su.query(`INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
                    SELECT $1, tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'ch8-hypothesis@1'`, [CONFIG]);
    await su.query(`UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = $1`, [CONFIG]);
  }
  // 选科只能选已上线（有已发布课程版本）的学科：阿鸦、文砺
  await su.query(`INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
                  SELECT 'nanjing', 2027, 9, 'bubian-2024', s::platform.subject, '0.1.0', 'published', now()
                    FROM unnest(ARRAY['aya', 'wenli']) s
                   WHERE NOT EXISTS (SELECT 1 FROM platform.curriculum_version c WHERE c.subject = s::platform.subject AND c.status = 'published')`);
  const bff = new pg.Pool({ user: 'bff_app', max: 40 });
  const scheduler = new pg.Pool({ user: 'scheduler_app', max: 5 });
  const setClock = async (ms: number) => {
    clock = ms;
    await su.query('UPDATE bfftest.clock SET now = $1', [new Date(ms)]);
  };
  return {
    su, bff, scheduler,
    clockMs: () => clock,
    setClock,
    advance: (ms: number) => setClock(clock + ms),
    close: async () => { await Promise.all([su.end(), bff.end(), scheduler.end()]); },
  };
}

export interface Family {
  guardian: string;
  household: string;
  students: { id: string; account: string }[];
}

// 一户家庭：家长建档，n 个孩子；由家长经函数开通订阅（与生产路径相同）。
// 基础会员（trial）固定 basic；试点（pilot_virtual）固定 PRO，家庭先进试点名单（名单只能由人工写入，这里用超级用户）
export async function createFamily(h: Harness, opts: { source: 'trial' | 'pilot_virtual'; children?: number }): Promise<Family> {
  const guardian = randomUUID();
  const household = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [guardian]);
  await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [household, guardian]);
  if (opts.source === 'pilot_virtual') {
    await h.su.query(`INSERT INTO platform.pilot_household (household_id, cohort_code) VALUES ($1, 'bff_integration')`, [household]);
  }
  const students: Family['students'] = [];
  for (let i = 0; i < (opts.children ?? 1); i++) {
    const account = randomUUID();
    const id = randomUUID();
    await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'student')`, [account]);
    await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 9, 'nanjing')`,
                     [id, account, household]);
    await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                      VALUES ($1, $2, 'household_creator')`, [guardian, id]);
    const c = await h.bff.connect();
    try {
      await c.query('BEGIN');
      await c.query("SELECT set_config('app.account_id', $1, true)", [guardian]);
      const { rows } = await c.query('SELECT platform.meter_open_subscription($1, $2) AS r', [id, opts.source]);
      if (rows[0].r.outcome !== 'opened') throw new Error('subscription not opened: ' + JSON.stringify(rows[0].r));
      await c.query('COMMIT');
    } finally {
      c.release();
    }
    students.push({ id, account });
  }
  return { guardian, household, students };
}

export async function counters(h: Harness, studentId: string, resource: string): Promise<{ granted: number; reserved: number; settled: number }> {
  const { rows } = await h.su.query(
    `SELECT e.granted::int, e.reserved::int, e.settled::int FROM platform.entitlement e
       JOIN platform.subscription s ON s.id = e.subscription_id
      WHERE e.student_id = $1 AND e.resource_type = $2 AND s.status IN ('active', 'grace') AND s.source <> 'addon'
        AND platform.meter_now() >= e.period_start AND platform.meter_now() < e.period_end`, [studentId, resource]);
  return rows[0];
}

// 两个对账视图都为零行（设计文档 I1、I2）
export async function ledgerConsistent(h: Harness): Promise<boolean> {
  const { rows } = await h.su.query(`SELECT (SELECT count(*) FROM platform.entitlement_reconciliation)
                                          + (SELECT count(*) FROM platform.reservation_ledger_mismatch) AS n`);
  return Number(rows[0].n) === 0;
}

export async function reservationsByStudent(h: Harness, studentId: string): Promise<{ state: string; reserved_qty: number }[]> {
  const { rows } = await h.su.query(`SELECT state::text, reserved_qty::int FROM platform.usage_reservation WHERE student_id = $1`, [studentId]);
  return rows;
}

export function key(): string {
  return randomUUID();
}

export function gradeBody(studentId: string, itemRef = 'aya:synthetic-0301-07', text = '2(x-1)'): Record<string, unknown> {
  return {
    context: { subject: 'aya', student_ref: studentId, request_id: randomUUID(), locale: 'zh-CN' },
    attempt: { item_ref: itemRef, response: { text }, time_spent_ms: 30000 },
  };
}

// 假学习服务：按 key 记住首次成功的结果（学习服务的幂等），脚本决定每次调用的结果；
// 重放接口只查不执行：没有结果时封存 key，之后同 key 的原请求 409 IDEMPOTENCY_KEY_SEALED；学生或 usage_id 对不上时 404 但不封存
export class FakeLearning {
  calls: { method: string; key: string; usageId: string }[] = [];
  replays: { method: string; key: string; studentRef: string; usageId: string }[] = [];
  sealed = new Set<string>();
  busy = false;
  private done = new Map<string, LearningOutcome>();
  private owner = new Map<string, { studentRef: string; usageId: string }>();
  private script: (attempt: number, key: string) => LearningOutcome | Promise<LearningOutcome>;
  constructor(script: (attempt: number, key: string) => LearningOutcome | Promise<LearningOutcome> =
                () => ({ kind: 'success', status: 200, body: { verdict: 'correct' }, replayed: false })) {
    this.script = script;
  }

  setScript(script: (attempt: number, key: string) => LearningOutcome | Promise<LearningOutcome>): void {
    this.script = script;
  }

  lastBody: Record<string, unknown> | undefined;

  readonly call: LearningCall = async (method, body) => {
    this.lastBody = body;
    const ctx = body['context'] as { idempotency_key: string; usage_id: string };
    this.calls.push({ method, key: ctx.idempotency_key, usageId: ctx.usage_id });
    if (this.sealed.has(ctx.idempotency_key)) {
      return { kind: 'failure', status: 409, problem: { code: 'IDEMPOTENCY_KEY_SEALED', retryable: false } };
    }
    this.owner.set(ctx.idempotency_key, { studentRef: (body['context'] as { student_ref: string }).student_ref, usageId: ctx.usage_id });
    const prior = this.done.get(ctx.idempotency_key);
    if (prior) return { ...prior, replayed: true } as LearningOutcome;
    const attempt = this.calls.filter((c) => c.key === ctx.idempotency_key).length;
    const out = await this.script(attempt, ctx.idempotency_key);
    if (out.kind === 'success') this.done.set(ctx.idempotency_key, out);
    return out;
  };

  readonly replay: LearningReplay = async (method, key, studentRef, usageId) => {
    this.replays.push({ method, key, studentRef, usageId });
    const notFound = { kind: 'not_found' as const, problem: { code: 'REPLAY_NOT_FOUND', retryable: false } };
    if (this.busy) return { kind: 'busy' };
    const o = this.owner.get(key);
    if (o && (o.studentRef !== studentRef || o.usageId !== usageId)) return notFound;
    const prior = this.done.get(key);
    if (prior?.kind === 'success') return { kind: 'found', body: prior.body };
    this.sealed.add(key);
    return notFound;
  };

  callsFor(k: string): number {
    return this.calls.filter((c) => c.key === k).length;
  }
}

export class CountingMetrics implements Metrics {
  counts = new Map<MeteringMetric, number>();
  increment(name: MeteringMetric): void {
    this.counts.set(name, (this.counts.get(name) ?? 0) + 1);
  }
  get(name: MeteringMetric): number {
    return this.counts.get(name) ?? 0;
  }
}

export const SUCCESS: LearningOutcome = { kind: 'success', status: 200, body: { verdict: 'correct' }, replayed: false };
export const REJECTED: LearningOutcome = { kind: 'failure', status: 422, problem: { code: 'INVALID_REQUEST', retryable: false } };
export const UNAVAILABLE: LearningOutcome = { kind: 'unknown', reason: 'retryable 503 UPSTREAM_UNAVAILABLE' };
