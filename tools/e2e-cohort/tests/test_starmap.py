"""星图截图（tools/e2e-cohort/e2e_cohort/starmap.py）的单测与反例：对账规则、跨学生 404、选科严格相等、节点身份取自 BFF JSON、
只在一次性库上写登录号码、进程组清理、测试构建目录原子占位、.env 拒绝、失败删图、截图来历。不需要数据库与浏览器。
make gate0-evidence GATE0_STARMAP=1 在截图前先跑这里。全部合成数据（规则 8）。
"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT / "tools/e2e-cohort", ROOT / "services/learning/tests"):
    sys.path.insert(0, str(p))

from e2e_cohort import starmap as SM  # noqa: E402
from e2e_cohort.cohort import PATTERNS, generate  # noqa: E402
from e2e_cohort.starmap import (  # noqa: E402
    RULES, WATERMARK, StarmapError, build_record, drop_images, evidence_nodes, plan_shots, reconcile, select_students,
    write_provenance,
)

SAMPLE_NODES = (ROOT / "tools/e2e-cohort/sample/nodes.csv").read_text(encoding="utf-8")
A, B = "00000000-0000-4000-8000-00000000000a", "00000000-0000-4000-8000-00000000000b"
# 登记表：local_id → label_code（故意不相等，节点身份以 local_id 为准）
CATALOG = {"aya": {f"K-{i:03d}": f"数-{i:03d}" for i in range(10)}, "zhixing": {f"Z-{i:02d}": f"道-{i:02d}" for i in range(4)},
           "echo": {}}
NODES_CSV = (
    "label,student_id,subject,node_id,evidence_count,observations_demonstrated,observations_not_demonstrated,observations_other,status\n"
    f"S01,{A},aya,K-001,2,1,0,0,evidence_only\n"
    f"S01,{A},aya,K-002,1,0,1,0,evidence_only\n"
    f"S01,{A},zhixing,Z-01,1,1,0,0,evidence_only\n"
    f"S01,{A},zhixing,-,1,0,0,0,evidence_only\n"
    f"S02,{B},aya,K-007,1,1,0,0,evidence_only\n"
)
ACTIVE = {"aya", "zhixing", "echo"}  # echo 没有已登记能力点：BFF 不画这片星空，期望集合也不计
EV = evidence_nodes(NODES_CSV)


def page(student: str = A, stateful: dict[str, dict[str, str]] | None = None, extra: dict[str, list[str]] | None = None,
         subjects: tuple[str, ...] = ("aya", "zhixing"), cross: tuple[int, int] = (404, 404)) -> dict:
    """按 v0 的形状造一张页的结果：BFF JSON（全部登记节点，默认未亮）+ 与之一致的 DOM 计数 + 跨学生状态码"""
    stateful, extra = stateful or {}, extra or {}
    api_skies, dom_skies = [], []
    for subj in subjects:
        nodes = sorted(CATALOG[subj]) + extra.get(subj, [])
        stars = [{"skill": f"{subj}:{n}", "label_code": CATALOG[subj].get(n, n), "display": stateful.get(subj, {}).get(n, "unlit"),
                  "retained": False, "needs_verification": False} for n in nodes]
        cnt = {d: sum(1 for s in stars if s["display"] == d) for d in ("unlit", "half", "lit")}
        api_skies.append({"subject": subj, "enrollment_status": "active", "stars": stars, "summary": dict(cnt)})
        rows = [{"display": s["display"], "retained": False, "needs_verification": False} for s in stars]
        dom_skies.append({"subject": subj, "rows": rows, "svg": {"total": len(rows), **cnt, "retained": 0}, "summary": dict(cnt)})
    return {"dom": {"watermark": WATERMARK, "empty": False, "skies": dom_skies},
            "api": {"student_id": student, "generated_at": "2026-10-01T00:00:00Z", "skies": api_skies},
            "cross": {"page_status": cross[0], "bff_status": cross[1]}}


def rec(result: dict, student: str = A, active: set[str] = ACTIVE, **kw) -> dict:
    return reconcile(result, student, active, EV, CATALOG, **kw)


def test_evidence_nodes_ignores_dash_and_keys_by_student_and_subject():
    assert EV == {(A, "aya"): {"K-001", "K-002"}, (A, "zhixing"): {"Z-01"}, (B, "aya"): {"K-007"}}


def test_neutral_v0_page_reconciles():
    r = rec(page())
    assert r["ok"], r["problems"]
    assert r["skies"][0] == {"subject": "aya", "stars_total": 10, "unlit": 10, "half": 0, "lit": 0, "retained": 0,
                             "needs_verification": 0, "registered_nodes": 10, "evidence_nodes": 2, "evidence_nodes_on_page": 2,
                             "stateful_stars": 0}
    assert r["cross_student"] == {"page_status": 404, "bff_status": 404}


def test_rules_state_v0_effect_honestly():
    """契约审查 1：规则 3–6 在 v0 下与学生无关或空真，记录里如实写明"""
    v0 = {r["rule"][0]: r["v0"] for r in RULES}
    assert "与学生无关" in v0["3"] and "与学生无关" in v0["4"] and "空真" in v0["5"]
    assert v0["1"].startswith("有效") and v0["7"].startswith("有效")


# ---------------- 规则 7：跨学生 404（契约审查 2） ----------------
@pytest.mark.parametrize("cross", [(200, 404), (404, 200), (None, 404), (404, 403)])
def test_cross_student_must_be_404_on_page_and_bff(cross):
    r = rec(page(cross=cross))
    assert not r["ok"] and any("未绑定学生" in p for p in r["problems"])


# ---------------- 规则 1：库里 active 选科严格相等（契约审查 3） ----------------
def test_subjects_must_equal_active_enrollment():
    assert not rec(page(subjects=("aya",)))["ok"], "少一片在读科目的星空"
    r = rec(page(), active={"aya"})
    assert not r["ok"] and any("active 选科" in p for p in r["problems"]), "多一片未在读（或已暂停）科目的星空"
    dom_only = page()
    dom_only["dom"]["skies"] = dom_only["dom"]["skies"][:1]
    assert not rec(dom_only)["ok"], "页面星空与 JSON 星空不一致"


# ---------------- 节点身份取自 BFF JSON（契约审查 4） ----------------
def test_node_identity_from_json_and_label_code_checked_against_registry():
    bad = page()
    bad["api"]["skies"][0]["stars"][3]["label_code"] = "K-003"  # 不再假设 label_code = local_id
    r = rec(bad)
    assert not r["ok"] and any("label_code 与登记表不一致" in p for p in r["problems"])
    wrong_subject = page()
    wrong_subject["api"]["skies"][0]["stars"][0]["skill"] = "zhixing:Z-01"
    assert not rec(wrong_subject)["ok"]


def test_dom_counts_must_match_json():
    r1 = page()
    r1["dom"]["skies"][0]["svg"] = {**r1["dom"]["skies"][0]["svg"], "total": 11, "unlit": 11}
    assert not rec(r1)["ok"]
    r2 = page()
    r2["dom"]["skies"][0]["rows"][0]["display"] = "half"  # 页面渲染与接口不一致
    assert not rec(r2)["ok"]


def test_student_id_in_json_must_match():
    assert not rec(page(student=B))["ok"]


# ---------------- 规则 3–6 的反例 ----------------
def test_extra_node_of_another_student_fails():
    """反例：页面多出一颗不属于该学生的带状态的星（K-007 是 B 的证据节点）→ 对账失败；放开“必须中性”也失败"""
    r = rec(page(stateful={"aya": {"K-007": "half"}}))
    assert not r["ok"] and any("不是该学生的证据节点" in p for p in r["problems"])
    r2 = rec(page(stateful={"aya": {"K-007": "half"}}), require_neutral=False)
    assert not r2["ok"] and any("不是该学生的证据节点" in p for p in r2["problems"])


def test_cross_student_evidence_does_not_leak():
    """同一份星（K-001 有状态，是 A 的证据节点）：按 A 对账通过子集规则，按 B 对账失败"""
    a = page(stateful={"aya": {"K-001": "lit"}})
    assert rec(a, require_neutral=False)["ok"]
    b = copy.deepcopy(a)
    b["api"]["student_id"] = B
    rb = rec(b, student=B, require_neutral=False)
    assert not rb["ok"] and any("不是该学生的证据节点" in p for p in rb["problems"])


def test_stateful_star_fails_while_mastery_not_written():
    r = rec(page(stateful={"aya": {"K-001": "half"}}))
    assert not r["ok"] and any("中性底星" in p for p in r["problems"])


def test_unregistered_extra_node_fails():
    r = rec(page(extra={"aya": ["K-999"]}))
    assert not r["ok"] and r["problems"] == ["aya：页面多出 1 个未登记节点（如 K-999）"]


# ---------------- 选取、记录 ----------------
def _cohort():
    from e2e_cohort.replay import load_zhixing_items
    return generate({k: v["points"] for k, v in load_zhixing_items().items()})


def test_selection_is_fixed_and_cross_target_is_unbound():
    cohort = _cohort()
    chosen = select_students(cohort, 3)
    assert [s.pattern for s in chosen] == list(PATTERNS[:3])
    shots, phones = plan_shots(cohort, 3)
    assert [s["kind"] for s in shots] == ["student"] * 3 + ["guardian"]
    assert shots[-1]["path"] == f"/family/{chosen[0].student_id}/star-map"
    assert all(p.startswith("+999") for p in phones.values())
    hh = {s.student_id: s.household_id for s in cohort.students}
    for s in shots:
        assert hh[s["other_student_id"]] != hh[s["student_id"]], "跨学生对象必须不同家庭（对学生与家长都未绑定）"
        assert s["other_path"].endswith(f"/{s['other_student_id']}/star-map")
    with pytest.raises(ValueError):
        select_students(cohort, 0)
    ev = evidence_nodes(SAMPLE_NODES)
    sets = [frozenset(ev.get((s.student_id, "aya"), set())) for s in chosen]
    assert all(sets) and len(set(sets)) > 1


def _result(name: str, r: dict) -> dict:
    return {"name": name, "file": f"{name}.jpg", "bytes": 1, "file_sha256": "0" * 64, "pixel_sha256": "1" * 64,
            "pixel_size": {"width": 1, "height": 1}, **r}


PLAN = [{"name": "S01-student", "kind": "student", "label": "S01", "student_id": A, "pattern": "all_correct", "path": "/s/a/star-map",
         "other_label": "S02", "other_student_id": B, "other_path": "/s/b/star-map"},
        {"name": "S01-guardian", "kind": "guardian", "label": "S01", "student_id": A, "pattern": "all_correct",
         "path": "/family/a/star-map", "other_label": "S02", "other_student_id": B, "other_path": "/family/b/star-map"}]
FACTS = {"catalog": CATALOG, "active": {A: sorted(ACTIVE)}, "content_versions": {"aya": {"0.1.0"}}, "commit": {"commit": "c1"}}


def test_build_record_captured_and_failures():
    ok = build_record(PLAN, [_result("S01-student", page()), _result("S01-guardian", page())], NODES_CSV, FACTS, 1)
    assert ok["status"] == "captured" and len(ok["shots"]) == 2
    assert ok["registered_nodes"]["source"].startswith("公共登记表 platform.skill_node")
    assert ok["registered_nodes"]["content_versions"] == {"aya": ["0.1.0"]}
    assert ok["captured_at_commit"] == {"commit": "c1"}
    assert ok["shots"][0]["cross_student"]["page_status"] == 404
    bad = build_record(PLAN, [_result("S01-student", page()), _result("S01-guardian", page(subjects=("aya",)))], NODES_CSV, FACTS, 1)
    assert bad["status"] == "failed" and any("家长视角与学生视角不一致" in p for p in bad["reconciliation"]["problems"])
    assert build_record(PLAN, [_result("S01-student", page())], NODES_CSV, FACTS, 1)["status"] == "failed"
    nowm = page()
    nowm["dom"]["watermark"] = None
    assert build_record(PLAN[:1], [_result("S01-student", nowm)], NODES_CSV, FACTS, 1)["status"] == "failed"
    leak = build_record(PLAN[:1], [_result("S01-student", page(cross=(200, 200)))], NODES_CSV, FACTS, 1)
    assert leak["status"] == "failed"


# ---------------- 只在一次性库上写（契约审查 6） ----------------
def test_seed_logins_refuses_non_ephemeral_db():
    class Fake:
        db = "starbuddy_test"
        work = "/tmp/x"

        def psql(self, sql):  # pragma: no cover —— 不应被调用
            raise AssertionError("不应写库")
    with pytest.raises(StarmapError):
        SM.seed_logins(Fake(), {"g": "+99900009001"})


# ---------------- 安全审查 L1：按进程组清理 ----------------
def test_stop_group_kills_grandchildren(tmp_path):
    pidfile = tmp_path / "pid"
    p = SM._spawn(["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], tmp_path, {"PATH": "/usr/bin:/bin"}, tmp_path / "log")
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.05)
    grandchild = int(pidfile.read_text())
    SM.stop_group(p, grace=3)
    time.sleep(0.2)
    status = Path(f"/proc/{grandchild}/status")
    assert not status.exists() or "\nState:\tZ" in status.read_text(), "孙进程（组内）应已被清理"


# ---------------- 安全审查 L2：.next-e2e 原子占位、只删自己建的 ----------------
def test_test_build_refuses_existing_dist_and_keeps_it(tmp_path):
    dist = tmp_path / SM.DIST_DIR
    dist.mkdir()
    (dist / "someone-else").write_text("x")
    with pytest.raises(StarmapError):
        with SM.test_build({}, tmp_path, web=tmp_path):
            pass
    assert (dist / "someone-else").exists(), "别人的构建目录不能删"


def test_test_build_removes_own_dist_on_failure(tmp_path):
    with pytest.raises(StarmapError):
        with SM.test_build({"PATH": "/nonexistent"}, tmp_path, web=tmp_path):
            pass
    assert not (tmp_path / SM.DIST_DIR).exists()


# ---------------- 安全审查 L3：apps/web/.env* 拒绝运行（只看文件名） ----------------
def test_env_file_in_web_dir_refuses(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text("")
    monkeypatch.setattr(SM, "WEB", tmp_path)
    why = SM.missing_prerequisites()
    assert why and ".env.local" in why


# ---------------- A：失败删图；B：截图来历 ----------------
def test_drop_images_only_removes_shots(tmp_path):
    d = tmp_path / "starmap"
    d.mkdir()
    for n in ("S01-student.jpg", "S01-guardian.jpg", "starmap.json", "other.jpg"):
        (d / n).write_text("x")
    assert drop_images(tmp_path) == ["S01-student.jpg", "S01-guardian.jpg"]
    assert sorted(p.name for p in d.iterdir()) == ["other.jpg", "starmap.json"]


def test_provenance_marks_reused_screenshots(tmp_path):
    assert write_provenance(tmp_path, "c2") is None
    d = tmp_path / "starmap"
    d.mkdir()
    (d / "starmap.json").write_text(json.dumps({"status": "captured", "page_version": SM.PAGE_VERSION,
                                                "captured_at_commit": {"commit": "c1"}}), encoding="utf-8")
    text = write_provenance(tmp_path, "c2").read_text(encoding="utf-8")
    assert "截取提交号：c1" in text and "截图沿用" in text and "须重截" not in text
    assert "截图沿用" not in write_provenance(tmp_path, "c1").read_text(encoding="utf-8")
    (d / "starmap.json").write_text(json.dumps({"status": "captured", "page_version": "星图 v1",
                                                "captured_at_commit": {"commit": "c1"}}), encoding="utf-8")
    assert "须重截" in write_provenance(tmp_path, "c1").read_text(encoding="utf-8")


def test_write_pack_keeps_starmap_without_flag_and_drops_images_on_failure(tmp_path, monkeypatch):
    """B：不带 GATE0_STARMAP 时保留已有截图；A：status ≠ captured 时删本轮截图并在 run_metadata、summary 标 failed"""
    from e2e_cohort.evidence import write_pack

    class R:
        def __init__(self):
            self.metadata = {"commit": "c9", "starmap_screenshot": {"record": "starmap/starmap.json"}}
    import e2e_cohort.evidence as E
    rendered = {}

    def fake_render(report):
        rendered["meta"] = copy.deepcopy(report.metadata)
        return {"run_metadata.json": json.dumps(report.metadata)}
    monkeypatch.setattr(E, "render", fake_render)
    d = tmp_path / "starmap"
    d.mkdir()
    (d / "S01-student.jpg").write_text("x")
    (d / "starmap.json").write_text(json.dumps({"status": "captured", "page_version": SM.PAGE_VERSION,
                                                "captured_at_commit": {"commit": "c1"}}), encoding="utf-8")
    write_pack(R(), tmp_path)
    assert (d / "S01-student.jpg").exists() and "截图沿用" in (d / "provenance.md").read_text(encoding="utf-8")
    write_pack(R(), tmp_path, starmap_record={"status": "failed", "reason": "合成失败", "captured_at_commit": {"commit": "c9"}})
    assert not (d / "S01-student.jpg").exists()
    assert rendered["meta"]["starmap_screenshot"]["status"] == "failed"
    assert json.loads((d / "starmap.json").read_text(encoding="utf-8"))["images_removed"] == ["S01-student.jpg"]


def test_capture_never_reports_success_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(SM, "missing_prerequisites", lambda: None)

    class Rep:
        cohort = None
    r = SM.capture(object(), Rep(), NODES_CSV, tmp_path)
    assert r["status"] == "failed" and "EphemeralPG" in r["reason"]
