"""一致性测试的公共部分：夹具加载、请求组装、HTTP 调用（含异步任务轮询）、泄露检查。与学科无关。

所有期望都来自两处：契约 YAML（schema、枚举、pattern），以及共享的平台侧核对库 starbuddy_contract.checks
（也就是学习服务落库前用的那一份）。这里不写任何学科专用的判断。依赖：PyYAML 与共享库；HTTP 只用标准库。
"""
from __future__ import annotations

import copy
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from starbuddy_contract.checks import ContractViolation, parse_time  # noqa: E402
from starbuddy_contract.contract import CONTRACT_VERSION, PLUGIN_CONTRACT  # noqa: E402
from starbuddy_contract.present import answer_field_paths, project_presented_item  # noqa: E402

STUDENT = "5c000000-0000-4000-8000-00000000c0f0"  # 合成学生（规则 8）
T0 = datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc)


@lru_cache(maxsize=1)
def _contract_doc() -> dict:
    return yaml.safe_load(PLUGIN_CONTRACT.read_text(encoding="utf-8"))


def _schemas() -> dict:
    return _contract_doc()["components"]["schemas"]


# 方法、学科、消耗类方法都从契约推导，契约新增学科或方法时不会不同步（契约守护复核）
METHODS: tuple[str, ...] = tuple(_schemas()["Method"]["enum"])
SUBJECTS: tuple[str, ...] = tuple(_schemas()["Subject"]["enum"])
# Gate 0 覆盖率的分母固定是六个学科方法（开发计划 v2.0 Gate 0：“六方法覆盖”）。v1.1 的 present_item 是只读的平台取数方法，
# 不计入 of / implemented / passed，在报告里单列为 read_only_methods（PLT-12 审查阻断 1）
SUBJECT_METHODS: tuple[str, ...] = ("diagnose", "plan", "teach", "grade", "generate_variation", "report")
READ_ONLY_METHODS: tuple[str, ...] = tuple(m for m in METHODS if m not in SUBJECT_METHODS)


def _schema_name(method: str, kind: str) -> str:
    return "".join(p.capitalize() for p in method.split("_")) + kind


REQUEST_SCHEMA = {m: _schema_name(m, "Request") for m in METHODS}
RESPONSE_SCHEMA = {m: _schema_name(m, "Response") for m in METHODS}
METERED: tuple[str, ...] = tuple(
    m for m in METHODS if "MeteredContext" in json.dumps(_schemas()[REQUEST_SCHEMA[m]]["properties"]["context"])
)


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


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in str(v).split("."))


