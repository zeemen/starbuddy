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
- 到期条目：每个合成学生经学习服务的 grade 写入路径（与 BFF 同一服务令牌、键盘通道）先做错一道 N1 的题（再练条目到期，
  首轮间隔 0 小时），再做对一道 N2 的题（复测参照题），并以学习服务角色种一条已到期的平台内验证任务（verify_in_platform，
  复测条目；手工种入，不是 KGE-04 调度产生的；不属于复测轮，不需要掌握记录）。这些是压测前准备数据的种子：
  种子 grade 不经 BFF 与计量（不预留、不结算、不计次），不进压测指标；
- 一致性核对：压测前后各取一次计数（只取本轮合成学生；计次经幂等键关联到 SYNP-* 题的作答），
  作答次数（真票据 2xx）= 合成题的计次增量（platform.practice_log）= 作答证据行增量（platform.attempt 的作答证据），
  且没有“计次无对应作答证据”与“作答证据无计次”。题库自练的计次口径是“开始即计数、没有释放”（BFF 先 meter_practice 再调学习服务，
  specs/metering-v2 偏差 13）：学习服务在计次之后失败时会留下“计次无对应作答证据”，这是设计口径而非计量故障；
  无故障注入的本机压测里应为 0，出现即判不通过并在报告列出。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

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
    return None


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


RETEST_TASK_SQL = """INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at)
VALUES ($1::uuid, 'aya', $2, $3, 'verify_in_platform', now() - interval '1 hour')"""


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


async def seed_students(client, learning_url: str, credential: str, admin, students: Sequence[str], seed: int,
                        curriculum: Mapping, concurrency: int = 8) -> dict:
    """每个合成学生：做错 N1 一道（再练到期）、做对 N2 一道（复测参照题），再以学习服务角色种一条已到期的平台内验证任务。
    经学习服务 grade 写入路径（BFF 服务令牌）；不经 BFF 与计量（不预留、不结算、不计次），只用于准备数据，不计入压测指标。
    复测任务是手工种入的（不是 KGE-04 调度）。任何一笔没成功、判分结论不符即失败。"""
    # 先核对写入目标，再发任何请求：学习服务只能是本机回环地址；库只能是本机 Unix socket 上的一次性测试库（规则 9）
    check_local_url(learning_url)
    if not await admin.fetchval("SELECT current_database() ~ '^starbuddy_test' AND inet_server_addr() IS NULL"):
        raise RuntimeError("写路径种子只能写本机 Unix socket 上的一次性测试库（starbuddy_test*）")
    sem = asyncio.Semaphore(concurrency)
    verdicts: Counter = Counter()
    now = datetime.now(timezone.utc).isoformat()

    async def one(student: str, label: str, item: str, answer: dict) -> None:
        key = uuid4_shaped("seed", seed, student, label)
        body = seed_envelope(student, item, answer, key, uuid4_shaped("seed-usage", seed, student, label), curriculum, now)
        async with sem:
            r = await client.post(f"{learning_url}/internal/v1/grade", json=body, headers={"authorization": f"Bearer {credential}"},
                                  timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"写路径种子 grade 失败：{r.status_code}")
        verdicts[f"{label}:{_verdict(r.json())}"] += 1

    await asyncio.gather(*(one(s, label, item, ans) for s in students
                           for label, (item, ans) in (("practice", PRACTICE_SEED), ("retest", RETEST_SEED))))
    check_seed_verdicts(verdicts, len(students))
    async with admin.acquire() as c, c.transaction():
        await c.execute("SET LOCAL ROLE starbuddy_learning")
        await c.executemany(RETEST_TASK_SQL, [(s, N2, LEVEL) for s in students])
    return {"students": len(students), "grades": sum(verdicts.values()), "verdicts": dict(sorted(verdicts.items())),
            "retest_tasks": len(students)}


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


def answers_ok(paths: Mapping[tuple[str, str, str], int]) -> dict[str, int]:
    """驱动器记的作答：真票据且 2xx 的次数、真票据总数、派生票据次数（Recorder.paths）"""
    out = {"ok": 0, "real": 0, "derived": 0}
    for (step, kind, outcome), n in paths.items():
        if step not in ANSWER_STEPS:
            continue
        out[kind] += n
        if kind == "real" and outcome == "ok":
            out["ok"] += n
    return out


def consistency(answers: Mapping[str, int], before: Mapping[str, int], after: Mapping[str, int], *, applicable: bool = True) -> dict:
    """判定口径（全部是压测前后的增量，只取本轮合成学生）：
    1. 作答次数（驱动器记的真票据 2xx）= 合成题范围的计次增量（经幂等键关联到 SYNP-* 作答的 practice_log 行）
       = SYNP-* 的作答证据行增量（每次作答一行）；
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
    real = int(answers.get("real", 0)) + int(answers.get("derived", 0))
    return {"answers_ok": n, "answers_real": int(answers.get("real", 0)), "answers_derived": int(answers.get("derived", 0)),
            "real_share": round(int(answers.get("real", 0)) / real, 4) if real else None,
            "counted_delta": d["counted"], "counted_all_delta": d["counted_all"],
            "response_evidence_delta": d["response_evidence"], "evidence_delta": d["evidence"],
            "evidence_per_answer": round(d["evidence"] / d["response_evidence"], 3) if d["response_evidence"] else None,
            "counted_without_evidence_delta": d["counted_without_evidence"], "evidence_without_count_delta": d["evidence_without_count"],
            "applicable": applicable, "ok": (not problems) if applicable else None, "problems": problems}


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
