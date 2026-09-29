// 内容登记脚本（spec 设计要点 1；写法同 adapters/zhixing/scripts/register-content.ts）：把内容包里的课程版本、合成能力格（能力视图节点）、
// 客观题与作文题登记进 platform.curriculum_version / skill_node / item。证据表对题目有外键，不登记就写不进去。
//
// 只输出 SQL，不连接任何数据库（CLAUDE.md 规则 9）。目标库由执行人决定（pilot 与生产由人执行）：
//   node scripts/register-content.ts > /tmp/wenli-register.sql
//   psql -X -v ON_ERROR_STOP=1 -f /tmp/wenli-register.sql
//
// 只增不改：已登记的行 ON CONFLICT DO NOTHING。题目一律登记为 pending_review：准入由教研在审核台做（发布闸门），
// 不由本脚本做。量表与字数规则（rubric_version、rule_ref）只随判分结果落库（platform.grade_dimension），平台目前没有注册表，不在这里登记。
import { fileURLToPath } from "node:url";
import { loadPack, type Admission, type LoadedPack } from "../src/content.ts";

/** 登记时的准入状态：一律 pending_review；已被拒的保持 rejected（只会更严） */
export const registeredAdmission = (a: Admission): Admission => (a === "rejected" ? "rejected" : "pending_review");

const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;

export function registrationSql({ pack }: LoadedPack): string {
  const c = pack.curriculum;
  const cvWhere =
    `region = ${lit(c.region)} AND exam_year = ${c.exam_year} AND grade = ${c.grade} AND ` +
    `textbook_version = ${lit(c.textbook_version)} AND subject = 'wenli' AND content_version = ${lit(c.content_version)}`;
  const nodes = pack.cells.map((n) => `(${lit(n.id)})`).join(",\n  ");
  const items = [...pack.items, ...pack.essays]
    .map((it) => `(${lit(it.id)}, ${lit(it.cell)}, ${lit(registeredAdmission(it.admission))}::platform.admission)`)
    .join(",\n  ");
  return `-- 生成自 adapters/wenli/content/pack.json（${pack.pack_name}@${pack.pack_version}；合成内容、非真题）
-- ${pack.items.length} 道客观题、${pack.essays.length} 道作文题、${pack.cells.length} 个能力格。以 starbuddy_learning 身份写入；只增不改；题目一律 pending_review。
BEGIN;
SET LOCAL ROLE starbuddy_learning;

INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version)
VALUES (${lit(c.region)}, ${c.exam_year}, ${c.grade}, ${lit(c.textbook_version)}, 'wenli', ${lit(c.content_version)})
ON CONFLICT (region, exam_year, grade, textbook_version, subject, content_version) DO NOTHING;

INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code)
SELECT 'wenli', v.local_id, cv.id, v.local_id
  FROM (SELECT id FROM platform.curriculum_version WHERE ${cvWhere}) cv,
       (VALUES
  ${nodes}) AS v(local_id)
ON CONFLICT (subject, local_id) DO NOTHING;

INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, admitted_at)
SELECT 'wenli', v.item_id, cv.id, v.skill_local_id, v.admission, NULL
  FROM (SELECT id FROM platform.curriculum_version WHERE ${cvWhere}) cv,
       (VALUES
  ${items}) AS v(item_id, skill_local_id, admission)
ON CONFLICT (subject, item_id) DO NOTHING;

COMMIT;
`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.stdout.write(registrationSql(loadPack()));
}
