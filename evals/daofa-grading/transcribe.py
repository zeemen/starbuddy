#!/usr/bin/env python3
"""道法适配器判分 → 评测 CSV 的转写（W13 T34，specs/tech-debt-batch3 F2；evals/daofa-grading/README.md“适配器判分 CSV”）。

输入：JSON Lines，每行一条 {"response_id": <不透明编号>, "item_id": <题号>, "grade": <知行适配器 grade 的完整返回>}。
  response_id 是评测方给回应编的不透明编号（不是学生 ID，不含个人信息）；grade 原样取自适配器（plugin-api v1.0 GradeResponse）。
输出：grading.py 的适配器 CSV（response_id,item_id,point_id,score,attribution），一个采分点一行：
  - point_id = result.dimensions[].dimension_id；score = 该维度得分（0.5 的倍数）；
  - attribution：result.error_attributions 里 evidence_ids 与该维度 evidence_ids 有交集的那一条的 subject_category（知行四类）；
    没有失分或没有归因留空；同一维度对上两条以上归因 → 输入错误（不猜）；
  - 维度得分为 null（待复核、未判定，score.excluded_dimensions）的点不写：grading.py 把它算作适配器缺点（不一致），与 README 口径相同。
只写 ID 与分数：作答原文、证据内容、评语一律不读不写（规则 13）；不调用任何模型、不联网，只用标准库。

命令行：
  python3 transcribe.py --in <grades.jsonl> --out <adapter.csv>        输出文件已存在即拒绝（加 --replace 覆盖）
退出码：0 成功；2 输入不对（格式、编号、分值、归因对不上）或输出文件已存在。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grading import ATTRIBUTIONS, OPAQUE_ID, POINT_ID  # noqa: E402  与评测脚本同一套编号与归因口径

HEADER = ("response_id", "item_id", "point_id", "score", "attribution")
NOTICE = "# 适配器判分转写（evals/daofa-grading/transcribe.py）：只含 ID 与分数；未判定的点不写（评测按缺点计）"


class InputError(Exception):
    pass


def _score(v: Any, where: str) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise InputError(f"{where}：得分不是数字")
    f = Fraction(v).limit_denominator(2)
    if f != Fraction(v) or f < 0 or f.denominator not in (1, 2) or f > 999:
        raise InputError(f"{where}：得分必须是 0–999 之间 0.5 的倍数")
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator // 2}.5"


def rows_of(record: Any, n: int) -> list[tuple[str, str, str, str, str]]:
    where = f"第 {n} 行"
    if not isinstance(record, dict) or set(record) != {"response_id", "item_id", "grade"}:
        raise InputError(f"{where}：只能有 response_id、item_id、grade 三个键")
    rid, iid, grade = record["response_id"], record["item_id"], record["grade"]
    if not isinstance(rid, str) or not OPAQUE_ID.fullmatch(rid):
        raise InputError(f"{where}：response_id 不是合法的不透明编号")
    if not isinstance(iid, str) or not OPAQUE_ID.fullmatch(iid):
        raise InputError(f"{where}：item_id 不合法")
    result = grade.get("result") if isinstance(grade, dict) else None
    dims = result.get("dimensions") if isinstance(result, dict) else None
    if not isinstance(dims, list) or not dims:
        raise InputError(f"{where}：grade 里没有 result.dimensions")
    attrs = result.get("error_attributions", [])
    if not isinstance(attrs, list):
        raise InputError(f"{where}：error_attributions 不是数组")
    by_evidence: dict[str, set[str]] = {}
    for a in attrs:
        code = a.get("subject_category") if isinstance(a, dict) else None
        ids = a.get("evidence_ids") if isinstance(a, dict) else None
        if code not in ATTRIBUTIONS or not isinstance(ids, list):
            raise InputError(f"{where}：错因不是知行四类（{' / '.join(ATTRIBUTIONS)}）或缺 evidence_ids")
        for e in ids:
            by_evidence.setdefault(str(e), set()).add(code)
    out, seen = [], set()
    for d in dims:
        pid = d.get("dimension_id") if isinstance(d, dict) else None
        if not isinstance(pid, str) or not POINT_ID.fullmatch(pid):
            raise InputError(f"{where}：dimension_id 不合法")
        if pid in seen:
            raise InputError(f"{where}：采分点 {pid} 重复")
        seen.add(pid)
        if d.get("score") is None:  # 待复核、未判定：不写，评测按缺点计
            continue
        score = _score(d["score"], f"{where} 采分点 {pid}")
        codes = set().union(*(by_evidence.get(str(e), set()) for e in d.get("evidence_ids") or []))
        if len(codes) > 1:
            raise InputError(f"{where} 采分点 {pid}：对上了 {len(codes)} 条错因，不能确定是哪一类")
        out.append((rid, iid, pid, score, next(iter(codes), "")))
    return out


def transcribe(lines: Iterable[str]) -> str:
    buf = io.StringIO()
    buf.write(NOTICE + "\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    keys: set[tuple[str, str]] = set()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        except ValueError:
            raise InputError(f"第 {n} 行不是合法的 JSON") from None
        rows = rows_of(record, n)
        key = (record["response_id"], record["item_id"])
        if key in keys:
            raise InputError(f"第 {n} 行：回应 {key[0]} 的题目 {key[1]} 重复")
        keys.add(key)
        w.writerows(rows)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="transcribe.py", description="道法适配器判分 → 评测 CSV")
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--replace", action="store_true")
    a = p.parse_args(argv)
    out = Path(a.out)
    if out.exists() and not a.replace:
        print("transcribe: 输出文件已存在（覆盖须加 --replace）", file=sys.stderr)
        return 2
    try:
        text = transcribe(Path(a.src).read_text(encoding="utf-8").splitlines())
    except InputError as e:
        print(f"transcribe: 输入不对（{e}）", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"transcribe: 读写文件失败（{e.__class__.__name__}）", file=sys.stderr)
        return 2
    out.write_text(text, encoding="utf-8")
    print(f"transcribe: 写入 {sum(1 for x in text.splitlines() if x and not x.startswith('#')) - 1} 个采分点", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
