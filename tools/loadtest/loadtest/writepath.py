"""PLT-21 写路径补全（第一步，specs/loadtest-write-path-plt21）：让本机一次性栈上的再练 / 复测取题返回 ready 与真票据，
作答真正经判分落库、计次。只在本机一次性库（starbuddy_test*，Unix socket），只用合成数据（规则 8、9）；不改服务、计量、契约与适配器。

组成（全部复用仓库现有的路径，不另写替身）：
- 合成题：学习服务的合成题面夹具（services/learning/learning/practice/fixtures/synthetic-items.json，题号 SYNP-*，再练题面提供者也读它）
  里的客观题，写进阿鸦内容包的一份合成副本（真实内容包原样 + 这些题：答案、来源已核、学科审核 Q2、admitted），
  副本经阿鸦自己的 indexPack 校验；阿鸦适配器以 AYA_CONTENT_PACK 起一个进程（只在候选版开关打开的测试栈里允许；副作用：
  这个进程同时接受 1.1.0 候选版请求，只限该进程，学习服务对它仍发 1.0.0），generate_variation 与 grade 都走它的合成题路径
  （验证器 aya-symbolic-ts，确定性判分）。副本的准入决定记录（admission_decisions）是合成占位摘要（全零 sha256、合成导出编号），
  不是真实准入依据，条数与副本里带审核依据的题数一致；
- 登记与准入：阿鸦的内容登记脚本（registrationSql，经 node/aya-register.mts）把 SYNP-* 登记进一次性库（pending_review），
  再由两名合成 staff 经发布闸门签准入并执行（platform.sign_admission → change_admission），升为 admitted；
- 合成地图：一份阿鸦地图草稿（两个知识节点，层次 apply；题目族与打标审核通过），经审核签字（两名合成 staff，kge.sign_review）
  与 kge.publish_map_version 发布，并绑到阿鸦内容包的草稿内容版本（证据落库时据此记地图版本）；之后两名合成 staff 停用；
- 知识服务：起真知识服务（services/knowledge，只读角色 starbuddy_kge_read 的一次性登录角色，只读这个一次性库；
  学习服务按 packages/contracts 知识服务 OpenAPI 严格校验它的响应）；
- 掌握配置（PLT-22，specs/loadtest-mastery-path-plt22）：学习服务以 LEARNING_MASTERY_CONFIG_AYA 加载压测专用的合成掌握配置
  tools/loadtest/config/mastery.synthetic.json（由学习服务单测用的 synthetic-test.json 原样复制、数值未改，与单测配置解耦；
  synthetic=true、未签字；加载器只在 LEARNING_ENV=test 接受，本工具起栈前再核对一遍），落库后由 KGE-04 的 after_ingest
  同步重算作答涉及的单元（只按主节点）。配置参数是测试占位、数值待人确认，不代表任何学科口径。--no-mastery 不加载（对照）；
- 种子：每个合成学生经学习服务的 grade 写入路径（与 BFF 同一服务令牌、键盘通道）串行做两道：先做对一道 N2 的题（复测参照题），
  再做错一道 N1 的题（再练条目到期，首轮间隔 0 小时）。**工具不写任务表**：任务只由重算生成（PLT-21 手工种入 verify_in_platform
  的做法已去掉）。合成配置下一道题的置信度（质量 / (质量 + k)）低于复核下限，重算当即给两个单元各排一条 decay 复测任务，
  到期时刻 = 最后一条计入证据的记录时间（已到期，KGE-04 现有口径，不用时间注入）；复测列表按到期时间排序，先做的 N2 在前。
  复测轮（rung0，到“执行”时排）的间隔是 7200 秒，学习服务进程没有可从外部注入的时钟（MasteryEngine / 再练时钟只在进程内测试里
  可注入；证据 recorded_at 是数据库服务器时间），压测窗口内排出的复测轮不会到期（只计数、不作答）。
  种子 grade 不经 BFF 的 HTTP 入口、不进压测指标；但要经计量（LT-04：学习服务写入前按计量账本核对 usage_id + 幂等键，
  没有预留的裸写 409）——由 SeedMeter 以学生本人账号、BFF 角色调同一组计量函数（meter_reserve 一笔 mistake_analysis / grade，
  学习服务确定成功即 meter_settle 1 次，确定失败 meter_release），usage_id 用库里按幂等键派生的那一个；不改任何计量规则、额度或扣次口径；
- 一致性核对：压测前后各取一次计数（只取本轮合成学生；计次经幂等键关联到 SYNP-* 题的作答），
  作答次数（真票据 2xx）= 合成题的计次增量（platform.practice_log）= 作答证据行增量（platform.attempt 的作答证据）
  = 触发的单元重算次数（pg_stat_statements 里重算读当前记录那条语句的调用数；见 mastery_consistency），
  且没有“计次无对应作答证据”与“作答证据无计次”。题库自练的计次口径是“开始即计数、没有释放”（BFF 先 meter_practice 再调学习服务，
  specs/metering-v2 偏差 13）：学习服务在计次之后失败时会留下“计次无对应作答证据”，这是设计口径而非计量故障；
  无故障注入的本机压测里应为 0，出现即判不通过并在报告列出。
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import meter as M
from .metrics import percentile

ROOT = Path(__file__).resolve().parents[3]
AYA = ROOT / "adapters/aya"
KNOWLEDGE = ROOT / "services/knowledge"
KNOWLEDGE_PY = KNOWLEDGE / ".venv/bin/python"
PRACTICE_FIXTURE = ROOT / "services/learning/learning/practice/fixtures/synthetic-items.json"
REGISTER_SCRIPT = ROOT / "tools/loadtest/node/aya-register.mts"

SUBJECT = "aya"
NS = uuid.UUID("2b7f6c1e-8d3a-4e59-9a0c-5e1f2d3c4b21")
MAP_ID = "5c1e0000-0000-4000-8000-00000000e021"
MAP_VERSION = "1.0.0"
STAFF = ("5c1e0000-0000-4000-8000-0000000005a1", "5c1e0000-0000-4000-8000-0000000005a2")
KNOWLEDGE_ROLE = "loadtest_knowledge"      # 只继承 starbuddy_kge_read（知识服务启动时自己再断言一遍）
KNOWLEDGE_CREDENTIAL = "synthetic-learning-to-knowledge-loadtest-01"  # 合成常量，不是真实凭据（≥ 32 字符、与其他令牌各不相同）
LEVEL = "apply"
N1, N2 = "MATH.LTW.T1.N1", "MATH.LTW.T1.N2"
# （题号, 主节点, 题目族）：N1 给再练（原题 SYNP-0001），N2 给复测（参照题 SYNP-0006）；每个单元都有不同族的候选。
# SYNP-0013 在夹具里标为 model_rubric（主观题，不进再练），不收
ITEMS: tuple[tuple[str, str, str], ...] = (
    ("SYNP-0001", N1, "ltw-a"), ("SYNP-0002", N1, "ltw-a"), ("SYNP-0003", N1, "ltw-b"), ("SYNP-0004", N1, "ltw-c"),
    ("SYNP-0005", N1, "ltw-d"), ("SYNP-0011", N1, "ltw-e"),
    ("SYNP-0006", N2, "ltw-f"), ("SYNP-0007", N2, "ltw-g"), ("SYNP-0008", N2, "ltw-h"), ("SYNP-0009", N2, "ltw-i"),
    ("SYNP-0010", N2, "ltw-j"), ("SYNP-0012", N2, "ltw-k"),
)
FILL_ANSWER = "1"            # 驱动器对填空题一律答 "1"（bodies.answer_for），合成题的标准答案也是它
PRACTICE_SEED = ("SYNP-0001", {"text": "0"})   # 做错：再练条目到期
RETEST_SEED = ("SYNP-0006", {"text": FILL_ANSWER})  # 做对：复测参照题（不开再练条目）
# PLT-22：每名学生先做 N2（做对）、再做 N1（做错），串行。合成配置下一道题的置信度低于复核下限，重算当即给两个单元各排一条
# decay 复测任务（到期时刻 = 最后一条计入证据的记录时间，即已到期）；复测列表按到期时间排序，先做的 N2 排在前面，
# 驱动器取第一条到期条目 → 复测落在 N2，不与再练（N1）撞同一单元（N1 那条 decay 会在再练作答后由重算撤销）。工具不写任务表
SEEDS: tuple[tuple[str, str, dict], ...] = (("retest", *RETEST_SEED), ("practice", *PRACTICE_SEED))
MASTERY_CONFIG = ROOT / "tools/loadtest/config/mastery.synthetic.json"   # 压测专用副本（数值同下面的来源文件）
MASTERY_CONFIG_SOURCE = ROOT / "services/learning/config/mastery/synthetic-test.json"   # 只读、不动；测试核对数值一致
MASTERY_SUBJECT = "aya"
RUBRIC = "aya-symbolic-ts@0.1.0"
ANSWER_STEPS = ("practice.answer", "retest.answer")
PLACEHOLDER_EXPORT = "SYN-EXPORT-LT-PLACEHOLDER"   # 合成占位：不是真实准入决定文件的导出编号
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64          # 合成占位摘要：不是任何真实文件的摘要


def missing() -> str | None:
    """起写路径还缺什么（缺时栈照旧：再练 / 复测作答只到票据校验）"""
    if not KNOWLEDGE_PY.exists():
        return "没有 services/knowledge/.venv（知识服务）"
    if not PRACTICE_FIXTURE.exists():
        return "没有再练合成题面夹具"
    if not (AYA / "content/pack.json").exists():
        return "没有阿鸦内容包"
    if not MASTERY_CONFIG.exists():
        return "没有合成掌握配置（tools/loadtest/config/mastery.synthetic.json）"
    return None


# ---------------- 合成掌握配置（PLT-22） ----------------
def check_mastery_config(path: Path = MASTERY_CONFIG) -> dict:
    """起栈前在工具侧核对掌握配置只能是合成占位：synthetic=true、未签字（signed_by / signed_at 都空）、学科 aya。
    学习服务的加载器另有一道：合成或未签字的配置只在 LEARNING_ENV=test 加载（learning/mastery/config.py parse_config）。
    返回配置（只读；不改任何数值，参数是测试占位、不代表任何学科口径）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("synthetic") is not True:
        raise RuntimeError("压测只加载合成掌握配置（synthetic=true）")
    if str(doc.get("signed_by") or "").strip() or str(doc.get("signed_at") or "").strip():
        raise RuntimeError("压测不加载已签字的掌握配置（只用合成占位）")
    if doc.get("subject") != MASTERY_SUBJECT:
        raise RuntimeError(f"合成掌握配置的学科必须是 {MASTERY_SUBJECT}")
    return doc


