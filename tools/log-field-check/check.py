"""Python 服务日志字段的静态检查（specs/log-field-audit）。

学习、洞察、知识服务的日志写法是 log.<级别>(json.dumps({...}))，JsonFormatter 经 log_fields 只留 LOG_FIELDS 里的顶层键，
其余整项丢掉（LT-05 安全审查 B）。不在白名单里的键写了也看不到；以前有 7 处这样丢了 caller、node_id 等字段。本检查扫：
  - log.<级别>(json.dumps({...})) 里字典字面量的键；
  - 名为 record / rec、且同一函数里经 log.<级别>(json.dumps(record)) 写进日志的字典：字面量初始化（含带类型注解的）、
    record["键"] = …、record.update(键=… / {...})。别的同名字典（例如知识快照里的 rec）不算。
键（驼峰先归一为下划线小写）必须在 services/learning/learning/obs.py 的 LOG_FIELDS 里。
动态拼出的键（例如 record.update(**fields)）扫不到；BFF 的 emit / note 由 services/bff/tests/gate-log-fields.test.ts 覆盖。

  python3 tools/log-field-check/check.py              扫三个服务
  python3 tools/log-field-check/check.py --self-test  正反例自检
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBS = ROOT / "services/learning/learning/obs.py"
ROOTS = [ROOT / "services/learning/learning", ROOT / "services/learning/review_console", ROOT / "services/insight/insight",
         ROOT / "services/knowledge/knowledge", ROOT / "services/scheduler"]
SKIP_DIRS = {".venv", "__pycache__", "tests", "node_modules"}
LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
RECORD_NAMES = {"record", "rec"}


def normalize_key(key: str) -> str:
    """与 obs.py 的 normalize_key 相同：驼峰先归一为下划线小写"""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(key)).replace("-", "_").lower()


def log_fields() -> frozenset[str]:
    """读 obs.py 的 ENVELOPE 与 LOG_FIELDS（只解析，不导入：obs.py 依赖服务自己的虚拟环境）"""
    tree = ast.parse(OBS.read_text(encoding="utf-8"))
    consts: dict[str, ast.expr] = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            consts[n.targets[0].id] = n.value
    envelope = ast.literal_eval(consts["ENVELOPE"])
    v = consts["LOG_FIELDS"]
    assert isinstance(v, ast.Call) and isinstance(v.args[0], ast.BinOp), "LOG_FIELDS 的写法变了：frozenset(ENVELOPE + (...))"
    return frozenset(envelope) | frozenset(ast.literal_eval(v.args[0].right))


def _dict_keys(d: ast.Dict) -> list[tuple[int, str]]:
    return [(k.lineno, k.value) for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]


def _is_dumps(a: ast.expr) -> bool:
    return isinstance(a, ast.Call) and (getattr(a.func, "attr", None) or getattr(a.func, "id", None)) == "dumps" and bool(a.args)


def _logged_names(scope: ast.AST) -> set[str]:
    """这个函数里经 log.<级别>(json.dumps(名字)) 写进日志的 record / rec"""
    out = set()
    for n in ast.walk(scope):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in LEVELS and n.args and _is_dumps(n.args[0])
                and isinstance(n.args[0].args[0], ast.Name) and n.args[0].args[0].id in RECORD_NAMES):
            out.add(n.args[0].args[0].id)
    return out


def _record_keys(scope: ast.AST, names: set[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for n in ast.walk(scope):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id in names:
            out += [(n.lineno, kw.arg) for kw in n.keywords if kw.arg]
            out += [kv for a in n.args if isinstance(a, ast.Dict) for kv in _dict_keys(a)]
        targets: list[ast.expr] = []
        value = None
        if isinstance(n, ast.Assign):
            targets, value = n.targets, n.value
        elif isinstance(n, ast.AnnAssign):
            targets, value = [n.target], n.value
        for t in targets:
            if isinstance(t, ast.Name) and t.id in names and isinstance(value, ast.Dict):
                out += _dict_keys(value)
            if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id in names
                    and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str)):
                out.append((t.lineno, t.slice.value))
    return out


def keys_in(text: str) -> list[tuple[int, str]]:
    """一份源码里写进日志的键（行号、键名）：日志调用里的字典字面量；同一函数里经日志写出的 record / rec 字典的键"""
    tree = ast.parse(text)
    out: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in LEVELS and n.args \
                and _is_dumps(n.args[0]) and isinstance(n.args[0].args[0], ast.Dict):
            out += _dict_keys(n.args[0].args[0])
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] + [tree]
    for sc in scopes:
        names = _logged_names(sc)
        if names:
            out += _record_keys(sc, names)
    return out


def scan_text(text: str, allowed: frozenset[str]) -> list[tuple[int, str]]:
    return sorted({(no, k) for no, k in keys_in(text) if normalize_key(k) not in allowed})


def files() -> list[Path]:
    out = []
    for r in ROOTS:
        if not r.exists():
            continue
        for p in r.rglob("*.py"):
            if not (SKIP_DIRS & set(p.relative_to(ROOT).parts)):
                out.append(p)
    return sorted(out)


_W = "def f():\n    {}\n    log.info(json.dumps(record))\n"
BAD = [
    'log.info(json.dumps({"event": "x", "caller_secret": 1}))',
    'log.warning(json.dumps({"event": "x", "rowz": 2}, ensure_ascii=False))',
    _W.format('record = {"tool": t, "who_called": c}'),
    _W.format('record: dict[str, Any] = {"tool": t, "whoCalled": c}'),
    _W.format('record["unknown_key"] = 1'),
    _W.format('record.update(status=200, extra_thing=1)'),
    _W.format('record.update({"status": 200, "nope": 1})'),
    "def g():\n    rec = {'event': 'x', 'zzz': 1}\n    log.warning(json.dumps(rec, ensure_ascii=False))\n",
]
GOOD = [
    'log.info(json.dumps({"event": "x", "status": 200, "error_class": "E"}))',
    _W.format('record = {"tool": t, "caller": c}'),
    _W.format('record["duration_ms"] = 3'),
    _W.format('record.update(status=200, code="X")'),
    _W.format('record.update(**fields)'),           # 动态键扫不到（文件头已写明）
    'other = {"anything": 1}',                       # 不是写进日志的字典
    'log.info("plain text %s", x)',
    "def h():\n    rec = dict(n)\n    rec['curriculum'] = []\n    add(rec)\n",  # 同名但没写进日志
]


def self_test(allowed: frozenset[str]) -> int:
    bad = [s for s in BAD if not scan_text(s, allowed)]
    good = [s for s in GOOD if scan_text(s, allowed)]
    if bad or good:
        for s in bad:
            print(f"log-field-check 自检失败：没拦住 {s}")
        for s in good:
            print(f"log-field-check 自检失败：误拦 {s}")
        return 1
    print(f"log-field-check 自检通过（{len(BAD)} 个反例被拦，{len(GOOD)} 个正例放行）")
    return 0


def main(argv: list[str]) -> int:
    allowed = log_fields()
    if "--self-test" in argv:
        return self_test(allowed)
    problems = []
    fs = files()
    for p in fs:
        for no, k in scan_text(p.read_text(encoding="utf-8"), allowed):
            problems.append(f"{p.relative_to(ROOT)}:{no}: 日志键 {k} 不在 LOG_FIELDS 里，生产日志会整项丢掉（改用已登记的字段，或登记进 obs.py 与 BFF log.ts）")
    if problems:
        print("\n".join(problems))
        print(f"log-field-check: 失败（{len(problems)} 处）")
        return 1
    print(f"log-field-check: 通过（{len(fs)} 个文件，写进日志的键都在 LOG_FIELDS 里）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
