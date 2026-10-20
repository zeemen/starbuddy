#!/usr/bin/env bash
# 迁移执行器在一次性本机库上的端到端核对（LT-01 范围第 4 条“副本演练模式在一次性库上通过”）。
# 用压测工具同款的一次性 Postgres（db/tests/pg-ephemeral.sh：只监听 Unix socket、trust 认证、结束即删除；规则 9），
# 空库上依次核对：
#   1. plan 打印清单与确认串；确认串不一致拒绝（退出码 2、库没被改）；
#   2. 非副本标签不能演练、不能 --with-bootstrap；连接串带口令拒绝；
#   3. rehearse：up → 校验 → down → 校验 → up → 校验，全部迁移；
#   4. 再次 plan：没有待执行的；换一个标签连同一个库拒绝（防连错库）；
#   5. down 到某个版本再 up 回来（确认串核对）；改动已执行迁移的 sha256 被发现（用迁移目录的副本，不改仓库文件）。
# 退出码：0 通过；1 失败；3 本机没有 Postgres。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../../db/tests/pg-ephemeral.sh"
pg_ephemeral_start || exit $?
PY="${PYTHON:-python3}"
RUN() { "$PY" "$HERE/migrate.py" "$@"; }
fail() { echo "migrate-rehearse: 失败（$1）"; exit 1; }
export MIGRATE_DSN="postgresql://postgres@/starbuddy_test?host=$WORK"
export PSQL_BIN="$PGBIN/psql"
L=ephemeral-replica
N=$(ls "$ROOT"/db/migrations/*.up.sql | wc -l)
LAST=$(printf '%04d' "$N")

out=$(RUN plan --target-label "$L" --psql "$PSQL_BIN") || fail "plan 退出码非 0"
echo "$out" | grep -q -- "--confirm $L:up:$N:$LAST" || fail "plan 没有打印确认串"
echo "$out" | grep -q "postgresql://" && fail "plan 输出里出现了连接串"

RUN rehearse --target-label "$L" --replica --with-bootstrap --confirm "$L:rehearse:$N:9999" --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "确认串不一致没有拒绝"
RUN rehearse --target-label ephemeral --replica --confirm "ephemeral:rehearse:$N:$LAST" --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "非 -replica 标签的演练没有拒绝"
RUN up --target-label ephemeral --with-bootstrap --confirm "ephemeral:up:$N:$LAST" --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "非副本的 --with-bootstrap 没有拒绝"
MIGRATE_DSN="postgresql://postgres:synthetic-pw@/starbuddy_test?host=$WORK" RUN plan --target-label "$L" --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "带口令的连接串没有拒绝"
MIGRATE_DSN="" RUN plan --target-label "$L" --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "没有连接串没有拒绝"
left=$(psql_run -tAc "SELECT count(*) FROM pg_namespace WHERE nspname IN ('platform', 'deploy_meta')" </dev/null)
[ "$left" = "0" ] || fail "被拒绝的命令改了库"

out=$(RUN rehearse --target-label "$L" --replica --with-bootstrap --confirm "$L:rehearse:$N:$LAST" --psql "$PSQL_BIN") \
  || { echo "$out" | tail -5; fail "副本演练失败"; }
echo "$out" | grep -q "副本演练通过" || fail "副本演练没有报告通过"
n=$(psql_run -tAc "SELECT count(*) FROM deploy_meta.schema_migrations" </dev/null)
[ "$n" = "$N" ] || fail "演练后版本表不是 $N 条"

out=$(RUN plan --target-label "$L" --psql "$PSQL_BIN") || fail "演练后 plan 失败"
echo "$out" | grep -q -- "--confirm $L:up:0:none" || fail "演练后仍有待执行的迁移"
RUN plan --target-label other-replica --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "标签不一致没有拒绝"

TO=$(printf '%04d' $((N - 3)))
RUN down --target-label "$L" --to "$TO" --confirm "$L:down:3:$TO" --psql "$PSQL_BIN" >/dev/null || fail "down 到 $TO 失败"
n=$(psql_run -tAc "SELECT count(*) FROM deploy_meta.schema_migrations" </dev/null)
[ "$n" = "$((N - 3))" ] || fail "down 之后版本表条数不对"
RUN up --target-label "$L" --confirm "$L:up:3:$LAST" --psql "$PSQL_BIN" >/dev/null || fail "重新 up 失败"

# 已执行迁移的文件被改过：在迁移目录的副本里改一个已执行的 up 文件，plan 必须拒绝
COPY="$WORK/migrations-copy"; mkdir -p "$COPY"; cp "$ROOT"/db/migrations/*.sql "$COPY/"
first=$(ls "$COPY"/*.up.sql | sort | head -1); printf '\n-- 改动\n' >> "$first"
RUN plan --target-label "$L" --migrations "$COPY" --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "已执行迁移被改过没有发现"
# S9：不是本执行器管理的库（已有 platform schema 而没有 deploy_meta）——plan 只提示，up 拒绝；非回环主机的演练在连库前拒绝
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres starbuddy_unmanaged
for f in "$ROOT/db/bootstrap/roles.sql" "$ROOT/db/migrations/0001_platform__foundation.up.sql"; do
  PGOPTIONS='-c client_min_messages=warning' as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_unmanaged -X -q -v ON_ERROR_STOP=1 -1 -f "$f" \
    >/dev/null || fail "准备未接管库失败"
done
U_DSN="postgresql://postgres@/starbuddy_unmanaged?host=$WORK"
out=$(MIGRATE_DSN="$U_DSN" RUN plan --target-label unmanaged-replica --psql "$PSQL_BIN") || fail "未接管库的 plan 失败"
echo "$out" | grep -q "不是本执行器管理的库" || fail "未接管库的 plan 没有提示"
MIGRATE_DSN="$U_DSN" RUN up --target-label unmanaged-replica --confirm "unmanaged-replica:up:$N:$LAST" --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "未接管库的 up 没有拒绝"
MIGRATE_DSN="postgresql://postgres@10.0.0.5/starbuddy_test" RUN rehearse --target-label "$L" --replica --confirm "$L:rehearse:0:none" \
  --psql "$PSQL_BIN" >/dev/null 2>&1
[ $? -eq 2 ] || fail "非回环主机的副本演练没有拒绝"
echo "migrate-rehearse: 通过（一次性库上 $N 个迁移：plan、确认串、拒绝路径、副本演练 up → down → up、down / up 到指定版本、改动检测、未接管库与非回环主机拒绝）"
