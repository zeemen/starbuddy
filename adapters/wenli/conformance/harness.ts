// 一致性测试专用启动器（只用于测试与演练，不用于部署；来源：adapters/zhixing/conformance/harness.ts）。
//
// packages/contracts/conformance/cli.py 只向被测进程传服务间令牌与端口（环境变量白名单），不传模型路由配置。
// 本启动器先在本机回环上起一个“学习服务内部模型路由接口”的桩（tests/route-stub.ts 的档位桩判定器，随机令牌），
// 把地址与令牌放进本进程环境，再加载正式入口 src/main.ts。适配器代码与正式启动完全相同，只是路由指向桩。
//   python packages/contracts/conformance/cli.py --adapter-cmd "node --no-warnings conformance/harness.ts" \
//       --adapter-cwd adapters/wenli --token-var WENLI_ADAPTER_SERVICE_TOKEN --fixtures adapters/wenli/conformance/fixtures.yaml
// 命令行参数原样交给 src/main.ts（例如 --contract-version 1.0.0 --allow-candidate-contract）。
import { levelJudge, startRouteStub } from "../tests/route-stub.ts";

const stub = await startRouteStub(levelJudge());
process.env.WENLI_ROUTE_URL = stub.url;
process.env.WENLI_ROUTE_TOKEN = stub.token;
await import("../src/main.ts");
