// 真实模型路由的连接参数（向量层 T7、模型层 T8 共用）。本任务只把接口形状定下来：
// 桩类（RoutedEmbedder、RoutedTagModel）构造时核对路由地址与令牌，调用时一律抛错，不回落到假实现。
// 令牌只存在私有字段里，不进 toString / JSON / 报错信息；本工具不读 .env，由调用方显式传入。
import { TaggingError } from "./types.ts";

export interface RouteConfig {
  /** 学习服务模型路由的地址（http / https） */
  routeUrl: string;
  /** 调用路由的令牌（不落盘、不打印） */
  token: string;
}

/** 核对路由参数并返回规范化的地址；令牌只核对非空，不回显 */
export function checkRouteConfig(cfg: RouteConfig, who: string): string {
  if (typeof cfg?.routeUrl !== "string" || cfg.routeUrl.trim() === "") throw new TaggingError(`${who}：需要模型路由地址`);
  let url: URL;
  try {
    url = new URL(cfg.routeUrl);
  } catch {
    throw new TaggingError(`${who}：模型路由地址不是合法 URL`);
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new TaggingError(`${who}：模型路由地址只能是 http / https`);
  if (url.username || url.password) throw new TaggingError(`${who}：模型路由地址里不能带账号或口令，令牌单独传`);
  if (typeof cfg.token !== "string" || cfg.token.trim() === "") throw new TaggingError(`${who}：需要模型路由令牌`);
  return url.href;
}
