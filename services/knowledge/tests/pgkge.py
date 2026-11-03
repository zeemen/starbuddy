"""一次性的本地 Postgres（做法同 services/learning/tests/pgharness.py 与 db/tests/pg-ephemeral.sh）：Unix socket、
不监听 TCP、结束即删除；执行角色 bootstrap 与全部 up 迁移，再灌合成 kge 数据。不连接任何共享或生产库（规则 9）。

合成数据（规则 8）：节点 ID、教材版次、地区、题目都是占位编码。
- 地图版本：VR 数学 0.8.0 已退役、VD 数学 0.10.0 草稿、VP 数学 0.9.0 已发布（按这个顺序插入：堆顺序上草稿与退役在前，
  不带发布条件的查询会先读到它们，便于发现“放过草稿版本”）；
- VP 里：有效节点、已停用节点、强 / 弱 / 已停用前置边、已核对 / 未核对 / 有争议的投影、审核通过 / 待审 / 驳回的标签；
- VD、VR 里：只在草稿或退役版本出现的节点、投影、标签、采分点；
- 一个包含超长 includes 的节点（BAD）：数据库允许，但不合契约（NodeView.includes 每条 ≤ 200 字），用来证明响应校验在起作用。
kge 的行以超级用户在 session_replication_role = replica 下直接插入（与 db/tests/79h_kge_reader.sql 造可见性夹具的做法相同），
只是为了构造可见性夹具，不走发布与签字流程。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROLE = "knowledge_app"          # 只继承 starbuddy_kge_read（正例）
BYPASS_ROLE = "knowledge_bypass"    # 继承 kge_read 但有 BYPASSRLS（反例）
WRITER_ROLE = "knowledge_writer"    # 同时继承 kge_read 与 starbuddy_kge（反例）
OTHER_ROLE = "knowledge_other"      # 不继承 kge_read（反例）
PREDEF_ROLE = "knowledge_predef"    # 继承 kge_read 且是 pg_read_all_data 成员（反例）
CREATEDB_ROLE = "knowledge_createdb"  # 继承 kge_read 但有 CREATEDB（反例）
CREATEROLE_ROLE = "knowledge_createrole"  # 继承 kge_read 但有 CREATEROLE（反例）

VR = "c0000000-0000-4000-8000-0000000000a8"
VD = "c0000000-0000-4000-8000-0000000000aa"
VP = "c0000000-0000-4000-8000-0000000000a9"
CV = "c0000000-0000-4000-8000-0000000000c1"
FAM = "c0000000-0000-4000-8000-0000000000f1"
FAM_D = "c0000000-0000-4000-8000-0000000000f2"

TRANSPOSE, LINEAR, APPLY = "MATH.SYN.EQ.TRANSPOSE", "MATH.SYN.EQ.LINEAR", "MATH.SYN.EQ.APPLY"
WEAK, OLD, BAD = "MATH.SYN.EQ.WEAK_PRE", "MATH.SYN.EQ.OLD_TOPIC", "MATH.SYN.EQ.BAD_ROW"
DRAFT_ONLY, RETIRED_ONLY = "MATH.SYN.EQ.DRAFT_ONLY", "MATH.SYN.EQ.RETIRED_ONLY"
QUOTE_A = "合成教材原句甲：只含有一个未知数并且次数是一的方程叫作示例方程。"
QUOTE_B = "合成教材原句乙：含一个未知数、未知数次数为一的整式方程称为示例方程。"
QUOTE_T = "合成教材原句甲：把某项变号后移到另一边叫作示例移项。"
UNVERIFIED_QUOTE = "合成未核对原句：不应被任何工具返回。"
DISPUTED_QUOTE = "合成有争议原句：不应被任何工具返回。"
DRAFT_QUOTE = "合成草稿版本原句：不应被任何工具返回。"
DRAFT_NAME = "合成草稿版本名称"
FORBIDDEN_TEXT = (UNVERIFIED_QUOTE, DISPUTED_QUOTE, DRAFT_QUOTE, DRAFT_NAME, DRAFT_ONLY, RETIRED_ONLY, OLD, "SYN-I4", "SYN-I5",
                  "syn-rubric@0.2.0", "合成退役版本名称", "0.99")

SEED_SQL = f"""
-- 迁移 0039 起已发布的内容版本与已准入的题只能经发布闸门产生；知识服务测试只要它们存在，以复制模式直接写入（同下面的地图），
-- 已准入的题仍须来源已核（约束不随复制模式跳过）
SET session_replication_role = replica;
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
VALUES ('{CV}', 'syn-region', 2027, 7, 'syn-ed-a', 'aya', '0.9.0', 'published', now());
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('aya', 'K-SYN-EQ', '{CV}', 'K-SYN-EQ');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, admitted_at, source_ref, source_type, license_status)
SELECT 'aya', 'SYN-I' || i, '{CV}', 'K-SYN-EQ', 'admitted', now(), 'SYN-SRC-KGE', 'self_authored', 'cleared' FROM generate_series(1, 6) i;

INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES
  ('{VR}', 'aya', 2022, '0.8.0', 'retired', now() - interval '30 days'),
  ('{VD}', 'aya', 2022, '0.10.0', 'draft', NULL),
  ('{VP}', 'aya', 2022, '0.9.0', 'published', now());
INSERT INTO kge.curriculum_item (subject, standard_year, code, domain, text, verb_level, source_ref) VALUES
  ('aya', 2022, 'SYN-EQ-01', '合成领域', '合成课标条目：理解一元一次方程的概念。', 'understand', 'synthetic');

-- 草稿与退役版本（先插入）
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, cognitive_levels) VALUES
  ('{VR}', 'MATH.SYN', 'domain', NULL, NULL, '合成领域', '{{}}', '{{}}', '{{}}'),
  ('{VR}', 'MATH.SYN.EQ', 'topic', 'MATH.SYN', NULL, '合成方程单元', '{{}}', '{{}}', '{{}}'),
  ('{VR}', '{LINEAR}', 'node', 'MATH.SYN.EQ', 'kp', '合成退役版本名称', '{{}}', '{{}}', '{{understand}}'),
  ('{VR}', '{RETIRED_ONLY}', 'node', 'MATH.SYN.EQ', 'kp', '合成退役节点', '{{}}', '{{}}', '{{}}'),
  ('{VD}', 'MATH.SYN', 'domain', NULL, NULL, '合成领域', '{{}}', '{{}}', '{{}}'),
  ('{VD}', 'MATH.SYN.EQ', 'topic', 'MATH.SYN', NULL, '合成方程单元', '{{}}', '{{}}', '{{}}'),
  ('{VD}', '{LINEAR}', 'node', 'MATH.SYN.EQ', 'kp', '{DRAFT_NAME}', '{{合成草稿边界}}', '{{}}', '{{understand}}'),
  ('{VD}', '{DRAFT_ONLY}', 'node', 'MATH.SYN.EQ', 'kp', '合成草稿节点', '{{}}', '{{}}', '{{}}');
INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type, strength, status) VALUES
  ('{VD}', '{DRAFT_ONLY}', '{VD}', '{LINEAR}', 'prerequisite', 'strong', 'active'),
  ('{VR}', '{RETIRED_ONLY}', '{VR}', '{LINEAR}', 'prerequisite', 'strong', 'active');
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, quote_text,
                                     verified_by, verified_at, mapping_state) VALUES
  ('{VD}', '{LINEAR}', 'syn-ed-a', 2024, '合成七上', '合成第三章', 80, 82, '{DRAFT_QUOTE}', 'syn-staff-01', now(), 'verified');
INSERT INTO kge.region_projection (map_version_id, node_id, region, exam_year, weight, typical_slots, source_ref) VALUES
  ('{VD}', '{LINEAR}', 'syn-region', 2025, 0.99, '{{草稿题位}}', 'synthetic');
INSERT INTO kge.item_family (id, map_version_id, main_node_id, code) VALUES ('{FAM_D}', '{VD}', '{LINEAR}', 'syn-fam-draft');
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, prior_difficulty, family_id,
                          source_kind, license_state, tag_review_status) VALUES
  ('{VD}', 'aya', 'SYN-I1', '{LINEAR}', '{{}}', 'apply', 0.99, '{FAM_D}', 'original', 'original', 'approved'),
  ('{VD}', 'aya', 'SYN-I6', '{DRAFT_ONLY}', '{{}}', 'apply', 0.5, NULL, 'original', 'original', 'approved');
INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids) VALUES
  ('{VD}', 'aya', 'SYN-I1', 'syn-rubric@0.2.0', 'draft-point', 3, '{{{LINEAR}}}');

