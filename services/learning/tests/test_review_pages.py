"""编译页面对账（specs/release-gate-sprint4 冲刺 3 验收“对账”）：纯函数与命令行，不连库、不连托管平台。合成数据（规则 8）。

验收四条：合成导出与审核记录一致时通过；删掉一条审核记录、改一个提交号、高风险只剩一名批准人，各报失败。
make page-reconcile-test 只跑本文件（也随 learning-test 运行）。
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from review_console import pages

FIX = Path(__file__).parent / "fixtures" / "pages-reconcile"
LEARNING = Path(__file__).resolve().parents[1]
H1, H2, H4 = "a1" * 20, "a2" * 20, "a4" * 20
T1, T2 = "5f0e0000-0000-4000-8000-0000000000e1", "5f0e0000-0000-4000-8000-0000000000e2"


def raw(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def run(export: dict | None = None, records: dict | None = None, staff: dict | None = None) -> dict:
    return pages.reconcile(pages.parse_export(export or raw("export.json")), pages.parse_records(records or raw("records.json")),
                           pages.parse_staff_map(staff or raw("staff-map.json")))


def merged(report: dict, page: str) -> dict:
    [e] = [e for e in report["merged"] if e["page_id"] == page]
    return e


# ---------------- 验收四条 ----------------
def test_consistent_export_and_records_pass():
    r = run()
    assert r["ok"] and r["held_pages"] == [] and r["orphans"] == []
    assert [(e["page_id"], e["status"], e["required"]) for e in r["merged"]] == [
        ("MATH.SYN.T.A", "ok", 2), ("MATH.SYN.T.B", "ok", 2), ("MATH.SYN.T.C", "ok", 2), ("MATH.SYN.T.D", "ok", 0)]
    # 待审合并请求只列出，不算失败；签了一人
    assert [(e["page_id"], e["approvals_review"], e["required"]) for e in r["pending"]] == [("MATH.SYN.T.E", 1, 2)]


def test_deleted_review_record_fails():
    rec = raw("records.json")
    rec["records"] = [x for x in rec["records"] if not (x["page_id"] == "MATH.SYN.T.A" and x["signer"] == T2)]
    r = run(records=rec)
    assert not r["ok"] and r["held_pages"] == ["MATH.SYN.T.A"]
    assert merged(r, "MATH.SYN.T.A")["problems"] == ["approver_mismatch", "insufficient_approvals"]


def test_deleted_all_records_of_a_page_fails():
    rec = raw("records.json")
    rec["records"] = [x for x in rec["records"] if x["page_id"] != "MATH.SYN.T.C"]
    r = run(records=rec)
    assert r["held_pages"] == ["MATH.SYN.T.C"]
    assert merged(r, "MATH.SYN.T.C")["problems"] == ["insufficient_approvals", "missing_review"]
    assert merged(r, "MATH.SYN.T.B")["status"] == "ok"  # 同一合并请求的另一页不受影响


def test_changed_commit_fails_both_sides():
    rec = raw("records.json")
    rec["records"][2]["page_commit"] = "c2" * 20  # MATH.SYN.T.B 的一条记录改了提交号
    r = run(records=rec)
    assert not r["ok"] and r["held_pages"] == ["MATH.SYN.T.B"]
    # 孤儿本身不扣页（最新头提交上还有 T2 的批准），页面因批准人不符被扣
    assert r["orphans"] == [{"page_id": "MATH.SYN.T.B", "page_commit": "c2" * 20, "records": 1, "problems": ["no_merge"], "blocking": False}]
    assert merged(r, "MATH.SYN.T.B")["problems"] == ["approver_mismatch", "insufficient_approvals"]


def test_changed_commit_in_export_fails():
    exp = raw("export.json")
    exp["merge_requests"][0]["head_commit"] = "d1" * 20
    r = run(export=exp)
    assert r["held_pages"] == ["MATH.SYN.T.A"]
    assert merged(r, "MATH.SYN.T.A")["problems"] == ["insufficient_approvals", "missing_review"]
    assert [(o["page_commit"], o["blocking"]) for o in r["orphans"]] == [(H1, True)]


def test_pushed_after_approval_then_resigned_passes():
    """批准后又推了一个提交才合并：旧头提交上的签字成了孤儿并扣页；对最新头提交重新签字后放行"""
    exp = raw("export.json")
    exp["merge_requests"][0]["head_commit"] = "d1" * 20
    r = run(export=exp)
    assert r["held_pages"] == ["MATH.SYN.T.A"] and all(o["blocking"] for o in r["orphans"])
    rec = raw("records.json")
    rec["records"] += [{"page_id": "MATH.SYN.T.A", "page_commit": "d1" * 20, "risk_level": "high", "decision": "approved", "signer": s}
                       for s in (T1, T2)]
    r = run(export=exp, records=rec)
    assert r["ok"] and r["held_pages"] == [] and merged(r, "MATH.SYN.T.A")["status"] == "ok"
    assert [(o["page_commit"], o["blocking"]) for o in r["orphans"]] == [(H1, False)]


def test_open_mr_pushed_after_approval_holds_until_resigned():
    """待审合并请求在签字后又推了提交：旧签字成孤儿，扣页直到对新头提交重签"""
    exp, rec = raw("export.json"), raw("records.json")
    exp["merge_requests"][3]["head_commit"] = "a5" * 20
    assert run(export=exp)["held_pages"] == ["MATH.SYN.T.E"]
    rec["records"].append({"page_id": "MATH.SYN.T.E", "page_commit": "a5" * 20, "risk_level": "high", "decision": "approved", "signer": T1})
    assert run(export=exp, records=rec)["ok"]


def test_old_orphan_outside_export_does_not_hold():
    """导出范围外（导出里没有改到这一页的合并请求）的旧孤儿只列出，不扣页面"""
    rec = raw("records.json")
    rec["records"].append({"page_id": "MATH.SYN.T.Z", "page_commit": "e9" * 20, "risk_level": "high", "decision": "approved", "signer": T1})
    r = run(records=rec)
    assert r["ok"] and r["orphans"] == [{"page_id": "MATH.SYN.T.Z", "page_commit": "e9" * 20, "records": 1, "problems": ["no_merge"],
                                         "blocking": False}]


def test_page_judged_by_latest_merge_only():
    """同一页面在导出里合并过两次：只按最近一次合并判定，更早的列在 superseded"""
    exp = raw("export.json")
    exp["merge_requests"].append({"mr_id": "90", "state": "merged", "risk": "high", "approvers": [], "pages": ["MATH.SYN.T.A"],
                                  "head_commit": "e1" * 20, "merge_commit": "f1" * 20, "merged_at": "2026-09-30T10:00:00+08:00"})
    r = run(export=exp)
    assert r["ok"] and [e["mr_id"] for e in r["merged"] if e["page_id"] == "MATH.SYN.T.A"] == ["101"]
    assert r["superseded"] == [{"page_id": "MATH.SYN.T.A", "mr_id": "90", "head_commit": "e1" * 20, "merge_commit": "f1" * 20}]
    # 反过来：更晚的那次合并没有签字，就扣页
    exp["merge_requests"][-1]["merged_at"] = "2026-10-03T10:00:00+08:00"
    r = run(export=exp)
    assert r["held_pages"] == ["MATH.SYN.T.A"] and merged(r, "MATH.SYN.T.A")["mr_id"] == "90"


def test_report_maps_head_to_merge_commit():
    e = merged(run(), "MATH.SYN.T.A")
    assert e["head_commit"] == H1 and e["merge_commit"] == "b1" * 20


def test_required_equals_compiler_reviewers():
    """对账的所需人数与 KGE-03 合并窗口 REVIEWERS 相等（两处各写一份，这里核对）"""
    import ast
    src = (LEARNING.parent / "knowledge" / "knowledge" / "compiler" / "window.py").read_text(encoding="utf-8")
    [node] = [n for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REVIEWERS"]
    assert ast.literal_eval(node.value) == pages.REQUIRED


def test_high_risk_with_single_approver_fails():
    exp, rec = raw("export.json"), raw("records.json")
    exp["merge_requests"][0]["approvers"] = ["syn-teacher-1"]
    rec["records"] = [x for x in rec["records"] if not (x["page_id"] == "MATH.SYN.T.A" and x["signer"] == T2)]
    r = run(export=exp, records=rec)
    assert r["held_pages"] == ["MATH.SYN.T.A"]
    e = merged(r, "MATH.SYN.T.A")
    assert e["problems"] == ["insufficient_approvals"] and e["approvals_platform"] == e["approvals_review"] == 1


# ---------------- 其他规则 ----------------
def test_unmapped_approver_fails():
    staff = raw("staff-map.json")
    del staff["users"]["syn-teacher-2"]
    r = run(staff=staff)
    assert r["held_pages"] == ["MATH.SYN.T.A", "MATH.SYN.T.B", "MATH.SYN.T.C"]
    assert "approver_unmapped" in merged(r, "MATH.SYN.T.A")["problems"]


def test_rejection_in_review_fails():
    rec = raw("records.json")
    rec["records"][0]["decision"] = "rejected"
    r = run(records=rec)
    assert "rejected_in_review" in merged(r, "MATH.SYN.T.A")["problems"]


def test_ledger_risk_raises_requirement():
    """合并记录标低风险，但审核记录按高风险签：所需人数按较高的一方算"""
    exp, rec = raw("export.json"), raw("records.json")
    exp["merge_requests"][2]["approvers"] = ["syn-teacher-1"]
    rec["records"].append({"page_id": "MATH.SYN.T.D", "page_commit": "a3" * 20, "risk_level": "high", "decision": "approved", "signer": T1})
    e = merged(run(export=exp, records=rec), "MATH.SYN.T.D")
    assert e["risk"] == "high" and e["required"] == 2 and e["problems"] == ["insufficient_approvals"]


def test_low_risk_platform_approval_without_record_passes():
    """低风险无事前签字：托管平台上有批准人而审核台没有记录不算失败；审核台有记录时才比对"""
    exp, rec = raw("export.json"), raw("records.json")
    exp["merge_requests"][2]["approvers"] = ["syn-teacher-1"]
    assert merged(run(export=exp), "MATH.SYN.T.D")["status"] == "ok"
    rec["records"].append({"page_id": "MATH.SYN.T.D", "page_commit": "a3" * 20, "risk_level": "low", "decision": "approved", "signer": T2})
    assert merged(run(export=exp, records=rec), "MATH.SYN.T.D")["problems"] == ["approver_mismatch"]


def test_different_approver_with_same_count_fails():
    exp, staff = raw("export.json"), raw("staff-map.json")
    staff["users"]["syn-teacher-3"] = "5f0e0000-0000-4000-8000-0000000000e3"
    exp["merge_requests"][0]["approvers"] = ["syn-teacher-1", "syn-teacher-3"]
    assert merged(run(export=exp, staff=staff), "MATH.SYN.T.A")["problems"] == ["approver_mismatch"]


def test_orphan_of_page_with_signed_latest_head_does_not_hold():
    rec = raw("records.json")
    rec["records"].append({"page_id": "MATH.SYN.T.E", "page_commit": "a5" * 20, "risk_level": "high", "decision": "approved", "signer": T2})
    r = run(records=rec)
    assert r["ok"] and r["orphans"][0]["page_commit"] == "a5" * 20 and not r["orphans"][0]["blocking"]


def test_report_is_deterministic_and_has_no_names():
    a, b = pages.dumps(run()), pages.dumps(run())
    assert a == b
    assert "syn-teacher" not in a and T1 not in a and T2 not in a  # 报告不含用户名与账号


def test_report_order_does_not_depend_on_input_order():
    exp, rec = raw("export.json"), raw("records.json")
    exp["merge_requests"].reverse()
    rec["records"].reverse()
    assert pages.dumps(run(export=exp, records=rec)) == pages.dumps(run())


# ---------------- 输入校验 ----------------
@pytest.mark.parametrize("mutate", [
    lambda e: e.update(format="other/1"),
    lambda e: e.update(extra=1),
    lambda e: e["merge_requests"][0].update(head_commit="A1" * 20),
    lambda e: e["merge_requests"][0].update(head_commit="a1" * 19),
    lambda e: e["merge_requests"][0].update(note="free text"),
    lambda e: e["merge_requests"][0].update(pages=["math.syn.t.a"]),
    lambda e: e["merge_requests"][0].update(pages=[]),
    lambda e: e["merge_requests"][0].update(approvers=["syn-teacher-1", "syn-teacher-1"]),
    lambda e: e["merge_requests"][0].update(risk="urgent"),
    lambda e: e["merge_requests"][0].update(mr_id="12a"),
    lambda e: e["merge_requests"][0].update(state="closed"),
    lambda e: e["merge_requests"][0].pop("merge_commit"),
    lambda e: e["merge_requests"][3].update(merge_commit="b4" * 20),
    lambda e: e["merge_requests"][1].update(mr_id="101"),
    lambda e: e["merge_requests"][1].update(pages=["MATH.SYN.T.A"], head_commit="a1" * 20),
])
def test_export_rejected(mutate):
    exp = copy.deepcopy(raw("export.json"))
    mutate(exp)
    with pytest.raises(pages.ReconcileInputError):
        pages.parse_export(exp)


@pytest.mark.parametrize("mutate", [
    lambda s: s["users"].update({"syn-teacher-3": "5f0e0000-0000-4000-8000-0000000000e1"}),  # 两个用户名对应同一 staff
    lambda s: s["users"].update({"syn-teacher-3": "not-a-uuid"}),
    lambda s: s["users"].update({"bad name": "5f0e0000-0000-4000-8000-0000000000e3"}),
    lambda s: s["users"].update({"syn-teacher-3": "5F0E0000-0000-4000-8000-0000000000E3"}),
    lambda s: s.update(students=[]),
])
def test_staff_map_rejected(mutate):
    s = copy.deepcopy(raw("staff-map.json"))
    mutate(s)
    with pytest.raises(pages.ReconcileInputError):
        pages.parse_staff_map(s)


@pytest.mark.parametrize("mutate", [
    lambda r: r["records"][0].update(page_commit="A1" * 20),
    lambda r: r["records"][0].update(decision="pending"),
    lambda r: r["records"][0].update(signer="nobody"),
    lambda r: r["records"][0].pop("signer"),
    lambda r: r["records"][0].update(signer=T1.upper()),                      # 离线文件只收小写规范 UUID
    lambda r: r["records"][0].update(signer="{" + T1 + "}"),
    lambda r: r["records"][0].update(signer=T1.replace("-", "")),
    lambda r: r["records"][0].update(review_id="5f0e0000-0000-4000-8000-0000000000f1"),  # 只允许 5 个字段
    lambda r: r["records"][0].update(login="syn-teacher-1"),
])
def test_records_rejected(mutate):
    r = copy.deepcopy(raw("records.json"))
    mutate(r)
    with pytest.raises(pages.ReconcileInputError):
        pages.parse_records(r)


def test_db_rows_path_stays_lenient():
    """库行（kge.page_review_record）多出的列忽略、signer 可以是 uuid 对象"""
    import uuid as _uuid
    rows = [{**x, "review_id": "x", "signer_no": 1, "digest": "0" * 64, "signer": _uuid.UUID(x["signer"])} for x in raw("records.json")["records"]]
    assert pages.records_from_rows(rows) == pages.parse_records(raw("records.json"))


def test_json_loader_rejects_oversize(tmp_path):
    p = tmp_path / "big.json"
    p.write_bytes(b'{"a": "' + b"x" * (pages.MAX_FILE_BYTES) + b'"}')
    with pytest.raises(pages.ReconcileInputError, match="MB"):
        pages.load_json(str(p))


def test_json_loader_rejects_duplicates_and_nan(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(pages.ReconcileInputError):
        pages.load_json(str(p))
    p.write_text('{"a": NaN}', encoding="utf-8")
    with pytest.raises(pages.ReconcileInputError):
        pages.load_json(str(p))


def test_mr_url_template():
    t = pages.check_mr_url_template("https://git.example.cn/kge/pages/-/merge_requests/{mr_id}")
    assert pages.mr_url(t, "101") == "https://git.example.cn/kge/pages/-/merge_requests/101"
    for bad in ("http://git.example.cn/{mr_id}", "https://git.example.cn/{mr_id}?x=1", "https://git.example.cn/mr",
                "https://git.example.cn/{mr_id}/{mr_id}", "javascript:alert(1)//{mr_id}", "https://a.cn/{mr_id}#x",
                "https://mr{mr_id}.example.cn/x",        # 拼进主机名
                "https://git.example.{mr_id}/x",         # 拼进顶级域
                "https://git.example.cn{mr_id}",         # 紧跟在顶级域后面
                "https://git.example.cn:{mr_id}/x",      # 拼进端口
                "https://git.example.cn/mr-{mr_id}"):    # 前面不是“/”
        with pytest.raises(ValueError):
            pages.check_mr_url_template(bad)
    with pytest.raises(pages.ReconcileInputError):
        pages.mr_url(t, "1/../2")


# ---------------- 命令行（Makefile page-reconcile-test 的验收入口） ----------------
def cli(tmp_path, records: dict | None = None, export: dict | None = None) -> tuple[int, dict | None, str]:
    rp = tmp_path / "records.json"
    rp.write_text(json.dumps(records or raw("records.json")), encoding="utf-8")
    ep = tmp_path / "export.json"
    ep.write_text(json.dumps(export or raw("export.json")), encoding="utf-8")
    out = tmp_path / "report.json"
    p = subprocess.run([sys.executable, "-m", "review_console", "reconcile-pages", "--export", str(ep),
                        "--staff-map", str(FIX / "staff-map.json"), "--records", str(rp), "--out", str(out)],
                       cwd=LEARNING, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    return p.returncode, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None), p.stderr


def test_cli_pass_and_fail_exit_codes(tmp_path):
    (tmp_path / "ok").mkdir()
    rc, report, _ = cli(tmp_path / "ok")
    assert rc == 0 and report["ok"] and report["format"] == "kge-pages-reconcile/1"
    rec = raw("records.json")
    rec["records"].pop(0)
    (tmp_path / "bad").mkdir()
    rc, report, err = cli(tmp_path / "bad", records=rec)
    assert rc == 1 and report["held_pages"] == ["MATH.SYN.T.A"] and "合并窗口跳过 1 页" in err


def test_cli_input_error_exit_2_without_echo(tmp_path):
    exp = raw("export.json")
    exp["merge_requests"][0]["note"] = "SECRET-LOOKING-TEXT"
    rc, report, err = cli(tmp_path, export=exp)
    assert rc == 2 and report is None and "SECRET-LOOKING-TEXT" not in err


def test_cli_refuses_existing_output(tmp_path):
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    rc, _, err = cli(tmp_path)
    assert rc == 2 and "不能已存在" in err
