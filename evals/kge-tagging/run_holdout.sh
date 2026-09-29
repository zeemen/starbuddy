#!/usr/bin/env bash
# 留出集盲测（CLAUDE.md 规则 14；KGE V1.1 :748 留出集无处可标率 < 2%、:751 Top-1 ≥ 90%）。
# 由教研负责人在封存环境里自行执行；构建方不接触留出集：不读取、不索要、不用它调参。
#   - 本脚本不含任何留出集路径，所有输入由执行人用参数传入；
#   - 输入必须在仓库目录之外（留出集不进仓库）；
#   - 只打印汇总指标（Top-1、Top-3 召回、无处可标率、门槛判定），不打印逐题结果，不写任何文件；
#     输入格式出错时也只报行号或字段名，不带题目 ID 与节点。
# 用法：
#   bash evals/kge-tagging/run_holdout.sh --gold <留出集金标.csv> --tagging <对留出集的打标报告.json> [--candidates <候选.csv|.json>] [--enforce]
# 退出码：同 top1.py（0 算完；--enforce 且未达标为 1；输入错误为 2）。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd -P)"
PY="${PYTHON:-python3}"

usage() { echo "用法：bash run_holdout.sh --gold <路径> --tagging <路径> [--candidates <路径>] [--enforce]" >&2; exit 2; }
gold="" tagging="" candidates="" enforce=""
while [ $# -gt 0 ]; do
  case "$1" in
    --gold) [ $# -ge 2 ] || usage; gold="$2"; shift 2 ;;
    --tagging) [ $# -ge 2 ] || usage; tagging="$2"; shift 2 ;;
    --candidates) [ $# -ge 2 ] || usage; candidates="$2"; shift 2 ;;
    --enforce) enforce="--enforce"; shift ;;
    *) usage ;;
  esac
done
[ -n "$gold" ] && [ -n "$tagging" ] || usage

resolve_outside_repo() {  # 打印真实路径；在仓库里就拒绝（留出集不进仓库；也防止误用仓库里的开发集冒充留出集）
  local p
  p="$(realpath -e -- "$1" 2>/dev/null)" || { echo "run_holdout: 输入文件不存在（$2）" >&2; exit 2; }
  case "$p/" in "$REPO"/*) echo "run_holdout: 拒绝：$2 在仓库目录内，留出集只能放在仓库之外的封存环境" >&2; exit 2 ;; esac
  printf '%s\n' "$p"
}
gold="$(resolve_outside_repo "$gold" --gold)"
tagging="$(resolve_outside_repo "$tagging" --tagging)"
args=(--gold "$gold" --tagging "$tagging" --summary-only)
if [ -n "$candidates" ]; then candidates="$(resolve_outside_repo "$candidates" --candidates)"; args+=(--candidates "$candidates"); fi
[ -n "$enforce" ] && args+=("$enforce")

echo "run_holdout: 留出集盲测，只输出汇总指标（规则 14：构建方不接触留出集）"
cd "$HERE"
PYTHONDONTWRITEBYTECODE=1 exec "$PY" top1.py "${args[@]}"
