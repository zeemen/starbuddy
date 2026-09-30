"""SIE-02a 选题规则与状态机的单测、合成学生回放与性质测试（纯函数，不连库）。全部合成数据（规则 8）。

- 合成学生：按预设掌握图作答（掌握 → 答对），每题用时取合成分布（种子固定）；
- 断言：断点与预设一致（独立的“神谕”按掌握图直接算）、答对节点的强前置全部跳过、无题节点为 no_item、
  题数与用时在上限内、同样输入两次选题序列逐字节相同、弱边不参与追溯；
- 性质测试：随机无环图 × 随机掌握图（单调与非单调）× 随机上限，逐条核对不变量。
"""
from __future__ import annotations

import json
import random

import httpx
import pytest

from learning.diagnosis import engine
from learning.diagnosis.engine import (ALGORITHM_VERSION, Done, Event, InvalidTransition, Params, State, derive_state, trace,
                                       transition)
from learning.diagnosis.presenter import SyntheticPresenter
from learning.diagnosis.source import FixtureSource, KnowledgeServiceSource, _Graph, load_fixture

Q = "MATH.SYN.QUAD."
BASE = {"MATH.SYN.BASIC.COORD", "MATH.SYN.EXPR.COMPLETE_SQ", "MATH.SYN.EQ.QUAD_SOLVE"}


def closure(src, nodes: set[str]) -> set[str]:
    """掌握图单调化：掌握一个节点就掌握它的全部强前置。"""
    out = set(nodes)
    for n in nodes:
        out |= src.ancestors(n)
    return out


def simulate(src, mastery: set[str], params: Params = Params(), seed: int = 7, grade: int = 9, stop_after: int | None = None,
             history: list[Done] | None = None) -> tuple[list[Done], engine.Trace]:
    """合成学生：掌握的节点答对；用时取合成分布（自适应 15–90 秒，过程题 60–180 秒，都不超过规则里的预留）。"""
    rng = random.Random(seed)
    hist = list(history or [])
    for _ in range(200):
        t = trace(src, grade, hist, params)
        if t.finished or (stop_after is not None and len(hist) >= stop_after):
            return hist, t
        assert t.pending_index is None
        p = t.next
        if p.reason == "no_item":
            hist.append(Done(p.kind, p.node_id, None, p.reason, "no_item"))
            continue
        sec = rng.randint(15, params.adaptive_step_sec) if p.kind == "adaptive" else rng.randint(60, params.process_step_sec)
        hist.append(Done(p.kind, p.node_id, p.item_id, p.reason, "answered", "correct" if p.node_id in mastery else "incorrect", sec))
    raise AssertionError("did not finish")


def oracle_break_points(src, targets: list[str], mastery: set[str]) -> set[str]:
    """独立于选题顺序的期望断点（掌握图单调、上限不触发时）：从目标沿“没掌握且有题”的节点往下走；
    断点 = 这样的节点里，直接强前置都已掌握或无题可测的那些。"""
    fail: set[str] = set()
    stack = [t for t in targets if t not in mastery and src.items(t)]
    while stack:
        n = stack.pop()
        if n in fail:
            continue
        fail.add(n)
        stack += [p for p in src.prereqs(n) if p not in mastery and src.items(p)]
    return {n for n in fail if all(p in mastery or not src.items(p) for p in src.prereqs(n))}


@pytest.fixture(scope="module")
def src():
    return FixtureSource("test")