-- 已发布版本 VP
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, cognitive_levels, status) VALUES
  ('{VP}', 'MATH.SYN', 'domain', NULL, NULL, '合成领域', '{{}}', '{{}}', '{{}}', 'active'),
  ('{VP}', 'MATH.SYN.EQ', 'topic', 'MATH.SYN', NULL, '合成方程单元', '{{}}', '{{}}', '{{}}', 'active'),
  ('{VP}', '{TRANSPOSE}', 'node', 'MATH.SYN.EQ', 'kp', '移项', '{{把等式一边的项改变符号后移到另一边}}', '{{分式方程的去分母}}', '{{understand,apply}}', 'active'),
  ('{VP}', '{LINEAR}', 'node', 'MATH.SYN.EQ', 'kp', '一元一次方程', '{{只含一个未知数且未知数的次数是一的整式方程}}', '{{二元一次方程组}}', '{{understand,apply}}', 'active'),
  ('{VP}', '{APPLY}', 'node', 'MATH.SYN.EQ', 'kp', '一元一次方程的应用', '{{列一元一次方程解决简单实际问题}}', '{{}}', '{{apply}}', 'active'),
  ('{VP}', '{WEAK}', 'node', 'MATH.SYN.EQ', 'kp', '合成弱前置节点', '{{}}', '{{}}', '{{}}', 'active'),
  ('{VP}', '{OLD}', 'node', 'MATH.SYN.EQ', 'kp', '已撤下的合成节点', '{{}}', '{{}}', '{{}}', 'deprecated'),
  ('{VP}', '{BAD}', 'node', 'MATH.SYN.EQ', 'kp', '合成超长边界节点', ARRAY[repeat('长', 201)], '{{}}', '{{}}', 'active');
INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type, strength, status) VALUES
  ('{VP}', '{TRANSPOSE}', '{VP}', '{LINEAR}', 'prerequisite', 'strong', 'active'),
  ('{VP}', '{LINEAR}', '{VP}', '{APPLY}', 'prerequisite', 'strong', 'active'),
  ('{VP}', '{WEAK}', '{VP}', '{LINEAR}', 'prerequisite', 'weak', 'active'),
  ('{VP}', '{OLD}', '{VP}', '{LINEAR}', 'prerequisite', 'strong', 'active'),
  ('{VP}', '{TRANSPOSE}', '{VP}', '{APPLY}', 'prerequisite', 'strong', 'deprecated'),
  ('{VP}', '{TRANSPOSE}', '{VP}', '{APPLY}', 'related', 'strong', 'active');
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, quote_text,
                                     verified_by, verified_at, mapping_state) VALUES
  ('{VP}', '{LINEAR}', 'syn-ed-a', 2024, '合成七上', '合成第三章', 80, 82, '{QUOTE_A}', 'syn-staff-01', now(), 'verified'),
  ('{VP}', '{LINEAR}', 'syn-ed-b', 2024, '合成七上', '合成第四章', 95, 95, '{QUOTE_B}', 'syn-staff-01', now(), 'verified'),
  ('{VP}', '{LINEAR}', 'syn-ed-c', 2024, '合成七上', '合成第五章', 101, 101, '{UNVERIFIED_QUOTE}', NULL, NULL, 'unverified'),
  ('{VP}', '{LINEAR}', 'syn-ed-d', 2024, '合成七上', '合成第六章', 110, 110, '{DISPUTED_QUOTE}', NULL, NULL, 'disputed'),
  ('{VP}', '{TRANSPOSE}', 'syn-ed-a', 2024, '合成七上', '合成第三章', 86, 86, '{QUOTE_T}', 'syn-staff-01', now(), 'verified');
INSERT INTO kge.region_projection (map_version_id, node_id, region, exam_year, weight, typical_slots, source_ref) VALUES
  ('{VP}', '{LINEAR}', 'syn-region', 2025, 0.03, '{{填空题}}', 'synthetic'),
  ('{VP}', '{LINEAR}', 'syn-other', 2025, 0.02, '{{选择题}}', 'synthetic'),
  ('{VP}', '{APPLY}', 'syn-region', 2025, 0.05, '{{解答题}}', 'synthetic');
