"""PLT-21 写路径补全（第一步）：合成内容包、种子 SQL、到期条目信封、一致性核对与环节耗时的纯函数；
以及一次性 Postgres 上的种子与真知识服务启动（只读角色、响应按 packages/contracts 知识服务 OpenAPI 校验）。全部合成数据。"""
from __future__ import annotations

import json
import os
import re

import httpx
import pytest

from loadtest import bodies, writepath as W

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
BASE = json.loads((W.AYA / "content/pack.json").read_text(encoding="utf-8"))
FIXTURE = W.load_fixture()


# ---------------- 合成内容包 ----------------
def test_synthetic_pack_appends_admitted_objective_items_without_touching_base():
    before = json.dumps(BASE, sort_keys=True)
    pack = W.synthetic_pack(BASE, FIXTURE)
    assert json.dumps(BASE, sort_keys=True) == before                      # 不改输入
    added = pack["items"][len(BASE["items"]):]
    assert [it["id"] for it in added] == [i for i, _, _ in W.ITEMS]
    assert pack["items"][:len(BASE["items"])] == BASE["items"]             # 真实题原样
    for it in added:
        assert it["admission"] == "admitted" and it["source"]["license_status"] == "cleared" and it["reviewStatus"] == "Q2"
        assert it["admission_review"]["review_level"] == "Q2" and len(it["admission_review"]["review_ids"]) == 1
        assert "stem" not in it and "option_content" not in it              # 题面不进内容包
        f = FIXTURE[it["id"]]
        assert it["format"] == f["format"]
        if f["format"] == "fill":
            assert it["answer"] == [W.FILL_ANSWER]
        else:
            assert it["answer"] == [f["options"][0]["key"]] and [o["key"] for o in it["options"]] == [o["key"] for o in f["options"]]
    reviewed = sum(1 for it in pack["items"] if it.get("admission_review"))
    assert BASE["admission_decisions"] is None and reviewed == len(W.ITEMS)
    dec = pack["admission_decisions"]
    assert dec["decisions"] == reviewed                                    # 与阿鸦 indexPack 的口径一致
    # 合成占位：不冒充真实准入依据（占位导出编号、全零摘要）
    assert dec["digest"] == W.PLACEHOLDER_DIGEST == "sha256:" + "0" * 64 and dec["export_ref"] == W.PLACEHOLDER_EXPORT
    assert "PLACEHOLDER" in dec["export_ref"] and dec["format"] == "aya-admission-decisions/1"


def test_synthetic_pack_refuses_to_mix_with_real_admission_decisions():
    real = {**BASE, "admission_decisions": {"format": "aya-admission-decisions/1", "export_ref": "EXP-1", "exported_at": "x",
                                            "digest": "sha256:" + "1" * 64, "decisions": 3}}
    with pytest.raises(ValueError, match="准入决定"):
        W.synthetic_pack(real, FIXTURE)


def test_driver_answers_match_synthetic_answers():
    """驱动器的作答（bodies.answer_for）对合成题一律是标准答案：判分确定（填空 "1"、选择题第一个选项）"""
    pack = W.synthetic_pack(BASE, FIXTURE)
    for item_id, _, _ in W.ITEMS:
        f = FIXTURE[item_id]
        view = {"format": f["format"], **({"options": f["options"]} if "options" in f else {})}
        got = bodies.answer_for(view)
        assert (got.get("text") and [got["text"]] or got["choice"]) == W.answer_of(pack, item_id)
    assert W.PRACTICE_SEED[1]["text"] not in W.answer_of(pack, W.PRACTICE_SEED[0])   # 种子：做错
    assert [W.RETEST_SEED[1]["text"]] == W.answer_of(pack, W.RETEST_SEED[0])          # 种子：做对


def test_synthetic_pack_refuses_subjective_or_duplicate_items():
    bad = {**FIXTURE, "SYNP-0001": {**FIXTURE["SYNP-0001"], "grading": "model_rubric"}}
    with pytest.raises(ValueError):
        W.synthetic_pack(BASE, bad)
    dup = {**W.synthetic_pack(BASE, FIXTURE), "admission_decisions": None}
    with pytest.raises(ValueError, match="已有 SYNP-0001"):
        W.synthetic_pack(dup, FIXTURE)


