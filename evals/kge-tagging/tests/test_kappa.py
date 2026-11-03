"""kappa.py 单测：与手算结果一致（全部合成数据）。运行：cd evals/kge-tagging && python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import random
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))

import kappa  # noqa: E402
from common import InputError, read_annotations  # noqa: E402

FIX = BASE / "fixtures"
N1, N2, N3, N4 = ("MATH.SYN.SYN_A.K_SYN_0001", "MATH.SYN.SYN_A.K_SYN_0002", "MATH.SYN.SYN_B.S_SYN_0003", "MATH.SYN.SYN_B.M_SYN_0004")


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = kappa.main(argv)
    return code, out.getvalue(), err.getvalue()


def write(tmp: str, name: str, text: str) -> str:
    p = Path(tmp) / name
    p.write_text(text, encoding="utf-8")
    return str(p)


class CohenKappa(unittest.TestCase):
    def test_perfect_agreement_is_one(self):
        # 三个类别、两人逐题相同：p_o = 1，p_e = (2·2 + 1·1 + 1·1)/16 = 6/16 < 1，kappa = (1 − 6/16)/(1 − 6/16) = 1
        pairs = [(N1, N1), (N1, N1), (N2, N2), (None, None)]
        self.assertEqual(kappa.cohen_kappa(pairs), 1)

    def test_single_category_degenerate_is_one(self):
        # 两人所有题都标同一个节点：p_e = 1，按约定记 1（sklearn 此时给 nan）
        self.assertEqual(kappa.cohen_kappa([(N1, N1)] * 5), 1)

    def test_empty_is_none(self):
        self.assertIsNone(kappa.cohen_kappa([]))

    def test_chance_level_is_zero(self):
        # A = [x, x, y, y]，B = [x, y, x, y]：p_o = 2/4 = 1/2；p_A(x) = p_A(y) = p_B(x) = p_B(y) = 1/2
        # p_e = 1/2·1/2 + 1/2·1/2 = 1/2；kappa = (1/2 − 1/2)/(1 − 1/2) = 0
        pairs = [(N1, N1), (N1, N2), (N2, N1), (N2, N2)]
        self.assertEqual(kappa.cohen_kappa(pairs), 0)

    def test_random_independent_labels_near_zero(self):
        # 两人各自独立随机标（固定种子）：期望 kappa ≈ 0；n = 4000、4 个类别时标准误约 0.01，取 |kappa| < 0.05
        rng = random.Random(20260929)
        cats = [N1, N2, N3, N4]
        pairs = [(rng.choice(cats), rng.choice(cats)) for _ in range(4000)]
        k = kappa.cohen_kappa(pairs)
        self.assertLess(abs(float(k)), 0.05)

    def test_complete_disagreement_negative(self):
        # A = [x, y]，B = [y, x]：p_o = 0，p_e = 1/2，kappa = (0 − 1/2)/(1/2) = −1
        self.assertEqual(kappa.cohen_kappa([(N1, N2), (N2, N1)]), -1)

    def test_two_by_two_hand_example(self):
        # 2×2 手算例（50 题，两类：N1 / N2）：
        #              B=N1   B=N2
        #   A=N1        20      5     → A 标 N1 共 25 题
        #   A=N2        10     15     → A 标 N2 共 25 题
        #   B 合计      30     20
        # p_o = (20 + 15)/50 = 0.7
        # p_e = (25/50)(30/50) + (25/50)(20/50) = 0.30 + 0.20 = 0.5
        # kappa = (0.7 − 0.5)/(1 − 0.5) = 0.4
        pairs = [(N1, N1)] * 20 + [(N1, N2)] * 5 + [(N2, N1)] * 10 + [(N2, N2)] * 15
        self.assertEqual(kappa.cohen_kappa(pairs), Fraction(2, 5))

    def test_unplaceable_is_its_own_category(self):
        # 一人标 N1、一人标“无处可标”算不一致。A = [N1, N1]，B = [N1, None]：p_o = 1/2；
        # p_A(N1) = 1，p_B(N1) = 1/2，“无处可标”只出现在 B（p_A = 0）→ p_e = 1·1/2 = 1/2；kappa = (1/2 − 1/2)/(1 − 1/2) = 0
        self.assertEqual(kappa.cohen_kappa([(N1, N1), (N1, None)]), 0)


class Jaccard(unittest.TestCase):
    def test_values(self):
        self.assertEqual(kappa.jaccard(frozenset({N1, N2}), frozenset({N1})), Fraction(1, 2))
        self.assertEqual(kappa.jaccard(frozenset({N1}), frozenset({N2})), 0)
        self.assertEqual(kappa.jaccard(frozenset(), frozenset()), 1)
        self.assertEqual(kappa.jaccard(frozenset({N4, N1}), frozenset({N4, N1, N2})), Fraction(2, 3))


class FixtureFile(unittest.TestCase):
    def test_fixture_hand_computed(self):
        # fixtures/annotations.csv（10 题，T1 / T2）：
        #   主考点一致：01 02 04 05 06 08 09 10 → p_o = 8/10
        #   T1：N1×4（01 02 03 09）N2×2 N3×2 N4×1 无处可标×1；T2：N1×3 N2×3（03 04 05）N3×1 N4×2（07 08）无处可标×1
        #   p_e = (4·3 + 2·3 + 2·1 + 1·2 + 1·1)/100 = 23/100
        #   kappa = (80/100 − 23/100)/(77/100) = 57/77 ≈ 0.7403 → 达标（≥ 0.7）
        #   Jaccard（集合 = 主考点 ∪ node_ids）：01:1 02:1/2 03:0 04:1 05:1/2 06:1 07:0 08:2/3 09:1 10:1（都空）
        #   和 = 20/3，平均 = 2/3
        data = read_annotations(FIX / "annotations.csv")
        r = kappa.compare(data, *kappa.pick_annotators(data, None))
        self.assertEqual((r.annotator_a, r.annotator_b), ("T1", "T2"))
        self.assertEqual(r.items, 10)
        self.assertEqual(r.observed, Fraction(8, 10))
        self.assertEqual(r.kappa, Fraction(57, 77))
        self.assertEqual(r.mean_jaccard, Fraction(2, 3))
        self.assertTrue(r.passed)
        self.assertEqual([(x.item_id, x.main_node_id, y.main_node_id) for x, y in r.disagreements],
                         [("SYN-G-03", N1, N2), ("SYN-G-07", N3, N4)])
        self.assertEqual(kappa.disagreements_csv(r).splitlines(), [
            "item_id,annotator_a,main_a,nodes_a,annotator_b,main_b,nodes_b",
            f"SYN-G-03,T1,{N1},{N1},T2,{N2},{N2}",
            f"SYN-G-07,T1,{N3},{N3},T2,{N4},{N4}",
        ])

    def test_only_one_side_items_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write(tmp, "a.csv", "item_id,annotator,main_node_id,node_ids\n"
                                    f"S-1,A,{N1},\nS-1,B,{N1},\nS-2,A,{N1},\nS-3,B,{N2},\n")
            data = read_annotations(p)
            r = kappa.compare(data, "A", "B")
            self.assertEqual((r.items, r.only_a, r.only_b), (1, 1, 1))


class Cli(unittest.TestCase):
    def test_fixture_run_and_enforce(self):
        code, out, _ = run(["--annotations", str(FIX / "annotations.csv")])
        self.assertEqual(code, 0)
        self.assertIn("Cohen's kappa = 0.7403", out)
        self.assertIn("平均 Jaccard = 0.6667", out)
        self.assertIn("门槛 kappa ≥ 0.7：通过", out)
        self.assertIn("SYN-G-03", out)
        self.assertEqual(run(["--annotations", str(FIX / "annotations.csv"), "--enforce"])[0], 0)

    def test_enforce_fails_below_threshold(self):
        # 2×2 手算例 kappa = 0.4 < 0.7：不加 --enforce 退出码 0，加了为 1
        rows = [(N1, N1)] * 20 + [(N1, N2)] * 5 + [(N2, N1)] * 10 + [(N2, N2)] * 15
        text = "item_id,annotator,main_node_id,node_ids\n" + "".join(
            f"S-{i:03d},T1,{a},\nS-{i:03d},T2,{b},\n" for i, (a, b) in enumerate(rows))
        with tempfile.TemporaryDirectory() as tmp:
            p = write(tmp, "ann.csv", text)
            code, out, _ = run(["--annotations", p, "--json"])
            self.assertEqual(code, 0)
            self.assertIn('"kappa": 0.4', out)
            self.assertIn('"passed": false', out)
            self.assertEqual(run(["--annotations", p, "--enforce", "--json"])[0], 1)
            dis = str(Path(tmp) / "dis.csv")
            run(["--annotations", p, "--disagreements", dis])
            self.assertEqual(len(Path(dis).read_text(encoding="utf-8").splitlines()), 1 + 15)

    def test_input_errors_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_header = write(tmp, "h.csv", "item,who,main\nS-1,A,X\n")
            bad_node = write(tmp, "n.csv", "item_id,annotator,main_node_id,node_ids\nS-1,A,not-a-node,\n")
            dup = write(tmp, "d.csv", f"item_id,annotator,main_node_id,node_ids\nS-1,A,{N1},\nS-1,A,{N2},\n")
            three = write(tmp, "t.csv", f"item_id,annotator,main_node_id,node_ids\nS-1,A,{N1},\nS-1,B,{N1},\nS-1,C,{N1},\n")
            for p in (bad_header, bad_node, dup, three):
                self.assertEqual(run(["--annotations", p])[0], 2, p)
            self.assertEqual(run(["--annotations", three, "--annotators", "A,C"])[0], 0)
            self.assertEqual(run(["--annotations", three, "--annotators", "A,Z"])[0], 2)
            with self.assertRaises(InputError):
                read_annotations(bad_node)


if __name__ == "__main__":
    unittest.main()
