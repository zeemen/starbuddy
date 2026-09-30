// 连续使用提醒（spec 第 10 节）：学生会话连续使用 N 分钟（配置，默认 40）后，壳顶部出现提醒条，建议休息 M 分钟；只提醒不锁定。
// 计时以 BFF 为准，前端只负责显示，不写 localStorage。连续使用计时只用于提醒，不是计量的“有效时长”。
//
// 记在哪里：spec 要求会话记录“开始时间与最近一次确认休息的时间”，那需要在 platform.auth_session 上加列与函数（迁移，必停，
// H4 不写 db/，见 lessons H4 待人确认）。在它落地之前用这个进程内的存储：键是会话令牌的带密钥 HMAC（不存令牌本身），
// 多实例部署时各实例各记各的（最坏是提醒晚出现），进程重启后从头计。只存两个时间戳，按会话最长寿命过期清理。
//
// 规则（纯函数 restState）：两次壳内请求之间停了 ≥ 建议休息分钟数，视为已经休息过，从头计；否则累计；
// 学生点“知道了”（确认休息）也从头计。
export interface RestMark { since: number; last: number }
export interface RestRule { continuousMinutes: number; breakMinutes: number }
export interface RestView { show: boolean; continuous_minutes: number; threshold_minutes: number; suggest_break_minutes: number }

const MIN = 60_000;

export function restState(prev: RestMark | undefined, now: number, rule: RestRule): { mark: RestMark; view: RestView } {
  const fresh = !prev || now - prev.last >= rule.breakMinutes * MIN || now < prev.since;
  const mark = { since: fresh ? now : prev!.since, last: now };
  const minutes = Math.floor((now - mark.since) / MIN);
  return { mark, view: { show: minutes >= rule.continuousMinutes, continuous_minutes: minutes,
                         threshold_minutes: rule.continuousMinutes, suggest_break_minutes: rule.breakMinutes } };
}

export interface RestStore {
  touch(key: string, now: number, rule: RestRule): RestView;
  /** 确认休息：从现在起重新计 */
  reset(key: string, now: number, rule: RestRule): RestView;
}

export class MemoryRestStore implements RestStore {
  private readonly marks = new Map<string, RestMark>();
  private readonly maxAgeMs: number;
  private readonly maxEntries: number;
  constructor(maxAgeMs: number, maxEntries = 100_000) {
    this.maxAgeMs = maxAgeMs;
    this.maxEntries = maxEntries;
  }

  private sweep(now: number): void {
    if (this.marks.size < this.maxEntries) return;
    for (const [k, m] of this.marks) if (now - m.last > this.maxAgeMs) this.marks.delete(k);
    // 仍然太多：丢最早的（Map 按插入顺序），只影响提醒
    while (this.marks.size >= this.maxEntries) this.marks.delete(this.marks.keys().next().value!);
  }

  touch(key: string, now: number, rule: RestRule): RestView {
    const { mark, view } = restState(this.marks.get(key), now, rule);
    this.marks.delete(key);
    this.sweep(now);
    this.marks.set(key, mark);
    return view;
  }

  reset(key: string, now: number, rule: RestRule): RestView {
    this.marks.delete(key);
    return this.touch(key, now, rule);
  }
}
