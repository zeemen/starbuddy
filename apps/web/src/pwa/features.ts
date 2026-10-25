// PWA 停用开关（LT-07 安全审查 1）：STARBUDDY_PWA_KILL_SWITCH=1 时 /sw.js 返回“自毁”版 Service Worker（激活时删掉本站全部缓存、
// 注销自己、让已打开的页面重新导航），页面也不再注册，只请已有的注册去取一次更新（取到的就是自毁版）。缺省（不设或别的取值）关。
// 只认 "1"；只在服务端读（src/app/sw.js/route.ts、src/app/layout.tsx），登记在 deploy/config/env-inventory.yaml 的 web 名下。
type Env = Record<string, string | undefined>;

export const pwaKillSwitch = (env: Env = process.env): boolean => env['STARBUDDY_PWA_KILL_SWITCH'] === '1';
