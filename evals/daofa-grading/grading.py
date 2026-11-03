"""道法采分点判分一致率（冲刺 4-K，specs/kge-daofa-slice-sprint4/spec.md 设计要点 6；待人确认 #7 口径默认）。

比对适配器的逐点判分与教师标注（金标，教研提供，放 evals/daofa-grading/gold/，构建方只读）：
- 逐点判定一致率（主指标，门槛 ≥ 90%）= 两边得分相同的采分点数 / 教师标注的采分点数；适配器缺某个点算不一致；
- 逐题总分一致率（辅助）= 两边该题总分相同的（回应, 题目）数 / 教师标注的（回应, 题目）数；适配器缺任何一个点算不一致；
- 失分归因混淆表（辅助）：行 = 教师的归因，列 = 适配器的归因；没有失分记 none，适配器缺这个点记 missing；
  另给失分归因一致率 = 教师标了归因的点里、适配器归因相同的比例。
全部用 fractions.Fraction 精确计算：恰好 90% 算达标。

输入（UTF-8 CSV；开头以 # 起始的行是注释）：表头固定为 response_id,item_id,point_id,score,attribution
- response_id：不透明的回应编号（不是学生 ID，不含个人信息）；item_id / point_id 同内容包；
- score：该点得分（0.5 的倍数）；attribution：失分归因编码（shenti_cuo / dingwei_cuo / zhuanhua_cuo / guifan_cuo），没有失分留空；
- **只含 ID 与分数**：作答原文、评语一类的列一律拒绝（规则 13；表头多一列即报错）。
可选 --pack：用内容包核对题目与采分点存在、得分不超过分值且在允许分值里、满分的点不能标失分归因。

报告只含汇总与 ID（回应、题目、采分点编号），不含作答与采分点内容。金标不在 evals/daofa-grading/gold/ 下时，报告标“流程样例”。

用法（在 evals/daofa-grading 下）：
  python3 grading.py --gold 教师标注.csv --adapter 适配器判分.csv [--pack ../../adapters/zhixing/content/pack.json]
                     [--json] [--summary-only] [--enforce]
退出码：0 算完（未加 --enforce 时未达标也是 0）；--enforce 且逐点一致率未达标为 1；输入错误为 2。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLD_DIR = HERE / "gold"
POINT_MIN = Fraction(9, 10)  # 逐点判定一致率门槛（spec 验收“采分点判分与教师一致率 ≥ 90%”）
COLUMNS = ("response_id", "item_id", "point_id", "score", "attribution")
ATTRIBUTIONS = ("shenti_cuo", "dingwei_cuo", "zhuanhua_cuo", "guifan_cuo")
CATEGORIES = (*ATTRIBUTIONS, "none", "missing")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
POINT_ID = re.compile(r"^[a-z0-9_\-]{1,48}$")
SCORE = re.compile(r"^\d{1,3}(\.[05])?$")

Key = tuple[str, str, str]  # (response_id, item_id, point_id)


class InputError(Exception):
    pass


@dataclass(frozen=True)
class Mark:
    score: Fraction
    attribution: str  # "" = 没有失分

    @property
    def category(self) -> str:
        return self.attribution or "none"


def read_marks(path: str | Path, pack: dict[tuple[str, str], tuple[Fraction, frozenset[Fraction]]] | None = None) -> dict[Key, Mark]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as e:
        raise InputError(f"文件读不了：{e}") from None
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    reader = csv.reader(io.StringIO("\n".join(lines)))
    head = next(reader, None)
    if head is None or tuple(h.strip() for h in head) != COLUMNS:
        raise InputError(f"{Path(path).name}：表头应为 {','.join(COLUMNS)}（只含 ID 与分数，不收作答原文）")
    out: dict[Key, Mark] = {}
    for n, row in enumerate(reader, start=2):
        if not row or all(c.strip() == "" for c in row):
            continue
        where = f"{Path(path).name} 第 {n} 条"
        if len(row) != len(COLUMNS):
            raise InputError(f"{where}：应有 {len(COLUMNS)} 列")
        rid, item, point, score, attr = (c.strip() for c in row)
        if not OPAQUE_ID.match(rid):
            raise InputError(f"{where}：response_id 不合法")
        if not OPAQUE_ID.match(item):
            raise InputError(f"{where}：item_id 不合法")
        if not POINT_ID.match(point):
            raise InputError(f"{where}：point_id 不合法")
        if not SCORE.match(score):
            raise InputError(f"{where}：score 应为非负的 0.5 倍数")
        if attr not in ("", *ATTRIBUTIONS):
            raise InputError(f"{where}：attribution 只能是 {' / '.join(ATTRIBUTIONS)} 或留空")
        s = Fraction(score)
        if pack is not None:
            spec = pack.get((item, point))
            if spec is None:
                raise InputError(f"{where}：内容包里没有 {item} 的采分点 {point}")
            full, allowed = spec
            if s not in allowed:
                raise InputError(f"{where}：得分不在该点允许的分值里")
            if s == full and attr:
                raise InputError(f"{where}：满分的点不能标失分归因")
        key = (rid, item, point)
        if key in out:
            raise InputError(f"{where}：同一回应的同一采分点重复")
        out[key] = Mark(s, attr)
    return out


def load_pack(path: str | Path) -> dict[tuple[str, str], tuple[Fraction, frozenset[Fraction]]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise InputError(f"内容包读不了：{e}") from None
    out = {}
    for it in raw.get("items", []):
        for p in it.get("points", []):
            allowed = p.get("partial_credit_rule", {}).get("allowed_scores") or [0, p["score"]]
            out[(it["id"], p["point_id"])] = (Fraction(str(p["score"])), frozenset(Fraction(str(x)) for x in allowed))
    return out


@dataclass
class Result:
    points: int = 0
    point_agree: int = 0
    missing: int = 0
    extra: int = 0
    items: int = 0
    item_agree: int = 0
    lost: int = 0  # 教师标了失分归因的点
    lost_agree: int = 0
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)
    point_mismatches: list[Key] = field(default_factory=list)
    item_mismatches: list[tuple[str, str]] = field(default_factory=list)

    @property
    def point_rate(self) -> Fraction | None:
        return Fraction(self.point_agree, self.points) if self.points else None

    @property
    def item_rate(self) -> Fraction | None:
        return Fraction(self.item_agree, self.items) if self.items else None

    @property
    def attribution_rate(self) -> Fraction | None:
        return Fraction(self.lost_agree, self.lost) if self.lost else None

    @property
    def passed(self) -> bool:
        return self.point_rate is not None and self.point_rate >= POINT_MIN


def evaluate(gold: dict[Key, Mark], adapter: dict[Key, Mark]) -> Result:
    r = Result()
    r.extra = sum(1 for k in adapter if k not in gold)
    by_item: dict[tuple[str, str], list[Key]] = {}
    for k in sorted(gold):
        g, a = gold[k], adapter.get(k)
        by_item.setdefault((k[0], k[1]), []).append(k)
        r.points += 1
        if a is None:
            r.missing += 1
        if a is not None and a.score == g.score:
            r.point_agree += 1
        else:
            r.point_mismatches.append(k)
        cell = (g.category, a.category if a is not None else "missing")
        r.confusion[cell] = r.confusion.get(cell, 0) + 1
        if g.attribution:
            r.lost += 1
            if a is not None and a.attribution == g.attribution:
                r.lost_agree += 1
    for ik, keys in sorted(by_item.items()):
        r.items += 1
        if all(k in adapter for k in keys) and sum(gold[k].score for k in keys) == sum(adapter[k].score for k in keys):
            r.item_agree += 1
        else:
            r.item_mismatches.append(ik)
    return r


def pct(x: Fraction | None) -> str:
    return "无法计算" if x is None else f"{float(x) * 100:.1f}%"


def as_float(x: Fraction | None) -> float | None:
    return None if x is None else round(float(x), 6)


def is_real_gold(path: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(GOLD_DIR.resolve())
        return True
    except ValueError:
        return False


def confusion_table(r: Result) -> list[list[str]]:
    cols = [c for c in CATEGORIES if any(k[1] == c for k in r.confusion)]
    rows = [c for c in CATEGORIES if any(k[0] == c for k in r.confusion)]
    return [["教师 \\ 适配器", *cols], *[[g, *[str(r.confusion.get((g, a), 0)) for a in cols]] for g in rows]]


def summary(r: Result, sample: bool) -> dict:
    return {
        "label": "流程样例" if sample else "金标",
        "points": r.points, "point_agree": r.point_agree, "point_agreement": as_float(r.point_rate),
        "items": r.items, "item_agree": r.item_agree, "item_total_agreement": as_float(r.item_rate),
        "attribution_points": r.lost, "attribution_agree": r.lost_agree, "attribution_agreement": as_float(r.attribution_rate),
        "missing": r.missing, "extra": r.extra,
        "confusion": [{"teacher": g, "adapter": a, "count": n} for (g, a), n in sorted(r.confusion.items(),
                      key=lambda x: (CATEGORIES.index(x[0][0]), CATEGORIES.index(x[0][1])))],
        "threshold": {"point_agreement_min": float(POINT_MIN), "passed": r.passed},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grading.py", description="道法采分点判分：适配器 vs 教师标注的一致率与失分归因混淆表")
    ap.add_argument("--gold", required=True, help="教师逐点标注 CSV（金标放 gold/，只读）")
    ap.add_argument("--adapter", required=True, help="适配器逐点判分 CSV")
    ap.add_argument("--pack", help="内容包 pack.json（核对题目、采分点与分值）")
    ap.add_argument("--json", action="store_true", help="汇总以 JSON 打印")
    ap.add_argument("--summary-only", action="store_true", help="只打印汇总，不列不一致的 ID")
    ap.add_argument("--enforce", action="store_true", help="逐点一致率未达标时退出码为 1（教研标注到位前不进 make check）")
    args = ap.parse_args(argv)
    try:
        pack = load_pack(args.pack) if args.pack else None
        gold = read_marks(args.gold, pack)
        adapter = read_marks(args.adapter, pack)
        if not gold:
            raise InputError("教师标注是空的")
    except InputError as e:
        print(f"grading: {e}", file=sys.stderr)
        return 2
    r = evaluate(gold, adapter)
    sample = not is_real_gold(args.gold)
    if args.json:
        out = summary(r, sample)
        if not args.summary_only:
            out["point_mismatches"] = [list(k) for k in r.point_mismatches]
            out["item_mismatches"] = [list(k) for k in r.item_mismatches]
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    else:
        if sample:
            print("grading: 【流程样例】金标不在 evals/daofa-grading/gold/ 下：合成数据只证明计算流程，数字不代表判分质量")
        print(f"grading: 逐点判定一致率 = {pct(r.point_rate)}（{r.point_agree}/{r.points}；适配器缺 {r.missing} 个点，多出 {r.extra} 个点不计）")
        print(f"grading: 逐题总分一致率 = {pct(r.item_rate)}（{r.item_agree}/{r.items}）")
        print(f"grading: 失分归因一致率 = {pct(r.attribution_rate)}（{r.lost_agree}/{r.lost}，教师标了归因的点）")
        print(f"grading: 门槛 逐点一致率 ≥ {float(POINT_MIN) * 100:.0f}%：{'通过' if r.passed else '未通过'}"
              f"{'' if args.enforce else '（未加 --enforce，不影响退出码）'}")
        print("grading: 失分归因混淆表（行 = 教师，列 = 适配器）：")
        for row in confusion_table(r):
            print("  | " + " | ".join(row) + " |")
        if not args.summary_only:
            for k in r.point_mismatches:
                print(f"grading: 逐点不一致 {k[0]} / {k[1]} / {k[2]}")
            for k in r.item_mismatches:
                print(f"grading: 总分不一致 {k[0]} / {k[1]}")
    return 1 if args.enforce and not r.passed else 0


if __name__ == "__main__":
    sys.exit(main())
