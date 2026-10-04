"""一次性的本地 Postgres（做法同 services/knowledge/tests/pgkge.py 与 services/learning/tests/pgharness.py）：Unix socket、
不监听 TCP、结束即删除；执行角色 bootstrap 与全部 up 迁移（含 0010 判断账本、0028 洞察服务只读授权），再灌合成数据。
不连接任何共享或生产库（规则 9）。

合成数据（规则 8）：学生、家长、题目、证据、掌握行都是占位编码。
- S1（家长 gA 绑定）：6 条当前掌握行 M1–M6（skill_local_id 占位；节点 × 层次由测试的合成 MasteryRow 给出，KGE-04 第二步前不猜新列）、
  1 条已被取代的掌握行 M_OLD、12 条 A 级证据 E01–E12；
- S2（家长 gB 绑定）：1 条掌握行 M_S2（跨学生依据用）；
- S3：中圈指标用的作答、判分（含一条被取代的判分）、探针、计划与会话事件、错因（含一条被取代的错因），数值见 tests/test_db_metrics.py 的手算；
- 模板：合成占位模板（以超级用户身份模拟人工登记；应用角色都不能写模板）——知识掌握、知识迁移、中圈指标三类已审核，
  另有草稿与已退役各一条。文本模板是明显的占位，不是真实文案（必停项）。
平台事实行以超级用户在 session_replication_role = replica 下直接插入（与 pgkge 造夹具的做法相同），只为构造夹具。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROLE = "insight_app"            # 只继承 starbuddy_insight（正例）
BYPASS_ROLE = "insight_bypass"      # 继承 insight 但有 BYPASSRLS（反例）
MULTI_ROLE = "insight_multi"        # 同时继承 insight 与 learning（反例）
OTHER_ROLE = "insight_other"        # 不继承 insight（反例）
PREDEF_ROLE = "insight_predef"      # 继承 insight 且是 pg_read_all_data 成员（反例）
CREATEDB_ROLE = "insight_createdb"  # 继承 insight 但有 CREATEDB（反例）
BFF_ROLE = "bff_app"                # 只继承 starbuddy_bff
LEARNING_ROLE = "learning_app"      # 只继承 starbuddy_learning

GA, GB = "a0000000-0000-4000-8000-00000000000a", "b0000000-0000-4000-8000-00000000000b"
SA1, SA2, SA3 = "a1000000-0000-4000-8000-0000000000a1", "b1000000-0000-4000-8000-0000000000b1", "a3000000-0000-4000-8000-0000000000a3"
S1, S2, S3 = "c1000000-0000-4000-8000-0000000000c1", "c2000000-0000-4000-8000-0000000000c2", "c3000000-0000-4000-8000-0000000000c3"
LINK_A = "11000000-0000-4000-8000-0000000000a1"
M = [f"6d000000-0000-4000-8000-0000000000{i:02d}" for i in range(1, 7)]
M_OLD = "6d000000-0000-4000-8000-0000000000f0"
M_S2 = "6d000000-0000-4000-8000-000000000020"
E = [f"e1000000-0000-4000-8000-0000000000{i:02d}" for i in range(1, 13)]
TPL_MASTERY, TPL_TRANSFER, TPL_METRIC = "syn.math.node_mastery", "syn.math.node_transfer", "syn.metric.middle_circle"
TPL_DRAFT, TPL_RETIRED = "syn.math.draft", "syn.math.retired"
TEXT_MASTERY = "【占位模板·未审核】{node_id}｜{level}｜{gate}"
TEXT_TRANSFER = "【占位模板·未审核】迁移｜{node_id}｜{level}"

_SRC = "'SYN-SRC-INSIGHT', 'self_authored', 'cleared'"  # 已准入的题须来源已核（迁移 0039 的约束）
_items = ",\n".join(f"('aya', 'syn-item-{i:03d}', 'e0000000-0000-4000-8000-0000000000e0', 'admitted', now(), {_SRC})" for i in range(1, 41))
_witems = ",\n".join(f"('wenli', 'syn-w-item-{i:02d}', 'e9000000-0000-4000-8000-0000000000e9', 'admitted', now(), {_SRC})" for i in range(1, 11))
_evidence = ",\n".join(
    f"('{E[i]}', now() - interval '{20 - i} days', '{S1}', 'aya', 'response_text', 'syn-item-{i + 1:03d}', 'admitted', "
    f"'{{\"text\": \"syn\"}}', 'grade', gen_random_uuid(), 'synthetic-insight-{i:04d}', 'independent', 'A', 'keypad', 'objective')"
    for i in range(12))

SEED_SQL = f"""
INSERT INTO platform.account (id, kind) VALUES ('{GA}', 'guardian'), ('{GB}', 'guardian'), ('{SA1}', 'student'), ('{SA2}', 'student'),
  ('{SA3}', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('d1000000-0000-4000-8000-0000000000d1', '{GA}'), ('d2000000-0000-4000-8000-0000000000d2', '{GB}'),
  ('d3000000-0000-4000-8000-0000000000d3', '{GA}');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('{S1}', '{SA1}', 'd1000000-0000-4000-8000-0000000000d1', 8, 'nanjing', 'bubian-2024', '2013-03-01'),
  ('{S2}', '{SA2}', 'd2000000-0000-4000-8000-0000000000d2', 8, 'nanjing', 'bubian-2024', '2013-05-01'),
  ('{S3}', '{SA3}', 'd3000000-0000-4000-8000-0000000000d3', 8, 'nanjing', 'bubian-2024', '2013-07-01');
INSERT INTO platform.guardian_student_link (id, guardian_account_id, student_id, granted_via) VALUES
  ('{LINK_A}', '{GA}', '{S1}', 'household_creator');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ('{GB}', '{S2}', 'household_creator');
-- 迁移 0039 起已发布的内容版本与已准入的题只能经发布闸门产生；洞察服务测试只要它们存在，以复制模式直接写入（同下面的证据）
SET session_replication_role = replica;
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
VALUES ('e0000000-0000-4000-8000-0000000000e0', 'nanjing', 2027, 8, 'bubian-2024', 'aya', '0.1.0', 'published', now()),
       ('e9000000-0000-4000-8000-0000000000e9', 'nanjing', 2027, 8, 'bubian-2024', 'wenli', '0.1.0', 'published', now());
INSERT INTO platform.item (subject, item_id, curriculum_version_id, admission, admitted_at, source_ref, source_type, license_status) VALUES
{_items},
{_witems};
SET session_replication_role = origin;
SELECT platform.ensure_monthly_partitions('platform.evidence_event'::regclass, (now() - interval '200 days')::date, 9);
SELECT platform.ensure_monthly_partitions('platform.attempt'::regclass, (now() - interval '200 days')::date, 9);
SET session_replication_role = replica;

INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, payload, source_method,
  request_id, request_idempotency_key, evidence_kind, evidence_level, channel, source_type) VALUES
{_evidence};

