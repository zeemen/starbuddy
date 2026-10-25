// “添加到主屏幕”引导（LT-07）：家长首页底部的一个可展开说明，服务端按 User-Agent 选说明（src/pwa/ua.ts）。
// 微信内置浏览器另显示“在浏览器打开”的提示（决定记录第 2 条：微信内照常登录使用，只在添加到主屏幕处提示）。
// 不调用浏览器的安装接口、不弹窗、不记录；文案只来自 src/pwa/copy.ts。
import { headers } from 'next/headers';
import { INSTALL } from './copy.ts';
import { installOs, isWeChat } from './ua.ts';

export async function InstallHint() {
  const ua = (await headers()).get('user-agent');
  const os = installOs(ua);
  return (
    <details className="install-hint" data-testid="install-hint">
      <summary>{INSTALL.entry}</summary>
      <p data-testid={`install-${os}`}>{INSTALL[os]}</p>
      {isWeChat(ua) ? <p data-testid="install-wechat">{INSTALL.wechat}</p> : null}
    </details>
  );
}
