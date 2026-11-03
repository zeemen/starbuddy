"""LT-05 一致性：学习、洞察、知识三个服务的可观测性模块各带一份（不跨服务导入），代码部分（标记行之后）必须逐字相同；
标签白名单与 deploy/observability/metrics-inventory.yaml、BFF（src/observability/metrics.ts）一致；标签枚举、日志字段白名单
（与 IaC 日志索引字段同一来源）、禁止键名与脱敏规则 Python 与 BFF 同口径。
不导入各服务的包（deploy 测试的解释器里没有 prometheus_client 以外的服务依赖）：从源码里取常量。全部合成值。"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OBS = [ROOT / "services/learning/learning/obs.py", ROOT / "services/insight/insight/service/obs.py",
       ROOT / "services/knowledge/knowledge/service/obs.py"]
MARK = "# ---- 以下在 learning/learning/obs.py、insight/insight/service/obs.py、knowledge/knowledge/service/obs.py 三处逐字相同"
BFF_METRICS = ROOT / "services/bff/src/observability/metrics.ts"
BFF_LOG = ROOT / "services/bff/src/observability/log.ts"
INVENTORY = yaml.safe_load((ROOT / "deploy/observability/metrics-inventory.yaml").read_text(encoding="utf-8"))


def shared_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(MARK) == 1, f"{path.relative_to(ROOT)} 缺少共用代码的标记行"
    return text[text.index(MARK):]


def _const(body: str, name: str):
    for node in ast.parse(body).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            v = node.value
            if isinstance(v, ast.Call) and getattr(v.func, "id", "") == "frozenset":
                v = v.args[0]
            return ast.literal_eval(v)
    raise AssertionError(f"缺常量 {name}")


def test_three_obs_modules_identical():
    bodies = [shared_body(p) for p in OBS]
    assert bodies[0] == bodies[1] == bodies[2], "三份 obs.py 的代码部分必须逐字相同（改一处同步另外两处）"
    body = bodies[0]
    for needed in ("disable_created_metrics()", "REGISTRY = CollectorRegistry(auto_describe=True)", "def label_value(",
                   "SKIP_PATHS = frozenset({METRICS_PATH, \"/healthz\", \"/readyz\"})", "route_template(scope, self.prefixes)",
                   '"cache-control": "no-store"', "def scrub(", "class JsonFormatter", "exc_type", "async def metrics_app(",
                   "def metrics_port(", "def log_fields(", "for k, v in log_fields(fields).items():"):
        assert needed in body, needed


def test_label_whitelist_same_everywhere():
    py = set(_const(shared_body(OBS[0]), "LABELS"))
    assert py == set(INVENTORY["labels"]) == {"service", "route", "method", "status_class", "subject", "task", "provider", "outcome"}
    ts = BFF_METRICS.read_text(encoding="utf-8")
    m = re.search(r"export const LABELS = \[([^\]]*)\] as const;", ts)
    assert m and set(re.findall(r"'([a-z_]+)'", m.group(1))) == py, "BFF 的标签白名单与 Python 一致"


def _ts_set(ts: str, name: str) -> set[str]:
    m = re.search(rf"export const {name}(?:: ReadonlySet<string>)? = new Set\(\[(.*?)\]\);", ts, re.S)
    assert m, f"BFF 缺 {name}"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def _py_log_fields(body: str) -> set[str]:
    envelope = _const(body, "ENVELOPE")
    m = re.search(r"LOG_FIELDS = frozenset\(ENVELOPE \+ \((.*?)\)\)", body, re.S)
    assert m
    return set(envelope) | set(ast.literal_eval("(" + m.group(1) + ")"))


def test_log_field_whitelist_same_in_python_bff_and_iac():
    """字段白名单（安全审查 B）：Python 三份、BFF、IaC 日志索引字段同一来源"""
    body = shared_body(OBS[0])
    py = _py_log_fields(body)
    ts = BFF_LOG.read_text(encoding="utf-8")
    m = re.search(r"export const ENVELOPE = \[([^\]]*)\] as const;", ts)
    assert m and tuple(re.findall(r"'([a-z_]+)'", m.group(1))) == tuple(_const(body, "ENVELOPE")), "信封字段两边一致"
    assert _ts_set(ts, "LOG_FIELDS") | set(re.findall(r"'([a-z_]+)'", m.group(1))) == py, "BFF 的 LOG_FIELDS 与 Python 逐项相同"
    assert _ts_set(ts, "HASHED_FIELDS") == {"student", "account"} and not (_ts_set(ts, "HASHED_FIELDS") & py)
    import importlib.util
    import sys
    mod = sys.modules.get("check_iac")
    if mod is None:
        spec = importlib.util.spec_from_file_location("check_iac", ROOT / "deploy/iac/check_iac.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["check_iac"] = mod  # dataclass 需要模块在 sys.modules 里
        spec.loader.exec_module(mod)
    assert set(mod.LOG_INDEX_FIELDS) <= py, "IaC 日志索引字段只能从 LOG_FIELDS 里选"
    assert mod.LOG_FIELDS_SOURCE == ROOT / "services/learning/learning/obs.py"


def test_enums_same_in_python_and_bff():
    body = shared_body(OBS[0])
    m = re.search(r"^ENUMS: dict\[str, frozenset\[str\]\] = \{(.*?)^\}", body, re.S | re.M)
    assert m
    py = {k: set(v) for k, v in ast.literal_eval("{" + re.sub(r"frozenset\((\{.*?\})\)", r"\1", m.group(1), flags=re.S) + "}").items()}
    ts = BFF_METRICS.read_text(encoding="utf-8")
    for name, values in py.items():
        tm = re.search(rf"  {name}: new Set\(\[(.*?)\]\),", ts, re.S)
        assert tm, f"BFF 缺枚举 {name}"
        assert set(re.findall(r"'([A-Za-z0-9_-]+)'", tm.group(1))) == values, f"枚举 {name} 两边一致"
    assert set(py) == {"subject", "task", "provider", "outcome"}


def test_redaction_rules_same_in_python_and_bff():
    body = shared_body(OBS[0])
    ts = BFF_LOG.read_text(encoding="utf-8")
    py_key = re.search(r'_FORBIDDEN_KEY = re.compile\(r"\(\?i\)\^\(\?:\.\*_\)\?\((.*?)\)\(\?:_\(\?:id\|ref\)\)\?\$"\)',
                       body.replace('"\n                            r"', ""))
    ts_key = re.search(r"const FORBIDDEN_KEY = /\^\(\?:\.\*_\)\?\((.*?)\)\(\?:_\(\?:id\|ref\)\)\?\$/i;", ts)
    assert py_key and ts_key
    assert py_key.group(1).split("|") == ts_key.group(1).split("|"), "禁止键名两边逐项相同"
    for needed in ("session", "name", "texts?", "messages?", "student", "account", "photo", "audio", "url", "raw"):
        assert needed in py_key.group(1).split("|"), needed
    for pat in (r"postgres(?:ql)?://\S+", r"\beyJ[A-Za-z0-9_-]{8,}", r"\b(?:sk|ak|pk|rk)-[A-Za-z0-9_-]{16,}", r"LTAI[0-9A-Za-z]{12,}",
                r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])", r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)",
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"):
        assert pat in body, pat
        assert pat.replace("/", r"\/") in ts or pat in ts, f"BFF 缺同一条脱敏规则：{pat}"
    assert 'UUID_MARK = "[uuid]"' in body and "export const UUID_MARK = '[uuid]';" in ts
    py_norm = re.search(r're\.sub\(r"(.*?)", r"_\\1", str\(key\)\)', body)
    ts_norm = re.search(r"String\(key\)\.replace\(/(.*?)/g, '_\$1'\)", ts)
    assert py_norm and ts_norm and py_norm.group(1) == ts_norm.group(1), "键名驼峰归一同一规则"
