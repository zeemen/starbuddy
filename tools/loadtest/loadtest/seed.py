"""本机一次性库的准备（只对 starbuddy_test* 临时实例；规则 8、9）：

- 计量时钟：把 platform.meter_now() 换成“当天北京时间 10:00 起、随真实时间前进、可整体快进”的版本（做法同
  apps/web/scripts/e2e.sh 与 services/bff/tests/harness.ts），否则夜里压测会撞上 22:00–06:00 宵禁；压测后快进过预留有效期，
  以定时任务角色跑一次过期，再做零泄漏核对。只在测试库里替换，不进迁移；
- 额度配置：把草稿 v12-pilot@1 原样复制成已发布的 loadtest-plan@1（草稿本身不动，发布仍由人决定）；
- 合成家庭：每户一名家长（+999 未分配国家码的合成号码）、一名九年级学生、绑定、合成同意（synthetic-0 文本）、三科选科；
  订阅由家长经 platform.meter_open_subscription 开通（与生产路径相同）：每三户一户进试点名单（PRO 档），其余试用（基本档）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg

from .arrivals import sid

PLAN = "loadtest-plan@1"
SOURCE_PLAN = "v12-pilot@1"
SUBJECTS = ("aya", "wenli", "zhixing")

SETUP_SQL = f"""
DO $$
BEGIN
  IF current_database() !~ '^starbuddy_test' THEN
    RAISE EXCEPTION '压测准备只能用于一次性测试库（starbuddy_test*），当前库：%', current_database();
  END IF;
  -- 只接受本机 Unix socket 连接（一次性实例不监听 TCP）：经网络连上来的库一律拒绝（安全审查 L5，规则 9）
  IF inet_server_addr() IS NOT NULL THEN
    RAISE EXCEPTION '压测准备只能经本机 Unix socket 连接一次性实例';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS loadtest;
GRANT USAGE ON SCHEMA loadtest TO starbuddy_authz;
CREATE TABLE IF NOT EXISTS loadtest.clock (base timestamptz NOT NULL, started timestamptz NOT NULL, skew interval NOT NULL);
DELETE FROM loadtest.clock;
INSERT INTO loadtest.clock
  SELECT (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') + interval '10 hours') AT TIME ZONE 'Asia/Shanghai', now(), interval '0';
GRANT SELECT ON loadtest.clock TO starbuddy_authz;
CREATE OR REPLACE FUNCTION platform.meter_now() RETURNS timestamptz
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT base + (now() - started) + skew FROM loadtest.clock $f$;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN
    CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'loadtest_scheduler') THEN
    CREATE ROLE loadtest_scheduler LOGIN IN ROLE starbuddy_scheduler;
  END IF;
END $$;
INSERT INTO platform.plan_config (version, note_code)
  SELECT '{PLAN}', 'loadtest_copy_of_v12_pilot' WHERE NOT EXISTS (SELECT 1 FROM platform.plan_config WHERE version = '{PLAN}');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
  SELECT '{PLAN}', tier, resource_type, quantity, fair_use FROM platform.plan_allowance a
   WHERE config_version = '{SOURCE_PLAN}'
     AND NOT EXISTS (SELECT 1 FROM platform.plan_allowance b WHERE b.config_version = '{PLAN}');
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
  SELECT '{PLAN}', tier, feature_code, value_code FROM platform.plan_feature
   WHERE config_version = '{SOURCE_PLAN}'
     AND NOT EXISTS (SELECT 1 FROM platform.plan_feature b WHERE b.config_version = '{PLAN}');
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = '{PLAN}' AND status = 'draft';
-- 迁移 0039 起已发布的内容版本只能经发布闸门产生（且数学、道法要指向已发布地图）；压测只需要选科门控放行，
-- 在这个一次性测试库里以复制模式直接写入合成的已发布版本（只有超级用户能这样做）
SET session_replication_role = replica;
INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
  SELECT 'nanjing', 2027, 9, 'loadtest-2024', s::platform.subject, '0.1.0', 'published', now()
    FROM unnest(ARRAY['aya', 'wenli', 'zhixing']) s
   WHERE NOT EXISTS (SELECT 1 FROM platform.curriculum_version c WHERE c.subject = s::platform.subject AND c.status = 'published');