def test_units_have_new_family_candidates_and_sources():
    """每个单元：原题 / 参照题之外至少还有两个不同族的候选（复测只出没做过的族；再练优先新族）"""
    for node, source in ((W.N1, W.PRACTICE_SEED[0]), (W.N2, W.RETEST_SEED[0])):
        fams = {f for i, n, f in W.ITEMS if n == node}
        src_fam = next(f for i, n, f in W.ITEMS if i == source)
        assert next(n for i, n, _ in W.ITEMS if i == source) == node
        assert len(fams - {src_fam}) >= 2
    assert all(FIXTURE[i]["grading"] in ("validator", "deterministic_rubric") for i, _, _ in W.ITEMS)
    fam_nodes = {}
    for _, node, f in W.ITEMS:
        assert fam_nodes.setdefault(f, node) == node                      # 题目族只挂一个主节点


# ---------------- 种子 SQL ----------------
def test_seed_sql_goes_through_gates_and_guards():
    cur = BASE["curriculum"]
    sqls = {"admission": W.admission_sql(), "map": W.map_sql(), "publish": W.publish_sql(cur), "role": W.ROLE_SQL}
    for name, sql in sqls.items():
        assert "session_replication_role" not in sql, name                 # 不绕过发布与准入守卫
        assert "starbuddy_test" in sql and "inet_server_addr()" in sql, name  # 只认本机一次性库
    a = sqls["admission"]
    assert a.count("platform.sign_admission(") == len(W.ITEMS) and "platform.change_admission(" in a and "SET ROLE starbuddy_review" in a
    # 合成 staff：先断言两个 ID 原先不存在，插入不覆盖已有账号（DO NOTHING，不改状态）
    assert "ON CONFLICT (id) DO NOTHING" in a and "DO UPDATE" not in a
    assert a.index("合成 staff 账号 ID 已存在") < a.index("INSERT INTO platform.account")
    m = sqls["map"]
    assert "SET LOCAL ROLE starbuddy_kge" in m and "'published'" not in m   # 草稿，发布只经签字
    for item_id, node, fam in W.ITEMS:
        assert f"'{item_id}', '{node}', '{{}}', '{W.LEVEL}', '{W.family_id(fam)}'" in m
    p = sqls["publish"]
    assert p.count("kge.sign_review('map_version'") == 2 and W.STAFF[0] in p and W.STAFF[1] in p
    assert "kge.publish_map_version" in p and f"textbook_version = '{cur['textbook_version']}'" in p and "status = 'draft'" in p
    assert "SET status = 'disabled'" in p                                   # 不留可用的合成签字账号
    # 绑内容版本以学习服务角色执行（不用管理员绕 RLS）
    bind = p.index("UPDATE platform.curriculum_version")
    assert p.rindex("SET ROLE starbuddy_learning;", 0, bind) > p.rindex("RESET ROLE;", 0, bind)
    assert f"CREATE ROLE {W.KNOWLEDGE_ROLE} LOGIN IN ROLE starbuddy_kge_read;" in sqls["role"]


def test_knowledge_env_is_local_readonly_learning_only():
    from e2e_cohort.stack import ADAPTER_TOKENS, LEARNING_CREDENTIAL, ROUTE_TOKENS

    env = W.knowledge_env("postgresql://loadtest_knowledge@/starbuddy_test?host=/x", 8790, W.ROOT / "x")
    assert env["KNOWLEDGE_ENV"] == "test" and env["KNOWLEDGE_HOST"] == "127.0.0.1"
    assert [k for k in env if k.startswith("KNOWLEDGE_TOKEN_")] == ["KNOWLEDGE_TOKEN_LEARNING"]
    assert "KNOWLEDGE_PAGE_SNAPSHOT" not in env and "KNOWLEDGE_READ_CACHE" not in env
    cred = env["KNOWLEDGE_TOKEN_LEARNING"]
    assert len(cred) >= 32 and cred not in {LEARNING_CREDENTIAL, *ADAPTER_TOKENS.values(), *ROUTE_TOKENS.values()}


