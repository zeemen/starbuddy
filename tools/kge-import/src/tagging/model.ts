// 打标模型层（spec 设计要点 2：本任务只做接口与假实现）。
// 模型只能在 Top-K 候选里选主考点与 1–3 个节点；输出候选、理由码（不存自由文本）与 model_version。
// 本文件的 modelLayer 负责把关：模型选了候选之外的节点、节点数不在 1–3、理由码或版本不合法，一律拒绝（抛错），不静默修正。
//
// TODO(KGE-02 待人确认 #4、#5)：真实实现经学习服务的模型路由（T8、L0）调用，只用境内已备案模型，题目文本不含学生数据、不出境；
// KGE V1.1 :664“离线构建可用 Claude”与 2026-09-28“一律不出境”冲突，口径未定前不接任何真实模型。
// 真实实现同样只实现 TagModel 接口，本工具不直接持有任何供应商密钥。
import { MAX_NODES, MODEL_VERSION, REASON_CODE, round3, TaggingError, VERSIONED_NAME, type Candidate } from "./types.ts";

export interface ModelCandidate { nodeId: string; name: string; score: number }
export interface ModelInput {
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
  choose(input: ModelInput): ModelChoice;
}

/**
 * 假模型：取分数最高的候选作主考点，再按分数取至多 2 个作次要节点，置信度固定 0.88。
 * **不是生产模型**，不联网、不调用任何模型，只用于测试与把流水线打通。
 */
export class FakeTagModel implements TagModel {
  readonly taggerVersion = "aya-tagger-model@0.1.0";
  readonly modelVersion = "model:fake-a/fake-tagger@0.0.0";
  choose(input: ModelInput): ModelChoice {
    const sorted = [...input.candidates].sort((a, b) => b.score - a.score || (a.nodeId < b.nodeId ? -1 : a.nodeId > b.nodeId ? 1 : 0));
    const main = sorted[0];
    if (!main) throw new TaggingError(`${input.itemId}：没有候选，假模型不作选择`);
    return { mainNodeId: main.nodeId, nodeIds: sorted.slice(0, MAX_NODES).map((c) => c.nodeId), confidence: 0.88, reasonCode: "model_choice" };
  }
}

/** 调用模型并把关；没有候选时不调用模型，返回空 */
export function modelLayer(model: TagModel, input: ModelInput): Candidate[] {
  if (input.candidates.length === 0) return [];
  if (!VERSIONED_NAME.test(model.taggerVersion)) throw new TaggingError(`模型层打标器版本 ${model.taggerVersion} 不合法`);
  if (!MODEL_VERSION.test(model.modelVersion)) throw new TaggingError(`模型版本 ${model.modelVersion} 不合法（应为 model:<vendor>/<model>@<ver>）`);
  const allowed = new Set(input.candidates.map((c) => c.nodeId));
  const out = model.choose(input);
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
