#!/usr/bin/env bash
# 重新生成授权与策略快照（db/tests/expected/）。只在一次性本地 Postgres 上跑全部 up 迁移，不跑测试夹具。
# 任何授权或策略变动都必须同时更新快照；评审时把快照的 diff 当作 RLS 变更对待（3-B spec 必停项说明）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/db/tests/expected"
mkdir -p "$OUT"
bash "$ROOT/db/tests/pg-ephemeral.sh" -- bash -c '
  psql -X -q -v ON_ERROR_STOP=1 -v testdir="$0/db/tests" <<SQL
\i :testdir/snapshot/privileges.sql
\i :testdir/snapshot/policies.sql
\o $1/privileges.txt
\qecho :snap_privileges
\o $1/policies.txt
\qecho :snap_policies
SQL
' "$ROOT" "$OUT"
echo "update-snapshots: 已写出 $(wc -l < "$OUT/privileges.txt") 条授权、$(wc -l < "$OUT/policies.txt") 条策略"
