#!/usr/bin/env bash
# 页面仓库的程序体检入口（CI 与本地共用）。有阻断退出 1，参数或输入错误退出 2——两者都不能合并。
#
# 信任边界（BRANCH_PROTECTION.md 第 13–18 条）：CI 里本脚本必须从受保护分支 main 的检出运行；
# 本脚本、地图快照（snapshots/current.yaml）、人设词表（lexicon/lexicon.json）与上一审核版页面（pages/）一律取自这份 main 检出，
# 合并请求头部只提供 HEAD_PAGES_DIR（它的 pages/ 目录）。合并请求里改动的 ci/、snapshots/、lexicon/ 不参与本次体检。
#
#   KNOWLEDGE_DIR   检查工具：StarBuddy services/knowledge 的固定版本（ci/install-tools.sh 装好的只读制品）
#   HEAD_PAGES_DIR  合并请求头部的 pages/ 目录；CI 里必须给。本地不给时体检本检出自己的 pages/（不做上一审核版比对）
#   LINT_REPORT     体检 JSON 的输出路径（缺省 <本检出>/lint-report.json）
#   PYTHON          解释器（默认 python3）
# 等价于：python -m knowledge.lint "$HEAD_PAGES_DIR" --map-snapshot <main>/snapshots/current.yaml --require-map-snapshot \
#           --lexicon <main>/lexicon/lexicon.json --baseline <main>/pages --json
# 结果字段对应 KGE 第七章 lint_issue，作为 CI 产物保存；体检消息只给定位，不回显页面原文。
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON:-python3}"
if [ -z "${KNOWLEDGE_DIR:-}" ] || [ ! -d "$KNOWLEDGE_DIR/knowledge" ]; then
  echo "kge-lint: 需要 KNOWLEDGE_DIR 指向检查工具（StarBuddy 的 services/knowledge，见 ci/install-tools.sh）" >&2; exit 2
fi
snapshot="$root/snapshots/current.yaml"
lexicon="$root/lexicon/lexicon.json"
for f in "$snapshot" "$lexicon"; do
  if [ ! -f "$f" ] || [ -L "$f" ]; then
    echo "kge-lint: 缺少 ${f#"$root"/}（地图快照与人设词表必需，取自 main）" >&2; exit 2
  fi
done
baseline=()
if [ -n "${HEAD_PAGES_DIR:-}" ]; then
  pages="$HEAD_PAGES_DIR"
  baseline=(--baseline "$root/pages")
elif [ "${CI:-}" = "true" ]; then
  echo "kge-lint: CI 里必须给 HEAD_PAGES_DIR（合并请求头部的 pages/），本脚本与快照、词表从 main 的检出取" >&2; exit 2
else
  pages="$root/pages"
fi
if [ -L "$pages" ] || [ ! -d "$pages" ]; then
  echo "kge-lint: 页面目录不存在或是符号链接" >&2; exit 2
fi
if [ -n "$(find "$pages" -type l -print -quit)" ]; then
  echo "kge-lint: 页面目录里不能有符号链接" >&2; exit 2
fi
report="${LINT_REPORT:-$root/lint-report.json}"
PYTHONPATH="$KNOWLEDGE_DIR${PYTHONPATH:+:$PYTHONPATH}" "$py" -m knowledge.lint "$pages" --map-snapshot "$snapshot" \
  --require-map-snapshot --lexicon "$lexicon" "${baseline[@]}" --json > "$report"
rc=$?
REPORT="$report" "$py" - <<'PY' || true
import json, os
try:
    s = json.load(open(os.environ["REPORT"], encoding="utf-8"))["summary"]
    print(f"kge-lint: {s['pages']} 页，阻断 {s['blocks']}，警告 {s['warnings']}")
except Exception:
    print("kge-lint: 没有生成体检报告")
PY
exit $rc