# ---------- 合成学生回放（验收第 1 条） ----------
PRESETS = {
    "全会": None,  # 掌握全部
    "卡在顶点式": {Q + "CONCEPT", Q + "GRAPH_BASIC"} | BASE,
    "卡在一般式": {Q + "CONCEPT", Q + "GRAPH_BASIC", Q + "VERTEX_FORM", Q + "AXIS"} | BASE,
    "卡在对称轴": {Q + "VERTEX_FORM", Q + "GENERAL_FORM", Q + "ROOTS"} | BASE,
    "会一般式不会方程": {Q + "GENERAL_FORM", Q + "AXIS"},
    # 下面两个在默认上限（20 题，给过程观察留 4 题，每节点 1 + 1 确认）内追不到底：只断言找到的断点都对（lessons 待人确认）
    "只会最底层": set(BASE),
    "全不会": set(),
}
LIMIT_BOUND = {"只会最底层", "全不会"}


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_synthetic_student_matches_preset(src, name):
    preset = PRESETS[name]
    doc = load_fixture()
    mastery = {n["node_id"] for n in doc["nodes"]} if preset is None else closure(src, preset)
    targets = src.targets(9)
    hist, t = simulate(src, mastery)
    # 断点与预设一致（上限内追到底）；上限先到时，找到的断点也都是真断点
    expected = oracle_break_points(src, targets, mastery)
    assert t.limited == (name in LIMIT_BOUND), name
    if name in LIMIT_BOUND:
        assert set(t.break_points) <= expected and len([h for h in hist if h.kind == "adaptive" and h.item_id]) == 16, name
    else:
        assert set(t.break_points) == expected, name
    # 题数与用时在上限内
    p = Params()
    answered = [h for h in hist if h.status == "answered"]
    assert len(answered) <= p.item_limit and sum(h.elapsed_sec for h in answered) <= p.time_budget_sec
    # 答对节点的强前置全部跳过：之后不再为它们出题
    for i, h in enumerate(hist):
        if h.kind == "adaptive" and h.outcome == "correct":
            later = {x.node_id for x in hist[i + 1:] if x.kind == "adaptive"}
            assert not (later & src.ancestors(h.node_id)), (name, h.node_id)
    # 无题节点是 no_item，且不出题
    for h in hist:
        if h.reason == "no_item":
            assert h.item_id is None and not src.items(h.node_id)
        if h.item_id is not None:
            assert h.item_id in src.items(h.node_id)
    # 每个节点在自适应阶段最多 2 题（1 题 + 答错后 1 题确认）
    per: dict[str, int] = {}
    for h in hist:
        if h.kind == "adaptive" and h.item_id:
            per[h.node_id] = per.get(h.node_id, 0) + 1
    assert max(per.values(), default=0) <= 2
    # 过程观察：2–3 道 process + 1 道 explain，都在断点附近、用没出过的题
    proc = [h for h in hist if h.kind in ("process", "explain")]
    assert 2 <= len([h for h in proc if h.kind == "process"]) <= 3 and len([h for h in proc if h.kind == "explain"]) == 1
    adaptive_items = {h.item_id for h in hist if h.kind == "adaptive"}
    assert not ({h.item_id for h in proc} & adaptive_items)
    assert len({h.item_id for h in proc}) == len(proc)


def test_break_point_examples(src):
    # 卡在顶点式：图象会、顶点式不会 → 断点在顶点式；配方无题可测
    _, t = simulate(src, closure(src, {Q + "GRAPH_BASIC"}) | BASE)
    assert Q + "VERTEX_FORM" in t.break_points
    assert t.status["MATH.SYN.EXPR.COMPLETE_SQ"] in ("no_item", "skipped")
    assert t.status[Q + "INEQ"] == "no_item"  # 目标节点无题：记无题可测，不往下追
    # 全会：没有断点，目标的前置全部跳过
    everything = {n["node_id"] for n in load_fixture()["nodes"]}
    hist, t = simulate(src, everything)
    assert t.break_points == []
    assert {h.node_id for h in hist if h.kind == "adaptive" and h.item_id} <= set(src.targets(9))


def test_same_input_same_sequence_bytes(src):
    m = closure(src, {Q + "GRAPH_BASIC"}) | BASE
    a, _ = simulate(src, m, seed=3)
    b, _ = simulate(src, m, seed=3)
    dump = lambda h: json.dumps([x.__dict__ for x in h], ensure_ascii=False, sort_keys=True).encode()
    assert dump(a) == dump(b)
    # 选题只依赖作答结果，不依赖用时：换用时分布，选出的题目序列不变（上限不触发时）
    c, _ = simulate(src, m, seed=99)
    assert [(x.kind, x.node_id, x.item_id, x.reason) for x in a] == [(x.kind, x.node_id, x.item_id, x.reason) for x in c]


def test_pause_resume_does_not_change_result(src):
    """暂停续做：任意位置截断历史再接着做，结果与一次做完相同（复算只看历史）。"""
    m = closure(src, {Q + "VERTEX_FORM"}) | BASE
    full, t_full = simulate(src, m, seed=5)
    for cut in range(1, len(full)):
        part, _ = simulate(src, m, seed=5, stop_after=cut)
        rest, t = simulate(src, m, seed=5, history=part)
        assert [(x.node_id, x.item_id, x.reason) for x in rest] == [(x.node_id, x.item_id, x.reason) for x in full]
        assert t.break_points == t_full.break_points


