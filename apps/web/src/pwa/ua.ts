// 浏览器识别（LT-07 决定记录第 2 条）：只用于“添加到主屏幕”引导选哪段说明——iOS 提示 Safari 的“分享 → 添加到主屏幕”，安卓提示浏览器菜单；
// 微信内置浏览器（含企业微信）另提示“在浏览器打开”。不记录、不上报、不据此改变任何功能与数据（微信内照常登录使用）。
export type InstallOs = 'ios' | 'android' | 'other';

export const isWeChat = (ua: string | null | undefined): boolean => /\bMicroMessenger\//i.test(ua ?? '');

export function installOs(ua: string | null | undefined): InstallOs {
  const s = ua ?? '';
  if (/\b(iPhone|iPad|iPod)\b/.test(s)) return 'ios';
  if (/\bAndroid\b/.test(s)) return 'android';
  return 'other';
}