INSERT INTO platform.mastery_estimate (id, student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version,
  evidence_ids, is_current, superseded_by, superseded_at) VALUES
  ('{M[0]}', '{S1}', 'aya', 'SYN-M-01', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn', '{{{E[0]},{E[1]}}}', true, NULL, NULL),
  ('{M[1]}', '{S1}', 'aya', 'SYN-M-02', 'stable', 0.8, 0.8, 'kge-mastery@0.1.0.syn', '{{{E[2]},{E[3]},{E[4]}}}', true, NULL, NULL),
  ('{M[2]}', '{S1}', 'aya', 'SYN-M-03', 'transfer', 0.9, 0.9, 'kge-mastery@0.1.0.syn', '{{{E[5]},{E[6]}}}', true, NULL, NULL),
  ('{M[3]}', '{S1}', 'wenli', 'SYN-M-04', 'stable', 0.8, 0.8, 'kge-mastery@0.1.0.syn', '{{{E[7]}}}', true, NULL, NULL),
  ('{M[4]}', '{S1}', 'aya', 'SYN-M-05', 'understand', 0.4, 0.3, 'kge-mastery@0.1.0.syn', '{{{E[8]}}}', true, NULL, NULL),
  ('{M[5]}', '{S1}', 'aya', 'SYN-M-06', 'stable', 0.8, 0.8, 'kge-mastery@0.1.0.syn', '{{{E[9]},{E[10]},{E[11]}}}', true, NULL, NULL),
  ('{M_OLD}', '{S1}', 'aya', 'SYN-M-06', 'execute', 0.6, 0.6, 'kge-mastery@0.1.0.syn', '{{{E[9]}}}', false, '{M[5]}', now()),
  ('{M_S2}', '{S2}', 'aya', 'SYN-M-01', 'stable', 0.8, 0.8, 'kge-mastery@0.1.0.syn', '{{{E[0]}}}', true, NULL, NULL);

-- S3：中圈指标
CREATE TEMP TABLE s3_attempt (n int, item text, hint int, kind text, verdict text, at timestamptz);
INSERT INTO s3_attempt
  SELECT i, format('syn-item-%s', lpad(i::text, 3, '0')), 0, 'independent', CASE WHEN i <= 9 THEN 'correct' ELSE 'incorrect' END,
         now() - interval '1 day' - i * interval '1 minute' FROM generate_series(1, 12) i
  UNION ALL SELECT i, format('syn-item-%s', lpad(i::text, 3, '0')), (ARRAY[2, 3, 4, 6])[i - 12], 'hinted', 'correct',
         now() - interval '1 day' - i * interval '1 minute' FROM generate_series(13, 16) i
  UNION ALL SELECT i, format('syn-item-%s', lpad(i::text, 3, '0')), 0, 'transfer', CASE WHEN i <= 22 THEN 'correct' ELSE 'incorrect' END,
         now() - interval '20 days' - i * interval '1 minute' FROM generate_series(17, 26) i
  UNION ALL SELECT i, format('syn-item-%s', lpad(i::text, 3, '0')), 0, 'delayed_retest', CASE WHEN i <= 33 THEN 'correct' ELSE 'incorrect' END,
         now() - interval '35 days' - i * interval '1 minute' FROM generate_series(27, 36) i;
INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, hint_level_used,
  request_idempotency_key, evidence_kind, evidence_level, channel)
  SELECT ('a3000000-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid, at, '{S3}', 'aya', item, gen_random_uuid(), hint,
         format('synthetic-s3-attempt-%s', lpad(n::text, 4, '0')), kind, 'A', 'keypad' FROM s3_attempt;
INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
  calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
  SELECT '{S3}', 'aya', ('a3000000-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid, at, verdict, 'aya-symbolic-ts@0.1.0',
         CASE WHEN verdict = 'correct' THEN 1 ELSE 0 END, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0',
         false, 'formal', ARRAY[gen_random_uuid()], gen_random_uuid() FROM s3_attempt;
-- 第 1 题有一条已被取代的判分（incorrect，写入时间故意更晚）：指标只认 superseded_by 为空的当前判分，不靠时间先后
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
  calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id,
  superseded_by, superseded_at, created_at)
  SELECT gen_random_uuid(), '{S3}', 'aya', ('a3000000-0000-4000-8000-' || lpad('1', 12, '0'))::uuid, at, 'incorrect', 'aya-symbolic-ts@0.1.0',
         0, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', false, 'formal', ARRAY[gen_random_uuid()],
         gen_random_uuid(), (SELECT id FROM platform.grade_result WHERE attempt_id = ('a3000000-0000-4000-8000-' || lpad('1', 12, '0'))::uuid),
         now(), now() + interval '1 minute' FROM s3_attempt WHERE n = 1;
-- 探针：关联前 20 次作答（1–16 近期、17–20 迁移题）；1–10 说“能”，11–15 说“不能”，16–20 说“不确定”
INSERT INTO insight.confidence_probe (student_id, subject, item_id, prediction, asked_at, attempt_id, idempotency_key)
  SELECT '{S3}', 'aya', item, CASE WHEN n <= 10 THEN 'yes' WHEN n <= 15 THEN 'no' ELSE 'unsure' END, at - interval '1 minute',
         ('a3000000-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid, gen_random_uuid() FROM s3_attempt WHERE n <= 20;
-- 一条没有关联作答的探针（不进校准）
INSERT INTO insight.confidence_probe (student_id, subject, item_id, prediction, asked_at, idempotency_key)
  VALUES ('{S3}', 'aya', 'syn-item-040', 'yes', now() - interval '1 hour', gen_random_uuid());
-- 计划：T1 完成、T2 跳过、T3 先跳过后完成、T4 完成、T5 跳过 → 3 / 5
INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, recorded_by, idempotency_key) VALUES
  ('{S3}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-000000000001"}}', now() - interval '20 days', 'student', gen_random_uuid()),
  ('{S3}', 'plan_skip', '{{"plan_task_id": "70000000-0000-4000-8000-000000000002"}}', now() - interval '18 days', 'student', gen_random_uuid()),
  ('{S3}', 'plan_skip', '{{"plan_task_id": "70000000-0000-4000-8000-000000000003"}}', now() - interval '10 days', 'student', gen_random_uuid()),
  ('{S3}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-000000000003"}}', now() - interval '9 days', 'student', gen_random_uuid()),
  ('{S3}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-000000000004"}}', now() - interval '3 days', 'student', gen_random_uuid()),
  ('{S3}', 'plan_skip', '{{"plan_task_id": "70000000-0000-4000-8000-000000000005"}}', now() - interval '2 days', 'student', gen_random_uuid()),
  ('{S3}', 'session_start', '{{"entry": "today"}}', now() - interval '5 days', 'student', gen_random_uuid()),
  ('{S3}', 'session_start', '{{"entry": "today"}}', now() - interval '13 days', 'student', gen_random_uuid()),
  ('{S3}', 'session_start', '{{"entry": "star_map"}}', now() - interval '27 days', 'student', gen_random_uuid());
-- 错因：数学 5 条（2 条审题）、文砺 5 条（1 条审题），另有一条已被取代的数学审题错因（不计）→ 3 / 10
INSERT INTO platform.error_attribution (student_id, subject, subject_category, cross_subject_category, confidence, algorithm_version,
  evidence_ids, source_request_id)
  SELECT '{S3}', s::platform.subject, 'syn-category', c::platform.cross_subject_error, 0.8, 'syn-attr@0.1.0', ARRAY[gen_random_uuid()], gen_random_uuid()
    FROM (VALUES ('aya', 'reading_the_question'), ('aya', 'reading_the_question'), ('aya', 'knowledge'), ('aya', 'thinking_method'),
                 ('aya', 'computation_or_grammar'), ('wenli', 'reading_the_question'), ('wenli', 'expression_convention'),
                 ('wenli', 'knowledge'), ('wenli', 'knowledge'), ('wenli', 'strategy_and_time')) v(s, c);
INSERT INTO platform.error_attribution (student_id, subject, subject_category, cross_subject_category, confidence, algorithm_version,
  evidence_ids, source_request_id, superseded_by, superseded_at)
  SELECT '{S3}', 'aya', 'syn-category', 'reading_the_question', 0.8, 'syn-attr@0.1.0', ARRAY[gen_random_uuid()], gen_random_uuid(),
         (SELECT id FROM platform.error_attribution WHERE student_id = '{S3}' AND subject = 'aya' AND cross_subject_category = 'knowledge' LIMIT 1),
         now();
SET session_replication_role = origin;

-- 合成占位模板（模拟人工登记；文本是明显占位，不是真实文案）
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at) VALUES
  ('{TPL_MASTERY}', 1, 'node', '{{agent,student,guardian}}', '60 days', '{TEXT_MASTERY}', 'approved', 'test-reviewer', now()),
  ('{TPL_TRANSFER}', 1, 'node', '{{agent,student,guardian}}', '60 days', '{TEXT_TRANSFER}', 'approved', 'test-reviewer', now()),
  ('{TPL_METRIC}', 1, 'metric', '{{agent}}', '30 days', NULL, 'approved', 'test-reviewer', now()),
  ('{TPL_DRAFT}', 1, 'node', '{{agent,student}}', '30 days', '【占位模板·草稿】{{node_id}}', 'draft', NULL, NULL),
  ('{TPL_RETIRED}', 1, 'node', '{{agent,student,guardian}}', '30 days', '【占位模板·将退役】{{node_id}}', 'approved', 'test-reviewer', now());