SET session_replication_role = origin;
"""


def setup_sql() -> str:
    return SETUP_SQL


METER_NOW_MS_SQL = "SELECT (extract(epoch FROM platform.meter_now()) * 1000)::bigint"


def skew_sql(interval: str) -> str:
    """整体快进计量时钟（只在测试库）"""
    if not interval.replace(" ", "").replace(":", "").isalnum():
        raise ValueError("interval must be a plain literal")
    return f"UPDATE loadtest.clock SET skew = skew + interval '{interval}';"


async def assert_local_test_db(pool: asyncpg.Pool) -> None:
    """同 SETUP_SQL 的护栏：库名 starbuddy_test*，且经 Unix socket（inet_server_addr() 为空）"""
    ok = await pool.fetchval("SELECT current_database() ~ '^starbuddy_test' AND inet_server_addr() IS NULL")
    if not ok:
        raise RuntimeError("压测只能写本机 Unix socket 上的一次性测试库（starbuddy_test*）")


@dataclass(frozen=True)
class Family:
    kind: str       # student | parent | meter：服务于哪类旅程
    slot: int
    guardian: str
    household: str
    student: str
    student_account: str
    phone: str
    pilot: bool


PHONE_PREFIX = {"student": "+99901", "parent": "+99902", "meter": "+99903"}


def family(seed: int, kind: str, slot: int) -> Family:
    tag = (seed, kind, slot)
    return Family(kind, slot, sid("guardian", *tag), sid("household", *tag), sid("student", *tag), sid("student-account", *tag),
                  f"{PHONE_PREFIX[kind]}{slot:07d}", slot % 3 == 2)


async def seed_families(admin: asyncpg.Pool, bff: asyncpg.Pool, families: list[Family]) -> dict[str, int]:
    """超级用户写账号侧准备数据（非学习数据）；订阅由家长以 BFF 角色经计量函数开通"""
    if not families:
        return {"families": 0}
    await assert_local_test_db(admin)
    async with admin.acquire() as c, c.transaction():
        await c.executemany("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')",
                            [(f.guardian, f.student_account) for f in families])
        await c.executemany("INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES ($1::uuid, $2, now())",
                            [(f.guardian, f.phone) for f in families])
        await c.executemany("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)",
                            [(f.household, f.guardian) for f in families])
        await c.executemany(
            """INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
               VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'bubian-2024', '2012-03-01')""",
            [(f.student, f.student_account, f.household) for f in families])
        await c.executemany("""INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                               VALUES ($1::uuid, $2::uuid, 'household_creator')""", [(f.guardian, f.student) for f in families])
        await c.executemany(
            """INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
               VALUES ($1::uuid, $2::uuid, 'privacy_policy', 'privacy-policy@synthetic-0', 'granted'),
                      ($1::uuid, $2::uuid, 'guardian_consent_under14', 'guardian-consent@synthetic-0', 'granted')""",
            [(f.student, f.guardian) for f in families])
        await c.executemany("INSERT INTO platform.subject_enrollment (student_id, subject) VALUES ($1::uuid, $2::platform.subject)",
                            [(f.student, s) for f in families for s in SUBJECTS])
        await c.executemany("INSERT INTO platform.pilot_household (household_id, cohort_code) VALUES ($1::uuid, 'loadtest')",
                            [(f.household,) for f in families if f.pilot])
    outcomes: dict[str, int] = {}
    async with bff.acquire() as c:
        for f in families:
            async with c.transaction():
                await c.execute("SELECT set_config('app.account_id', $1, true)", f.guardian)
                r = json.loads(await c.fetchval("SELECT platform.meter_open_subscription($1::uuid, $2)",
                                                f.student, "pilot_virtual" if f.pilot else "trial"))
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    if set(outcomes) != {"opened"}:
        raise RuntimeError(f"订阅没有全部开通：{outcomes}")
    return {"families": len(families), "pilot": sum(f.pilot for f in families)}
