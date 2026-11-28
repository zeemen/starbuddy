"""讲评课目录（learning/lesson/catalog.py，specs/lesson-catalog PR B）的单测：不连库，适配器用 httpx.MockTransport 假扮。

覆盖：缓存与过期、题目引用 → 讲评课引用（同一道题取编号版本最小的一节）、目录暂时取不到时 by_item 为空、没有讲评课的学科不调适配器、
请求形状（PresentItemContext：不带学生、不计量，L1）。全部合成数据（规则 8）。
"""
from __future__ import annotations

import json

import httpx
import pytest

from conftest import ADAPTER_CREDENTIAL
from learning.config import AdapterTarget
from learning.errors import IngestError
from learning.lesson.catalog import LessonCatalog, project


@pytest.fixture(autouse=True)
def candidate(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")


def entry(lid: str, item: str | None = "aya:SYN-1", version: str = "0.1.0") -> dict:
    e = {"lesson_ref": {"lesson_id": lid, "lesson_version": version}, "skill_node": "aya:K-SYN-0001", "title": "（合成）讲评课", "step_count": 3}
    if item is not None:
        e["item_ref"] = item
    return e


def envelope(lessons: list[dict]) -> dict:
    return {"contract_version": "1.1.0", "result": {"lessons": lessons}, "evidence_ids": [], "confidence": 1,
            "source_version": "algo:aya-lesson-list@0.1.0;items:aya-lessons@syn00001", "model_version": "algo:aya-lesson-list@0.1.0",
            "human_review_required": False, "semantics": "practice"}


class Fake:
    def __init__(self, lessons: list[dict]) -> None:
        self.lessons, self.status, self.calls = lessons, 200, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/plugin/v0/list_lessons")
        self.calls.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"code": "UPSTREAM_UNAVAILABLE", "retryable": True})
        return httpx.Response(200, json=envelope(self.lessons))


def catalog(fake: Fake, now: list[float]) -> LessonCatalog:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return LessonCatalog(client, {"aya": AdapterTarget("http://aya-adapter.invalid", ADAPTER_CREDENTIAL)}, ttl=300, clock=lambda: now[0])


async def test_cache_expires_after_ttl_and_request_shape():
    fake, now = Fake([entry("syn.lesson.b"), entry("syn.lesson.a")]), [0.0]
    c = catalog(fake, now)
    first = await c.entries("aya")
    assert [e.lesson_id for e in first] == ["syn.lesson.a", "syn.lesson.b"], "按编号排序"
    now[0] = 299.0
    assert await c.entries("aya") == first and len(fake.calls) == 1
    now[0] = 301.0
    await c.entries("aya")
    assert len(fake.calls) == 2, "5 分钟后重新取"
    ctx = fake.calls[0]["context"]
    assert set(ctx) == {"request_id", "contract_version", "subject", "data_class"} and ctx["data_class"] == "L1"
    assert "student_ref" not in ctx and "usage_id" not in ctx


async def test_by_item_first_lesson_per_item_and_degrades_to_empty():
    fake, now = Fake([entry("syn.lesson.b", "aya:SYN-1"), entry("syn.lesson.a", "aya:SYN-1", "0.2.0"), entry("syn.lesson.a", "aya:SYN-1"),
                      entry("syn.lesson.c", None), entry("syn.lesson.d", "aya:SYN-2")]), [0.0]
    c = catalog(fake, now)
    assert await c.by_item("aya") == {"aya:SYN-1": {"lesson_id": "syn.lesson.a", "lesson_version": "0.1.0"},
                                      "aya:SYN-2": {"lesson_id": "syn.lesson.d", "lesson_version": "0.1.0"}}
    down, now2 = Fake([entry("syn.lesson.a")]), [0.0]
    down.status = 503
    c2 = catalog(down, now2)
    assert await c2.by_item("aya") == {}, "目录暂时取不到：条目照常列出，只是不带讲评课"
    with pytest.raises(IngestError) as e:
        await c2.entries("aya")
    assert e.value.status == 503 and e.value.retryable


async def test_subject_without_lessons_does_not_call_adapter():
    fake, now = Fake([entry("syn.lesson.a")]), [0.0]
    c = catalog(fake, now)
    assert await c.entries("zhixing") == () and await c.by_item("wenli") == {} and fake.calls == []


def test_project_rejects_out_of_contract_entries():
    ok = envelope([entry("syn.lesson.a")])
    assert [e.title for e in project(ok, "aya")] == ["（合成）讲评课"]
    for bad in (envelope([{**entry("syn.lesson.a"), "dsl": {}}]), envelope([{**entry("syn.lesson.a"), "skill_node": "zhixing:K-1"}]),
                envelope([entry("syn.lesson.a"), entry("syn.lesson.a")]), {**ok, "evidence_ids": ["00000000-0000-4000-8000-0000000000e1"]}):
        with pytest.raises(IngestError) as e:
            project(bad, "aya")
        assert e.value.code == "ADAPTER_RESULT_REJECTED"
