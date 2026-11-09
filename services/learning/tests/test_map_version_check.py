"""地图版本交叉核对（PLT-25，W13 T13；specs/plt25-map-version-check 第二节）。全部合成数据（规则 8）。

grade / diagnose：适配器 source_version 的 graph: 段与内容版本绑定的地图版本比对，mismatch 即 502 不落库；absent、unbound、match 照常写入。
plan：只计数，不改返回（tests/test_plan.py 另核对今日计划本身）。
"""
from __future__ import annotations

import uuid

import pytest

from conftest import counts, diagnose_body, env, grade_body, to_v1_body
from learning import map_check, metrics


@pytest.fixture(autouse=True)
def no_candidate_switch(monkeypatch):
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)


# ---------- 纯函数 ----------
@pytest.mark.parametrize("sv,versions,want", [
    ("verifier:aya-symbolic-ts@0.1.0;items:syn-pack@0.0.1", ["1.2.0"], "absent"),
    ("verifier:aya-symbolic-ts@0.1.0", None, "absent"),
    (None, ["1.2.0"], "absent"),
    ("algo:aya-priority@0.1.0;graph:aya-map@1.2.0", ["1.2.0"], "match"),
    ("graph:aya-map@1.2.0", ["1.1.0", "1.2.0"], "match"),
    ("graph:aya-map@1.2.0", ["1.3.0"], "mismatch"),
    ("graph:aya-map@1.2.0", None, "unbound"),
    ("graph:aya-map@1.2.0", [], "unbound"),
    ("graph:wenli-map@1.2.0", ["1.2.0"], "mismatch"),        # 名称不是本科
    ("graph:wenli-map@1.2.0", None, "mismatch"),             # 名称不对时，没有绑定也是 mismatch
    ("graph:aya-map@1.2.0;graph:aya-map@1.2.0", ["1.2.0"], "mismatch"),  # 两个以上 graph: 段
    ("graph:aya-maps@1.2.0", ["1.2.0"], "mismatch"),
])
def test_classify(sv, versions, want):
    assert map_check.classify("aya", sv, versions) == want


# ---------- 写入路径 ----------
def _counter(outcome: str) -> float:
    return metrics.MAP_VERSION_CHECK._m.labels(outcome=outcome)._value.get()


async def _bind(admin, body: dict, version: str) -> None:
    """本测试专用的内容版本绑定一张合成地图（与 test_evidence_attributes 的地图用例同法）"""
    cv = body["context"]["content_version"]
    cv["content_version"] = f"0.{uuid.uuid4().int % 900 + 100}.{uuid.uuid4().int % 90 + 10}"
    map_id = str(uuid.uuid4())
    async with admin.acquire() as c:
        await c.execute("INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ($1::uuid, 'aya', 2022, $2)",
                        map_id, version)
        await c.execute(
            """INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version, map_version_id)
               VALUES ($1, $2, $3, $4, 'aya', $5, $6::uuid)""",
            cv["region"], cv["exam_year"], cv["grade"], cv["textbook_version"], cv["content_version"], map_id)


def _declare(graph: str | None):
    def mutate(out):
        if graph is not None:
            out["source_version"] = out["source_version"] + ";" + graph
        return out
    return mutate


def _fresh_version() -> str:
    return f"9.{uuid.uuid4().int % 1000}.{uuid.uuid4().int % 1000}"


@pytest.mark.parametrize("method", ["grade", "diagnose"])
async def test_mismatch_is_rejected_and_nothing_written(api, pools, student, fake_adapter, method):
    _, admin = pools
    body = to_v1_body(grade_body(student) if method == "grade" else diagnose_body(student, [grade_body(student)["attempt"]]))
    bound = _fresh_version()
    await _bind(admin, body, bound)
    fake_adapter.mutate = _declare(f"graph:aya-map@{bound}.1")
    before = _counter("mismatch")
    r = await api.post(f"/internal/v1/{method}", json=env(body))
    assert r.status_code == 502 and r.json()["code"] == "ADAPTER_RESULT_REJECTED", r.text
    assert r.json()["detail"] == "map version mismatch"  # 固定的编码说明（与键盘核对同口径），不带版本号
    assert all(n == 0 for n in (await counts(admin, student)).values())
    assert _counter("mismatch") == before + 1
    # 适配器改正后同一个键可以重试（拒收是确定失败，幂等记录不卡住）
    fake_adapter.mutate = _declare(f"graph:aya-map@{bound}")
    r = await api.post(f"/internal/v1/{method}", json=env(body))
    assert r.status_code == 200, r.text


async def test_match_writes_with_bound_map(api, pools, student, fake_adapter):
    _, admin = pools
    body = to_v1_body(grade_body(student))
    bound = _fresh_version()
    await _bind(admin, body, bound)
    fake_adapter.mutate = _declare(f"graph:aya-map@{bound}")
    before = _counter("match")
    assert (await api.post("/internal/v1/grade", json=env(body))).status_code == 200
    assert _counter("match") == before + 1
    async with admin.acquire() as c:
        got = await c.fetchval("""SELECT array_agg(DISTINCT v.version) FROM platform.evidence_event e JOIN kge.map_version v
                                   ON v.id = e.map_version_id WHERE e.student_id = $1::uuid""", student)
    assert got == [bound]


@pytest.mark.parametrize("graph,outcome", [(None, "absent"), ("graph:aya-map@1.0.0", "unbound")])
async def test_absent_and_unbound_write_as_before(api, pools, student, fake_adapter, graph, outcome):
    """内容版本没绑地图（conftest 的缺省内容版本就是这样）：没声明 → absent；声明了 → unbound，都照常写入"""
    _, admin = pools
    fake_adapter.mutate = _declare(graph)
    before = _counter(outcome)
    assert (await api.post("/internal/v1/grade", json=env(to_v1_body(grade_body(student))))).status_code == 200
    assert _counter(outcome) == before + 1
    assert (await counts(admin, student))["evidence_event"] > 0


async def test_wrong_subject_map_rejected_even_unbound(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.mutate = _declare("graph:wenli-map@1.0.0")
    r = await api.post("/internal/v1/grade", json=env(to_v1_body(grade_body(student))))
    assert r.status_code == 502
    assert (await counts(admin, student))["evidence_event"] == 0
