// 采分点入原子层（冲刺 4-K，spec 设计要点 3）：kge.scoring_point 是采分点的唯一真相源（规则 2、3）。
//   - 导入：内容包格式（adapters/zhixing 的 zhixing-content-pack/1，字段已对齐 0011）→ kge.scoring_point，带 rubric_version；
//     只新增不修改：已有同键（学科, 题目, rubric_version, point_id）的行必须逐字段相同，否则整个事务失败（改内容发新 rubric_version）。
//   - 一致性检查（make zhixing-rubric-check）：内容包与原子层导出（tools/kge-import/sql/export-scoring-points.sql）按同一 rubric_version
//     逐字段比对，不一致即失败。skill_node 是适配器侧的能力视图挂接（platform.skill_node），不在原子层，不参与比对。
// 本任务不改适配器运行时（改读 rubric.get 另起任务，spec 待人确认 #4）。
import { indexPack, type ContentPack, type PackItem } from "../../../../adapters/zhixing/src/content.ts";
import { jsonb, nodeArray, q, qn } from "../items/sql.ts";
import { cmp, DaofaError, SUBJECT } from "./common.ts";
import { syntheticGuard } from "./seed.ts";
import type { SnapshotScoringPoint } from "./snapshot.ts";

export const EXPORT_FORMAT = "kge-scoring-point-export/1";

/** 原子层一行采分点（kge.scoring_point 除 id、map_version_id、subject 外的列） */
export interface ScoringAtom {
  item_id: string;
  rubric_version: string;
  point_id: string;
  score: number;
  node_ids: string[];
  subject_dimension: string | null;
  keyword_family: unknown;
  required_logic: string | null;
  material_linkage: string | null;
  partial_credit_rule: unknown;
  common_errors: unknown;
}

/** 参与比对的字段（每个字段的差异都要能被发现：tests/daofa/rubric.test.ts 的变异逐个覆盖） */
export const COMPARED_FIELDS = [
  "score", "node_ids", "subject_dimension", "keyword_family", "required_logic", "material_linkage", "partial_credit_rule", "common_errors",
] as const;

export interface RubricExport {
  format: typeof EXPORT_FORMAT;
  subject: "zhixing";
  points: ScoringAtom[];
}

const atomKey = (a: { item_id: string; rubric_version: string; point_id: string }) => `${a.item_id}\u0000${a.rubric_version}\u0000${a.point_id}`;

/** 内容包 → 原子（只收道法题；历史题拒绝，decisions #13） */
export function atomsFromPack(pack: ContentPack): ScoringAtom[] {
  const { pack: p } = indexPack(pack); // 与适配器启动时同一套校验
  const out: ScoringAtom[] = [];
  for (const it of p.items as PackItem[]) {
    if (it.discipline !== "daofa") throw new DaofaError(`题目 ${it.id} 不是道法题，不能写进道法的采分点（道法与历史分库）`);
    for (const pt of it.points) {
      out.push({
        item_id: it.id, rubric_version: it.rubric_version, point_id: pt.point_id, score: pt.score, node_ids: [...pt.node_ids],
        subject_dimension: pt.subject_dimension,
        keyword_family: { core: pt.keyword_family.core, accepted: pt.keyword_family.accepted, colloquial: pt.keyword_family.colloquial },
        required_logic: pt.required_logic, material_linkage: pt.material_linkage,
        partial_credit_rule: { mode: pt.partial_credit_rule.mode, allowed_scores: pt.partial_credit_rule.allowed_scores,
          description: pt.partial_credit_rule.description },
        common_errors: pt.common_errors.map((e) => ({ type: e.type, pattern: e.pattern, note: e.note })),
      });
    }
  }
  return out.sort((a, b) => cmp(atomKey(a), atomKey(b)));
}

