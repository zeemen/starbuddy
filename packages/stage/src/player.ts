// 播放状态机（纯函数）：idle → playing → paused → ended，支持逐步前进、后退、跳到某步。
// cursor = 已执行的步数（0..total）；画面由 boardAt(dsl, cursor) 从头重算。计时由调用方负责：播放中每隔一段时间发一次 tick。
// 不做 live（学生插话转实时需要模型，另起）。

export type PlayerStatus = 'idle' | 'playing' | 'paused' | 'ended';

export interface PlayerState {
  readonly status: PlayerStatus;
  readonly cursor: number;
  readonly total: number;
}

export type PlayerEvent =
  | { type: 'play' }
  | { type: 'pause' }
  | { type: 'tick' }
  | { type: 'next' }
  | { type: 'prev' }
  | { type: 'seek'; step: number }
  | { type: 'reset' };

/** 初始状态：空白板。total 必须是正整数（校验器保证 DSL 至少一步） */
export function initialPlayer(total: number): PlayerState {
  if (!Number.isInteger(total) || total < 1) throw new RangeError('total 必须是正整数');
  return { status: 'idle', cursor: 0, total };
}

/** 按 cursor 推出状态：0 是 idle，走完是 ended，中间保持“播放中”或“暂停” */
function at(s: PlayerState, cursor: number, playing: boolean): PlayerState {
  if (cursor <= 0) return { status: 'idle', cursor: 0, total: s.total };
  if (cursor >= s.total) return { status: 'ended', cursor: s.total, total: s.total };
  return { status: playing ? 'playing' : 'paused', cursor, total: s.total };
}

/** 状态转移。不合法的事件（如结束后 tick、越界跳转）返回原状态 */
export function step(s: PlayerState, e: PlayerEvent): PlayerState {
  switch (e.type) {
    case 'play':
      if (s.status === 'idle') return at(s, 1, true);
      if (s.status === 'paused') return { ...s, status: 'playing' };
      return s;
    case 'pause':
      return s.status === 'playing' ? { ...s, status: 'paused' } : s;
    case 'tick':
      return s.status === 'playing' ? at(s, s.cursor + 1, true) : s;
    case 'next':
      if (s.status === 'ended') return s;
      return at(s, s.cursor + 1, s.status === 'playing');
    case 'prev':
      if (s.status === 'idle') return s;
      return at(s, s.cursor - 1, s.status === 'playing');
    case 'seek':
      if (!Number.isInteger(e.step) || e.step < 0 || e.step > s.total) return s;
      return at(s, e.step, s.status === 'playing');
    case 'reset':
      return { status: 'idle', cursor: 0, total: s.total };
  }
}
