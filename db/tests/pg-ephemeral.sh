#!/usr/bin/env bash
# 一次性的本机 Postgres：只监听 Unix socket（不开 TCP），结束即删除；不连接任何共享或生产库（CLAUDE.md 规则 9）。
# 两种用法：
#   1. 被 source（db/tests/run.sh）：提供 pg_ephemeral_start、psql_run、apply、as_pg，以及 PGBIN、WORK、ROOT。
#   2. 直接执行：bash db/tests/pg-ephemeral.sh -- <命令…>
#      起实例 → 执行角色 bootstrap 与全部 up 迁移 →（可选）执行 $PGEPHEMERAL_EXTRA_SQL →
#      以 PGHOST / PGDATABASE / PGUSER 环境变量运行命令 → 删除实例。命令的退出码原样返回。
# 退出码：3 表示本机没有 Postgres（由 Makefile 决定是否算失败）。

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# 固定优先用 PG16（迁移按 Postgres 16 编写），没有再退到本机最新版本
PGBIN="${PGBIN:-$( [ -x /usr/lib/postgresql/16/bin/initdb ] && echo /usr/lib/postgresql/16/bin \
                   || ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)}"
if [ -z "$PGBIN" ] && command -v pg_config >/dev/null 2>&1; then PGBIN="$(pg_config --bindir)"; fi

# initdb 不允许以 root 运行：root 时切到 postgres 用户
as_pg() { if [ "$(id -u)" = "0" ]; then runuser -u postgres -- "$@"; else "$@"; fi; }

psql_run() {  # 从标准输入读 SQL；出错即停
  as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -v ON_ERROR_STOP=1 "$@"
}

apply() {  # 每个文件单事务执行
  for f in "$@"; do
    PGOPTIONS='-c client_min_messages=warning' psql_run -1 < "$f" \
      || { echo "pg-ephemeral: 失败（执行 $(basename "$f") 出错）"; return 1; }
  done
}

pg_ephemeral_start() {  # 成功返回 0；没有 Postgres 返回 3
  if [ -z "$PGBIN" ] || [ ! -x "$PGBIN/initdb" ]; then
    echo "pg-ephemeral: 跳过（本机没有 Postgres 服务端；需要 postgresql-16）"
    return 3
  fi
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-pg.XXXXXX")"
  # 只让 postgres 用户访问：trust 认证下，目录可访问就等于能以超级用户连进测试库
  if [ "$(id -u)" = "0" ]; then chown postgres "$WORK"; fi
  chmod 700 "$WORK"
  # PGEPHEMERAL_KEEP=1：实例在脚本退出后继续运行（渗透测试环境 db/pentest/up.sh 用），由调用方负责销毁
  if [ "${PGEPHEMERAL_KEEP:-0}" != "1" ]; then trap pg_ephemeral_stop EXIT; fi
  as_pg "$PGBIN/initdb" -D "$WORK/data" -A trust -U postgres -E UTF8 --no-sync >/dev/null
  as_pg "$PGBIN/pg_ctl" -D "$WORK/data" -w -l "$WORK/pg.log" \
    -o "-k $WORK -c listen_addresses='' -c fsync=off -c timezone=UTC -c max_connections=200" start >/dev/null
  as_pg "$PGBIN/createdb" -h "$WORK" -U postgres starbuddy_test
}

pg_ephemeral_stop() {
  [ -n "${WORK:-}" ] || return 0
  as_pg "$PGBIN/pg_ctl" -D "$WORK/data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$WORK"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  [ "${1:-}" = "--" ] && shift
  [ $# -gt 0 ] || { echo "用法：bash db/tests/pg-ephemeral.sh -- <命令…>"; exit 2; }
  pg_ephemeral_start || exit $?
  apply "$ROOT/db/bootstrap/roles.sql"
  mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
  apply "${UPS[@]}"
  if [ -n "${PGEPHEMERAL_EXTRA_SQL:-}" ]; then
    printf '%s\n' "$PGEPHEMERAL_EXTRA_SQL" | psql_run -1 >/dev/null || { echo "pg-ephemeral: 附加 SQL 执行失败"; exit 1; }
  fi
  # root 运行时，测试进程（root）也能进 700 的 socket 目录；非 root 时目录属于当前用户
  set +e
  PGHOST="$WORK" PGDATABASE=starbuddy_test PGUSER=postgres "$@"
  rc=$?
  exit $rc
fi
