"""文件 URL 转路径的静态检查（内测前整改 P2：目录名含中文时测试 ENOENT）。

`new URL(..., import.meta.url).pathname` 拿到的是百分号编码的路径（中文目录会变成 %E5%8D%87…），交给文件系统就找不到文件；
`import.meta.url === "file://" + process.argv[1]` 在这样的目录里永远不相等，CLI 静默不运行。
一律改用 node:url 的 fileURLToPath(new URL(...))（转路径）或 pathToFileURL(路径).href（转 URL）。

  python3 tools/path-url-check/check.py              扫全仓（跳过 node_modules、.next、legacy、.git）
  python3 tools/path-url-check/check.py --self-test  正反例自检
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"node_modules", ".next", ".next-e2e", ".git", "legacy", ".venv", "dist", "coverage"}
EXTS = {".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs"}
RULES = [
    # 第一个参数随意，第二个参数是 import.meta.url，或名字里带 ROOT / HERE / DIR / REPO 的常量（按约定是 file: URL）；
    # ORIGIN 这类 http 基址不算
    (re.compile(r"new URL\([^()]*,\s*(import\.meta\.url|[A-Z_]*(?:ROOT|HERE|DIR|REPO)[A-Z0-9_]*)\s*\)\.pathname"), "URL 的 pathname 是百分号编码的，交给文件系统要用 fileURLToPath(new URL(...))"),
    (re.compile(r"import\.meta\.url\)?\.pathname"), "import.meta.url 的 pathname 是百分号编码的，用 fileURLToPath(import.meta.url)"),
    # 遍历目录的写法：statSync(u).isDirectory() ? … : [u.pathname]（u 是 file: URL）
    (re.compile(r"statSync\(\w+\)\.isDirectory\(\).*\b\w+\.pathname\b"), "目录遍历里的 file: URL 要用 fileURLToPath(u) 转成路径"),
    (re.compile(r"`file://\$\{"), "拼 file:// 不做编码，含中文、空格、% 的路径对不上；用 pathToFileURL(路径).href"),
]
SELF = __file__


def scan_text(text: str) -> list[tuple[int, str]]:
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        for rx, why in RULES:
            if rx.search(line):
                out.append((no, why))
                break
    return out


def files(root: Path):
    for p in root.rglob("*"):
        if p.suffix in EXTS and p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS):
            yield p


def self_test() -> int:
    bad = [
        "const WEB = new URL('..', import.meta.url).pathname;",
        "const L = new URL('services/learning/', ROOT).pathname;",
        "const p = import.meta.url.pathname;",
        "if (import.meta.url === `file://${process.argv[1]}`) {",
        "return statSync(u).isDirectory() ? walk(new URL(`${n}/`, dir)) : [u.pathname];",
    ]
    good = [
        "const WEB = fileURLToPath(new URL('..', import.meta.url));",
        "const L = fileURLToPath(new URL('services/learning/', ROOT));",
        "const p = new URL(req.url, 'http://bff.invalid').pathname;",
        "calls.map((u) => new URL(u).pathname)",
        "`synthetic ${new URL(path, ORIGIN).pathname}`",
        "return statSync(u).isDirectory() ? walk(new URL(`${n}/`, dir)) : [fileURLToPath(u)];",
        "if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {",
    ]
    errs = [b for b in bad if not scan_text(b)] + [g for g in good if scan_text(g)]
    for e in errs:
        print(f"path-url-check 自检失败：{e}", file=sys.stderr)
    if not errs:
        print(f"path-url-check 自检通过（{len(bad)} 个反例被拦，{len(good)} 个正例放行）")
    return 1 if errs else 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    hits = []
    for p in files(ROOT):
        if str(p) == SELF:
            continue
        for no, why in scan_text(p.read_text(encoding="utf-8", errors="replace")):
            hits.append(f"  {p.relative_to(ROOT)}:{no}：{why}")
    if hits:
        print("path-url-check: 失败（目录名含中文、空格或 % 时这些路径会出错）", file=sys.stderr)
        print("\n".join(hits), file=sys.stderr)
        return 1
    print("path-url-check: 通过（没有把 URL 的 pathname 当文件路径用，也没有手拼 file:// URL）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
