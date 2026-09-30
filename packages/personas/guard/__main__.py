"""命令行：给前端文案单测、页面检查等非 Python 调用方用（输出 JSON，不回显原文）。

  python -m personas.guard scan --persona platform [--text 文本 ...] [--stdin-json] [文件 ...] [--exit-zero]
      逐段判定：--text 每个参数一段；--stdin-json 从标准输入读字符串数组；每个文件整体算一段。
      有命中时退出码 1（--exit-zero 时总是 0）；输出 {"lexicon_version", "guard_version", "persona_id", "results": [{"source", "hits"}]}
  python -m personas.guard rules --persona aya     列出该角色适用的规则（rule_id、class、method）
  python -m personas.guard export-kge [文件]       导出 KGE-03 页面人设中立检查用的词表（JSON）

在仓库根目录运行时先设 PYTHONPATH=packages。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import GUARD_VERSION, PersonaConfigError
from . import applicable_rules, classify_detail, export_kge, load_context


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="personas.guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--persona", required=True)
    s.add_argument("--text", action="append", default=[])
    s.add_argument("--stdin-json", action="store_true")
    s.add_argument("--exit-zero", action="store_true")
    s.add_argument("files", nargs="*")
    r = sub.add_parser("rules")
    r.add_argument("--persona", required=True)
    e = sub.add_parser("export-kge")
    e.add_argument("out", nargs="?")
    a = ap.parse_args(argv)
    try:
        ctx = load_context()
        if a.cmd == "rules":
            rows = [{"rule_id": x.rule_id, "class": x.cls, "method": x.method} for x in applicable_rules(ctx, a.persona)]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if a.cmd == "export-kge":
            text = json.dumps(export_kge(ctx), ensure_ascii=False, indent=2) + "\n"
            if a.out:
                Path(a.out).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
            return 0
        items: list[tuple[str, str]] = [(f"text[{i}]", t) for i, t in enumerate(a.text)]
        if a.stdin_json:
            data = json.loads(sys.stdin.read())
            if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
                print("stdin 必须是字符串数组", file=sys.stderr)
                return 2
            items += [(f"stdin[{i}]", t) for i, t in enumerate(data)]
        items += [(f, Path(f).read_text(encoding="utf-8")) for f in a.files]
        results = [{"source": src, "hits": classify_detail(a.persona, t, ctx=ctx)["hits"]} for src, t in items]
    except PersonaConfigError as ex:
        print(f"guard: {ex}", file=sys.stderr)
        return 2
    out = {"lexicon_version": ctx.lexicon.version, "guard_version": GUARD_VERSION, "persona_id": a.persona, "results": results}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if a.exit_zero or not any(x["hits"] for x in results) else 1


if __name__ == "__main__":
    sys.exit(main())
