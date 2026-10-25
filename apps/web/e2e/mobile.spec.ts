// LT-07 移动端走查（specs/launch-lt07-mobile-pwa 范围第 1、3、5 条）：只在三个移动设备项目（playwright.config.ts 的 mobile-iphone13、
// mobile-pixel7、mobile-360；本机只有 Chromium，iPhone 13 用 Chromium 按其视口、UA、触屏模拟）里运行，全程真实会话、合成数据。
// 每页核对：
//   - 没有横向滚动（scrollWidth 不超过视口宽度），并指出超出视口的元素；
//   - 触控目标不小于 44×44（句中的行内链接除外，WCAG 2.5.8 的行内例外；复选框与单选按钮看它所在的整行标签）；
//   - 输入框、下拉框、多行输入字号不小于 16px（iOS 聚焦时不自动放大）；
//   - 固定定位的元素（点点入口）在页面顶端与滚到底时都不压住任何可点的元素（软键盘弹出时布局视口变小，同样不压住提交按钮）；
//   - axe（wcag2a / wcag2aa）在移动视口无违规。
// 页面清单沿用安全头爬取的登记表（e2e/crawl-pages.ts）：开关全关的一套（S1 / gS）、信任界面一套（GT / gH）、星图 v1 一套（V1 / gV）、
// 错题再练一套（P1）；只走 200 的页面。另核对：微信内置浏览器 UA 下照常登录（会话 cookie 属性不变），家长首页的“添加到主屏幕”
// 引导只在微信里另提示“在浏览器打开”；登录页验证码框是 one-time-code。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { INSTALL } from '../src/pwa/copy.ts';
import { targets, type CrawlTarget } from './crawl-pages.ts';
import { codeFor, loginGuardian, loginStudent, PHONE, PRACTICE_BASE, STARMAP_BASE, TRUST_BASE } from './session.ts';

const S1 = '5a000000-0000-4000-8000-0000000000c1';
const GT = '5d000000-0000-4000-8000-0000000000c1';
const V1 = '5e000000-0000-4000-8000-0000000000c1';
const P1 = '59000000-0000-4000-8000-0000000000c1';

const WECHAT_UA = {
  ios: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.47(0x18002f2c) NetType/WIFI Language/zh_CN',
  android: 'Mozilla/5.0 (Linux; Android 14; SYN-AL00) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36 XWEB/1160065 MMWEBSDK/20231202 MicroMessenger/8.0.47.2560(0x28002F30) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64',
};

