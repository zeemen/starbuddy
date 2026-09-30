"""grading.py 单测：合成夹具（fixtures/，标注“合成、非金标”）上与手算一致。运行：cd evals/daofa-grading && python3 -m unittest discover -s tests

手算（教师 10 个点、4 个（回应, 题目）；适配器缺 R03/p2，多出 R05/p1）：
  逐点：R01 三点全同（3）；R02 p1 0≠1、p2 同、p3 同分（归因不同不影响）（2）；R03 p1 同、p2 缺（1）；R04 p1 同、p2 2≠1（1）→ 7/10 = 70%
  逐题总分：R01 5=5 ✓；R02 3≠4；R03 缺点；R04 3≠2 → 1/4 = 25%
  失分归因（教师标了归因的 5 个点）：R01p2 定位=定位 ✓、R02p1 转化=转化 ✓、R02p3 规范→转化 ✗、R03p2 审题→缺 ✗、R04p1 规范=规范 ✓ → 3/5 = 60%
  混淆表：none→none 4；none→guifan 1；dingwei→dingwei 1；zhuanhua→zhuanhua 1；guifan→zhuanhua 1；guifan→guifan 1；shenti→missing 1（合计 10）
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))

import grading  # noqa: E402

FIX = BASE / "fixtures"
GOLD, ADAPTER = str(FIX / "teacher.csv"), str(FIX / "adapter.csv")
PACK = str(BASE.parent.parent / "adapters/zhixing/content/pack.json")


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = grading.main(argv)
    return code, out.getvalue(), err.getvalue()


def write(tmp: str, text: str) -> str:
    p = Path(tmp) / "x.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


class HandCalculated(unittest.TestCase):
    def setUp(self):
        pack = grading.load_pack(PACK)
        self.r = grading.evaluate(grading.read_marks(GOLD, pack), grading.read_marks(ADAPTER, pack))

    def test_rates(self):
        r = self.r
        self.assertEqual((r.points, r.point_agree, r.missing, r.extra), (10, 7, 1, 1))
        self.assertEqual(r.point_rate, Fraction(7, 10))
        self.assertEqual((r.items, r.item_agree), (4, 1))
        self.assertEqual(r.attribution_rate, Fraction(3, 5))
        self.assertFalse(r.passed)
        self.assertEqual(r.point_mismatches, [("SYN-R-02", "synthetic-m3-01", "p1"), ("SYN-R-03", "synthetic-m3-03", "p2"),
                                              ("SYN-R-04", "synthetic-m3-04", "p2")])
        self.assertEqual(r.item_mismatches, [("SYN-R-02", "synthetic-m3-01"), ("SYN-R-03", "synthetic-m3-03"), ("SYN-R-04", "synthetic-m3-04")])

    def test_confusion(self):
        self.assertEqual(self.r.confusion, {("none", "none"): 4, ("none", "guifan_cuo"): 1, ("dingwei_cuo", "dingwei_cuo"): 1,
                                            ("zhuanhua_cuo", "zhuanhua_cuo"): 1, ("guifan_cuo", "zhuanhua_cuo"): 1,
                                            ("guifan_cuo", "guifan_cuo"): 1, ("shenti_cuo", "missing"): 1})
        self.assertEqual(sum(self.r.confusion.values()), 10)
        table = grading.confusion_table(self.r)
        self.assertEqual(table[0], ["教师 \\ 适配器", "dingwei_cuo", "zhuanhua_cuo", "guifan_cuo", "none", "missing"])
        self.assertEqual(table[-1], ["none", "0", "0", "1", "4", "0"])

    def test_threshold_boundary_exactly_90_percent_passes(self):
        g = {("R", "i", f"p{k}"): grading.Mark(Fraction(1), "") for k in range(10)}
        a = dict(g)
        a[("R", "i", "p0")] = grading.Mark(Fraction(0), "guifan_cuo")
        self.assertTrue(grading.evaluate(g, a).passed, "9/10 = 90% 达标")
        a[("R", "i", "p1")] = grading.Mark(Fraction(0), "guifan_cuo")
        self.assertFalse(grading.evaluate(g, a).passed, "8/10 未达标")


class Cli(unittest.TestCase):
    def test_json_is_deterministic_and_labelled_sample(self):
        code, out, _ = run(["--gold", GOLD, "--adapter", ADAPTER, "--pack", PACK, "--json"])
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["label"], "流程样例")
        self.assertEqual((d["point_agreement"], d["item_total_agreement"], d["attribution_agreement"]), (0.7, 0.25, 0.6))
        self.assertEqual(run(["--gold", GOLD, "--adapter", ADAPTER, "--pack", PACK, "--json"])[1], out)
        self.assertEqual(run(["--gold", GOLD, "--adapter", ADAPTER, "--enforce"])[0], 1)

    def test_text_report_ids_only(self):
        code, out, _ = run(["--gold", GOLD, "--adapter", ADAPTER])
        self.assertEqual(code, 0)
        self.assertIn("【流程样例】", out)
        self.assertIn("逐点判定一致率 = 70.0%（7/10", out)
        self.assertIn("逐题总分一致率 = 25.0%（1/4）", out)
        # 报告只含 ID 与汇总：不含内容包里的材料、设问、关键词
        pack = json.loads(Path(PACK).read_text(encoding="utf-8"))
        for it in pack["items"]:
            self.assertNotIn(it["material_text"][:12], out)
            for p in it["points"]:
                self.assertNotIn(p["keyword_family"]["core"][0], out)
        _, summ, _ = run(["--gold", GOLD, "--adapter", ADAPTER, "--summary-only"])
        self.assertNotIn("SYN-R-", summ)

    def test_input_errors_exit_2(self):
        head = "response_id,item_id,point_id,score,attribution\n"
        cases = {
            "extra column (answer text)": "response_id,item_id,point_id,score,attribution,answer_text\nR,synthetic-m3-01,p1,2,,x\n",
            "bad attribution": head + "R,synthetic-m3-01,p1,1,other\n",
            "bad score": head + "R,synthetic-m3-01,p1,0.3,\n",
            "duplicate": head + "R,synthetic-m3-01,p1,2,\nR,synthetic-m3-01,p1,2,\n",
            "unknown point with pack": head + "R,synthetic-m3-01,p9,2,\n",
            "score above max with pack": head + "R,synthetic-m3-01,p1,3,\n",
            "full score with attribution": head + "R,synthetic-m3-01,p1,2,guifan_cuo\n",
            "bad response id": head + "学生甲,synthetic-m3-01,p1,2,\n",
            "empty": head,
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in cases.items():
                code, _, err = run(["--gold", write(tmp, text), "--adapter", ADAPTER, "--pack", PACK])
                self.assertEqual(code, 2, name)
                self.assertTrue(err.startswith("grading: "), name)

    def test_gold_dir_label(self):
        self.assertFalse(grading.is_real_gold(GOLD))
        self.assertTrue(grading.is_real_gold(grading.GOLD_DIR / "x.csv"))


if __name__ == "__main__":
    unittest.main()
