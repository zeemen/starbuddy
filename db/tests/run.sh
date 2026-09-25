#!/usr/bin/env bash
# 在一次性的本地 Postgres 上验证迁移与 RLS（spec：specs/learner-schema-v0.9/spec.md）。
#   1. 全部 up → 全部 down → 全部 up（迁移可回滚，开发计划 冲刺 1-B）
#   2. 按文件名顺序执行 db/tests/*.sql（合成数据，CLAUDE.md 规则 8）
# 只连本机临时实例（Unix socket，不监听 TCP），结束即删除；不连接任何共享或生产库（规则 9）。
# 退出码：0 通过；1 失败；3 本机没有 Postgres（由 Makefile 决定是否算失败）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# 固定优先用 PG16（迁移按 Postgres 16 编写），没有再退到本机最新版本
PGBIN="${PGBIN:-$( [ -x /usr/lib/postgresql/16/bin/initdb ] && echo /usr/lib/postgresql/16/bin \
                   || ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)}"
if [ -z "$PGBIN" ] && command -v pg_config >/dev/null 2>&1; then PGBIN="$(pg_config --bindir)"; fi
if [ -z "$PGBIN" ] || [ ! -x "$PGBIN/initdb" ]; then
  echo "db-test: 跳过（本机没有 Postgres 服务端；需要 postgresql-16）"
  exit 3
fi

# initdb 不允许以 root 运行：root 时切到 postgres 用户
as_pg() { if [ "$(id -u)" = "0" ]; then runuser -u postgres -- "$@"; else "$@"; fi; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-dbtest.XXXXXX")"
# 只让 postgres 用户访问：trust 认证下，目录可访问就等于能以超级用户连进测试库
if [ "$(id -u)" = "0" ]; then chown postgres "$WORK"; fi
chmod 700 "$WORK"
cleanup() {
  as_pg "$PGBIN/pg_ctl" -D "$WORK/data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

as_pg "$PGBIN/initdb" -D "$WORK/data" -A trust -U postgres -E UTF8 --no-sync >/dev/null
as_pg "$PGBIN/pg_ctl" -D "$WORK/data" -w -l "$WORK/pg.log" \
  -o "-k $WORK -c listen_addresses='' -c fsync=off -c timezone=UTC" start >/dev/null
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres starbuddy_test

psql_run() {  # 从标准输入读 SQL；-1 单事务，出错即停
  as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -v ON_ERROR_STOP=1 "$@"
}

mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
mapfile -t DOWNS < <(ls "$ROOT"/db/migrations/*.down.sql | sort -r)
[ "${#UPS[@]}" -eq "${#DOWNS[@]}" ] || { echo "db-test: 失败（up 与 down 脚本数量不一致）"; exit 1; }

apply() {
  for f in "$@"; do
    PGOPTIONS='-c client_min_messages=warning' psql_run -1 < "$f" \
      || { echo "db-test: 失败（执行 $(basename "$f") 出错）"; exit 1; }
  done
}

# 角色由人执行的 bootstrap 脚本创建（不属于迁移）
apply "$ROOT/db/bootstrap/roles.sql"
apply "${UPS[@]}"
apply "${DOWNS[@]}"
left=$(psql_run -tAc "SELECT count(*) FROM pg_namespace WHERE nspname = 'platform'" </dev/null)
[ "$left" = "0" ] || { echo "db-test: 失败（down 之后 platform schema 仍存在）"; exit 1; }
apply "${UPS[@]}"
echo "db-test: 迁移 up → down → up 通过（${#UPS[@]} 个迁移）"

# 变异测试用：在迁移之后、测试之前额外执行一段 SQL（故意放宽某条规则）
if [ -n "${DBTEST_MUTATION_SQL:-}" ]; then
  printf '%s\n' "$DBTEST_MUTATION_SQL" | psql_run -1 >/dev/null || { echo "db-test: 变异 SQL 执行失败"; exit 1; }
fi

OUT="$WORK/tests.out"
if ! cat "$ROOT"/db/tests/[0-9]*.sql | psql_run > "$OUT" 2>&1; then
  grep -E 'FAIL|ERROR' "$OUT" | head -20
  echo "db-test: 失败"
  exit 1
fi
# 角色属性断言（安全审查阻断 5）：BFF 角色带 BYPASSRLS 时，0001 必须拒绝执行
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres role_check
psql_role() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d role_check -X -q -v ON_ERROR_STOP=1 "$@"; }
psql_run -c "ALTER ROLE starbuddy_bff BYPASSRLS" </dev/null
if psql_role -1 < "$ROOT/db/migrations/0001_platform__foundation.up.sql" >/dev/null 2>&1; then
  echo "db-test: 失败（starbuddy_bff 带 BYPASSRLS 时 0001 仍然执行成功）"; exit 1
fi
psql_run -c "ALTER ROLE starbuddy_bff NOBYPASSRLS" </dev/null
echo "ok - 角色带 BYPASSRLS 时迁移拒绝执行" >> "$OUT"

n=$(grep -c 'ok - ' "$OUT" || true)
echo "db-test: 通过（${n} 项断言）"
