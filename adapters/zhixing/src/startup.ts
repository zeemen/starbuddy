// 启动前的安全检查（审查意见）：不通过就拒绝启动。纯函数，main.ts 调用；错误信息只写原因，不含地址与令牌。
//
// 1. 路由客户端不走代理：Node 的全局 fetch 在 NODE_USE_ENV_PROXY（任何非空值）或 --use-env-proxy 打开时会按
//    HTTP(S)_PROXY 转发请求，服务间令牌与学生作答会经过代理。检测到就拒绝启动（命令行参数与 NODE_OPTIONS 都查）。
// 2. 两个服务间令牌必须不同：ZHIXING_ROUTE_TOKEN（适配器 → 学习服务）与 ZHIXING_ADAPTER_SERVICE_TOKEN（平台 → 适配器）
//    相同时，任何一方泄露都能冒充另一方向，拒绝启动。

export const USE_ENV_PROXY_FLAG = "--use-env-proxy";

const hasProxyFlag = (args: readonly string[]) => args.some((a) => a === USE_ENV_PROXY_FLAG || a.startsWith(`${USE_ENV_PROXY_FLAG}=`));

/** 本进程的 fetch 是否会按环境变量走代理 */
export function envProxyEnabled(env: Record<string, string | undefined>, execArgv: readonly string[]): boolean {
  if ((env.NODE_USE_ENV_PROXY ?? "") !== "") return true;
  if (hasProxyFlag(execArgv)) return true;
  return hasProxyFlag((env.NODE_OPTIONS ?? "").split(/\s+/).filter(Boolean));
}

/** 返回拒绝启动的原因（中文，不含任何配置值）；null 表示可以启动 */
export function startupRefusal(env: Record<string, string | undefined>, execArgv: readonly string[]): string | null {
  if (envProxyEnabled(env, execArgv)) {
    return "检测到 NODE_USE_ENV_PROXY 或 --use-env-proxy：模型路由调用不得经过代理";
  }
  const credential = env.ZHIXING_ADAPTER_SERVICE_TOKEN;
  if (!credential) return "缺少 ZHIXING_ADAPTER_SERVICE_TOKEN";
  if (env.ZHIXING_ROUTE_TOKEN && env.ZHIXING_ROUTE_TOKEN === credential) {
    return "ZHIXING_ROUTE_TOKEN 与 ZHIXING_ADAPTER_SERVICE_TOKEN 相同：两个方向必须用不同的令牌";
  }
  return null;
}
