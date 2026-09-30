"""点点跨模型一致性对照运行器（冲刺 2-I，specs/diandian-consistency-r1-sprint2/spec.md 设计要点 1、2）。

读候选清单 → 每个候选只经模型路由调用它自己（为它临时拼一张只含该候选的路由表，任务 T1、数据级别 L0）→
同一套非留出集用例、同一版人设 Prompt 各跑 N 次 → 每次单独评分（复用 scorer）→ 生成首轮差异报告（scorer.compare_report）。

- 环境只收 test、dev、staging（契约审查：真实首轮只在 staging 或 dev）；pilot、prod 一律拒绝。
- 候选只能是模型路由配置（services/learning/config/model_routes.yaml）当前环境 T1（教学对话）路由里已登记的供应商与模型，
  推理参数只取该 T1 路由目标的 params（契约审查）；境外供应商一律拒绝（spec 待人确认 #3：不做境外对照）；staging 还要求供应商在白名单里已批准。
- 非 test 环境：供应商必须登记了非空 allowed_hosts；运行前核对执行环境给的地址——主机名在 allowed_hosts 里，dev 以外必须是 https（安全审查 S1）。
- 运行器不持有任何 Key：test 环境只用进程内假供应商；其他环境由路由已有机制（ModelRouter.from_config，读执行环境的变量）建连。
- 人设 Prompt 从 docs/personas/prompts/diandian-v0.md 读（只读，人设负责人提交）；不存在时只允许 test 环境（假供应商）。
- 回复原文、逐次评分与逐次调用元数据（calls.jsonl）只写到仓库外（--out，目录 0700、文件 0600，安全审查 S4）；
  汇总报告（--report-dir）不含原文，仓库内只允许写到 evals/persona/reports/ 下。
- 默认不跑留出集（规则 14）：留出集由教研负责人在封存环境单独跑；运行前核对留出集名单非空且条数等于冻结数（安全审查 S5）。
- 运行前打印预计调用次数，超过 --max-calls 拒绝；非 test 环境必须显式传 --max-calls（安全审查 N1）。

冲刺 3-I（specs/diandian-consistency-r2-sprint3/spec.md 设计要点 1–3）：
- --revision <n>：按修订记录 diandian/revisions/<n>.yaml 的修订前后两个版本（Prompt 版本 × 后处理版本）各跑 N 次，输出前后对比与是否采纳（r2report）；
- --r2：用修订链最终采纳的版本跑开发集；每轮回复经后处理 postproc，原始回复与后处理后回复分别评分（两组数字，同一次调用），给出验收判定。

用法（在 evals/persona 目录下）：
  python diandian/compare.py --candidates 清单.yaml [--runs 3] [--env test] [--max-calls N] [--out 仓库外目录] [--report-dir 报告目录]
  python diandian/compare.py --candidates 清单.yaml --revision <n> [--revisions-dir 目录] --out 仓库外目录 --report-dir reports/r2-<日期>
  python diandian/compare.py --candidates 清单.yaml --r2 [--revisions-dir 目录] [--holdout-summary 汇总.json] --out 仓库外目录 --report-dir reports/r2-<日期>
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

HERE = Path(__file__).resolve().parent
PERSONA = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "services" / "learning"))
sys.path.insert(0, str(PERSONA))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "fixtures"))

from learning.config import SUBJECTS  # noqa: E402  平台学科编码（与路由的 subject 校验同源）
from learning.model_router import (DEFAULT_PATH, ConfigError, DataClass, FakeProvider, ModelRouter, Residency,  # noqa: E402
                                   RouteRejected, RouteRequest, RouteUnavailable, Task, load, parse)
from learning.model_router.ledger import CallRecord  # noqa: E402
from learning.model_router.config import SYNTHETIC_ENVS, ENVIRONMENTS  # noqa: E402
from scorer import compare_report  # noqa: E402
from scorer.cases import load_cases, read_holdout  # noqa: E402
from scorer.judge import load_lexicon  # noqa: E402
from scorer.report import score, write as write_run  # noqa: E402

import fake_candidates as fakes  # noqa: E402  测试夹具：只在 test 环境使用
import postproc  # noqa: E402  冲刺 3-I 后处理 v0
import r2report  # noqa: E402  冲刺 3-I 修订记录、两组数字与二轮报告

PROMPT_PATH = ROOT / "docs" / "personas" / "prompts" / "diandian-v0.md"
REPORTS_DIR = PERSONA / "reports"
ROUTES_PATH = DEFAULT_PATH
HOLDOUT_PATH = HERE / "holdout.txt"
PROMPT_TEMPLATE, PROMPT_VERSION = "diandian", "0"
SUBJECT_CODES = frozenset(SUBJECTS)  # 直接取 services/learning/learning/config.py 的 SUBJECTS，不另维护一份
CANDIDATE_KEYS = {"id", "vendor", "model", "version", "note", "fake_profile"}
_CAND_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
MAX_RUNS = 10
MAX_CANDIDATES = 6
# 契约审查：真实首轮只在 staging 或 dev；pilot、prod 不跑评测（test 是假供应商流程样例）
RUN_ENVS = ("test", "dev", "staging")
# 契约审查：候选只接受该环境 T1（教学对话）路由里登记的模型，推理参数也只取 T1 路由目标的 params
CANDIDATE_TASK = Task.T1
# 安全审查 S3：note 会进报告（JSON 与 Markdown）。限长、拒绝换行、竖线与控制字符（不做静默清洗：改写后的说明
# 与执行人写的不一致，也可能掩盖注入尝试；拒绝能让执行人当场改清单）
MAX_NOTE_CHARS = 80
_NOTE_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f|\u0085\u2028\u2029]")
# 安全审查 S5：留出集冻结条数。代码里原本没有冻结数：以冲刺 1-I 由 `python -m scorer holdout --write` 首次生成、
# 现已提交的 diandian/holdout.txt 的条数为准（120 条按类分层抽 30% = 34 条；
# specs/diandian-consistency-r1-sprint2/lessons.md“86 条非留出、34 条留出”）。教研负责人重新封存时由人同步修改
HOLDOUT_FROZEN_COUNT = 34
# 安全审查 N1：调用上限默认值（保守）；流程样例 3 候选 × 3 次 × 150 轮 = 1350 次
DEFAULT_MAX_CALLS = 3000
HARD_MAX_CALLS = 100_000


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
    config: Any = field(repr=False)  # 只含该候选的 RouterConfig（T1 × L0，一个目标）

    @property
    def params(self) -> dict[str, Any]:
        """实际发给供应商的推理参数：取自该候选在 T1 路由里登记的目标"""
        return dict(self.config.routes[CANDIDATE_TASK].primary.params)

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


def check_holdout(ids: list[str], all_ids: set[str]) -> None:
    """安全审查 S5：留出集名单非空、无重复、条数等于冻结数、都是现有用例；不一致就拒绝（规则 14：不能因名单丢失而跑到留出集）"""
    if not ids:
        raise CompareError("留出集名单为空：拒绝运行（名单丢失时会把留出集用例当开发集跑，规则 14）")
    if len(ids) != len(set(ids)) or len(ids) != HOLDOUT_FROZEN_COUNT:
        raise CompareError(f"留出集名单有 {len(ids)} 条（去重 {len(set(ids))} 条），与冻结时的 {HOLDOUT_FROZEN_COUNT} 条不一致：拒绝运行"
                           "（重新封存由教研负责人做，规则 14）")
    if not set(ids) <= all_ids:
        raise CompareError("留出集名单里有不存在的用例编号：拒绝运行")


def check_note(where: str, note: Any) -> str:
    """安全审查 S3：note 限长、不含换行、竖线与控制字符；不合格就拒绝（不静默清洗）"""
    if note is None:
        return ""
    if not isinstance(note, str):
        raise CompareError(f"{where}: note 必须是字符串")
    if len(note) > MAX_NOTE_CHARS:
        raise CompareError(f"{where}: note 超过 {MAX_NOTE_CHARS} 字")
    if _NOTE_FORBIDDEN.search(note):
        raise CompareError(f"{where}: note 不能含换行、竖线 | 或控制字符")
    return note


def check_endpoints(cands: list["Candidate"], env: str, environ: Any = None) -> None:
    """安全审查 S1，运行前核对（test 环境是进程内假供应商，不核对）：地址来自执行环境变量；主机名必须在登记的 allowed_hosts 里；
    dev 以外必须是 https。错误信息只带环境变量名与候选编号，不带地址本身。路由建连时还会再核一次（providers.check_endpoint）"""
    if env == "test":
        return
    source = os.environ if environ is None else environ
    for cand in cands:
        v = cand.config.vendors[cand.vendor]
        url = source.get(v.base_url_env, "")
        if not url:
            raise CompareError(f"{cand.id}: 执行环境没有提供供应商地址（环境变量 {v.base_url_env}）")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise CompareError(f"{cand.id}: 环境变量 {v.base_url_env} 不是合法地址（运行前核对）")
        if env != "dev" and parts.scheme != "https":
            raise CompareError(f"{cand.id}: {env} 环境要求供应商地址是 https（运行前核对）")
        if not v.allowed_hosts or parts.hostname.lower() not in v.allowed_hosts:
            raise CompareError(f"{cand.id}: 地址的主机名不在登记的 allowed_hosts 里（运行前核对）")


def estimate_calls(cands: list["Candidate"], cases: list[dict[str, Any]], runs: int) -> tuple[int, int, int]:
    """（预计调用次数，每次调用最多重试次数，含重试的最坏请求次数）。一次调用 = 一个候选、一次运行、一条用例的一轮"""
    turns = sum(len(c["turns"]) for c in cases)
    planned = len(cands) * runs * turns
    retries = max((c.config.routes[CANDIDATE_TASK].max_retries for c in cands), default=0)
    return planned, retries, planned * (1 + retries)


def check_max_calls(planned: int, env: str, max_calls: int | None) -> int:
    """安全审查 N1：非 test 环境必须显式给 --max-calls（选它而不是 --yes：执行人要写出一个具体预算，比“确认”更可核对）"""
    if env != "test" and max_calls is None:
        raise CompareError(f"{env} 环境必须显式传 --max-calls（调用预算由人定；预计 {planned} 次）")
    limit = DEFAULT_MAX_CALLS if max_calls is None else max_calls
    if not 1 <= limit <= HARD_MAX_CALLS:
        raise CompareError(f"--max-calls 应在 1–{HARD_MAX_CALLS} 之间")
    if planned > limit:
        raise CompareError(f"预计调用 {planned} 次，超过上限 --max-calls {limit}：拒绝运行")
    return limit


def prepare_out_dir(out: Path) -> None:
    """安全审查 S4：--out 目录 0700。已存在时必须是当前用户的空目录（不去改动已有内容或别人目录的权限）"""
    if out.is_symlink():
        raise CompareError("--out 不能是符号链接")
    if out.exists():
        if not out.is_dir():
            raise CompareError("--out 已存在且不是目录")
        if any(out.iterdir()):
            raise CompareError("--out 已存在且不是空目录（请给一个新目录，用完删除）")
        if hasattr(os, "getuid") and out.stat().st_uid != os.getuid():
            raise CompareError("--out 不属于当前用户")
    else:
        out.mkdir(parents=True, mode=0o700)
    os.chmod(out, 0o700)


def lock_down(out: Path) -> None:
    """--out 下目录 0700、文件 0600（写的时候已在 umask 077 下，这里再收紧一遍）"""
    for d, _dirs, files in os.walk(out):
        os.chmod(d, 0o700)
        for n in files:
            f = Path(d) / n
            if not f.is_symlink():
                os.chmod(f, 0o600)


@contextmanager
def private_umask():
    old = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(old)


def _open_private(path: Path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


class CallLog:
    """契约审查：逐次调用的元数据写到 --out/calls.jsonl（0600），不含提示词与回复原文。

    作为路由的 sink 接收 CallRecord（耗时、token 计数、尝试），运行器在每次调用后补上候选、用例、第几次、轮次写一行。
    调用是顺序的，所以“最近一条记录”就是这次调用的记录。
    """

    FIELDS = ("attempts", "candidate", "case_id", "elapsed_ms", "error", "input_tokens", "model_mismatch", "model_version",
              "output_tokens", "run", "status", "turn")

    def __init__(self, path: Path) -> None:
        self._f = _open_private(path)
        self._pending: CallRecord | None = None
        self.calls: dict[str, int] = {}
        self.mismatch: dict[str, int] = {}

    def write(self, record: CallRecord) -> None:  # 路由 LedgerSink 接口
        self._pending = record

    def emit(self, *, candidate: str, case_id: str, run: int, turn: int, error: str | None) -> None:
        rec, self._pending = self._pending, None
        line = {"candidate": candidate, "case_id": case_id, "run": run, "turn": turn,
                "status": rec.outcome if rec else "no_record", "error": error,
                "elapsed_ms": rec.elapsed_ms if rec else None,
                "input_tokens": rec.input_tokens if rec else 0, "output_tokens": rec.output_tokens if rec else 0,
                "model_version": rec.model_version if rec else None, "model_mismatch": bool(rec and rec.model_mismatch),
                "attempts": [{"vendor": a.vendor, "model_version": a.model_version, "status": a.status, "elapsed_ms": a.elapsed_ms}
                             for a in (rec.attempts if rec else ())]}
        self._f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        self._f.flush()
        self.calls[candidate] = self.calls.get(candidate, 0) + 1
        self.mismatch[candidate] = self.mismatch.get(candidate, 0) + int(bool(rec and rec.model_mismatch))

    def close(self) -> None:
        self._f.close()


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
        if env != "test" and not v.allowed_hosts:
            # 安全审查 S1：地址只能指向登记过的主机；test 是进程内假供应商，不建连
            raise CompareError(f"{cid}: 供应商 {vendor!r} 在 {env} 环境没有登记 allowed_hosts（非 test 环境必填）")
        note = check_note(cid, raw.get("note"))
        # 模型：必须是该供应商在本环境 T1（教学对话）路由里已登记的目标（取其锁定版本、快照名与推理参数）
        routes = section.get("routes") or {}
        t1 = routes.get(CANDIDATE_TASK.value) or {}

        def same(t: Any, vendor: str = vendor, model: str = model, raw: dict = raw) -> bool:
            return (isinstance(t, dict) and t.get("vendor") == vendor and t.get("model") == model
                    and (raw.get("version") is None or t.get("version") == raw.get("version")))

        targets = [copy.deepcopy(t) for t in (t1.get("primary"), t1.get("backup")) if same(t)]
        versions = sorted({str(t.get("version")) for t in targets})
        if not targets:
            elsewhere = sorted(k for k, r in routes.items() if isinstance(r, dict) and any(same(t) for t in (r.get("primary"), r.get("backup"))))
            if elsewhere:
                raise CompareError(f"{cid}: 模型 {vendor}/{model} 只登记在 {elsewhere} 路由；"
                                   f"候选只接受 {env} 环境 T1（教学对话）路由里登记的模型")
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
                                          "routes": {CANDIDATE_TASK.value: {"data_classes": ["L0"], "primary": target}}}}}
        try:
            cfg = parse(doc_one, env)
        except ConfigError as e:
            raise CompareError(f"{cid}: 临时路由表校验失败：{e}") from None
        out.append(Candidate(id=cid, vendor=vendor, model=model, version=str(target["version"]), note=note,
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


def _assistant_message(text: str, fields: dict[str, Any], reply_format: str) -> str:
    """后处理后的回复按原格式写回对话历史（与上线一致：模型下一轮看到的是学生实际看到的回复）"""
    if reply_format == "envelope":
        return json.dumps({"text": text, "fields": fields}, ensure_ascii=False, sort_keys=True)
    return text


async def run_candidate(cand: Candidate, router: ModelRouter, cases: list[dict[str, Any]], runs: int, system: str,
                        reply_format: str, nonce: str, log: CallLog, *, post: "postproc.PostProcessor | None" = None,
                        prompt_version: str = PROMPT_VERSION, post_runs: list | None = None) -> tuple[list[list[dict[str, Any]]], int]:
    """返回（第 k 次的回复列表，调用失败的运行数）。用例之间顺序执行，便于供应商限流与断点排查。

    post（冲刺 3-I）：每轮回复经后处理；原始回复进返回值（“只 Prompt”一组），后处理后的回复进 post_runs（“Prompt + 后处理”一组），
    两组来自同一次调用；对话历史里写后处理后的回复。
    """
    per_run: list[list[dict[str, Any]]] = []
    errors = 0
    for run in range(1, runs + 1):
        out: list[dict[str, Any]] = []
        post_out: list[dict[str, Any]] = []
        for case in cases:
            subject = (case.get("context") or {}).get("subject")
            messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *_context_message(case)]
            turns: list[dict[str, Any]] = []
            post_turns: list[dict[str, Any]] = []
            first_contact = bool((case.get("context") or {}).get("first_contact"))
            source_version = f"prompt:{PROMPT_TEMPLATE}@{prompt_version}"
            error = None
            for t, text in enumerate(case["turns"]):
                messages.append({"role": "user", "content": text})
                key = f"cmp-{nonce}-{cand.id}-{case['case_id']}-r{run}-t{t}"
                try:
                    res = await router.route(RouteRequest(
                        task=Task.T1, data_class=DataClass.L0, subject=subject if subject in SUBJECT_CODES else None,
                        prompt_template=PROMPT_TEMPLATE, prompt_version=prompt_version, messages=tuple(messages),
                        usage_id=f"u-{key}", idempotency_key=key))
                except (RouteRejected, RouteUnavailable) as e:
                    error = e.code  # 只记错误类别；缺的轮次由评分器判为 missing_reply（不通过）
                    log.emit(candidate=cand.id, case_id=case["case_id"], run=run, turn=t, error=error)
                    break
                log.emit(candidate=cand.id, case_id=case["case_id"], run=run, turn=t, error=None)
                reply_text, fields = parse_reply(res.output, reply_format)
                turns.append({"text": reply_text, "fields": fields, "model_version": res.model_version})
                if post is None:
                    messages.append({"role": "assistant", "content": res.output})
                else:
                    p_text, p_fields, actions = post.process(reply_text, fields, student_text=text, turn_index=t, first_contact=first_contact)
                    post_turns.append({"text": p_text, "fields": p_fields, "model_version": res.model_version, "postproc": actions})
                    messages.append({"role": "assistant", "content": _assistant_message(p_text, p_fields, reply_format)})
                source_version = res.source_version
            errors += error is not None
            versions = sorted({x["model_version"] for x in turns})
            row = {"case_id": case["case_id"], "run": run, "candidate": cand.id,
                   "model_version": versions[0] if len(versions) == 1 else ("mixed" if versions else None),
                   "prompt_version": prompt_version, "source_version": source_version, "error": error, "turns": turns}
            out.append(row)
            if post is not None:
                post_out.append({**row, "turns": post_turns, "postproc_version": post.version})
        per_run.append(out)
        if post_runs is not None:
            post_runs.append(post_out)
    return per_run, errors


def build_router(cand: Candidate, cases: list[dict[str, Any]], sink: CallLog, seen: list | None = None) -> ModelRouter:
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


def _check_env_runs(env: str, runs: int) -> None:
    if env not in ENVIRONMENTS:
        raise CompareError(f"未知环境 {env!r}")
    if env not in RUN_ENVS:
        raise CompareError(f"{env} 环境不跑对照评测：只收 {'、'.join(RUN_ENVS)}（真实首轮只在 staging 或 dev；pilot、prod 拒绝）")
    if not 1 <= runs <= MAX_RUNS:
        raise CompareError(f"--runs 应在 1–{MAX_RUNS} 之间")


def _load_inputs(candidates_path: Path, env: str) -> tuple[list[dict[str, Any]], set[str], list[Candidate]]:
    """（开发集用例，留出集编号，候选）。默认不跑留出集（规则 14）"""
    all_cases = load_cases(HERE / "cases")
    holdout_ids = read_holdout(HOLDOUT_PATH)
    check_holdout(holdout_ids, {c["case_id"] for c in all_cases})
    holdout = set(holdout_ids)
    cases = [c for c in all_cases if c["case_id"] not in holdout]
    cands = load_candidates(candidates_path, env, {c["category"] for c in all_cases})
    return cases, holdout, cands


def _budget(cands: list[Candidate], cases: list[dict[str, Any]], runs: int, env: str, max_calls: int | None, sides: int = 1) -> tuple[int, int]:
    planned, retries, worst = estimate_calls(cands, cases, runs)
    planned, worst = planned * sides, worst * sides
    turns = sum(len(c["turns"]) for c in cases)
    side_note = f" × {sides} 个版本" if sides > 1 else ""
    print(f"eval-persona compare: 预计调用 {planned} 次（{len(cands)} 个候选 × {runs} 次 × {len(cases)} 条用例共 {turns} 轮{side_note}）；"
          f"失败重试另计，每次调用最多重试 {retries} 次，最坏 {worst} 次供应商请求")
    return planned, check_max_calls(planned, env, max_calls)


def _score_write(per_run: list[list[dict[str, Any]]], cases: list[dict[str, Any]], lex: Any, holdout: set[str], gold_present: bool,
                 cand_dir: Path, suffix: str = "") -> list[dict[str, Any]]:
    scored = []
    for k, responses in enumerate(per_run, start=1):
        rep = score(cases, responses, lex, holdout=holdout, gold_present=gold_present)
        d = cand_dir / f"run-{k}{suffix}"
        d.mkdir(parents=True, mode=0o700)
        # 回复原文只写到仓库外（spec 设计要点 1）；用完请删除
        with _open_private(d / "responses.jsonl") as f:
            for r in responses:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        write_run(rep, d)
        scored.append(rep)
    return scored


@dataclass
class SideRun:
    """一个版本（Prompt × 后处理）下全部候选的结果：raw = 只 Prompt，post = Prompt + 后处理（没用后处理时为 None）"""
    raw: list[dict[str, Any]]
    post: list[dict[str, Any]] | None
    raw_runs: dict[str, list[list[dict[str, Any]]]]
    post_runs: dict[str, list[list[dict[str, Any]]]] | None


def _run_side(cands: list[Candidate], cases: list[dict[str, Any]], runs: int, system: str, fmt: str, out_dir: Path, *, lex: Any,
              holdout: set[str], gold_present: bool, nonce: str, seen: list | None, post: "postproc.PostProcessor | None" = None,
              prompt_version: str = PROMPT_VERSION) -> SideRun:
    raw_results: list[dict[str, Any]] = []
    post_results: list[dict[str, Any]] = []
    raw_runs: dict[str, list] = {}
    post_by: dict[str, list] = {}
    with private_umask():
        log = CallLog(out_dir / "calls.jsonl")
        try:
            for cand in cands:
                router = build_router(cand, cases, log, seen)
                p_runs: list | None = [] if post is not None else None

                async def go(c=cand, r=router, pr=p_runs):
                    try:
                        return await run_candidate(c, r, cases, runs, system, fmt, nonce, log, post=post,
                                                   prompt_version=prompt_version, post_runs=pr)
                    finally:
                        await r.aclose()

                per_run, errors = asyncio.run(go())
                base = {"id": cand.id, "vendor": cand.vendor, "model": cand.model, "model_version": cand.model_version,
                        "note": cand.note, "params": cand.params,
                        "fake_profile": None if cand.fake_profile is None else _profile_str(cand.fake_profile),
                        "calls": log.calls.get(cand.id, 0), "call_errors": errors, "model_mismatch": log.mismatch.get(cand.id, 0)}
                raw_results.append({**base, "runs": _score_write(per_run, cases, lex, holdout, gold_present, out_dir / cand.id)})
                raw_runs[cand.id] = per_run
                if post is not None:
                    # “Prompt + 后处理”一组：同一次调用的后处理后回复，单独评分（不与原始回复混算）
                    post_results.append({**base, "runs": _score_write(p_runs, cases, lex, holdout, gold_present, out_dir / cand.id, "-post")})
                    post_by[cand.id] = p_runs
        finally:
            log.close()
            lock_down(out_dir)
    return SideRun(raw_results, post_results if post is not None else None, raw_runs, post_by if post is not None else None)


def _meta(env: str, runs: int, fmt: str, prompt_meta: dict[str, Any], holdout: set[str], lex: Any, gold_present: bool, planned: int,
          limit: int, cases: list[dict[str, Any]]) -> dict[str, Any]:
    # 结构字段类检查点（依赖回复结构字段，如 referral、handoff）：报告单列（契约审查）；真实模型的结构字段来源待定
    structural = sorted({ch["field"] for c in cases for ch in c["checks"] if ch["type"] == "field"})
    return {"sample": env == "test", "env": env, "runs_per_candidate": runs, "reply_format": fmt, "prompt": prompt_meta,
            "holdout_excluded": len(holdout), "lexicon_version": lex.version, "lexicon_reviewed": lex.reviewed,
            "gold_present": gold_present, "data_class": "L0", "task": CANDIDATE_TASK.value,
            "calls_planned": planned, "max_calls": limit, "structural_fields": structural}


def compare(candidates_path: Path, *, runs: int = 3, env: str = "test", out_dir: Path, report_dir: Path | None,
            reply_format: str | None = None, seen: list | None = None, max_calls: int | None = None) -> dict[str, Any]:
    _check_env_runs(env, runs)
    check_out_dir(out_dir)
    if report_dir is not None:
        check_report_dir(report_dir)
    system, prompt_meta = load_prompt(env)
    cases, holdout, cands = _load_inputs(candidates_path, env)
    check_endpoints(cands, env)
    planned, limit = _budget(cands, cases, runs, env, max_calls)
    fmt = reply_format or ("envelope" if env == "test" else "text")
    lex = load_lexicon()
    gold_present = (HERE / "gold").is_dir()
    nonce = uuid.uuid4().hex[:8]
    prepare_out_dir(out_dir)
    side = _run_side(cands, cases, runs, system, fmt, out_dir, lex=lex, holdout=holdout, gold_present=gold_present, nonce=nonce, seen=seen)
    report = compare_report.build(_meta(env, runs, fmt, prompt_meta, holdout, lex, gold_present, planned, limit, cases), side.raw)
    if report_dir is not None:
        compare_report.write(report, report_dir)
    return report


# ================================================================ 冲刺 3-I：修订前后对比与二轮报告

PROMPTS_DIR = ROOT / "docs" / "personas" / "prompts"
PROMPT_REL = "docs/personas/prompts/diandian-v0.md"  # 与 PROMPT_PATH 同一个文件（相对仓库根，写进报告与修订链）


def load_prompt_side(side: "r2report.Side", env: str) -> tuple[str, dict[str, Any]]:
    """按修订记录里的 Prompt 版本取人设 Prompt（只读，docs/personas/prompts/ 下由人设负责人提交）。
    写了 sha256 就核对（签字后被改过就拒绝）；文件不存在时只有 test 环境可以用测试占位"""
    source_version = f"prompt:{PROMPT_TEMPLATE}@{side.prompt_version}"
    if side.prompt_path:
        path = ROOT / side.prompt_path
        if not _inside(path, PROMPTS_DIR):
            raise CompareError("人设 Prompt 只能来自 docs/personas/prompts/")
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if side.prompt_sha256 and sha != side.prompt_sha256:
                raise CompareError(f"{_display(path)} 的指纹与修订记录不一致（签字后被改过）：拒绝运行")
            return text, {"source": _display(path), "source_version": source_version, "sha256": sha}
    if env != "test":
        raise CompareError(f"{source_version} 的人设 Prompt 文件不存在：{env} 环境拒绝运行（Prompt 由人设负责人提交）")
    text = f"{fakes.PLACEHOLDER_SYSTEM}（{source_version}）"
    return text, {"source": "测试占位（不是人设 Prompt）", "source_version": source_version,
                  "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _with_profiles(cands: list[Candidate], overrides: dict[str, str] | None, categories: set[str]) -> list[Candidate]:
    """test 环境：按修订记录的 fake_profiles 覆盖假候选的行为（证明“版本变了、结果变了”）"""
    if not overrides:
        return cands
    ids = {c.id for c in cands}
    unknown = sorted(set(overrides) - ids)
    if unknown:
        raise CompareError(f"fake_profiles 里有不在候选清单里的候选：{unknown}")
    out = []
    for c in cands:
        if c.id in overrides:
            try:
                c = replace(c, fake_profile=fakes.parse_profile(overrides[c.id], categories))
            except ValueError as e:
                raise CompareError(f"{c.id}: {e}") from None
        out.append(c)
    return out


def _side_stats(side: SideRun, cand_ids: list[str]) -> dict[str, dict[str, Any]]:
    """修订对比用：每个候选这个版本“实际下发的输出”（有后处理即后处理后）的七项与禁语触发率，另附只 Prompt 的禁语触发率"""
    final, final_runs = (side.post, side.post_runs) if side.post is not None else (side.raw, side.raw_runs)
    by_id = {r["id"]: r for r in final}
    return {cid: {"columns": r2report.columns(by_id[cid]["runs"]),
                  "forbidden_rate": r2report.forbidden_rate(final_runs[cid], postproc.forbidden_hits),
                  "forbidden_rate_prompt_only": r2report.forbidden_rate(side.raw_runs[cid], postproc.forbidden_hits)}
            for cid in cand_ids}


def _r2_errors(fn):
    """把二轮模块的拒绝统一成 CompareError（退出码 2）"""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except (r2report.R2Error, postproc.PostprocConfigError) as e:
            raise CompareError(str(e)) from None
    wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
    return wrapped


@_r2_errors
def compare_revision(candidates_path: Path, n: int, *, revisions_dir: Path = r2report.REVISIONS_DIR, runs: int = 3, env: str = "test",
                     out_dir: Path, report_dir: Path | None, reply_format: str | None = None, seen: list | None = None,
                     max_calls: int | None = None, criteria_path: Path = r2report.CRITERIA_PATH) -> dict[str, Any]:
    """修订 n：在开发集上按修订前后两个版本各跑 N 次，输出前后对比（列对齐 persona-evaluator 七项）与是否采纳"""
    criteria = r2report.load_criteria(criteria_path)
    _check_env_runs(env, runs)
    check_out_dir(out_dir)
    if report_dir is not None:
        check_report_dir(report_dir)
    rev = r2report.load_revision(revisions_dir, n, env, criteria)
    cases, holdout, cands = _load_inputs(candidates_path, env)
    categories = {c["category"] for c in load_cases(HERE / "cases")}
    prompts = {name: load_prompt_side(getattr(rev, name), env) for name in ("before", "after")}
    posts = {name: postproc.get(getattr(rev, name).postproc) for name in ("before", "after")}
    side_cands = {name: _with_profiles(cands, rev.fake_profiles.get(name), categories) for name in ("before", "after")}
    check_endpoints(cands, env)
    _planned, _limit = _budget(cands, cases, runs, env, max_calls, sides=2)
    fmt = reply_format or ("envelope" if env == "test" else "text")
    lex = load_lexicon()
    gold_present = (HERE / "gold").is_dir()
    nonce = uuid.uuid4().hex[:8]
    prepare_out_dir(out_dir)
    ids = [c.id for c in cands]
    stats = {}
    for name in ("before", "after"):
        d = out_dir / name
        d.mkdir(mode=0o700)
        side = _run_side(side_cands[name], cases, runs, prompts[name][0], fmt, d, lex=lex, holdout=holdout, gold_present=gold_present,
                         nonce=nonce, seen=seen, post=posts[name], prompt_version=getattr(rev, name).prompt_version)
        stats[name] = _side_stats(side, ids)
    lock_down(out_dir)
    res = r2report.revision_result(rev, env=env, runs=runs, cases=len(cases), sides=stats, cand_ids=ids)
    if report_dir is not None:
        r2report.write_revision(res, report_dir)
    return res


@_r2_errors
def compare_r2(candidates_path: Path, *, revisions_dir: Path = r2report.REVISIONS_DIR, runs: int = 3, env: str = "test", out_dir: Path,
               report_dir: Path | None, reply_format: str | None = None, seen: list | None = None, max_calls: int | None = None,
               holdout_summary: Path | None = None, criteria_path: Path = r2report.CRITERIA_PATH) -> dict[str, Any]:
    """二轮：用修订链最终采纳的版本（没有修订时为 Prompt v0 + 后处理 v0）跑开发集，同一批输出分别统计两组数字，给出验收判定"""
    criteria = r2report.load_criteria(criteria_path)
    _check_env_runs(env, runs)
    check_out_dir(out_dir)
    if report_dir is not None:
        check_report_dir(report_dir)
    revisions = r2report.load_revision_results(report_dir, revisions_dir, criteria)
    default = r2report.Side(prompt_version=PROMPT_VERSION, postproc=postproc.VERSION, prompt_path=PROMPT_REL)
    base = r2report.baseline(revisions, default)
    post = postproc.get(base.postproc)
    if post is None:
        raise CompareError("二轮版本没有后处理：两组数字要求“Prompt + 后处理”一组存在（修订链最终版本的 postproc 不能是 none）")
    system, prompt_meta = load_prompt_side(base, env)
    cases, holdout, cands = _load_inputs(candidates_path, env)
    ids = [c.id for c in cands]
    holdout_doc = r2report.load_holdout_summary(holdout_summary, ids)
    check_endpoints(cands, env)
    planned, limit = _budget(cands, cases, runs, env, max_calls)
    fmt = reply_format or ("envelope" if env == "test" else "text")
    lex = load_lexicon()
    gold_present = (HERE / "gold").is_dir()
    nonce = uuid.uuid4().hex[:8]
    prepare_out_dir(out_dir)
    side = _run_side(cands, cases, runs, system, fmt, out_dir, lex=lex, holdout=holdout, gold_present=gold_present, nonce=nonce,
                     seen=seen, post=post, prompt_version=base.prompt_version)
    meta = {**_meta(env, runs, fmt, prompt_meta, holdout, lex, gold_present, planned, limit, cases), "baseline": base.to_dict()}
    sets = {"prompt_only": compare_report.build(meta, side.raw), "prompt_plus_post": compare_report.build(meta, side.post)}
    runs_of = {"prompt_only": side.raw_runs, "prompt_plus_post": side.post_runs}
    results_of = {"prompt_only": {r["id"]: r for r in side.raw}, "prompt_plus_post": {r["id"]: r for r in side.post}}
    extras = {s: {cid: {"columns": r2report.columns(results_of[s][cid]["runs"]),
                        "forbidden_rate": r2report.forbidden_rate(runs_of[s][cid], postproc.forbidden_hits)} for cid in ids}
              for s in r2report.SETS}
    post_desc = {**post.describe(), "actions": {cid: r2report.action_counts(side.post_runs[cid]) for cid in ids}}
    samples = {cid: r2report.failure_samples(cases, results_of[criteria.verdict_basis][cid]["runs"]) for cid in ids}
    rep = r2report.build(meta, sets, extras, revisions=revisions, holdout=holdout_doc, criteria=criteria, post=post_desc, samples=samples)
    if report_dir is not None:
        r2report.write(rep, report_dir)
    return rep


def _profile_str(p: tuple[str, Any]) -> str:
    kind, arg = p
    if kind == "fail_category":
        return f"fail_category:{arg}"
    if kind == "random":
        return f"random:{arg[0]:g}:{arg[1]}"
    return kind


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="compare.py", description="点点跨模型一致性对照运行器（首轮；--revision 修订前后对比；--r2 二轮报告）")
    ap.add_argument("--candidates", required=True, help="候选清单 YAML")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--env", default="test", choices=ENVIRONMENTS)
    ap.add_argument("--out", help="回复原文与逐次评分的目录（必须在仓库外；默认新建临时目录）")
    ap.add_argument("--report-dir", help="汇总报告目录（不含原文；仓库内只能是 evals/persona/reports/ 下）")
    ap.add_argument("--reply-format", choices=("text", "envelope"), help="回复解析方式；默认 test 环境 envelope，其他 text")
    ap.add_argument("--max-calls", type=int, default=None,
                    help=f"调用次数上限（预计超过就拒绝）；test 环境默认 {DEFAULT_MAX_CALLS}，其他环境必须显式给出")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--revision", type=int, help="冲刺 3-I：修订 n 的前后对比（读 --revisions-dir/<n>.yaml，写 revision-<n>.{json,md}）")
    mode.add_argument("--r2", action="store_true", help="冲刺 3-I：二轮报告（两组数字、修订记录、留出集汇总位、验收判定）")
    ap.add_argument("--revisions-dir", help="修订记录目录（默认 diandian/revisions）")
    ap.add_argument("--holdout-summary", help="--r2：教研负责人交回的留出集汇总 JSON（可选；本仓不跑留出集）")
    ap.add_argument("--criteria", help="验收口径 YAML（默认 diandian/r2_criteria.yaml）")
    a = ap.parse_args(argv)
    out_dir = (Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="diandian-compare."))).resolve()
    report_dir = Path(a.report_dir).resolve() if a.report_dir else None
    extra: dict[str, Any] = {}
    if a.revisions_dir:
        extra["revisions_dir"] = Path(a.revisions_dir).resolve()
    if a.criteria:
        extra["criteria_path"] = Path(a.criteria).resolve()
    run_env = getattr(a, "env")
    try:
        if a.revision is not None:
            res = compare_revision(Path(a.candidates), a.revision, runs=a.runs, env=run_env, out_dir=out_dir, report_dir=report_dir,
                                   reply_format=a.reply_format, max_calls=a.max_calls, **extra)
            print(f"eval-persona compare: 修订 {res['revision']} 判定：{res['decision']['label']}"
                  + ("（" + "；".join(res["decision"]["reasons"]) + "）" if res["decision"]["reasons"] else ""))
            print(f"eval-persona compare: 回复原文在 {out_dir}（仓库外，用完请删除）" + (f"；前后对比在 {a.report_dir}" if a.report_dir else ""))
            return 0
        if a.r2:
            rep = compare_r2(Path(a.candidates), runs=a.runs, env=run_env, out_dir=out_dir, report_dir=report_dir, reply_format=a.reply_format,
                             max_calls=a.max_calls, holdout_summary=Path(a.holdout_summary).resolve() if a.holdout_summary else None, **extra)
            print(f"eval-persona compare: 二轮验收判定（{rep['verdict']['basis_label']}）：{rep['verdict']['result']}；"
                  f"参考（{r2report.SET_LABELS[rep['verdict']['reference']['basis']]}）：{rep['verdict']['reference']['result']}；结论由人定")
            print(f"eval-persona compare: 回复原文在 {out_dir}（仓库外，用完请删除）" + (f"；报告在 {a.report_dir}" if a.report_dir else ""))
            return 0
        if extra or a.holdout_summary:
            raise CompareError("--revisions-dir、--criteria、--holdout-summary 只和 --revision 或 --r2 一起用")
        rep = compare(Path(a.candidates), runs=a.runs, env=run_env, out_dir=out_dir, report_dir=report_dir, reply_format=a.reply_format,
                      max_calls=a.max_calls)
    except CompareError as e:
        print(f"eval-persona compare: 拒绝运行：{e}")
        return 2
    tag = f"（{compare_report.SAMPLE_BANNER}）" if rep["sample"] else ""
    for c in rep["candidates"]:
        print(f"  {c['id']}: 总通过率 {c['overall']['rate']}，核心 {c['core']['rate']}，红线 {c['redline']['count']} 次，"
              f"不稳定 {c['unstable']['ratio']}，调用失败 {c['call_errors']}")
    print(f"eval-persona compare: 完成{tag}；{len(rep['candidates'])} 个候选 × {a.runs} 次 × {rep['cases']} 条用例；"
          f"回复原文与调用元数据在 {out_dir}（仓库外，用完请删除）" + (f"；报告在 {a.report_dir}" if a.report_dir else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
