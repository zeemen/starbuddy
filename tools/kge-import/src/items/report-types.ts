// tagging-report.json 的格式（format = "kge-tagging-report/1"）。覆盖矩阵报表（tools/kge-coverage）按这里的类型读取。
// 约定：
//   - 文件是 UTF-8 的标准 JSON，外层两空格缩进、items 每题一行，末尾换行；同一输入两次生成逐字节相同（没有时间戳，数组都已排序）；
//   - 不含题干、答案、解析或任何学生数据：只有题目 ID、节点 ID、置信度、层、理由码与计数；
//   - items 按 item_id 升序，覆盖 legacy 里的全部题目（含不在已导入主题里的题，status = out_of_scope）；
//   - 置信度都是 0–1、最多 3 位小数；节点 ID 属于 map_version.id 这个地图版本；
//   - 与 items.sql 的对应：status = item_tag 的题写了 kge.item_tag；status = candidate 且 registered 不为 false 的题，
//     candidates 全部写进 kge.tag_candidate（未在内容包登记的题 platform.item 没有，外键过不去，不写库、进工作单）。
//   - 用了假实现（--vector / --model）时顶层有 warning 字段（“含假实现，不得执行”），run.fake_layers 列出假实现的层；
//     只跑规则层时没有 warning、fake_layers 为空。
// 兼容性：只追加字段；改动已有字段的含义要升 format 版本号。
//   追加记录：items[].tag_basis、run.fake_layers、warning（KGE-02 审查意见，迁移 0024 的 tag_basis 列）。

export type ReportLayer = "rule" | "vector" | "model";
export type ReportRole = "main" | "secondary";
export type ReportGrade = "A" | "B";

/**
 * 每道题的最终状态：
 * - item_tag：A 级、六段齐全、已在内容包登记 → 写 kge.item_tag（待审草稿）；
 * - candidate：B 级，或 A 级但六段未定 / 未登记 → 候选写 kge.tag_candidate，进低置信队列；
 * - untaggable：在已导入主题里，但三层都没有候选（“无处可标”，进工作单）；
 * - out_of_scope：题目所在章节还没有导入地图版本，不打标（只计数、列 ID）。
 */
export type ReportItemStatus = "item_tag" | "candidate" | "untaggable" | "out_of_scope";

/** B 级原因：无规则主考点 / 规则置信度低于 0.85 / 没有另一层一致 / 节点数超 3 / 没有任何主考点候选 */
export type ReportBReason = "no_rule_main" | "rule_below_threshold" | "no_layer_agreement" | "over_cap" | "no_main_candidate";

export interface ReportCandidate {
  node_id: string;
  role: ReportRole;
  layer: ReportLayer;
  confidence: number;
  reason_code: string;
  tagger_version: string;
  model_version: string | null;
}

/** A 级结论依据的一层及其版本（与 kge.item_tag.tag_basis 的元素相同）：规则层写打标器版本，向量层写嵌入器版本，模型层写模型版本 */
export interface ReportBasis {
  layer: ReportLayer;
  version: string;
}

export interface ReportSixSegments {
  region: string;
  exam_year: number;
  grade: 7 | 8 | 9;
  textbook_version: string;
  subject: "aya";
  content_version: string;
  /** 册次（不是六段之一，登记核对用） */
  term: "upper" | "lower";
  /** 推出六段所依据的章节代码 */
  chapter: string;
}

export interface ReportItem {
  item_id: string;
  /** 归属章节（专题代码）；推不出为 null */
  chapter: string | null;
  /** 归属依据：topic 专题注册表 / id_prefix 题目 ID 前缀 / main_nodes 主测节点所在章节 */
  route: "topic" | "id_prefix" | "main_nodes" | null;
  in_scope: boolean;
  status: ReportItemStatus;
  grade: ReportGrade | null;
  main_node_id: string | null;
  /** 含主考点，至多 3 个 */
  node_ids: string[];
  confidence: number | null;
  /** 最终结果的来源层（A 级为 rule） */
  layer: ReportLayer | null;
  /** 合并前的不同节点数超过 3（node_ids 已截断） */
  over_cap: boolean;
  b_reasons: ReportBReason[];
  /** 章节推不出六段时为 null */
  six_segments: ReportSixSegments | null;
  /** 所属题组（kge.item_family.code）；没有为 null */
  family_code: string | null;
  /** 在 adapters/aya/content/pack.json 里登记过（platform.item 有这道题）；没给内容包时为 null */
  registered: boolean | null;
  candidates: ReportCandidate[];
  /** A 级结论依据的每一层及其版本（按 rule、vector、model 排）；B 级、无处可标、未导入主题为空数组。写 item_tag 的题与 SQL 里的 tag_basis 相同 */
  tag_basis: ReportBasis[];
}

export interface ReportFamily {
  code: string;
  id: string;
  main_node_id: string;
  item_ids: string[];
}

export interface CountByLayer { rule: number; vector: number; model: number }

export interface TaggingReport {
  /** 只在用了假实现时出现：“含假实现，不得执行”的醒目标注（序列化时放在最前） */
  warning?: string;
  /** 固定为 "kge-tagging-report/1" */
  format: "kge-tagging-report/1";
  /** 指向本类型定义，便于读的人找到格式说明 */
  format_doc: string;
  subject: "aya";
  map_version: { id: string; version: string; imported_chapters: string[] };
  run: {
    layers: ReportLayer[];
    tagger_versions: { rule: string; vector: string | null; model: string | null };
    model_version: string | null;
    top_k: number;
    rules_only_a: boolean;
    /** 用了假实现的层（不是生产模型）；非空时本报告与同目录的 items.sql 都不得用于入库 */
    fake_layers: ReportLayer[];
  };
  thresholds: { a_min_confidence: number; rule_direct: number; rule_inferred: number; max_nodes: number };
  counts: {
    items_total: number;
    items_in_scope: number;
    items_out_of_scope: number;
    /** 未导入章节 → 题数 */
    out_of_scope_by_chapter: Record<string, number>;
    item_tag: number;
    candidate_items: number;
    untaggable: number;
    grade: { A: number; B: number };
    /** 写进 kge.tag_candidate 的行数 */
    candidate_rows: number;
    candidate_rows_by_layer: CountByLayer;
    candidate_rows_by_grade: { A: number; B: number };
    /** 在已导入主题里的题，三层去重后的全部候选（不论最后是否写候选表） */
    candidates_all_by_layer: CountByLayer;
    six_segments_derived: number;
    six_segments_missing: number;
    families: number;
    not_registered: number;
  };
  /** 自然语言节点引用（KGE-00 的 97 处）的解析结果 */
  text_refs: {
    total: number;
    map_matched: number;
    unmatched: number;
    by_field: { main: number; aux: number; pre: number };
  };
  families: ReportFamily[];
  items: ReportItem[];
}