# ---------------- 到期条目的种子信封 ----------------
def test_seed_envelope_matches_plugin_grade_request():
    contract = pytest.importorskip("learning.contract")
    key = W.uuid4_shaped("seed", 1, "s", "practice")
    env = W.seed_envelope("5c1e0000-0000-4000-8000-0000000000c1", "SYNP-0001", {"text": "0"}, key, W.uuid4_shaped("u"),
                          BASE["curriculum"], "2026-10-18T02:00:00+00:00")
    assert set(env) == {"request", "ingest"} and env["ingest"] == {"channel": "keypad"}   # 用途缺省 practice，等级按键盘记 A
    assert contract.plugin_contract("1.0.0").validate("GradeRequest", env["request"]) == []
    assert env["request"]["rubric_version"] == W.RUBRIC and env["request"]["attempt"]["hint_level_used"] == 0
    assert UUID4.fullmatch(key) and UUID4.fullmatch(env["request"]["attempt"]["attempt_id"])
    assert W.uuid4_shaped("seed", 1, "s", "practice") == key and W.uuid4_shaped("seed", 2, "s", "practice") != key


@pytest.mark.parametrize("url,ok", [
    ("http://127.0.0.1:8788", True), ("http://127.0.0.1:8788/", True),
    ("http://localhost:8788", False), ("https://127.0.0.1:8788", False), ("http://10.0.0.5:8788", False),
    ("http://127.0.0.1", False), ("http://127.0.0.1:8788/x", False), ("http://u@127.0.0.1:8788", False),
    ("http://127.0.0.1.example.com:8788", False),
])
def test_seed_only_targets_local_learning(url, ok):
    if ok:
        W.check_local_url(url)
    else:
        with pytest.raises(RuntimeError, match="127.0.0.1"):
            W.check_local_url(url)


def test_seed_verdicts_must_match_exactly():
    W.check_seed_verdicts({"practice:incorrect": 3, "retest:correct": 3}, 3)
    for bad in ({"practice:incorrect": 2, "practice:correct": 1, "retest:correct": 3},
                {"practice:incorrect": 3, "retest:needs_review": 3}, {"practice:incorrect": 3}, {}):
        with pytest.raises(RuntimeError, match="判分结论不符"):
            W.check_seed_verdicts(bad, 3)


def test_seed_checks_target_before_any_request():
    """库或学习服务地址不对时，一笔种子都不发"""
    import asyncio

    class Admin:
        def __init__(self, local: bool):
            self.local = local

        async def fetchval(self, _sql):
            return self.local

    class Client:
        calls = 0

        async def post(self, *a, **kw):
            Client.calls += 1
            raise AssertionError("不应发请求")

    for url, admin, needle in (("http://10.0.0.1:1", Admin(True), "127.0.0.1"), ("http://127.0.0.1:1", Admin(False), "一次性测试库")):
        with pytest.raises(RuntimeError, match=needle):
            asyncio.run(W.seed_students(Client(), url, "c" * 40, admin, ["s"], 1, BASE["curriculum"]))
    assert Client.calls == 0


def test_run_ok_exit_code_rules():
    good = {"enabled": True, "check": {"ok": True}}
    assert W.run_ok(good, False) and W.run_ok(good, True)
    assert not W.run_ok({"enabled": True, "check": {"ok": False}}, False)
    assert W.run_ok({"enabled": False, "note": "x"}, False) and not W.run_ok({"enabled": False, "note": "x"}, True)
    assert W.run_ok(None, False) and not W.run_ok(None, True)
    assert W.run_ok({"enabled": True, "check": {"ok": None}}, True)       # 没开作答场景：不判定


