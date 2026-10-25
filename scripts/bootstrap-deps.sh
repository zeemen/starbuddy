#!/usr/bin/env bash
# 恢复本地开发依赖，使 `make check CONTRACT_STRICT=1` 的各目标不因缺依赖被跳过。
#
# 原则（CLAUDE.md 规则 10、W13 技术债 T06）：
#   - 只按仓库已有的锁文件安装：Python 一律 pip install --require-hashes -r <锁文件>，Node 一律 npm ci（按 package-lock.json）；
#     不升级、不新增依赖，不下载锁文件以外的东西。
#   - 唯一例外是 .venv-contracts：packages/contracts/requirements-dev.txt 没有带哈希的锁文件，按 CI（.github/workflows/check.yml）
#     同样的命令 pip install -r 安装（直接依赖已锁版本，传递依赖由 pip 解析）。
#   - 幂等：每一步把所用锁文件的 sha256 记在安装目录里（.venv*/.bootstrap-stamp、node_modules/.bootstrap-stamp），
#     锁文件没变且目录完整时跳过；锁文件变了就整目录重建。
#   - 失败即停，并打印是哪一步。
#   - 不安装 Playwright 浏览器（环境里已有，PLAYWRIGHT_BROWSERS_PATH）；不安装 Postgres（用 /usr/lib/postgresql/16/bin，
#     db/tests/pg-ephemeral.sh 会自动找到，也可传 PGBIN）。
#
# 用法：bash scripts/bootstrap-deps.sh            # 在仓库任意位置执行
#       FORCE=1 bash scripts/bootstrap-deps.sh    # 忽略记录，全部重装
# 装完后：make check CONTRACT_STRICT=1 PYTHON=.venv-contracts/bin/python PGBIN=/usr/lib/postgresql/16/bin

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY_BOOT="${PY_BOOT:-python3}"          # 建 venv 用的解释器（CI 为 3.11）
FORCE="${FORCE:-0}"
STEP="(初始化)"

on_error() {
  local rc=$?
  echo "" >&2
  echo "bootstrap-deps: 失败于步骤：$STEP（退出码 $rc）" >&2
  exit "$rc"
}
trap on_error ERR

log() { echo "bootstrap-deps: $*"; }

# 锁文件（及其 -r 引用的锁文件）的合并摘要
lock_digest() {
  sha256sum "$@" | sha256sum | cut -d' ' -f1
}

stamp_ok() { # <stamp 文件> <摘要>
  [ "$FORCE" != "1" ] && [ -f "$1" ] && [ "$(cat "$1")" = "$2" ]
}

# ---------- Python ----------
# venv_install <venv 目录> <工作目录> <摘要> <安装命令...>
venv_setup() {
  local venv="$1" digest="$2"
  local stamp="$venv/.bootstrap-stamp"
  if [ -x "$venv/bin/python" ] && stamp_ok "$stamp" "$digest"; then
    log "跳过 $venv（锁文件未变）"
    return 1
  fi
  if [ -d "$venv" ]; then
    log "重建 $venv（锁文件已变或上次未完成）"
    rm -rf "$venv"
  fi
  "$PY_BOOT" -m venv "$venv"
  return 0
}

py_locked() { # <服务目录> [额外：contracts-editable]
  local dir="$1" extra="${2:-}"
  local lock="$dir/requirements-dev.lock"
  local deps=("$lock")
  # 锁文件里的 -r 引用（scheduler 引用 insight 的锁）也计入摘要
  local ref
  while read -r ref; do
    deps+=("$dir/$ref")
  done < <(sed -n 's/^-r[[:space:]]\{1,\}//p' "$lock")
  [ -n "$extra" ] && deps+=("packages/contracts/python/pyproject.toml")
  local digest; digest="$(lock_digest "${deps[@]}")"

  STEP="$dir：创建 .venv"
  if venv_setup "$dir/.venv" "$digest"; then
    STEP="$dir：pip install --require-hashes -r requirements-dev.lock"
    log "$STEP"
    (cd "$dir" && .venv/bin/pip install -q --disable-pip-version-check --require-hashes -r requirements-dev.lock)
    if [ -n "$extra" ]; then
      STEP="$dir：pip install --no-deps -e ../../packages/contracts/python"
      log "$STEP"
      (cd "$dir" && .venv/bin/pip install -q --disable-pip-version-check --no-deps -e ../../packages/contracts/python)
    fi
    echo "$digest" > "$dir/.venv/.bootstrap-stamp"
  fi
}