def test_order_upper_layer_first_then_node_id(src):
    """上层优先（强前置链更长的先测），同层按节点 ID 升序；追溯来的前置同样按这个顺序。"""
    hist, _ = simulate(src, set(BASE) | {Q + "CONCEPT", Q + "GRAPH_BASIC", Q + "VERTEX_FORM"}, stop_after=20)
    firsts = [h.node_id for h in hist if h.reason in ("target", "no_item", "prereq_trace")]
    assert firsts[:5] == [Q + "APPLY", Q + "INEQ", Q + "ANALYTIC", Q + "COEF_SIGN", Q + "EXTREMUM"]
    assert [src.height(n) for n in firsts[:5]] == [6, 6, 5, 5, 5]
    traced = [h.node_id for h in hist if h.reason == "prereq_trace"]
    assert traced[:2] == [Q + "AXIS", Q + "GENERAL_FORM"]


def test_history_mismatch_detected(src):
    hist, _ = simulate(src, set(), stop_after=3)
    bad = list(hist)
    bad[1] = Done("adaptive", bad[1].node_id, "Q-FUN-U01-T-002", "confirm", "answered", "incorrect", 10)
    with pytest.raises(engine.HistoryMismatch):
        trace(src, 9, bad)


def test_weak_edges_are_not_traced(src):
    doc = load_fixture()
    weak = [(e["from"], e["to"]) for e in doc["edges"] if e["strength"] == "weak"]
    assert weak
    for f, t in weak:
        assert f not in src.prereqs(t)
    # 实际问题（APPLY）答错：追的是最值（强边），不是概念（弱边）
    hist, _ = simulate(src, set(), stop_after=12)
    traced = [h.node_id for h in hist if h.reason == "prereq_trace"]
    assert Q + "CONCEPT" not in traced[:3]


def test_limits_stop_the_session(src):
    p = Params(item_limit=6, time_budget_sec=900)
    hist, t = simulate(src, set(), params=p)
    answered = [h for h in hist if h.status == "answered"]
    assert t.finished and len(answered) <= 6 and sum(h.elapsed_sec for h in answered) <= 900
    # 用时先到：每题都用满预留时间
    slow = Params(time_budget_sec=600)
    hist, t = simulate(src, set(), params=slow, seed=1)
    assert sum(h.elapsed_sec for h in hist if h.status == "answered") <= 600


def test_confirm_item_decides(src):
    """第一题错、确认题对：以确认题为准，记通过，前置跳过。"""
    t = trace(src, 9, [])
    first = t.next
    hist = [Done("adaptive", first.node_id, first.item_id, first.reason, "answered", "incorrect", 30)]
    t = trace(src, 9, hist)
    assert t.next.reason == "confirm" and t.next.node_id == first.node_id
    hist.append(Done("adaptive", first.node_id, t.next.item_id, "confirm", "answered", "correct", 30))
    t = trace(src, 9, hist)
    assert t.status[first.node_id] == "pass"
    assert all(t.status.get(a) in ("skipped", "pass") for a in src.ancestors(first.node_id))


def test_unclear_verdict_neither_skips_nor_traces(src):
    assert engine.outcome_of("needs_review") == "unclear" and engine.outcome_of("partial") == "incorrect"
    t = trace(src, 9, [])
    first = t.next
    hist = [Done("adaptive", first.node_id, first.item_id, first.reason, "answered", "unclear", 30)]
    t = trace(src, 9, hist)
    assert t.status[first.node_id] == "unclear"
    assert not any(v == "skipped" for v in t.status.values())
    assert t.next.reason in ("target", "no_item") and t.next.node_id != first.node_id


def test_algorithm_version_is_versioned_name():
    import re
    assert re.fullmatch(r"[a-z][a-z0-9-]*@\d+\.\d+\.\d+", ALGORITHM_VERSION)


