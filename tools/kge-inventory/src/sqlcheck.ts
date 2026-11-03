// 库模式只读 SQL 的静态检查（spec 设计要点 4、验收）：只读事务、只查白名单内容表、不出现学生表、不 SELECT *、没有写语句。
// 返回违规说明；空数组表示通过。

/** 每类查询允许的“表 → 列”。分组列只能是枚举或低基数状态，不能是内容、作者或任何可能含个人信息的列（安全审查）。 */
export const ALLOWED: Record<"rows" | "group" | "true" | "notnull", Record<string, string[]>> = {
  rows: Object.fromEntries(
    ["knowledge_nodes", "questions", "knowledge_point", "item_bank", "textbook_catalog", "recitation_corpus", "classical_text", "daofa_item", "doc_chunks"].map((t) => [t, []]),
  ),
  group: {
    knowledge_nodes: ["kind"],
    knowledge_point: ["domain", "level", "status"],
    item_bank: ["domain", "review_status", "source"],
    textbook_catalog: ["grade"],
    classical_text: ["status"],
    daofa_item: ["status"],
    doc_chunks: ["source"],
  },
  true: { textbook_catalog: ["must_recite"], recitation_corpus: ["must_recite"] },
  notnull: { knowledge_point: ["catalog_ref"], item_bank: ["textbook_anchor"], daofa_item: ["rubric"] },
};

/** 库模式输出里允许出现的键（section:表[.列]），parseDbOutput 用它拒绝陌生行 */
export function allowedOutputKeys(): Set<string> {
  const keys = new Set<string>();
  for (const [section, tables] of Object.entries(ALLOWED)) {
    for (const [t, cols] of Object.entries(tables)) {
      if (cols.length === 0) keys.add(`${section}:${t}`);
      for (const c of cols) keys.add(`${section}:${t}.${c}`);
    }
  }
  for (const t of Object.keys(ALLOWED.rows)) keys.add(`columns:${t}`);
  return keys;
}

export const CONTENT_TABLES = [
  "knowledge_nodes", "questions", "knowledge_point", "item_bank", "textbook_catalog",
  "recitation_corpus", "classical_text", "daofa_item", "doc_chunks",
];
// 两个 legacy 库里的学生个人表与账号表（阿鸦 db/schema.sql、文砺 supabase/migrations）
export const STUDENT_TABLES = [
  "students", "profiles", "attempts", "node_mastery", "coach_sessions", "guardians", "ability_state",
  "ability_self_rating", "assessment_event", "bind_code", "calibration_sample", "daily_checkin", "daofa_ability",
  "daofa_submission", "daofa_review_queue", "debate_session", "diagnostic_session", "error_record", "essay_review",
  "intake_profile", "mastery_snapshot", "mock_exam", "parent_action_plan", "parent_consult_session", "parent_report",
  "pattern_flag", "perception_card", "point_mastery", "quiz_attempt", "review_log", "self_assessment",
  "semester_assessment", "semester_score", "signal_conflict", "socratic_session", "stage_report", "study_plan",
  "submission", "submission_file", "wechat_identity", "item_review",
];

export function checkReadonlySql(sql: string): string[] {
  const problems: string[] = [];
  const code = sql.replace(/--.*$/gm, "");
  if (
    !/^\\set ON_ERROR_STOP on\s*\nBEGIN TRANSACTION READ ONLY;\s*\nSET LOCAL statement_timeout = '30s';\s*\nSET LOCAL lock_timeout = '2s';\s*\nSET LOCAL row_security = off;/m.test(
      code,
    )
  ) {
    problems.push("开头必须是 \\set ON_ERROR_STOP on、BEGIN TRANSACTION READ ONLY、statement_timeout、lock_timeout、row_security = off");
  }
  if (!/ROLLBACK;$/.test(code.trimEnd())) problems.push("必须以 ROLLBACK 结束");
  for (const s of code.split(";").map((x) => x.trim()).filter(Boolean)) {
    if (!/^(\\set ON_ERROR_STOP on\s+)?(BEGIN TRANSACTION READ ONLY|SET LOCAL statement_timeout = '30s'|SET LOCAL lock_timeout = '2s'|SET LOCAL row_security = off|WITH |ROLLBACK$)/.test(s)) {
      problems.push(`不允许的语句：${s.slice(0, 40)}`);
    }
  }
  const write = /\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|CALL|VACUUM|LOCK)\b/i.exec(code);
  if (write) problems.push(`出现写语句关键字 ${write[1]}`);
  if (/\bDO\s*\$/i.test(code)) problems.push("不得有 DO 块");
  if (/\\(?!set ON_ERROR_STOP)[a-z!]/.test(code)) problems.push("不得有其他 psql 元命令");
  if (/SELECT\s+\*/i.test(code)) problems.push("不得 SELECT *");
  const specRows = [...code.matchAll(/\('(rows|group|true|notnull)',\s*'([a-z_]+)',\s*(?:NULL|'([a-z_]+)')\s*\)/g)];
  const specTables = specRows.map((m) => m[2]!);
  for (const m of specRows) {
    const [, section, t, col] = m as unknown as [string, keyof typeof ALLOWED, string, string | undefined];
    const cols = ALLOWED[section][t];
    if (!cols || (col === undefined ? cols.length > 0 : !cols.includes(col))) problems.push(`不在列白名单：${section} ${t}${col ? `.${col}` : ""}`);
  }
  if (!/GROUP BY 1 ORDER BY 1 LIMIT 50\)/.test(code)) problems.push("分组查询必须限制在 50 组以内");
  const inList = [...(/c\.table_name IN \(([^)]*)\)/.exec(code)?.[1] ?? "").matchAll(/'([a-z_]+)'/g)].map((m) => m[1]!);
  if (specTables.length === 0 || inList.length === 0) problems.push("找不到表清单");
  for (const t of new Set([...specTables, ...inList])) if (!CONTENT_TABLES.includes(t)) problems.push(`不在白名单：${t}`);
  for (const t of STUDENT_TABLES) if (new RegExp(`\\b${t}\\b`).test(sql)) problems.push(`出现了学生表 ${t}`);
  return problems;
}