CREATE ROLE {APP_ROLE} LOGIN IN ROLE starbuddy_insight;
CREATE ROLE {BYPASS_ROLE} LOGIN BYPASSRLS IN ROLE starbuddy_insight;
CREATE ROLE {MULTI_ROLE} LOGIN IN ROLE starbuddy_insight, starbuddy_learning;
CREATE ROLE {OTHER_ROLE} LOGIN;
CREATE ROLE {PREDEF_ROLE} LOGIN IN ROLE starbuddy_insight, pg_read_all_data;
CREATE ROLE {CREATEDB_ROLE} LOGIN CREATEDB IN ROLE starbuddy_insight;
CREATE ROLE {BFF_ROLE} LOGIN IN ROLE starbuddy_bff;
CREATE ROLE {LEARNING_ROLE} LOGIN IN ROLE starbuddy_learning;
"""


def find_pgbin() -> str | None:
    if os.environ.get("PGBIN"):
        return os.environ["PGBIN"]
    if os.path.exists("/usr/lib/postgresql/16/bin/initdb"):
        return "/usr/lib/postgresql/16/bin"
    found = sorted(glob.glob("/usr/lib/postgresql/*/bin"))
    return found[-1] if found else None


class EphemeralPG:
    def __init__(self) -> None:
        pgbin = find_pgbin()
        if not pgbin or not os.path.exists(f"{pgbin}/initdb"):
            raise FileNotFoundError("本机没有 Postgres 服务端（需要 postgresql-16）")
        self.pgbin = pgbin
        self.root = os.geteuid() == 0
        self.work = tempfile.mkdtemp(prefix="starbuddy-insight-test.")
        if self.root:
            shutil.chown(self.work, "postgres")
        os.chmod(self.work, 0o700)
        self.db = "insight_test"

    def _as_pg(self, *args: str, **kw) -> subprocess.CompletedProcess:
        cmd = (["runuser", "-u", "postgres", "--"] if self.root else []) + list(args)
        return subprocess.run(cmd, check=True, capture_output=True, **kw)

    def start(self) -> None:
        self._as_pg(f"{self.pgbin}/initdb", "-D", f"{self.work}/data", "-A", "trust", "-U", "postgres", "-E", "UTF8", "--no-sync")
        self._as_pg(f"{self.pgbin}/pg_ctl", "-D", f"{self.work}/data", "-w", "-l", f"{self.work}/pg.log",
                    "-o", f"-k {self.work} -c listen_addresses='' -c fsync=off -c timezone=UTC -c max_connections=100", "start")
        self._as_pg(f"{self.pgbin}/createdb", "-h", self.work, "-U", "postgres", self.db)
        self.psql((ROOT / "db/bootstrap/roles.sql").read_text(encoding="utf-8"))
        for f in sorted((ROOT / "db/migrations").glob("*.up.sql")):
            self.psql(f.read_text(encoding="utf-8"))
        self.psql(SEED_SQL)

    def psql(self, sql: str) -> str:
        r = self._as_pg(f"{self.pgbin}/psql", "-h", self.work, "-U", "postgres", "-d", self.db, "-X", "-q", "-tA",
                        "-v", "ON_ERROR_STOP=1", "-1", input=sql.encode())
        return r.stdout.decode()

    def connect_kwargs(self, user: str) -> dict:
        return {"host": self.work, "user": user, "database": self.db}

    def stop(self) -> None:
        try:
            self._as_pg(f"{self.pgbin}/pg_ctl", "-D", f"{self.work}/data", "-m", "immediate", "stop")
        except subprocess.CalledProcessError:
            pass
        shutil.rmtree(self.work, ignore_errors=True)
