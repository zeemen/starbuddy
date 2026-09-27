#!/usr/bin/env bash
# 在一次性的本地 Postgres 上验证迁移与 RLS（spec：specs/learner-schema-v0.9/spec.md）。
#   1. 全部 up → 全部 down → 全部 up（迁移可回滚，开发计划 冲刺 1-B）
#   2. 按文件名顺序执行 db/tests/*.sql（合成数据，CLAUDE.md 规则 8）
# 只连本机临时实例（Unix socket，不监听 TCP），结束即删除；不连接任何共享或生产库（规则 9）。
# 退出码：0 通过；1 失败；3 本机没有 Postgres（由 Makefile 决定是否算失败）。
set -euo pipefail

# 启动、停止一次性实例的公共部分与 BFF 集成测试共用（db/tests/pg-ephemeral.sh）
. "$(dirname "$0")/pg-ephemeral.sh"
pg_ephemeral_start || exit $?

mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
mapfile -t DOWNS < <(ls "$ROOT"/db/migrations/*.down.sql | sort -r)
[ "${#UPS[@]}" -eq "${#DOWNS[@]}" ] || { echo "db-test: 失败（up 与 down 脚本数量不一致）"; exit 1; }

apply_or_exit() { apply "$@" || exit 1; }

# 角色由人执行的 bootstrap 脚本创建（不属于迁移）
apply_or_exit "$ROOT/db/bootstrap/roles.sql"
apply_or_exit "${UPS[@]}"
apply_or_exit "${DOWNS[@]}"
left=$(psql_run -tAc "SELECT count(*) FROM pg_namespace WHERE nspname IN ('platform', 'insight', 'kge')" </dev/null)
[ "$left" = "0" ] || { echo "db-test: 失败（down 之后 platform、insight 或 kge schema 仍存在）"; exit 1; }
apply_or_exit "${UPS[@]}"
echo "db-test: 迁移 up → down → up 通过（${#UPS[@]} 个迁移）"

apply_mutation() {
  [ -n "${DBTEST_MUTATION_SQL:-}" ] || return 0
  printf '%s\n' "$DBTEST_MUTATION_SQL" | psql_run -1 >/dev/null || { echo "db-test: 变异 SQL 执行失败"; exit 1; }
}

# 攻击面变异专用（db/tests/mutations.sh 按变异设置 DBTEST_ATTACK_ONLY=1）：证明变异是被 80_attack_surface 本身发现的，
# 而不是被 20–70 的某条逐表断言或快照先拦下（安全审查阻断 2）。做法：
#   先加载 10_fixtures 与渗透测试夹具（4 户、16 个账号），再执行变异，然后按变异后的状态重新生成快照
#   （DBTEST_KEEP_SNAPSHOT=1 时不重新生成，用来证明快照本身能发现），最后只跑 00_helpers 与 80。
if [ "${DBTEST_ATTACK_ONLY:-0}" = "1" ]; then
  apply_or_exit "$ROOT/db/tests/10_fixtures.sql" "$ROOT/db/pentest/fixtures.sql"
  apply_mutation
  EXPECTED="$ROOT/db/tests/expected"
  if [ "${DBTEST_KEEP_SNAPSHOT:-0}" != "1" ]; then
    EXPECTED="$WORK/expected"; mkdir -p "$EXPECTED"; [ "$(id -u)" = "0" ] && chown postgres "$EXPECTED"
    psql_run -v testdir="$ROOT/db/tests" >/dev/null <<SQL || { echo "db-test: 重新生成快照失败"; exit 1; }
\i :testdir/snapshot/privileges.sql
\i :testdir/snapshot/policies.sql
\o $EXPECTED/privileges.txt
\qecho :snap_privileges
\o $EXPECTED/policies.txt
\qecho :snap_policies
SQL
  fi
  OUT="$WORK/tests.out"
  if ! cat "$ROOT/db/tests/00_helpers.sql" "$ROOT/db/tests/80_attack_surface.sql" |
       psql_run -v testdir="$ROOT/db/tests" -v expecteddir="$EXPECTED" > "$OUT" 2>&1; then
    grep -E 'FAIL|ERROR' "$OUT" | head -20
    echo "db-test: 失败"
    exit 1
  fi
  echo "db-test: 通过（只跑攻击面，$(grep -c 'ok - ' "$OUT") 项断言）"
  exit 0
fi

# 变异测试用：在迁移之后、测试之前额外执行一段 SQL（故意放宽某条规则）
apply_mutation

OUT="$WORK/tests.out"
if ! cat "$ROOT"/db/tests/[0-9]*.sql | psql_run -v testdir="$ROOT/db/tests" -v expecteddir="$ROOT/db/tests/expected" > "$OUT" 2>&1; then
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
# 嵌套继承（安全审查 N1）：登录角色经两层中间角色同时继承 bff 与 learning，0001 也必须拒绝执行
psql_run </dev/null -c "CREATE ROLE t_grp_a IN ROLE starbuddy_bff; CREATE ROLE t_grp_b IN ROLE starbuddy_learning;
                        CREATE ROLE t_app_login LOGIN IN ROLE t_grp_a, t_grp_b;"
if psql_role -1 < "$ROOT/db/migrations/0001_platform__foundation.up.sql" >/dev/null 2>&1; then
  echo "db-test: 失败（登录角色间接同时继承 bff 与 learning 时 0001 仍然执行成功）"; exit 1
fi
psql_run </dev/null -c "DROP ROLE t_app_login; DROP ROLE t_grp_a; DROP ROLE t_grp_b;"
echo "ok - 登录角色间接同时继承 bff 与 learning 时迁移拒绝执行" >> "$OUT"

n=$(grep -c 'ok - ' "$OUT" || true)
echo "db-test: 通过（${n} 项断言）"
