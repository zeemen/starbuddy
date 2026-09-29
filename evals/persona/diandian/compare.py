"""点点跨模型一致性对照运行器（冲刺 2-I，specs/diandian-consistency-r1-sprint2/spec.md 设计要点 1、2）。

读候选清单 → 每个候选只经模型路由调用它自己（为它临时拼一张只含该候选的路由表，任务 T1、数据级别 L0）→
同一套非留出集用例、同一版人设 Prompt 各跑 N 次 → 每次单独评分（复用 scorer）→ 生成首轮差异报告（scorer.compare_report）。

- 候选只能是模型路由配置（services/learning/config/model_routes.yaml）当前环境里已登记的供应商与模型；境外供应商一律拒绝
  （spec 待人确认 #3：不做境外对照）；staging、pilot、prod 还要求供应商在白名单里已批准。
- 运行器不持有任何 Key：test 环境只用进程内假供应商；其他环境由路由已有机制（ModelRouter.from_config，读执行环境的变量）建连。
- 人设 Prompt 从 docs/personas/prompts/diandian-v0.md 读（只读，人设负责人提交）；不存在时只允许 test 环境（假供应商）。
- 回复原文与逐次评分只写到仓库外（--out）；汇总报告（--report-dir）不含原文，仓库内只允许写到 evals/persona/reports/ 下。
- 默认不跑留出集（规则 14）：留出集由教研负责人在封存环境单独跑。

用法（在 evals/persona 目录下）：
  python diandian/compare.py --candidates 清单.yaml [--runs 3] [--env test] [--out 仓库外目录] [--report-dir 报告目录]
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
PERSONA = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "services" / "learning"))
sys.path.insert(0, str(PERSONA))
sys.path.insert(0, str(HERE / "fixtures"))

from learning.model_router import (DEFAULT_PATH, ConfigError, DataClass, FakeProvider, MemorySink, ModelRouter, Residency,  # noqa: E402
                                   RouteRejected, RouteRequest, RouteUnavailable, Task, load, parse)
from learning.model_router.config import SYNTHETIC_ENVS, ENVIRONMENTS  # noqa: E402
from scorer import compare_report  # noqa: E402
from scorer.cases import load_cases, read_holdout  # noqa: E402
from scorer.judge import load_lexicon  # noqa: E402
from scorer.report import score, write as write_run  # noqa: E402

import fake_candidates as fakes  # noqa: E402  测试夹具：只在 test 环境使用

PROMPT_PATH = ROOT / "docs" / "personas" / "prompts" / "diandian-v0.md"
REPORTS_DIR = PERSONA / "reports"
ROUTES_PATH = DEFAULT_PATH
PROMPT_TEMPLATE, PROMPT_VERSION = "diandian", "0"
SUBJECT_CODES = {"aya", "wenli", "zhixing", "echo"}
CANDIDATE_KEYS = {"id", "vendor", "model", "version", "note", "fake_profile"}
_CAND_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
MAX_RUNS = 10
MAX_CANDIDATES = 6


class CompareError(Exception):
    """拒绝运行（退出码 2）：消息只含固定文本、编号与路径，不含凭据与回复"""


@dataclass(frozen=True)
class Candidate:
    id: str
    vendor: str
    model: str
    version: str
    note: str
    fake_profile: tuple[str, Any] | None
    config: Any = field(repr=False)  # 只含该候选的 RouterConfig

    @property
    def model_version(self) -> str:
        return f"model:{self.vendor}/{self.model}@{self.version}"


def _inside(path: Path, base: Path) -> bool:
    path, base = path.resolve(), base.resolve()
    return path == base or base in path.parents


def check_out_dir(out: Path) -> None:
    # 回复原文不入库（沿用 1-I 的做法）：输出目录不能落在仓库里
    if _inside(out, ROOT):
        raise CompareError("--out 不能指向仓库内的目录（回复原文与逐次评分不入库）")


def check_report_dir(report_dir: Path) -> None:
    if _inside(report_dir, ROOT) and not (_inside(report_dir, REPORTS_DIR) and report_dir.resolve() != REPORTS_DIR.resolve()):
        raise CompareError("--report-dir 在仓库内时只能是 evals/persona/reports/ 下的子目录")


def _display(p: Path) -> str:
    return p.resolve().relative_to(ROOT.resolve()).as_posix() if _inside(p, ROOT) else p.name


def load_prompt(env: str) -> tuple[str, dict[str, Any]]:
    """人设 Prompt：文件由人设负责人提交（必停项），这里只读；不存在时只允许 test 环境用测试占位"""
    source_version = f"prompt:{PROMPT_TEMPLATE}@{PROMPT_VERSION}"
    if PROMPT_PATH.is_file():
        text = PROMPT_PATH.read_text(encoding="utf-8")
        return text, {"source": _display(PROMPT_PATH), "source_version": source_version,
                      "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    if env != "test":
        raise CompareError(f"人设 Prompt 文件 {_display(PROMPT_PATH)} 不存在：{env} 环境拒绝运行"
                           "（只有 test 环境可以用假供应商跑流程；Prompt 由人设负责人提交，spec 待人确认 #2）")
    text = fakes.PLACEHOLDER_SYSTEM
    return text, {"source": "测试占位（不是人设 Prompt）", "source_version": source_version,
                  "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def load_candidates(path: Path, env: str, categories: set[str]) -> list[Candidate]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("candidates"), list):
        raise CompareError("候选清单格式：version: 1，candidates: [{id, vendor, model, note}]")
    raw_routes = yaml.safe_load(Path(ROUTES_PATH).read_text(encoding="utf-8"))
    try:
        registry = load(ROUTES_PATH, env)  # 先按路由表自己的规则校验整个环境
    except ConfigError as e:
        raise CompareError(f"模型路由配置在 {env} 环境不可用：{e}") from None
    section = (raw_routes.get("environments") or {}).get(env) or {}
    raw_vendors = section.get("vendors") or {}
    items = doc["candidates"]
    if not 1 <= len(items) <= MAX_CANDIDATES:
        raise CompareError(f"候选数量应在 1–{MAX_CANDIDATES} 之间")
    out: list[Candidate] = []
    seen: set[str] = set()
    for i, raw in enumerate(items):
        where = f"候选第 {i + 1} 项"
        if not isinstance(raw, dict):
            raise CompareError(f"{where}: 必须是映射")
        extra = set(raw) - CANDIDATE_KEYS
        if extra:
            raise CompareError(f"{where}: 未知键 {sorted(extra)}")
        cid, vendor, model = raw.get("id"), raw.get("vendor"), raw.get("model")
        if not (isinstance(cid, str) and _CAND_ID.match(cid)) or cid in seen:
            raise CompareError(f"{where}: id 缺失、重复或不合格式（小写字母、数字、连字符，至多 24 位）")
        seen.add(cid)
        if not isinstance(vendor, str) or not isinstance(model, str):
            raise CompareError(f"{cid}: vendor 与 model 必须是字符串")
        v = registry.vendors.get(vendor)
        if v is None:
            raise CompareError(f"{cid}: 供应商 {vendor!r} 未在模型路由配置的 {env} 环境登记（新增供应商属必停项，由人登记）")
        if v.residency is not Residency.DOMESTIC:
            raise CompareError(f"{cid}: 供应商 {vendor!r} 是境外供应商；首轮只比较境内候选（spec 待人确认 #3）")
        if env not in SYNTHETIC_ENVS and (v.whitelist != "approved" or v.protocol == "fake"):
            raise CompareError(f"{cid}: {env} 环境只能用白名单里已批准的真实供应商")
        # 模型：必须是该供应商在本环境路由里已登记的目标（取其锁定版本、快照名与推理参数）
        targets = [copy.deepcopy(t) for r in (section.get("routes") or {}).values() for t in (r.get("primary"), r.get("backup"))
                   if isinstance(t, dict) and t.get("vendor") == vendor and t.get("model") == model
                   and (raw.get("version") is None or t.get("version") == raw.get("version"))]
        versions = sorted({str(t.get("version")) for t in targets})
        if not targets:
            raise CompareError(f"{cid}: 模型 {vendor}/{model} 未在 {env} 环境的路由里登记")
        if len(versions) > 1:
            raise CompareError(f"{cid}: {vendor}/{model} 登记了多个版本 {versions}，请在候选里写 version")
        profile = None
        if "fake_profile" in raw:
            if env != "test":
                raise CompareError(f"{cid}: fake_profile 只在 test 环境有效")
        if env == "test":
            try:
                profile = fakes.parse_profile(raw.get("fake_profile"), categories)
            except ValueError as e:
                raise CompareError(f"{cid}: {e}") from None
        target = targets[0]
        doc_one = {"version": 1, "defaults": copy.deepcopy(raw_routes.get("defaults") or {}),
                   "environments": {env: {"vendors": {vendor: copy.deepcopy(raw_vendors[vendor])},
                                          "routes": {"T1": {"data_classes": ["L0"], "primary": target}}}}}
        try:
            cfg = parse(doc_one, env)
        except ConfigError as e:
            raise CompareError(f"{cid}: 临时路由表校验失败：{e}") from None
        out.append(Candidate(id=cid, vendor=vendor, model=model, version=str(target["version"]), note=str(raw.get("note") or ""),
                             fake_profile=profile, config=cfg))
    return out


def parse_reply(output: str, reply_format: str) -> tuple[str, dict[str, Any]]:
    """text：整段输出即回复文本、没有结构字段；envelope：{"text": ..., "fields": {...}}，解不开时按 text 处理"""
    if reply_format == "envelope":
        try:
            body = json.loads(output)
            if isinstance(body, dict) and isinstance(body.get("text"), str) and isinstance(body.get("fields", {}), dict):
                return body["text"], dict(body.get("fields") or {})
        except ValueError:
            pass
    return output, {}


def _context_message(case: dict[str, Any]) -> list[dict[str, str]]:
    # 用例上下文（引擎事件、交接老师、复述标记等，全部合成）作为第二条 system 消息；注入格式待与人设 Prompt v0 对齐（lessons 待人确认）
    ctx = case.get("context") or {}
    if not ctx:
        return []
    return [{"role": "system", "content": "用例上下文（合成数据，JSON）：" + json.dumps(ctx, ensure_ascii=False, sort_keys=True)}]


async def run_candidate(cand: Candidate, router: ModelRouter, cases: list[dict[str, Any]], runs: int, system: str,
                        reply_format: str, nonce: str) -> tuple[list[list[dict[str, Any]]], int]:
    """返回（第 k 次的回复列表，调用失败的运行数）。用例之间顺序执行，便于供应商限流与断点排查"""
    per_run: list[list[dict[str, Any]]] = []
    errors = 0
    for run in range(1, runs + 1):
        out: list[dict[str, Any]] = []
        for case in cases:
            subject = (case.get("context") or {}).get("subject")
            messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *_context_message(case)]
            turns: list[dict[str, Any]] = []
            source_version = f"prompt:{PROMPT_TEMPLATE}@{PROMPT_VERSION}"
            error = None
            for t, text in enumerate(case["turns"]):
                messages.append({"role": "user", "content": text})
                key = f"cmp-{nonce}-{cand.id}-{case['case_id']}-r{run}-t{t}"
                try:
                    res = await router.route(RouteRequest(
                        task=Task.T1, data_class=DataClass.L0, subject=subject if subject in SUBJECT_CODES else None,
                        prompt_template=PROMPT_TEMPLATE, prompt_version=PROMPT_VERSION, messages=tuple(messages),
                        usage_id=f"u-{key}", idempotency_key=key))
                except (RouteRejected, RouteUnavailable) as e:
                    error = e.code  # 只记错误类别；缺的轮次由评分器判为 missing_reply（不通过）
                    break
                reply_text, fields = parse_reply(res.output, reply_format)
                messages.append({"role": "assistant", "content": res.output})
                turns.append({"text": reply_text, "fields": fields, "model_version": res.model_version})
                source_version = res.source_version
            errors += error is not None
            versions = sorted({x["model_version"] for x in turns})
            out.append({"case_id": case["case_id"], "run": run, "candidate": cand.id,
                        "model_version": versions[0] if len(versions) == 1 else ("mixed" if versions else None),
                        "prompt_version": PROMPT_VERSION, "source_version": source_version, "error": error, "turns": turns})
        per_run.append(out)
    return per_run, errors


def build_router(cand: Candidate, cases: list[dict[str, Any]], sink: MemorySink, seen: list | None = None) -> ModelRouter:
    cfg = cand.config
    if cfg.env == "test":
        # test 环境：只用进程内假供应商，行为来自测试夹具；不联网、不读任何凭据
        return ModelRouter(cfg, {cand.vendor: FakeProvider(cfg.vendors[cand.vendor], fakes.make_behavior(cand.fake_profile, cases, seen))},
                           sink=sink)
    try:
        # 其他环境：沿用路由已有机制，地址与 Key 从执行环境的变量读取（运行器不持有）
        return ModelRouter.from_config(cfg, sink=sink)
    except ConfigError as e:
        raise CompareError(f"{cand.id}: 供应商连接未就绪（地址或凭据由执行环境提供）：{e}") from None


def compare(candidates_path: Path, *, runs: int = 3, env: str = "test", out_dir: Path, report_dir: Path | None,
            reply_format: str | None = None, seen: list | None = None) -> dict[str, Any]:
    if env not in ENVIRONMENTS:
        raise CompareError(f"未知环境 {env!r}")
    if not 1 <= runs <= MAX_RUNS:
        raise CompareError(f"--runs 应在 1–{MAX_RUNS} 之间")
    check_out_dir(out_dir)
    if report_dir is not None:
        check_report_dir(report_dir)
    system, prompt_meta = load_prompt(env)
    all_cases = load_cases(HERE / "cases")
    holdout = set(read_holdout(HERE / "holdout.txt"))
    # 默认不跑留出集（规则 14）
    cases = [c for c in all_cases if c["case_id"] not in holdout]
    cands = load_candidates(candidates_path, env, {c["category"] for c in all_cases})
    fmt = reply_format or ("envelope" if env == "test" else "text")
    lex = load_lexicon(HERE / "lexicon" / "forbidden.yaml")
    gold_present = (HERE / "gold").is_dir()
    nonce = uuid.uuid4().hex[:8]

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for cand in cands:
        sink = MemorySink()
        router = build_router(cand, cases, sink, seen)

        async def go(c=cand, r=router):
            try:
                return await run_candidate(c, r, cases, runs, system, fmt, nonce)
            finally:
                await r.aclose()

        per_run, errors = asyncio.run(go())
        scored = []
        for k, responses in enumerate(per_run, start=1):
            rep = score(cases, responses, lex, holdout=holdout, gold_present=gold_present)
            d = out_dir / cand.id / f"run-{k}"
            d.mkdir(parents=True, exist_ok=True)
            # 回复原文只写到仓库外（spec 设计要点 1）；用完请删除
            with open(d / "responses.jsonl", "w", encoding="utf-8") as f:
                for r in responses:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            write_run(rep, d)
            scored.append(rep)
        results.append({"id": cand.id, "vendor": cand.vendor, "model": cand.model, "model_version": cand.model_version,
                        "note": cand.note, "fake_profile": None if cand.fake_profile is None else _profile_str(cand.fake_profile),
                        "calls": len(sink.records), "call_errors": errors,
                        "model_mismatch": sum(1 for x in sink.records if x.model_mismatch), "runs": scored})

    meta = {"sample": env == "test", "env": env, "runs_per_candidate": runs, "reply_format": fmt, "prompt": prompt_meta,
            "holdout_excluded": len(holdout), "lexicon_version": lex.version, "lexicon_reviewed": lex.reviewed,
            "gold_present": gold_present, "data_class": "L0", "task": "T1"}
    report = compare_report.build(meta, results)
    if report_dir is not None:
        compare_report.write(report, report_dir)
    return report


def _profile_str(p: tuple[str, Any]) -> str:
    kind, arg = p
    if kind == "fail_category":
        return f"fail_category:{arg}"
    if kind == "random":
        return f"random:{arg[0]:g}:{arg[1]}"
    return kind


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="compare.py", description="点点跨模型一致性对照运行器（首轮）")
    ap.add_argument("--candidates", required=True, help="候选清单 YAML")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--env", default="test", choices=ENVIRONMENTS)
    ap.add_argument("--out", help="回复原文与逐次评分的目录（必须在仓库外；默认新建临时目录）")
    ap.add_argument("--report-dir", help="汇总报告目录（不含原文；仓库内只能是 evals/persona/reports/ 下）")
    ap.add_argument("--reply-format", choices=("text", "envelope"), help="回复解析方式；默认 test 环境 envelope，其他 text")
    a = ap.parse_args(argv)
    out_dir = (Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="diandian-compare."))).resolve()
    try:
        rep = compare(Path(a.candidates), runs=a.runs, env=a.env, out_dir=out_dir,
                      report_dir=Path(a.report_dir).resolve() if a.report_dir else None, reply_format=a.reply_format)
    except CompareError as e:
        print(f"eval-persona compare: 拒绝运行：{e}")
        return 2
    tag = f"（{compare_report.SAMPLE_BANNER}）" if rep["sample"] else ""
    for c in rep["candidates"]:
        print(f"  {c['id']}: 总通过率 {c['overall']['rate']}，核心 {c['core']['rate']}，红线 {c['redline']['count']} 次，"
              f"不稳定 {c['unstable']['ratio']}，调用失败 {c['call_errors']}")
    print(f"eval-persona compare: 完成{tag}；{len(rep['candidates'])} 个候选 × {a.runs} 次 × {rep['cases']} 条用例；"
          f"回复原文在 {out_dir}（仓库外，用完请删除）" + (f"；报告在 {a.report_dir}" if a.report_dir else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