def load_fixtures(path: str | Path) -> Fixtures:
    """夹具里的用例可以写 `since: 1.1.0`：只在不低于这个契约版本时才跑（例如 v1.1 才有的 present_item），
    同一份夹具因此能在 0.9.0 / 1.0.0 / 1.1.0 下都跑。"""
    p = Path(path)
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    if d["subject"] not in SUBJECTS:
        raise ValueError(f"夹具学科 {d['subject']} 不在契约 Subject 枚举里")
    ids = [c["id"] for c in d["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("夹具 case id 重复")
    d["cases"] = [c for c in d["cases"] if _version(c.get("since", "0.0.0")) <= _version(CONTRACT_VERSION)]
    for c in d["cases"]:
        if c["method"] not in METHODS:
            raise ValueError(f"{c['id']}: 未知方法 {c['method']}")
    return Fixtures(
        subject=d["subject"],
        content_version=d["content_version"],
        rubric_version=d.get("rubric_version"),
        items=dict(d["registry"]["items"]),
        skill_nodes=set(d["registry"]["skill_nodes"]),
        secrets=[str(s) for s in d.get("secrets", []) if str(s) != ""],
        cases=d["cases"],
        path=p,
    )


# ---------- 请求 ----------
def coverage_report(caps: dict, results: dict[str, list[str]], subject: str | None, exit_status: int,
                    global_failures: list[str], read_only: tuple[str, ...] = READ_ONLY_METHODS) -> dict:
    """覆盖率报告（Gate 0 统计用）。of / implemented / passed 只按六个学科方法算；只读方法单列在 read_only_methods。
    任何全局用例失败（例如不校验令牌、契约版本不对），或整套测试没有通过，所有方法都不算“通过”（契约守护 S1）。"""
    suite_ok = int(exit_status) == 0 and not global_failures

    def one(m: str) -> dict:
        implemented = bool(((caps.get("methods") or {}).get(m) or {}).get("implemented"))
        outcomes = results.get(m, [])
        return {
            "implemented": implemented,
            "tests": len(outcomes),
            "failed": sum(o != "passed" for o in outcomes),
            "passed": suite_ok and implemented and bool(outcomes) and all(o == "passed" for o in outcomes),
        }

    methods = {m: one(m) for m in SUBJECT_METHODS}
    return {
        "subject": subject,
        "adapter_version": caps.get("adapter_version"),
        "contract_version": caps.get("contract_version"),
        "implemented": sum(v["implemented"] for v in methods.values()),
        "passed": sum(v["passed"] for v in methods.values()),
        "of": len(SUBJECT_METHODS),
        "methods": methods,
        "read_only_methods": {m: one(m) for m in read_only},
        "suite_exit_status": int(exit_status),
        "global_failures": list(global_failures),
    }


def context(fx: Fixtures, case: dict) -> dict:
    if case["method"] == "present_item":
        # v1.1 PresentItemContext：题面与学生无关，不带 student_ref、usage_id；幂等键可选（默认不带），最低 L1
        ctx = {
            "request_id": str(uuid.uuid4()),
            "contract_version": CONTRACT_VERSION,
            "subject": fx.subject,
            "data_class": case.get("data_class", "L1"),
            "content_version": dict(fx.content_version),
        }
        if case.get("idempotency_key"):
            ctx["idempotency_key"] = str(uuid.uuid4())
        return ctx
    ctx = {
        "request_id": str(uuid.uuid4()),
        "contract_version": CONTRACT_VERSION,
        "subject": fx.subject,
        "student_ref": STUDENT,
        # 夹具可以声明 L3（照片、语音类作答）；默认 L2
        "data_class": case.get("data_class", "L2"),
        # v1.0 只收 UUIDv4 幂等键；v0.9 保留可读前缀便于排查
        "idempotency_key": (str(uuid.uuid4()) if CONTRACT_VERSION != "0.9.0"
                            else f"conformance-{case['id']}-{uuid.uuid4().hex[:12]}"[:128]),
        "content_version": dict(fx.content_version),
    }
    if case["method"] in METERED:
        ctx["usage_id"] = str(uuid.uuid4())
    return ctx


def build_request(fx: Fixtures, case: dict) -> dict:
    """由夹具生成完整请求：补上下文、作答时间（每道作答相隔一分钟，保证可区分）。"""
    m = case["method"]
    body: dict = {"context": context(fx, case)}
    if m == "grade":
        body["attempt"] = {**copy.deepcopy(case["attempt"]), "occurred_at": T0.isoformat()}
        if CONTRACT_VERSION != "0.9.0":  # v1.0：attempt_id 由平台生成，必填（评审 N3、N4）
            body["attempt"].setdefault("attempt_id", str(uuid.uuid4()))
        body["rubric_version"] = case.get("rubric_version", fx.rubric_version)
        if "appeal" in case:
            body["appeal"] = case["appeal"]
    elif m == "diagnose":
        body["mode"] = case.get("mode", "intake")
        if "attempts" in case:
            body["attempts"] = [
                {**copy.deepcopy(a), "occurred_at": (T0 + timedelta(minutes=i)).isoformat()} for i, a in enumerate(case["attempts"])
            ]
            if CONTRACT_VERSION != "0.9.0":
                for a in body["attempts"]:
                    a.setdefault("attempt_id", str(uuid.uuid4()))
        body.update(copy.deepcopy(case.get("body", {})))
    elif m == "present_item":
        # v1.1：取题面（只读、非消耗类）。内容版本取夹具的六段标识；夹具的 body 只给 item_ref
        body["content_version"] = dict(fx.content_version)
        body.update(copy.deepcopy(case.get("body", {})))
    else:
        body.update(copy.deepcopy(case.get("body", {})))
    return body


def attempts_of(method: str, request: dict) -> list[dict]:
    if method == "grade":
        return [request["attempt"]]
    return request.get("attempts", [])


def student_inputs(method: str, request: dict) -> list[str]:
    """学生自己提供的内容：作答原文、选项、以及本次请求带来的媒体引用。"""
    out: list[str] = []
    for a in attempts_of(method, request):
        r = a.get("response", {})
        for k in ("text", "image_ref", "transcript_ref"):
            if k in r:
                out.append(r[k])
        out.extend(r.get("choice", []))
    out.extend(request.get("paper_image_refs", []))
    out.extend(p["media_ref"] for p in request.get("prior_evidence", []) if "media_ref" in p)
    return out


def conflict_variant(case: dict, body: dict) -> dict | None:
    """同 key 的“另一个请求体”：有作答时改 elapsed_sec（AttemptInput 允许的字段）；
    否则由夹具用 conflict_body 提供要合并的字段；都没有时返回 None（测试会明确失败并提示补夹具）。"""
    changed = copy.deepcopy(body)
    attempts = attempts_of(case["method"], changed)
    if attempts:
        for a in attempts:
            a["elapsed_sec"] = int(a.get("elapsed_sec", 0)) + 7
        return changed
    if "conflict_body" in case:
        changed.update(copy.deepcopy(case["conflict_body"]))
        return changed
    return None


# ---------- HTTP ----------
@dataclass
class Reply:
    status: int
    body: object
    raw: str
    content_type: str = ""

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
                raw, status, ctype = r.read().decode(), r.status, r.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            raw, status, ctype = e.read().decode(), e.code, e.headers.get("content-type", "")
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return Reply(status, parsed, raw, ctype)

    def invoke(self, method: str, body: dict, *, deadline_s: float = 60) -> tuple[Reply, dict | None, str | None]:
        """调用一个方法。同步 200 直接返回；202 按契约轮询 GET /jobs/{job_id} 直到 succeeded / failed。
        返回 (首个回复, 最终的方法响应或 None, job_id 或 None)。"""
        from starbuddy_contract.contract import plugin_contract  # noqa: PLC0415

        first = self.call("POST", method, body)
        if first.status == 200:
            return first, first.body, None
        if first.status != 202:
            return first, None, None
        errs = plugin_contract().validate("JobAccepted", first.body)
        assert errs == [], f"202 受理体不符合契约 JobAccepted：{errs}"
        job = first.body
        assert job["method"] == method, "JobAccepted.method 与调用的方法不一致"
        assert job["usage_id"] == body["context"].get("usage_id"), "JobAccepted 没有回传 usage_id"
        assert job["idempotency_key"] == body["context"]["idempotency_key"], "JobAccepted 没有回传 idempotency_key"
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            time.sleep(max(job.get("poll_after_ms", 200), 200) / 1000)
            st = self.call("GET", f"jobs/{job['job_id']}")
            assert st.status == 200, f"轮询任务返回 {st.status}"
            errs = plugin_contract().validate("JobStatus", st.body)
            assert errs == [], f"任务状态不符合契约 JobStatus：{errs}"
            if st.body["status"] == "succeeded":
                return first, st.body["response"], job["job_id"]
            if st.body["status"] == "failed":
                return first, None, job["job_id"]
        raise AssertionError("异步任务在期限内没有完成")


# ---------- 泄露检查（结构白名单，沿用阿鸦 leak.test.ts 的做法；pattern 取自契约） ----------
@lru_cache(maxsize=None)
def _contract_pattern(name: str) -> re.Pattern:
    return re.compile(_schemas()[name]["pattern"])


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# 编码形状：契约里 dimension_id、subject_category、local_level、point_id 等都在这个范围内
CODE_RE = re.compile(r"[A-Za-z0-9_\-]{1,48}")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")
OBJSTORE_RE = re.compile(r"objstore://[a-z0-9][a-z0-9/_.\-]{0,255}")
# 数字只能出现在这些字段（按契约里全部数值字段的名字整理）
NUMERIC_KEYS = {"confidence", "score", "max_score", "total", "max_total", "sampled_points", "agreeing_points",
                "points_awarded", "low", "high", "span_start", "span_end", "value", "poll_after_ms", "status",
                "exam_year", "grade", "priority", "estimated_minutes", "difficulty", "attempts", "count",
                "basis_admitted_items", "hint_level_used", "elapsed_sec", "p_mastery"}
STUDENT_TEXT_KINDS = ("ocr_transcription", "asr_transcription")


def _leaves(v: object, path: str = ""):
    if isinstance(v, list):
        for i, x in enumerate(v):
            yield from _leaves(x, f"{path}/{i}")
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _leaves(x, f"{path}/{k}")
    else:
        yield path, v


def _secret_hit(value: str, secrets: list[str]) -> bool:
    """3 个字符以上的秘密按子串查（防止藏在编码形状的字符串里）；更短的只按完全相等查，避免和 ID 偶然重合。"""
    return any((s in value) if len(s) >= 3 else (s == value) for s in secrets)


def leak_problems(body: object, *, inputs: list[str], secrets: list[str]) -> list[str]:
    """grade / diagnose 响应的泄露检查。返回违规列表（空表示通过），只报路径，不回显值。

    检查边界（写在 README）：只适用于没有自由文本字段的方法；teach 的讲解文本不在范围内。"""
    patterns = [_contract_pattern(n) for n in ("SkillNodeRef", "ModelVersion", "SourceVersion", "VersionedName")]
    evidence = body.get("evidence", []) if isinstance(body, dict) else []
    problems = []
    for path, v in _leaves(body):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            if path.rsplit("/", 1)[-1] not in NUMERIC_KEYS:
                problems.append(f"{path}: 数字出现在白名单之外的字段")
            elif any(str(v) == s or repr(v) == s for s in secrets):
                problems.append(f"{path}: 数值等于夹具标注的秘密")
            continue
        m = re.fullmatch(r"/evidence/(\d+)/payload/(text|choice/\d+)", path)
        if m:
            kind = evidence[int(m.group(1))].get("kind")
            if kind in STUDENT_TEXT_KINDS:
                continue  # 转写文本是学生的内容，但不在请求里，无法逐字比对
            if v not in inputs:
                problems.append(f"{path}: 作答证据不是学生原文")
            continue
        if _secret_hit(v, secrets):
            problems.append(f"{path}: 出现了夹具标注的秘密")
            continue
        if OBJSTORE_RE.fullmatch(v):
            if v not in inputs:
                problems.append(f"{path}: 媒体引用不是本次请求带来的")
            continue
        try:
            parse_time(v)
            continue  # 时间不是秘密；是否可信由平台侧核对负责
        except ValueError:
            pass
        controlled = UUID_RE.fullmatch(v) or CODE_RE.fullmatch(v) or SEMVER_RE.fullmatch(v) or any(p.fullmatch(v) for p in patterns)
        if not controlled:
            problems.append(f"{path}: 不是受控值")
    return problems


def string_leaves(v: object) -> list[str]:
    return [x for _, x in _leaves(v) if isinstance(x, str)]


# ---------- present_item（v1.1，PLT-12）的泄露检查 ----------
# 题面内容本身（文字、公式、图片说明）是教研内容，允许出现夹具里登记为“题面文字”的秘密（例如知行的材料地名）；
# 其他位置只能是受控值。用例自己的 secrets（答案、解析、采分点说明）在任何位置都不许出现，包括题面内容。
CONTENT_LEAF = re.compile(r"/result/(stem/\d+/runs|options/\d+/content)/\d+/(text|latex|alt)")
PRESENT_NUMERIC = NUMERIC_KEYS | {"min_choices", "max_choices", "max_length"}


def present_problems(body: object, *, secrets: list[str], answer_secrets: list[str]) -> list[str]:
    """present_item 响应的平台侧核对与泄露检查（只报路径，不回显值）：
    1. 平台侧白名单（starbuddy_contract.present，学习服务用的同一份）能重建题面；
    2. 任何位置都没有答案类字段名；不产生证据；
    3. 用例的答案类秘密在任何位置都不出现（3 个字符以上按子串，更短的按完全相等）；
    4. 题面内容之外的字符串只能是受控值，且不含夹具的全局秘密；数字只在白名单字段。"""
    problems: list[str] = []
    if not isinstance(body, dict):
        return ["/: 响应不是对象"]
    try:
        project_presented_item(body.get("result"))
    except ContractViolation as e:
        problems.append(f"/result: 平台侧白名单不通过（{e}）")
    problems += [f"{p}: 答案类字段名" for p in answer_field_paths(body)]
    if body.get("evidence_ids") or "evidence" in body:
        problems.append("/evidence_ids: 题面不得产生证据")
    patterns = [_contract_pattern(n) for n in ("SkillNodeRef", "ModelVersion", "SourceVersion", "VersionedName")]
    for path, v in _leaves(body):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            if path.rsplit("/", 1)[-1] not in PRESENT_NUMERIC:
                problems.append(f"{path}: 数字出现在白名单之外的字段")
            continue
        if _secret_hit(v, answer_secrets):
            problems.append(f"{path}: 出现了用例标注的答案类秘密")
            continue
        if CONTENT_LEAF.fullmatch(path):
            continue  # 题面内容：教研内容，受限富文本已由契约与白名单约束
        if _secret_hit(v, secrets):
            problems.append(f"{path}: 出现了夹具标注的秘密")
            continue
        if OBJSTORE_RE.fullmatch(v):
            continue  # 图片引用（契约已限定为对象存储引用）
        controlled = UUID_RE.fullmatch(v) or CODE_RE.fullmatch(v) or SEMVER_RE.fullmatch(v) or any(p.fullmatch(v) for p in patterns)
        if not controlled:
            problems.append(f"{path}: 不是受控值")
    return problems
