// 安全响应头与 CSP（specs/unified-shell-sprint4 第 16 节，H2；第二轮 7.6 的完整爬取随 H6）：
// 壳内页面（登录、家长首页、学生页、星图、建档、404、开发构建里的符号样张页）的每个响应都有带本次 nonce 的 CSP
// （不含 unsafe-inline、unsafe-eval，frame-ancestors 'none'）、no-store、nosniff、no-referrer；HTML 里每个 <script> 都带本次的 nonce，
// 没有内联 style 属性与不带 nonce 的 <style>；浏览器里没有任何 CSP 违规报告。测试环境不发 HSTS（只在 staging、pilot、生产）。
import { expect, test, type Page } from '@playwright/test';
import { loginStudent } from './session.ts';

const S1 = '5a000000-0000-4000-8000-0000000000c1';

function checkHeaders(h: Record<string, string>, path: string): string {
  const csp = h['content-security-policy'] ?? '';
  const m = /script-src 'self' 'nonce-([A-Za-z0-9+/=]+)' 'strict-dynamic'/.exec(csp);
  expect(m, `${path} CSP 带 nonce`).not.toBeNull();
  expect(csp, path).toContain(`style-src 'self' 'nonce-${m![1]}'`);
  expect(csp, path).toContain("frame-ancestors 'none'");
  expect(csp, path).not.toMatch(/unsafe-inline|unsafe-eval/);
  expect(h['cache-control'] ?? '', path).toContain('no-store');
  expect(h['x-content-type-options'], path).toBe('nosniff');
  expect(h['referrer-policy'], path).toBe('no-referrer');
  expect(h['x-frame-options'], path).toBe('DENY');
  expect(h['strict-transport-security'], `${path} 测试环境不发 HSTS`).toBeUndefined();
  return m![1]!;
}

function checkHtml(html: string, nonce: string, path: string) {
  const scripts = [...html.matchAll(/<script\b[^>]*>/g)].map((x) => x[0]);
  expect(scripts.length, `${path} 有脚本`).toBeGreaterThan(0);
  for (const s of scripts) expect(s, path).toContain(`nonce="${nonce}"`);
  for (const s of [...html.matchAll(/<style\b[^>]*>/g)].map((x) => x[0])) expect(s, path).toContain(`nonce="${nonce}"`);
  expect(html, `${path} 没有内联 style 属性`).not.toMatch(/<[a-z][^>]*\sstyle="/i);
}

async function crawl(page: Page, paths: string[]) {
  const violations: string[] = [];
  page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text())) violations.push(m.text()); });
  const nonces = new Set<string>();
  for (const path of paths) {
    const res = await page.goto(path);
    expect(res, path).not.toBeNull();
    const nonce = checkHeaders(res!.headers(), path);
    checkHtml(await res!.text(), nonce, path);
    nonces.add(nonce);
    await page.waitForLoadState('networkidle');
  }
  expect(nonces.size, '每个请求一个新的 nonce').toBe(paths.length);
  expect(violations).toEqual([]);
}

test('未登录：登录页、404、符号样张页的安全头与 nonce', async ({ page }) => {
  await crawl(page, ['/login', '/no-such-page', '/dev/star-glyphs']);
});

test('学生模式：学生页、星图、建档的安全头与 nonce；页面脚本在 CSP 下照常运行（表单水合）', async ({ page }) => {
  await loginStudent(page, S1);
  await crawl(page, ['/student', `/s/${S1}/star-map`, `/onboarding/${S1}`,
                    // H4 壳页面：今日、科目、三个科目页
                    `/s/${S1}/today`, `/s/${S1}/learn`, `/s/${S1}/learn/math`, `/s/${S1}/learn/politics`, `/s/${S1}/learn/chinese`]);
  await page.goto('/student');
  await expect(page.getByTestId('exit-student-mode')).toHaveAttribute('data-hydrated', 'true');
});
