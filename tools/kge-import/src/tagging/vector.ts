// 打标向量层（spec 设计要点 2：本任务只做接口与假实现）。
// 向量只用于识别节点与兜底检索（KGE V1.1 :241、:480）：按节点“名称 / 包含 / 题例”文本与题目文本的相似度排序，给出 Top-K 候选。
// 接口是异步的（真实实现要经模型路由），请求带路由必需字段：task = T7、data_class = L0（只送题目文本与节点文本，不含学生数据）。
//
// TODO(KGE-02 待人确认 #4)：真实嵌入模型（默认建议自托管 BGE-M3 + pgvector，或登记境内嵌入 API）属于新增供应商或依赖，
// 必停项；登记并开放模型路由 T7 之前，流水线只能用下面的假实现。RoutedEmbedder 只是定下接口形状的桩，调用即抛错。
import type { MapNode } from "../map-version.ts";
import { checkRouteConfig, type RouteConfig } from "./route.ts";
import {
  cmp, ROUTE_NOT_CONNECTED, round3, TAGGING_DATA_CLASS, TaggingError, VECTOR_TASK, VERSIONED_NAME,
  type Candidate, type DataClass, type TagItem,
} from "./types.ts";

/** 发给嵌入器的请求：路由必需字段 + 文本（只在内存里，不落盘） */
export interface EmbedRequest {
  task: typeof VECTOR_TASK;
  data_class: DataClass;
  texts: string[];
}

export interface Embedder {
  /** 嵌入器版本（VersionedName），写进候选的 tagger_version 与 A 级的 tag_basis */
  readonly version: string;
  /** 假实现为 true：流水线据此在产物里标注“含假实现，不得执行”，命令行据此拒绝写进种子目录 */
  readonly fake: boolean;
  /** 返回与输入等长的向量；同一输入必须得到同一向量（确定性） */
  embed(req: EmbedRequest): Promise<number[][]>;
}

export const embedRequest = (texts: string[]): EmbedRequest => ({ task: VECTOR_TASK, data_class: TAGGING_DATA_CLASS, texts });

function checkEmbedRequest(req: EmbedRequest, who: string): void {
  if (req?.task !== VECTOR_TASK || req.data_class !== TAGGING_DATA_CLASS) {
    throw new TaggingError(`${who}：请求必须带 task = ${VECTOR_TASK}、data_class = ${TAGGING_DATA_CLASS}`);
  }
  if (!Array.isArray(req.texts)) throw new TaggingError(`${who}：texts 应为数组`);
}

/**
 * 假嵌入：字符一元、二元组哈希到固定维度后做 L2 归一化，相似度即余弦。
 * **不是生产模型**，没有任何语义能力，只用于测试与把流水线打通；不联网、不调用任何模型。
 */
export class FakeNgramEmbedder implements Embedder {
  readonly version = "fake-ngram-embedder@0.0.0";
  readonly fake = true;
  private readonly dims: number;
  constructor(dims = 256) {
    this.dims = dims;
  }
  async embed(req: EmbedRequest): Promise<number[][]> {
    checkEmbedRequest(req, "假嵌入");
    return req.texts.map((t) => {
      const v = new Array<number>(this.dims).fill(0);
      const chars = [...t.normalize("NFKC").replace(/\s+/g, "")];
      const grams = [...chars, ...chars.slice(1).map((c, i) => chars[i]! + c)];
      for (const g of grams) v[fnv1a(g) % this.dims]! += 1;
      const norm = Math.sqrt(v.reduce((a, x) => a + x * x, 0));
      return norm === 0 ? v : v.map((x) => x / norm);
    });
  }
}

/**
 * 真实嵌入经模型路由 T7 的桩：构造时要求路由地址、令牌与嵌入器版本；**调用时一律抛错**，不回落到假实现。
 * 命令行（main.ts）不接它，只用来把接口形状定下来；接入要等 spec 待人确认 #4、#5。
 */
export class RoutedEmbedder implements Embedder {
  readonly version: string;
  readonly fake = false;
  readonly routeUrl: string;
  readonly #token: string;
  constructor(cfg: RouteConfig & { version: string }) {
    this.routeUrl = checkRouteConfig(cfg, "RoutedEmbedder");
    if (!VERSIONED_NAME.test(cfg.version)) throw new TaggingError(`RoutedEmbedder：嵌入器版本 ${String(cfg.version)} 不合法`);
    this.version = cfg.version;
    this.#token = cfg.token;
  }
  /** 令牌已配置（不暴露令牌本身） */
  get hasToken(): boolean {
    return this.#token.length > 0;
  }
  async embed(req: EmbedRequest): Promise<number[][]> {
    checkEmbedRequest(req, "RoutedEmbedder");
    throw new TaggingError(`RoutedEmbedder：${ROUTE_NOT_CONNECTED}`);
  }
}

function fnv1a(s: string): number {
  let h = 0x811c9dc5;
  for (const ch of s) {
    h ^= ch.codePointAt(0)!;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h;
}

export function cosine(a: number[], b: number[]): number {
  if (a.length !== b.length) throw new TaggingError("向量维度不一致");
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i]! * b[i]!;
    na += a[i]! * a[i]!;
    nb += b[i]! * b[i]!;
  }
  return na === 0 || nb === 0 ? 0 : dot / Math.sqrt(na * nb);
}

/** 节点的检索文本：名称、包含、题例（KGE-01 导入时包含 / 题例多为空，只剩名称） */
export function nodeText(n: MapNode): string {
  return [n.name, ...n.includes, ...n.examples].join("\n");
}

export interface VectorIndex { embedder: Embedder; nodes: MapNode[]; vectors: number[][] }

/** 只索引叶子节点（知识节点）；领域、主题不参与相似度排序 */
export async function buildVectorIndex(embedder: Embedder, nodes: MapNode[]): Promise<VectorIndex> {
  if (!VERSIONED_NAME.test(embedder.version)) throw new TaggingError(`嵌入器版本 ${embedder.version} 不合法`);
  const leaves = nodes.filter((n) => n.level === "node").sort((a, b) => cmp(a.id, b.id));
  const vectors = await embedder.embed(embedRequest(leaves.map(nodeText)));
  if (vectors.length !== leaves.length) throw new TaggingError("嵌入器返回的向量条数不对");
  return { embedder, nodes: leaves, vectors };
}

/** Top-K：相似度降序、节点 ID 升序；第 1 名作主考点候选，其余作次要候选。相似度 ≤ 0 的不出候选 */
export async function vectorLayer(item: TagItem, index: VectorIndex, topK: number): Promise<Candidate[]> {
  const [q] = await index.embedder.embed(embedRequest([item.text]));
  if (!q) throw new TaggingError("嵌入器没有返回题目向量");
  const scored = index.nodes
    .map((n, i) => ({ id: n.id, score: round3(Math.max(0, Math.min(1, cosine(q, index.vectors[i]!)))) }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || cmp(a.id, b.id))
    .slice(0, topK);
  return scored.map((x, rank): Candidate => ({
    itemId: item.id, nodeId: x.id, role: rank === 0 ? "main" : "secondary", layer: "vector", confidence: x.score,
    taggerVersion: index.embedder.version, modelVersion: null, reasonCode: "vector_similarity",
  }));
}
