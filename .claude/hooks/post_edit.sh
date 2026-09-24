#!/usr/bin/env bash
# PostToolUse：改动后跑快速检查；失败时退出码 2，把错误回传给 Claude 立即修正。
cd "$CLAUDE_PROJECT_DIR" || exit 0
[ -f Makefile ] || exit 0
out=$(make -s check-fast 2>&1)
if [ $? -ne 0 ]; then
  echo "$out" | tail -n 40 >&2
  exit 2
fi
exit 0
