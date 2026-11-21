"""道法判分转写（transcribe.py，W13 T34）的单测。全部是合成的 grade 返回（只含本脚本读的字段），不是任何真实判分。"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))

import grading  # noqa: E402
import transcribe as tr  # noqa: E402


def grade(dims: list[tuple[str, float | None, list[str]]], attrs: list[tuple[str, list[str]]] = ()) -> dict:
    return {"contract_version": "1.0.0", "result": {
        "verdict": "partial",
        "dimensions": [{"dimension_id": p, "score": s, "max_score": 2, "evidence_ids": ev} for p, s, ev in dims],
        "error_attributions": [{"subject_category": c, "cross_subject_category": "x", "evidence_ids": ev} for c, ev in attrs],
        "scoring_points": [{"point_id": p, "evidence_ids": ev, "quote": "合成作答原文，不应写出"} for p, _, ev in dims]},
        "evidence": [{"evidence_id": "e1", "content": "合成作答原文，不应写出"}]}


def line(rid: str, iid: str, g: dict) -> str:
    return json.dumps({"response_id": rid, "item_id": iid, "grade": g}, ensure_ascii=False)


G1 = grade([("p1", 2, ["e1"]), ("p2", 1, ["e2", "e3"]), ("p3", 1.5, ["e4"]), ("p4", None, ["e5"])],
           [("dingwei_cuo", ["e3"]), ("guifan_cuo", ["e4"])])


class TranscribeTest(unittest.TestCase):
    def test_rows_scores_attributions_and_undecided(self):
        out = tr.transcribe([line("SYN-R-01", "synthetic-m3-01", G1)])
        rows = [ln for ln in out.splitlines() if not ln.startswith("#")]
        self.assertEqual(rows, ["response_id,item_id,point_id,score,attribution",
                                "SYN-R-01,synthetic-m3-01,p1,2,",
                                "SYN-R-01,synthetic-m3-01,p2,1,dingwei_cuo",
                                "SYN-R-01,synthetic-m3-01,p3,1.5,guifan_cuo"])  # p4 未判定：不写（评测按缺点计）
        self.assertNotIn("作答原文", out)

    def test_output_reads_back_with_grading(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "adapter.csv"
            p.write_text(tr.transcribe([line("SYN-R-01", "synthetic-m3-01", G1), "",
                                        line("SYN-R-02", "synthetic-m3-01", grade([("p1", 0, ["x1"])], [("shenti_cuo", ["x1"])]))]),
                         encoding="utf-8")
            marks = grading.read_marks(p)
        self.assertEqual(len(marks), 4)

    def test_refusals(self):
        bad = [
            ("不是合法的 JSON", ["{"]),
            ("只能有", [json.dumps({"response_id": "SYN-R-01", "item_id": "i", "grade": G1, "text": "x"})]),
            ("不透明编号", [line("学生 1", "i", G1)]),
            ("没有 result.dimensions", [line("SYN-R-01", "i", {"result": {}})]),
            ("0.5 的倍数", [line("SYN-R-01", "i", grade([("p1", 0.3, ["e1"])]))]),
            ("得分不是数字", [line("SYN-R-01", "i", grade([("p1", True, ["e1"])]))]),
            ("知行四类", [line("SYN-R-01", "i", grade([("p1", 1, ["e1"])], [("careless", ["e1"])]))]),
            ("2 条错因", [line("SYN-R-01", "i", grade([("p1", 1, ["e1", "e2"])], [("shenti_cuo", ["e1"]), ("guifan_cuo", ["e2"])]))]),
            ("重复", [line("SYN-R-01", "i", grade([("p1", 1, ["e1"]), ("p1", 1, ["e2"])]))]),
            ("重复", [line("SYN-R-01", "i", G1), line("SYN-R-01", "i", G1)]),
            ("dimension_id 不合法", [line("SYN-R-01", "i", grade([("P 1", 1, ["e1"])]))]),
        ]
        for msg, lines in bad:
            with self.subTest(msg=msg), self.assertRaisesRegex(tr.InputError, msg):
                tr.transcribe(lines)

    def test_cli_refuses_existing_output_and_bad_input(self):
        with tempfile.TemporaryDirectory() as t:
            src, out = Path(t) / "g.jsonl", Path(t) / "a.csv"
            src.write_text(line("SYN-R-01", "synthetic-m3-01", G1) + "\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(tr.main(["--in", str(src), "--out", str(out)]), 0)
                self.assertEqual(tr.main(["--in", str(src), "--out", str(out)]), 2)
                self.assertEqual(tr.main(["--in", str(src), "--out", str(out), "--replace"]), 0)
                src.write_text("{\n", encoding="utf-8")
                self.assertEqual(tr.main(["--in", str(src), "--out", str(out), "--replace"]), 2)
            self.assertIn("已存在", err.getvalue())
            self.assertIn("SYN-R-01,synthetic-m3-01,p2,1,dingwei_cuo", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