/** 生成写 kge.scoring_point 的 SQL；nodeIds 是目标地图版本里的观点 ID，采分点只能挂在观点上 */
export function renderScoringSql(atoms: ScoringAtom[], opts: { mapVersionId: string; viewpointIds: ReadonlySet<string>; synthetic: boolean; source: string }): string {
  if (atoms.length === 0) throw new DaofaError("内容包里没有采分点");
  for (const a of atoms) {
    for (const n of a.node_ids) {
      if (!opts.viewpointIds.has(n)) throw new DaofaError(`题目 ${a.item_id} 采分点 ${a.point_id} 的节点 ${n} 不是目标地图版本里的观点`);
    }
  }
  const mv = opts.mapVersionId;
  const row = (a: ScoringAtom) => [q(mv), `'${SUBJECT}'`, q(a.item_id), q(a.rubric_version), q(a.point_id), a.score.toFixed(2), nodeArray(a.node_ids),
    qn(a.subject_dimension), jsonb(a.keyword_family), qn(a.required_logic), qn(a.material_linkage), jsonb(a.partial_credit_rule), jsonb(a.common_errors)];
  const cols = "map_version_id, subject, item_id, rubric_version, point_id, score, node_ids, subject_dimension, keyword_family, required_logic, "
    + "material_linkage, partial_credit_rule, common_errors";
  const values = atoms.map((a) => `  (${row(a).join(", ")})`).join(",\n");
  if (values.includes("$check$")) throw new DaofaError("采分点文本里不能出现 $check$（生成的核对块用它作界定符）");
  return [
    `-- 冲刺 4-K 采分点入原子层（生成文件，勿手改）：${opts.source} → kge.scoring_point（${atoms.length} 个采分点）`,
    "-- 只新增不修改：同键已存在时必须逐字段相同，否则整个事务失败（改内容请发新 rubric_version）。不含任何学生数据。",
    ...(opts.synthetic ? ["-- 合成样例（合成、非真题）：只用于在一次性测试库证明流程。"] : []),
    "BEGIN;",
    "SET LOCAL standard_conforming_strings = on;",
    ...(opts.synthetic ? [syntheticGuard()] : []),
    "SET LOCAL ROLE starbuddy_kge;",
    `INSERT INTO kge.scoring_point (${cols}) VALUES`,
    values,
    "ON CONFLICT (subject, item_id, rubric_version, point_id) DO NOTHING;",
    "-- 核对：库里的每一个采分点都与本文件逐字段相同（已存在的旧行内容不同 → 失败）",
    "DO $check$",
    "DECLARE v_bad integer;",
    "BEGIN",
    "  SELECT count(*) INTO v_bad",
    `    FROM (VALUES\n${values.replace(/^ {2}/gm, "      ")}) AS v(${cols})`,
    "   WHERE NOT EXISTS (SELECT 1 FROM kge.scoring_point s",
    "                      WHERE s.subject = v.subject::platform.subject AND s.item_id = v.item_id AND s.rubric_version = v.rubric_version",
    "                        AND s.point_id = v.point_id AND s.map_version_id = v.map_version_id::uuid AND s.score = v.score",
    "                        AND s.node_ids = v.node_ids AND s.subject_dimension IS NOT DISTINCT FROM v.subject_dimension",
    "                        AND s.keyword_family = v.keyword_family AND s.required_logic IS NOT DISTINCT FROM v.required_logic",
    "                        AND s.material_linkage IS NOT DISTINCT FROM v.material_linkage",
    "                        AND s.partial_credit_rule = v.partial_credit_rule AND s.common_errors = v.common_errors);",
    "  IF v_bad > 0 THEN",
    "    RAISE EXCEPTION '采分点只能新增不能修改：% 个采分点与库里同一 rubric_version 的已有内容不同（改内容请发新 rubric_version）', v_bad",
    "      USING ERRCODE = 'check_violation';",
    "  END IF;",
    "END $check$;",
    "COMMIT;",
    "",
  ].join("\n");
}

export const snapshotPoints = (atoms: ScoringAtom[]): SnapshotScoringPoint[] =>
  atoms.map((a) => ({ item_id: a.item_id, rubric_version: a.rubric_version, point_id: a.point_id, score: a.score, node_ids: a.node_ids }));

