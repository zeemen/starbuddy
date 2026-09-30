// plan 的领域权重（spec 4-D 设计要点“知行 plan”；待人确认 #2）。
// KGE V1.1 第八章：道法观点之间以“关联”为主，不为路径规划硬凑前置；学习路径按“领域权重 × 失分 × 可提升”排序。
// 领域权重应由道法教研给出（4-K 真实数据）；在那之前用 content/plan-weights.json 里的合成值，文件自带“待教研校准”标注，
// source_version 里的版本段带 `-uncalibrated` 后缀，下游能机器识别“未校准”。
// 不改 content/pack.json（其采分点须与原子层 kge.scoring_point 一致，make zhixing-rubric-check）：权重单独成文件。
// 领域的归属按采分点的第一个 node_ids（kge.node_id）前缀匹配；加载时要求每个道法采分点恰好落在一个领域里。
import { readFileSync } from "node:fs";
import type { LoadedPack, ScoringPointDef } from "./content.ts";

export const PLAN_WEIGHTS_FORMAT = "zhixing-plan-weights/1";
export const CALIBRATION_STATES = ["pending_research_calibration", "calibrated"] as const;
export type CalibrationState = (typeof CALIBRATION_STATES)[number];

export interface PlanDomain {
  /** 领域编码（小写，只在适配器内部与 task_id 派生里用） */
  code: string;
  /** kge.node_id 前缀（不含末尾的点），例如 MORAL.SYN.LAW */
  node_prefix: string;
  /** 领域权重，(0, 1] */
  weight: number;
}

export interface PlanWeightsFile {
  format: typeof PLAN_WEIGHTS_FORMAT;
  name: string;
  version: string;
  synthetic: boolean;
  calibration: CalibrationState;
  notice: string;
  domains: PlanDomain[];
}

export interface PlanWeights {
  file: PlanWeightsFile;
  domainByCode: Map<string, PlanDomain>;
  /** 采分点 → 领域编码 */
  domainOfPoint(p: ScoringPointDef): string;
  /** SourceVersion 的一段，例如 `algo:zhixing-plan-weights@0.1.0-uncalibrated` */
  sourceVersion: string;
}

const NAME_PART = /^[a-z0-9][a-z0-9\-]{0,47}$/;
const VERSION_PART = /^[A-Za-z0-9][A-Za-z0-9._\-]{0,18}$/;
const DOMAIN_CODE = /^[a-z0-9_]{1,16}$/;
const NODE_PREFIX = /^MORAL(\.[A-Z0-9][A-Z0-9_]{0,31}){1,3}$/;

/** 检查权重文件与内容包的一致性；不通过就拒绝启动。 */
export function indexPlanWeights(file: PlanWeightsFile, content: LoadedPack): PlanWeights {
  const problems: string[] = [];
  if (file.format !== PLAN_WEIGHTS_FORMAT) problems.push(`format 应为 ${PLAN_WEIGHTS_FORMAT}`);
  if (!NAME_PART.test(file.name)) problems.push("name 不符合 SourceVersion 的名称格式");
  if (!VERSION_PART.test(file.version)) problems.push("version 不符合 SourceVersion 的版本格式");
  if (!(CALIBRATION_STATES as readonly string[]).includes(file.calibration)) problems.push("calibration 未知");
  if (file.synthetic !== true && file.calibration !== "calibrated") problems.push("非合成的权重必须是已校准的");
  if (file.calibration !== "calibrated" && !(typeof file.notice === "string" && file.notice.includes("合成") && file.notice.includes("待教研校准"))) {
    problems.push("未校准的权重必须在 notice 里标注“合成”“待教研校准”");
  }
  const domainByCode = new Map<string, PlanDomain>();
  const prefixes = new Set<string>();
  for (const d of file.domains ?? []) {
    if (!DOMAIN_CODE.test(d.code)) problems.push(`领域 ${d.code} 的编码不合格`);
    if (domainByCode.has(d.code)) problems.push(`领域 ${d.code} 重复`);
    if (!NODE_PREFIX.test(d.node_prefix)) problems.push(`领域 ${d.code} 的 node_prefix 不是 MORAL 命名空间的 kge.node_id 前缀`);
    if (prefixes.has(d.node_prefix)) problems.push(`领域 ${d.code} 的 node_prefix 重复`);
    if (!(typeof d.weight === "number" && d.weight > 0 && d.weight <= 1)) problems.push(`领域 ${d.code} 的权重必须在 (0, 1]`);
    domainByCode.set(d.code, d);
    prefixes.add(d.node_prefix);
  }
  if (domainByCode.size === 0) problems.push("至少要有一个领域");
  const matches = (p: ScoringPointDef) => [...domainByCode.values()].filter((d) => (p.node_ids[0] ?? "").startsWith(`${d.node_prefix}.`));
  for (const it of content.pack.items) {
    if (it.discipline !== "daofa") continue;
    for (const p of it.points) {
      const m = matches(p);
      if (m.length !== 1) problems.push(`题目 ${it.id} 采分点 ${p.point_id} 落在 ${m.length} 个领域里（应恰好 1 个）`);
    }
  }
  if (problems.length) throw new Error(`plan 领域权重不合格：\n  ${problems.slice(0, 20).join("\n  ")}`);
  const suffix = file.calibration === "calibrated" ? "" : "-uncalibrated";
  return {
    file,
    domainByCode,
    domainOfPoint: (p) => matches(p)[0]!.code,
    sourceVersion: `algo:${file.name}@${file.version}${suffix}`,
  };
}

export const DEFAULT_PLAN_WEIGHTS_PATH = new URL("../content/plan-weights.json", import.meta.url);

export function loadPlanWeights(content: LoadedPack, path: string | URL = DEFAULT_PLAN_WEIGHTS_PATH): PlanWeights {
  return indexPlanWeights(JSON.parse(readFileSync(path, "utf8")) as PlanWeightsFile, content);
}
