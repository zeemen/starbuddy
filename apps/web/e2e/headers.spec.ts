// 安全响应头与 CSP（specs/unified-shell-sprint4 第 16 节，H2；第二轮 7.6 的完整爬取，H6）：
// 按 e2e/crawl-pages.ts 的登记爬遍壳内全部页面（未登录、学生、家长；信任界面的页面在开关全关的这一套里是 404 页，安全头照样核对，
// 开关打开的一套在 e2e/trust-on.spec.ts 里爬），核对见 e2e/headers-check.ts。tests/headers-crawl.test.ts 保证 src/app 的每个页面都登记了。
import { expect, test } from '@playwright/test';
import { EXTRA_ANON, S1, targets } from './crawl-pages.ts';
import { crawl } from './headers-check.ts';
import { loginGuardian, loginStudent } from './session.ts';

// 这一套（trust-off）的信任界面开关全关
const pages = (who: 'anon' | 'student' | 'guardian') => targets(who, S1, false);

test('未登录：登录页、404、符号样张页的安全头与 nonce', async ({ page }) => {
  await crawl(page, [...pages('anon'), ...EXTRA_ANON.map((x) => ({ ...x, final: x.path }))]);
});

test('学生模式：学生壳全部页面的安全头与 nonce；页面脚本在 CSP 下照常运行（表单水合）', async ({ page }) => {
  await loginStudent(page, S1);
  await crawl(page, pages('student'));
  await page.goto('/student');
  await expect(page.getByTestId('exit-student-mode')).toHaveAttribute('data-hydrated', 'true');
});

test('家长：家长端全部页面的安全头与 nonce', async ({ page }) => {
  await loginGuardian(page, 'gS');
  await crawl(page, pages('guardian'));
});
