"""月度计划的纯函数测试（PLT-14 第 2 步，insight/monthly.py；不连库、合成数据）：
- 配置与模板库：合成或未签字只允许 test；模板文案只收 {{TODO_COPY:…}} 占位；模板库过禁用推断词表与“名次 / 概率 / 承诺”词表（反例在内存里注入）；
- “名次 / 概率 / 承诺”最小词表自检（每类禁止示例被本类命中，允许示例两张词表都不命中）；
- 优先知识点：按 缺口 × 权重 × 频率 × 关注学科系数 排序，取前 N；每项引用一条“待点亮”的知识掌握判断，没有可引用的判断就不出；
- 学生段不含系数、优先级与任何来自家长诉求的字段；家长注意事项按计划事实选取、不超过 3 条；
- 上月回顾只引用上个月新建的迁移判断；分差只在同一满分口径、有对家长可见的考试判断时给出；
- 同一输入重放逐字节相同，输入顺序无关；输入变了摘要跟着变。
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from insight import monthly as M
from insight.claims import MasteryRow
from insight.policy import PolicyError
from insight.shared import forbidden_matcher
from support import NODE_A, NODE_B, NODE_C, STUDENT, make_policy

POLICY = make_policy(visible_subjects=["aya", "wenli"])
CFG = M.synthetic_monthly_config()
TPL = M.load_templates(env="test")
FM = forbidden_matcher()
MONTH = date(2026, 10, 1)
AS_OF = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)
TEXT_M = "【占位模板·未审核】{node_id}｜{level}｜{gate}"
TEXT_T = "【占位模板·未审核】迁移｜{node_id}｜{level}"
TEXT_E = "【占位模板·未审核】对账｜{position}"
NODE_W = "CHN.SYN.READ.MAIN"
MK, TK = POLICY.templates["node_mastery"].key, POLICY.templates["node_transfer"].key
GOAL_1, GOAL_2 = "9a540000-0000-4000-8000-000000000001", "9a540000-0000-4000-8000-000000000002"
REC = "9a540000-0000-4000-8000-0000000000e1"


def cid(n: int) -> str:
    return f"c1a54000-0000-4000-8000-{n:012d}"


def row(n: int, node: str, gate: str, level: str = "apply", subject: str = "aya") -> MasteryRow:
    return MasteryRow(f"6d540000-0000-4000-8000-{n:012d}", STUDENT, subject, node, level, gate, 0.6, 0.5,
                      ("e1000000-0000-4000-8000-000000000001",), "kge-mastery@0.1.0.syn", AS_OF - timedelta(days=2))


def claim(n: int, node: str, gate: str, *, level: str = "apply", key: str = MK, version: int = 1, status: str = "hypothesis",
          vis: tuple[str, ...] = ("agent", "student", "guardian"), source: str = "inferred", subject: str = "aya",
          expires: datetime = AS_OF + timedelta(days=30), created: datetime = AS_OF - timedelta(days=1), text: str | None = TEXT_M,
          target_type: str = "node", value: dict | None = None, star_at: datetime | None = None) -> M.PlanClaim:
    return M.PlanClaim(cid(n), subject, target_type, node, key, version, status, value if value is not None else {"level": level, "gate": gate},
                       vis, source, expires, created, text, hid(n), star_at)


def hid(n: int) -> str:
    return f"4a540000-0000-4000-8000-{n:012d}"


def base_inputs(**kw) -> M.MonthlyInputs:
    rows = (row(1, NODE_A, "execute"), row(2, NODE_B, "stable"), row(3, NODE_C, "understand"))
    claims = (claim(1, NODE_A, "execute"), claim(2, NODE_B, "stable"), claim(3, NODE_C, "understand"))
    args = dict(student=STUDENT, month=MONTH, as_of=AS_OF, mastery=rows, claims=claims)
    args.update(kw)
    return M.MonthlyInputs(**args)


def build(inp: M.MonthlyInputs, cfg: M.MonthlyConfig = CFG) -> dict:
    return M.build_monthly_plan(inp, POLICY, cfg, TPL, FM)


def nodes(plan: dict) -> list[str]:
    return [n["node_id"] for n in plan["student_plan"]["priority_nodes"]]


# ---------------------------------------------------------------- 配置、模板库与词表

def cfg_doc() -> dict:
    return json.loads((M.CONFIG_DIR / "monthly-plan.synthetic.json").read_text(encoding="utf-8"))


def tpl_doc() -> dict:
    return yaml.safe_load(M.TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_config_synthetic_only_in_test_and_values_checked():
    assert CFG.max_priority_nodes == 8 and CFG.focus_factor == 1.5 and CFG.minutes_per_session == 30  # 每次时长取今日计划配置
    with pytest.raises(PolicyError):
        M.parse_monthly_config(cfg_doc(), env="dev")
    for bad in ({"max_priority_nodes": 0}, {"focus_subject_factor": 0.5}, {"sessions_per_week": 8},
                {"gate_gap": {"unseen": 0.2, "understand": 0.5, "execute": 0.75, "stable": 1.0}}, {"extra": 1}):
        d = cfg_doc()
        d.update(bad)
        with pytest.raises(PolicyError):
            M.parse_monthly_config(d, env="test")


def test_templates_are_placeholders_and_unsigned_only_in_test():
    assert set(TPL.fixed) == set(M.COPY.values())
    for key, text in TPL.fixed.items():
        assert text == "{{TODO_COPY:" + key + "}}"
    assert [n.key for n in TPL.notes] and all(n.copy == "{{TODO_COPY:" + n.copy_key + "}}" for n in TPL.notes)
    with pytest.raises(PolicyError):
        M.parse_templates(tpl_doc(), env="dev")
    d = tpl_doc()
    d["student"]["retests"]["copy"] = "本月复测几次"  # 未签字只收占位
    with pytest.raises(PolicyError):
        M.parse_templates(d, env="test")
    d = tpl_doc()
    d["guardian"][0]["copy"] = "{{TODO_COPY:monthly_plan.guardian.other_key}}"  # 占位键与模板键不一致
    with pytest.raises(PolicyError):
        M.parse_templates(d, env="test")
    d = tpl_doc()
    d["guardian"][0]["vars"] = ["note"]  # 变量只能是计划事实（不读家长补充说明）
    with pytest.raises(PolicyError):
        M.parse_templates(d, env="test")
    d = tpl_doc()
    d["guardian"][0]["when"] = {"focus_subject": "aya"}  # 条件只能用计划事实
    with pytest.raises(PolicyError):
        M.parse_templates(d, env="test")


def signed_doc(copy_text: str) -> dict:
    d = tpl_doc()
    d.update(status="signed", synthetic=False, signed_by="test-signer", signed_at="2026-10-11")
    d["guardian"][0]["copy"] = copy_text
    return d


@pytest.mark.parametrize("text", ["坚持一个月保证提分", "期中有望进入年级前 50 名", "考上的概率很高", "本月可以提高 15 分"])
def test_signed_copy_blocked_by_no_promise_lexicon(text):
    assert M.parse_templates(signed_doc("请帮孩子安排安静的复测时段"), env="dev").signed  # 正例：人写的安排建议可以加载
    with pytest.raises(PolicyError, match="名次 / 概率 / 承诺"):
        M.parse_templates(signed_doc(text), env="dev")


def test_signed_copy_blocked_by_forbidden_inference_lexicon():
    with pytest.raises(PolicyError, match="禁用推断"):
        M.parse_templates(signed_doc("孩子有点懒惰，请多督促"), env="dev")


def test_no_promise_lexicon_self_test_and_matcher():
    assert M.no_promise_self_test() == []
    npm = M.no_promise_matcher()
    assert npm("请保证每周 4 天、每天 30 分钟的安静时段") == []          # 对家长的安排建议不是承诺
    assert npm("每周增加 30 分钟练习") == []
    assert "promise" in npm("按计划学一定能考上")
    assert "rank" in npm("你已经超过了 80% 的同学")
    assert "probability" in npm("有八成把握达到目标分")
    assert "rank" in npm("排　名")  # 全角空白规范化
    for text in [*TPL.fixed.values(), *(n.copy for n in TPL.notes)]:
        assert npm(text) == [] and FM(text) == []


# ---------------------------------------------------------------- 优先知识点

def test_priority_order_cites_pending_claims():
    plan = build(base_inputs())
    assert nodes(plan) == [NODE_C, NODE_A, NODE_B]  # 缺口 0.75 > 0.5 > 0.25
    pn = plan["student_plan"]["priority_nodes"]
    assert [n["position"] for n in pn] == [1, 2, 3]  # 计划里的先后（不是名次）
    assert [n["claim_id"] for n in pn] == [cid(3), cid(1), cid(2)]
    assert all(n["template_key"] == MK and n["template_version"] == 1 and n["copy_key"] == "monthly_plan.student.priority_node" for n in pn)
    assert M.claim_ids(plan) == [cid(3), cid(1), cid(2)]
    assert [i["priority"] for i in plan["plan_basis"]["items"]] == [0.75, 0.5, 0.25]


def test_focus_subject_factor_reorders():
    rows = base_inputs().mastery + (row(4, NODE_W, "stable", subject="wenli"),)
    claims = base_inputs().claims + (claim(4, NODE_W, "stable", subject="wenli"),)
    without = build(base_inputs(mastery=rows, claims=claims))
    assert nodes(without) == [NODE_C, NODE_A, NODE_B, NODE_W]  # 同为 0.25 时按学科、节点排序
    goal = M.Goal(GOAL_1, ("wenli",), ())
    with_focus = build(base_inputs(mastery=rows, claims=claims, goals=(goal,)))
    assert nodes(with_focus) == [NODE_C, NODE_A, NODE_W, NODE_B]  # 0.25 × 1.5 = 0.375
    item = next(i for i in with_focus["plan_basis"]["items"] if i["node_id"] == NODE_W)
    assert item["focus_factor"] == 1.5 and item["priority"] == 0.375
    # 学生段只有顺序变化，不含任何系数或诉求字段
    for n in with_focus["student_plan"]["priority_nodes"]:
        assert set(n) == {"position", "subject", "node_id", "cognitive_level", "claim_id", "template_key", "template_version", "copy_key"}


def test_student_section_has_no_guardian_derived_fields():
    goal = M.Goal(GOAL_1, ("aya",), (M.TargetScore("aya", 120.0, 105.0),))
    plan = build(base_inputs(goals=(goal,), latest_scores=(M.ExamScore(REC, "aya", "midterm", date(2026, 9, 20), 120.0, 92.5),),
                             claims=base_inputs().claims + (claim(9, REC, "", target_type="exam", key="syn.exam.reconcile", text=TEXT_E,
                                                                  value={"position": "below"}, vis=("agent", "guardian")),)))
    text = json.dumps(M.student_section(plan), ensure_ascii=False)
    for word in ("goal", "focus", "factor", "priority\"", "weight", "target", "gap", "guardian", "score", "note", GOAL_1, REC):
        assert word not in text, word
    assert set(plan["student_plan"]) == {"priority_nodes", "weekly_rhythm", "retests", "mini_reviews"}


@pytest.mark.parametrize("bad", [
    {"source": "guardian"},                                   # 家长观察来源：学生段读得到，不收
    {"vis": ("agent", "guardian")},                           # 学生看不到
    {"vis": ("agent", "student")},                            # 家长看不到
    {"status": "to_verify"},                                  # 待验证不收
    {"status": "withdrawn"},
    {"expires": AS_OF - timedelta(seconds=1)},                # 已到期
    {"version": 2},                                           # 模板版本与策略不一致
    {"key": "syn.math.other"},
    {"text": None},                                           # 渲染不了（没有文本模板）
    {"text": "【占位】{node_id}｜你很聪明"},                     # 渲染命中禁用推断
    {"level": "understand"},                                  # 层次与掌握行不一致
    {"gate": "understand"},                                   # 门槛与掌握行不一致
])
def test_no_citable_claim_no_item(bad):
    kw = {k: v for k, v in bad.items() if k != "gate"}
    gate = bad.get("gate", "execute")
    claims = (claim(1, NODE_A, gate, **kw), claim(2, NODE_B, "stable"), claim(3, NODE_C, "understand"))
    plan = build(base_inputs(claims=claims))
    assert NODE_A not in nodes(plan) and nodes(plan) == [NODE_C, NODE_B]


def test_star_gate_and_missing_claim_and_invisible_subject_are_not_candidates():
    rows = (row(1, NODE_A, "transfer"), row(2, NODE_B, "stable"), row(5, "SCI.SYN.X.Y", "execute", subject="zhixing"))
    claims = (claim(1, NODE_A, "transfer"), claim(5, "SCI.SYN.X.Y", "execute", subject="zhixing"))
    plan = build(base_inputs(mastery=rows, claims=claims))
    assert nodes(plan) == []  # 迁移以上不是候选；B 没有判断；道法不在策略可见学科里
    assert plan["plan_basis"]["items"] == []


def test_max_priority_nodes():
    rows = tuple(row(i, f"MATH.SYN.N{i:02d}.X", "execute") for i in range(1, 13))
    claims = tuple(claim(i, f"MATH.SYN.N{i:02d}.X", "execute") for i in range(1, 13))
    plan = build(base_inputs(mastery=rows, claims=claims))
    assert len(nodes(plan)) == CFG.max_priority_nodes == 8
    assert len(plan["plan_basis"]["items"]) == 12
    assert nodes(plan) == sorted(nodes(plan))  # 同优先级按节点排序


def test_one_item_per_node_takes_highest_priority_level():
    rows = (row(1, NODE_A, "stable", level="know"), row(2, NODE_A, "understand", level="apply"))
    claims = (claim(1, NODE_A, "stable", level="know"), claim(2, NODE_A, "understand", level="apply"))
    plan = build(base_inputs(mastery=rows, claims=claims))
    assert [(n["node_id"], n["cognitive_level"], n["claim_id"]) for n in plan["student_plan"]["priority_nodes"]] == [(NODE_A, "apply", cid(2))]


def test_policy_weights_default_and_published():
    assert build(base_inputs())["policy_weights"] == "default"
    plan = build(base_inputs(weights={f"aya:{NODE_B}": 4.0}))
    assert plan["policy_weights"] == "published" and nodes(plan)[0] == NODE_B  # 0.25 × 4


# ---------------------------------------------------------------- 节奏、复测、小复评、注意事项

def test_rhythm_retests_and_mini_reviews():
    due = (M.RetestDue("aya", NODE_A, date(2026, 10, 3)), M.RetestDue("aya", NODE_B, date(2026, 10, 3)),
           M.RetestDue("wenli", NODE_W, date(2026, 10, 12)), M.RetestDue("aya", NODE_C, date(2026, 11, 2)))
    plan = build(base_inputs(retests_due=due, mastery=base_inputs().mastery + (row(4, NODE_W, "stable", subject="wenli"),)))
    sp = plan["student_plan"]
    assert sp["weekly_rhythm"] == {"sessions_per_week": 5, "minutes_per_session": 30, "copy_key": "monthly_plan.student.weekly_rhythm"}
    assert sp["retests"] == {"count": 3, "dates": ["2026-10-03", "2026-10-12"],
                             "by_subject": [{"subject": "aya", "count": 2}, {"subject": "wenli", "count": 1}],
                             "copy_key": "monthly_plan.student.retests"}
    assert sp["mini_reviews"] == [{"subject": "aya", "date": "2026-10-24", "copy_key": "monthly_plan.student.mini_review"},
                                  {"subject": "wenli", "date": "2026-10-25", "copy_key": "monthly_plan.student.mini_review"}]


def test_guardian_notes_follow_plan_facts_only():
    plain = build(base_inputs())["guardian_notes"]
    assert [n["key"] for n in plain] == ["mini_review_dates", "weekly_rhythm_support"]
    due = tuple(M.RetestDue("aya", NODE_A, date(2026, 10, d)) for d in (3, 10))
    busy = build(base_inputs(retests_due=due, exam_plans=(REC,)))["guardian_notes"]
    assert [n["key"] for n in busy] == ["quiet_time_for_retests", "exam_month_routine", "mini_review_dates"]  # 最多 3 条
    assert busy[0]["vars"] == {"retest_count": 2, "sessions_per_week": 5, "minutes_per_session": 30}
    assert busy[1]["vars"] == {"exam_count": 1}
    for n in busy:
        assert n["copy_key"] == f"monthly_plan.guardian.{n['key']}" and set(n["vars"]) <= set(M.NOTE_FACTS)


# ---------------------------------------------------------------- 回顾

PREV = {"priority_nodes": [{"subject": "aya", "node_id": NODE_A}, {"subject": "aya", "node_id": NODE_B}]}


def test_last_month_lit_cites_transfer_claims_that_entered_transfer_last_month():
    sep = datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc)
    aug = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
    t = dict(key=TK, text=TEXT_T, vis=("agent", "guardian"))
    claims = base_inputs().claims + (
        claim(11, NODE_A, "transfer", created=aug, star_at=sep, **t),        # 按历史在上月进入迁移（新建时间早于上月也算）、在上月计划里
        claim(12, NODE_B, "transfer", created=sep, star_at=AS_OF, **t),      # 本月才进入迁移：不算上月（不看新建时间）
        claim(13, NODE_C, "transfer", created=sep, star_at=sep, **t),        # 不在上月计划里
        claim(14, NODE_B, "transfer", created=sep, star_at=aug, **t),        # 上上月就进入了迁移
        claim(15, NODE_B, "transfer", created=sep, star_at=None, **t),       # 历史里没有进入迁移的记录
        claim(16, NODE_B, "transfer", key=TK, text=TEXT_T, created=sep, star_at=sep, vis=("agent", "student")),  # 家长看不到
    )
    plan = build(base_inputs(claims=claims, prev_plan=PREV))
    lm = plan["review"]["last_month"]
    assert lm == {"month": "2026-09-01", "planned_nodes": 2, "copy_key": "monthly_plan.review.last_month",
                  "lit": [{"subject": "aya", "node_id": NODE_A, "claim_id": cid(11), "template_key": TK, "template_version": 1,
                           "copy_key": "monthly_plan.review.lit"}]}  # 不给任何不引用判断的关于学生的计数
    assert build(base_inputs(claims=claims))["review"]["last_month"] is None  # 没有上月快照：不出回顾


def test_score_gap_needs_same_scale_and_guardian_visible_exam_claim():
    goal = M.Goal(GOAL_1, (), (M.TargetScore("aya", 120.0, 105.0),))
    score = M.ExamScore(REC, "aya", "midterm", date(2026, 9, 20), 120.0, 92.5)
    exam = claim(9, REC, "", target_type="exam", key="syn.exam.reconcile", text=TEXT_E, value={"position": "below"},
                 vis=("agent", "student", "guardian"))
    plan = build(base_inputs(goals=(goal,), latest_scores=(score,), claims=base_inputs().claims + (exam,)))
    assert plan["review"]["score_gaps"] == [{  # 只存引用，不存成绩、目标分与分差的值（家长视图读时现算区间）
        "subject": "aya", "goal_id": GOAL_1, "exam_record_id": REC, "claim_id": cid(9),
        "template_key": "syn.exam.reconcile", "template_version": 1, "copy_key": "monthly_plan.review.score_gap"}]
    assert "92.5" not in M.to_json(plan["review"]) and "105" not in M.to_json(plan["review"])
    assert cid(9) in M.claim_ids(plan)
    other_scale = M.Goal(GOAL_1, (), (M.TargetScore("aya", 100.0, 90.0),))
    assert build(base_inputs(goals=(other_scale,), latest_scores=(score,), claims=base_inputs().claims + (exam,)))["review"]["score_gaps"] == []
    assert build(base_inputs(goals=(goal,), latest_scores=(score,)))["review"]["score_gaps"] == []  # 没有考试判断：不出
    hidden = claim(9, REC, "", target_type="exam", key="syn.exam.reconcile", text=TEXT_E, value={"position": "below"}, vis=("agent",))
    assert build(base_inputs(goals=(goal,), latest_scores=(score,), claims=base_inputs().claims + (hidden,)))["review"]["score_gaps"] == []
    # 两位家长各写的目标都给出（各带诉求 ID，家长视图只给本人的项）；没有成绩的学科不出
    g2 = M.Goal(GOAL_2, (), (M.TargetScore("aya", 120.0, 110.0), M.TargetScore("wenli", 120.0, 100.0)))
    both = build(base_inputs(goals=(g2, goal), latest_scores=(score,), claims=base_inputs().claims + (exam,)))
    assert [(g["goal_id"], g["subject"]) for g in both["review"]["score_gaps"]] == [(GOAL_1, "aya"), (GOAL_2, "aya")]


# ---------------------------------------------------------------- 重放

def test_replay_is_byte_identical_and_order_independent():
    goal = M.Goal(GOAL_1, ("aya",), (M.TargetScore("aya", 120.0, 105.0),))
    due = (M.RetestDue("aya", NODE_A, date(2026, 10, 3), "7a540000-0000-4000-8000-000000000001"),)
    prev_id = "9a540000-0000-4000-8000-0000000000aa"
    kw = dict(goals=(goal,), retests_due=due, exam_plans=(REC,), prev_plan=PREV, prev_plan_id=prev_id)
    inp = base_inputs(**kw)
    a, b = build(inp), build(copy.deepcopy(inp))
    assert M.to_json(a) == M.to_json(b)
    shuffled = base_inputs(**kw, mastery=tuple(reversed(inp.mastery)), claims=tuple(reversed(inp.claims)))
    assert M.to_json(build(shuffled)) == M.to_json(a)
    assert a["inputs_digest"] == M.inputs_digest(inp) and len(a["inputs_digest"]) == 64
    later = base_inputs(**kw, as_of=AS_OF + timedelta(hours=1))
    assert build(later)["inputs_digest"] != a["inputs_digest"]  # 只有生成时刻不同，摘要也不同（重放要带上 as_of）
    assert build(base_inputs(**{**kw, "exam_plans": (REC, GOAL_2)}))["inputs_digest"] != a["inputs_digest"]
    assert a["algorithm_version"] == M.ALGORITHM_VERSION and a["template_version"] == TPL.template_version
    assert a["config_version"] == f"{POLICY.config_version}_{CFG.config_version}"
    assert set(a) == {"student_plan", "guardian_notes", "review", "plan_basis", "input_refs", "algorithm_version", "config_version",
                      "template_version", "policy_weights", "inputs_digest"}


def test_input_refs_and_fingerprints():
    goal = M.Goal(GOAL_1, ("aya",), ())
    due = (M.RetestDue("aya", NODE_A, date(2026, 10, 3), "7a540000-0000-4000-8000-000000000001"),)
    plan = build(base_inputs(goals=(goal,), retests_due=due, exam_plans=(REC,)))
    refs = plan["input_refs"]
    assert refs["as_of"] == AS_OF.isoformat() and refs["goals"] == [GOAL_1] and refs["exam_plans"] == [REC]
    assert refs["claims"] == sorted([cid(i), hid(i)] for i in (1, 2, 3)) and refs["retest_tasks"] == [due[0].task_id]
    assert refs["mastery"] == sorted(r.basis_id for r in base_inputs().mastery) and refs["prev_plan_id"] is None
    assert refs["fingerprints"] == {"claims_policy": POLICY.fingerprint, "config": CFG.fingerprint, "templates": TPL.fingerprint,
                                    "today": CFG.today_fingerprint}
    # 模板库指纹按内容（含 when 条件）
    d = yaml.safe_load(M.TEMPLATE_PATH.read_text(encoding="utf-8"))
    d["guardian"][0]["when"] = {"retest_count_min": 2}
    assert M.parse_templates(d, env="test").fingerprint != TPL.fingerprint
    # 今日计划预算配置的指纹进配置指纹之外单独一项
    assert len(CFG.today_fingerprint) == 32 and CFG.today_fingerprint != CFG.fingerprint


def test_month_must_be_first_day():
    with pytest.raises(ValueError):
        build(base_inputs(month=date(2026, 10, 2)))
    assert M.previous_month(date(2026, 1, 1)) == date(2025, 12, 1)
    assert M.month_window(date(2026, 12, 1))[1] == datetime(2027, 1, 1, tzinfo=M.SHANGHAI)