INSERT INTO kge.item_family (id, map_version_id, main_node_id, code) VALUES ('{FAM}', '{VP}', '{LINEAR}', 'syn-fam-linear');
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, prior_difficulty, family_id,
                          source_kind, license_state, tag_review_status) VALUES
  ('{VP}', 'aya', 'SYN-I1', '{LINEAR}', '{{{TRANSPOSE}}}', 'apply', 0.4, '{FAM}', 'original', 'original', 'approved'),
  ('{VP}', 'aya', 'SYN-I2', '{LINEAR}', '{{}}', 'apply', 0.6, '{FAM}', 'original', 'original', 'approved'),
  ('{VP}', 'aya', 'SYN-I3', '{APPLY}', '{{{LINEAR}}}', 'understand', 0.3, NULL, 'original', 'licensed', 'approved'),
  ('{VP}', 'aya', 'SYN-I4', '{LINEAR}', '{{}}', 'apply', 0.5, '{FAM}', 'original', 'original', 'pending'),
  ('{VP}', 'aya', 'SYN-I5', '{LINEAR}', '{{}}', 'apply', 0.5, '{FAM}', 'original', 'original', 'rejected');
INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids, subject_dimension,
                               keyword_family, partial_credit_rule) VALUES
  ('{VP}', 'aya', 'SYN-I1', 'syn-rubric@0.1.0', 'transpose', 2, '{{{TRANSPOSE}}}', 'computation', '["移项", "变号", "移项"]', '{{}}'),
  ('{VP}', 'aya', 'SYN-I1', 'syn-rubric@0.1.0', 'solve', 1, '{{{LINEAR}}}', NULL, '[]', '{{"mode": "stepwise"}}'),
  -- 映射有损（fail closed，回 500）：关键词按类别分组的对象写法；部分给分规则带 mode 以外的键
  ('{VP}', 'aya', 'SYN-I2', 'syn-rubric@0.1.0', 'grouped', 1, '{{{LINEAR}}}', NULL, '{{"core": ["移项"]}}', '{{}}'),
  ('{VP}', 'aya', 'SYN-I3', 'syn-rubric@0.1.0', 'extra', 1, '{{{APPLY}}}', NULL, '[]', '{{"mode": "stepwise", "cap": 1}}'),
  -- 题目标签待审（SYN-I4）：采分点不返回
  ('{VP}', 'aya', 'SYN-I4', 'syn-rubric@0.1.0', 'pending', 1, '{{{LINEAR}}}', NULL, '[]', '{{}}');
INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES
  ('{VP}', '{{{LINEAR}}}', '合成来源：有效', current_date - 1, current_date + 30, 'published'),
  ('{VP}', '{{{LINEAR}}}', '合成来源：已过期', current_date - 60, current_date, 'published'),
  ('{VP}', '{{{LINEAR}}}', '合成来源：待审', current_date - 1, current_date + 30, 'pending');
SET session_replication_role = origin;

CREATE ROLE {APP_ROLE} LOGIN IN ROLE starbuddy_kge_read;
CREATE ROLE {BYPASS_ROLE} LOGIN BYPASSRLS IN ROLE starbuddy_kge_read;
CREATE ROLE {WRITER_ROLE} LOGIN IN ROLE starbuddy_kge_read, starbuddy_kge;
CREATE ROLE {OTHER_ROLE} LOGIN;
CREATE ROLE {PREDEF_ROLE} LOGIN IN ROLE starbuddy_kge_read, pg_read_all_data;
CREATE ROLE {CREATEDB_ROLE} LOGIN CREATEDB IN ROLE starbuddy_kge_read;
CREATE ROLE {CREATEROLE_ROLE} LOGIN CREATEROLE IN ROLE starbuddy_kge_read;
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
        self.work = tempfile.mkdtemp(prefix="starbuddy-knowledge-test.")
        if self.root:
            shutil.chown(self.work, "postgres")
        os.chmod(self.work, 0o700)
        self.db = "knowledge_test"

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

    def dsn(self, user: str) -> str:
        return f"postgresql://{user}@/{self.db}?host={self.work}"

    def stop(self) -> None:
        try:
            self._as_pg(f"{self.pgbin}/pg_ctl", "-D", f"{self.work}/data", "-m", "immediate", "stop")
        except subprocess.CalledProcessError:
            pass
        shutil.rmtree(self.work, ignore_errors=True)