const axe = async (page: Page, label: string) => {
  await expect(page).toHaveTitle(/\S/);
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`), label).toEqual([]);
};

/** 当前视口下的移动端布局问题（空数组 = 没有） */
async function layoutIssues(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const vw = document.documentElement.clientWidth;
    const desc = (el: Element) => {
      const t = el.getAttribute('data-testid');
      const c = typeof el.className === 'string' && el.className ? `.${el.className.trim().split(/\s+/).join('.')}` : '';
      return `<${el.tagName.toLowerCase()}${t ? ` [${t}]` : ''}${c}>「${(el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 24)}」`;
    };
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !el.closest('.visually-hidden');
    };
    const scrolls = (el: Element | null): boolean => {
      for (let p = el?.parentElement ?? null; p && p !== document.body; p = p.parentElement) {
        if (['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(p).overflowX)) return true;
      }
      return false;
    };
    const sw = document.documentElement.scrollWidth;
    if (sw > vw + 1) {
      out.push(`横向滚动：页面宽 ${sw}px，视口 ${vw}px`);
      const wide = [...document.body.querySelectorAll('*')].filter((el) => visible(el) && el.getBoundingClientRect().right > vw + 1 && !scrolls(el));
      // 只报最外层的超宽元素
      for (const el of wide.filter((el) => !wide.includes(el.parentElement as Element)).slice(0, 5)) {
        out.push(`  超出视口 ${Math.round(el.getBoundingClientRect().right - vw)}px：${desc(el)}`);
      }
    }
    const targets = [...document.querySelectorAll('a[href], button, input:not([type="hidden"]), select, textarea, summary')];
    for (const el of targets) {
      if (!visible(el)) continue;
      let box = el.getBoundingClientRect();
      if (el instanceof HTMLInputElement && (el.type === 'checkbox' || el.type === 'radio')) {
        const row = el.closest('label') ?? el.parentElement;
        if (row) box = row.getBoundingClientRect();
      }
      const inline = getComputedStyle(el).display === 'inline'
        && [...(el.parentElement?.childNodes ?? [])].some((n) => n !== el && n.nodeType === Node.TEXT_NODE && (n.textContent ?? '').trim() !== '');
      if (inline) continue;
      if (box.height < 43.5 || box.width < 43.5) out.push(`触控目标 ${Math.round(box.width)}×${Math.round(box.height)}：${desc(el)}`);
    }
    for (const el of document.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), select, textarea')) {
      if (!visible(el)) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 16) out.push(`输入框字号 ${fs}px：${desc(el)}`);
    }
    const fixed = [...document.body.querySelectorAll('*')].filter((el) => getComputedStyle(el).position === 'fixed' && visible(el));
    const overlaps = () => {
      for (const f of fixed) {
        const fr = f.getBoundingClientRect();
        for (const el of targets) {
          if (f.contains(el) || !visible(el)) continue;
          const r = el.getBoundingClientRect();
          if (r.bottom <= 0 || r.top >= window.innerHeight) continue;
          const ix = Math.min(fr.right, r.right) - Math.max(fr.left, r.left);
          const iy = Math.min(fr.bottom, r.bottom) - Math.max(fr.top, r.top);
          if (ix > 1 && iy > 1) out.push(`固定元素 ${desc(f)} 压住 ${desc(el)}（滚动 ${Math.round(window.scrollY)}）`);
        }
      }
    };
    const y = window.scrollY;
    window.scrollTo(0, 0);
    overlaps();
    window.scrollTo(0, document.documentElement.scrollHeight);
    overlaps();
    window.scrollTo(0, y);
    const meta = document.querySelector('meta[name="viewport"]')?.getAttribute('content') ?? '';
    if (!/width=device-width/.test(meta) || /user-scalable=no|maximum-scale=1(\D|$)/.test(meta)) out.push(`viewport 元信息不对：${meta}`);
    return out;
  });
}

async function walk(page: Page, list: CrawlTarget[]): Promise<void> {
  const problems: string[] = [];
  for (const t of list.filter((x) => x.status === 200)) {
    const res = await page.goto(t.path);
    expect(res?.status(), t.path).toBe(200);
    await page.waitForLoadState('networkidle');
    for (const p of await layoutIssues(page)) problems.push(`${t.path}：${p}`);
    await axe(page, t.path);
  }
  expect(problems).toEqual([]);
}

test('未登录：登录页两步（手机号、验证码）与审核台登录页；验证码框是 one-time-code、数字键盘', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByTestId('login')).toHaveAttribute('data-hydrated', 'true');
  expect(await layoutIssues(page)).toEqual([]);
  await axe(page, '/login');
  const phone = page.getByLabel('手机号');
  await expect(phone).toHaveAttribute('type', 'tel');
  await expect(phone).toHaveAttribute('autocomplete', 'tel');
  const t = Date.now() - 1000;
  await phone.fill(PHONE.gS);
  await page.getByRole('button', { name: '获取验证码' }).click();
  await expect(page.getByTestId('login')).toHaveAttribute('data-step', 'code');
  const code = page.getByLabel('验证码');
  await expect(code).toHaveAttribute('autocomplete', 'one-time-code');
  await expect(code).toHaveAttribute('inputmode', 'numeric');
  await code.focus();
  expect(await layoutIssues(page)).toEqual([]);
  await axe(page, '/login 验证码');
  await code.fill(await codeFor(PHONE.gS, t));
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page).toHaveURL(/\/family$/);
  await page.context().clearCookies();
  await page.goto('/review/login');
  expect(await layoutIssues(page)).toEqual([]);
});

test('学生模式（开关全关的一套，S1）：壳内全部页面', async ({ page }) => {
  await loginStudent(page, S1);
  await walk(page, targets('student', S1, false));
});

test('家长（开关全关的一套，gS）：家长端全部页面', async ({ page }) => {
  await loginGuardian(page, 'gS');
  await walk(page, targets('guardian', S1, false));
});

test.describe('信任界面一套（GT / gH）', () => {
  test.use({ baseURL: TRUST_BASE });
  test('学生：信任界面页面', async ({ page }) => {
    await loginStudent(page, GT);
    await walk(page, targets('student', GT, true, true));
  });
  test('家长：孩子页与信任界面页面（周报、对账卡、数据权利）', async ({ page }) => {
    await loginGuardian(page, 'gH');
    await walk(page, [{ path: `/family/${GT}`, status: 200, final: `/family/${GT}` }, ...targets('guardian', GT, true, true)]);
  });
});

test.describe('星图 v1 一套（V1 / gV）', () => {
  test.use({ baseURL: STARMAP_BASE });
  test('学生与家长的星图 v1：小屏折行、可缩放，不横向滚动', async ({ page }) => {
    await loginStudent(page, V1);
    await walk(page, [{ path: `/s/${V1}/star-map`, status: 200, final: `/s/${V1}/star-map` }]);
    await loginGuardian(page, 'gV');
    await walk(page, [{ path: `/family/${V1}/star-map`, status: 200, final: `/family/${V1}/star-map` }]);
  });
});

test.describe('错题再练一套（P1）', () => {
  test.use({ baseURL: PRACTICE_BASE });
  test('学生：今日（跨科编排）与再练列表', async ({ page }) => {
    await loginStudent(page, P1);
    await walk(page, [`/s/${P1}/today`, `/s/${P1}/learn/math/practice`].map((path) => ({ path, status: 200, final: path })));
  });
});

for (const [os, ua] of Object.entries(WECHAT_UA)) {
  test.describe(`微信内置浏览器（${os}）`, () => {
    test.use({ userAgent: ua });
    test(`照常登录与打开学生模式；会话 cookie 属性不变；“添加到主屏幕”另提示在浏览器打开（${os}）`, async ({ page }) => {
      await loginGuardian(page, 'gS');
      const c = (await page.context().cookies()).find((x) => x.name === 'sb_session');
      expect(c?.httpOnly).toBe(true);
      expect(c?.sameSite).toBe('Lax');
      const hint = page.getByTestId('install-hint');
      await hint.locator('summary').click();
      await expect(hint.getByTestId(`install-${os}`)).toHaveText(INSTALL[os as 'ios' | 'android']);
      await expect(hint.getByTestId('install-wechat')).toHaveText(INSTALL.wechat);
      expect(await layoutIssues(page)).toEqual([]);
      await axe(page, '/family 微信');
      await loginStudent(page, S1);
      await expect(page.getByTestId('student-mode')).toBeVisible();
    });
  });
}

test('浏览器里（非微信）：“添加到主屏幕”只给本平台的说明，不提示在浏览器打开', async ({ page }) => {
  await loginGuardian(page, 'gS');
  const hint = page.getByTestId('install-hint');
  await hint.locator('summary').click();
  await expect(hint.getByTestId('install-wechat')).toHaveCount(0);
  await expect(hint.locator('p')).toHaveCount(1);
  await expect(hint.locator('p')).toHaveText(/添加到主屏幕|安装应用/);
});
