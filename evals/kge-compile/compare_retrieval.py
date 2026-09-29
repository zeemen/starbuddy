"""对照评测：页面优先 vs 纯切块检索（KGE-03 spec 设计要点 7；KGE V1.1 第九章“编译”一行）。

同一套金标问答、同一份公共层语料（已发布页面快照 + 已发布地图快照），两条路径各跑一遍：
- page_first：knowledge.retrieval.pipeline.PageFirstRetriever（定位节点 → 页面 → 按需原子 → 兜底检索）；
- chunk：knowledge.retrieval.pipeline.ChunkRetriever（同一套切块上的 BM25，取前 5 块）。

指标（每题取“主问题第一次”的上下文算正确率与成本；一致性用全部取回）：
- 正确率：上下文是否覆盖答案要点。判定规则可插拔（JUDGES / register_judge）：
    all_points（缺省）= 全部要点都出现；majority = 过半要点出现。要点与上下文都先做 NFKC、小写、去空白与标点再按子串匹配。
  另报要点覆盖率 = Σ 覆盖要点数 / Σ 要点数。
- 同题讲法一致性：主问题重复取 runs 次（缺省 2）+ 每条同义改写各取 1 次，两两算来源集合的 Jaccard（来源 = 页面 ID + 版本 + 段落，
  或原子键），取平均；各题再平均。两边都为空记 1。
- 单次上下文成本：主问题上下文的字符数与 token 估算（每个汉字 1、每串连续字母数字 1、其余每个非空白符号 1）。
- 另报：定位命中率（上下文包的节点 = 金标节点）、页面优先路径的兜底次数（按原因）。
报告（Markdown + JSON）只含汇总与题目 ID：不含问题、改写、答案要点与上下文原文；不含时间与本机路径；同样输入逐字节相同。
金标 `synthetic: true` 时报告头标注“流程样例，不是评测结论”。M1 结论由人定（未达标则以切块检索为主路径进入封测）。

    services/knowledge/.venv/bin/python evals/kge-compile/compare_retrieval.py --gold <金标.yaml> \
        --map-snapshot <地图快照> --page-snapshot <页面快照目录> --out <输出目录> [--judge all_points] [--runs 2]
退出码：0 生成成功；2 参数或输入错误（消息只含字段名与题号，不含题目原文）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
KNOWLEDGE = HERE.parents[1] / "services" / "knowledge"
if str(KNOWLEDGE) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE))

import yaml  # noqa: E402

from knowledge.atoms import SnapshotError, load_snapshot  # noqa: E402
from knowledge.nodeid import is_node_id  # noqa: E402
from knowledge.retrieval.pipeline import (RETRIEVAL_VERSION, ChunkRetriever, ContextPack, Corpus,  # noqa: E402
                                          PageFirstRetriever)
from knowledge.retrieval.signals import REASONS  # noqa: E402
from knowledge.retrieval.tokenize import TOKENIZER_VERSION  # noqa: E402
from knowledge.service.pages import SnapshotLoadError, load_snapshot_dir  # noqa: E402

GOLD_FORMAT = "kge-gold-qa/1"
REPORT_FORMAT = "kge-compare-retrieval/1"
PATHS = ("page_first", "chunk")
MIN_REAL_QUESTIONS = 50
QID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
SAMPLE_NOTICE = "流程样例，不是评测结论：金标为合成数据，只证明脚本流程与指标计算。"
REAL_NOTICE = "M1 结论由人定：页面优先未达标时，以切块检索为主路径进入封测（KGE V1.1 第九章）。"
_NORM_DROP = re.compile(r"[\s　，。、；：！？,.;:!?（）()《》“”\"'【】\[\]—\-·…]+")
_TOKEN = re.compile(r"[㐀-䶿一-鿿]|[0-9A-Za-z]+|[^\s0-9A-Za-z㐀-䶿一-鿿]")


class GoldError(ValueError):
    """金标格式错误（消息只含字段名与题号）"""


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    answer_points: tuple[str, ...]
    node_id: str | None = None
    paraphrases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Gold:
    synthetic: bool
    questions: tuple[Question, ...]
    digest: str


def load_gold(path: str | Path) -> Gold:
    raw = Path(path).read_bytes()
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise GoldError("金标不是 UTF-8 YAML") from None
    if not isinstance(doc, dict) or doc.get("format") != GOLD_FORMAT:
        raise GoldError(f"金标 format 必须是 {GOLD_FORMAT}")
    if not isinstance(doc.get("synthetic"), bool):
        raise GoldError("金标必须写明 synthetic: true / false")
    qs = doc.get("questions")
    if not isinstance(qs, list) or not qs:
        raise GoldError("questions 必须是非空数组")
    out: list[Question] = []
    seen: set[str] = set()
    for i, q in enumerate(qs):
        where = f"questions[{i}]"
        if not isinstance(q, dict) or set(q) - {"id", "question", "answer_points", "node_id", "paraphrases"}:
            raise GoldError(f"{where}: 字段不对")
        qid = q.get("id")
        if not isinstance(qid, str) or not QID.fullmatch(qid) or qid in seen:
            raise GoldError(f"{where}.id 缺失、不合规范或重复")
        seen.add(qid)
        if not isinstance(q.get("question"), str) or not q["question"].strip():
            raise GoldError(f"{qid}: question 必须是非空字符串")
        pts = q.get("answer_points")
        if not isinstance(pts, list) or not pts or not all(isinstance(p, str) and _norm(p) for p in pts):
            raise GoldError(f"{qid}: answer_points 必须是非空字符串数组")
        paras = q.get("paraphrases", [])
        if not isinstance(paras, list) or not all(isinstance(p, str) and p.strip() for p in paras):
            raise GoldError(f"{qid}: paraphrases 必须是字符串数组")
        nid = q.get("node_id")
        if nid is not None and (not isinstance(nid, str) or not is_node_id(nid)):
            raise GoldError(f"{qid}: node_id 不合节点 ID 规范")
        out.append(Question(qid, q["question"], tuple(pts), nid, tuple(paras)))
    return Gold(doc["synthetic"], tuple(sorted(out, key=lambda q: q.id)), hashlib.sha256(raw).hexdigest())


# ---------- 判定规则（可插拔） ----------
@dataclass(frozen=True)
class Judgement:
    correct: bool
    covered: int
    total: int


Judge = Callable[[str, Sequence[str]], Judgement]


def _norm(s: str) -> str:
    return _NORM_DROP.sub("", unicodedata.normalize("NFKC", s)).lower()


def _covered(context: str, points: Sequence[str]) -> int:
    c = _norm(context)
    return sum(1 for p in points if _norm(p) in c)


def all_points(context: str, points: Sequence[str]) -> Judgement:
    n = _covered(context, points)
    return Judgement(n == len(points), n, len(points))


def majority(context: str, points: Sequence[str]) -> Judgement:
    n = _covered(context, points)
    return Judgement(2 * n > len(points), n, len(points))


JUDGES: dict[str, Judge] = {"all_points": all_points, "majority": majority}


def register_judge(name: str, fn: Judge) -> None:
    """接入别的判定规则（例如教研给的同义词表、人工判定结果回填）；名字进报告"""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) or name in JUDGES:
        raise ValueError("判定规则名不合规范或已存在")
    JUDGES[name] = fn


# ---------- 指标 ----------
def estimate_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def jaccard(a: set[str], b: set[str]) -> Fraction:
    if not a and not b:
        return Fraction(1)
    return Fraction(len(a & b), len(a | b))


def consistency(packs: Sequence[ContextPack]) -> Fraction:
    sets = [set(p.source_keys) for p in packs]
    pairs = list(combinations(sets, 2))
    if not pairs:
        return Fraction(1)
    return sum((jaccard(a, b) for a, b in pairs), Fraction(0)) / len(pairs)


def _dec(x: Fraction) -> float:
    return float(round(x, 4))


def _mean(xs: Sequence[Fraction | int]) -> Fraction:
    return sum((Fraction(x) for x in xs), Fraction(0)) / len(xs) if xs else Fraction(0)


def evaluate(gold: Gold, corpus: Corpus, *, judge: str = "all_points", runs: int = 2,
             retrievers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """retrievers 只给测试注入（手算核对汇总）；缺省为两条真实路径"""
    if judge not in JUDGES:
        raise GoldError(f"未登记的判定规则：{judge}")
    if runs < 2:
        raise GoldError("runs 至少为 2（一致性需要重复取回）")
    fn = JUDGES[judge]
    if retrievers is None:
        retrievers = {"page_first": PageFirstRetriever(corpus), "chunk": ChunkRetriever(corpus)}
    rows: list[dict[str, Any]] = []
    agg: dict[str, dict[str, list]] = {p: {"correct": [], "covered": [], "total": [], "cons": [], "tok": [], "chars": [],
                                           "hit": [], "fallback": []} for p in PATHS}
    for q in gold.questions:
        row: dict[str, Any] = {"id": q.id}
        for path in PATHS:
            r = retrievers[path]
            packs = [r.lookup(q.question) for _ in range(runs)] + [r.lookup(p) for p in q.paraphrases]
            main = packs[0]
            j = fn(main.text, q.answer_points)
            cons = consistency(packs)
            tok, chars = estimate_tokens(main.text), len(main.text)
            hit = None if q.node_id is None else (main.node_id == q.node_id)
            a = agg[path]
            a["correct"].append(int(j.correct)); a["covered"].append(j.covered); a["total"].append(j.total)
            a["cons"].append(cons); a["tok"].append(tok); a["chars"].append(chars)
            if hit is not None:
                a["hit"].append(int(hit))
            if main.fallback_reason:
                a["fallback"].append(main.fallback_reason)
            cell = {"correct": j.correct, "points_covered": j.covered, "points_total": j.total, "consistency": _dec(cons),
                    "context_tokens": tok, "context_chars": chars, "segments": len(main.segments), "node_hit": hit}
            if path == "page_first":
                cell["fallback"] = main.fallback_reason
            row[path] = cell
        rows.append(row)
    summary: dict[str, Any] = {}
    for path in PATHS:
        a = agg[path]
        n = len(a["correct"])
        s = {"questions": n, "correct": sum(a["correct"]), "accuracy": _dec(Fraction(sum(a["correct"]), n)),
             "point_coverage": _dec(Fraction(sum(a["covered"]), sum(a["total"]))),
             "consistency_mean": _dec(_mean(a["cons"])),
             "context_tokens_mean": _dec(_mean(a["tok"])), "context_tokens_max": max(a["tok"]),
             "context_chars_mean": _dec(_mean(a["chars"])),
             "node_hit_rate": _dec(Fraction(sum(a["hit"]), len(a["hit"]))) if a["hit"] else None}
        if path == "page_first":
            s["fallback"] = {r: a["fallback"].count(r) for r in REASONS}
        summary[path] = s
    pf, ch = summary["page_first"], summary["chunk"]
    delta = {"accuracy": round(pf["accuracy"] - ch["accuracy"], 4),
             "consistency_mean": round(pf["consistency_mean"] - ch["consistency_mean"], 4),
             "context_tokens_mean_ratio": round(pf["context_tokens_mean"] / ch["context_tokens_mean"], 4) if ch["context_tokens_mean"] else None}
    notices = [SAMPLE_NOTICE] if gold.synthetic else [REAL_NOTICE]
    if len(gold.questions) < MIN_REAL_QUESTIONS:
        notices.append(f"题量 {len(gold.questions)} 少于 {MIN_REAL_QUESTIONS}（spec 待人确认 #8 的默认下限），不足以支撑 M1 结论。")
    return {
        "format": REPORT_FORMAT,
        "synthetic": gold.synthetic,
        "notices": notices,
        "inputs": {"gold_sha256": gold.digest, "questions": len(gold.questions), "map_version": corpus.atoms.map_version,
                   "subject": corpus.atoms.subject, "page_snapshot_commit": corpus.pages.commit if corpus.pages else None,
                   "chunks": len(corpus.chunks)},
        "method": {"judge": judge, "runs": runs, "retrieval_version": RETRIEVAL_VERSION, "tokenizer_version": TOKENIZER_VERSION,
                   "token_estimate": "每个汉字 1、每串连续字母数字 1、其余每个非空白符号 1"},
        "summary": summary,
        "delta_page_first_minus_chunk": delta,
        "questions": rows,
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def render_markdown(rep: Mapping[str, Any]) -> str:
    inp, m, s = rep["inputs"], rep["method"], rep["summary"]
    title = "# 对照评测：页面优先 vs 切块检索" + ("（流程样例）" if rep["synthetic"] else "")
    lines = [title, ""] + [f"> {n}" for n in rep["notices"]] + [""]
    lines += [f"- 金标：{inp['questions']} 题，sha256 {inp['gold_sha256'][:16]}…；合成：{_fmt(rep['synthetic'])}",
              f"- 语料：{inp['subject']} 地图 {inp['map_version']}；页面快照 {_fmt(inp['page_snapshot_commit'])}；切块 {inp['chunks']} 块",
              f"- 方法：判定规则 {m['judge']}；主问题重复 {m['runs']} 次；{m['retrieval_version']}；分词 {m['tokenizer_version']}",
              f"- token 估算：{m['token_estimate']}", "",
              "## 汇总", "", "| 指标 | 页面优先 | 切块检索 |", "|---|---|---|"]
    labels = [("正确率", "accuracy"), ("正确题数", "correct"), ("要点覆盖率", "point_coverage"), ("讲法一致性（平均 Jaccard）", "consistency_mean"),
              ("上下文 token（平均）", "context_tokens_mean"), ("上下文 token（最大）", "context_tokens_max"),
              ("上下文字符（平均）", "context_chars_mean"), ("定位命中率", "node_hit_rate")]
    for label, k in labels:
        lines.append(f"| {label} | {_fmt(s['page_first'][k])} | {_fmt(s['chunk'][k])} |")
    d = rep["delta_page_first_minus_chunk"]
    lines += ["", f"- 页面优先 − 切块：正确率 {d['accuracy']:+.4f}；一致性 {d['consistency_mean']:+.4f}；"
                  f"平均 token 之比 {_fmt(d['context_tokens_mean_ratio'])}",
              "- 页面优先的兜底次数：" + "、".join(f"{k} {v}" for k, v in s["page_first"]["fallback"].items()),
              "- KGE 第九章的口径：正确率不低于切块路径，一致性与成本明显更优。是否达到由人判断。", "",
              "## 逐题（只列题目 ID）", "",
              "| 题目 ID | 页面优先：正确 | 要点 | 一致性 | token | 兜底 | 切块：正确 | 要点 | 一致性 | token |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["questions"]:
        a, b = r["page_first"], r["chunk"]
        lines.append(f"| {r['id']} | {_fmt(a['correct'])} | {a['points_covered']}/{a['points_total']} | {_fmt(a['consistency'])} | "
                     f"{a['context_tokens']} | {_fmt(a['fallback'])} | {_fmt(b['correct'])} | {b['points_covered']}/{b['points_total']} | "
                     f"{_fmt(b['consistency'])} | {b['context_tokens']} |")
    lines.append("")
    return "\n".join(lines)


def write_report(rep: Mapping[str, Any], out: str | Path) -> tuple[Path, Path]:
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    md, js = d / "compare-retrieval.md", d / "compare-retrieval.json"
    md.write_text(render_markdown(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="对照评测：页面优先 vs 切块检索（KGE-03）")
    ap.add_argument("--gold", required=True, help="金标问答 YAML（format: kge-gold-qa/1）")
    ap.add_argument("--map-snapshot", required=True, help="已发布地图版本的快照（knowledge.atoms 格式）")
    ap.add_argument("--page-snapshot", help="已发布页面快照目录；不给则页面优先路径只能走原子与兜底")
    ap.add_argument("--out", required=True, help="报告输出目录")
    ap.add_argument("--judge", default="all_points", help=f"判定规则：{'、'.join(JUDGES)}")
    ap.add_argument("--runs", type=int, default=2, help="主问题重复取回次数（≥ 2）")
    args = ap.parse_args(argv)
    try:
        gold = load_gold(args.gold)
        atoms = load_snapshot(args.map_snapshot)
        pages = load_snapshot_dir(args.page_snapshot) if args.page_snapshot else None
        rep = evaluate(gold, Corpus(pages, atoms), judge=args.judge, runs=args.runs)
        md, js = write_report(rep, args.out)
    except (GoldError, SnapshotError, SnapshotLoadError, OSError, ValueError) as e:
        print(f"对照评测无法运行：{e}", file=sys.stderr)
        return 2
    print(f"已生成：{md.name}、{js.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