def mastery_env(path: Path = MASTERY_CONFIG) -> dict[str, str]:
    """学习服务的掌握配置环境：只给阿鸦一科（LEARNING_MASTERY_CONFIG_AYA），同步重算预算取服务缺省（见 sync_budget_seconds）。
    只在 LEARNING_ENV=test 的本栈用（stack.local_stack 固定 LEARNING_ENV=test）"""
    check_mastery_config(path)
    return {f"LEARNING_MASTERY_CONFIG_{MASTERY_SUBJECT.upper()}": str(path)}


def rung0_gap_seconds(doc: Mapping) -> int:
    return int(doc["retest"]["rung0_min_gap_seconds"])


def sync_budget_seconds() -> tuple[float | None, str]:
    """学习服务同步重算的时间预算（本栈不设 LEARNING_MASTERY_SYNC_BUDGET_SECONDS，取服务缺省）：从 learning.config 读；
    读不到时返回 (None, "假设")，报告按 2 s 假设标注"""
    try:
        from learning.config import _budget

        return float(_budget(None)), "learning.config 缺省"
    except Exception:  # noqa: BLE001 — 只为报告标注来源
        return None, "假设"

def _uuid(*parts: object) -> str:
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def uuid4_shaped(*parts: object) -> str:
    """由输入确定的 UUIDv4 形状（版本位 4、变体位 10）：幂等键、作答 ID 用"""
    b = bytearray(uuid.uuid5(NS, "|".join(str(p) for p in parts)).bytes)
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def family_id(code: str) -> str:
    return _uuid("family", MAP_ID, code)


