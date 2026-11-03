// KGE-01 导入的隔离子进程入口（与 ../extract.ts 相同的隔离方式：只读、不能写文件与起子进程、环境变量清空）。
// 只 import 阿鸦一个专题的节点文件，把节点的结构字段以 JSON 写到 stdout；不输出题目与任何学生数据（legacy 里本来就没有）。
import { pathToFileURL } from "node:url";
import { registerLegacyHooks } from "../legacy.ts";

const [aya, topic] = process.argv.slice(2);
if (!aya || !topic || !/^[a-z]{2,8}$/.test(topic)) {
  process.stderr.write("extract-topic: 用法 node src/import/extract-topic.ts <aya> <专题代码>\n");
  process.exit(2);
}
registerLegacyHooks(aya, [aya]);
// 纵深防御（安全审查建议 1）：Node 的权限模型不管网络，legacy 代码还能用 process.getBuiltinModule 绕过解析钩子拿到 node:net。
// 导入 legacy 之前拿掉全局网络接口、封掉 getBuiltinModule。这不是边界：真正禁网要在无网络的环境（如 unshare -n）里运行。
for (const k of ["fetch", "WebSocket", "EventSource", "XMLHttpRequest"]) Reflect.deleteProperty(globalThis, k);
Object.defineProperty(process, "getBuiltinModule", {
  value: () => { throw new Error("getBuiltinModule is disabled while loading legacy"); }, configurable: false, writable: false,
});
const mod = (await import(pathToFileURL(`${aya}/content/chapters/${topic}/nodes.ts`).href)) as Record<string, unknown>;
const lists = Object.values(mod).filter(
  (v): v is Record<string, unknown>[] =>
    Array.isArray(v) && v.length > 0 && v.every((x) => x !== null && typeof x === "object" && "id" in x && "cluster" in x),
);
if (lists.length !== 1) {
  process.stderr.write(`extract-topic: ${topic} 的节点文件应恰好导出一个节点数组，实际 ${lists.length} 个\n`);
  process.exit(1);
}
const str = (v: unknown) => (typeof v === "string" ? v : "");
process.stdout.write(JSON.stringify(lists[0]!.map((x) => ({
  id: str(x.id), name: str(x.name), kind: str(x.kind), cluster: str(x.cluster), clusterName: str(x.clusterName), core: str(x.core),
  prerequisites: Array.isArray(x.prerequisites) ? x.prerequisites.map(str) : [],
}))));
