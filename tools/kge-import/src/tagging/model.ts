// 打标模型层（spec 设计要点 2：本任务只做接口与假实现）。
// 模型只能在 Top-K 候选里选主考点与 1–3 个节点；输出候选、理由码（不存自由文本）与 model_version。
// 本文件的 modelLayer 负责把关：模型选了候选之外的节点、节点数不在 1–3、理由码或版本不合法，一律拒绝（抛错），不静默修正。
//
// TODO(KGE-02 待人确认 #4、#5)：真实实现经学习服务的模型路由（T8、L0）调用，只用境内已备案模型，题目文本不含学生数据、不出境；
// KGE V1.1 :664“离线构建可用 Claude”与 2026-09-28“一律不出境”冲突，口径未定前不接任何真实模型。
// 真实实现同样只实现 TagModel 接口，本工具不直接持有任何供应商密钥；RoutedTagModel 只是定下接口形状的桩，调用即抛错。
// 接口是异步的（真实实现要经模型路由），入参带路由必需字段：task = T8、data_class = L0。
import { checkRouteConfig, type RouteConfig } from "./route.ts";
import {
  MAX_NODES, MODEL_TASK, MODEL_VERSION, REASON_CODE, ROUTE_NOT_CONNECTED, round3, TAGGING_DATA_CLASS, TaggingError, VERSIONED_NAME,
  type Candidate, type DataClass,
} from "./types.ts";

export interface ModelCandidate { nodeId: string; name: string; score: number }
export interface ModelInput {
  /** 模型路由任务：打标选节点固定 T8 */
  task: typeof MODEL_TASK;
  /** 数据等级：只送题目文本与节点名，L0（不含学生数据） */
  data_class: DataClass;
  itemId: string;
  /** 题目文本（只在内存里传给模型路由；不落盘） */
  text: string;
  /** Top-K 候选（模型只能从这里选） */
  candidates: ModelCandidate[];
}
export interface ModelChoice {
  mainNodeId: string;
  /** 含主考点，1–3 个 */
  nodeIds: string[];
  confidence: number;
  /** 理由码，^[a-z0-9_]{1,48}$，不接受自由文本 */
  reasonCode: string;
}
export interface TagModel {
  /** 打标器版本（VersionedName） */
  readonly taggerVersion: string;
  /** 模型版本，格式 model:<vendor>/<model>@<ver> */
  readonly modelVersion: string;
  /** 假实现为 true：流水线据此在产物里标注“含假实现，不得执行”，命令行据此拒绝写进种子目录 */
  readonly fake: boolean;
  choose(input: ModelInput): Promise<ModelChoice>;
}

function checkModelInput(input: ModelInput, who: string): void {
  if (input?.task !== MODEL_TASK || input.data_class !== TAGGING_DATA_CLASS) {
    throw new TaggingError(`${who}：请求必须带 task = ${MODEL_TASK}、data_class = ${TAGGING_DATA_CLASS}`);
  }
}

/**
 * 假模型：取分数最高的候选作主考点，再按分数取至多 2 个作次要节点，置信度固定 0.88。
 * **不是生产模型**，不联网、不调用任何模型，只用于测试与把流水线打通。
 */
export class FakeTagModel implements TagModel {
  readonly taggerVersion = "aya-tagger-model@0.1.0";
  readonly modelVersion = "model:fake-a/fake-tagger@0.0.0";
  readonly fake = true;
  async choose(input: ModelInput): Promise<ModelChoice> {
    checkModelInput(input, "假模型");
    const sorted = [...input.candidates].sort((a, b) => b.score - a.score || (a.nodeId < b.nodeId ? -1 : a.nodeId > b.nodeId ? 1 : 0));
    const main = sorted[0];
    if (!main) throw new TaggingError(`${input.itemId}：没有候选，假模型不作选择`);
    return { mainNodeId: main.nodeId, nodeIds: sorted.slice(0, MAX_NODES).map((c) => c.nodeId), confidence: 0.88, reasonCode: "model_choice" };
  }
}

