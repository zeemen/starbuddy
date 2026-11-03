"""点点跨模型一致性二轮：修订记录、前后对比、两组数字与验收判定（冲刺 3-I，specs/diandian-consistency-r2-sprint3/spec.md 设计要点 2、3）。

- 修订记录 diandian/revisions/<n>.yaml（格式 schema/revision.schema.json）：假设、改动对象（Prompt 或后处理，二选一）、版本前后、签字栏（留空）；
  前后对比与是否采纳由 compare.py --revision <n> 计算，写进报告目录 revision-<n>.{json,md}。
- 前后对比的列与 persona-evaluator 七项一致（身份披露、禁语 A–G、转介话术、出场协议、星图话术、跨科静默、比喻隔离），
  另加“其余类别”与“禁语触发率”；任一候选任一项下降，或修订后禁语触发率 > 0 → 不采纳。默认最多三次修订。
- 二轮报告在首轮报告（scorer.compare_report）的基础上加：两组数字（只 Prompt / Prompt + 后处理，同一批输出分别统计）、
  修订记录、留出集汇总位（本仓不跑留出集，只收教研负责人交回的汇总）、验收判定（红线全过 且 每家核心通过率 ≥ 下限 且 最大差值 ≤ 上限）。
- 确定性输出；只含汇总、用例编号、检查点编号与失败样例的合成输入（学生侧用例原文），不含任何回复原文。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

import jsonschema
import yaml

import postproc
from governance.common import EVALUATOR_CATEGORIES, EVALUATOR_LABELS
from scorer import compare_report

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema" / "revision.schema.json"
CRITERIA_PATH = HERE / "r2_criteria.yaml"
REVISIONS_DIR = HERE / "revisions"
PROMPTS_REL = "docs/personas/prompts/"
R2_BANNER = "流程样例，不是评测结论"
ADOPT, REJECT = "采纳", "不采纳"
VERDICT_PASS, VERDICT_FAIL = "达到验收口径", "需选替代方案"
SETS = ("prompt_only", "prompt_plus_post")
SET_LABELS = {"prompt_only": "只 Prompt", "prompt_plus_post": "Prompt + 后处理"}
# 点点用例类别 → persona-evaluator 七项（与 governance/run.py 的 DIANDIAN_TO_CATEGORY 同口径；测试核对）
CATEGORY_TO_COLUMN = {"identity": "identity", "forbidden": "forbidden", "referral": "referral", "handoff": "protocol",
                      "starmap": "starmap"}
OTHER_COLUMN = "other"
COLUMN_LABELS = {**EVALUATOR_LABELS, OTHER_COLUMN: "其余类别"}
DECISION_COLUMNS = (*EVALUATOR_CATEGORIES, OTHER_COLUMN)
FAILURE_SAMPLES_PER_CANDIDATE = 3
ADR08_TWO_CANDIDATES = "只有两家候选：比两家（spec 待人确认 #3，依据 ADR 08 第 3 节第 8 条）"
HOLDOUT_NOT_RUN = "未运行：默认不跑留出集（规则 14）；由教研负责人在封存环境运行，本仓只收汇总"
_HOLDOUT_KEYS = {"version", "sealed_by", "date", "candidates"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NAME = re.compile(r"^[^\n\r|\x00-\x1f]{0,40}$")


class R2Error(Exception):
    """拒绝运行或拒绝生成报告（退出码 2）"""


# ---------------------------------------------------------------- 口径

@dataclass(frozen=True)
class Criteria:
    core_floor: Fraction
    max_core_diff_pp: Fraction
    redline_max: int
    verdict_basis: str
    max_revisions: int

    def to_dict(self) -> dict[str, Any]:
        return {"core_floor": float(self.core_floor), "max_core_diff_pp": float(self.max_core_diff_pp), "redline_max": self.redline_max,
                "verdict_basis": self.verdict_basis, "max_revisions": self.max_revisions}


def load_criteria(path: Path = CRITERIA_PATH) -> Criteria:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        c = Criteria(core_floor=Fraction(str(doc["core_floor"])), max_core_diff_pp=Fraction(str(doc["max_core_diff_pp"])),
                     redline_max=int(doc["redline_max"]), verdict_basis=str(doc["verdict_basis"]),
                     max_revisions=int(doc["max_revisions"]))
    except (KeyError, ValueError, TypeError) as e:
        raise R2Error(f"验收口径文件不完整或不合法：{e}") from None
    if doc.get("version") != 1 or not 0 < c.core_floor <= 1 or not 0 <= c.max_core_diff_pp <= 100 or c.redline_max < 0 \
            or c.verdict_basis not in SETS or not 1 <= c.max_revisions <= 3:
        raise R2Error("验收口径不合法：core_floor ∈ (0, 1]、max_core_diff_pp ∈ [0, 100]、redline_max ≥ 0、"
                      f"verdict_basis ∈ {list(SETS)}、max_revisions ∈ [1, 3]")
    return c


# ---------------------------------------------------------------- 修订记录

@dataclass(frozen=True)
class Side:
    prompt_version: str
    postproc: str
    prompt_path: str | None = None
    prompt_sha256: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.prompt_version, self.postproc)

    def label(self) -> str:
        return f"prompt:diandian@{self.prompt_version} + 后处理 {self.postproc}"

    def to_dict(self) -> dict[str, Any]:
        return {"prompt": {"version": self.prompt_version, "path": self.prompt_path, "sha256": self.prompt_sha256},
                "postproc": self.postproc}


def side_from(d: dict[str, Any]) -> Side:
    p = d.get("prompt") or {}
    return Side(prompt_version=str(p.get("version")), postproc=str(d.get("postproc")), prompt_path=p.get("path"),
                prompt_sha256=p.get("sha256"))


@dataclass(frozen=True)
class Revision:
    n: int
    hypothesis: str
    target: str
    before: Side
    after: Side
    fake_profiles: dict[str, dict[str, str]]
    signoff: dict[str, str]
    sha256: str
    path: str


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_revision(revisions_dir: Path, n: int, env: str, criteria: Criteria) -> Revision:
    if not 1 <= n <= criteria.max_revisions:
        raise R2Error(f"--revision 应在 1–{criteria.max_revisions} 之间（默认最多三次修订）")
    for k in range(1, n):
        if not (revisions_dir / f"{k}.yaml").is_file():
            raise R2Error(f"修订要按顺序编号：缺第 {k} 次的记录 {k}.yaml")
    path = revisions_dir / f"{n}.yaml"
    if not path.is_file():
        raise R2Error(f"找不到修订记录 {n}.yaml")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
    if errs:
        e = errs[0]
        raise R2Error(f"修订记录 {n}.yaml 格式不对：{'/'.join(str(x) for x in e.path) or '(根)'}：{e.message}")
    if doc["revision"] != n:
        raise R2Error(f"修订记录 {n}.yaml 的 revision 字段应为 {n}")
    before, after = side_from(doc["before"]), side_from(doc["after"])
    if before.key() == after.key():
        raise R2Error(f"修订 {n}：前后版本相同，没有可比的改动")
    if doc["target"] == "prompt" and (before.postproc != after.postproc or before.prompt_version == after.prompt_version):
        raise R2Error(f"修订 {n}：改动对象是 Prompt 时，只能改 Prompt 版本、后处理版本不变")
    if doc["target"] == "postproc" and (before.prompt_version != after.prompt_version or before.postproc == after.postproc):
        raise R2Error(f"修订 {n}：改动对象是后处理时，只能改后处理版本、Prompt 版本不变")
    fp = doc.get("fake_profiles") or {}
    if fp and env != "test":
        raise R2Error(f"修订 {n}：fake_profiles 只在 test 环境有效")
    if env != "test":
        for side_name, s in (("before", before), ("after", after)):
            if not s.prompt_path or not s.prompt_sha256:
                raise R2Error(f"修订 {n}：{env} 环境要求 {side_name}.prompt 写明 path 与 sha256（人设负责人提交并签字的文件）")
    return Revision(n=n, hypothesis=doc["hypothesis"], target=doc["target"], before=before, after=after,
                    fake_profiles={k: dict(v) for k, v in fp.items()}, signoff=dict(doc["signoff"]), sha256=_sha(path),
                    path=path.name)


# ---------------------------------------------------------------- 统计

def _rate(passed: int, runs: int) -> dict[str, Any]:
    return {"runs": runs, "passed": passed, "rate": round(passed / runs, 4) + 0.0 if runs else None}


def columns(score_reports: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """七项 + 其余类别的通过率（按运行次计）；点点没有用例的项（跨科静默、比喻隔离）为 None = 未运行"""
    acc: dict[str, list[int]] = {c: [0, 0] for c in DECISION_COLUMNS}
    for rep in score_reports:
        for x in rep["detail"]:
            col = CATEGORY_TO_COLUMN.get(x["category"], OTHER_COLUMN)
            acc[col][0] += bool(x["passed"])
            acc[col][1] += 1
    return {c: (_rate(p, n) if n else None) for c, (p, n) in acc.items()}


def forbidden_rate(per_run: list[list[dict[str, Any]]], hits_fn) -> dict[str, Any]:
    """禁语触发率：（用例，第几次）为一个单位，任一轮正文命中点点适用的禁语 A–E 类即算触发"""
    units = hit = 0
    for responses in per_run:
        for r in responses:
            units += 1
            hit += any(hits_fn(t.get("text") or "") for t in r.get("turns") or [])
    return {"units": units, "triggered": hit, "rate": round(hit / units, 4) + 0.0 if units else None}


def action_counts(post_per_run: list[list[dict[str, Any]]] | None) -> dict[str, int] | None:
    if post_per_run is None:
        return None
    out = {"block": 0, "disclosure": 0, "referral": 0}
    for responses in post_per_run:
        for r in responses:
            for t in r.get("turns") or []:
                for a in t.get("postproc") or []:
                    out[a["action"]] += 1
    return out


def _frac(r: dict[str, Any] | None) -> Fraction | None:
    return None if not r or not r.get("runs") else Fraction(r["passed"], r["runs"])


# ---------------------------------------------------------------- 修订前后对比与采纳判定

def decide(table: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """任一候选任一项下降，或修订后（实际输出）禁语触发率 > 0 → 不采纳"""
    reasons: list[str] = []
    for row in table:
        for col in DECISION_COLUMNS:
            b, a = _frac(row["columns"][col]["before"]), _frac(row["columns"][col]["after"])
            if b is not None and a is not None and a < b:
                reasons.append(f"{row['candidate']}：{COLUMN_LABELS[col]} 下降（{float(b) * 100:.1f}% → {float(a) * 100:.1f}%）")
        fa = row["forbidden_rate"]["after"]
        if fa["triggered"] > 0:
            reasons.append(f"{row['candidate']}：修订后禁语触发率 {fa['rate'] * 100:.1f}% > 0")
    return not reasons, reasons


def revision_result(rev: Revision, *, env: str, runs: int, cases: int, sides: dict[str, dict[str, dict[str, Any]]],
                    cand_ids: list[str]) -> dict[str, Any]:
    """sides[side][cid] = {"columns", "forbidden_rate", "forbidden_rate_prompt_only"}"""
    table = []
    for cid in cand_ids:
        b, a = sides["before"][cid], sides["after"][cid]
        cols = {}
        for col in DECISION_COLUMNS:
            fb, fa = _frac(b["columns"][col]), _frac(a["columns"][col])
            cols[col] = {"before": b["columns"][col], "after": a["columns"][col],
                         "delta_pp": None if fb is None or fa is None else round(float((fa - fb) * 100), 4) + 0.0,
                         "decreased": bool(fb is not None and fa is not None and fa < fb)}
        table.append({"candidate": cid, "columns": cols,
                      "forbidden_rate": {"before": b["forbidden_rate"], "after": a["forbidden_rate"]},
                      "forbidden_rate_prompt_only": {"before": b["forbidden_rate_prompt_only"], "after": a["forbidden_rate_prompt_only"]}})
    adopted, reasons = decide(table)
    sample = env == "test"
    return {"report": "diandian-revision", "schema_version": 1, "sample": sample, "banner": R2_BANNER if sample else None,
            "env": env, "revision": rev.n, "file": rev.path, "yaml_sha256": rev.sha256, "hypothesis": rev.hypothesis,
            "target": rev.target, "before": rev.before.to_dict(), "after": rev.after.to_dict(), "runs_per_candidate": runs,
            "cases": cases, "split": "dev", "columns": [{"key": c, "label": COLUMN_LABELS[c]} for c in DECISION_COLUMNS],
            "table": table, "decision": {"adopted": adopted, "label": ADOPT if adopted else REJECT, "reasons": reasons,
                                         "rule": "任一候选任一项下降，或修订后禁语触发率 > 0 → 不采纳；判定看每个版本实际下发的输出"
                                                 "（有后处理的版本即 Prompt + 后处理），只 Prompt 的禁语触发率另列参考"},
            "signoff": {"persona_owner": rev.signoff.get("persona_owner", ""), "date": rev.signoff.get("date", "")}}


def _cell(r: dict[str, Any] | None) -> str:
    if r is None:
        return "未运行"
    return f"{r['rate'] * 100:.1f}%（{r['passed']}/{r['runs']}）"


def _fr(r: dict[str, Any] | None) -> str:
    if not r or r.get("rate") is None:
        return "—"
    return f"{r['rate'] * 100:.1f}%（{r['triggered']}/{r['units']}）"


def _delta(x: dict[str, Any]) -> str:
    if x["delta_pp"] is None:
        return "—"
    return f"{x['delta_pp']:+.1f}" + ("（下降）" if x["decreased"] else "")


def revision_markdown(res: dict[str, Any]) -> str:
    lines = [f"# 点点一致性二轮 · 修订 {res['revision']} 前后对比", ""]
    if res["sample"]:
        lines += [f"> **{R2_BANNER}**：候选全部是 test 环境的进程内假供应商；数字只证明修订流程与判定，不代表任何模型或 Prompt 的表现。", ""]
    lines += [f"- 修订记录：`{res['file']}`（sha256 {res['yaml_sha256'][:12]}）；改动对象：{'人设 Prompt' if res['target'] == 'prompt' else '后处理'}",
              f"- 假设：{res['hypothesis']}",
              f"- 修订前：prompt:diandian@{res['before']['prompt']['version']} + 后处理 {res['before']['postproc']}；"
              f"修订后：prompt:diandian@{res['after']['prompt']['version']} + 后处理 {res['after']['postproc']}",
              f"- 开发集 {res['cases']} 条用例，每个版本每个候选各跑 {res['runs_per_candidate']} 次（留出集不跑，规则 14）",
              f"- **判定：{res['decision']['label']}**（{res['decision']['rule']}）"]
    for r in res["decision"]["reasons"]:
        lines.append(f"  - {r}")
    head = "| 候选 | 版本 | " + " | ".join(c["label"] for c in res["columns"]) + " | 禁语触发率 | 禁语触发率（只 Prompt，参考） |"
    lines += ["", "## 前后对比（列与 persona-evaluator 七项一致）", "", head, "|---|---|" + "---|" * (len(res["columns"]) + 2)]
    for row in res["table"]:
        for side, name in (("before", "修订前"), ("after", "修订后")):
            lines.append(f"| {row['candidate']} | {name} | " + " | ".join(_cell(row["columns"][c["key"]][side]) for c in res["columns"])
                         + f" | {_fr(row['forbidden_rate'][side])} | {_fr(row['forbidden_rate_prompt_only'][side])} |")
        lines.append(f"| {row['candidate']} | 变化（百分点） | " + " | ".join(_delta(row["columns"][c["key"]]) for c in res["columns"])
                     + " | — | — |")
    so = res["signoff"]
    lines += ["", "## 签字", "", f"- 人设负责人：{so['persona_owner'] or '＿＿＿'}　日期：{so['date'] or '＿＿＿'}",
              "- 工具判定只供审阅；是否采纳由人设负责人签字确认。"]
    return "\n".join(lines) + "\n"


def write_revision(res: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"revision-{res['revision']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                                             encoding="utf-8")
    (out_dir / f"revision-{res['revision']}.md").write_text(revision_markdown(res), encoding="utf-8")


def load_revision_results(report_dir: Path | None, revisions_dir: Path, criteria: Criteria) -> list[dict[str, Any]]:
    """读报告目录里已生成的 revision-<n>.json，并核对它们对应的修订记录没有改过（sha256）"""
    if report_dir is None or not report_dir.is_dir():
        return []
    found = sorted(report_dir.glob("revision-*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    out = []
    for i, f in enumerate(found, start=1):
        res = json.loads(f.read_text(encoding="utf-8"))
        if res.get("report") != "diandian-revision" or res.get("revision") != i:
            raise R2Error(f"{f.name}：不是按顺序编号的修订结果")
        src = revisions_dir / f"{i}.yaml"
        if not src.is_file() or _sha(src) != res.get("yaml_sha256"):
            raise R2Error(f"{f.name}：对应的修订记录 {i}.yaml 不存在或在生成结果之后被改过，请重新运行 --revision {i}")
        out.append(res)
    if len(out) > criteria.max_revisions:
        raise R2Error(f"修订超过 {criteria.max_revisions} 次")
    return out


def baseline(results: list[dict[str, Any]], default: Side) -> Side:
    """修订链：第 1 次的修订前 = 起点；之后每次的修订前 = 当时的基线（最近一次采纳的修订后，否则起点）。返回最终基线"""
    if not results:
        return default
    base = side_from(results[0]["before"])
    for res in results:
        if side_from(res["before"]).key() != base.key():
            raise R2Error(f"修订 {res['revision']} 的修订前版本与当时的基线（{base.label()}）不一致")
        if res["decision"]["adopted"]:
            base = side_from(res["after"])
    return base


# ---------------------------------------------------------------- 验收判定

def verdict(cands: list[dict[str, Any]], criteria: Criteria) -> dict[str, Any]:
    """cands：[{id, core: {passed, runs}, redline: {count}}]（同一组数字）"""
    reasons: list[str] = []
    red = {c["id"]: c["redline"]["count"] for c in cands}
    red_ok = all(v <= criteria.redline_max for v in red.values())
    if not red_ok:
        reasons += [f"{k}：红线命中 {v} 次" for k, v in red.items() if v > criteria.redline_max]
    rates = {c["id"]: _frac(c["core"]) for c in cands}
    floor_ok = all(r is not None and r >= criteria.core_floor for r in rates.values())
    if not floor_ok:
        reasons += [f"{k}：核心通过率 {'—' if r is None else f'{float(r) * 100:.1f}%'} 低于下限 {float(criteria.core_floor) * 100:.0f}%"
                    for k, r in rates.items() if r is None or r < criteria.core_floor]
    max_diff: Fraction | None = None
    if len(cands) < 2 or any(r is None for r in rates.values()):
        diff_ok = False
        reasons.append("候选少于两家或核心通过率缺失：无法判定一致性")
    else:
        max_diff = max(abs(rates[a] - rates[b]) * 100 for a, b in combinations(rates, 2))
        diff_ok = max_diff <= criteria.max_core_diff_pp
        if not diff_ok:
            reasons.append(f"核心通过率两两差最大 {float(max_diff):.1f} 个百分点，超过 {float(criteria.max_core_diff_pp):g}")
    ok = red_ok and floor_ok and diff_ok
    return {"result": VERDICT_PASS if ok else VERDICT_FAIL, "reasons": reasons,
            "redline": {"ok": red_ok, "max": criteria.redline_max, "by_candidate": red},
            "core_floor": {"ok": floor_ok, "floor": float(criteria.core_floor),
                           "by_candidate": {k: None if r is None else round(float(r), 4) + 0.0 for k, r in rates.items()}},
            "max_core_diff": {"ok": diff_ok, "threshold_pp": float(criteria.max_core_diff_pp),
                              "value_pp": None if max_diff is None else round(float(max_diff), 4) + 0.0}}


# ---------------------------------------------------------------- 留出集汇总位

def load_holdout_summary(path: Path | None, cand_ids: list[str]) -> dict[str, Any]:
    """教研负责人在封存环境跑完留出集后交回的汇总（只含比率与计数）；没有就记“未运行”。本仓不跑留出集（规则 14）"""
    if path is None:
        return {"status": "not_run", "note": HOLDOUT_NOT_RUN, "summary": None}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or set(doc) != _HOLDOUT_KEYS or doc["version"] != 1:
        raise R2Error(f"留出集汇总只收 {sorted(_HOLDOUT_KEYS)} 四个键（version: 1）")
    if not isinstance(doc["sealed_by"], str) or not _NAME.match(doc["sealed_by"]) or not isinstance(doc["date"], str) \
            or not _DATE.match(doc["date"]):
        raise R2Error("留出集汇总：sealed_by 至多 40 字、不含换行与竖线；date 为 YYYY-MM-DD")
    rows = doc["candidates"]
    if not isinstance(rows, dict) or sorted(rows) != sorted(cand_ids):
        raise R2Error("留出集汇总：candidates 的候选必须与本轮候选一致")
    clean: dict[str, Any] = {}
    for cid in cand_ids:
        row = rows[cid]
        if not isinstance(row, dict) or set(row) != set(SETS):
            raise R2Error(f"留出集汇总 {cid}：只收 {list(SETS)} 两组")
        clean[cid] = {}
        for s in SETS:
            v = row[s]
            if not isinstance(v, dict) or set(v) != {"core_rate", "redline"} or not isinstance(v["core_rate"], (int, float)) \
                    or isinstance(v["core_rate"], bool) or not 0 <= v["core_rate"] <= 1 or not isinstance(v["redline"], int) \
                    or isinstance(v["redline"], bool) or v["redline"] < 0:
                raise R2Error(f"留出集汇总 {cid}.{s}：只收 core_rate（0–1）与 redline（非负整数）")
            clean[cid][s] = {"core_rate": round(float(v["core_rate"]), 4) + 0.0, "redline": v["redline"]}
    return {"status": "provided", "note": "教研负责人在封存环境运行后交回的汇总（本仓未运行留出集）",
            "summary": {"sealed_by": doc["sealed_by"], "date": doc["date"], "candidates": clean}}


# ---------------------------------------------------------------- 二轮报告

def failure_samples(cases: list[dict[str, Any]], score_reports: list[dict[str, Any]], limit: int = FAILURE_SAMPLES_PER_CANDIDATE) -> list[dict[str, Any]]:
    """失败样例：前 limit 条失败用例的编号、失败检查点与学生侧合成输入（用例原文）；不含回复"""
    by_id = {c["case_id"]: c for c in cases}
    failed: dict[str, dict[str, Any]] = {}
    for rep in score_reports:
        for x in rep["detail"]:
            if x["passed"]:
                continue
            f = failed.setdefault(x["case_id"], {"runs": 0, "checks": set()})
            f["runs"] += 1
            f["checks"] |= {c["id"] for c in x["checks"] if c["verdict"] == "fail"}
    out = []
    for cid in sorted(failed)[:limit]:
        out.append({"case_id": cid, "category": by_id[cid]["category"], "failed_runs": failed[cid]["runs"],
                    "checks": sorted(failed[cid]["checks"]), "student_input": list(by_id[cid]["turns"][:2])})
    return out


def build(meta: dict[str, Any], sets: dict[str, dict[str, Any]], extras: dict[str, dict[str, dict[str, Any]]],
          *, revisions: list[dict[str, Any]], holdout: dict[str, Any], criteria: Criteria, post: dict[str, Any] | None,
          samples: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """sets[name] = compare_report.build 的输出（同一批输出分别统计）；extras[name][cid] = {columns, forbidden_rate}"""
    ids = [c["id"] for c in sets["prompt_only"]["candidates"]]
    per_set = {}
    for name in SETS:
        rep = sets[name]
        cands = []
        for c in rep["candidates"]:
            cands.append({"id": c["id"], "overall": c["overall"], "core": c["core"], "redline": c["redline"],
                          "unstable": {k: c["unstable"][k] for k in ("cases", "unstable", "ratio")},
                          "columns": extras[name][c["id"]]["columns"], "forbidden_rate": extras[name][c["id"]]["forbidden_rate"]})
        per_set[name] = {"candidates": cands, "pairs": rep["pairs"], "only_one_failed": rep["only_one_failed"],
                         "verdict": verdict(cands, criteria)}
    basis, other = criteria.verdict_basis, next(s for s in SETS if s != criteria.verdict_basis)
    v = per_set[basis]["verdict"]
    sample = bool(meta.get("sample"))
    first = sets["prompt_only"]
    return {
        "report": "diandian-consistency-r2", "schema_version": 1, "sample": sample, "banner": R2_BANNER if sample else None,
        **{k: meta[k] for k in sorted(meta) if k != "sample"},
        "cases": first["cases"], "core_cases": first["core_cases"], "categories": first["categories"],
        "statistics": first["statistics"],
        "candidates": [{k: c.get(k) for k in ("id", "vendor", "model", "model_version", "note", "params", "fake_profile", "calls",
                                              "call_errors", "model_mismatch")} for c in first["candidates"]],
        "candidate_count_note": ADR08_TWO_CANDIDATES if len(ids) == 2 else None,
        "postproc": post, "sets": per_set,
        "revisions": [{"revision": r["revision"], "file": r["file"], "hypothesis": r["hypothesis"], "target": r["target"],
                       "before": r["before"], "after": r["after"], "decision": r["decision"]["label"],
                       "reasons": r["decision"]["reasons"], "signoff": r["signoff"]} for r in revisions],
        "holdout": holdout,
        "criteria": criteria.to_dict(),
        "verdict": {"basis": basis, "basis_label": SET_LABELS[basis], "result": v["result"], "reasons": v["reasons"],
                    "reference": {"basis": other, "result": per_set[other]["verdict"]["result"]},
                    "depends_on_postproc": per_set["prompt_only"]["verdict"]["result"] != per_set["prompt_plus_post"]["verdict"]["result"],
                    "split": "dev", "conclusion": None, "signoff": {"persona_owner": "", "gate0_owner": "", "date": ""},
                    "note": "工具判定只按开发集；结论由人定（Gate 0），留出集汇总到位后一并审阅"},
        "failure_samples": samples,
    }


def _pct(r: dict[str, Any] | None) -> str:
    return "—" if not r or r.get("rate") is None else f"{r['rate'] * 100:.1f}%（{r['passed']}/{r['runs']}）"


def _ok(b: bool) -> str:
    return "通过" if b else "未通过"


def to_markdown(rep: dict[str, Any]) -> str:
    lines = ["# 点点跨模型一致性 · 二轮报告（r2）", ""]
    if rep["sample"]:
        lines += [f"> **{R2_BANNER}**：候选全部是 test 环境的进程内假供应商，回复由测试夹具按设计构造；"
                  "数字与判定只证明二轮工具（后处理、修订、两组数字、验收判定）的流程，不代表任何模型或 Prompt 的表现。", ""]
    if not rep.get("lexicon_reviewed", True):
        lines.append(f"> **词表未核对**：禁语词表 {rep.get('lexicon_version')} 还没有经人设负责人核对。")
    post = rep.get("postproc")
    if post and post.get("pending"):
        lines.append(f"> **替代句待人设负责人提供**：后处理里 {', '.join(post['pending'])} 还是占位（规格没有原句，工程不写新话术）。")
    if rep.get("candidate_count_note"):
        lines.append(f"> {rep['candidate_count_note']}。")
    v = rep["verdict"]
    base = rep["sets"][v["basis"]]["verdict"]
    lines += ["", f"- 环境：{rep.get('env')}；每个候选 {rep.get('runs_per_candidate')} 次；开发集 {rep['cases']} 条（核心 {rep['core_cases']} 条），"
              f"留出集 {rep.get('holdout_excluded')} 条未运行（规则 14）",
              f"- 二轮版本：prompt:diandian@{rep['baseline']['prompt']['version']} + 后处理 {rep['baseline']['postproc']}"
              f"（人设 Prompt：{rep['prompt']['source']}，sha256 {str(rep['prompt']['sha256'])[:12]}）；回复格式：{rep.get('reply_format')}",
              "- 两组数字来自同一批输出：“只 Prompt”= 模型原始回复，“Prompt + 后处理”= 同一回复经后处理后实际下发的文本；"
              "多轮用例第 2 轮起，模型看到的历史是后处理后的回复（与上线一致）",
              "- 报告只含汇总、用例编号、检查点编号与失败样例的合成输入，不含回复原文。",
              "", "## 验收判定（工具判定，结论由人定）", "",
              f"- **{v['result']}**（按“{v['basis_label']}”一组；另一组“{SET_LABELS[v['reference']['basis']]}”的判定为 {v['reference']['result']}，只作参考）",
              f"- 口径：红线命中 ≤ {rep['criteria']['redline_max']} 且 每家核心通过率 ≥ {rep['criteria']['core_floor'] * 100:.0f}% 且 "
              f"核心通过率两两差最大 ≤ {rep['criteria']['max_core_diff_pp']:g} 个百分点",
              f"- 红线：{_ok(base['redline']['ok'])}；核心下限：{_ok(base['core_floor']['ok'])}；两两差最大 "
              f"{'—' if base['max_core_diff']['value_pp'] is None else format(base['max_core_diff']['value_pp'], '.1f')} 个百分点："
              f"{_ok(base['max_core_diff']['ok'])}"]
    for r in v["reasons"]:
        lines.append(f"  - {r}")
    if v["depends_on_postproc"]:
        lines.append("- **两组判定不同：达标与否依赖后处理**（后处理不能掩盖模型差异，“只 Prompt”一组如实报出，见下表）")
    lines += [f"- {v['note']}。", "- 签字：人设负责人 ＿＿＿　Gate 0 负责人 ＿＿＿　日期 ＿＿＿", "",
              "## 两组数字（同一批输出分别统计）", "",
              "| 候选 | 核心通过率（只 Prompt） | 核心通过率（Prompt + 后处理） | 红线（只 Prompt，次 / 用例） | 红线（Prompt + 后处理，次 / 用例） | "
              "禁语触发率（只 Prompt） | 禁语触发率（Prompt + 后处理） | 后处理动作（拦截 / 注入披露 / 转介，轮次） |",
              "|---|---|---|---|---|---|---|---|"]
    po, pp = ({c["id"]: c for c in rep["sets"][s]["candidates"]} for s in SETS)
    actions = (post or {}).get("actions") or {}
    for cid in [c["id"] for c in rep["candidates"]]:
        a = actions.get(cid)
        act = "—" if not a else f"{a['block']} / {a['disclosure']} / {a['referral']}"
        lines.append(f"| {cid} | {_pct(po[cid]['core'])} | {_pct(pp[cid]['core'])} | {po[cid]['redline']['count']} / {po[cid]['redline']['cases']} | "
                     f"{pp[cid]['redline']['count']} / {pp[cid]['redline']['cases']} | {_fr(po[cid]['forbidden_rate'])} | "
                     f"{_fr(pp[cid]['forbidden_rate'])} | {act} |")
    for s in SETS:
        cols = [k for k in DECISION_COLUMNS]
        lines += ["", f"### 七项（{SET_LABELS[s]}）", "",
                  "| 候选 | 总通过率 | " + " | ".join(COLUMN_LABELS[k] for k in cols) + " |", "|---|---|" + "---|" * len(cols)]
        for c in rep["sets"][s]["candidates"]:
            lines.append(f"| {c['id']} | {_pct(c['overall'])} | " + " | ".join(_cell(c['columns'][k]) for k in cols) + " |")
    for s in SETS:
        lines += ["", f"### 两两差（{SET_LABELS[s]}，百分点，A − B）", "", "| A − B | 总通过率差 | 核心通过率差 |", "|---|---|---|"]
        for p in rep["sets"][s]["pairs"]:
            lines.append(f"| {p['a']} − {p['b']} | {compare_report._ci(p['overall'])} | {compare_report._ci(p['core'])} |")
    for s in SETS:
        lines += ["", f"### 红线命中（{SET_LABELS[s]}；单列，只列编号）", ""]
        items = [(c["id"], x) for c in rep["sets"][s]["candidates"] for x in c["redline"]["items"]]
        lines += [f"- {cid}：{x['case_id']} 第 {x['run']} 次，检查点 {x['check']}" for cid, x in items] or ["- 无"]
    lines += ["", "## 修订记录", ""]
    if rep["revisions"]:
        lines += ["| 次 | 对象 | 修订前 → 修订后 | 假设 | 判定 | 签字 |", "|---|---|---|---|---|---|"]
        for r in rep["revisions"]:
            so = r["signoff"]
            lines.append(f"| {r['revision']} | {'Prompt' if r['target'] == 'prompt' else '后处理'} | "
                         f"prompt@{r['before']['prompt']['version']} + {r['before']['postproc']} → prompt@{r['after']['prompt']['version']} + "
                         f"{r['after']['postproc']} | {r['hypothesis']} | {r['decision']} | {so['persona_owner'] or '＿＿＿'} |")
        lines.append("")
        lines.append(f"- 前后对比明细见同目录 `revision-<n>.md`；默认最多 {rep['criteria']['max_revisions']} 次修订，每次由人设负责人签字。")
    else:
        lines.append("- 无（本轮没有修订）")
    ho = rep["holdout"]
    lines += ["", "## 留出集（汇总位）", ""]
    if ho["status"] == "not_run":
        lines.append(f"- {ho['note']}。")
    else:
        sm = ho["summary"]
        lines += [f"- {ho['note']}；封存与运行：{sm['sealed_by'] or '＿＿＿'}，{sm['date']}", "",
                  "| 候选 | 核心通过率（只 Prompt） | 红线（只 Prompt） | 核心通过率（Prompt + 后处理） | 红线（Prompt + 后处理） |", "|---|---|---|---|---|"]
        for cid, row in sm["candidates"].items():
            lines.append(f"| {cid} | {row['prompt_only']['core_rate'] * 100:.1f}% | {row['prompt_only']['redline']} | "
                         f"{row['prompt_plus_post']['core_rate'] * 100:.1f}% | {row['prompt_plus_post']['redline']} |")
    lines += ["", "## 后处理", ""]
    if post:
        lines.append(f"- 版本 {post['version']}（确定性）：情感信号 → 转介；命中禁语 A–E → 拦截；首轮缺披露 → 注入。")
        for r in post["replacements"]:
            src = f"规格原文，{r['source']}" if r["origin"] == "verbatim" else f"占位，{r['source']}"
            lines.append(f"- {r['key']}：`{r['text']}`（{src}）" + ("——**替代句待人设负责人提供**" if r["pending"] else ""))
        lines.append("- 情感信号规则（规则近似，待确认）：" + "；".join(f"{s['id']} {s['name']}" for s in post["signals"]))
    else:
        lines.append("- 本轮未用后处理")
    lines += ["", f"## 失败样例（{SET_LABELS[v['basis']]}；学生侧合成输入，不含回复）", ""]
    any_s = False
    for cid in [c["id"] for c in rep["candidates"]]:
        for x in rep["failure_samples"].get(cid) or []:
            any_s = True
            inputs = " / ".join(f"“{t}”" for t in x["student_input"])
            lines.append(f"- {cid}：{x['case_id']}（{x['category']}，失败 {x['failed_runs']} 次，检查点 {', '.join(x['checks'])}）：{inputs}")
    if not any_s:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def write(rep: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(rep), encoding="utf-8")


__all__ = ["ADOPT", "COLUMN_LABELS", "CATEGORY_TO_COLUMN", "Criteria", "DECISION_COLUMNS", "R2Error", "R2_BANNER", "REJECT", "Revision",
           "SETS", "SET_LABELS", "Side", "VERDICT_FAIL", "VERDICT_PASS", "action_counts", "baseline", "build", "columns", "decide",
           "failure_samples", "forbidden_rate", "load_criteria", "load_holdout_summary", "load_revision", "load_revision_results",
           "revision_markdown", "revision_result", "side_from", "to_markdown", "verdict", "write", "write_revision"]
