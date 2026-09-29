"""打标结果对金标集（specs/kge-02-atoms-tagging/spec.md 设计要点 5；KGE V1.1 :745–753）。

指标：
- Top-1 准确率 = 预测主考点与金标主考点相同的题数 / 金标有主考点的题数（缺预测的题算错）；
- Top-3 召回 = 金标主考点在该题主考点候选前 3 位内的题数 / 金标有主考点的题数；
    候选来源按优先级：--candidates（kge.tag_candidate 格式）＞ 报告里每题的 candidates ＞ 退化为 [主考点] + node_ids（报告里没有候选列表时）；
    排序：同一节点取最高置信度，置信度降序，相同按节点 ID 码点升序；
- 无处可标率 = 预测没有主考点（含缺预测）的题数 / 金标题数；
- 可选 --annotations：同时算两名标注者的 kappa（见 kappa.py）。
门槛：kappa ≥ 0.7、Top-1 ≥ 90%、无处可标率 < 2%。给出通过 / 未通过，但只有 --enforce 时才据此返回 1
（教研标注到位、真实模型接入前不作为 make check 的通过条件）。

用法（在 evals/kge-tagging 下）：
  python3 top1.py --gold 金标.csv --tagging tagging-report.json [--candidates 候选.csv|.json] [--gold-annotator final]
                  [--annotations 两人标注.csv [--annotators T1,T2]] [--json] [--summary-only] [--enforce]
--summary-only：只打印汇总指标，不打印逐题结果（留出集用，见 run_holdout.sh）。
退出码：0 算完；--enforce 且有门槛未通过为 1；输入错误为 2。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from fractions import Fraction

from common import (KAPPA_MIN, TOP1_MIN, UNPLACEABLE_MAX, Annotation, InputError, Prediction, as_float, fmt, rank, read_annotations,
                    read_candidates, read_tagging_report)

TOPK = 3


@dataclass
class ItemResult:
    item_id: str
    gold: str | None
    predicted: str | None
    top1: bool
    in_top3: bool
    missing: bool


@dataclass
class Top1Result:
    gold_items: int
    gold_unplaceable: int  # 金标认为无处可标的题（不进 Top-1 / Top-3 分母）
    scored: int  # 金标有主考点的题
    top1_hits: int
    top3_hits: int
    unplaceable: int  # 预测没有主考点（含缺预测）
    missing: int  # 金标里有、打标报告里没有的题
    extra: int  # 打标报告里有、金标里没有的题（不计分）
    candidate_source: str
    items: list[ItemResult] = field(default_factory=list)

    @property
    def top1(self) -> Fraction | None:
        return Fraction(self.top1_hits, self.scored) if self.scored else None

    @property
    def top3(self) -> Fraction | None:
        return Fraction(self.top3_hits, self.scored) if self.scored else None

    @property
    def unplaceable_rate(self) -> Fraction | None:
        return Fraction(self.unplaceable, self.gold_items) if self.gold_items else None


def pick_gold(data: dict[str, dict[str, Annotation]], annotator: str | None) -> dict[str, Annotation]:
    """金标定稿：指定标注者（默认 final）；文件里只有一名标注者时直接用。"""
    if annotator:
        if annotator not in data:
            raise InputError(f"金标文件里没有标注者 {annotator}")
        return data[annotator]
    if "final" in data:
        return data["final"]
    if len(data) == 1:
        return next(iter(data.values()))
    raise InputError("金标文件里有多名标注者、没有 final，请用 --gold-annotator 指定定稿")


def evaluate(gold: dict[str, Annotation], preds: dict[str, Prediction],
             candidates: dict[str, tuple[tuple[str, Fraction], ...]] | None) -> Top1Result:
    if candidates is not None:
        source = "候选文件（kge.tag_candidate 格式）"
    elif any(p.candidates is not None for p in preds.values()):
        source = "打标报告里的 candidates"
    else:
        source = "退化：报告的主考点 + node_ids（没有候选列表）"
    r = Top1Result(gold_items=len(gold), gold_unplaceable=0, scored=0, top1_hits=0, top3_hits=0, unplaceable=0,
                   missing=0, extra=len(preds.keys() - gold.keys()), candidate_source=source)
    for item in sorted(gold):
        g = gold[item].main_node_id
        p = preds.get(item)
        missing = p is None
        predicted = None if p is None else p.main_node_id
        if missing:
            r.missing += 1
        if predicted is None:
            r.unplaceable += 1
        if candidates is not None:
            ranked = rank(candidates.get(item, ()))
        elif p is not None and p.candidates is not None:
            ranked = rank(p.candidates)
        elif p is not None:
            ranked = list(dict.fromkeys(([p.main_node_id] if p.main_node_id else []) + list(p.node_ids)))
        else:
            ranked = []
        hit1 = g is not None and predicted == g
        hit3 = g is not None and g in ranked[:TOPK]
        if g is None:
            r.gold_unplaceable += 1
        else:
            r.scored += 1
            r.top1_hits += hit1
            r.top3_hits += hit3
        r.items.append(ItemResult(item, g, predicted, hit1, hit3, missing))
    return r


def thresholds(r: Top1Result, kappa: Fraction | None, kappa_measured: bool) -> dict[str, dict]:
    out = {
        "top1": {"value": as_float(r.top1), "rule": f">= {float(TOP1_MIN)}", "passed": r.top1 is not None and r.top1 >= TOP1_MIN},
        "unplaceable_rate": {"value": as_float(r.unplaceable_rate), "rule": f"< {float(UNPLACEABLE_MAX)}",
                             "passed": r.unplaceable_rate is not None and r.unplaceable_rate < UNPLACEABLE_MAX},
    }
    if kappa_measured:
        out = {"kappa": {"value": as_float(kappa), "rule": f">= {float(KAPPA_MIN)}", "passed": kappa is not None and kappa >= KAPPA_MIN},
               **out}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="top1.py", description="打标结果对金标集：Top-1、Top-3 召回、无处可标率")
    ap.add_argument("--gold", required=True, help="金标 CSV：item_id,annotator,main_node_id,node_ids")
    ap.add_argument("--gold-annotator", help="定稿的标注者代号（默认 final；文件里只有一名时直接用）")
    ap.add_argument("--tagging", required=True, help="打标报告 tagging-report.json（或同格式）")
    ap.add_argument("--candidates", help="主考点候选：kge.tag_candidate 格式的 CSV 或 JSON")
    ap.add_argument("--annotations", help="两名标注者的标注 CSV（同时算 kappa）")
    ap.add_argument("--annotators", help="两名标注者代号，逗号分隔")
    ap.add_argument("--json", action="store_true", help="汇总以 JSON 打印")
    ap.add_argument("--summary-only", action="store_true", help="只打印汇总，不打印逐题结果（留出集）")
    ap.add_argument("--enforce", action="store_true", help="有门槛未通过时退出码为 1（教研标注到位前不进 make check）")
    args = ap.parse_args(argv)
    redact = args.summary_only
    try:
        gold = pick_gold(read_annotations(args.gold), args.gold_annotator)
        map_version, preds = read_tagging_report(args.tagging)
        cands = read_candidates(args.candidates) if args.candidates else None
        kappa = None
        if args.annotations:
            from kappa import compare, pick_annotators
            data = read_annotations(args.annotations)
            kappa = compare(data, *pick_annotators(data, args.annotators)).kappa
    except InputError as e:
        print(f"top1: {e.redacted if redact else e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"top1: 文件读不了{'' if redact else f'：{e}'}", file=sys.stderr)
        return 2
    r = evaluate(gold, preds, cands)
    th = thresholds(r, kappa, args.annotations is not None)
    all_passed = all(v["passed"] for v in th.values())
    if args.json:
        out = {
            "map_version": map_version, "gold_items": r.gold_items, "gold_unplaceable": r.gold_unplaceable, "scored": r.scored,
            "top1": as_float(r.top1), "top3_recall": as_float(r.top3), "unplaceable_rate": as_float(r.unplaceable_rate),
            "missing": r.missing, "extra": r.extra, "candidate_source": r.candidate_source,
            "thresholds": th, "passed": all_passed,
        }
        if not args.summary_only:
            out["mismatches"] = [{"item_id": x.item_id, "gold": x.gold, "predicted": x.predicted, "in_top3": x.in_top3}
                                 for x in r.items if x.gold is not None and not x.top1]
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    else:
        print(f"top1: 地图版本 {map_version}；金标 {r.gold_items} 题（金标无处可标 {r.gold_unplaceable}，计分 {r.scored}）；"
              f"缺预测 {r.missing}，报告多出 {r.extra}")
        print(f"top1: Top-1 准确率 = {fmt(r.top1, pct=True)}（{r.top1_hits}/{r.scored}）；Top-3 召回 = {fmt(r.top3, pct=True)}"
              f"（{r.top3_hits}/{r.scored}，候选来源：{r.candidate_source}）")
        print(f"top1: 无处可标率 = {fmt(r.unplaceable_rate, pct=True)}（{r.unplaceable}/{r.gold_items}）")
        for name, v in th.items():
            print(f"top1: 门槛 {name} {v['rule']}：{'通过' if v['passed'] else '未通过'}")
        if not args.annotations:
            print("top1: 门槛 kappa：未测（没有给 --annotations）")
        print(f"top1: 总体：{'通过' if all_passed else '未通过'}{'' if args.enforce else '（未加 --enforce，不影响退出码）'}")
        if not args.summary_only:
            for x in r.items:
                if x.gold is not None and not x.top1:
                    print(f"top1: 不一致 {x.item_id}：金标 {x.gold}，预测 {x.predicted or '（无）'}，"
                          f"{'在' if x.in_top3 else '不在'}前 3 候选内")
    return 1 if args.enforce and not all_passed else 0


if __name__ == "__main__":
    sys.exit(main())
