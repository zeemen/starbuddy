// 星图读取（spec 第 1 节）：在 withAccount 事务里以当前账号身份只读查询，授权完全交给 RLS（can_read_student）。
// 学生不存在与无权查看同样返回 null（调用方回 404），不泄露学生是否存在。
import type { Pool } from 'pg';
import { withAccount } from '../metering/db.ts';
import { toDisplay, type Display, type MasteryState } from './display-state.ts';

export interface Star {
  skill: string;
  label_code: string;
  display: Display;
  retained: boolean;
  needs_verification: boolean;
}

export interface Sky {
  subject: string;
  enrollment_status: 'active' | 'paused';
  stars: Star[];
  summary: Record<Display, number>;
}

export interface StarMap {
  student_id: string;
  generated_at: string;
  skies: Sky[];
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const isUuid = (s: string): boolean => UUID_RE.test(s);

interface Row {
  subject: string;
  enrollment_status: 'active' | 'paused';
  local_id: string;
  label_code: string;
  state: MasteryState | null;
  confidence: string | null;
}

export async function getStarMap(pool: Pool, accountId: string, studentId: string, now: () => Date = () => new Date()): Promise<StarMap | null> {
  if (!isUuid(studentId) || !isUuid(accountId)) return null;
  return withAccount(pool, accountId, async (client) => {
    await client.query('SET TRANSACTION READ ONLY');
    // RLS 过滤：看不到就是“未找到”
    const seen = await client.query('SELECT 1 FROM platform.student_profile WHERE id = $1', [studentId]);
    if (seen.rowCount === 0) return null;
    // 只取在读的学科（待人确认 #7）；每个学科的全部已登记能力点（待人确认 #8）；
    // 掌握记录必须加 is_current：RLS 不按它过滤，不加会把已被取代的历史也画出来
    const { rows } = await client.query<Row>(
      `SELECT e.subject::text AS subject, e.status AS enrollment_status, n.local_id, n.label_code,
              m.state::text AS state, m.confidence::text AS confidence
         FROM platform.subject_enrollment e
         JOIN platform.skill_node n ON n.subject = e.subject
         LEFT JOIN platform.mastery_estimate m
                ON m.student_id = e.student_id AND m.subject = n.subject AND m.skill_local_id = n.local_id AND m.is_current
        WHERE e.student_id = $1 AND e.status = 'active'
        ORDER BY e.subject, n.local_id`,
      [studentId],
    );
    const skies = new Map<string, Sky>();
    for (const r of rows) {
      let sky = skies.get(r.subject);
      if (!sky) {
        sky = { subject: r.subject, enrollment_status: r.enrollment_status, stars: [], summary: { unlit: 0, half: 0, lit: 0 } };
        skies.set(r.subject, sky);
      }
      const d = toDisplay(r.state, r.confidence === null ? null : Number(r.confidence));
      sky.stars.push({ skill: `${r.subject}:${r.local_id}`, label_code: r.label_code, ...d });
      sky.summary[d.display] += 1;
    }
    return { student_id: studentId, generated_at: now().toISOString(), skies: [...skies.values()] };
  });
}

/**
 * 启动时核对连接角色（安全审查 S2）：授权完全交给 RLS，连接角色配错（超级用户、BYPASSRLS、表属主、
 * 同时继承别的应用角色）会让 RLS 整体失效。只允许“继承 starbuddy_bff、别的应用角色一个都不继承”的普通角色。
 */
export async function assertBffRole(pool: Pool): Promise<void> {
  const { rows } = await pool.query<{ ok: boolean; who: string }>(
    `SELECT current_user AS who,
            NOT r.rolsuper AND NOT r.rolbypassrls
            AND pg_has_role(current_user, 'starbuddy_bff', 'MEMBER')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_authz']) o(name)
                             WHERE pg_has_role(current_user, o.name, 'MEMBER'))
            AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = 'platform'::regnamespace AND c.relowner = r.oid) AS ok
       FROM pg_roles r WHERE r.rolname = current_user`,
  );
  if (!rows[0]?.ok) throw new Error(`BFF 连接角色 ${rows[0]?.who ?? '?'} 不合格：必须只继承 starbuddy_bff，不能是超级用户、BYPASSRLS 或表属主`);
}
