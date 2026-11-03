// PWA 与移动端的界面文案（LT-07）：“添加到主屏幕”引导、微信内“在浏览器打开”提示、离线页。系统口吻，不是任何角色的话术，
// 不对学生作任何陈述（规则 11、12）。人写人审之前一律带 {{TODO_COPY:pwa.*}} 占位，后附中性提示便于走查（与 src/internal-test/copy.ts 同一做法）。
// 离线页是静态文件 public/offline.html（Service Worker 预缓存，不经 React 渲染），文字与这里的 OFFLINE 逐字一致（tests/pwa.test.ts 核对）。
// 应用名（manifest 的 name / short_name，主屏幕图标下显示）用产品名“魁星点斗”，待人确认。

export const APP_NAME = '魁星点斗';
export const APP_SHORT_NAME = '魁星点斗';

export const INSTALL = {
  entry: '{{TODO_COPY:pwa.install_entry}} 添加到主屏幕',
  ios: '{{TODO_COPY:pwa.install_ios}} 在 Safari 中点底部的“分享”按钮，再选“添加到主屏幕”。',
  android: '{{TODO_COPY:pwa.install_android}} 在浏览器菜单中选“添加到主屏幕”或“安装应用”。',
  other: '{{TODO_COPY:pwa.install_other}} 用手机浏览器打开本页，在菜单中选“添加到主屏幕”。',
  wechat: '{{TODO_COPY:pwa.install_wechat}} 微信内不能添加到主屏幕。请点右上角“···”，选“在浏览器打开”，再按上面的方法添加。',
} as const;

export const OFFLINE = {
  title: '{{TODO_COPY:pwa.offline_title}} 现在没有联网',
  body: '{{TODO_COPY:pwa.offline_body}} 连上网络后重新打开就可以继续。离线时不显示任何学习记录。',
  retry: '{{TODO_COPY:pwa.offline_retry}} 重新打开',
} as const;

/** 全部 PWA 文案（单测查禁语用） */
export const ALL_PWA_COPY: string[] = [APP_NAME, APP_SHORT_NAME, ...Object.values(INSTALL), ...Object.values(OFFLINE)];
