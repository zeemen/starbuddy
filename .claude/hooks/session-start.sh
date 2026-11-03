#!/bin/bash
# 云会话启动时按锁文件恢复开发依赖（幂等；锁文件未变时秒级跳过）。只在 Claude Code 云会话运行，同步执行。
# 不安装浏览器、不装 Postgres（由环境提供；版本不符时见 scripts/bootstrap-deps.sh 的核对输出）。
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
bash scripts/bootstrap-deps.sh

# 后续命令默认用契约测试 venv 与系统 Postgres（与 CI 一致）
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PYTHON=\"$CLAUDE_PROJECT_DIR/.venv-contracts/bin/python\"" >> "$CLAUDE_ENV_FILE"
  echo 'export PGBIN="/usr/lib/postgresql/16/bin"' >> "$CLAUDE_ENV_FILE"
fi
