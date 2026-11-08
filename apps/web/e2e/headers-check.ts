// 安全头与 CSP 的逐页核对（spec 第 16 节，H2；第二轮 7.6 的完整爬取，H6）：e2e/headers.spec.ts 与 e2e/trust-on.spec.ts 共用。
// 每个响应都有带本次 nonce 的 CSP（不含 unsafe-inline、unsafe-eval，frame-ancestors 'none'）、no-store、nosniff、no-referrer、
// X-Frame-Options: DENY；测试环境不发 HSTS；HTML 里每个 <script> / <style> 都带本次 nonce，没有内联 style 属性；浏览器没有 CSP 违规。
import { expect, type Page } from '@playwright/test';
import type { CrawlTarget } from './crawl-pages.ts';

export function checkHeaders(h: Record<string, string>, path: string): string {
  const csp = h['content-security-policy'] ?? '';
  const m = /script-src 'self' 'nonce-([A-Za-z0-9+/=]+)' 'strict-dynamic'/.exec(csp);
  expect(m, `${path} CSP 带 nonce`).not.toBeNull();
  expect(csp, path).toContain(`style-src 'self' 'nonce-${m![1]}'`);
  expect(csp, path).toContain("frame-ancestors 'none'");
  expect(csp, path).toContain("object-src 'none'");
  expect(csp, path).toContain("base-uri 'none'");
  expect(csp, path).toContain("worker-src 'self'");  // Service Worker 只许同源（LT-07）
  expect(csp, `${path} 图片只许同源，不许 data:（T46）`).toMatch(/(?:^|; )img-src 'self'(?:;|$)/);
  expect(csp, path).not.toMatch(/unsafe-inline|unsafe-eval/);
  expect(h['cache-control'] ?? '', path).toContain('no-store');
  expect(h['x-content-type-options'], path).toBe('nosniff');
  expect(h['referrer-policy'], path).toBe('no-referrer');
  expect(h['x-frame-options'], path).toBe('DENY');
  expect(h['permissions-policy'] ?? '', path).toContain('camera=()');
  expect(h['strict-transport-security'], `${path} 测试环境不发 HSTS`).toBeUndefined();
  expect(h['x-powered-by'], `${path} 不暴露框架`).toBeUndefined();
  return m![1]!;
}

export function checkHtml(html: string, nonce: string, path: string) {
  const scripts = [...html.matchAll(/<script\b[^>]*>/g)].map((x) => x[0]);
  expect(scripts.length, `${path} 有脚本`).toBeGreaterThan(0);
  for (const s of scripts) expect(s, path).toContain(`nonce="${nonce}"`);
  for (const s of [...html.matchAll(/<style\b[^>]*>/g)].map((x) => x[0])) expect(s, path).toContain(`nonce="${nonce}"`);
  expect(html, `${path} 没有内联 style 属性`).not.toMatch(/<[a-z][^>]*\sstyle="/i);
  // 资源同源：src、href、action、srcset、poster、data 不指向别的来源（含协议相对地址 //host），样式里不 url(http…)
  expect(html, `${path} 资源同源`).not.toMatch(/\s(?:src|href|action|srcset|poster|data|formaction)="\s*(?:[a-z][a-z0-9+.-]*:)?\/\//i);
  expect(html, `${path} 样式不引外部资源`).not.toMatch(/url\(\s*['"]?\s*(?:https?:)?\/\//i);
  expect(html, `${path} 没有 data: 资源（T46）`).not.toMatch(/(?:\s(?:src|srcset|href|poster|data)="|url\(\s*['"]?)\s*data:/i);
}

/** 逐页打开并核对：状态码与重定向后的最终路径与登记一致；每个请求一个新的 nonce；没有 CSP 违规。结束后移除控制台监听 */
export async function crawl(page: Page, list: CrawlTarget[]): Promise<void> {
  const violations: string[] = [];
  const onConsole = (m: { text(): string }) => { if (/Content Security Policy|Refused to/i.test(m.text())) violations.push(m.text()); };
  page.on('console', onConsole);
  try {
    const nonces = new Set<string>();
    for (const t of list) {
      const res = await page.goto(t.path);
      expect(res, t.path).not.toBeNull();
      expect(res!.status(), `${t.path} 状态码`).toBe(t.status);
      expect(new URL(page.url()).pathname, `${t.path} 最终地址`).toBe(t.final);
      const nonce = checkHeaders(res!.headers(), t.path);
      checkHtml(await res!.text(), nonce, t.path);
      nonces.add(nonce);
      await page.waitForLoadState('networkidle');
    }
    expect(nonces.size, '每个请求一个新的 nonce').toBe(list.length);
    expect(violations).toEqual([]);
  } finally {
    page.off('console', onConsole);
  }
}
