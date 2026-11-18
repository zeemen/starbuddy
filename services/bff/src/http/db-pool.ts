// 处理请求的数据库连接池的超时与兜底（specs/db-call-timeouts）：数据库卡住（锁等待、慢查询、连接被占满）时请求不能一直挂着，
// 否则处理函数不结束、分学科闸门的名额也不归还（PLT-16 lessons 待人确认第 4 条）。数值放在这一处（2026-11-18 用户确认默认值）：
//   - 取连接最多等 3 秒（池满时排队的上限）；
//   - 单条语句 10 秒（连接级 statement_timeout，库端取消，错误码 57014）；客户端另设 12 秒兜底（库端没响应时）；
//   - 事务开着却不发语句超过 15 秒，库端断开连接（idle_in_transaction_session_timeout），锁与连接都放出来。
// 只用于处理请求的池；调度器、命令行任务、迁移执行器不用它（它们有长查询）。
import type pg from 'pg';

export const DB_TIMEOUTS = {
  connectionTimeoutMillis: 3_000,
  statementTimeoutMs: 10_000,
  queryTimeoutMs: 12_000,
  idleInTransactionMs: 15_000,
} as const;

export type DbTimeouts = { [K in keyof typeof DB_TIMEOUTS]: number };

/** 处理请求的连接池配置；overrides 只给测试缩短时限用 */
export function requestPoolConfig(connectionString: string | undefined, max = 10, overrides: Partial<DbTimeouts> = {}): pg.PoolConfig {
  const t = { ...DB_TIMEOUTS, ...overrides };
  return {
    ...(connectionString === undefined ? {} : { connectionString }),
    max,
    connectionTimeoutMillis: t.connectionTimeoutMillis,
    statement_timeout: t.statementTimeoutMs,
    query_timeout: t.queryTimeoutMs,
    idle_in_transaction_session_timeout: t.idleInTransactionMs,
  };
}
