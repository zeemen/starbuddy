"""讲评课验收清单检查器的单测（specs/lesson-acceptance-checklist 第二节第 5 条）：每条规则的正例与反例；仓库里的清单与合成示例合格。
全部合成数据（规则 8）。"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import check as C  # noqa: E402

CL = C._load(C.CHECKLIST)
SAMPLE = C._load(C.SAMPLE)
DIGEST = C.checklist_digest()
SIGNED = {**CL, "status": "signed", "signed_by": "（合成）教研负责人", "signed_at": "2026-11-26"}


def items_of(cl: dict) -> list[dict]:
    rep = C.Report()
    out = C.check_checklist(cl, rep)
    assert rep.ok, rep.problems
    return out


ITEMS = items_of(CL)


def run(rec: dict, cl: dict = CL) -> tuple[str | None, list[str]]:
    rep = C.Report()
    v = C.check_record(rec, cl, ITEMS, DIGEST, rep)
    return v, rep.problems


def all_pass() -> dict:
    rec = copy.deepcopy(SAMPLE)
    for it in ITEMS:
        e = {"result": "（合成示例）与期望一致", "outcome": "pass"}
        if "evidence_kind_expect" in it:
            e["evidence_kind_actual"] = it["evidence_kind_expect"]
        rec["items"][it["id"]] = e
    return rec


def cl_problems(mutate) -> list[str]:
    cl = copy.deepcopy(CL)
    mutate(cl)
    rep = C.Report()
    C.check_checklist(cl, rep)
    return rep.problems


class RepoFiles(unittest.TestCase):
    def test_checklist_and_sample_are_valid(self):
        self.assertEqual(C.main([]), 0)
        self.assertEqual(run(SAMPLE), ("trial", []))

    def test_checklist_is_draft_and_covers_six_sections(self):
        self.assertEqual(CL["status"], "draft")
        self.assertFalse(C.is_signed(CL))
        self.assertEqual(sorted({it["section"] for it in ITEMS}), list("ABCDEF"))
        must = {it["id"] for it in ITEMS if it["must"]}
        # 待人确认 #3 默认：A 节、C 节全部，D 节的“预设错例”与“没有关于学生的判断”
        self.assertEqual(must, {f"A{i}" for i in range(1, 8)} | {f"C{i}" for i in range(1, 5)} | {"D1", "D2"})
        for it in ITEMS:
            if it["section"] == "A" and it["id"] != "A7":
                self.assertIn(it["evidence_kind_expect"], C.EVIDENCE_KINDS)


class ChecklistRules(unittest.TestCase):
    def test_duplicate_id(self):
        self.assertTrue(any("id 重复" in p for p in cl_problems(lambda c: c["items"].append(copy.deepcopy(c["items"][0])))))

    def test_missing_field_and_empty_expect(self):
        self.assertTrue(cl_problems(lambda c: c["items"][0].pop("expect")))
        self.assertTrue(cl_problems(lambda c: c["items"][0].update(expect="  ")))

    def test_bad_evidence_kind(self):
        self.assertTrue(any("evidence_kind_expect" in p for p in cl_problems(lambda c: c["items"][0].update(evidence_kind_expect="assisted"))))

    def test_refs_must_exist(self):
        self.assertTrue(any("refs" in p for p in cl_problems(lambda c: c["items"][0].update(refs=["no/such/file.md"]))))
        self.assertTrue(any("refs" in p for p in cl_problems(lambda c: c["items"][0].update(refs=["../outside.md"]))))
        self.assertTrue(any("refs" in p for p in cl_problems(lambda c: c["items"][0].update(refs=[]))))

    def test_section_must_match_id(self):
        self.assertTrue(cl_problems(lambda c: c["items"][0].update(section="B")))
        self.assertTrue(cl_problems(lambda c: c["items"][0].update(id="a1")))

    def test_signature_consistency(self):
        self.assertTrue(cl_problems(lambda c: c.update(status="signed")))                       # 签字栏空却标 signed
        self.assertTrue(cl_problems(lambda c: c.update(signed_by="（合成）某人")))              # 有人无日期
        self.assertFalse(cl_problems(lambda c: c.update(status="signed", signed_by="（合成）某人", signed_at="2026-11-26")))

    def test_pii_in_checklist(self):
        self.assertTrue(any("手机号" in p for p in cl_problems(lambda c: c["items"][0].update(setup="联系 13812345678 的同学"))))


class RecordRules(unittest.TestCase):
    def test_every_item_exactly_once(self):
        rec = copy.deepcopy(SAMPLE)
        rec["items"].pop("A1")
        rec["items"]["Z9"] = {"result": "（合成示例）多出来的", "outcome": "pass"}
        v, probs = run(rec)
        self.assertIsNone(v)
        self.assertTrue(any("A1" in p and "缺这一条" in p for p in probs))
        self.assertTrue(any("Z9" in p for p in probs))

    def test_fail_and_blocked_need_repro(self):
        for outcome in ("fail", "blocked"):
            rec = all_pass()
            rec["items"]["B1"] = {"result": "（合成示例）没有还给学生", "outcome": outcome}
            v, probs = run(rec)
            self.assertIsNone(v)
            self.assertTrue(any("复现" in p for p in probs))

    def test_n_a_needs_reason(self):
        rec = all_pass()
        rec["items"]["F3"] = {"result": "（合成示例）本次不涉及", "outcome": "n_a"}
        self.assertIsNone(run(rec)[0])

    def test_evidence_kind_mismatch_cannot_pass(self):
        rec = all_pass()
        rec["items"]["A3"] = {"result": "（合成示例）重做被记成独立", "outcome": "pass", "evidence_kind_actual": "independent"}
        v, probs = run(rec)
        self.assertIsNone(v)
        self.assertTrue(any("不能记 pass" in p for p in probs))
        rec["items"]["A3"] = {"result": "（合成示例）重做被记成独立", "outcome": "fail", "evidence_kind_actual": "independent",
                              "repro": "（合成示例）看完示范 → 重做 → 证据情境为 independent"}
        self.assertEqual(run(rec, SIGNED)[0], "failed")

    def test_evidence_kind_required_where_expected(self):
        for outcome, extra in [("pass", {}), ("fail", {"repro": "（合成示例）作答后查看证据记录"})]:
            rec = all_pass()
            rec["items"]["A1"] = {"result": "（合成示例）没看证据记录", "outcome": outcome, **extra}
            v, probs = run(rec)
            self.assertIsNone(v, outcome)
            self.assertTrue(any("这一条要记" in p for p in probs), probs)
        rec = all_pass()
        rec["items"]["B1"]["evidence_kind_actual"] = "assisted"     # 没有期望的条目，写了也要是四种之一
        self.assertIsNone(run(rec)[0])

    def test_header_rules(self):
        for key, bad in [("synthetic_inputs", False), ("environment", "production"), ("date", "26/11/2026"), ("tester", " "),
                         ("checklist_sha256", "0" * 64), ("checklist_version", "9.9.9"), ("schema_version", "x")]:
            rec = all_pass()
            rec[key] = bad
            self.assertIsNone(run(rec)[0], key)
        rec = all_pass()
        rec["tested_versions"].pop("persona_prompt")
        self.assertIsNone(run(rec)[0])
        rec = all_pass()
        rec["score"] = 95
        self.assertIsNone(run(rec)[0])

    def test_attachments_only_relative_refs(self):
        for bad in ["https://example.invalid/a.png", "/abs/path.png", "data:image/png;base64,AAAA", "../up.png", "a b.png"]:
            rec = all_pass()
            rec["items"]["A1"]["attachments"] = [bad]
            self.assertIsNone(run(rec)[0], bad)
        rec = all_pass()
        rec["items"]["A1"]["attachments"] = ["shots/syn-001.png", "syn-log-7"]
        self.assertEqual(run(rec)[0], "trial")

    def test_pii_in_record(self):
        for text, kind in [("（合成示例）回拨 13912345678", "手机号"), ("（合成示例）证件 32010219990101123X", "身份证号"),
                           ("（合成示例）发到 someone@example.com", "邮箱"), ("（合成示例）在金陵某某中学测的", "学校全名")]:
            rec = all_pass()
            rec["items"]["B2"]["result"] = text
            v, probs = run(rec)
            self.assertIsNone(v, text)
            self.assertTrue(any(kind in p for p in probs), probs)
            self.assertFalse(any(text in p for p in probs), "问题里不回显原文")
        rec = all_pass()
        rec["items"]["B2"]["result"] = "（合成示例）在合成实验中学的账号上测"
        self.assertEqual(run(rec)[0], "trial")
        for ok in ["（合成示例）这是初中学生常见的写法", "（合成示例）回到学校以后再做", "（合成示例）初中学段的题", "（合成示例）小学阶段学过"]:
            rec = all_pass()
            rec["items"]["B2"]["result"] = ok
            self.assertEqual(run(rec)[0], "trial", ok)


class Verdict(unittest.TestCase):
    def test_unsigned_checklist_is_only_trial(self):
        self.assertEqual(run(all_pass())[0], "trial")

    def test_signed_all_must_pass_passes_even_if_observation_fails(self):
        rec = all_pass()
        rec["items"]["B3"] = {"result": "（合成示例）重复了导入", "outcome": "fail", "repro": "（合成示例）新会话 → 说会了 → 重复导入"}
        self.assertEqual(run(rec, SIGNED)[0], "passed")

    def test_must_blocked_or_fail_or_n_a_fails(self):
        for outcome, extra in [("blocked", {"repro": "（合成示例）环境没起来，无法核对"}), ("fail", {"repro": "（合成示例）出现了答案"}),
                               ("n_a", {"n_a_reason": "（合成示例）这节课没有新题"})]:
            rec = all_pass()
            rec["items"]["C2"] = {"result": "（合成示例）按清单操作", "outcome": outcome, **extra}
            self.assertEqual(run(rec, SIGNED)[0], "failed", outcome)

    def test_signed_all_pass(self):
        self.assertEqual(run(all_pass(), SIGNED)[0], "passed")


if __name__ == "__main__":
    unittest.main()
