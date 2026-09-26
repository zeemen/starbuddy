// 计量指标：少扣、结果未知这类情况不报错，但必须被看见（设计文档场景 5）。接入监控系统之前默认什么也不做。
export type MeteringMetric =
  | 'late_success_after_expiry'   // 学习服务成功了，但预留已过期：不扣费
  | 'replay_after_expiry'         // 预留过期后同 key 重试：拿重放结果，不扣费（N17 缺口）
  | 'result_unknown'              // 重试预算用完仍不知道结果：留给过期任务
  | 'learning_retry';             // 结果未知时的一次重试

export interface Metrics {
  increment(name: MeteringMetric): void;
}

export const noopMetrics: Metrics = { increment() {} };
