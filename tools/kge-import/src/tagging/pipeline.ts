// 打标流水线：规则层（总是跑）→ 向量层（可选）→ 模型层（可选，只能从规则与向量的候选里选）→ 合并分级。
// 默认只跑规则层；向量层、模型层通过参数开启（命令行 --vector / --model，只接假实现）。
import type { MapInput } from "../map-version.ts";
import { mergeAndGrade, type TagDecision } from "./merge.ts";
import { modelLayer, type ModelCandidate, type TagModel } from "./model.ts";
import { ruleLayer, type RuleContext, type RuleResult } from "./rules.ts";
import { checkCandidate, cmp, DEFAULT_TOP_K, RULE_TAGGER_VERSION, type Candidate, type Layer, type TagItem } from "./types.ts";
import { buildVectorIndex, vectorLayer, type Embedder, type VectorIndex } from "./vector.ts";

export interface PipelineOptions {
  embedder?: Embedder;
  model?: TagModel;
  topK?: number;
  rulesOnlyA?: boolean;
}

export interface Pipeline {
  layers: ReadonlySet<Layer>;
  taggerVersions: { rule: string; vector: string | null; model: string | null };
  modelVersion: string | null;
  topK: number;
  rulesOnlyA: boolean;
  tag(item: TagItem, inScope: boolean): { rule: RuleResult; decision: TagDecision | null };
}

export function makePipeline(map: MapInput, ctx: RuleContext, opts: PipelineOptions = {}): Pipeline {
  const topK = opts.topK ?? DEFAULT_TOP_K;
  if (!Number.isInteger(topK) || topK < 1 || topK > 20) throw new Error("Top-K 必须是 1–20 的整数");
  const layers = new Set<Layer>(["rule"]);
  const index: VectorIndex | null = opts.embedder ? buildVectorIndex(opts.embedder, map.nodes) : null;
  if (index) layers.add("vector");
  if (opts.model) layers.add("model");
  const names = new Map(map.nodes.map((n) => [n.id, n.name]));
  const rulesOnlyA = opts.rulesOnlyA ?? false;
  return {
    layers, topK, rulesOnlyA,
    taggerVersions: { rule: RULE_TAGGER_VERSION, vector: opts.embedder?.version ?? null, model: opts.model?.taggerVersion ?? null },
    modelVersion: opts.model?.modelVersion ?? null,
    tag(item, inScope) {
      const rule = ruleLayer(item, ctx, inScope);
      if (!inScope) return { rule, decision: null };
      const all: Candidate[] = [...rule.candidates];
      if (index) all.push(...vectorLayer(item, index, topK));
      if (opts.model) {
        // Top-K 候选：规则与向量层给出的节点，按最高置信度降序、出现顺序、节点 ID
        const score = new Map<string, number>();
        const first = new Map<string, number>();
        all.forEach((c, i) => {
          score.set(c.nodeId, Math.max(score.get(c.nodeId) ?? 0, c.confidence));
          if (!first.has(c.nodeId)) first.set(c.nodeId, i);
        });
        const candidates: ModelCandidate[] = [...score.keys()]
          .sort((a, b) => score.get(b)! - score.get(a)! || first.get(a)! - first.get(b)! || cmp(a, b))
          .map((nodeId) => ({ nodeId, name: names.get(nodeId) ?? nodeId, score: score.get(nodeId)! }));
        all.push(...modelLayer(opts.model, { itemId: item.id, text: item.text, candidates }));
      }
      all.forEach(checkCandidate);
      return { rule, decision: mergeAndGrade(item.id, all, { layers, rulesOnlyA }) };
    },
  };
}
