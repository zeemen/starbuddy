"""计划完成 / 跳过的统计口径（PLT-15 PR2，insight/plan_tasks.py）：计划完成率与周报客观栏同一口径——同一任务以最后一次记录为准、
计入全部来源（是否只计 source = plan 待人确认，写在 config/plan-tasks.json）。全部合成数据。"""
from __future__ import annotations

import inspect
import json
import random
from datetime import timedelta

import pytest

from insight import metrics as m
from insight import plan_tasks as pt
from insight import store_weekly
from insight.plan_tasks import PlanCounting, PlanRecord, last_per_task, parse_plan_counting, plan_outcome_counts
from insight.policy import CONFIG_DIR
from support import T0, make_policy

P = make_policy()
NOW = T0


def rec(i: int, kind: str, task: str, minutes: float) -> PlanRecord:
    return PlanRecord(f"00000000-0000-4000-8000-{i:012d}", kind, task, NOW - timedelta(minutes=minutes))


def test_last_record_wins_in_both_directions():
    xs = [rec(1, "plan_skip", "a", 30), rec(2, "plan_done", "a", 20),   # 先跳过后完成 → 完成
          rec(3, "plan_done", "b", 30), rec(4, "plan_skip", "b", 20),   # 先完成后跳过 → 跳过
          rec(5, "plan_done", "c", 30), rec(6, "plan_done", "c", 20),   # 重复完成 → 一个任务
          rec(7, "session_start", "d", 10), rec(8, "plan_done", "", 10)]  # 别的事件、没有任务 ID：不计
    assert last_per_task(xs) == {"a": "plan_done", "b": "plan_skip", "c": "plan_done"}
    assert plan_outcome_counts(xs) == (2, 1)
    # 输入顺序无关：按 (发生时间, 记录 ID) 排序；同一时刻按记录 ID
    assert last_per_task(reversed(xs)) == last_per_task(xs)
    tie = [rec(2, "plan_skip", "e", 5), rec(1, "plan_done", "e", 5)]
    assert last_per_task(tie) == {"e": "plan_skip"} == last_per_task(list(reversed(tie)))
    # 记录 ID 的顺序与时间相反时以时间为准
    late_first_id = [rec(11, "plan_skip", "f", 5), rec(12, "plan_done", "f", 50)]
    assert last_per_task(late_first_id) == {"f": "plan_skip"}


def test_metric_and_weekly_agree_on_random_histories():
    """同一批记录：计划完成率的 n、v 与周报客观栏的完成 / 跳过数一致（同一口径）"""
    rnd = random.Random(20261015)
    for _ in range(200):
        xs = [rec(i, rnd.choice(pt.PLAN_KINDS), f"t{rnd.randrange(6)}", rnd.uniform(0, 60 * 24 * 20)) for i in range(rnd.randrange(1, 25))]
        xs.append(rec(999, "plan_done", "t0", 60 * 24 * 15))  # 保证跨两周，指标才出结果
        done, skipped = plan_outcome_counts(xs)
        facts = [m.PlanFact(r.record_id, r.kind, r.plan_task_id, r.occurred_at) for r in xs]
        res = m.plan_completion_rate(m.MetricInputs(plans=tuple(facts)), P, NOW)
        assert len(res) == 1
        assert res[0].value == {"v": round(done / (done + skipped), 4), "n": done + skipped}


def test_both_callers_use_the_shared_rule():
    """指标与周报都调同一个函数，不各自再写一套（周报原来“只要有完成就算完成”）"""
    metric_src = inspect.getsource(m.plan_completion_rate)
    weekly_src = inspect.getsource(store_weekly.objective_facts)
    assert "last_per_task(" in metric_src and "plan_outcome_counts(" in weekly_src
    for src in (metric_src, weekly_src):
        assert "- done" not in src and "setdefault" not in src


def test_config_counts_all_sources_and_marks_pending_decision():
    doc = json.loads((CONFIG_DIR / "plan-tasks.json").read_text(encoding="utf-8"))
    assert doc["count_sources"] == "all"
    assert "待人确认" in doc["$comment"] and "source = plan" in doc["$comment"]
    assert pt.load_plan_counting() == PlanCounting("all") == pt.default_counting()


def test_counting_config_is_pinned_to_algorithm_versions():
    """改口径配置必须同时升指标与周报的算法版本（版本写进判断与周报快照，规则 6）：配置、版本对应表、两个版本常量三者锁在一起"""
    from insight import weekly
    doc = json.loads((CONFIG_DIR / "plan-tasks.json").read_text(encoding="utf-8"))
    assert set(pt.COUNTING_VERSIONS) == set(pt.SUPPORTED_SOURCES)
    assert pt.COUNTING_VERSIONS[doc["count_sources"]] == (m.ALGORITHM_VERSION, weekly.ALGORITHM_VERSION)
    assert m.MODEL_VERSION.endswith(f"@{m.ALGORITHM_VERSION}")
    # 不缓存：改了配置文件，下一次计数就按新内容（这里用不支持的值验证会被现读并拒绝）
    assert not hasattr(pt.default_counting, "cache_info")


def test_counting_is_read_on_every_call(tmp_path, monkeypatch):
    cfg = tmp_path / "plan-tasks.json"
    cfg.write_text(json.dumps({"$comment": "", "schema_version": "plan-tasks/0.1", "count_sources": "all"}), encoding="utf-8")
    monkeypatch.setattr(pt, "CONFIG_DIR", tmp_path)
    assert last_per_task([rec(1, "plan_done", "a", 1)]) == {"a": "plan_done"}
    cfg.write_text(json.dumps({"$comment": "", "schema_version": "plan-tasks/0.1", "count_sources": "plan"}), encoding="utf-8")
    with pytest.raises(ValueError):
        last_per_task([rec(1, "plan_done", "a", 1)])


@pytest.mark.parametrize("bad", [
    {"$comment": "", "schema_version": "plan-tasks/0.1", "count_sources": "plan"},     # 只计 plan：待人确认，且需授权迁移
    {"$comment": "", "schema_version": "plan-tasks/0.1", "count_sources": "ALL"},
    {"$comment": "", "schema_version": "plan-tasks/0.2", "count_sources": "all"},
    {"$comment": "", "schema_version": "plan-tasks/0.1"},
    {"$comment": "", "schema_version": "plan-tasks/0.1", "count_sources": "all", "extra": 1},
])
def test_config_rejects_unsupported(bad):
    with pytest.raises(ValueError):
        parse_plan_counting(bad)


def test_unsupported_counting_is_not_silently_treated_as_all():
    with pytest.raises(ValueError):
        last_per_task([rec(1, "plan_done", "a", 1)], PlanCounting("plan"))


def test_wording_never_claims_mastery_or_verification():
    """周报的计划栏与指标说明只说“自己标记”的记录，不说成已掌握或已核实完成（规则 6、11）"""
    texts = [pt.__doc__ or "", json.loads((CONFIG_DIR / "plan-tasks.json").read_text(encoding="utf-8"))["$comment"]]
    for t in texts:
        assert "不是核实" in t or "待人确认" in t
    weekly_doc = store_weekly.ObjectiveFacts.__doc__ or ""
    assert "不是核实过的完成" in weekly_doc and "不代表掌握" in weekly_doc
