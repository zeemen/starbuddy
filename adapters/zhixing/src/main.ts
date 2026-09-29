// 启动入口：node src/main.ts
//   ZHIXING_ADAPTER_SERVICE_TOKEN  平台到适配器的服务间令牌（必填，≥ 32 字符；由部署环境注入，不写进仓库）
//   PORT                           监听端口（默认 8788）。只监听 127.0.0.1，由平台侧网关转发
//   ZHIXING_ROUTE_URL              学习服务内部模型路由接口的地址（https，或本机回环的 http）
//   ZHIXING_ROUTE_TOKEN            调用该接口的服务间令牌（≥ 32 字符）
//                                  两者缺一或不合格：照常启动，但 grade 返回 503 UPSTREAM_UNAVAILABLE，其他方法照常
//                                  与 ZHIXING_ADAPTER_SERVICE_TOKEN 相同：拒绝启动（src/startup.ts）
//   ZHIXING_CONTRACT_VERSION       能力声明报的默认契约版本（缺省 0.9.0）。请求一律按各自的 context.contract_version 应答。
//                                  设为候选版 1.0.0 还需 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1（签字前只在测试与演练里用）
// 命令行参数（优先于环境变量；一致性测试的 cli.py 不向适配器进程透传环境变量，用参数选版本）：
//   --contract-version <v>        同 ZHIXING_CONTRACT_VERSION
//   --allow-candidate-contract    同 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1
// 适配器不读取、也不需要任何模型供应商的地址或 Key（ADR 04 第 1 条）。
// 设置了 NODE_USE_ENV_PROXY（任何非空值），或命令行 / NODE_OPTIONS 里有 --use-env-proxy：拒绝启动（路由调用不得走代理）。
import { parseArgs } from "node:util";
import { createAdapter } from "./adapter.ts";
import { loadPack } from "./content.ts";
import { CANDIDATE_CONTRACT_VERSIONS, DEFAULT_CONTRACT_VERSION, isSupportedVersion, loadContract } from "./contract.ts";
import { parseRouteConfig } from "./route-client.ts";
import { createAdapterServer } from "./server.ts";
import { startupRefusal } from "./startup.ts";
import { loadTaxonomy } from "./taxonomy.ts";

const refusal = startupRefusal(process.env, process.execArgv);
if (refusal) {
  // 只写原因，不写地址、令牌与代理配置
  console.error(`zhixing-adapter: ${refusal}，拒绝启动`);
  process.exit(1);
}
const credential = process.env.ZHIXING_ADAPTER_SERVICE_TOKEN!;
const { values: args } = parseArgs({
  options: {
    "contract-version": { type: "string" },
    "allow-candidate-contract": { type: "boolean", default: false },
  },
});
const defaultVersion = args["contract-version"] || process.env.ZHIXING_CONTRACT_VERSION || DEFAULT_CONTRACT_VERSION;
if (!isSupportedVersion(defaultVersion)) {
  console.error(`zhixing-adapter: 不支持的契约版本 ${defaultVersion.slice(0, 16)}，拒绝启动`);
  process.exit(1);
}
const candidateAllowed = args["allow-candidate-contract"] || process.env.STARBUDDY_ALLOW_CANDIDATE_CONTRACT === "1";
if (CANDIDATE_CONTRACT_VERSIONS.includes(defaultVersion) && !candidateAllowed) {
  console.error(`zhixing-adapter: ${defaultVersion} 是候选版，作默认版本需 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1，拒绝启动`);
  process.exit(1);
}
const route = parseRouteConfig({ url: process.env.ZHIXING_ROUTE_URL, token: process.env.ZHIXING_ROUTE_TOKEN });
if (!route.ok) {
  // 只记原因编码，不记地址与令牌
  console.log(JSON.stringify({ event: "model_route_unavailable", reason: route.reason }));
}
const adapter = createAdapter({
  content: loadPack(),
  taxonomy: loadTaxonomy(),
  contract: loadContract({ defaultVersion }),
  route: route.ok ? route.config : null,
  allowCandidate: candidateAllowed,
  // 日志只含方法、状态码、错误码、request_id、耗时、路由失败类别（adapter.ts LogRecord）
  log: (r) => console.log(JSON.stringify(r)),
});
const port = Number(process.env.PORT ?? 8788);
createAdapterServer(adapter, credential).listen(port, "127.0.0.1", () => {
  console.log(JSON.stringify({ event: "listening", port }));
});