# ---------- 性质测试：随机图 × 随机掌握图 ----------
def random_graph(rng: random.Random, n: int) -> tuple[_Graph, list[str]]:
    ids = [f"MATH.SYN.N{i:02d}" for i in range(n)]
    strong = [(ids[i], ids[j]) for j in range(n) for i in range(j) if rng.random() < 0.18]
    items = {x: [f"SYN-I{k:02d}-{x[-3:]}" for k in range(rng.choice([0, 1, 1, 2, 3]))] for x in ids}
    targets = rng.sample(ids[n // 2:], k=min(4, n - n // 2))
    return _Graph(strong, items, {9: targets}, "0.0.1"), targets


@pytest.mark.parametrize("seed", range(60))
def test_properties_random(seed):
    rng = random.Random(seed)
    g, targets = random_graph(rng, rng.randint(4, 18))
    nodes = [f"MATH.SYN.N{i:02d}" for i in range(40)]
    raw = {x for x in nodes if rng.random() < 0.45}
    monotone = seed % 2 == 0
    mastery = closure(g, raw) if monotone else raw
    p = Params(item_limit=rng.choice([6, 10, 20]), time_budget_sec=rng.choice([900, 1500, 2100]))
    hist, t = simulate(g, mastery, params=p, seed=seed)
    answered = [h for h in hist if h.status == "answered"]
    # 上限
    assert len(answered) <= p.item_limit
    assert sum(h.elapsed_sec for h in answered) <= p.time_budget_sec
    # 确定性
    again, _ = simulate(g, mastery, params=p, seed=seed)
    assert again == hist
    # 答对之后不再测它的任何强前置；无题节点不出题；每个节点自适应最多两题
    for i, h in enumerate(hist):
        if h.kind == "adaptive" and h.outcome == "correct":
            assert not ({x.node_id for x in hist[i + 1:] if x.kind == "adaptive"} & g.ancestors(h.node_id))
        if h.reason == "no_item":
            assert not g.items(h.node_id)
        if h.reason == "prereq_trace":
            # 追溯来的节点必须是某个已答错节点的直接强前置
            before = {x.node_id for x in hist[:i] if x.kind == "adaptive" and x.outcome == "incorrect"}
            assert any(h.node_id in g.prereqs(b) for b in before)
    # 断点都是未通过、且直接前置都通过或无题的节点
    for b in t.break_points:
        assert t.status[b] == "fail"
        assert all(t.status.get(q) in ("pass", "skipped", "no_item") for q in g.prereqs(b))
    # 单调掌握图：找到的断点都是真断点；上限没触发时，与神谕完全一致
    if monotone:
        expected = oracle_break_points(g, targets, mastery)
        assert set(t.break_points) <= expected
        if not t.limited:
            assert set(t.break_points) == expected
            ORACLE_CHECKED.append(seed)


ORACLE_CHECKED: list[int] = []


def test_properties_checked_enough_cases():
    """性质测试里至少有一批单调、上限没触发的用例真的走到了“与神谕完全一致”的断言（防止条件写错后什么都没比）。"""
    assert len(ORACLE_CHECKED) >= 10, ORACLE_CHECKED


# ---------- 状态机 ----------
def test_state_machine_transitions():
    s = transition(State.CREATED, Event.SELECT)
    s = transition(s, Event.PRESENT)
    assert s == State.PROBING
    assert transition(s, Event.ANSWER) == State.GRADING  # 探针可跳过
    s = transition(s, Event.PROBE)
    assert s == State.AWAITING_ANSWER
    with pytest.raises(InvalidTransition):
        transition(s, Event.PROBE)
    s = transition(s, Event.PAUSE)
    assert s == State.PAUSED
    with pytest.raises(InvalidTransition):
        transition(s, Event.ANSWER)
    s = transition(s, Event.RESUME)
    assert transition(transition(s, Event.ENTER_PROCESS), Event.PRESENT) == State.PROBING
    assert transition(s, Event.FINISH) == State.FINISHED
    for terminal in (State.FINISHED, State.ABANDONED):
        for ev in Event:
            with pytest.raises(InvalidTransition):
                transition(terminal, ev)
    assert derive_state("active", None, False) == State.SELECTING
    d = Done("adaptive", Q + "APPLY", "X", "target", "presented")
    assert derive_state("active", d, False) == State.PROBING and derive_state("active", d, True) == State.AWAITING_ANSWER
    assert derive_state("paused", d, True) == State.PAUSED


# ---------- 题面提供者、夹具、知识服务来源 ----------
def test_presenter_refuses_outside_test_and_never_returns_answers():
    for env in ("dev", "staging", "pilot", "prod", ""):
        with pytest.raises(RuntimeError):
            SyntheticPresenter(env)
        with pytest.raises(RuntimeError):
            FixtureSource(env)
    pr = SyntheticPresenter("test")
    for it in load_fixture()["items"]:
        v = pr.present("aya", it["item_id"])
        assert set(v) <= {"format", "stem", "options"}
        assert all(set(o) == {"key", "text"} for o in v.get("options", []))
        assert "answer" not in json.dumps(v)
    assert pr.present("zhixing", load_fixture()["items"][0]["item_id"]) is None
    # 夹具里混进答案字段：加载即拒绝
    bad = json.loads(json.dumps(load_fixture()))
    bad["items"][0]["answer"] = ["A"]
    path = __import__("tempfile").mktemp(suffix=".json")
    open(path, "w", encoding="utf-8").write(json.dumps(bad))
    with pytest.raises(RuntimeError):
        load_fixture(path)
    # 出口白名单：视图多一个键就拒绝
    from learning.diagnosis.presenter import _clean
    with pytest.raises(ValueError):
        _clean({"format": "fill", "stem": "x", "answer": "1"})


def test_fixture_items_are_registered_aya_items():
    pack = json.load(open(__import__("pathlib").Path(__file__).resolve().parents[3] / "adapters/aya/content/pack.json", encoding="utf-8"))
    by_id = {i["id"]: i for i in pack["items"]}
    for it in load_fixture()["items"]:
        assert it["item_id"] in by_id
        assert it["format"] == by_id[it["item_id"]]["format"]
        if it["format"] != "fill":
            assert [o["key"] for o in it["options"]] == [o["key"] for o in by_id[it["item_id"]]["options"]]


def knowledge_transport(doc: dict, calls: list):
    """假知识服务：按契约形状应答 node.path（只走强前置边，direction = up）与 item.search。"""
    strong = [(e["from"], e["to"]) for e in doc["edges"] if e["strength"] == "strong"]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool = request.url.path.rsplit("/", 1)[-1]
        calls.append((tool, body, request.headers.get("authorization")))
        assert body["context"]["caller"] == "learning" and "student_ref" not in body["context"]
        env = {"contract_version": "0.9.0", "evidence_ids": [], "confidence": 1, "source_version": "graph:math-map@0.1.0",
               "model_version": "algo:kge-node-path@0.1.0", "human_review_required": False}
        if tool == "node.path":
            root, steps, frontier, seen = body["node_id"], [], [body["node_id"]], set()
            for depth in range(1, body["max_depth"] + 1):
                nxt = []
                for f, t in strong:
                    if t in frontier and (f, t) not in seen:
                        seen.add((f, t))
                        steps.append({"from_id": f, "to_id": t, "depth": depth})
                        nxt.append(f)
                frontier = sorted(set(nxt))
            return httpx.Response(200, json={**env, "result": {"root": root, "direction": "up", "steps": steps}})
        items = [{"item_ref": f"aya:{i['item_id']}", "main_node_id": i["node_id"], "license_state": "unverified"}
                 for i in doc["items"] if i["node_id"] == body["node_id"]]
        return httpx.Response(200, json={**env, "result": {"items": items}})
    return httpx.MockTransport(handler)


async def test_knowledge_source_matches_fixture_source(src):
    doc = load_fixture()
    calls: list = []
    async with httpx.AsyncClient(transport=knowledge_transport(doc, calls)) as client:
        ks = KnowledgeServiceSource(client, "http://knowledge.test", "k" * 40, "aya", {9: src.targets(9)}, doc["map_version"])
        await ks.load(9)
        n_calls = len(calls)
        await ks.load(9)
        assert len(calls) == n_calls  # 同一会话只取一次
    assert {c[0] for c in calls} == {"node.path", "item.search"} and all(c[2] == "Bearer " + "k" * 40 for c in calls)
    for n in {x["node_id"] for x in doc["nodes"]} - {"MATH.SYN.QUAD.INEQ"} | set(src.targets(9)):
        if n in ks._items or n in src.targets(9):
            assert ks.prereqs(n) == src.prereqs(n), n
            assert ks.ancestors(n) == src.ancestors(n), n
            assert ks.height(n) == src.height(n), n
            assert ks.items(n) == src.items(n), n
    # 同一合成学生走两种来源，选题序列相同
    m = closure(src, {Q + "GRAPH_BASIC"}) | BASE
    a, _ = simulate(src, m)
    b, _ = simulate(ks, m)
    assert a == b


async def test_knowledge_source_rejects_bad_answers():
    from learning.diagnosis.source import SourceError

    env = {"contract_version": "0.9.0", "evidence_ids": [], "confidence": 1, "source_version": "graph:math-map@0.1.0",
           "model_version": "algo:kge-node-path@0.1.0", "human_review_required": False}

    def bad(request):  # 形状合乎契约，但 node.path 答的是另一个问题（别的根节点、向下）；item.search 正常
        if request.url.path.endswith("item.search"):
            return httpx.Response(200, json={**env, "result": {"items": []}})
        return httpx.Response(200, json={**env, "result": {"root": "MATH.SYN.OTHER", "direction": "down", "steps": []}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(bad)) as client:
        ks = KnowledgeServiceSource(client, "http://knowledge.test", "k" * 40, "aya", {9: ["MATH.SYN.QUAD.APPLY"]}, "0.1.0")
        with pytest.raises(SourceError):
            await ks.load(9)

    def down(request):
        return httpx.Response(503, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        ks = KnowledgeServiceSource(client, "http://knowledge.test", "k" * 40, "aya", {9: ["MATH.SYN.QUAD.APPLY"]}, "0.1.0")
        with pytest.raises(SourceError):
            await ks.load(9)


async def test_knowledge_source_contract_depth_and_license():
    """契约审查建议 3：响应按契约校验（多一个键也拒）；前置链到了 max_depth 报错而不当作“没有前置”；restricted 的题不出。"""
    from learning.diagnosis.source import MAX_DEPTH, SourceError
    doc = load_fixture()
    env = {"contract_version": "0.9.0", "evidence_ids": [], "confidence": 1, "source_version": "graph:math-map@0.1.0",
           "model_version": "algo:kge-node-path@0.1.0", "human_review_required": False}
    root = "MATH.SYN.QUAD.APPLY"

    def make(steps, items, extra=None):
        def handler(request):
            tool = request.url.path.rsplit("/", 1)[-1]
            if tool == "node.path":
                res = {"root": root, "direction": "up", "steps": steps}
                return httpx.Response(200, json={**env, "result": res, **(extra or {})})
            body = json.loads(request.content)
            return httpx.Response(200, json={**env, "result": {"items": [i for i in items if i["main_node_id"] == body["node_id"]]}})
        return httpx.MockTransport(handler)

    step = {"from_id": "MATH.SYN.QUAD.ROOTS", "to_id": root, "depth": 1}
    items = [{"item_ref": "aya:Q-A", "main_node_id": root, "license_state": "original"},
             {"item_ref": "aya:Q-B", "main_node_id": root, "license_state": "restricted"},
             {"item_ref": "aya:Q-C", "main_node_id": "MATH.SYN.QUAD.ROOTS", "license_state": "unverified"}]
    async with httpx.AsyncClient(transport=make([step], items)) as client:
        ks = KnowledgeServiceSource(client, "http://knowledge.test", "k" * 40, "aya", {9: [root]}, doc["map_version"])
        await ks.load(9)
        assert ks.items(root) == ["Q-A"] and ks.items("MATH.SYN.QUAD.ROOTS") == ["Q-C"]
    deep = [{"from_id": f"MATH.SYN.D{i + 1}", "to_id": root if i == 0 else f"MATH.SYN.D{i}", "depth": i + 1} for i in range(MAX_DEPTH)]
    for bad in (make(deep, items), make([step], items, {"extra": 1}),
                make([{**step, "note": "x"}], items), make([{**step, "depth": 0}], items)):
        async with httpx.AsyncClient(transport=bad) as client:
            ks = KnowledgeServiceSource(client, "http://knowledge.test", "k" * 40, "aya", {9: [root]}, doc["map_version"])
            with pytest.raises(SourceError):
                await ks.load(9)


def test_fixture_uses_synthetic_namespace():
    doc = load_fixture()
    nodes = {n["node_id"] if isinstance(n, dict) else n for n in doc["nodes"]} | {i["node_id"] for i in doc["items"]}
    assert nodes and all(n.startswith("MATH.SYN.") for n in nodes)