def load_fixture(path: Path = PRACTICE_FIXTURE) -> dict[str, dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {it["item_id"]: it for it in doc["items"] if it["subject"] == SUBJECT}


# ---------------- 合成内容包 ----------------
def synthetic_pack(base: Mapping, fixture: Mapping[str, Mapping]) -> dict:
    """真实内容包原样 + ITEMS 里的合成题（不改输入）。题面不进内容包（再练题面来自学习服务的合成夹具）；
    标准答案：填空 "1"，选择题取第一个选项；主测节点取真实内容包第一道题的主测节点（判分与选题只看同节点）。"""
    pack = json.loads(json.dumps(base))
    skill = pack["items"][0]["mainNodes"][0]
    have = {it["id"] for it in pack["items"]}
    added = 0
    for n, (item_id, _node, _fam) in enumerate(ITEMS, 1):
        if item_id in have:
            raise ValueError(f"真实内容包里已有 {item_id}")
        f = fixture[item_id]
        if f.get("grading") not in ("validator", "deterministic_rubric") or f.get("rubric_version") != RUBRIC:
            raise ValueError(f"{item_id} 不是验证器判分的客观题")
        it: dict = {
            "id": item_id, "format": f["format"], "mainNodes": [skill], "reviewStatus": "Q2", "admission": "admitted",
            "source": {"source_ref": f"SYN-SRC-LT-{n:04d}", "source_type": "self_authored", "license_status": "cleared"},
            "admission_review": {"review_level": "Q2", "review_ids": [_uuid("review", item_id)]},
            "usage": "L", "estMinutes": 1, "hintPath": ["（合成提示）先读题，再写出结果。"],
        }
        if f["format"] == "fill":
            it["answer"] = [FILL_ANSWER]
        else:
            it["options"] = [{"key": o["key"]} for o in f["options"]]
            it["answer"] = [f["options"][0]["key"]]
        pack["items"].append(it)
        added += 1
    if pack.get("admission_decisions") is not None:
        # 真实内容包已有准入决定文件时不能把合成题混进它的摘要（会把占位冒充成真实依据）：这一步不支持，失败
        raise ValueError("真实内容包已有准入决定记录：合成副本不与真实准入依据混用")
    # 合成占位摘要（全零 sha256、合成导出编号）：不是真实准入依据；条数与副本里带审核依据的题数一致（阿鸦 indexPack 的口径）
    pack["admission_decisions"] = {"format": "aya-admission-decisions/1", "export_ref": PLACEHOLDER_EXPORT,
                                   "exported_at": "2026-10-18T00:00:00Z", "digest": PLACEHOLDER_DIGEST,
                                   "decisions": sum(1 for it in pack["items"] if it.get("admission_review"))}
    return pack


def answer_of(pack: Mapping, item_id: str) -> list[str]:
    return next(it["answer"] for it in pack["items"] if it["id"] == item_id)


# ---------------- 一次性库：知识服务登录角色、准入、地图 ----------------
GUARD_SQL = """DO $$
BEGIN
  IF current_database() !~ '^starbuddy_test' OR inet_server_addr() IS NOT NULL THEN
    RAISE EXCEPTION '写路径种子只能经本机 Unix socket 用于一次性测试库（starbuddy_test*）';
  END IF;
END $$;
"""

ROLE_SQL = GUARD_SQL + f"""DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{KNOWLEDGE_ROLE}') THEN
    CREATE ROLE {KNOWLEDGE_ROLE} LOGIN IN ROLE starbuddy_kge_read;
  END IF;
END $$;
"""


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def admission_sql() -> str:
    """合成 staff 对每道合成题签准入（pending_review → admitted，来源已核、Q2）并执行（迁移 0039 的发布闸门）"""
    signs = ",\n  ".join(
        f"platform.sign_admission('aya', {_lit(i)}, 'admitted', 'approved', 'Q2', 'SYN-SRC-LT-{n:04d}', 'self_authored', 'cleared', "
        f"platform.item_admission_digest('aya', {_lit(i)}), 'loadtest_synthetic')"
        for n, (i, _, _) in enumerate(ITEMS, 1))
    return (GUARD_SQL + f"""DO $$
BEGIN
  -- 两名合成 staff 必须是这里新建的（库里原先有同 ID 的账号就停下，不复用、不改它的状态）
  IF EXISTS (SELECT 1 FROM platform.account WHERE id IN ('{STAFF[0]}', '{STAFF[1]}')) THEN
    RAISE EXCEPTION '合成 staff 账号 ID 已存在：写路径种子只用于新建的一次性库';
  END IF;
END $$;
INSERT INTO platform.account (id, kind) VALUES ('{STAFF[0]}', 'staff'), ('{STAFF[1]}', 'staff') ON CONFLICT (id) DO NOTHING;
SET ROLE starbuddy_review;
SELECT set_config('app.account_id', '{STAFF[0]}', false);
SELECT platform.change_admission(ARRAY[
  {signs}]);
SELECT set_config('app.account_id', '', false);
RESET ROLE;
""")


def map_sql() -> str:
    """合成地图草稿（以知识层角色写入，同 KGE-01 导入器的做法）：一个领域、一个主题、两个知识节点（边界字段齐全、层次 apply），
    题目族与打标（草稿版本上审核通过的打标随版本签字一起生效）"""
    nodes = [
        f"('{MAP_ID}', 'MATH.LTW', 'domain', NULL, NULL, '（合成）压测领域', '{{}}', '{{}}', '{{}}', '{{}}')",
        f"('{MAP_ID}', 'MATH.LTW.T1', 'topic', 'MATH.LTW', NULL, '（合成）压测主题', '{{}}', '{{}}', '{{}}', '{{}}')",
    ]
    for nid, name in ((N1, "（合成）压测节点一"), (N2, "（合成）压测节点二")):
        nodes.append(f"('{MAP_ID}', '{nid}', 'node', 'MATH.LTW.T1', 'kp', '{name}', '{{（合成）包含}}', '{{（合成）不包含}}', "
                     f"'{{（合成）题例}}', '{{{LEVEL}}}')")
    node_rows = ",\n  ".join(nodes)
    fams = sorted({(f, node) for _, node, f in ITEMS})
    fam_rows = ",\n  ".join(f"('{family_id(f)}', '{MAP_ID}', '{node}', '{f}')" for f, node in fams)
    tag_rows = ",\n  ".join(
        f"('{MAP_ID}', 'aya', '{i}', '{node}', '{{}}', '{LEVEL}', '{family_id(f)}', 'original', 'original', 'approved')"
        for i, node, f in ITEMS)
    return (GUARD_SQL + f"""BEGIN;
SET LOCAL ROLE starbuddy_kge;
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('{MAP_ID}', 'aya', 2022, '{MAP_VERSION}');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
  {node_rows};
INSERT INTO kge.item_family (id, map_version_id, main_node_id, code) VALUES
  {fam_rows};
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, family_id, source_kind, license_state,
                          tag_review_status) VALUES
  {tag_rows};
COMMIT;
""")


def publish_sql(curriculum: Mapping) -> str:
    """两名合成 staff 对地图草稿签字、发布（结构性变更要两名），以学习服务角色（内容登记方，不用管理员绕 RLS）
    把阿鸦内容包的草稿内容版本绑到这份地图，最后停用两名 staff（管理员在一次性库里收尾）。绑定是否生效由 VERIFY_SQL 的 cv_bound 核对"""
    c = curriculum
    return (GUARD_SQL + f"""SET ROLE starbuddy_review;
SELECT set_config('app.account_id', '{STAFF[1]}', false);
SELECT kge.sign_review('map_version', '{MAP_ID}', 'approved', 'high', 'synthetic_seed');
SELECT set_config('app.account_id', '{STAFF[0]}', false);
SELECT kge.sign_review('map_version', '{MAP_ID}', 'approved', 'high', 'synthetic_seed');
SELECT kge.publish_map_version('{MAP_ID}');
SELECT set_config('app.account_id', '', false);
RESET ROLE;
SET ROLE starbuddy_learning;
UPDATE platform.curriculum_version SET map_version_id = '{MAP_ID}'
 WHERE subject = 'aya' AND status = 'draft' AND region = {_lit(c['region'])} AND exam_year = {int(c['exam_year'])}
   AND grade = {int(c['grade'])} AND textbook_version = {_lit(c['textbook_version'])} AND content_version = {_lit(c['content_version'])};
RESET ROLE;
UPDATE platform.account SET status = 'disabled' WHERE id IN ('{STAFF[0]}', '{STAFF[1]}');
""")


VERIFY_SQL = f"""SELECT json_build_object(
  'map_published', (SELECT status = 'published' FROM kge.map_version WHERE id = '{MAP_ID}'),
  'tags_approved', (SELECT count(*) FROM kge.item_tag WHERE map_version_id = '{MAP_ID}' AND tag_review_status = 'approved'),
  'items_admitted', (SELECT count(*) FROM platform.item WHERE subject = 'aya' AND item_id LIKE 'SYNP-%' AND admission = 'admitted'),
  'cv_bound', (SELECT count(*) FROM platform.curriculum_version WHERE subject = 'aya' AND map_version_id = '{MAP_ID}'),
  'active_staff', (SELECT count(*) FROM platform.account WHERE id IN ('{STAFF[0]}', '{STAFF[1]}') AND status = 'active'))"""


def registration_sql(pack_path: Path) -> str:
    return subprocess.run(["node", "--no-warnings", str(REGISTER_SCRIPT), str(pack_path)], cwd=ROOT, check=True,
                          capture_output=True).stdout.decode()


def prepare_content(pg, work: Path) -> dict:
    """在一次性库里装好合成题与合成地图（顺序：知识服务登录角色 → 登记 → 准入 → 地图草稿 → 签字发布 → 绑内容版本），
    并写出阿鸦的合成内容包副本。返回（内容包路径, 内容版本, 核对结果）。前提：stack 已执行过各科 register_content。"""
    base = json.loads((AYA / "content/pack.json").read_text(encoding="utf-8"))
    pack = synthetic_pack(base, load_fixture())
    path = work / "aya-synthetic-pack.json"
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    pg.psql(ROLE_SQL)
    pg.psql(GUARD_SQL + "SET ROLE starbuddy_learning;\n" + registration_sql(path))
    pg.psql(admission_sql())
    pg.psql(map_sql())
    pg.psql(publish_sql(pack["curriculum"]))
    check = json.loads(pg.psql(VERIFY_SQL).strip())
    want = {"map_published": True, "tags_approved": len(ITEMS), "items_admitted": len(ITEMS), "cv_bound": 1, "active_staff": 0}
    if check != want:
        raise RuntimeError(f"写路径种子核对不通过：{check}")
    return {"pack": path, "curriculum": dict(pack["curriculum"]), "check": check}


def knowledge_env(db_url: str, port: int, cache_dir: Path) -> dict[str, str]:
    """真知识服务（只读）的环境：只配学习服务一个调用方令牌；只监听 127.0.0.1；页面快照不配（page.get 回 501，取题用不到）"""
    return {"KNOWLEDGE_ENV": "test", "KNOWLEDGE_DATABASE_URL": db_url, "KNOWLEDGE_TOKEN_LEARNING": KNOWLEDGE_CREDENTIAL,
            "KNOWLEDGE_HOST": "127.0.0.1", "KNOWLEDGE_PORT": str(port), "KNOWLEDGE_CACHE_DIR": str(cache_dir),
            "PYTHONDONTWRITEBYTECODE": "1"}


# ---------------- 合成学生的到期条目 ----------------
def seed_envelope(student: str, item_id: str, answer: dict, key: str, usage_id: str, curriculum: Mapping, occurred_at: str) -> dict:
    """学习服务 learning-internal v1 的 grade 信封（同 BFF 与再练作答的形状；键盘通道，等级按平台规则记 A）"""
    request = {
        "context": {"request_id": str(uuid.uuid4()), "contract_version": "1.0.0", "subject": SUBJECT, "student_ref": student,
                    "data_class": "L2", "usage_id": usage_id, "idempotency_key": key, "content_version": dict(curriculum)},
        "attempt": {"attempt_id": uuid4_shaped("seed-attempt", key), "item_ref": f"{SUBJECT}:{item_id}", "occurred_at": occurred_at,
                    "response": dict(answer), "hint_level_used": 0},
        "rubric_version": RUBRIC,
    }
    return {"request": request, "ingest": {"channel": "keypad"}}


def _verdict(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    for v in (body.get("verdict"), (body.get("summary") or {}).get("verdict") if isinstance(body.get("summary"), dict) else None):
        if isinstance(v, str):
            return v
    return None


def check_local_url(url: str) -> None:
    """种子只发给本机回环地址上的学习服务（http://127.0.0.1:<端口>，本栈自己起的进程）"""
    u = urlsplit(url)
    if u.scheme != "http" or u.hostname != "127.0.0.1" or u.port is None or u.path not in ("", "/") or u.username or u.query:
        raise RuntimeError("写路径种子只发给本机 127.0.0.1 上的学习服务")


def check_seed_verdicts(verdicts: Mapping[str, int], students: int) -> None:
    """种子的判分结论必须正好是 practice:incorrect × n、retest:correct × n（否则再练条目不会到期、复测参照题不对），不符立即失败"""
    want = {"practice:incorrect": students, "retest:correct": students}
    got = dict(verdicts)
    if got != want:
        raise RuntimeError(f"写路径种子的判分结论不符：应为 {want}，实际 {dict(sorted(got.items()))}"
                           "（检查合成内容包的标准答案、阿鸦验证器版本与种子作答）")


# ---------------- 复测任务的来源核对（只读；PLT-22：任务只由重算生成） ----------------
# 每条任务：原因、是否开着、是否已到期，以及能否从库内字段证明它由重算写出——
# 复测轮（scheduled / early）：scheduled_by_estimate_id 指向同学生同单元的掌握记录，且那条记录与任务同一事务写出
#   （computed_at 与 created_at 都是事务开始时刻 now()，迁移 0037 的守卫把 created_at 置为 now()）、到期时间等于其 next_retest_at；
# 其他原因：同单元有一条与任务同一事务写出的掌握记录。两者都不满足的记为 unproven（例如 PLT-21 工具手工种入的那种行）。
TASKS_SQL = """SELECT t.reason, t.status, t.due_at <= now() AS due,
  CASE WHEN t.reason IN ('scheduled', 'early') THEN EXISTS (
         SELECT 1 FROM platform.mastery_estimate e WHERE e.id = t.scheduled_by_estimate_id AND e.student_id = t.student_id
            AND e.node_id = t.node_id AND e.cognitive_level = t.cognitive_level AND e.computed_at = t.created_at
            AND e.next_retest_at = t.due_at)
       ELSE EXISTS (
         SELECT 1 FROM platform.mastery_estimate e WHERE e.student_id = t.student_id AND e.subject = t.subject
            AND e.node_id = t.node_id AND e.cognitive_level = t.cognitive_level AND e.computed_at = t.created_at) END AS proven
  FROM platform.retest_task t WHERE t.student_id = ANY($1::uuid[])"""


def task_origins(rows: Iterable[Mapping]) -> dict:
    """任务行 → 汇总：总数、按“原因:状态”计数、开着且已到期的条数、能证明由重算写出的条数、不能证明的条数（应为 0）"""
    by: Counter = Counter()
    total = proven = due_open = 0
    for r in rows:
        total += 1
        by[f"{r['reason']}:{r['status']}"] += 1
        proven += bool(r["proven"])
        due_open += bool(r["due"]) and r["status"] == "open"
    return {"total": total, "by_reason_status": dict(sorted(by.items())), "from_recompute": proven, "unproven": total - proven,
            "due_open": due_open}


async def tasks(admin, students: Sequence[str]) -> dict:
    """只读（管理员连接、只在一次性库）：本轮合成学生的全部复测任务的来源汇总"""
    return task_origins(await admin.fetch(TASKS_SQL, list(students)))


# 上面的“同一事务”推断依赖两个触发器把时间戳置为 now()：迁移 0005 的 guard_inference_insert（mastery_estimate.computed_at）
# 与迁移 0037 的 retest_task_guard（retest_task.created_at）。只读核对它们存在且启用（tgenabled 'O' / 'A'）
TRIGGERS = (("mastery_estimate", "guard_inference_insert"), ("retest_task", "retest_task_guard"))
TRIGGERS_SQL = """SELECT c.relname, t.tgname, t.tgenabled::text AS enabled FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'platform' AND NOT t.tgisinternal AND (c.relname, t.tgname) IN
   (('mastery_estimate', 'guard_inference_insert'), ('retest_task', 'retest_task_guard'))"""


def triggers_ok(rows: Iterable[Mapping]) -> dict:
    """触发器核对行 → {表.触发器: 启用与否}，以及全部存在且启用的结论"""
    got = {f"{r['relname']}.{r['tgname']}": r["enabled"] in ("O", "A") for r in rows}
    want = [f"{a}.{b}" for a, b in TRIGGERS]
    return {"triggers": {k: got.get(k, False) for k in want}, "ok": all(got.get(k, False) for k in want)}


async def check_triggers(admin) -> dict:
    return triggers_ok(await admin.fetch(TRIGGERS_SQL))


# 直接证据：pg_stat_statements 里所有写 platform.retest_task 的语句（INSERT / UPDATE），按执行角色与语句文本分类。
# 学习服务自己的写入语句只有下面这几条（mastery/recompute.py：重算建任务、关任务、撤任务；自述验证的建与撤），由 starbuddy_learning
# 执行；其他角色、或 starbuddy_learning 下的其他写语句都算“非服务写入”，应为 0（PLT-21 手工种入的 INSERT 列不同，会落在这里）。
# 局限：pg_stat_statements 从一次性实例开启后累计，压测开始前清零一次，所以种子阶段在清零前取一次、压测后再取一次，两次相加。
SERVICE_TASK_WRITES = (
    "INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, rung, reason, due_at, scheduled_by_estimate_id)",
    "UPDATE platform.retest_task SET status = $2, closing_attempt_id = $3::uuid",
    "UPDATE platform.retest_task SET status = 'cancelled' WHERE id = $1::uuid AND student_id = $2::uuid",
    "INSERT INTO platform.retest_task (id, student_id, subject, node_id, cognitive_level, reason, due_at)",
)
TASK_WRITES_SQL = r"""SELECT r.rolname, regexp_replace(s.query, '\s+', ' ', 'g') AS query, s.calls
  FROM loadtest_ext.pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
 WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
   AND regexp_replace(s.query, '\s+', ' ', 'g') ~* '^\s*(INSERT\s+INTO|UPDATE)\s+platform\.retest_task\M'"""


_PARAM = re.compile(r"\$\d+|'(?:[^']|'')*'")


def normalize_sql(q: str) -> str:
    """比较用：pg_stat_statements 把常量换成 $n 并重排编号（如 status = 'cancelled' → status = $3），这里把参数与字符串常量都换成 $?"""
    return _PARAM.sub("$?", " ".join(str(q).split()))


def task_writes(rows: Iterable[Mapping]) -> dict:
    """写任务表的语句 → 服务写入调用数、非服务写入调用数（应为 0）"""
    svc = other = 0
    known = [normalize_sql(p) for p in SERVICE_TASK_WRITES]
    for r in rows:
        q = normalize_sql(r["query"])
        if r["rolname"] == "starbuddy_learning" and any(q.startswith(p) for p in known):
            svc += int(r["calls"])
        else:
            other += int(r["calls"])
    return {"service_calls": svc, "non_service_calls": other}


async def task_writes_now(admin, enabled: bool) -> dict | None:
    if not enabled:
        return None
    return task_writes(await admin.fetch(TASK_WRITES_SQL))


def check_seed_tasks(t: Mapping, students: int) -> None:
    """种子之后：全部任务能证明由重算写出，且开着、已到期的任务至少每名学生一条（合成配置下是两个单元各一条 decay）；
    不符立即失败。到期的原因与条数照实汇总进报告"""
    if t["unproven"] or t["due_open"] < students:
        raise RuntimeError(f"种子之后的复测任务不符：应全部由重算写出、且每名学生至少一条开着的到期任务，实际 {dict(t)}"
                           "（检查合成掌握配置是否加载、种子判分与地图打标）")


SEED_RESOURCE = "mistake_analysis"   # 与 BFF 批改同一资源（meteredCall 的 mistake_analysis → grade）


class SeedMeter:
    """写路径种子的计量（LT-04）：学习服务写入前按计量账本核对 usage_id + 幂等键 + 学生 + 方法（迁移 0058），没有预留的裸写一律
    409 IDEMPOTENCY_KEY_SEALED。种子与 BFF 的 meteredCall 走同一组计量函数、同一数据库角色（bff_app 池，事务里先设 app.account_id
    为学生本人账号）：预留一笔 mistake_analysis（来源方法 grade），学习服务确定成功即结算 1 次，否则释放。只调函数、不直写计量表，
    不改任何计量规则；结算与释放让每笔预留恰好一个终态，零泄漏核对（meter.leak_report）照常成立。"""

    def __init__(self, bff_pool, accounts: Mapping[str, str]) -> None:
        self.pool, self.accounts = bff_pool, dict(accounts)

    async def reserve(self, student: str, key: str, fingerprint: str) -> str:
        r = await M.reserve(self.pool, self.accounts[student], student, SEED_RESOURCE, key, fingerprint, 1, None, "grade")
        if not isinstance(r, dict) or r.get("outcome") != "reserved" or not isinstance(r.get("usage_id"), str):
            raise RuntimeError(f"写路径种子预留失败：{r.get('outcome') if isinstance(r, dict) else r}")
        return r["usage_id"]

    async def settle(self, student: str, usage_id: str) -> None:
        r = await M.settle(self.pool, self.accounts[student], usage_id, 1)
        if not isinstance(r, dict) or r.get("outcome") != "settled":
            raise RuntimeError(f"写路径种子结算失败：{r.get('outcome') if isinstance(r, dict) else r}")

    async def release(self, student: str, usage_id: str) -> None:
        await M.release(self.pool, self.accounts[student], usage_id, "loadtest_seed_failed")


async def seed_students(client, learning_url: str, credential: str, admin, students: Sequence[str], seed: int,
                        curriculum: Mapping, concurrency: int = 8, mastery: bool = True, *, meter: SeedMeter) -> dict:
    """mastery=False（--no-mastery 对照）时学习服务不重算，不核对任务（照实汇总，应为 0 条）。
    每个合成学生依次：做对 N2 一道（复测参照题）、做错 N1 一道（再练到期）；重算按合成配置给两个单元各排一条已到期的 decay 任务。
    经学习服务 grade 写入路径（BFF 服务令牌）；每笔先经 meter（SeedMeter，BFF 同一组计量函数）预留、成功结算、失败释放——
    学习服务写入前核对计量（LT-04），裸写会被 409 拒绝；只用于准备数据，不计入压测指标。
    **不写任务表**：复测任务只由学习服务落库后的同步重算生成（PLT-22）；本函数对库只读（目标核对与任务来源核对）。
    同一学生的两笔串行（先 N2 后 N1：复测列表按到期时间排序，见 SEEDS），不同学生之间并发。
    任何一笔没成功、判分结论不符、任务不符即失败。"""
    # 先核对写入目标，再发任何请求：学习服务只能是本机回环地址；库只能是本机 Unix socket 上的一次性测试库（规则 9）
    check_local_url(learning_url)
    if not await admin.fetchval("SELECT current_database() ~ '^starbuddy_test' AND inet_server_addr() IS NULL"):
        raise RuntimeError("写路径种子只能写本机 Unix socket 上的一次性测试库（starbuddy_test*）")
    sem = asyncio.Semaphore(concurrency)
    verdicts: Counter = Counter()
    now = datetime.now(timezone.utc).isoformat()

    async def student_seeds(student: str) -> None:
        async with sem:
            for kind, item, answer in SEEDS:
                label = f"{kind}:{item}"
                key = uuid4_shaped("seed", seed, student, label)
                # usage_id 只能是库里按幂等键派生的那一个（meter_reserve 返回），学习服务按它核对计量
                usage_id = await meter.reserve(student, key, M.fingerprint("loadtest-seed", student, label, key))
                body = seed_envelope(student, item, answer, key, usage_id, curriculum, now)
                r = await client.post(f"{learning_url}/internal/v1/grade", json=body,
                                      headers={"authorization": f"Bearer {credential}"}, timeout=60)
                if r.status_code != 200:
                    # 与 BFF 同一口径：确定失败（4xx，408 / 429 除外）释放；结果未知（5xx 等）不释放，留给过期任务
                    if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                        await meter.release(student, usage_id)
                    raise RuntimeError(f"写路径种子 grade 失败：{r.status_code}")
                await meter.settle(student, usage_id)
                verdicts[f"{kind}:{_verdict(r.json())}"] += 1

    await asyncio.gather(*(student_seeds(s) for s in students))
    check_seed_verdicts(verdicts, len(students))
    t = await tasks(admin, students)
    if mastery:
        check_seed_tasks(t, len(students))
    return {"students": len(students), "grades": sum(verdicts.values()), "verdicts": dict(sorted(verdicts.items())),
            "retest_tasks": t, "manual_tasks": 0}


# ---------------- 一致性核对 ----------------
COUNTS_SQL = """SELECT
  (SELECT count(*) FROM platform.practice_log p WHERE p.student_id = ANY($1::uuid[]) AND EXISTS
     (SELECT 1 FROM platform.attempt a WHERE a.student_id = p.student_id AND a.request_idempotency_key = p.idempotency_key
         AND a.item_id LIKE 'SYNP-%')) AS counted,
  (SELECT count(*) FROM platform.practice_log WHERE student_id = ANY($1::uuid[])) AS counted_all,
  (SELECT count(*) FROM platform.attempt a JOIN platform.evidence_event e ON e.evidence_id = a.response_evidence_id
    WHERE a.student_id = ANY($1::uuid[]) AND a.item_id LIKE 'SYNP-%') AS response_evidence,
  (SELECT count(*) FROM platform.evidence_event WHERE student_id = ANY($1::uuid[]) AND item_id LIKE 'SYNP-%') AS evidence,
  (SELECT count(*) FROM platform.practice_log p WHERE p.student_id = ANY($1::uuid[]) AND NOT EXISTS
     (SELECT 1 FROM platform.attempt a WHERE a.student_id = p.student_id AND a.request_idempotency_key = p.idempotency_key))
     AS counted_without_evidence,
  (SELECT count(*) FROM platform.attempt a WHERE a.student_id = ANY($1::uuid[]) AND a.item_id LIKE 'SYNP-%' AND NOT EXISTS
     (SELECT 1 FROM platform.practice_log p WHERE p.student_id = a.student_id AND p.idempotency_key = a.request_idempotency_key))
     AS evidence_without_count"""
COUNT_KEYS = ("counted", "counted_all", "response_evidence", "evidence", "counted_without_evidence", "evidence_without_count")


async def counts(admin, students: Sequence[str]) -> dict[str, int]:
    """只读计数（管理员连接、只在一次性库）。只取本轮合成学生：
    counted = 经幂等键关联到 SYNP-* 题作答的计次行（合成题范围）；counted_all = 这些学生的全部计次行；
    response_evidence = SYNP-* 题的作答证据行（每次作答一行）；evidence = SYNP-* 题的全部证据行（含判分轨迹）；
    counted_without_evidence = 没有任何对应作答（同一幂等键）的计次行；evidence_without_count = 没有对应计次的 SYNP-* 作答"""
    row = await admin.fetchrow(COUNTS_SQL, list(students))
    return {k: int(row[k]) for k in COUNT_KEYS}


def answers_ok(paths: Mapping[tuple[str, str, str], int], steps: Sequence[str] = ANSWER_STEPS) -> dict[str, int]:
    """驱动器记的作答（只算 steps 里的步骤）：真票据且 2xx 的次数、真票据总数、派生票据次数（Recorder.paths）"""
    out = {"ok": 0, "real": 0, "derived": 0}
    for (step, kind, outcome), n in paths.items():
        if step not in steps:
            continue
        out[kind] += n
        if kind == "real" and outcome == "ok":
            out["ok"] += n
    return out


def consistency(answers: Mapping[str, int], before: Mapping[str, int], after: Mapping[str, int], *, applicable: bool = True,
                mastery: Mapping | None = None) -> dict:
    """判定口径（全部是压测前后的增量，只取本轮合成学生）：
    1. 作答次数（驱动器记的真票据 2xx）= 合成题范围的计次增量（经幂等键关联到 SYNP-* 作答的 practice_log 行）
       = SYNP-* 的作答证据行增量（每次作答一行）；给了 mastery（PLT-22，见 mastery_problems）时另要 = 触发的单元重算次数，
       且复测任务全部能证明由重算写出、重算没有失败或超时；
    2. 计次无对应作答证据 = 0：题库自练“开始即计数、没有释放”（BFF 先 meter_practice 再调学习服务），学习服务在计次之后失败
       就会留下这种行——这是设计口径，不是计量故障；但本机无故障注入时应为 0，出现即不通过并列出；
    3. 作答证据无计次 = 0（落库了却没计次）；
    4. 全部计次 = 合成题范围的计次 + 计次无对应作答证据（没有计次关联到合成题以外的作答）；
    5. 作答都走真票据（派生票据为 0）。
    另给证据行总增量（含判分轨迹）与每次作答的证据行数（只报告，不判定）。applicable = 开了作答场景；没开时只报告、不判定。"""
    d = {k: int(after[k]) - int(before[k]) for k in COUNT_KEYS}
    n = int(answers.get("ok", 0))
    problems = []
    if applicable:
        if int(answers.get("real", 0)) == 0:
            problems.append("没有一次真票据作答")
        if int(answers.get("derived", 0)):
            problems.append(f"有 {answers['derived']} 次作答用了派生票据（写路径可用时真路径应为 100%）")
        if not n == d["counted"] == d["response_evidence"]:
            problems.append(f"作答 {n}、合成题计次增量 {d['counted']}、作答证据行增量 {d['response_evidence']} 不相等")
        if d["counted_without_evidence"]:
            problems.append(f"{d['counted_without_evidence']} 次计次无对应作答证据（题库自练开始即计数：计次之后学习服务失败）")
        if d["evidence_without_count"]:
            problems.append(f"{d['evidence_without_count']} 行作答证据无对应计次（落库了却没计次）")
        if d["counted_all"] != d["counted"] + d["counted_without_evidence"]:
            problems.append(f"{d['counted_all'] - d['counted'] - d['counted_without_evidence']} 次计次关联到合成题以外的作答")
        if mastery is not None:
            problems += mastery_problems(n, mastery)
    real = int(answers.get("real", 0)) + int(answers.get("derived", 0))
    extra = {} if mastery is None else {"recompute_units": mastery.get("recompute_units"), "tasks": mastery.get("tasks"),
                                        "recompute_failures": dict(mastery.get("events") or {}),
                                        "triggers": mastery.get("triggers"), "task_writes": mastery.get("task_writes")}
    return {**extra, "answers_ok": n, "answers_real": int(answers.get("real", 0)), "answers_derived": int(answers.get("derived", 0)),
            "real_share": round(int(answers.get("real", 0)) / real, 4) if real else None,
            "counted_delta": d["counted"], "counted_all_delta": d["counted_all"],
            "response_evidence_delta": d["response_evidence"], "evidence_delta": d["evidence"],
            "evidence_per_answer": round(d["evidence"] / d["response_evidence"], 3) if d["response_evidence"] else None,
            "counted_without_evidence_delta": d["counted_without_evidence"], "evidence_without_count_delta": d["evidence_without_count"],
            "applicable": applicable, "ok": (not problems) if applicable else None, "problems": problems}


# ---------------- 掌握重算的测量（PLT-22；只读，服务源码不改、不加日志） ----------------
# 触发规则（learning/service.py _recompute_mastery → mastery/recompute.py after_ingest）：每次 grade / diagnose 新落库提交后
# （重放不触发），该科有掌握配置时同步重算这次作答涉及的单元（只按主节点；单元 = 当前已发布地图里打了标的题的主节点 × 层次）。
# 每个单元一次 recompute()：拿单元锁后读一次当前记录——这条读语句只在重算里用（mastery/store.py current_estimate），
# 所以它在 pg_stat_statements 里的调用数 = 单元重算次数（含撞唯一索引后的一次重试；重试在日志里不可见，见 README）。
# 重算在 learning_tx 里执行（SET LOCAL ROLE starbuddy_learning），pg_stat_statements 记在 starbuddy_learning 名下而不是登录角色
# learning_app，所以这里按语句文本取、不按角色过滤（只有学习服务执行这条语句）。
# 本栈只有 SYNP-* 合成题在合成地图里打了标：学生旅程的数学批改、诊断题也会触发 after_ingest，但不涉及任何单元，不读这条语句。
# 因此每次合成题作答恰好重算一个单元：作答 = 单元重算次数。
RECOMPUTE_STATEMENT = ("SELECT id::text, state::text, p_mastery, confidence, next_retest_at, algorithm_version::text, "
                       "map_version_id::text, evidence_ids::text[] FROM platform.mastery_estimate")
RECOMPUTE_CALLS_SQL = r"""SELECT coalesce(sum(s.calls), 0)::bigint FROM loadtest_ext.pg_stat_statements s
 WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
   AND starts_with(regexp_replace(s.query, '\s+', ' ', 'g'), $1)"""
MASTERY_EVENTS = ("mastery_after_ingest_timeout", "mastery_after_ingest_failed", "mastery_recompute_failed")


async def recompute_calls_now(admin, enabled: bool) -> int | None:
    """此刻 pg_stat_statements 里单元重算读语句的累计调用数（只读、只在一次性实例）；没打开 pg_stat_statements 为空"""
    if not enabled:
        return None
    return int(await admin.fetchval(RECOMPUTE_CALLS_SQL, RECOMPUTE_STATEMENT))


def stage_diffs(marks: Sequence[int | None]) -> list[int] | None:
    """阶段边界上的累计值 → 各阶段增量；任一边界没取到为空"""
    if len(marks) < 2 or any(x is None for x in marks):
        return None
    return [int(marks[i + 1]) - int(marks[i]) for i in range(len(marks) - 1)]


def mastery_log_events(lines: Iterable[str]) -> dict[str, int]:
    """学习服务日志里重算失败 / 超时的事件数（只读 event 字段；这些事件只记学科、单元 ID 与错误类名，不含学生）"""
    c: Counter = Counter()
    for line in lines:
        i = line.find("{")
        if i < 0 or '"mastery_' not in line:
            continue
        try:
            rec = json.loads(line[i:])
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("event") in MASTERY_EVENTS:
            c[rec["event"]] += 1
    return {k: c[k] for k in MASTERY_EVENTS}


def mastery_problems(answers: int, m: Mapping) -> list[str]:
    """PLT-22 的追加判定：作答 = 单元重算次数；复测任务全部能证明由重算写出（推断，依赖两个触发器存在且启用）；
    任务表没有非服务写入（直接证据，pg_stat_statements）；重算没有失败或超时（失败的单元留给对账，掌握记录与作答不再一致）"""
    out = []
    r = m.get("recompute_units")
    if r is None:
        out.append("单元重算次数未采集（pg_stat_statements 不可用）")
    elif int(r) != answers:
        out.append(f"作答 {answers} 与触发的单元重算次数 {r} 不相等")
    t = m.get("tasks") or {}
    if t.get("unproven"):
        out.append(f"{t['unproven']} 条复测任务不能证明由重算写出（工具直写或来源不明）")
    trg = m.get("triggers")
    if trg is not None and not trg.get("ok"):
        out.append("来源推断依赖的触发器缺失或停用：" + "、".join(k for k, v in trg["triggers"].items() if not v))
    tw = m.get("task_writes")
    if tw is None:
        out.append("任务表写入的直接证据未采集（pg_stat_statements 不可用）")
    elif tw.get("non_service_calls"):
        out.append(f"任务表有 {tw['non_service_calls']} 次非学习服务重算语句的写入")
    bad = {k: v for k, v in (m.get("events") or {}).items() if v}
    if bad:
        out.append("重算失败 / 超时：" + "、".join(f"{k}×{v}" for k, v in sorted(bad.items())) + "（这些单元留给对账）")
    return out


# 写出的掌握记录（压测窗口内、本轮合成学生、节点键）：状态、前一条记录的状态，以及按提交时间戳（track_commit_timestamp，
# 只在一次性实例打开）算的两段耗时——
#   unit_ms：单元重算事务（computed_at = 事务开始的 now() → 事务提交）；
#   after_ms：作答后重算（这名学生最近一次在此之前提交的合成题作答证据的落库提交 → 这条记录的重算事务提交；
#             含 after_ingest 预读地图与证据的那个只读事务）。
# 提交时间戳取写入这条记录的事务：记录被下一条取代时它自己的 xmin 变成取代事务，所以取“取代它的前一条记录”的 xmin
# （同一事务里先改前一条、再插这一条）；没有前一条的当前记录取自己的 xmin；没有前一条、又已被取代的取不到（样本里少这一条）。
# 没写出新记录的重算（变化低于写入阈值）不在样本里：样本数 ≤ 单元重算次数，报告并列两个数。
MASTERY_ROWS_SQL = """WITH r AS (
  SELECT e.id, e.student_id, e.state::text AS state, e.computed_at, p.state::text AS prev_state,
         COALESCE(pg_xact_commit_timestamp(p.xmin), CASE WHEN e.is_current THEN pg_xact_commit_timestamp(e.xmin) END) AS committed
    FROM platform.mastery_estimate e
    LEFT JOIN platform.mastery_estimate p ON p.superseded_by = e.id
   WHERE e.student_id = ANY($1::uuid[]) AND e.node_id IS NOT NULL AND e.computed_at >= $2::timestamptz)
SELECT r.state, r.prev_state, r.computed_at,
       extract(epoch FROM r.committed - r.computed_at) * 1000 AS unit_ms,
       extract(epoch FROM r.committed - (
         SELECT max(pg_xact_commit_timestamp(ev.xmin)) FROM platform.attempt a
           JOIN platform.evidence_event ev ON ev.evidence_id = a.response_evidence_id
          WHERE a.student_id = r.student_id AND a.item_id LIKE 'SYNP-%' AND pg_xact_commit_timestamp(ev.xmin) <= r.computed_at
       )) * 1000 AS after_ms
  FROM r ORDER BY r.computed_at"""
MASTERY_STATES_SQL = """SELECT e.state::text AS state, p.state::text AS prev_state, e.computed_at, NULL::float8 AS unit_ms,
       NULL::float8 AS after_ms
  FROM platform.mastery_estimate e LEFT JOIN platform.mastery_estimate p ON p.superseded_by = e.id
 WHERE e.student_id = ANY($1::uuid[]) AND e.node_id IS NOT NULL AND e.computed_at >= $2::timestamptz
 ORDER BY e.computed_at"""


async def mastery_rows(admin, students: Sequence[str], since) -> list[dict]:
    """只读（管理员连接、只在一次性库）；track_commit_timestamp 没打开时耗时为空、只给状态"""
    on = await admin.fetchval("SELECT current_setting('track_commit_timestamp') = 'on'")
    rows = await admin.fetch(MASTERY_ROWS_SQL if on else MASTERY_STATES_SQL, list(students), since)
    return [{"state": r["state"], "prev_state": r["prev_state"], "computed_at": r["computed_at"],
             "unit_ms": None if r["unit_ms"] is None else float(r["unit_ms"]),
             "after_ms": None if r["after_ms"] is None else float(r["after_ms"])} for r in rows]


def stage_of_time(t, bounds: Sequence) -> int:
    """数据库时刻所在阶段：bounds = 各阶段边界的数据库时刻（开始、每个阶段结束）；之前算第一段，之后（收尾）算最后一段"""
    n = max(1, len(bounds) - 1)
    for i in range(n):
        if i + 1 < len(bounds) and t < bounds[i + 1]:
            return i
    return n - 1


def _dist(xs: Sequence[float]) -> dict:
    return {"p50_ms": percentile(xs, 0.5), "p95_ms": percentile(xs, 0.95), "max_ms": max(xs) if xs else None}


def mastery_timing(rows: Sequence[Mapping], bounds: Sequence, labels: Sequence[str]) -> dict:
    """写出记录的重算耗时：总体与按墙钟阶段（computed_at 所在阶段）的样本数、单元事务与作答后重算的 p50 / p95 / 最大"""
    def block(rs: Sequence[Mapping]) -> dict:
        unit = [float(r["unit_ms"]) for r in rs if r.get("unit_ms") is not None]
        after = [float(r["after_ms"]) for r in rs if r.get("after_ms") is not None]
        return {"written": len(rs), "timed": len(unit), "unit": _dist(unit), "after_answer": _dist(after)}

    out = {"overall": block(rows), "by_stage": []}
    if len(bounds) >= 2:
        groups: dict[int, list] = {}
        for r in rows:
            groups.setdefault(stage_of_time(r["computed_at"], bounds), []).append(r)
        for i in range(len(bounds) - 1):
            out["by_stage"].append({"stage": i, "label": labels[i] if i < len(labels) else str(i), **block(groups.get(i, []))})
    return out


def state_changes(rows: Iterable[Mapping]) -> dict:
    """写出的掌握记录里门槛状态的变化：“前一状态→新状态”计数（没有前一条记作“无”）；状态没变的写入（只是 p / 置信度变了）另计"""
    changes: Counter = Counter()
    same = 0
    for r in rows:
        prev = r.get("prev_state") or "无"
        if prev == r["state"]:
            same += 1
        else:
            changes[f"{prev}→{r['state']}"] += 1
    return {"changed": sum(changes.values()), "unchanged_state": same, "by_transition": dict(sorted(changes.items()))}


def run_ok(write_path: Mapping | None, require: bool) -> bool:
    """写路径对退出码的影响：一致性核对不通过一律失败；--require-write-path 时写路径没接上（或根本没打开再练）也失败"""
    w = write_path or {}
    if (w.get("check") or {}).get("ok") is False:
        return False
    return w.get("enabled") is True or not require


# ---------------- 写路径各环节耗时（各服务自己的访问日志，只读方法名、状态码、耗时三个字段） ----------------
SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("learning", "practice:next", "学习服务 再练取题（含知识服务与 generate_variation）"),
    ("learning", "retest:next", "学习服务 复测取题（同上）"),
    ("knowledge", "item.similar", "知识服务 item.similar"),
    ("knowledge", "item.search", "知识服务 item.search"),
    ("aya", "generate_variation", "阿鸦 generate_variation（选题）"),
    ("learning", "retest:check", "学习服务 复测作答预检（计次之前）"),
    ("learning", "practice:answer", "学习服务 再练作答（验票、判分、证据落库）"),
    ("learning", "retest:answer", "学习服务 复测作答（同上）"),
    ("aya", "grade", "阿鸦 grade（验证器；含学生旅程的数学批改）"),
)


def _records(lines: Iterable[str], field: str) -> Iterable[tuple[str, int, float]]:
    for line in lines:
        i = line.find("{")
        if i < 0:
            continue
        try:
            rec = json.loads(line[i:])
        except ValueError:
            continue
        if not isinstance(rec, dict) or not isinstance(rec.get(field), str):
            continue
        status, ms = rec.get("status"), rec.get("duration_ms")
        if isinstance(status, int) and isinstance(ms, (int, float)):
            yield rec[field], status, float(ms)


def segments(learning: Iterable[str], knowledge: Iterable[str], aya: Iterable[str]) -> list[dict]:
    """按环节：次数、非 2xx 次数、p50 / p95 / 最大毫秒（最近秩）。没有样本的环节不列"""
    got: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for src, lines, field in (("learning", learning, "method"), ("knowledge", knowledge, "tool"), ("aya", aya, "method")):
        for name, status, ms in _records(lines, field):
            got.setdefault((src, name), []).append((status, ms))
    out = []
    for src, name, label in SEGMENTS:
        xs = got.get((src, name))
        if not xs:
            continue
        ms = [m for _, m in xs]
        out.append({"source": src, "name": name, "label": label, "count": len(xs),
                    "not_2xx": sum(1 for s, _ in xs if not 200 <= s < 300),
                    "p50_ms": percentile(ms, 0.5), "p95_ms": percentile(ms, 0.95), "max_ms": max(ms)})
    return out


def read_from(path: Path, offset: int) -> list[str]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", errors="replace").splitlines()


def size(path: Path | None) -> int:
    return path.stat().st_size if path is not None and path.exists() else 0
