// KGE-02 题库导入的隔离子进程入口（与 tools/kge-inventory 的 extract.ts、import/extract-topic.ts 同一种隔离）：
// 只读权限、不能写文件与起子进程、环境变量清空；导入 legacy 前拿掉全局网络接口、封掉 process.getBuiltinModule。
// 只 import 阿鸦的 content/topics.ts（汇总各章 questions*.ts）与 content/sprint/index.ts，把题目的结构字段与打标用文本以 JSON 写到 stdout。
// 打标用文本（题干、选项、单元名）只在父进程内存里用于向量层与模型层，不写进任何产物（迁移 0011：题干不进库；spec 待人确认 #9）。
// legacy 里没有学生数据（KGE-00 已核对）。
import { pathToFileURL } from "node:url";
import { registerLegacyHooks } from "../../kge-inventory/src/legacy.ts";

const [aya] = process.argv.slice(2);
if (!aya) {
  process.stderr.write("extract-items: 用法 node src/extract-items.ts <aya>\n");
  process.exit(2);
}
registerLegacyHooks(aya, [aya]);
for (const k of ["fetch", "WebSocket", "EventSource", "XMLHttpRequest"]) Reflect.deleteProperty(globalThis, k);
Object.defineProperty(process, "getBuiltinModule", {
  value: () => { throw new Error("getBuiltinModule is disabled while loading legacy"); }, configurable: false, writable: false,
});

type Rec = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" ? v : "");
const strs = (v: unknown) => (Array.isArray(v) ? v.map(str) : []);
const topicsMod = (await import(pathToFileURL(`${aya}/content/topics.ts`).href)) as Rec;
const sprintMod = (await import(pathToFileURL(`${aya}/content/sprint/index.ts`).href)) as Rec;
const topics = Array.isArray(topicsMod.TOPICS) ? (topicsMod.TOPICS as Rec[]) : [];
const categories = Array.isArray(sprintMod.SPRINT_CATEGORIES) ? (sprintMod.SPRINT_CATEGORIES as Rec[]) : [];

function question(q: Rec, topic: string | null) {
  const options = Array.isArray(q.options) ? (q.options as Rec[]).map((o) => str(o?.text)) : [];
  return {
    id: str(q.id), topic, unit: str(q.unit), unitName: str(q.unitName),
    text: [str(q.unitName), str(q.stem), ...options].filter((x) => x !== "").join("\n"),
    mainNodes: strs(q.mainNodes), auxNodes: strs(q.auxNodes), preNodes: strs(q.preNodes),
  };
}

process.stdout.write(JSON.stringify({
  topics: topics.map((t) => ({
    id: str(t.id), name: str(t.name), chapterLabel: str(t.chapterLabel),
    nodes: (Array.isArray(t.nodes) ? (t.nodes as Rec[]) : []).map((n) => ({ id: str(n.id), name: str(n.name), kind: str(n.kind), cluster: str(n.cluster) })),
  })),
  questions: [
    ...topics.flatMap((t) => (Array.isArray(t.questions) ? (t.questions as Rec[]) : []).map((q) => question(q, str(t.id)))),
    ...categories.flatMap((c) => (Array.isArray(c.questions) ? (c.questions as Rec[]) : []).map((q) => question(q, null))),
  ],
  sprintCategories: categories.map((c) => ({
    id: str(c.id), name: str(c.name),
    questionIds: (Array.isArray(c.questions) ? (c.questions as Rec[]) : []).map((q) => str(q.id)),
  })),
}));
