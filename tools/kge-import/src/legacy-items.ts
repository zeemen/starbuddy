// 阿鸦题库的只读加载（父进程侧）：在隔离子进程里 import legacy（src/extract-items.ts），输出按不可信数据逐项核对。
import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

export interface LegacyNodeInfo { id: string; name: string; kind: string; cluster: string }
export interface LegacyTopic { id: string; name: string; chapterLabel: string; nodes: LegacyNodeInfo[] }
export interface LegacyQuestion {
  id: string;
  /** 所属章节（专题代码）；压轴专项题为 null */
  topic: string | null;
  unit: string;
  unitName: string;
  /** 打标用文本（单元名 + 题干 + 选项）：只在内存里用于向量层与模型层，不写进任何产物 */
  text: string;
  mainNodes: string[];
  auxNodes: string[];
  preNodes: string[];
}
export interface LegacySprintCategory { id: string; name: string; questionIds: string[] }
export interface LegacyBank { topics: LegacyTopic[]; questions: LegacyQuestion[]; sprintCategories: LegacySprintCategory[] }

export class LegacyDataError extends Error {}

const TOOL_DIR = fileURLToPath(new URL("..", import.meta.url));
const INVENTORY_SRC = fileURLToPath(new URL("../../kge-inventory/src/", import.meta.url));
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/; // 题目文本允许换行与制表
const ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/; // 与 platform.skill_local_id 同一规则

function text(v: unknown, where: string, max: number, allowEmpty = false): string {
  if (typeof v !== "string" || v.length > max || (!allowEmpty && v.length === 0) || CONTROL.test(v)) {
    throw new LegacyDataError(`${where} 必须是${allowEmpty ? "" : "非空、"}不超过 ${max} 字、无控制字符的文本`);
  }
  return v;
}
function list(v: unknown, where: string, maxLen: number, maxItem: number): string[] {
  if (!Array.isArray(v) || v.length > maxLen) throw new LegacyDataError(`${where} 必须是不超过 ${maxLen} 条的数组`);
  return v.map((x, i) => text(x, `${where}[${i}]`, maxItem));
}
const obj = (v: unknown, where: string): Record<string, unknown> => {
  if (v === null || typeof v !== "object" || Array.isArray(v)) throw new LegacyDataError(`${where} 不是对象`);
  return v as Record<string, unknown>;
};

/** legacy 子进程的输出按不可信数据处理：类型、长度、条数、题目 ID 形状都要核对（沿用 KGE-01 安全审查建议 2） */
export function checkBank(x: unknown): LegacyBank {
  const root = obj(x, "legacy 输出");
  const topicsRaw = root.topics;
  if (!Array.isArray(topicsRaw) || topicsRaw.length === 0 || topicsRaw.length > 200) throw new LegacyDataError("topics 必须是 1–200 条");
  const topics = topicsRaw.map((t, i): LegacyTopic => {
    const o = obj(t, `topics[${i}]`);
    const id = text(o.id, `topics[${i}].id`, 8);
    if (!/^[a-z]{2,8}$/.test(id)) throw new LegacyDataError(`专题代码 ${id} 不合法`);
    const nodesRaw = o.nodes;
    if (!Array.isArray(nodesRaw) || nodesRaw.length > 2000) throw new LegacyDataError(`${id} 的节点清单不合法`);
    return {
      id, name: text(o.name, `${id}.name`, 128), chapterLabel: text(o.chapterLabel, `${id}.chapterLabel`, 64),
      nodes: nodesRaw.map((n, j) => {
        const no = obj(n, `${id}.nodes[${j}]`);
        return { id: text(no.id, `${id}.nodes[${j}].id`, 64), name: text(no.name, `${id}.nodes[${j}].name`, 128),
                 kind: text(no.kind, `${id}.nodes[${j}].kind`, 8), cluster: text(no.cluster, `${id}.nodes[${j}].cluster`, 64, true) };
      }),
    };
  });
  const topicIds = new Set(topics.map((t) => t.id));
  const qRaw = root.questions;
  if (!Array.isArray(qRaw) || qRaw.length === 0 || qRaw.length > 20000) throw new LegacyDataError("questions 必须是 1–20000 条");
  const questions = qRaw.map((q, i): LegacyQuestion => {
    const o = obj(q, `questions[${i}]`);
    const id = text(o.id, `questions[${i}].id`, 64);
    if (!ITEM_ID.test(id)) throw new LegacyDataError(`题目 ID ${id} 不符合 platform.skill_local_id`);
    const topic = o.topic === null ? null : text(o.topic, `${id}.topic`, 8);
    if (topic !== null && !topicIds.has(topic)) throw new LegacyDataError(`${id} 的专题 ${topic} 不存在`);
    return {
      id, topic, unit: text(o.unit, `${id}.unit`, 32, true), unitName: text(o.unitName, `${id}.unitName`, 128, true),
      text: text(o.text, `${id}.text`, 20000, true),
      mainNodes: list(o.mainNodes, `${id}.mainNodes`, 16, 128), auxNodes: list(o.auxNodes, `${id}.auxNodes`, 16, 128),
      preNodes: list(o.preNodes, `${id}.preNodes`, 16, 128),
    };
  });
  const cRaw = root.sprintCategories;
  if (!Array.isArray(cRaw) || cRaw.length > 100) throw new LegacyDataError("sprintCategories 必须是不超过 100 条的数组");
  const sprintCategories = cRaw.map((c, i): LegacySprintCategory => {
    const o = obj(c, `sprintCategories[${i}]`);
    const id = text(o.id, `sprintCategories[${i}].id`, 16);
    if (!/^[a-z]{1,16}$/.test(id)) throw new LegacyDataError(`压轴专项代码 ${id} 不合法`);
    return { id, name: text(o.name, `${id}.name`, 128), questionIds: list(o.questionIds, `${id}.questionIds`, 200, 64) };
  });
  return { topics, questions, sprintCategories };
}

/** 隔离子进程的启动参数：只读 legacy、本工具与 kge-inventory 的源码目录；没有写文件与起子进程的权限 */
export function isolatedArgs(ayaRoot: string, script: string, args: string[]): string[] {
  const readable = [ayaRoot, TOOL_DIR, INVENTORY_SRC].map((p) => `${realpathSync(p).replace(/\/$/, "")}/*`);
  return ["--no-warnings", "--permission", ...readable.map((p) => `--allow-fs-read=${p}`), "--allow-worker", script, ...args];
}

export function loadBank(aya: string): LegacyBank {
  const root = realpathSync(aya);
  const script = fileURLToPath(new URL("./extract-items.ts", import.meta.url));
  const out = execFileSync(process.execPath, isolatedArgs(root, script, [root]),
    { env: {}, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "inherit"] });
  return checkBank(JSON.parse(out));
}
