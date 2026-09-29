"""top1.py 与 run_holdout.sh 单测（全部合成数据）。运行：cd evals/kge-tagging && python3 -m unittest discover -s tests

夹具手算（fixtures/gold.csv 定稿 final，10 题；SYN-G-10 金标无处可标 → 计分 9 题）：
  预测 fixtures/tagging-report.json：
    01 N1 ✓  02 N1 ✓  03 N1 ✗（金标 N2）  04 N2 ✓  05 N2 ✓  06 N3 ✓  07 无主考点 ✗  08 N4 ✓  09 缺预测 ✗  10 N1（不计分）
    另有 SYN-G-99 不在金标里（不计分）
  Top-1 = 6/9 ≈ 66.7%
  Top-3（报告里的 candidates；没有 candidates 的题退化为 [主考点] + node_ids）：
    01 02 04 05 06 08 靠主考点命中 → 6；03 候选 [N1 0.8, N2 0.7] → N2 在前 3 ✓；
    07 候选 [N4 .4, N1 .3, N2 .2, N3 .1] → N3 排第 4 ✗；09 缺预测 ✗ → 7/9 ≈ 77.8%
  无处可标率 = （07 无主考点 + 09 缺预测）/ 10 = 20%
  --candidates fixtures/candidates.csv（kge.tag_candidate 格式）：
    01 [N1] ✓；03 [N2 .9, N1 .6] ✓；07 [N3 max(.5,.2)]（N4 是 secondary 不计）✓；其余没有候选 ✗ → 3/9
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))

import top1  # noqa: E402
from common import InputError, rank, read_annotations, read_candidates, read_tagging_report  # noqa: E402

FIX = BASE / "fixtures"
N1, N2, N3, N4 = ("MATH.SYN.SYN_A.K_SYN_0001", "MATH.SYN.SYN_A.K_SYN_0002", "MATH.SYN.SYN_B.S_SYN_0003", "MATH.SYN.SYN_B.M_SYN_0004")
GOLD, TAGGING, CANDS, ANN = (str(FIX / f) for f in ("gold.csv", "tagging-report.json", "candidates.csv", "annotations.csv"))
HEADER = "item_id,annotator,main_node_id,node_ids\n"


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = top1.main(argv)
    return code, out.getvalue(), err.getvalue()


def write(d: str, name: str, text: str) -> str:
    p = Path(d) / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def perfect(tmp: str, n: int = 10, wrong: int = 0, unplaceable: int = 0) -> tuple[str, str]:
    """合成 n 题金标（主考点轮流 N1–N4）与预测：前 wrong 题主考点错，接着 unplaceable 题无主考点。"""
    nodes = [N1, N2, N3, N4]
    gold = HEADER + "".join(f"S-{i:03d},final,{nodes[i % 4]},\n" for i in range(n))
    items = []
    for i in range(n):
        main = nodes[i % 4]
        if i < wrong:
            main = nodes[(i + 1) % 4]
        elif i < wrong + unplaceable:
            main = None
        items.append({"item_id": f"S-{i:03d}", "main_node_id": main, "node_ids": [], "confidence": 0.9,
                      "layer": "rule", "grade": "A", "cognitive_level": None})
    return write(tmp, "gold.csv", gold), write(tmp, "t.json", json.dumps({"map_version": "0.0.1", "items": items}))


class Evaluate(unittest.TestCase):
    def fixture(self, cands=None):
        gold = top1.pick_gold(read_annotations(GOLD), None)
        _, preds = read_tagging_report(TAGGING)
        return top1.evaluate(gold, preds, cands)

    def test_fixture_hand_computed(self):
        r = self.fixture()
        self.assertEqual((r.gold_items, r.gold_unplaceable, r.scored), (10, 1, 9))
        self.assertEqual(r.top1, Fraction(6, 9))
        self.assertEqual(r.top3, Fraction(7, 9))
        self.assertEqual(r.unplaceable_rate, Fraction(2, 10))
        self.assertEqual((r.missing, r.extra), (1, 1))
        self.assertEqual(r.candidate_source, "打标报告里的 candidates")
        self.assertEqual([x.item_id for x in r.items if x.gold and not x.top1], ["SYN-G-03", "SYN-G-07", "SYN-G-09"])

    def test_candidates_file_tag_candidate_format(self):
        r = self.fixture(read_candidates(CANDS))
        self.assertEqual(r.top3, Fraction(3, 9))
        self.assertEqual(r.top1, Fraction(6, 9), "候选来源不影响 Top-1")
        self.assertTrue(r.candidate_source.startswith("候选文件"))

    def test_candidates_json_same_as_csv(self):
        rows = [{"item_id": "SYN-G-03", "node_id": N2, "role": "main", "layer": "rule", "confidence": 0.9},
                {"item_id": "SYN-G-07", "node_id": N4, "role": "secondary", "layer": "rule", "confidence": 0.99}]
        with tempfile.TemporaryDirectory() as tmp:
            c = read_candidates(write(tmp, "c.json", json.dumps(rows)))
        self.assertEqual(c, {"SYN-G-03": ((N2, Fraction(9, 10)),)})

    def test_rank_dedup_and_ties(self):
        # 同一节点取最高置信度；置信度相同时按节点 ID 码点升序
        cands = ((N3, Fraction(1, 2)), (N1, Fraction(1, 2)), (N3, Fraction(9, 10)), (N2, Fraction(1, 10)))
        self.assertEqual(rank(cands), [N3, N1, N2])

    def test_fallback_without_candidate_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = write(tmp, "g.csv", HEADER + f"S-1,final,{N2},\n")
            t = write(tmp, "t.json", json.dumps({"map_version": "x", "items": [
                {"item_id": "S-1", "main_node_id": N1, "node_ids": [N3, N2]}]}))
            gold = top1.pick_gold(read_annotations(g), None)
            r = top1.evaluate(gold, read_tagging_report(t)[1], None)
        self.assertTrue(r.candidate_source.startswith("退化"))
        self.assertEqual((r.top1, r.top3), (0, 1), "退化候选 [N1, N3, N2]：N2 在前 3")


    def test_formal_report_format(self):
        # 打标器正式格式（kge-tagging-report/1）：map_version 是对象，题目带 status 与 candidates（role / layer / confidence）
        report = {"format": "kge-tagging-report/1", "map_version": {"id": "00000000-0000-4000-8000-00000000c0de", "version": "9.9.9-syn",
                                                                   "imported_chapters": ["syn"]},
                  "counts": {}, "families": [], "items": [
                      {"item_id": "S-1", "status": "candidate", "in_scope": True, "main_node_id": N1, "node_ids": [N1], "confidence": 0.6,
                       "candidates": [{"node_id": N1, "role": "main", "layer": "vector", "confidence": 0.6},
                                      {"node_id": N2, "role": "main", "layer": "rule", "confidence": 0.6},
                                      {"node_id": N3, "role": "secondary", "layer": "rule", "confidence": 0.9}]},
                      {"item_id": "S-2", "status": "out_of_scope", "in_scope": False, "main_node_id": None, "node_ids": [], "candidates": []}]}
        with tempfile.TemporaryDirectory() as tmp:
            version, preds = read_tagging_report(write(tmp, "r.json", json.dumps(report)))
            self.assertEqual(version, "9.9.9-syn")
            self.assertEqual(rank(preds["S-1"].candidates), [N1, N2], "secondary 不进主考点候选；同置信度按码点")
            self.assertIsNone(preds["S-2"].main_node_id)
            with self.assertRaises(InputError):
                read_tagging_report(write(tmp, "r2.json", json.dumps({**report, "format": "kge-tagging-report/2"})))


class Thresholds(unittest.TestCase):
    def test_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Top-1 恰好 90%（9/10）达标
            r = top1.evaluate(top1.pick_gold(read_annotations(perfect(tmp, 10, wrong=1)[0]), None),
                              read_tagging_report(perfect(tmp, 10, wrong=1)[1])[1], None)
            th = top1.thresholds(r, None, False)
            self.assertEqual(r.top1, Fraction(9, 10))
            self.assertTrue(th["top1"]["passed"])
            self.assertTrue(th["unplaceable_rate"]["passed"])
            self.assertNotIn("kappa", th)
            # 无处可标率恰好 2%（2/100）不达标（严格小于）；1/100 达标
            g, t = perfect(tmp, 100, unplaceable=2)
            r = top1.evaluate(top1.pick_gold(read_annotations(g), None), read_tagging_report(t)[1], None)
            self.assertEqual(r.unplaceable_rate, Fraction(2, 100))
            self.assertFalse(top1.thresholds(r, None, False)["unplaceable_rate"]["passed"])
            g, t = perfect(tmp, 100, unplaceable=1)
            r = top1.evaluate(top1.pick_gold(read_annotations(g), None), read_tagging_report(t)[1], None)
            self.assertTrue(top1.thresholds(r, None, False)["unplaceable_rate"]["passed"])
        # kappa 恰好 0.7 达标，略低不达标，算不出不达标
        self.assertTrue(top1.thresholds(r, Fraction(7, 10), True)["kappa"]["passed"])
        self.assertFalse(top1.thresholds(r, Fraction(699, 1000), True)["kappa"]["passed"])
        self.assertFalse(top1.thresholds(r, None, True)["kappa"]["passed"])

    def test_no_scored_items_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = write(tmp, "g.csv", HEADER + "S-1,final,,\n")
            t = write(tmp, "t.json", json.dumps({"map_version": "x", "items": []}))
            r = top1.evaluate(top1.pick_gold(read_annotations(g), None), read_tagging_report(t)[1], None)
        self.assertIsNone(r.top1)
        self.assertFalse(top1.thresholds(r, None, False)["top1"]["passed"])


class Cli(unittest.TestCase):
    def test_fixture_report_and_enforce(self):
        code, out, _ = run(["--gold", GOLD, "--tagging", TAGGING])
        self.assertEqual(code, 0, "未加 --enforce 时未达标也返回 0")
        self.assertIn("Top-1 准确率 = 66.7%（6/9）", out)
        self.assertIn("Top-3 召回 = 77.8%（7/9", out)
        self.assertIn("无处可标率 = 20.0%（2/10）", out)
        self.assertIn("门槛 top1 >= 0.9：未通过", out)
        self.assertIn("不一致 SYN-G-03", out)
        self.assertEqual(run(["--gold", GOLD, "--tagging", TAGGING, "--enforce"])[0], 1)

    def test_with_annotations_kappa(self):
        code, out, _ = run(["--gold", GOLD, "--tagging", TAGGING, "--annotations", ANN, "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["thresholds"]["kappa"], {"value": round(57 / 77, 6), "rule": ">= 0.7", "passed": True})
        self.assertEqual(data["top1"], round(6 / 9, 6))
        self.assertEqual(data["top3_recall"], round(7 / 9, 6))
        self.assertFalse(data["passed"])
        self.assertEqual([m["item_id"] for m in data["mismatches"]], ["SYN-G-03", "SYN-G-07", "SYN-G-09"])

    def test_enforce_passes_when_all_thresholds_met(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, t = perfect(tmp, 20, wrong=2)  # Top-1 = 18/20 = 90%，无处可标 0
            code, out, _ = run(["--gold", g, "--tagging", t, "--enforce"])
            self.assertEqual(code, 0, out)
            self.assertIn("总体：通过", out)
            g, t = perfect(tmp, 20, wrong=3)  # 85%
            self.assertEqual(run(["--gold", g, "--tagging", t, "--enforce"])[0], 1)

    def test_summary_only_prints_no_items(self):
        code, out, _ = run(["--gold", GOLD, "--tagging", TAGGING, "--summary-only"])
        self.assertEqual(code, 0)
        self.assertNotIn("SYN-G", out)
        code, out, _ = run(["--gold", GOLD, "--tagging", TAGGING, "--summary-only", "--json"])
        self.assertNotIn("SYN-G", out)
        self.assertNotIn("mismatches", json.loads(out))

    def test_input_errors_exit_2_and_redacted_in_summary_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = write(tmp, "g.csv", HEADER + "SECRET-ITEM-1,final,not-a-node,\n")
            code, _, err = run(["--gold", bad, "--tagging", TAGGING])
            self.assertEqual(code, 2)
            self.assertIn("not-a-node", err)
            code, out, err = run(["--gold", bad, "--tagging", TAGGING, "--summary-only"])
            self.assertEqual(code, 2)
            self.assertNotIn("SECRET-ITEM-1", err + out)
            self.assertNotIn("not-a-node", err + out)
            self.assertEqual(run(["--gold", GOLD, "--tagging", write(tmp, "t.json", "{")])[0], 2)
            self.assertEqual(run(["--gold", GOLD, "--tagging", str(Path(tmp) / "missing.json")])[0], 2)
            multi = write(tmp, "m.csv", HEADER + f"S-1,A,{N1},\nS-1,B,{N1},\n")
            self.assertEqual(run(["--gold", multi, "--tagging", TAGGING])[0], 2)
            self.assertEqual(run(["--gold", multi, "--gold-annotator", "A", "--tagging", TAGGING])[0], 0)
            with self.assertRaises(InputError):
                read_tagging_report(write(tmp, "d.json", json.dumps({"map_version": "x", "items": [{"item_id": "S-1"}, {"item_id": "S-1"}]})))


class Holdout(unittest.TestCase):
    SCRIPT = str(BASE / "run_holdout.sh")

    def sh(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHON": sys.executable}
        return subprocess.run(["bash", self.SCRIPT, *args], capture_output=True, text=True, env=env, check=False)

    def test_refuses_paths_inside_repo(self):
        p = self.sh("--gold", GOLD, "--tagging", TAGGING)
        self.assertEqual(p.returncode, 2)
        self.assertIn("仓库目录内", p.stderr)

    def test_usage(self):
        self.assertEqual(self.sh("--gold").returncode, 2)
        self.assertEqual(self.sh("--bogus", "x").returncode, 2)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.sh("--gold", str(Path(tmp) / "none.csv"), "--tagging", str(Path(tmp) / "none.json")).returncode, 2)

    def test_summary_only_outside_repo_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = write(tmp, "g.csv", Path(GOLD).read_text(encoding="utf-8"))
            t = write(tmp, "t.json", Path(TAGGING).read_text(encoding="utf-8"))
            c = write(tmp, "c.csv", Path(CANDS).read_text(encoding="utf-8"))
            before = sorted(os.listdir(tmp))
            p = self.sh("--gold", g, "--tagging", t, "--candidates", c)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Top-1 准确率 = 66.7%", p.stdout)
            self.assertIn("Top-3 召回 = 33.3%", p.stdout)
            self.assertNotIn("SYN-G", p.stdout + p.stderr, "留出集模式不打印逐题结果")
            self.assertEqual(sorted(os.listdir(tmp)), before, "不写任何文件")
            self.assertEqual(self.sh("--gold", g, "--tagging", t, "--enforce").returncode, 1)


if __name__ == "__main__":
    unittest.main()