// ---------- 一致性检查 ----------
function canon(v: unknown): string {
  const norm = (x: unknown): unknown => {
    if (Array.isArray(x)) return x.map(norm);
    if (x !== null && typeof x === "object") return Object.fromEntries(Object.keys(x).sort(cmp).map((k) => [k, norm((x as Record<string, unknown>)[k])]));
    return x;
  };
  return JSON.stringify(norm(v));
}

/** 读原子层导出（不可信输入：逐项核对形状） */
export function parseExport(text: string): RubricExport {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new DaofaError("原子层导出不是 JSON");
  }
  const o = raw as Record<string, unknown>;
  if (o === null || typeof o !== "object" || o.format !== EXPORT_FORMAT) throw new DaofaError(`原子层导出的 format 应为 ${EXPORT_FORMAT}`);
  if (o.subject !== SUBJECT) throw new DaofaError("原子层导出的学科应为 zhixing");
  if (!Array.isArray(o.points)) throw new DaofaError("原子层导出缺 points");
  const points = o.points.map((p, i): ScoringAtom => {
    const x = p as Record<string, unknown>;
    const where = `points[${i}]`;
    for (const k of ["item_id", "rubric_version", "point_id"]) if (typeof x?.[k] !== "string") throw new DaofaError(`${where}.${k} 应为字符串`);
    if (typeof x.score !== "number") throw new DaofaError(`${where}.score 应为数字`);
    if (!Array.isArray(x.node_ids) || !x.node_ids.every((n) => typeof n === "string")) throw new DaofaError(`${where}.node_ids 应为字符串数组`);
    for (const k of ["subject_dimension", "required_logic", "material_linkage"]) {
      if (x[k] !== null && typeof x[k] !== "string") throw new DaofaError(`${where}.${k} 应为字符串或 null`);
    }
    return {
      item_id: x.item_id as string, rubric_version: x.rubric_version as string, point_id: x.point_id as string, score: x.score,
      node_ids: x.node_ids as string[], subject_dimension: x.subject_dimension as string | null, keyword_family: x.keyword_family,
      required_logic: x.required_logic as string | null, material_linkage: x.material_linkage as string | null,
      partial_credit_rule: x.partial_credit_rule, common_errors: x.common_errors,
    };
  });
  return { format: EXPORT_FORMAT, subject: SUBJECT, points };
}

export interface RubricDiff { key: string; field: string; detail: string }

/**
 * 内容包 vs 原子层导出：内容包里出现的每个 rubric_version，导出里同版本的采分点集合与每个字段都必须相同。
 * 导出里别的 rubric_version（旧版本、别的题）不参与比对。返回差异清单（空 = 一致）；只含 ID 与字段名，不回显内容。
 */
export function diffRubric(packAtoms: ScoringAtom[], exported: ScoringAtom[]): RubricDiff[] {
  const versions = new Set(packAtoms.map((a) => a.rubric_version));
  const out: RubricDiff[] = [];
  const mine = new Map(packAtoms.map((a) => [atomKey(a), a]));
  const theirs = new Map<string, ScoringAtom>();
  for (const a of exported) {
    if (!versions.has(a.rubric_version)) continue;
    const k = atomKey(a);
    if (theirs.has(k)) out.push({ key: k.replace(/\u0000/g, " / "), field: "(key)", detail: "原子层导出里重复" });
    theirs.set(k, a);
  }
  const keys = [...new Set([...mine.keys(), ...theirs.keys()])].sort(cmp);
  for (const k of keys) {
    const label = k.replace(/\u0000/g, " / ");
    const a = mine.get(k), b = theirs.get(k);
    if (!b) { out.push({ key: label, field: "(point)", detail: "内容包有、原子层没有" }); continue; }
    if (!a) { out.push({ key: label, field: "(point)", detail: "原子层有、内容包没有" }); continue; }
    for (const f of COMPARED_FIELDS) {
      const same = f === "score" ? Math.abs(a.score - b.score) < 1e-9 : canon(a[f]) === canon(b[f]);
      if (!same) out.push({ key: label, field: f, detail: "字段不一致" });
    }
  }
  return out;
}
