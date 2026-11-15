"""审核台错误码导出（specs/review-console-error-codes 第二节第 2 条）。

后端能发出的错误码有两处来源：review_console/*.py 里的 ReviewError(状态, "码") 调用（按语法树扫描，只认第二个参数是字符串字面量的），
与库错误映射 store._PG_MAP 的取值（store.db_error 按 SQLSTATE 换码，兜底 INTERNAL）。错误码不是字面量的调用只允许出现在
store.db_error 一处；别处新加了这样的调用，导出直接报错（免得漏收）。
写到 apps/web/src/lib/review-api.errors.json（提交进仓库）：{"codes": {码: [状态, …]}}。前端测试 apps/web/tests/review-errors.test.ts
核对每个码在 RESULT_LABEL 里都有提示；学习服务测试 tests/test_review_errors_export.py 核对提交的文件就是现在的导出。

  .venv/bin/python scripts/export_review_errors.py          写文件
  .venv/bin/python scripts/export_review_errors.py --check  只比较，不一致退出码 1
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
PKG = HERE / "review_console"
OUT = HERE.parents[1] / "apps" / "web" / "src" / "lib" / "review-api.errors.json"
NOTICE = "由 services/learning/scripts/export_review_errors.py 从审核台后端源码与库错误映射导出，不要手改（specs/review-console-error-codes）"
DYNAMIC_OK = {("store.py", "db_error")}  # 错误码来自 _PG_MAP 的那一处


def scan(pkg: Path = PKG) -> dict[str, set[int]]:
    codes: dict[str, set[int]] = {}
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] + [tree]:
            name = getattr(fn, "name", "<module>")
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "ReviewError"):
                    continue
                args = call.args
                if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                    status = args[0].value if isinstance(args[0], ast.Constant) else None
                    codes.setdefault(args[1].value, set()).update({status} if isinstance(status, int) else set())
                elif (path.name, name) not in DYNAMIC_OK and name != "<module>":
                    raise SystemExit(f"{path.name}:{call.lineno}（{name}）：ReviewError 的错误码不是字面量；改成字面量，或登记进 DYNAMIC_OK 并把来源加进导出")
    return codes


def export() -> dict[str, Any]:
    from review_console.store import _PG_MAP
    codes = scan()
    for status, code in _PG_MAP.values():
        codes.setdefault(code, set()).add(status)
    codes.setdefault("INTERNAL", set()).add(500)  # store.db_error 映射不到时的兜底
    return {"_notice": NOTICE, "codes": {c: sorted(s) for c, s in sorted(codes.items())}}


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render(export())
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"review-errors: {OUT} 与后端错误码不一致（重新运行 scripts/export_review_errors.py）", file=sys.stderr)
            return 1
        print("review-errors: 与后端错误码一致")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"review-errors: 写入 {OUT}（{len(json.loads(text)['codes'])} 个错误码）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