# ---------------- 派生票据的预期在写路径可用时停用 ----------------
def test_answer_request_without_derived_expectation():
    body, codes = bodies.answer_request("practice", None, 7, 3, expect_derived=False)
    assert codes == frozenset() and body["ticket"] == bodies.fallback_ticket("practice", 7, 3)
    assert bodies.answer_request("practice", None, 7, 3) == (body, bodies.EXPECT_TICKET_INVALID)   # 缺省同 PLT-20


# ---------------- 一致性核对 ----------------
ZERO = dict.fromkeys(W.COUNT_KEYS, 0)
SEEDED = {**ZERO, "response_evidence": 20, "evidence": 40, "evidence_without_count": 20}   # 种子：10 人 × 2 道，不计次


def after(n: int, **kw) -> dict:
    out = {"counted": n, "counted_all": n, "response_evidence": 20 + n, "evidence": 40 + 2 * n, "counted_without_evidence": 0,
           "evidence_without_count": 20}
    out |= kw
    if "counted_all" not in kw:
        out["counted_all"] = out["counted"] + out["counted_without_evidence"]
    return out


def test_consistency_passes_when_answers_counts_and_evidence_agree():
    c = W.consistency({"ok": 18, "real": 18, "derived": 0}, SEEDED, after(18))
    assert c["ok"] is True and c["problems"] == []
    assert (c["answers_ok"], c["counted_delta"], c["response_evidence_delta"], c["evidence_delta"]) == (18, 18, 18, 36)
    assert c["evidence_per_answer"] == 2.0 and c["real_share"] == 1.0


@pytest.mark.parametrize("answers,post,needle", [
    ({"ok": 17, "real": 18, "derived": 0}, after(18), "不相等"),                                   # 一次作答非 2xx 却计了次、落了库
    ({"ok": 18, "real": 18, "derived": 0}, after(18, counted_without_evidence=1), "计次无对应作答证据"),
    ({"ok": 18, "real": 18, "derived": 0}, after(18, response_evidence=39, evidence_without_count=21), "作答证据无对应计次"),
    ({"ok": 18, "real": 18, "derived": 0}, after(18, counted_all=19), "合成题以外"),        # 计次落到了非合成题的作答上
    ({"ok": 16, "real": 16, "derived": 2}, after(16), "派生票据"),
    ({"ok": 0, "real": 0, "derived": 0}, SEEDED, "没有一次真票据作答"),
])
def test_consistency_flags_each_mismatch(answers, post, needle):
    c = W.consistency(answers, SEEDED, post)
    assert c["ok"] is False and any(needle in p for p in c["problems"]), c["problems"]


def test_consistency_not_judged_without_answer_journey():
    c = W.consistency({"ok": 0, "real": 0, "derived": 0}, SEEDED, SEEDED, applicable=False)
    assert c["ok"] is None and c["problems"] == [] and c["real_share"] is None


def test_answers_ok_reads_only_answer_steps():
    paths = {("practice.answer", "real", "ok"): 5, ("retest.answer", "real", "ok"): 4, ("retest.answer", "real", "http_5xx"): 1,
             ("practice.answer", "derived", "http_4xx"): 2, ("practice.next", "real", "ok"): 9, ("plan.done", "derived", "expected"): 3}
    assert W.answers_ok(paths) == {"ok": 9, "real": 10, "derived": 2}


# ---------------- 环节耗时 ----------------
def test_segments_parse_only_method_status_duration():
    learning = ['INFO {"method": "practice:next", "status": 200, "duration_ms": 30}', '{"method": "practice:next", "status": 503, "duration_ms": 10}',
                '{"method": "practice:answer", "status": 200, "duration_ms": 50}', "not json", '{"event": "practice_queue"}',
                '{"method": "retest:next", "status": "200", "duration_ms": 5}']
    knowledge = ['{"tool": "item.similar", "caller": "learning", "status": 200, "duration_ms": 4}', "INFO: Started server process"]
    aya = ['{"method": "generate_variation", "status": 200, "duration_ms": 2}', '{"event": "listening", "port": 1}']
    seg = {s["name"]: s for s in W.segments(learning, knowledge, aya)}
    assert set(seg) == {"practice:next", "practice:answer", "item.similar", "generate_variation"}
    assert seg["practice:next"]["count"] == 2 and seg["practice:next"]["not_2xx"] == 1 and seg["practice:next"]["p95_ms"] == 30.0
    assert [s["name"] for s in W.segments(learning, knowledge, aya)] == ["practice:next", "item.similar", "generate_variation",
                                                                          "practice:answer"]


