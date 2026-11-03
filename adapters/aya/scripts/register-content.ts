// 内容登记脚本（spec 范围第 4 条；待人确认 #5）：把内容包里的课程版本、能力点、题目登记进
// platform.curriculum_version / skill_node / item。证据表对题目有外键，不登记就写不进去。
//
// 只输出 SQL，不连接任何数据库（CLAUDE.md 规则 9）。执行方：开发库由 Claude Code 执行；pilot 与生产由人执行。
//   node scripts/register-content.ts > /tmp/aya-register.sql
//   psql -X -v ON_ERROR_STOP=1 -f /tmp/aya-register.sql    # 目标库由执行人决定
//
// 只增不改：已登记的行 ON CONFLICT DO NOTHING；内容版本已发布或已撤下时不再往里追加（4-G 迁移 0039：登记只能追加到草稿，
// 否则保护触发器会在冲突判断之前拒绝，重跑登记就会失败）。准入状态的升级（例如来源核实后改为 admitted）属于
// 发布闸门，不由本脚本做：新题最高登记为 pending_review。准入降级（例如改为 rejected）同样不由本脚本同步，
// 要走发布流程（lessons 待人确认）。
import { fileURLToPath } from "node:url";
import { loadPack, type Admission, type LoadedPack } from "../src/content.ts";

/** 登记时的准入状态：内容包说 admitted 也只登记为 pending_review（准入升级属于发布闸门） */
export const registeredAdmission = (a: Admission): Admission => (a === "admitted" ? "pending_review" : a);

const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;

export function registrationSql({ pack }: LoadedPack): string {
  const c = pack.curriculum;
  const cvWhere =
    `region = ${lit(c.region)} AND exam_year = ${c.exam_year} AND grade = ${c.grade} AND ` +
    `textbook_version = ${lit(c.textbook_version)} AND subject = 'aya' AND content_version = ${lit(c.content_version)}`;
  const nodes = pack.nodes.map((n) => `(${lit(n.id)})`).join(",\n  ");
  const items = pack.items
    // 发布闸门：登记时最高只到 pending_review，admitted 只能由发布流程升级
    .map((it) => `(${lit(it.id)}, ${lit(it.mainNodes[0]!)}, ${lit(registeredAdmission(it.admission))}::platform.admission)`)
    .join(",\n  ");
  return `-- 生成自 adapters/aya/content/pack.json（${pack.pack_name}@${pack.pack_version}，legacy ${pack.legacy_commit}）
-- ${pack.items.length} 道题、${pack.nodes.length} 个能力点。以 starbuddy_learning 身份写入；只增不改。
BEGIN;
SET LOCAL ROLE starbuddy_learning;

INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version)
VALUES (${lit(c.region)}, ${c.exam_year}, ${c.grade}, ${lit(c.textbook_version)}, 'aya', ${lit(c.content_version)})
ON CONFLICT (region, exam_year, grade, textbook_version, subject, content_version) DO NOTHING;

INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code)
SELECT 'aya', v.local_id, cv.id, v.local_id
  FROM (SELECT id FROM platform.curriculum_version WHERE ${cvWhere} AND status = 'draft') cv,
       (VALUES
  ${nodes}) AS v(local_id)
ON CONFLICT (subject, local_id) DO NOTHING;

INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, admitted_at)
SELECT 'aya', v.item_id, cv.id, v.skill_local_id, v.admission, NULL
  FROM (SELECT id FROM platform.curriculum_version WHERE ${cvWhere} AND status = 'draft') cv,
       (VALUES
  ${items}) AS v(item_id, skill_local_id, admission)
ON CONFLICT (subject, item_id) DO NOTHING;

COMMIT;
`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.stdout.write(registrationSql(loadPack()));
}
