// 启动入口：node src/main.ts
//   AYA_ADAPTER_SERVICE_TOKEN  平台到适配器的服务间令牌（必填，≥ 32 字符；由部署环境注入，不写进仓库）
//   PORT                       监听端口（默认 8787）。只监听 127.0.0.1，由平台侧网关转发
//   AYA_CONTRACT_VERSION       能力声明报的默认契约版本（缺省 0.9.0，待人确认 #2）。请求一律按各自的
//                              context.contract_version 应答（0.9.0 / 1.0.0），与此无关。
//                              设为候选版 1.0.0 / 1.1.0 还需 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1（签字前只在测试与演练里用）
//   AYA_CONTENT_PACK           换一份内容包（文件路径；只给测试与演练：带合成题面的内容包，PLT-12）。不设用 content/pack.json
//   AYA_STEM_OVERLAY           题面叠加文件（文件路径；只给测试与演练：给真实内容包里的几道题加合成题面）
//                              这两项只在候选版开关打开时才读；开关没开却设了，拒绝启动（审查阻断 3）。
//                              用了它们时，内容版本号接上文件 sha256 前 8 位（items 段与题面 item_version）
// 命令行参数（优先于环境变量；一致性测试的 cli.py 不向适配器进程透传环境变量，用参数选版本）：
//   --contract-version <v>        同 AYA_CONTRACT_VERSION
//   --allow-candidate-contract    同 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1
//   --content-pack <path>         同 AYA_CONTENT_PACK
//   --stem-overlay <path>         同 AYA_STEM_OVERLAY
import { parseArgs } from "node:util";
import { createAdapter } from "./adapter.ts";
import { loadPackFrom, PACK_PATH_ENV, STEM_OVERLAY_ENV, type LoadedPack } from "./content.ts";
import { CANDIDATE_CONTRACT_VERSIONS, DEFAULT_CONTRACT_VERSION, isSupportedVersion, loadContract } from "./contract.ts";
import { createAdapterServer } from "./server.ts";
import { loadTaxonomy } from "./taxonomy.ts";

const credential = process.env.AYA_ADAPTER_SERVICE_TOKEN;
if (!credential) {
  console.error("aya-adapter: 缺少 AYA_ADAPTER_SERVICE_TOKEN，拒绝启动");
  process.exit(1);
}
const { values: args } = parseArgs({
  options: {
    "contract-version": { type: "string" },
    "allow-candidate-contract": { type: "boolean", default: false },
    "content-pack": { type: "string" },
    "stem-overlay": { type: "string" },
  },
});
const defaultVersion = args["contract-version"] || process.env.AYA_CONTRACT_VERSION || DEFAULT_CONTRACT_VERSION;
if (!isSupportedVersion(defaultVersion)) {
  console.error(`aya-adapter: 不支持的契约版本 ${defaultVersion.slice(0, 16)}，拒绝启动`);
  process.exit(1);
}
const candidateAllowed = args["allow-candidate-contract"] || process.env.STARBUDDY_ALLOW_CANDIDATE_CONTRACT === "1";
if (CANDIDATE_CONTRACT_VERSIONS.includes(defaultVersion) && !candidateAllowed) {
  console.error(`aya-adapter: ${defaultVersion} 是候选版，作默认版本需 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1，拒绝启动`);
  process.exit(1);
}
let content: LoadedPack;
try {
  content = loadPackFrom({
    packPath: args["content-pack"] || process.env[PACK_PATH_ENV] || undefined,
    overlayPath: args["stem-overlay"] || process.env[STEM_OVERLAY_ENV] || undefined,
    candidateAllowed,
  });
} catch (e) {
  console.error(`aya-adapter: ${(e as Error).message.split("\n")[0]!.slice(0, 300)}，拒绝启动`);
  process.exit(1);
}
const adapter = createAdapter({
  content,
  taxonomy: loadTaxonomy(),
  contract: loadContract({ defaultVersion }),
  allowCandidate: candidateAllowed,
  // 日志只含方法、状态码、错误码、request_id、耗时（adapter.ts LogRecord）
  log: (r) => console.log(JSON.stringify(r)),
});
const port = Number(process.env.PORT ?? 8787);
createAdapterServer(adapter, credential).listen(port, "127.0.0.1", () => {
  console.log(JSON.stringify({ event: "listening", port }));
});
