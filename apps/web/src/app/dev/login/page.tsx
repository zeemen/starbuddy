// 开发身份桩的选择页（spec 范围第 1 条）：只列合成账号，只在 STARBUDDY_DEV_AUTH=1 时存在。4-H 删除。
import { notFound } from 'next/navigation';
import { SYNTHETIC_ACCOUNTS } from '../../../dev/accounts.ts';
import { SYNTHETIC_STAFF } from '../../../dev/staff.ts';
import { headers } from 'next/headers';
import { devAuthEnabled, isLoopbackHost, safeNext } from '../../../lib/dev-auth.ts';

export const dynamic = 'force-dynamic';

export default async function DevLoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  if (!devAuthEnabled() || !isLoopbackHost((await headers()).get('host'))) notFound();
  const { next } = await searchParams;
  const target = safeNext(next ?? null);
  return (
    <main>
      <h1>开发登录（合成账号）</h1>
      <p className="muted">只用于本地开发与测试；正式登录在冲刺 4-H 实现。</p>
      <ul className="login-list">
        {SYNTHETIC_ACCOUNTS.map((a) => {
          const dest = target === '/dev/login' ? `/star-map/${a.student_id}` : target;
          return (
            <li key={a.account_id}>
              <a href={`/dev/session?account=${a.account_id}&next=${encodeURIComponent(dest)}`}>{a.label}</a>
            </li>
          );
        })}
      </ul>
      <h2>审核台（合成教研）</h2>
      <ul className="login-list">
        {SYNTHETIC_STAFF.map((a) => (
          <li key={a.account_id}>
            <a href={`/dev/session?staff=${a.account_id}`}>{a.label}</a>
          </li>
        ))}
      </ul>
    </main>
  );
}
