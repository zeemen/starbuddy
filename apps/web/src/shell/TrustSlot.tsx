// 信任界面的预留位（spec 第 12 节）：组件与位置做出来，由默认关闭的功能开关控制（src/shell/features.ts）。
// 开关关闭：返回 null——不渲染任何元素、不发任何请求。开关打开（只在 dev / test）：只画一个不可操作的占位，
// 仍然不发请求（冲刺 5 实现时再接 BFF 路由）。入口文字是 {{TODO_COPY}} 占位（人写人审）。
import { TRUST_SLOT } from './copy.ts';
import { trustEnabled, type TrustFeature } from './features.ts';

export function TrustSlot({ feature, claimId }: { feature: TrustFeature; claimId?: string }) {
  if (!trustEnabled(feature)) return null;
  return (
    <span className="trust-slot muted" data-trust-slot={feature} {...(claimId ? { 'data-for-claim': claimId } : {})} aria-disabled="true">
      {TRUST_SLOT[feature]}
    </span>
  );
}