def test_read_from_offset(tmp_path):
    p = tmp_path / "x.log"
    p.write_text("a\nb\n", encoding="utf-8")
    off = W.size(p)
    with open(p, "a", encoding="utf-8") as f:
        f.write("c\n")
    assert W.read_from(p, off) == ["c"] and W.read_from(tmp_path / "missing", 0) == [] and W.size(None) == 0


# ---------------- 报告 ----------------
def test_report_section_states_result_and_disabled_reason():
    from loadtest.report import render_write_path

    assert render_write_path(None) == []
    off = "\n".join(render_write_path({"enabled": False, "note": "没有 services/knowledge/.venv（知识服务）"}))
    assert "未接上" in off and "知识服务" in off and "派生票据按“预期”计" in off
    check = W.consistency({"ok": 4, "real": 4, "derived": 0}, SEEDED, after(4))
    w = {"enabled": True, "note": None, "content": {"map_version": W.MAP_VERSION, "items": len(W.ITEMS), "units": [W.N1, W.N2],
                                                     "level": W.LEVEL, "check": {}},
         "seeded": {"students": 2, "grades": 4, "verdicts": {"practice:incorrect": 2, "retest:correct": 2}, "retest_tasks": 2},
         "check": check, "segments": W.segments(['{"method": "practice:answer", "status": 200, "duration_ms": 9}'], [], [])}
    text = "\n".join(render_write_path(w))
    assert "**通过**" in text and "真路径占比 100.0%" in text and "合成题计次增量" in text and "| 4 / 4 |" in text
    assert "学习服务 再练作答" in text and "practice:incorrect×2" in text
    for label in ("手工种入，不是 KGE 调度", "不经计量", "合成占位摘要，不是真实准入依据", "开始即计数", "候选版开关"):
        assert label in text, label
    bad = "\n".join(render_write_path({**w, "check": W.consistency({"ok": 3, "real": 4, "derived": 0}, SEEDED, after(4))}))
    assert "**不通过**" in bad and "问题：" in bad


# ---------------- 样例可复现 ----------------
SAMPLE = W.ROOT / "tools/loadtest/sample"


def test_sample_peak2x_markdown_rerenders_from_sample_json():
    """sample/peak2x.md 是 sample/peak2x.json 的渲染结果（逐字节相等；没有手工改动），且两份都不含模型标识、带首段标注"""
    from loadtest.report import PEAK_BANNER, render_markdown

    md = (SAMPLE / "peak2x.md").read_text(encoding="utf-8")
    raw = (SAMPLE / "peak2x.json").read_text(encoding="utf-8")
    assert render_markdown(json.loads(raw)) == md
    for text in (md, raw):
        assert not re.search(r"(model:[a-z0-9-]+/|fake-[abx]\b|fake-(chat|reasoner|guard|batch))", text)
        assert PEAK_BANNER in text
    r = json.loads(raw)
    assert r["write_path"]["enabled"] is True and r["write_path"]["check"]["ok"] is True


def test_render_does_not_depend_on_dict_order():
    """由 JSON（sort_keys）读回的报告与原报告渲染相同：接口、旅程、闸门分档按固定顺序列出"""
    from loadtest.report import render_markdown

    r = json.loads((SAMPLE / "peak2x.json").read_text(encoding="utf-8"))

    def rev(d):
        return {k: rev(v) for k, v in reversed(list(d.items()))} if isinstance(d, dict) else [rev(x) for x in d] if isinstance(d, list) else d

    assert render_markdown(rev(r)) == render_markdown(r)