py_contracts() {
  local req="packages/contracts/requirements-dev.txt"
  local digest; digest="$(lock_digest "$req")"
  STEP=".venv-contracts：创建"
  if venv_setup ".venv-contracts" "$digest"; then
    STEP=".venv-contracts：pip install -r $req（与 CI 相同；该文件没有哈希锁）"
    log "$STEP"
    .venv-contracts/bin/pip install -q --disable-pip-version-check -r "$req"
    echo "$digest" > ".venv-contracts/.bootstrap-stamp"
  fi
}

# ---------- Node ----------
npm_locked() { # <目录>
  local dir="$1"
  local digest; digest="$(lock_digest "$dir/package-lock.json")"
  local stamp="$dir/node_modules/.bootstrap-stamp"
  if [ -f "$dir/node_modules/.package-lock.json" ] && stamp_ok "$stamp" "$digest"; then
    log "跳过 $dir/node_modules（package-lock.json 未变）"
    return
  fi
  STEP="$dir：npm ci"
  log "$STEP"
  # --ignore-scripts：这些锁里除 macOS 专用的 fsevents（可选依赖，Linux 不装）外没有安装脚本；Playwright 浏览器不在这里装
  (cd "$dir" && npm ci --no-audit --no-fund --ignore-scripts --update-notifier=false --loglevel=error)
  echo "$digest" > "$stamp"
}

# ---------- 前置检查 ----------
STEP="前置检查"
command -v "$PY_BOOT" >/dev/null || { echo "bootstrap-deps: 没有 $PY_BOOT" >&2; exit 1; }
command -v npm >/dev/null || { echo "bootstrap-deps: 没有 npm" >&2; exit 1; }
log "Python：$("$PY_BOOT" --version 2>&1)；Node：$(node --version)；npm：$(npm --version)"

# ---------- 安装 ----------
py_contracts                                   # contract-test、conformance、persona-guard-test 等用 PYTHON=.venv-contracts/bin/python
py_locked services/learning contracts-editable # learning-test 及大量 *-mutation、eval-persona、deploy-check、web e2e
py_locked services/insight                     # insight-test、gate1-test、kge-calibrate/irt、web e2e
py_locked services/knowledge                   # knowledge-*、kge-compile-eval-test
py_locked services/scheduler                   # scheduler-test（锁文件引用 insight 的锁）

for d in adapters/aya adapters/zhixing adapters/wenli services/bff apps/web tools/kge-inventory tools/kge-coverage; do
  npm_locked "$d"
done

# ---------- 核对（只读）----------
STEP="核对 Playwright 浏览器"
pw_dir="${PLAYWRIGHT_BROWSERS_PATH:-}"
if [ -z "$pw_dir" ] || [ ! -d "$pw_dir" ]; then
  log "提醒：PLAYWRIGHT_BROWSERS_PATH 未设置或目录不存在；web-test 的端到端需要已安装的 Chromium（本脚本不下载）"
else
  log "Playwright 浏览器目录：$pw_dir（$(ls "$pw_dir" | tr '\n' ' ')）"
fi

STEP="核对 Postgres"
if [ -x "${PGBIN:-/usr/lib/postgresql/16/bin}/initdb" ]; then
  log "Postgres 服务端：${PGBIN:-/usr/lib/postgresql/16/bin}"
else
  log "提醒：没有找到 initdb（PGBIN 或 /usr/lib/postgresql/16/bin）；db-test 等会跳过"
fi

STEP="完成"
log "完成。运行：make check CONTRACT_STRICT=1 PYTHON=.venv-contracts/bin/python PGBIN=/usr/lib/postgresql/16/bin"
