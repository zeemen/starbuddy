#!/usr/bin/env bash
# 页面仓库的程序体检入口（CI 与本地共用）。有阻断退出 1，参数或输入错误退出 2——两者都不能合并。
#   KNOWLEDGE_DIR  StarBuddy 仓库 services/knowledge 的检出目录（已按其 README 安装 requirements.txt）
#   BASELINE_DIR   目标分支（main）的检出目录，作为“上一审核版”做锁定段比对；CI 里必须给
#   PYTHON         解释器（默认 python3）
# 等价于：python -m knowledge.lint pages --map-snapshot snapshots/current.yaml --baseline "$BASELINE_DIR/pages" --lexicon lexicon/lexicon.json --json
# 结果写到 lint-report.json（字段对应 KGE 第七章 lint_issue），作为 CI 产物保存。
set -uo pipefail
cd "$(dirname "$0")/.."
if [ -z "${KNOWLEDGE_DIR:-}" ] || [ ! -d "$KNOWLEDGE_DIR/knowledge" ]; then
  echo "kge-lint: 需要 KNOWLEDGE_DIR 指向 StarBuddy 的 services/knowledge" >&2; exit 2
fi
args=(pages --map-snapshot snapshots/current.yaml --lexicon lexicon/lexicon.json --json)
if [ -n "${BASELINE_DIR:-}" ]; then
  args+=(--baseline "$BASELINE_DIR/pages")
elif [ "${CI:-}" = "true" ]; then
  echo "kge-lint: CI 里必须给 BASELINE_DIR（上一审核版），否则锁定段比对不跑" >&2; exit 2
fi
PYTHONPATH="$KNOWLEDGE_DIR${PYTHONPATH:+:$PYTHONPATH}" "${PYTHON:-python3}" -m knowledge.lint "${args[@]}" > lint-report.json
rc=$?
"${PYTHON:-python3}" - <<'PY' || true
import json
try:
    s = json.load(open("lint-report.json", encoding="utf-8"))["summary"]
    print(f"kge-lint: {s['pages']} 页，阻断 {s['blocks']}，警告 {s['warnings']}")
except Exception:
    print("kge-lint: 没有生成体检报告")
PY
exit $rc
