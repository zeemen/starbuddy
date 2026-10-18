"""KGE-11 文件式编入事件：原子变更清单 → 编译任务清单（复用 plan_jobs，结果一致；缺省须列全差异）、来源类型与原子类型交叉校验、
兜底检索信号并入同一清单（命中键校验、最小门槛）、命令行子命令（原子写、拒绝覆盖输入）。全部是合成数据（规则 8）。"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import APPLY, LINEAR, PAGES, SQUARE, TRANSPOSE, map_doc, node_rec
from knowledge.atoms import diff_snapshots, parse_snapshot
from knowledge.compiler.events import (CHANGES_FORMAT, DEFAULT_SIGNAL_MIN_GROUP, PLAN_FORMAT, SOURCE_ATOM_TYPES, EventError, build_plan,
                                       load_signals, parse_changes, plan_from_changes)
from knowledge.compiler.jobs import plan_jobs
from knowledge.compiler.output import load_repo
from knowledge.retrieval.signals import GapSignal, JsonlSink

SVC = Path(__file__).resolve().parents[1]


def mutate(d):
    node_rec(d, LINEAR)["name"] = "一元一次方程（合成改名）"  # 节点改名：本页高风险，相邻页的前置与后续随之变化
    d["region_projections"][0]["weight"] = 0.06  # 应用页低风险
    d["region_projections"].append({"id": "syn-rp-003", "node_id": SQUARE, "region": "syn-region", "exam_year": 2025,
                                    "weight": 0.02, "source_ref": "synthetic"})  # 新增原子
    d["textbook_projections"] = [p for p in d["textbook_projections"] if p["id"] != "syn-tp-002"]  # 下架原子


SOURCES = {f"node:{LINEAR}": "map_edit", "region:syn-rp-001": "exam_spec", "region:syn-rp-003": "past_paper",
           "projection:syn-tp-002": "textbook"}


@pytest.fixture()
def snaps():
    old = parse_snapshot(map_doc())
    doc = map_doc()
    mutate(doc)
    return old, replace(parse_snapshot(doc), map_version="0.9.1")


@pytest.fixture()
def pages():
    return {pid: f[1] for pid, f in load_repo(PAGES).files.items()}


def manifest(old, new, keys=None, **over):
    diff = diff_snapshots(old, new)
    changes = [{"atom": k, "change": v, "source_type": SOURCES[k]} for k, v in diff.items() if keys is None or k in keys]
    return {"format": CHANGES_FORMAT, "from_map_version": old.map_version, "to_map_version": new.map_version, "changes": changes, **over}


def as_tuples(jobs):
    return [(j.job_id, j.page_id, j.reason, j.triggers, j.rebuild_kind, j.risk_estimate, j.impact, j.priority) for j in jobs]


def plan(pages, old, new, raw, **kw):
    frm, to, items = parse_changes(raw)
    return plan_from_changes(pages, old, new, items, frm, to, **kw)


def test_full_manifest_equals_plan_jobs(snaps, pages):
    old, new = snaps
    raw = manifest(old, new)
    assert {c["atom"] for c in raw["changes"]} == set(SOURCES)
    got, unlisted = plan(pages, old, new, raw)
    assert as_tuples(got) == as_tuples(plan_jobs(pages, old, new)) and got and unlisted == 0
    p = build_plan(pages, old=old, new=new, changes=parse_changes(raw))
    assert p["format"] == PLAN_FORMAT and [j["page_id"] for j in p["jobs"]] == [j.page_id for j in got]
    assert set(p["jobs"][0]) == {"job_id", "page_id", "reason", "rebuild_kind", "risk_estimate", "impact", "priority", "status", "triggers"}
    srcs = {t["atom"]: t["source_type"] for j in p["jobs"] for t in j["triggers"]}
    assert srcs.items() <= SOURCES.items()
    assert p["summary"]["changes"] == 4 and p["summary"]["jobs"] == len(got)
    assert p["summary"]["unlisted"] == 0 and p["summary"]["partial"] is False
    assert (p["from_map_version"], p["to_map_version"]) == ("0.9.0", "0.9.1")


def test_partial_manifest_requires_allow_partial(snaps, pages):
    old, new = snaps
    raw = manifest(old, new, keys={"region:syn-rp-003"})
    with pytest.raises(EventError, match="漏列了 3 条"):
        plan(pages, old, new, raw)
    jobs, unlisted = plan(pages, old, new, raw, allow_partial=True)
    assert unlisted == 3
    assert [(j.page_id, [(t.key, t.change) for t in j.triggers], j.risk_estimate) for j in jobs] == \
        [(SQUARE, [("region:syn-rp-003", "added")], "low")]
    jobs, _ = plan(pages, old, new, manifest(old, new, keys={"projection:syn-tp-002"}), allow_partial=True)
    assert [(j.page_id, j.risk_estimate) for j in jobs] == [(TRANSPOSE, "high")]  # 教材投影下架：高风险
    p = build_plan(pages, old=old, new=new, changes=parse_changes(raw), allow_partial=True)
    assert p["summary"]["unlisted"] == 3 and p["summary"]["partial"] is True


def test_manifest_must_match_snapshots(snaps, pages):
    old, new = snaps
    m = manifest(old, new)
    m["changes"][0]["change"] = "removed" if m["changes"][0]["change"] != "removed" else "added"
    with pytest.raises(EventError, match="快照差异不符"):
        plan(pages, old, new, m)
    with pytest.raises(EventError, match="地图版本"):
        plan(pages, old, new, manifest(old, new, to_map_version="9.9.9"))
    unchanged = {"format": CHANGES_FORMAT, "from_map_version": "0.9.0", "to_map_version": "0.9.1",
                 "changes": [{"atom": f"node:{APPLY}", "change": "modified", "source_type": "map_edit"}]}
    with pytest.raises(EventError, match="无变化"):
        plan(pages, old, new, unchanged, allow_partial=True)


@pytest.mark.parametrize("bad", [
    {"format": "kge-atom-changes/2"},
    {"changes": [{"atom": "node:MATH.SYN.EQ.LINEAR", "change": "modified"}]},
    {"changes": [{"atom": "student:s1", "change": "modified", "source_type": "map_edit"}]},
    {"changes": [{"atom": "node:not an id", "change": "modified", "source_type": "map_edit"}]},
    {"changes": [{"atom": "node:MATH.SYN.EQ.LINEAR", "change": "renamed", "source_type": "map_edit"}]},
    {"changes": [{"atom": "node:MATH.SYN.EQ.LINEAR", "change": "modified", "source_type": "chat_log"}]},
    {"changes": [{"atom": "node:MATH.SYN.EQ.LINEAR", "change": "modified", "source_type": "map_edit"}] * 2},
    {"changes": [{"atom": "node:MATH.SYN.EQ.LINEAR", "change": "modified", "source_type": "past_paper"}]},  # 真题不会改节点
    {"changes": [{"atom": "projection:syn-tp-001", "change": "modified", "source_type": "exam_spec"}]},
    {"student_id": "x"},
    {"from_map_version": None},
])
def test_manifest_rejects_malformed(bad):
    raw = {"format": CHANGES_FORMAT, "from_map_version": "0.9.0", "to_map_version": "0.9.1", "changes": [], **bad}
    with pytest.raises(EventError):
        parse_changes(raw)


def test_source_atom_type_whitelist_covers_every_atom_type():
    from knowledge.atoms import ATOM_TYPES
    assert {t for ts in SOURCE_ATOM_TYPES.values() for t in ts} == set(ATOM_TYPES)


def _signals(tmp_path, rows):
    path = tmp_path / "gap.jsonl"
    sink = JsonlSink(path)
    for s in rows:
        sink.emit(s)
    return load_signals(path)


ROWS = [GapSignal(LINEAR, "page_low_coverage", "a" * 16, 3, ("atom:node:MATH.SYN.EQ.LINEAR", "page:MATH.SYN.EQ.LINEAR@1.0.0#key_points"), 0.4),
        GapSignal(LINEAR, "page_low_coverage", "b" * 16, 2, (), 0.3),
        GapSignal("MATH.SYN.EQ.NEW", "no_page", "c" * 16, 2, (), 0.2),
        GapSignal(None, "no_node", "d" * 16, 1, (), 0.0)]


def test_signals_join_the_same_plan(tmp_path, pages):
    p = build_plan(pages, signals=_signals(tmp_path, ROWS), signal_min_group=1)
    assert p["jobs"] == [] and p["from_map_version"] is None
    assert [(s["node_id"], s["action"], s["count"], s["distinct_queries"], s["page_id"]) for s in p["signals"]] == [
        (LINEAR, "review_page", 2, 2, LINEAR), (None, "triage", 1, 1, None), ("MATH.SYN.EQ.NEW", "new_page", 1, 1, None)]
    assert all(s["risk_estimate"] == "high" for s in p["signals"]) and p["summary"]["signals_below_threshold"] == 0
    assert "query" not in json.dumps(p).replace("distinct_queries", "")  # 不带查询原文（摘要也不进清单）


def test_signals_below_threshold_are_only_counted(tmp_path, pages):
    assert DEFAULT_SIGNAL_MIN_GROUP == 20
    p = build_plan(pages, signals=_signals(tmp_path, ROWS))
    assert p["signals"] == [] and p["summary"]["signals_below_threshold"] == 3 and p["summary"]["signal_min_group"] == 20
    many = [GapSignal(LINEAR, "page_low_coverage", f"{k:016x}", 2, (), 0.1) for k in range(20)]
    many += [GapSignal(APPLY, "page_low_coverage", "e" * 16, 2, (), 0.1)] * 25  # 次数够，但只有一种问法
    p = build_plan(pages, signals=many)
    assert [(s["node_id"], s["count"], s["distinct_queries"]) for s in p["signals"]] == [(LINEAR, 20, 20)]
    assert p["summary"]["signals_below_threshold"] == 1
    with pytest.raises(EventError):
        build_plan(pages, signals=[], signal_min_group=0)


SIG = {"format": "kge-gap-signal/1", "node_id": None, "reason": "no_node", "query_digest": "a" * 16, "query_terms": 1, "hits": [],
       "top_score": 0.0}


@pytest.mark.parametrize("line", [
    "not json",
    json.dumps({**SIG, "query": "原文"}),
    json.dumps({**SIG, "reason": "bored"}),
    json.dumps({**SIG, "node_id": "bad id"}),
    json.dumps({**SIG, "query_digest": "XYZ"}),
    json.dumps({**SIG, "query_digest": "A" * 16}),
    json.dumps({**SIG, "hits": ["学生问的原话"]}),
    json.dumps({**SIG, "hits": ["page:MATH.SYN.EQ.LINEAR#key_points"]}),
    json.dumps({**SIG, "hits": ["page:not-an-id@1.0.0#key_points"]}),
    json.dumps({**SIG, "hits": ["page:MATH.syn.bad@1.0.0#key_points"]}),  # 形似页面键、节点 ID 不合规范
    json.dumps({**SIG, "hits": ["atom:student:s1"]}),
    json.dumps({**SIG, "hits": ["atom:node:bad id"]}),
    json.dumps({**SIG, "query_terms": True}),
])
def test_signals_reject_malformed(tmp_path, line):
    p = tmp_path / "s.jsonl"
    p.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(EventError):
        load_signals(p)


def test_changes_require_both_snapshots(pages, snaps):
    old, new = snaps
    with pytest.raises(EventError):
        build_plan(pages, old=old, changes=parse_changes(manifest(old, new)))


def _cli(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", module, *args], capture_output=True, text=True, cwd=SVC,
                          env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}, timeout=120, check=False)


def _write_inputs(tmp_path, keys=None):
    old = parse_snapshot(map_doc())
    new_doc = map_doc()
    mutate(new_doc)
    new_doc["map_version"] = "0.9.1"
    (tmp_path / "old.json").write_text(json.dumps(map_doc(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "new.json").write_text(json.dumps(new_doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "changes.json").write_text(json.dumps(manifest(old, parse_snapshot(new_doc), keys=keys)), encoding="utf-8")
    return ["--changes", str(tmp_path / "changes.json"), "--old-snapshot", str(tmp_path / "old.json"),
            "--new-snapshot", str(tmp_path / "new.json")]


def test_cli_plan_events_and_semantic_affected(tmp_path):
    args = _write_inputs(tmp_path, keys={"region:syn-rp-003"})
    out = tmp_path / "plan.json"
    r = _cli("knowledge.compiler", "plan-events", "--pages", str(PAGES), *args, "--out", str(out))
    assert r.returncode == 2 and "漏列" in r.stderr and not out.exists()  # 缺省须列全
    r = _cli("knowledge.compiler", "plan-events", "--pages", str(PAGES), *args, "--allow-partial", "--out", str(out))
    assert r.returncode == 0, r.stderr
    p = json.loads(out.read_text(encoding="utf-8"))
    assert [j["page_id"] for j in p["jobs"]] == [SQUARE] and p["jobs"][0]["triggers"][0]["source_type"] == "past_paper"
    assert p["summary"]["unlisted"] == 3 and "漏列差异 3 条" in r.stdout
    assert sorted(x.name for x in tmp_path.iterdir()) == ["changes.json", "new.json", "old.json", "plan.json"]  # 没有残留临时文件
    # 编入清单直接作为语义体检的受影响页：平方差公式页没有带页面的相邻页，只查 1 页；清单的目标地图版本必须与快照一致
    r = _cli("knowledge.semantic", str(PAGES), "--map-snapshot", str(tmp_path / "new.json"), "--affected", str(out))
    assert r.returncode == 0 and "1 页（比对 0 对相邻页）" in r.stdout, r.stdout + r.stderr
    r = _cli("knowledge.semantic", str(PAGES), "--map-snapshot", str(tmp_path / "old.json"), "--affected", str(out))
    assert r.returncode == 2 and "地图版本" in r.stderr


def test_cli_plan_events_rejects_bad_arguments_and_outputs(tmp_path, pages_dir):
    args = _write_inputs(tmp_path)
    ok = _cli("knowledge.compiler", "plan-events", "--pages", str(pages_dir), *args)
    assert ok.returncode == 0 and json.loads(ok.stdout)["format"] == PLAN_FORMAT
    assert _cli("knowledge.compiler", "plan-events", "--pages", str(pages_dir)).returncode == 2
    assert _cli("knowledge.compiler", "plan-events", "--pages", str(pages_dir), "--changes", str(tmp_path / "changes.json")).returncode == 2
    swapped = ["--changes", args[1], "--old-snapshot", args[5], "--new-snapshot", args[3]]
    assert _cli("knowledge.compiler", "plan-events", "--pages", str(pages_dir), *swapped).returncode == 2
    before = (tmp_path / "changes.json").read_bytes()
    r = _cli("knowledge.compiler", "plan-events", "--pages", str(pages_dir), *args, "--out", str(tmp_path / "changes.json"))
    assert r.returncode == 2 and "输入文件" in r.stderr and (tmp_path / "changes.json").read_bytes() == before
    r = _cli("knowledge.compiler", "plan-events", "--pages", str(pages_dir), *args, "--out", str(pages_dir / "kp" / "plan.json"))
    assert r.returncode == 2 and "页面目录" in r.stderr and not (pages_dir / "kp" / "plan.json").exists()
    # 不带子命令时编译器命令行照旧（必填参数缺失 → argparse 退出码 2）
    assert _cli("knowledge.compiler", "--pages", str(pages_dir)).returncode == 2
