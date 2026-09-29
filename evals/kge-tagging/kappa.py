"""两名标注者的一致性（specs/kge-02-atoms-tagging/spec.md 设计要点 5；KGE V1.1 :745 kappa ≥ 0.7）。

- Cohen's kappa：按主考点算。类别 = 主考点节点 ID，“无处可标”（主考点为空）也算一个类别。
    p_o = 两人主考点相同的题数 / 共同标注的题数
    p_e = Σ_k p_A(k) · p_B(k)，p_X(k) = 标注者 X 把主考点标成 k 的比例
    kappa = (p_o − p_e) / (1 − p_e)；p_e = 1 时（两人所有题都标同一个类别，必然完全一致）记 1。
- 节点集合 Jaccard：每题 |A ∩ B| / |A ∪ B|，集合 = {主考点} ∪ node_ids；两人都为空集记 1；对共同标注的题取平均。
- 分歧清单：主考点不同的题（题目 ID、两人的主考点与节点集合），供教研“分歧转为节点描述”。

用法（在 evals/kge-tagging 下）：
  python3 kappa.py --annotations 标注.csv [--annotators T1,T2] [--disagreements 分歧.csv] [--json] [--enforce]
退出码：0 算完（未达标也是 0）；--enforce 时 kappa 未达标为 1；输入错误为 2。
标注只由教研提交（gold/ 只读）；本脚本不写 gold/，分歧清单写到 --disagreements 指定的位置（默认打印）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction

from common import KAPPA_MIN, Annotation, InputError, as_float, fmt, read_annotations

NONE = "（无处可标）"  # 主考点为空时的类别名


def cohen_kappa(pairs: list[tuple[str | None, str | None]]) -> Fraction | None:
    """pairs：每题两人的主考点。没有题时返回 None。"""
    n = len(pairs)
    if n == 0:
        return None
    a = Counter(x if x is not None else NONE for x, _ in pairs)
    b = Counter(y if y is not None else NONE for _, y in pairs)
    p_o = Fraction(sum(1 for x, y in pairs if x == y), n)
    p_e = sum((Fraction(a[k], n) * Fraction(b[k], n) for k in a.keys() & b.keys()), Fraction(0))
    if p_e == 1:
        return Fraction(1)
    return (p_o - p_e) / (1 - p_e)


def jaccard(a: frozenset[str], b: frozenset[str]) -> Fraction:
    union = a | b
    return Fraction(1) if not union else Fraction(len(a & b), len(union))


@dataclass
class KappaResult:
    annotator_a: str
    annotator_b: str
    items: int
    only_a: int
    only_b: int
    kappa: Fraction | None
    observed: Fraction | None
    mean_jaccard: Fraction | None
    disagreements: list[tuple[Annotation, Annotation]]

    @property
    def passed(self) -> bool:
        return self.kappa is not None and self.kappa >= KAPPA_MIN


def compare(data: dict[str, dict[str, Annotation]], a: str, b: str) -> KappaResult:
    if a not in data or b not in data:
        raise InputError(f"标注文件里没有标注者 {a if a not in data else b}")
    if a == b:
        raise InputError("两名标注者不能相同")
    A, B = data[a], data[b]
    common = sorted(A.keys() & B.keys())
    pairs = [(A[i].main_node_id, B[i].main_node_id) for i in common]
    n = len(common)
    return KappaResult(
        annotator_a=a, annotator_b=b, items=n,
        only_a=len(A.keys() - B.keys()), only_b=len(B.keys() - A.keys()),
        kappa=cohen_kappa(pairs),
        observed=Fraction(sum(1 for x, y in pairs if x == y), n) if n else None,
        mean_jaccard=(sum((jaccard(A[i].node_set(), B[i].node_set()) for i in common), Fraction(0)) / n) if n else None,
        disagreements=[(A[i], B[i]) for i in common if A[i].main_node_id != B[i].main_node_id],
    )


def pick_annotators(data: dict[str, dict[str, Annotation]], arg: str | None) -> tuple[str, str]:
    if arg:
        parts = [x.strip() for x in arg.split(",")]
        if len(parts) != 2:
            raise InputError("--annotators 要给两个标注者代号，逗号分隔")
        return parts[0], parts[1]
    names = sorted(k for k in data if k != "final")  # final 是定稿，不参与一致性
    if len(names) != 2:
        raise InputError(f"标注文件里有 {len(names)} 名标注者（不含 final），请用 --annotators 指定两名")
    return names[0], names[1]


def disagreements_csv(r: KappaResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["item_id", "annotator_a", "main_a", "nodes_a", "annotator_b", "main_b", "nodes_b"])
    for x, y in r.disagreements:
        w.writerow([x.item_id, x.annotator, x.main_node_id or "", ";".join(sorted(x.node_set())),
                    y.annotator, y.main_node_id or "", ";".join(sorted(y.node_set()))])
    return buf.getvalue()


def summary(r: KappaResult) -> dict:
    return {
        "annotators": [r.annotator_a, r.annotator_b], "items": r.items, "only_a": r.only_a, "only_b": r.only_b,
        "kappa": as_float(r.kappa), "observed_agreement": as_float(r.observed), "mean_jaccard": as_float(r.mean_jaccard),
        "disagreements": len(r.disagreements), "threshold": {"kappa_min": float(KAPPA_MIN), "passed": r.passed},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kappa.py", description="两名标注者主考点 Cohen's kappa 与节点集合 Jaccard")
    ap.add_argument("--annotations", required=True, help="标注 CSV：item_id,annotator,main_node_id,node_ids")
    ap.add_argument("--annotators", help="两名标注者代号，逗号分隔；不给时取文件里除 final 外的两名")
    ap.add_argument("--disagreements", help="分歧清单 CSV 的输出路径（不给则打印到标准输出）")
    ap.add_argument("--json", action="store_true", help="汇总以 JSON 打印")
    ap.add_argument("--enforce", action="store_true", help="kappa 未达标时退出码为 1（教研标注到位前不进 make check）")
    args = ap.parse_args(argv)
    try:
        data = read_annotations(args.annotations)
        a, b = pick_annotators(data, args.annotators)
        r = compare(data, a, b)
    except (InputError, OSError) as e:
        print(f"kappa: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary(r), ensure_ascii=False, sort_keys=True))
    else:
        print(f"kappa: 标注者 {a} 与 {b}，共同标注 {r.items} 题（只有 {a}：{r.only_a}，只有 {b}：{r.only_b}）")
        print(f"kappa: 主考点 Cohen's kappa = {fmt(r.kappa)}（观察一致率 {fmt(r.observed, pct=True)}）；节点集合平均 Jaccard = {fmt(r.mean_jaccard)}")
        print(f"kappa: 门槛 kappa ≥ {float(KAPPA_MIN)}：{'通过' if r.passed else '未通过'}；主考点分歧 {len(r.disagreements)} 题")
    text = disagreements_csv(r)
    if args.disagreements:
        with open(args.disagreements, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    elif not args.json:
        print("kappa: 分歧清单（CSV）：")
        print(text, end="")
    return 1 if args.enforce and not r.passed else 0


if __name__ == "__main__":
    sys.exit(main())