# ---------------- 一次性库：种子 + 真知识服务启动 ----------------
@pytest.fixture(scope="module")
def seeded_pg(tmp_path_factory):
    from e2e_cohort.stack import missing_prerequisites, register_content

    from loadtest import seed

    strict = os.environ.get("CONTRACT_STRICT") == "1"
    why = missing_prerequisites() or W.missing()
    if why:
        if strict:
            pytest.fail(why)
        pytest.skip(why)
    from pgharness import EphemeralPG

    try:
        pg = EphemeralPG()
    except FileNotFoundError as e:
        if strict:
            raise
        pytest.skip(str(e))
    pg.start()
    try:
        pg.psql(seed.setup_sql())
        register_content(pg)
        out = W.prepare_content(pg, tmp_path_factory.mktemp("write-path"))
        yield pg, out
    finally:
        pg.stop()


def test_seed_publishes_map_admits_items_and_binds_content(seeded_pg):
    pg, out = seeded_pg
    assert out["check"] == {"map_published": True, "tags_approved": len(W.ITEMS), "items_admitted": len(W.ITEMS), "cv_bound": 1,
                            "active_staff": 0}
    # 准入与发布都留了签字记录（经闸门，而不是直接写表）
    got = json.loads(pg.psql(f"""SELECT json_build_object(
      'admission_applied', (SELECT count(*) FROM platform.release_record WHERE line = 'admission' AND action = 'apply' AND item_id LIKE 'SYNP-%'),
      'map_reviews', (SELECT count(*) FROM kge.content_review WHERE object_type = 'map_version' AND object_id = '{W.MAP_ID}'))""").strip())
    assert got["admission_applied"] == len(W.ITEMS) and got["map_reviews"] >= 3    # 两次签字 + 发布
    assert pg.psql(f"SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = '{W.KNOWLEDGE_ROLE}'").strip() == "f"
    assert json.loads(out["pack"].read_text(encoding="utf-8"))["curriculum"] == out["curriculum"]


def test_real_knowledge_service_serves_synthetic_items_by_contract(seeded_pg, tmp_path):
    contract = pytest.importorskip("learning.contract")
    from loadtest.stack import free_port, process

    pg, _ = seeded_pg
    port = free_port()
    cache = tmp_path / "kcache"
    cache.mkdir(mode=0o700)
    env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"} | W.knowledge_env(
        f"postgresql://{W.KNOWLEDGE_ROLE}@/{pg.db}?host={pg.work}", port, cache)
    with process([str(W.KNOWLEDGE_PY), "-m", "knowledge.service"], W.KNOWLEDGE, env, tmp_path / "knowledge.log", port, "知识服务"):
        ctx = {"request_id": "5c1e0000-0000-4000-8000-0000000000f1", "contract_version": "0.9.0", "caller": "learning",
               "data_class": "L1", "map_version": W.MAP_VERSION}
        auth = {"authorization": f"Bearer {W.KNOWLEDGE_CREDENTIAL}"}
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30) as c:
            sim = c.post("/v0/item.similar", json={"context": ctx, "item_ref": "aya:SYNP-0001", "limit": 10}, headers=auth)
            search = c.post("/v0/item.search", json={"context": ctx, "subject": "aya", "node_id": W.N2, "cognitive_level": W.LEVEL,
                                                    "limit": 20}, headers=auth)
            denied = c.post("/v0/item.similar", json={"context": {**ctx, "caller": "bff"}, "item_ref": "aya:SYNP-0001"}, headers=auth)
    kc = contract.knowledge_contract()
    assert sim.status_code == 200 and kc.validate("ItemSimilarResponse", sim.json()) == []
    assert search.status_code == 200 and kc.validate("ItemSearchResponse", search.json()) == []
    assert {x["item"]["item_ref"] for x in sim.json()["result"]["items"]} == {f"aya:{i}" for i, n, _ in W.ITEMS if n == W.N1} - {"aya:SYNP-0001"}
    assert {x["item_ref"] for x in search.json()["result"]["items"]} == {f"aya:{i}" for i, n, _ in W.ITEMS if n == W.N2}
    assert denied.status_code == 403                                      # 令牌决定 caller：不一致即拒绝