/**
 * 打标模型经模型路由 T8 的桩：构造时要求路由地址、令牌、打标器版本与模型版本；**调用时一律抛错**，不回落到假实现。
 * 命令行（main.ts）不接它，只用来把接口形状定下来；接入要等 spec 待人确认 #4、#5（境内已备案模型、出境口径）。
 */
export class RoutedTagModel implements TagModel {
  readonly taggerVersion: string;
  readonly modelVersion: string;
  readonly fake = false;
  readonly routeUrl: string;
  readonly #token: string;
  constructor(cfg: RouteConfig & { taggerVersion: string; modelVersion: string }) {
    this.routeUrl = checkRouteConfig(cfg, "RoutedTagModel");
    if (!VERSIONED_NAME.test(cfg.taggerVersion)) throw new TaggingError(`RoutedTagModel：打标器版本 ${String(cfg.taggerVersion)} 不合法`);
    if (!MODEL_VERSION.test(cfg.modelVersion)) throw new TaggingError(`RoutedTagModel：模型版本 ${String(cfg.modelVersion)} 不合法（应为 model:<vendor>/<model>@<ver>）`);
    this.taggerVersion = cfg.taggerVersion;
    this.modelVersion = cfg.modelVersion;
    this.#token = cfg.token;
  }
  /** 令牌已配置（不暴露令牌本身） */
  get hasToken(): boolean {
    return this.#token.length > 0;
  }
  async choose(input: ModelInput): Promise<ModelChoice> {
    checkModelInput(input, "RoutedTagModel");
    throw new TaggingError(`RoutedTagModel：${ROUTE_NOT_CONNECTED}`);
  }
}

/** 给模型的请求：补上路由必需字段 */
export const modelInput = (itemId: string, text: string, candidates: ModelCandidate[]): ModelInput =>
  ({ task: MODEL_TASK, data_class: TAGGING_DATA_CLASS, itemId, text, candidates });

/** 调用模型并把关；没有候选时不调用模型，返回空 */
export async function modelLayer(model: TagModel, input: ModelInput): Promise<Candidate[]> {
  if (input.candidates.length === 0) return [];
  checkModelInput(input, "模型层");
  if (!VERSIONED_NAME.test(model.taggerVersion)) throw new TaggingError(`模型层打标器版本 ${model.taggerVersion} 不合法`);
  if (!MODEL_VERSION.test(model.modelVersion)) throw new TaggingError(`模型版本 ${model.modelVersion} 不合法（应为 model:<vendor>/<model>@<ver>）`);
  const allowed = new Set(input.candidates.map((c) => c.nodeId));
  const out = await model.choose(input);
  const where = `${input.itemId}：模型输出`;
  if (!allowed.has(out.mainNodeId)) throw new TaggingError(`${where}的主考点 ${out.mainNodeId} 不在 Top-K 候选里`);
  if (!Array.isArray(out.nodeIds) || out.nodeIds.length < 1 || out.nodeIds.length > MAX_NODES) throw new TaggingError(`${where}的节点数必须是 1–${MAX_NODES}`);
  if (new Set(out.nodeIds).size !== out.nodeIds.length) throw new TaggingError(`${where}的节点有重复`);
  for (const n of out.nodeIds) if (!allowed.has(n)) throw new TaggingError(`${where}的节点 ${n} 不在 Top-K 候选里`);
  if (!out.nodeIds.includes(out.mainNodeId)) throw new TaggingError(`${where}的节点清单没有包含主考点`);
  if (!REASON_CODE.test(out.reasonCode)) throw new TaggingError(`${where}的理由码不合法（只收 ^[a-z0-9_]{1,48}$，不收自由文本）`);
  if (!(out.confidence >= 0 && out.confidence <= 1)) throw new TaggingError(`${where}的置信度必须在 0–1`);
  const confidence = round3(out.confidence);
  return out.nodeIds.map((nodeId): Candidate => ({
    itemId: input.itemId, nodeId, role: nodeId === out.mainNodeId ? "main" : "secondary", layer: "model", confidence,
    taggerVersion: model.taggerVersion, modelVersion: model.modelVersion, reasonCode: out.reasonCode,
  }));
}
