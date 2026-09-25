"""一致性测试的公共部分：夹具加载、请求组装、HTTP 调用、泄露检查。与学科无关。

所有期望都来自两处：契约 YAML（schema 与 pattern），以及共享的平台侧核对库 starbuddy_contract.checks
（也就是学习服务落库前用的那一份）。这里不写任何学科专用的判断。
"""
from __future__ import annotations

import copy
import json
import re
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from starbuddy_contract.contract import CONTRACT_VERSION  # noqa: E402

METHODS = ("diagnose", "plan", "teach", "grade", "generate_variation", "report")
METERED = ("diagnose", "grade", "teach")
SUBJECTS = ("wenli", "aya", "zhixing", "echo")
STUDENT = "5c000000-0000-4000-8000-00000000c0f0"  # 合成学生（规则 8）
T0 = datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc)


# ---------- 夹具 ----------
@dataclass
class Fixtures:
    subject: str
    content_version: dict
    rubric_version: str | None
    items: dict[str, str]
    skill_nodes: set[str]
    secrets: list[str]
    cases: list[dict]
    path: Path = field(default=Path("."))

    def cases_for(self, method: str) -> list[dict]:
        return [c for c in self.cases if c["method"] == method]


def load_fixtures(path: str | Path) -> Fixtures:
    p = Path(path)
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    if d["subject"] not in SUBJECTS:
        raise ValueError(f"夹具学科 {d['subject']} 不在契约 Subject 枚举里")
    ids = [c["id"] for c in d["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("夹具 case id 重复")
    for c in d["cases"]:
        if c["method"] not in METHODS:
            raise ValueError(f"{c['id']}: 未知方法 {c['method']}")
    return Fixtures(
        subject=d["subject"],
        content_version=d["content_version"],
        rubric_version=d.get("rubric_version"),
        items=dict(d["registry"]["items"]),
        skill_nodes=set(d["registry"]["skill_nodes"]),
        secrets=[s for s in d.get("secrets", []) if isinstance(s, str) and len(s) >= 2],
        cases=d["cases"],
        path=p,
    )


# ---------- 请求 ----------
def context(fx: Fixtures, case_id: str, *, metered: bool = True) -> dict:
    ctx = {
        "request_id": str(uuid.uuid4()),
        "contract_version": CONTRACT_VERSION,
        "subject": fx.subject,
        "student_ref": STUDENT,
        "data_class": "L2",
        "idempotency_key": f"conformance-{case_id}-{uuid.uuid4().hex[:12]}"[:128],
        "content_version": dict(fx.content_version),
    }
    if metered:
        ctx["usage_id"] = str(uuid.uuid4())
    return ctx


def build_request(fx: Fixtures, case: dict) -> dict:
    """由夹具生成完整请求：补上下文、作答时间（每道作答相隔一分钟，保证可区分）。"""
    m = case["method"]
    body: dict = {"context": context(fx, case["id"], metered=m in METERED)}
    if m == "grade":
        body["attempt"] = {**copy.deepcopy(case["attempt"]), "occurred_at": T0.isoformat()}
        body["rubric_version"] = case.get("rubric_version", fx.rubric_version)
        if "appeal" in case:
            body["appeal"] = case["appeal"]
    elif m == "diagnose":
        body["mode"] = case.get("mode", "intake")
        body["attempts"] = [
            {**copy.deepcopy(a), "occurred_at": (T0 + timedelta(minutes=i)).isoformat()} for i, a in enumerate(case["attempts"])
        ]
    else:
        body.update(copy.deepcopy(case.get("body", {})))
    return body


def attempts_of(method: str, request: dict) -> list[dict]:
    if method == "grade":
        return [request["attempt"]]
    return request.get("attempts", [])


def student_inputs(method: str, request: dict) -> list[str]:
    out: list[str] = []
    for a in attempts_of(method, request):
        r = a.get("response", {})
        if "text" in r:
            out.append(r["text"])
        out.extend(r.get("choice", []))
    return out


# ---------- HTTP ----------
@dataclass
class Reply:
    status: int
    body: object
    raw: str

    def __repr__(self) -> str:  # 断言失败时不打印响应体（安全审查 N5）
        return f"Reply(status={self.status}, bytes={len(self.raw)})"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向：3xx 直接当作失败（被测方不能用重定向“代答”，也不会把令牌转发给别的主机）。"""

    def redirect_request(self, *args, **kwargs):
        return None


# 不走任何代理（安全审查 S1）：测试流量带服务间令牌和合成作答，只发往本机被测适配器
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class AdapterClient:
    def __init__(self, base_url: str, credential: str) -> None:
        self.base = base_url.rstrip("/") + "/plugin/v0"
        self.credential = credential

    def call(self, verb: str, path: str, body: object | None = None, *, auth: bool = True, timeout: float = 60) -> Reply:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(f"{self.base}/{path}", data=data, method=verb)
        req.add_header("content-type", "application/json")
        if auth:
            req.add_header("authorization", f"Bearer {self.credential}")
        try:
            with OPENER.open(req, timeout=timeout) as r:
                raw = r.read().decode()
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            status = e.code
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return Reply(status, parsed, raw)


# ---------- 泄露检查（结构白名单，沿用阿鸦 leak.test.ts 的做法；pattern 取自契约） ----------
@lru_cache(maxsize=None)
def _contract_pattern(name: str) -> re.Pattern:
    from starbuddy_contract.contract import PLUGIN_CONTRACT
    schema = yaml.safe_load(PLUGIN_CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"][name]
    return re.compile(schema["pattern"])


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CODE_RE = re.compile(r"[a-z0-9_\-]{1,48}")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")
# 数字只能出现在这些字段（契约里所有数值字段的名字）
NUMERIC_KEYS = {"confidence", "score", "max_score", "total", "max_total", "sampled_points", "agreeing_points",
                "points_awarded", "low", "high", "span_start", "span_end", "value", "poll_after_ms", "status", "exam_year", "grade"}


def _leaves(v: object, path: str = ""):
    if isinstance(v, list):
        for i, x in enumerate(v):
            yield from _leaves(x, f"{path}/{i}")
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _leaves(x, f"{path}/{k}")
    else:
        yield path, v


def leak_problems(body: object, *, inputs: list[str], times: list[str], secrets: list[str]) -> list[str]:
    """返回违规列表（空表示通过）。只报路径，不回显值。"""
    patterns = [_contract_pattern(n) for n in ("SkillNodeRef", "ModelVersion", "SourceVersion", "VersionedName")]
    problems = []
    for path, v in _leaves(body):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            if path.rsplit("/", 1)[-1] not in NUMERIC_KEYS:
                problems.append(f"{path}: 数字出现在白名单之外的字段")
            continue
        if re.search(r"/evidence/\d+/payload/(text|choice/\d+)$", path):
            if v not in inputs:
                problems.append(f"{path}: 作答证据不是学生原文")
            continue
        if v in secrets:
            problems.append(f"{path}: 出现了夹具标注的秘密")
            continue
        controlled = (
            UUID_RE.fullmatch(v) or CODE_RE.fullmatch(v) or SEMVER_RE.fullmatch(v) or v in times
            or any(p.fullmatch(v) for p in patterns)
        )
        if not controlled:
            problems.append(f"{path}: 不是受控值")
    return problems
