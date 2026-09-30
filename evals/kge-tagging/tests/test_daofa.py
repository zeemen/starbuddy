"""冲刺 4-K：kappa / Top-1 的学科参数化（道法 zhixing）。夹具 fixtures/daofa-*.{csv,json} 全部合成（节点 MORAL.LAW.SYN_*，题目 SYN-D-*），不是金标。

手算（A = VP_A1，B = VP_A2，C = VP_B1，N = 无处可标）：
  两人主考点：T1 = A A A A B B B C C N；T2 = A A A B B B C C C N
    p_o = (3 + 2 + 2 + 1) / 10 = 8/10
    p_e = (4·3 + 3·3 + 2·3 + 1·1) / 100 = 28/100
    kappa = (8/10 − 28/100) / (1 − 28/100) = 52/72 = 13/18 ≈ 0.7222（≥ 0.7，达标）
  金标 = T1；预测 = A A B A B B B C C N（D03 错；D03 的 node_ids 含 A，退化候选 [B, A] 里 A 在前 3）
    Top-1 = 8/9 ≈ 88.9%（未达 90%）；Top-3 = 9/9；无处可标率 = 1/10 = 10%（未达 < 2%）
运行：cd evals/kge-tagging && python3 -m unittest discover -s tests
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))

import kappa  # noqa: E402
import top1  # noqa: E402
from common import InputError, read_annotations, read_candidates, read_tagging_report  # noqa: E402

FIX = BASE / "fixtures"
ANN, GOLD, TAG = str(FIX / "daofa-annotations.csv"), str(FIX / "daofa-gold.csv"), str(FIX / "daofa-tagging-report.json")
MATH_ANN, MATH_GOLD, MATH_TAG = str(FIX / "annotations.csv"), str(FIX / "gold.csv"), str(FIX / "tagging-report.json")


def run(mod, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


class DaofaKappa(unittest.TestCase):
    def test_kappa_matches_hand_calculation(self):
        data = read_annotations(ANN, "zhixing")
        r = kappa.compare(data, "T1", "T2")
        self.assertEqual(r.items, 10)
        self.assertEqual(r.observed, Fraction(8, 10))
        self.assertEqual(r.kappa, Fraction(13, 18))
        self.assertTrue(r.passed)
        self.assertEqual([x.item_id for x, _ in r.disagreements], ["SYN-D-04", "SYN-D-07"])

    def test_cli_json_carries_subject_and_is_deterministic(self):
        code, out, _ = run(kappa, ["--annotations", ANN, "--subject", "zhixing", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["subject"], "zhixing")
        self.assertEqual(data["kappa"], round(13 / 18, 6))
        self.assertEqual(run(kappa, ["--annotations", ANN, "--subject", "zhixing", "--json"])[1], out)
        self.assertEqual(run(kappa, ["--annotations", ANN, "--subject", "zhixing", "--enforce", "--json"])[0], 0)

    def test_subject_prefix_is_enforced_both_ways(self):
        # 道法标注按数学核对、数学标注按道法核对，都是输入错误（退出 2）
        self.assertEqual(run(kappa, ["--annotations", ANN, "--subject", "aya"])[0], 2)
        self.assertEqual(run(kappa, ["--annotations", MATH_ANN, "--subject", "zhixing"])[0], 2)
        with self.assertRaises(InputError):
            read_tagging_report(MATH_TAG, "zhixing")
        with self.assertRaises(InputError):
            read_candidates(str(FIX / "candidates.csv"), "zhixing")
        with self.assertRaises(InputError):
            read_annotations(ANN, "history")

    def test_math_unchanged_without_or_with_aya(self):
        # 不给学科时与 KGE-02 行为一致；给 aya 时数学夹具结果完全相同
        base = run(kappa, ["--annotations", MATH_ANN, "--json"])
        with_aya = run(kappa, ["--annotations", MATH_ANN, "--subject", "aya", "--json"])
        self.assertEqual(base[0], 0)
        self.assertNotIn("subject", json.loads(base[1]))
        a, b = json.loads(base[1]), json.loads(with_aya[1])
        b.pop("subject")
        self.assertEqual(a, b)


class DaofaTop1(unittest.TestCase):
    def test_top1_matches_hand_calculation(self):
        code, out, _ = run(top1, ["--gold", GOLD, "--tagging", TAG, "--subject", "zhixing", "--annotations", ANN, "--json"])
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual((d["gold_items"], d["scored"], d["gold_unplaceable"]), (10, 9, 1))
        self.assertEqual(d["top1"], round(8 / 9, 6))
        self.assertEqual(d["top3_recall"], 1.0)
        self.assertEqual(d["unplaceable_rate"], 0.1)
        self.assertEqual(d["subject"], "zhixing")
        self.assertFalse(d["thresholds"]["top1"]["passed"])
        self.assertTrue(d["thresholds"]["kappa"]["passed"])
        self.assertEqual([m["item_id"] for m in d["mismatches"]], ["SYN-D-03"])
        self.assertEqual(run(top1, ["--gold", GOLD, "--tagging", TAG, "--subject", "zhixing", "--enforce"])[0], 1)

    def test_summary_only_prints_ids_only_and_prefix_checked(self):
        code, out, _ = run(top1, ["--gold", GOLD, "--tagging", TAG, "--subject", "zhixing", "--summary-only"])
        self.assertEqual(code, 0)
        self.assertNotIn("SYN-D-", out)
        self.assertNotIn("MORAL.", out.replace("节点前缀 MORAL.", ""))
        self.assertEqual(run(top1, ["--gold", GOLD, "--tagging", MATH_TAG, "--subject", "zhixing"])[0], 2)
        self.assertEqual(run(top1, ["--gold", MATH_GOLD, "--tagging", MATH_TAG, "--subject", "zhixing"])[0], 2)

    def test_math_top1_unchanged_with_aya(self):
        a = json.loads(run(top1, ["--gold", MATH_GOLD, "--tagging", MATH_TAG, "--json"])[1])
        b = json.loads(run(top1, ["--gold", MATH_GOLD, "--tagging", MATH_TAG, "--subject", "aya", "--json"])[1])
        b.pop("subject")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
