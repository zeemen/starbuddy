// 启动入口：node src/main.ts
//   AYA_ADAPTER_SERVICE_TOKEN  平台到适配器的服务间令牌（必填，≥ 32 字符；由部署环境注入，不写进仓库）
//   PORT                       监听端口（默认 8787）。只监听 127.0.0.1，由平台侧网关转发
import { createAdapter } from "./adapter.ts";
import { loadPack } from "./content.ts";
import { loadContract } from "./contract.ts";
import { createAdapterServer } from "./server.ts";
import { loadTaxonomy } from "./taxonomy.ts";

const credential = process.env.AYA_ADAPTER_SERVICE_TOKEN;
if (!credential) {
  console.error("aya-adapter: 缺少 AYA_ADAPTER_SERVICE_TOKEN，拒绝启动");
  process.exit(1);
}
const adapter = createAdapter({
  content: loadPack(),
  taxonomy: loadTaxonomy(),
  contract: loadContract(),
  // 日志只含方法、状态码、错误码、request_id、耗时（adapter.ts LogRecord）
  log: (r) => console.log(JSON.stringify(r)),
});
const port = Number(process.env.PORT ?? 8787);
createAdapterServer(adapter, credential).listen(port, "127.0.0.1", () => {
  console.log(JSON.stringify({ event: "listening", port }));
});
