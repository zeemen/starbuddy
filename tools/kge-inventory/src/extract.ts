// 隔离子进程入口（安全审查：import legacy 会执行 legacy 代码）。
// 由 main.ts 用 `node --permission --allow-fs-read=<legacy 与本工具目录> --allow-worker` 启动，环境变量清空：
// 不能写文件、不能起子进程、读不到运行者的环境变量。提取结果以 JSON 写到 stdout，由父进程生成报告。
// 注意：Node 的权限模型不限制网络；legacy 数据模块里没有网络调用（审查已核对），系统层网络隔离需要时由运行者另加。
import { inventoryConclusions } from "./conclusions.ts";
import { registerLegacyHooks } from "./legacy.ts";
import { inventoryChinese } from "./subjects/chinese.ts";
import { inventoryMath } from "./subjects/math.ts";
import { inventoryMoral } from "./subjects/moral.ts";

const [aya, wenli] = process.argv.slice(2);
if (!aya || !wenli) {
  process.stderr.write("extract: 用法 node src/extract.ts <aya> <wenli>\n");
  process.exit(2);
}
registerLegacyHooks(aya, [aya, wenli]);
const math = await inventoryMath(aya);
const chinese = await inventoryChinese(wenli);
const moral = await inventoryMoral(wenli);
const result = {
  subjects: [math.report, chinese.report, moral.report],
  answers: [...math.answers, ...chinese.answers, ...moral.answers],
  blockers: [...math.blockers, ...chinese.blockers, ...moral.blockers],
  conclusions: inventoryConclusions(aya, wenli),
};
process.stdout.write(JSON.stringify(result));
