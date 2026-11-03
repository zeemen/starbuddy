// 额度与时长状态（spec 第 9 节）：数值全部来自 BFF（BFF 取自 platform.meter_guard_status 与 entitlement_balance），前端不计算。
// 学生端：试用剩余天数 / 留存态 / 今日剩余分钟 / 单日上限已到 / 宵禁 / 距宵禁；不出现购买、升级或营销类文字。
// 家长端：另有试用结束后的说明、开通入口占位（订阅开通不在 4-H）、上限与宵禁及“调整”入口；不显示“距宵禁”。
import type { Balance } from '../lib/shell-api.ts';
import { BALANCE } from './copy.ts';

export function StatusBar({ balance, audience, timeHref }: { balance: Balance; audience: 'student' | 'guardian'; timeHref?: string }) {
  const b = balance;
  const t = b.time;
  const guardian = audience === 'guardian';
  const lines: { key: string; text: string; link?: string }[] = [];
  if (b.state === 'trialing' && b.trial_days_left !== null) lines.push({ key: 'trial', text: BALANCE.trialDaysLeft(b.trial_days_left) });
  if (b.state === 'trialing' && guardian) lines.push({ key: 'trial-after', text: BALANCE.trialAfter(b.practice_daily_limit) });
  if (b.state === 'retained') {
    lines.push({ key: 'retained', text: BALANCE.retained(b.practice_remaining_today) });
    lines.push({ key: 'ai-off', text: BALANCE.aiUnavailable });
    if (guardian) lines.push({ key: 'open', text: BALANCE.openPlaceholder });
  }
  if (b.state === 'none') lines.push({ key: 'none', text: guardian ? BALANCE.notOpenedGuardian : BALANCE.needGuardianStudent });
  const serving = b.state === 'trialing' || b.state === 'active' || b.state === 'grace';
  if (serving && t.in_curfew) {
    lines.push({ key: 'curfew', text: BALANCE.curfew(t.curfew_start, t.curfew_end), ...(guardian && timeHref ? { link: timeHref } : {}) });
  } else if (serving && t.daily_cap_reached) {
    lines.push({ key: 'cap', text: BALANCE.capReached, ...(guardian && timeHref ? { link: timeHref } : {}) });
  } else if (serving) {
    lines.push({ key: 'minutes', text: BALANCE.minutesLeft(Math.max(0, Math.floor(t.minutes_remaining_today))) });
    if (!guardian && t.curfew_near) lines.push({ key: 'curfew-near', text: BALANCE.curfewNear(t.minutes_until_curfew) });
  }
  if (guardian) lines.push({ key: 'settings', text: BALANCE.capAndCurfew(t.daily_cap, t.curfew_start, t.curfew_end) });
  return (
    <ul className="status-bar" data-testid="status-bar" data-state={b.state}>
      {lines.map((l) => (
        <li key={l.key} data-status={l.key}>
          {l.text}
          {l.link ? <> <a href={l.link}>{l.key === 'curfew' ? BALANCE.adjustCurfew : BALANCE.adjustCap}</a></> : null}
        </li>
      ))}
    </ul>
  );
}
