#!/usr/bin/env bash
# 在一次性的本地 Postgres 上验证迁移与 RLS（spec：specs/learner-schema-v0.9/spec.md）。
#   1. 全部 up → 全部 down → 全部 up，再把最近八个迁移逐个单独 down → up（迁移可回滚，开发计划 冲刺 1-B）；
#      最新四个迁移（0049–0052）另在跑完全部测试、库里有数据时再单独 down → up 一次（文件末尾）
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
left=$(psql_run -tAc "SELECT count(*) FROM pg_namespace WHERE nspname IN ('platform', 'insight', 'kge', 'policy')" </dev/null)
[ "$left" = "0" ] || { echo "db-test: 失败（down 之后 platform、insight、kge 或 policy schema 仍存在）"; exit 1; }
apply_or_exit "${UPS[@]}"
echo "db-test: 迁移 up → down → up 通过（${#UPS[@]} 个迁移）"
# 最新迁移单独 down → up（只回滚最后一步时，前面的迁移不受影响）；再把最近八个迁移逐个回滚到它为止再重新执行
# （一个 PR 加了几个迁移时每个都要能单独回滚，specs/policy-decisions-v0.9.1 验收：PR1 的 0045–0048、PR3 的 0049、0050 与 PR4 的 0051、0052）
for k in 1 2 3 4 5 6 7 8; do
  apply_or_exit "${DOWNS[@]:0:k}"
  apply_or_exit "${UPS[@]: -k}"
  echo "db-test: 迁移 $(basename "${UPS[${#UPS[@]}-k]}") 单独 down → up 通过（先回滚其后的 $((k - 1)) 个）"
done

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
# 发布与骨架写入的并发（KGE schema v0.9 安全审查 B1）：两个会话在同一实例上交错执行，单会话的 76 测不出来。
# 夹具：两个干净的数学草稿版本 kc、kd，各有一个带边界、层次与已核对投影的知识节点（可以直接发布）。
kge_fixture() {  # $1 = 版本 uuid，$2 = 版本号
  printf '%s\n' "SET ROLE starbuddy_kge;
    INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('$1', 'aya', 2022, '$2');
    INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
      ('$1', 'MATH.RACE', 'domain', NULL, NULL, 'r', '{}', '{}', '{}', '{}'),
      ('$1', 'MATH.RACE.T', 'topic', 'MATH.RACE', NULL, 'r', '{}', '{}', '{}', '{}'),
      ('$1', 'MATH.RACE.T.A', 'node', 'MATH.RACE.T', 'kp', 'r', '{a}', '{b}', '{c}', '{understand}');
    INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
      VALUES ('$1', 'MATH.RACE.T.A', 'bubian-2024', 2024, 'v', 'c', 1, 1);" | psql_run >/dev/null
}
KC=c0000000-0000-4000-8000-00000000c0c0; KD=d0000000-0000-4000-8000-00000000d0d0
kge_fixture "$KC" 7.0.0 && kge_fixture "$KD" 7.0.1 || { echo "db-test: 失败（并发测试夹具）"; exit 1; }
# 0019 起发布由 staff 经 starbuddy_review 执行，且要先签字（10 号夹具的 staff 账号）
AS_STAFF="SELECT set_config('app.account_id', '5f000000-0000-4000-8000-00000000005f', false); SET ROLE starbuddy_review;"
AS_STAFF2="SELECT set_config('app.account_id', '5e000000-0000-4000-8000-00000000005e', false); SET ROLE starbuddy_review;"
for v in "$KC" "$KD"; do  # 首次发布算结构性变更：两名 staff 签字（0019）
  printf '%s\n' "$AS_STAFF2 SELECT kge.sign_review('map_version', '$v', 'approved', 'high', 'race'); RESET ROLE;
                  $AS_STAFF SELECT kge.sign_review('map_version', '$v', 'approved', 'high', 'race');" | psql_run >/dev/null \
    || { echo "db-test: 失败（并发测试签字）"; exit 1; }
done
# 场景 1：会话 A 往草稿写入一个孤点并持有事务；会话 B 此时发布。发布必须等 A 提交后再校验，因而拒绝
printf '%s\n' "BEGIN; SET ROLE starbuddy_kge;
  INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels)
    VALUES ('$KC', 'MATH.RACE.T.ORPHAN', 'node', 'MATH.RACE.T', 'kp', 'r', '{a}', '{b}', '{c}', '{understand}');
  SELECT pg_sleep(1.5); COMMIT;" | psql_run >/dev/null 2>"$WORK/race_a.err" &
sleep 0.5
if printf '%s\n' "$AS_STAFF SELECT kge.publish_map_version('$KC');" | psql_run >/dev/null 2>"$WORK/race_b.err"; then
  wait; echo "db-test: 失败（发布没有等待进行中的骨架写入，带孤点的版本被发布）"; exit 1
fi
wait
grep -q '阻断问题' "$WORK/race_b.err" || { echo "db-test: 失败（并发场景 1 的发布不是被校验拒绝：$(cat "$WORK/race_b.err")）"; exit 1; }
echo "ok - 并发：发布等待进行中的骨架写入提交后再校验，带孤点的版本被拒绝" >> "$OUT"
# 场景 2：会话 B 发布后事务未提交；会话 A 此时往该版本写节点。写入必须等发布提交，然后按已发布状态拒绝
printf '%s\n' "BEGIN; $AS_STAFF SELECT kge.publish_map_version('$KD'); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_c.err" &
sleep 0.5
if printf '%s\n' "SET ROLE starbuddy_kge;
     INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels)
       VALUES ('$KD', 'MATH.RACE.T.LATE', 'node', 'MATH.RACE.T', 'kp', 'r', '{a}', '{b}', '{c}', '{understand}');" \
   | psql_run >/dev/null 2>"$WORK/race_d.err"; then
  wait; echo "db-test: 失败（发布进行中写入的节点进入了已发布版本）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 2 的发布本身失败：$(cat "$WORK/race_c.err")）"; exit 1; }
grep -q '已发布或已撤下' "$WORK/race_d.err" || { echo "db-test: 失败（并发场景 2 的写入不是被冻结拒绝：$(cat "$WORK/race_d.err")）"; exit 1; }
echo "ok - 并发：发布进行中的骨架写入等发布提交后按已发布状态拒绝" >> "$OUT"
# 场景 3（0038）：两名 staff 同时对同一页面同一提交签字。签字函数的咨询锁让后一个等前一个提交，再分到第 2 号签字位；
# 去掉锁时两人都算出第 1 号，后一个撞唯一约束失败
PC="3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a"
printf '%s\n' "BEGIN; $AS_STAFF SELECT kge.sign_page_review('MATH.RACE.T.PG', '$PC', 'approved', 'high', 'race'); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_e.err" &
sleep 0.5
printf '%s\n' "$AS_STAFF2 SELECT kge.sign_page_review('MATH.RACE.T.PG', '$PC', 'approved', 'high', 'race');" \
  | psql_run >/dev/null 2>"$WORK/race_f.err" || { wait; echo "db-test: 失败（并发页面签字：后一名 staff 的签字失败：$(cat "$WORK/race_f.err")）"; exit 1; }
wait || { echo "db-test: 失败（并发页面签字：前一名 staff 的签字失败：$(cat "$WORK/race_e.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT string_agg(page_signer_no::text, ',' ORDER BY page_signer_no) FROM kge.content_review WHERE object_type = 'page' AND page_commit = '$PC'" </dev/null)" = "1,2" ] \
  || { echo "db-test: 失败（并发页面签字的签字序号不是 1、2）"; exit 1; }
echo "ok - 并发：两名 staff 同时签同一页面同一提交，按咨询锁串行分到签字位 1、2" >> "$OUT"

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

# 0011 的角色断言（安全审查建议 1）：在只执行到 0010 的库上，kge 角色属性不安全、与其他应用角色共存、authz 有成员时，0011 都必须拒绝执行
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres kge_role_check
psql_kge() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d kge_role_check -X -q -v ON_ERROR_STOP=1 "$@"; }
for f in "${UPS[@]}"; do
  case "$(basename "$f")" in 0011_*|0012_*|0017_*|0018_*|0019_*|0024_*|0026_*|0028_*|0038_*|0039_*|0040_*|0041_*|0042_*|0043_*|0047_*|0048_*|0049_*|0050_*|0052_*) continue ;; esac  # 0012、0017–0019、0024、0026、0028、0038、0043 依赖 0011 的 kge 表；0039、0040 依赖 0018（0039 还依赖 0012）；0041 依赖 0040；0042 依赖 0039；0047 依赖 0041；0048 依赖 0018 的只追加函数；0049、0050、0052 依赖 0048
  psql_kge -1 < "$f" >/dev/null 2>&1 || { echo "db-test: 失败（角色断言测试库执行 $(basename "$f") 出错）"; exit 1; }
done
kge_must_refuse() {  # $1 = 布置 SQL，$2 = 撤销 SQL，$3 = 说明
  psql_run -c "$1" </dev/null
  if psql_kge -1 < "$ROOT/db/migrations/0011_kge__knowledge_map.up.sql" >/dev/null 2>&1; then
    psql_run -c "$2" </dev/null; echo "db-test: 失败（$3 时 0011 仍然执行成功）"; exit 1
  fi
  psql_run -c "$2" </dev/null
  echo "ok - $3 时 0011 拒绝执行" >> "$OUT"
}
kge_must_refuse "ALTER ROLE starbuddy_kge BYPASSRLS" "ALTER ROLE starbuddy_kge NOBYPASSRLS" "starbuddy_kge 带 BYPASSRLS"
kge_must_refuse "ALTER ROLE starbuddy_kge LOGIN" "ALTER ROLE starbuddy_kge NOLOGIN" "starbuddy_kge 可以登录"
kge_must_refuse "CREATE ROLE t_kge_mid IN ROLE starbuddy_kge; CREATE ROLE t_kge_login LOGIN IN ROLE t_kge_mid, starbuddy_learning;" \
                "DROP ROLE t_kge_login; DROP ROLE t_kge_mid;" "登录角色经中间角色同时继承 kge 与 learning"
kge_must_refuse "CREATE ROLE t_authz_member IN ROLE starbuddy_authz;" "DROP ROLE t_authz_member;" "starbuddy_authz 有成员"
psql_kge -1 < "$ROOT/db/migrations/0011_kge__knowledge_map.up.sql" >/dev/null 2>&1 \
  || { echo "db-test: 失败（角色都正常时 0011 反而执行失败）"; exit 1; }
echo "ok - 角色都正常时 0011 可以执行（上面的拒绝不是别的原因）" >> "$OUT"

# 0026 的角色断言（specs/kge-03-compile-layer）：知识服务只读角色属性不安全、或与写入角色 / 个人层角色共存时，0026 必须拒绝执行
for f in "${UPS[@]}"; do
  case "$(basename "$f")" in 0012_*|0017_*|0018_*|0019_*|0024_*)
    psql_kge -1 < "$f" >/dev/null 2>&1 || { echo "db-test: 失败（角色断言测试库补执行 $(basename "$f") 出错）"; exit 1; } ;;
  esac
done
kge_read_must_refuse() {  # $1 = 布置 SQL，$2 = 撤销 SQL，$3 = 说明
  psql_run -c "$1" </dev/null
  if psql_kge -1 < "$ROOT/db/migrations/0026_kge__knowledge_reader.up.sql" >/dev/null 2>&1; then
    psql_run -c "$2" </dev/null; echo "db-test: 失败（$3 时 0026 仍然执行成功）"; exit 1
  fi
  psql_run -c "$2" </dev/null
  echo "ok - $3 时 0026 拒绝执行" >> "$OUT"
}
kge_read_must_refuse "ALTER ROLE starbuddy_kge_read BYPASSRLS" "ALTER ROLE starbuddy_kge_read NOBYPASSRLS" "starbuddy_kge_read 带 BYPASSRLS"
kge_read_must_refuse "ALTER ROLE starbuddy_kge_read LOGIN" "ALTER ROLE starbuddy_kge_read NOLOGIN" "starbuddy_kge_read 可以登录"
kge_read_must_refuse "CREATE ROLE t_kr_login LOGIN IN ROLE starbuddy_kge_read, starbuddy_kge;" "DROP ROLE t_kr_login;" "登录角色同时继承只读角色与写入角色"
kge_read_must_refuse "CREATE ROLE t_kr_mid IN ROLE starbuddy_kge_read; CREATE ROLE t_kr_login LOGIN IN ROLE t_kr_mid, starbuddy_learning;" \
                     "DROP ROLE t_kr_login; DROP ROLE t_kr_mid;" "登录角色经中间角色同时继承只读角色与 learning"
kge_read_must_refuse "CREATE ROLE t_kr_login LOGIN IN ROLE starbuddy_kge_read, pg_read_all_data;" "DROP ROLE t_kr_login;" "登录角色同时继承只读角色与 pg_read_all_data"
kge_read_must_refuse "CREATE ROLE t_kr_login LOGIN CREATEROLE IN ROLE starbuddy_kge_read;" "DROP ROLE t_kr_login;" "继承只读角色的登录角色有 CREATEROLE"
psql_kge -1 < "$ROOT/db/migrations/0026_kge__knowledge_reader.up.sql" >/dev/null 2>&1 \
  || { echo "db-test: 失败（角色都正常时 0026 反而执行失败）"; exit 1; }
echo "ok - 角色都正常时 0026 可以执行（上面的拒绝不是别的原因）" >> "$OUT"

# 0035 的角色断言（specs/sie-03-dispute-reconcile 安全审查）：删除执行身份 starbuddy_eraser 属性不安全、有成员或是别的角色的成员时，0035 必须拒绝执行
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres eraser_role_check
psql_era() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d eraser_role_check -X -q -v ON_ERROR_STOP=1 "$@"; }
for f in "${UPS[@]}"; do
  case "$(basename "$f")" in 0035_*) break ;; esac  # 只执行 0035 之前的（0036 起依赖 0035 的清单）
  psql_era -1 < "$f" >/dev/null 2>&1 || { echo "db-test: 失败（角色断言测试库执行 $(basename "$f") 出错）"; exit 1; }
done
E35="$(ls "$ROOT"/db/migrations/0035_*.up.sql)"
eraser_must_refuse() {  # $1 = 布置 SQL，$2 = 撤销 SQL，$3 = 说明
  psql_run -c "$1" </dev/null
  if psql_era -1 < "$E35" >/dev/null 2>&1; then
    psql_run -c "$2" </dev/null; echo "db-test: 失败（$3 时 0035 仍然执行成功）"; exit 1
  fi
  psql_run -c "$2" </dev/null
  echo "ok - $3 时 0035 拒绝执行" >> "$OUT"
}
eraser_must_refuse "ALTER ROLE starbuddy_eraser LOGIN" "ALTER ROLE starbuddy_eraser NOLOGIN" "starbuddy_eraser 可以登录"
eraser_must_refuse "ALTER ROLE starbuddy_eraser CREATEROLE" "ALTER ROLE starbuddy_eraser NOCREATEROLE" "starbuddy_eraser 能建角色"
eraser_must_refuse "CREATE ROLE t_era_member IN ROLE starbuddy_eraser;" "DROP ROLE t_era_member;" "starbuddy_eraser 有成员"
eraser_must_refuse "GRANT starbuddy_bff TO starbuddy_eraser;" "REVOKE starbuddy_bff FROM starbuddy_eraser;" "starbuddy_eraser 继承别的应用角色"
psql_era -1 < "$E35" >/dev/null 2>&1 || { echo "db-test: 失败（角色都正常时 0035 反而执行失败）"; exit 1; }
echo "ok - 角色都正常时 0035 可以执行（上面的拒绝不是别的原因）" >> "$OUT"

# 0041 的角色断言（G 流第二部分 PR C）：在执行到 0040 的空库上（没有考情数据，数据前提检查必然通过），签字身份 review 与写入方 kge、
# 学习服务 learning、BFF 共存，或 authz 有成员时，0041 都必须拒绝执行；每次都核对报错是角色检查本身（不是别的原因让它失败）
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres policy_role_check
psql_pol() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d policy_role_check -X -q -v ON_ERROR_STOP=1 "$@"; }
U41="$(ls "$ROOT"/db/migrations/0041_*.up.sql)"
for f in "${UPS[@]}"; do
  [ "$f" = "$U41" ] && break  # 只执行 0041 之前的
  psql_pol -1 < "$f" >/dev/null 2>&1 || { echo "db-test: 失败（0041 角色断言测试库执行 $(basename "$f") 出错）"; exit 1; }
done
policy_must_refuse() {  # $1 = 布置 SQL，$2 = 撤销 SQL，$3 = 说明，$4 = 报错里必须出现的文字
  psql_run -c "$1" </dev/null
  if psql_pol -1 < "$U41" >/dev/null 2>"$WORK/role41.err"; then
    psql_run -c "$2" </dev/null; echo "db-test: 失败（$3 时 0041 仍然执行成功）"; exit 1
  fi
  psql_run -c "$2" </dev/null
  grep -qF "$4" "$WORK/role41.err" || { echo "db-test: 失败（$3 时 0041 不是被角色检查拒绝：$(cat "$WORK/role41.err")）"; exit 1; }
  echo "ok - $3 时 0041 拒绝执行" >> "$OUT"
}
policy_must_refuse "CREATE ROLE t_pol_login LOGIN IN ROLE starbuddy_kge, starbuddy_review;" "DROP ROLE t_pol_login;" \
                   "登录角色同时继承 kge 与 review" "不能同时继承 starbuddy_kge"
policy_must_refuse "CREATE ROLE t_pol_mid IN ROLE starbuddy_review; CREATE ROLE t_pol_login LOGIN IN ROLE t_pol_mid, starbuddy_learning;" \
                   "DROP ROLE t_pol_login; DROP ROLE t_pol_mid;" "登录角色经中间角色同时继承 learning 与 review" "不能同时继承 starbuddy_learning"
policy_must_refuse "CREATE ROLE t_pol_login LOGIN IN ROLE starbuddy_bff, starbuddy_review;" "DROP ROLE t_pol_login;" \
                   "登录角色同时继承 bff 与 review" "不能同时继承 starbuddy_bff"
policy_must_refuse "CREATE ROLE t_pol_member IN ROLE starbuddy_authz;" "DROP ROLE t_pol_member;" "starbuddy_authz 有成员" "starbuddy_authz 必须 NOLOGIN"
psql_pol -1 < "$U41" >/dev/null 2>"$WORK/role41.err" \
  || { echo "db-test: 失败（角色都正常时 0041 反而执行失败：$(cat "$WORK/role41.err")）"; exit 1; }
echo "ok - 角色都正常时 0041 可以执行（上面的拒绝不是别的原因）" >> "$OUT"

# 0047 的归档与回填（specs/policy-decisions-v0.9.1 PR1）：在执行到 0046 的库上，经签字发布三条合成指标生招录行
# （两所合成学校在 batch_1 上控制线不同，另一条在 batch_2），0047 必须以明确信息中止且库不变（控制线按城市一条）；
# 撤下 batch_1 的一条与 batch_2 的那条后 0047 执行成功：三条旧值原样进归档表，只回填已发布行（batch_1 一条控制线草稿，
# 值与来源取已发布行），已撤下行的值只留在归档表（batch_2 不回填），删掉旧列
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres policy_backfill_check
psql_bf() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d policy_backfill_check -X -q -v ON_ERROR_STOP=1 "$@"; }
U47="$(ls "$ROOT"/db/migrations/0047_*.up.sql)"
for f in "${UPS[@]}"; do
  [ "$f" = "$U47" ] && break  # 只执行 0047 之前的
  psql_bf -1 < "$f" >/dev/null 2>&1 || { echo "db-test: 失败（0047 回填测试库执行 $(basename "$f") 出错）"; exit 1; }
done
BF_SF1=5f000000-0000-4000-8000-00000000005f; BF_SF2=f5000000-0000-4000-8000-0000000000f5
BD=f7940000-0000-4000-8000-0000000000d1; BS=f7940000-0000-4000-8000-0000000000e1
BH1=f7940000-0000-4000-8000-0000000000f1; BH2=f7940000-0000-4000-8000-0000000000f2
BL1=f7940000-0000-4000-8000-0000000000b1; BL2=f7940000-0000-4000-8000-0000000000b2; BL3=f7940000-0000-4000-8000-0000000000b3
bf_sign2() {  # $1 = 一条签字语句；两名合成 staff 各签一次
  printf "SELECT set_config('app.account_id', '%s', false); SET ROLE starbuddy_review; %s RESET ROLE;
          SELECT set_config('app.account_id', '%s', false); SET ROLE starbuddy_review; %s RESET ROLE;" "$BF_SF2" "$1" "$BF_SF1" "$1"
}
bf_rec() { printf "SELECT policy.sign_record('%s', '%s', 'approved', policy.object_digest('%s', '%s'));" "$1" "$2" "$1" "$2"; }
{ printf '%s\n' "INSERT INTO platform.account (id, kind) VALUES ('$BF_SF1', 'staff'), ('$BF_SF2', 'staff');
    INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.synthcity.example', '合成教育局', 'staff-001');
    SET ROLE starbuddy_kge;
    INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
      VALUES ('$BD', 'synthcity', '合成教育局', '合成〔2026〕1号', '合成回填测试（非真实数据）', '2026-03-01', 'https://jyj.synthcity.example/doc/bf', 2027, '2026-09-01');
    RESET ROLE;"
  bf_sign2 "$(bf_rec source_document "$BD")"
  printf '%s\n' "SET ROLE starbuddy_kge;
    INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES ('$BS', 'synthcity', 2027, '1.0.0', '$BD');
    INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
      VALUES ('$BS', 'chinese', true, 100, 'city', 'closed_book', 9);
    INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track, control_line_basis) VALUES
      ('$BS', 'batch_1', 1, 'quota_allocation', 'total_score'), ('$BS', 'batch_2', 2, 'quota_allocation', 'total_score');
    INSERT INTO policy.school (id, city, code, public_name, level_code, source_document_id) VALUES
      ('$BH1', 'synthcity', 'synth-bf-a', '合成高中甲（非真实）', 'level_a', '$BD'), ('$BH2', 'synthcity', 'synth-bf-b', '合成高中乙（非真实）', 'level_a', '$BD');
    RESET ROLE;"
  bf_sign2 "SELECT policy.sign_scheme('$BS', 'approved', policy.object_digest('exam_scheme', '$BS'));"
  printf '%s\n' "SELECT set_config('app.account_id', '$BF_SF1', false); SET ROLE starbuddy_review; SELECT policy.publish_scheme('$BS'); RESET ROLE;"
  bf_sign2 "$(bf_rec school "$BH1") $(bf_rec school "$BH2")"
  printf '%s\n' "SET ROLE starbuddy_kge;
    INSERT INTO policy.admission_line (id, school_id, year, batch_code, track, plan_count, control_line, source_document_id) VALUES
      ('$BL1', '$BH1', 2027, 'batch_1', 'quota_allocation', 50, 80, '$BD'), ('$BL2', '$BH2', 2027, 'batch_1', 'quota_allocation', 40, 85, '$BD'),
      ('$BL3', '$BH1', 2027, 'batch_2', 'quota_allocation', 30, 75, '$BD');
    RESET ROLE;"
  bf_sign2 "$(bf_rec admission_line "$BL1") $(bf_rec admission_line "$BL2") $(bf_rec admission_line "$BL3")"
} | psql_bf >/dev/null 2>"$WORK/bf.err" || { echo "db-test: 失败（0047 回填测试夹具：$(cat "$WORK/bf.err")）"; exit 1; }
[ "$(psql_bf -tAc "SELECT string_agg(status::text, ',') FROM policy.admission_line" </dev/null)" = "published,published,published" ] \
  || { echo "db-test: 失败（0047 回填测试夹具：三条招录行没有都发布）"; exit 1; }
if psql_bf -1 < "$U47" >/dev/null 2>"$WORK/up47.err"; then
  echo "db-test: 失败（已发布招录行的控制线不一致时 0047 仍然执行成功）"; exit 1
fi
grep -q '0047 中止' "$WORK/up47.err" || { echo "db-test: 失败（0047 不是被回填检查中止：$(cat "$WORK/up47.err")）"; exit 1; }
[ "$(psql_bf -tAc "SELECT count(*) FROM pg_attribute WHERE attrelid = 'policy.admission_line'::regclass AND attname = 'control_line' AND NOT attisdropped" </dev/null)" = "1" ] \
  && [ "$(psql_bf -tAc "SELECT to_regclass('policy.control_line') IS NULL AND to_regclass('policy.admission_line_control_line_archive') IS NULL" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0047 中止后库被改动了）"; exit 1; }
echo "ok - 已发布招录行的控制线不一致时 0047 以明确信息中止，库不变" >> "$OUT"
printf '%s\n' "SET ROLE starbuddy_kge; UPDATE policy.admission_line SET status = 'retired' WHERE id IN ('$BL2', '$BL3');" | psql_bf >/dev/null \
  || { echo "db-test: 失败（0047 回填测试：撤下招录行失败）"; exit 1; }
psql_bf -1 < "$U47" >/dev/null 2>"$WORK/up47.err" || { echo "db-test: 失败（撤下冲突行后 0047 仍然执行失败：$(cat "$WORK/up47.err")）"; exit 1; }
[ "$(psql_bf -tAc "SELECT string_agg(concat_ws('|', city, year, batch_code, track, score, status, source_document_id, reviewed_at IS NULL), ',') FROM policy.control_line" </dev/null)" \
  = "synthcity|2027|batch_1|quota_allocation|80.0|draft|$BD|t" ] \
  || { echo "db-test: 失败（0047 回填的控制线不对：$(psql_bf -tAc "SELECT string_agg(to_jsonb(c)::text, ',') FROM policy.control_line c" </dev/null)）"; exit 1; }
[ "$(psql_bf -tAc "SELECT string_agg(admission_line_id || ':' || control_line || ':' || line_status, ',' ORDER BY admission_line_id) FROM policy.admission_line_control_line_archive" </dev/null)" \
  = "$BL1:80.0:published,$BL2:85.0:retired,$BL3:75.0:retired" ] \
  || { echo "db-test: 失败（0047 的归档表不对：$(psql_bf -tAc "SELECT string_agg(to_jsonb(a)::text, ',') FROM policy.admission_line_control_line_archive a" </dev/null)）"; exit 1; }
[ "$(psql_bf -tAc "SELECT count(*) FROM pg_attribute WHERE attrelid = 'policy.admission_line'::regclass AND attname = 'control_line' AND NOT attisdropped" </dev/null)" = "0" ] \
  || { echo "db-test: 失败（0047 回填后招录行的控制线列还在）"; exit 1; }
echo "ok - 0047 把三条旧控制线原样归档，只把已发布行回填为一条控制线草稿（已撤下行的值只在归档表），并删掉旧列" >> "$OUT"

# 0037 的回滚（KGE-04 第二步）：库里有节点键掌握记录时，回滚必须以明确错误中止（不能静默删除，也不能补假的能力点键），且什么都不改
D37="$(ls "$ROOT"/db/migrations/0037_*.down.sql)"
if { printf '%s\n' "INSERT INTO platform.mastery_estimate (student_id, subject, node_id, cognitive_level, map_version_id, state, confidence,
                     algorithm_version, evidence_ids) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'MATH.FUNC.QUAD.ROOT', 'know',
                     '7a000000-0000-4000-8000-000000000037', 'understand', 0.5, 'kge-mastery@0.1.0', '{e1000000-0000-4000-8000-000000000001}');"
     cat "$D37"; } | psql_run -1 >/dev/null 2>"$WORK/down37.err"; then
  echo "db-test: 失败（有节点键掌握记录时 0037 回滚仍然执行成功）"; exit 1
fi
grep -q '0037 回滚中止' "$WORK/down37.err" || { echo "db-test: 失败（0037 回滚不是被节点键检查中止：$(cat "$WORK/down37.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM pg_attribute WHERE attrelid = 'platform.mastery_estimate'::regclass AND attname = 'node_id' AND NOT attisdropped" </dev/null)" = "1" ] \
  || { echo "db-test: 失败（0037 回滚中止后节点键列不在了）"; exit 1; }
echo "ok - 有节点键掌握记录时 0037 回滚以明确错误中止，库不变" >> "$OUT"

# 0038 的回滚（G 流第二部分 PR A）：库里有页面审核记录（79t 写入，只追加不能删）时，回滚必须以明确错误中止，且什么都不改
D38="$(ls "$ROOT"/db/migrations/0038_*.down.sql)"
[ "$(psql_run -tAc "SELECT count(*) > 0 FROM kge.content_review WHERE object_type = 'page'" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0038 回滚测试的前提不成立：库里没有页面审核记录）"; exit 1; }
if psql_run -1 < "$D38" >/dev/null 2>"$WORK/down38.err"; then
  echo "db-test: 失败（有页面审核记录时 0038 回滚仍然执行成功）"; exit 1
fi
grep -q '0038 回滚中止' "$WORK/down38.err" || { echo "db-test: 失败（0038 回滚不是被页面记录检查中止：$(cat "$WORK/down38.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM pg_attribute WHERE attrelid = 'kge.content_review'::regclass AND attname = 'page_commit' AND NOT attisdropped" </dev/null)" = "1" ] \
  || { echo "db-test: 失败（0038 回滚中止后提交号列不在了）"; exit 1; }
echo "ok - 有页面审核记录时 0038 回滚以明确错误中止，库不变" >> "$OUT"

# 0039（G 流第二部分 PR B）的并发：内容版本发布与登记、两个草稿并发发布。语文没有地图骨架，用合成语文版本（前五段 syn-race）
CVX=e7b00000-0000-4000-8000-0000000000f1; CVY=e7b00000-0000-4000-8000-0000000000f2; CVZ=e7b00000-0000-4000-8000-0000000000f3
printf '%s\n' "INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version) VALUES
    ('$CVX', 'nanjing', 2027, 9, 'syn-race', 'wenli', '0.2.0'), ('$CVY', 'nanjing', 2027, 9, 'syn-race', 'wenli', '0.1.0'),
    ('$CVZ', 'nanjing', 2027, 8, 'syn-race', 'wenli', '0.1.0');
  $AS_STAFF SELECT platform.sign_content_version(v, 'approved', platform.content_version_digest(v), 'race')
    FROM unnest(ARRAY['$CVX', '$CVY', '$CVZ']::uuid[]) v;" | psql_run >/dev/null || { echo "db-test: 失败（0039 并发测试夹具）"; exit 1; }
# 场景 4：会话 A 发布 0.2.0 后事务未提交；会话 B 此时发布同前五段的 0.1.0。咨询锁让 B 等 A 提交，再按版本号递增拒绝
printf '%s\n' "BEGIN; $AS_STAFF SELECT platform.publish_content_version('$CVX'); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_g.err" &
sleep 0.5
if printf '%s\n' "$AS_STAFF SELECT platform.publish_content_version('$CVY');" | psql_run >/dev/null 2>"$WORK/race_h.err"; then
  wait; echo "db-test: 失败（两个草稿并发发布时，较小的版本号没有等前一个提交后被拒绝）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 4 的先发布失败：$(cat "$WORK/race_g.err")）"; exit 1; }
grep -q '必须大于' "$WORK/race_h.err" || { echo "db-test: 失败（并发场景 4 不是被版本号检查拒绝：$(cat "$WORK/race_h.err")）"; exit 1; }
echo "ok - 并发：同前五段两个草稿并发发布，后一个等前一个提交后按版本号递增拒绝" >> "$OUT"
# 场景 5：学习服务往已签字的草稿追加题目登记并持有事务；此时发布。登记对版本行的共享锁让发布等登记提交，再按摘要不符拒绝
printf '%s\n' "BEGIN; SET ROLE starbuddy_learning;
  INSERT INTO platform.item (subject, item_id, curriculum_version_id) VALUES ('wenli', 'SYN-RACE-I1', '$CVZ');
  SELECT pg_sleep(1.5); COMMIT;" | psql_run >/dev/null 2>"$WORK/race_i.err" &
sleep 0.5
if printf '%s\n' "$AS_STAFF SELECT platform.publish_content_version('$CVZ');" | psql_run >/dev/null 2>"$WORK/race_j.err"; then
  wait; echo "db-test: 失败（发布没有等待进行中的题目登记，签字后追加的登记进了已发布版本）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 5 的登记失败：$(cat "$WORK/race_i.err")）"; exit 1; }
grep -q '摘要不符' "$WORK/race_j.err" || { echo "db-test: 失败（并发场景 5 不是被摘要检查拒绝：$(cat "$WORK/race_j.err")）"; exit 1; }
echo "ok - 并发：发布等待进行中的题目登记提交后按摘要不符拒绝" >> "$OUT"

# 0039 的数据前提：库里已有 retired 的内容版本（79u 撤下的）时，0039 up 以明确信息中止（不会在补不出撤下时间的数据上加约束）
if psql_run -1 < "$(ls "$ROOT"/db/migrations/0039_*.up.sql)" >/dev/null 2>"$WORK/up39.err"; then
  echo "db-test: 失败（库里已有 retired 内容版本时 0039 up 仍然执行成功）"; exit 1
fi
grep -q '0039 中止：已有' "$WORK/up39.err" || { echo "db-test: 失败（0039 up 不是被数据前提检查中止：$(cat "$WORK/up39.err")）"; exit 1; }
echo "ok - 已有 retired 内容版本时 0039 up 以明确信息中止" >> "$OUT"
# 0039 的回滚：库里有审核与发布记录（只追加不能删）时，回滚必须以明确错误中止，且什么都不改
D39="$(ls "$ROOT"/db/migrations/0039_*.down.sql)"
[ "$(psql_run -tAc "SELECT count(*) > 0 FROM platform.release_record" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0039 回滚测试的前提不成立：库里没有审核与发布记录）"; exit 1; }
if psql_run -1 < "$D39" >/dev/null 2>"$WORK/down39.err"; then
  echo "db-test: 失败（有审核与发布记录时 0039 回滚仍然执行成功）"; exit 1
fi
grep -q '0039 回滚中止' "$WORK/down39.err" || { echo "db-test: 失败（0039 回滚不是被记录检查中止：$(cat "$WORK/down39.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM pg_attribute WHERE attrelid = 'platform.curriculum_version'::regclass AND attname = 'retired_at' AND NOT attisdropped" </dev/null)" = "1" ] \
  || { echo "db-test: 失败（0039 回滚中止后撤下时间列不在了）"; exit 1; }
echo "ok - 有审核与发布记录时 0039 回滚以明确错误中止，库不变" >> "$OUT"

# 0041（G 流第二部分 PR C）的并发：两名 staff 同时对同一所合成学校签字。签字函数锁住对象行，后一个等前一个提交后再计数，
# 第二名批准时发布；去掉锁时两人各只看到自己的一条签字，学校一直停在草稿（来源文件用 77 号已发布的合成文件）
PH=d7500000-0000-4000-8000-0000000000f9
printf '%s\n' "SET ROLE starbuddy_kge; INSERT INTO policy.school (id, city, code, public_name, level_code, source_document_id)
  VALUES ('$PH', 'synthcity', 'synth-hs-race', '合成高中（并发，非真实）', 'level_a', 'a2000000-0000-4000-8000-0000000000d2');" \
  | psql_run >/dev/null || { echo "db-test: 失败（0041 并发测试夹具）"; exit 1; }
SIGN_PH="SELECT policy.sign_record('school', '$PH', 'approved', policy.object_digest('school', '$PH'));"
printf '%s\n' "BEGIN; $AS_STAFF $SIGN_PH SELECT pg_sleep(1.5); COMMIT;" | psql_run >/dev/null 2>"$WORK/race_k.err" &
sleep 0.5
printf '%s\n' "SELECT set_config('app.account_id', 'f5000000-0000-4000-8000-0000000000f5', false); SET ROLE starbuddy_review; $SIGN_PH" \
  | psql_run >/dev/null 2>"$WORK/race_l.err" || { wait; echo "db-test: 失败（并发考情签字：后一名 staff 的签字失败：$(cat "$WORK/race_l.err")）"; exit 1; }
wait || { echo "db-test: 失败（并发考情签字：前一名 staff 的签字失败：$(cat "$WORK/race_k.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT review_status FROM policy.school WHERE id = '$PH'" </dev/null)" = "published" ] \
  || { echo "db-test: 失败（两名 staff 同时签同一所学校后没有发布：签字没有串行计数）"; exit 1; }
echo "ok - 并发：两名 staff 同时签同一考情对象，签字按对象行锁串行，第二名批准时发布" >> "$OUT"

# 考情方案发布路径的并发（specs/policy-schema-v0.9 lessons 留给后续的两会话测试；按 0041 后的现状）：
#   发布函数 policy.publish_scheme 的同城市同届别咨询锁、对方案行的 FOR UPDATE、对来源文件的 FOR SHARE；
#   子表冻结触发器 policy.guard_scheme_frozen 对方案行的 FOR SHARE；方案签字 policy.sign_scheme 对方案行的 FOR UPDATE。
# 夹具（合成城市 synthcity 2033–2037 届，非真实数据）：两份来源文件经两名 staff 签字发布；六个草稿方案各有一门计分科目与一个批次，
# 都已由两名 staff 对当前内容批准（可以直接发布）
QD1=d7600000-0000-4000-8000-0000000000d1; QD2=d7600000-0000-4000-8000-0000000000d2
QA=d7610000-0000-4000-8000-0000000000e1; QB=d7610000-0000-4000-8000-0000000000e2; QC=d7610000-0000-4000-8000-0000000000e3
QE=d7610000-0000-4000-8000-0000000000e4; QF=d7610000-0000-4000-8000-0000000000e5; QG=d7610000-0000-4000-8000-0000000000e6
QALL="ARRAY['$QA', '$QB', '$QC', '$QE', '$QF', '$QG']::uuid[]"
AS_SF2="SELECT set_config('app.account_id', 'f5000000-0000-4000-8000-0000000000f5', false); SET ROLE starbuddy_review;"
AS_SF3="SELECT set_config('app.account_id', 'd7000000-0000-4000-8000-0000000000a3', false); SET ROLE starbuddy_review;"  # 79v 建的第三名合成 staff
POL_SIGN_ALL="SELECT policy.sign_record('source_document', d, 'approved', policy.object_digest('source_document', d))
                FROM unnest(ARRAY['$QD1', '$QD2']::uuid[]) d;
              SELECT policy.sign_scheme(v, 'approved', policy.object_digest('exam_scheme', v)) FROM unnest($QALL) v;"
printf '%s\n' "SET ROLE starbuddy_kge;
  INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on) VALUES
    ('$QD1', 'synthcity', '合成教育局', '合成〔2032〕1号', '合成并发测试方案（非真实数据）', '2032-03-01', 'https://jyj.synthcity.example/doc/race1', 2033, '2032-09-01'),
    ('$QD2', 'synthcity', '合成教育局', '合成〔2032〕2号', '合成并发测试方案二（非真实数据）', '2032-03-01', 'https://jyj.synthcity.example/doc/race2', 2036, '2032-09-01');
  INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES
    ('$QA', 'synthcity', 2033, '1.0.0', '$QD1'), ('$QB', 'synthcity', 2033, '1.1.0', '$QD1'), ('$QC', 'synthcity', 2034, '1.0.0', '$QD1'),
    ('$QE', 'synthcity', 2035, '1.0.0', '$QD1'), ('$QF', 'synthcity', 2036, '1.0.0', '$QD2'), ('$QG', 'synthcity', 2037, '1.0.0', '$QD1');
  INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
    SELECT v, 'chinese', true, 100, 'city', 'closed_book', 9 FROM unnest($QALL) v;
  INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) SELECT v, 'batch_1', 1, 'unified' FROM unnest($QALL) v;
  RESET ROLE; $AS_SF2 $POL_SIGN_ALL RESET ROLE; $AS_STAFF $POL_SIGN_ALL" \
  | psql_run >/dev/null 2>"$WORK/polrace.err" || { echo "db-test: 失败（考情方案并发测试夹具：$(cat "$WORK/polrace.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM unnest($QALL) v WHERE policy.approval_count('exam_scheme', v) = 2 AND (SELECT count(*) FROM policy.validate_scheme(v) c WHERE c.severity = 'error') = 0" </dev/null)" = "6" ] \
  || { echo "db-test: 失败（考情方案并发测试夹具：六个草稿方案不是都可以直接发布）"; exit 1; }
# 场景 6（咨询锁）：同城市同届别两个草稿，会话 A 发布 1.0.0 后事务未提交；会话 B 此时发布 1.1.0。咨询锁让 B 等 A 提交，
# 再按已发布状态校验，撤下 A 的 1.0.0 后发布自己；去掉锁时 B 看不到 A 的发布，撞“唯一已发布”索引失败（新版本发不出去）
printf '%s\n' "BEGIN; $AS_STAFF SELECT policy.publish_scheme('$QA'); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_m.err" &
sleep 0.5
printf '%s\n' "$AS_SF2 SELECT policy.publish_scheme('$QB');" | psql_run >/dev/null 2>"$WORK/race_n.err" \
  || { wait; echo "db-test: 失败（同城市同届别并发发布：后发布的新版本失败，没有等前一个提交后接替：$(cat "$WORK/race_n.err")）"; exit 1; }
wait || { echo "db-test: 失败（并发场景 6 的先发布失败：$(cat "$WORK/race_m.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT string_agg(version || ':' || status, ',' ORDER BY version) FROM policy.exam_scheme WHERE city = 'synthcity' AND cohort = 2033" </dev/null)" = "1.0.0:retired,1.1.0:published" ] \
  || { echo "db-test: 失败（同城市同届别并发发布后不是旧版本撤下、新版本发布）"; exit 1; }
echo "ok - 并发：同城市同届别两个草稿并发发布，咨询锁让后一个等前一个提交，撤下旧版本后发布新版本" >> "$OUT"
# 场景 7（子表 FOR SHARE ← 发布 FOR UPDATE）：写入方改已签字草稿的科目配置并持有事务；此时发布。冻结触发器对方案行的共享锁让发布等改动提交，
# 再按新内容的摘要数签字（旧签字不再计数）而拒绝；去掉任一边的锁时，发布按改动前的内容通过，提交后已发布方案的内容与签字不符
printf '%s\n' "BEGIN; SET ROLE starbuddy_kge; UPDATE policy.subject_config SET full_score = 90 WHERE scheme_id = '$QC' AND subject = 'chinese';
  SELECT pg_sleep(1.5); COMMIT;" | psql_run >/dev/null 2>"$WORK/race_o.err" &
sleep 0.5
if printf '%s\n' "$AS_STAFF SELECT policy.publish_scheme('$QC');" | psql_run >/dev/null 2>"$WORK/race_p.err"; then
  wait; echo "db-test: 失败（发布没有等待进行中的科目配置修改，签字后改过的方案被发布）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 7 的科目配置修改失败：$(cat "$WORK/race_o.err")）"; exit 1; }
grep -q '阻断问题' "$WORK/race_p.err" || { echo "db-test: 失败（并发场景 7 的发布不是被校验拒绝：$(cat "$WORK/race_p.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT status FROM policy.exam_scheme WHERE id = '$QC'" </dev/null)" = "draft" ] \
  || { echo "db-test: 失败（并发场景 7 的方案不是草稿）"; exit 1; }
echo "ok - 并发：发布等待进行中的科目配置修改提交后，按新内容的签字不足拒绝" >> "$OUT"
# 场景 8（发布 FOR UPDATE → 子表 FOR SHARE）：方案发布后事务未提交；写入方此时给它加招录规则。冻结触发器的共享锁让写入等发布提交，
# 再按已发布状态拒绝；去掉锁时写入看到的还是草稿，规则进了已发布方案
printf '%s\n' "BEGIN; $AS_STAFF SELECT policy.publish_scheme('$QE'); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_q.err" &
sleep 0.5
if printf '%s\n' "SET ROLE starbuddy_kge; INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) VALUES ('$QE', 'batch_2', 2, 'unified');" \
   | psql_run >/dev/null 2>"$WORK/race_r.err"; then
  wait; echo "db-test: 失败（发布进行中写入的招录规则进入了已发布方案）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 8 的发布本身失败：$(cat "$WORK/race_q.err")）"; exit 1; }
grep -q '已发布或已撤下' "$WORK/race_r.err" || { echo "db-test: 失败（并发场景 8 的写入不是被冻结拒绝：$(cat "$WORK/race_r.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM policy.admission_rule WHERE scheme_id = '$QE'" </dev/null)" = "1" ] \
  || { echo "db-test: 失败（并发场景 8 的已发布方案多了招录规则）"; exit 1; }
echo "ok - 并发：方案发布进行中的招录规则写入等发布提交后按已发布状态拒绝" >> "$OUT"
# 场景 9（发布对来源文件的 FOR SHARE）：写入方撤下来源文件并持有事务；此时发布引用它的方案。共享锁让发布等撤下提交，再按来源文件未发布拒绝；
# 去掉锁时发布按撤下前的状态通过，方案在来源文件已撤下之后才发布
printf '%s\n' "BEGIN; SET ROLE starbuddy_kge; UPDATE policy.source_document SET status = 'retired' WHERE id = '$QD2';
  SELECT pg_sleep(1.5); COMMIT;" | psql_run >/dev/null 2>"$WORK/race_s.err" &
sleep 0.5
if printf '%s\n' "$AS_STAFF SELECT policy.publish_scheme('$QF');" | psql_run >/dev/null 2>"$WORK/race_t.err"; then
  wait; echo "db-test: 失败（发布没有等待进行中的来源文件撤下，方案引用已撤下的来源文件被发布）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 9 的来源文件撤下失败：$(cat "$WORK/race_s.err")）"; exit 1; }
grep -q '阻断问题' "$WORK/race_t.err" || { echo "db-test: 失败（并发场景 9 的发布不是被校验拒绝：$(cat "$WORK/race_t.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT status FROM policy.exam_scheme WHERE id = '$QF'" </dev/null)" = "draft" ] \
  || { echo "db-test: 失败（并发场景 9 的方案不是草稿）"; exit 1; }
echo "ok - 并发：发布等待进行中的来源文件撤下提交后，按来源文件未发布拒绝" >> "$OUT"
# 场景 10（方案签字 FOR UPDATE ↔ 发布 FOR UPDATE）：方案发布后事务未提交；第三名 staff 此时驳回它。签字对方案行的锁让驳回等发布提交，
# 再按“只能对草稿签字”拒绝；去掉锁时驳回记在已发布方案上（已发布方案的有效批准人数变成 0）
printf '%s\n' "BEGIN; $AS_STAFF SELECT policy.publish_scheme('$QG'); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_u.err" &
sleep 0.5
if printf '%s\n' "$AS_SF3 SELECT policy.sign_scheme('$QG', 'rejected', policy.object_digest('exam_scheme', '$QG'));" \
   | psql_run >/dev/null 2>"$WORK/race_v.err"; then
  wait; echo "db-test: 失败（发布进行中的驳回记在了已发布方案上）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 10 的发布本身失败：$(cat "$WORK/race_u.err")）"; exit 1; }
grep -q '只能对草稿方案签字' "$WORK/race_v.err" || { echo "db-test: 失败（并发场景 10 的驳回不是被草稿检查拒绝：$(cat "$WORK/race_v.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM platform.policy_signoff WHERE object_id = '$QG' AND decision = 'rejected'" </dev/null)" = "0" ] \
  || { echo "db-test: 失败（并发场景 10 的已发布方案上有驳回记录）"; exit 1; }
echo "ok - 并发：方案发布进行中的签字等发布提交后按非草稿拒绝" >> "$OUT"

# 考情待定事项 PR1（0045、0047，specs/policy-decisions-v0.9.1）的两会话并发与生产形态的登录角色。合成学生 Y1（79y 建，建档 9 年级，有效绑定家长 gA）、
# 79y 的 2028 届合成方案与来源文件（指标生 batch_2 设控制线）
Y1=f7900000-0000-4000-8000-0000000000c1; GA=a0000000-0000-4000-8000-00000000000a
AS_GA="SELECT set_config('app.account_id', '$GA', false); SET ROLE starbuddy_bff;"
Y1C="$(psql_run -tAc "SELECT exam_cohort FROM platform.student_profile WHERE id = '$Y1'" </dev/null)"
[ -n "$Y1C" ] && [ "$(psql_run -tAc "SELECT platform.current_grade(exam_cohort, now()) FROM platform.student_profile WHERE id = '$Y1'" </dev/null)" = "9" ] \
  || { echo "db-test: 失败（届别并发测试的前提不成立：79y 的合成学生 Y1 不在或当前不是 9 年级）"; exit 1; }
y1_chain() {  # Y1 最近 $1 条更正记录：旧值>新值:来源（相对 Y1C）
  psql_run -tAc "SELECT string_agg(x, ',' ORDER BY at) FROM (SELECT (old_cohort - $Y1C) || '>' || (new_cohort - $Y1C) || ':' || source AS x, changed_at AS at
                   FROM platform.exam_cohort_change WHERE student_id = '$Y1' ORDER BY changed_at DESC, id DESC LIMIT $1) r" </dev/null
}
# 场景 11（更正与再次更正）：家长经 BFF 更正 +1 后事务未提交；客服此时按 +1 之后的值再更正。函数锁住档案行，后一个等前一个提交后，
# 以提交后的届别为旧值核对 ±1 并记录（记录串成一条链）；去掉锁时后一个读到更正前的值，相差 2 被拒
printf '%s\n' "BEGIN; $AS_GA SELECT platform.correct_exam_cohort('$Y1', $((Y1C + 1))::smallint); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_w.err" &
sleep 0.5
printf '%s\n' "$AS_STAFF SELECT platform.correct_exam_cohort('$Y1', $((Y1C + 2))::smallint);" | psql_run >/dev/null 2>"$WORK/race_x.err" \
  || { wait; echo "db-test: 失败（两次更正并发：后一次没有等前一次提交后按新值核对：$(cat "$WORK/race_x.err")）"; exit 1; }
wait || { echo "db-test: 失败（并发场景 11 的先更正失败：$(cat "$WORK/race_w.err")）"; exit 1; }
[ "$(y1_chain 2)" = "0>1:guardian_report,1>2:staff_correction" ] \
  && [ "$(psql_run -tAc "SELECT (exam_cohort - $Y1C) || ':' || exam_cohort_source FROM platform.student_profile WHERE id = '$Y1'" </dev/null)" = "2:staff_correction" ] \
  || { echo "db-test: 失败（两次更正并发后届别或记录不对：$(y1_chain 2)）"; exit 1; }
echo "ok - 并发：同一学生的两次届别更正按档案行锁串行，后一次以提交后的值为旧值，记录串成一条链" >> "$OUT"
# 场景 12（更正与改建档年级）：DBA 改建档年级后事务未提交；家长此时更正。更正等改年级提交后执行，且不再拿建档年级比对
printf '%s\n' "BEGIN; UPDATE platform.student_profile SET grade = 8 WHERE id = '$Y1'; SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_y.err" &
sleep 0.5
printf '%s\n' "$AS_GA SELECT platform.correct_exam_cohort('$Y1', $((Y1C + 1))::smallint);" | psql_run >/dev/null 2>"$WORK/race_z.err" \
  || { wait; echo "db-test: 失败（改建档年级进行中的届别更正失败：$(cat "$WORK/race_z.err")）"; exit 1; }
wait || { echo "db-test: 失败（并发场景 12 的改年级失败：$(cat "$WORK/race_y.err")）"; exit 1; }
[ "$(y1_chain 1)" = "2>1:guardian_report" ] \
  && [ "$(psql_run -tAc "SELECT grade || ':' || (exam_cohort - $Y1C) || ':' || exam_cohort_source FROM platform.student_profile WHERE id = '$Y1'" </dev/null)" = "8:1:guardian_report" ] \
  || { echo "db-test: 失败（改年级与更正并发后档案或记录不对：$(y1_chain 1)）"; exit 1; }
echo "ok - 并发：改建档年级进行中的届别更正等它提交后执行，不再拿建档年级比对" >> "$OUT"
# 生产形态（审查建议 1）：服务以继承应用角色的登录角色连接，不 SET ROLE。更正函数此时按登录角色（session_user）分渠道：
# 继承 bff 的登录角色带家长账号走家长分支；带 staff 账号被拒（走不到客服分支）；继承 review 的登录角色带 staff 账号走客服分支
psql_run </dev/null -c "CREATE ROLE t_bff_login LOGIN IN ROLE starbuddy_bff; CREATE ROLE t_review_login LOGIN IN ROLE starbuddy_review;"
AS_LOGIN() { printf "SET SESSION AUTHORIZATION %s; SET app.account_id = '%s';" "$1" "$2"; }
[ "$(printf '%s\n' "$(AS_LOGIN t_bff_login "$GA") SELECT current_setting('role') || ':' || session_user;" | psql_run -tA 2>&1)" = "none:t_bff_login" ] \
  || { echo "db-test: 失败（生产形态测试的前提不成立：登录角色会话里 role 不是 none）"; psql_run </dev/null -c "DROP ROLE t_bff_login; DROP ROLE t_review_login;"; exit 1; }
LOGIN_OK=1
printf '%s\n' "$(AS_LOGIN t_bff_login "$GA") SELECT platform.correct_exam_cohort('$Y1', $((Y1C + 2))::smallint);" | psql_run >/dev/null 2>"$WORK/login_a.err" || LOGIN_OK=0
printf '%s\n' "$(AS_LOGIN t_bff_login 5f000000-0000-4000-8000-00000000005f) SELECT platform.correct_exam_cohort('$Y1', $((Y1C + 1))::smallint);" \
  | psql_run >/dev/null 2>"$WORK/login_b.err" && LOGIN_OK=0
printf '%s\n' "$(AS_LOGIN t_review_login 5f000000-0000-4000-8000-00000000005f) SELECT platform.correct_exam_cohort('$Y1', $((Y1C + 1))::smallint);" \
  | psql_run >/dev/null 2>"$WORK/login_c.err" || LOGIN_OK=0
psql_run </dev/null -c "DROP ROLE t_bff_login; DROP ROLE t_review_login;"
[ "$LOGIN_OK" = "1" ] && grep -q 'not allowed' "$WORK/login_b.err" && [ "$(y1_chain 2)" = "1>2:guardian_report,2>1:staff_correction" ] \
  || { echo "db-test: 失败（登录角色形态的更正渠道不对：$(cat "$WORK/login_a.err" "$WORK/login_b.err" "$WORK/login_c.err")；记录 $(y1_chain 2)）"; exit 1; }
echo "ok - 生产形态：不 SET ROLE 时按登录角色分渠道，继承 bff 的登录角色带家长账号能更正、带 staff 账号被拒，继承 review 的登录角色走客服分支" >> "$OUT"
# 场景 13（控制线发布唯一）：同一城市年份批次类别两条控制线草稿各有一名 staff 批准；第二名 staff 发布前一条后事务未提交，
# 第三名 staff 此时发布后一条。唯一索引让后一个等前一个提交后失败（整笔回滚），只留一条已发布；去掉索引时两条都发布
ZA=f7930000-0000-4000-8000-0000000000b1; ZB=f7930000-0000-4000-8000-0000000000b2; YCD1=f7910000-0000-4000-8000-0000000000d1
SIGN_Z() { printf "SELECT policy.sign_record('control_line', '%s', 'approved', policy.object_digest('control_line', '%s'));" "$1" "$1"; }
printf '%s\n' "SET ROLE starbuddy_kge;
  INSERT INTO policy.control_line (id, city, year, batch_code, score, source_document_id) VALUES
    ('$ZA', 'synthcity', 2028, 'batch_2', 91, '$YCD1'), ('$ZB', 'synthcity', 2028, 'batch_2', 92, '$YCD1');
  RESET ROLE; $AS_STAFF $(SIGN_Z "$ZA") $(SIGN_Z "$ZB")" | psql_run >/dev/null 2>"$WORK/clrace.err" \
  || { echo "db-test: 失败（控制线并发测试夹具：$(cat "$WORK/clrace.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM policy.control_line WHERE city = 'synthcity' AND year = 2028 AND batch_code = 'batch_2' AND status = 'published'" </dev/null)" = "0" ] \
  || { echo "db-test: 失败（控制线并发测试的前提不成立：2028 届 batch_2 已有已发布控制线）"; exit 1; }
printf '%s\n' "BEGIN; $AS_SF2 $(SIGN_Z "$ZA") SELECT pg_sleep(1.5); COMMIT;" | psql_run >/dev/null 2>"$WORK/race_cl1.err" &
sleep 0.5
if printf '%s\n' "$AS_SF3 $(SIGN_Z "$ZB")" | psql_run >/dev/null 2>"$WORK/race_cl2.err"; then
  wait; echo "db-test: 失败（同一城市年份批次类别的两条控制线并发发布都成功了）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 13 的先发布失败：$(cat "$WORK/race_cl1.err")）"; exit 1; }
grep -q 'control_line_one_published' "$WORK/race_cl2.err" || { echo "db-test: 失败（并发场景 13 的后一个不是被唯一索引拒绝：$(cat "$WORK/race_cl2.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT string_agg(id || ':' || status, ',' ORDER BY id) FROM policy.control_line WHERE id IN ('$ZA', '$ZB')" </dev/null)" = "$ZA:published,$ZB:draft" ] \
  && [ "$(psql_run -tAc "SELECT policy.approval_count('control_line', '$ZB')" </dev/null)" = "1" ] \
  || { echo "db-test: 失败（控制线并发发布后不是恰好一条已发布、另一条签字回滚）"; exit 1; }
echo "ok - 并发：同一城市年份批次类别的两条控制线并发发布，唯一索引让后一个等前一个提交后失败，只留一条已发布" >> "$OUT"
# 场景 14（0050，来源文件守卫对清单行 FOR SHARE ↔ 下线 FOR UPDATE）：有 policy_source_admin 的 staff（79y 授予 sf1）下线一个合成主机名后事务未提交；
# 写入方此时新建挂在它下面的来源文件。共享锁让新建等下线提交后再核对，按已下线拒绝；去掉锁时新建按下线前的快照通过，下线后仍进来新文件
printf '%s\n' "$AS_STAFF SELECT policy.add_source_domain('jyj.race14.example', '合成并发教育局');" | psql_run >/dev/null 2>"$WORK/race14.err" \
  || { echo "db-test: 失败（并发场景 14 的夹具：$(cat "$WORK/race14.err")）"; exit 1; }
printf '%s\n' "BEGIN; $AS_STAFF SELECT policy.retire_source_domain('jyj.race14.example'); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_sd1.err" &
sleep 0.5
if printf '%s\n' "SET ROLE starbuddy_kge; INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
     VALUES ('synthcity', '合成并发教育局', '合成〔2027〕140号', '合成并发文件（非真实数据）', '2027-03-01', 'https://jyj.race14.example/doc/1', 2028, '2027-09-01');" \
   | psql_run >/dev/null 2>"$WORK/race_sd2.err"; then
  wait; echo "db-test: 失败（来源清单下线进行中新建的来源文件没有等下线提交，挂在已下线的主机名下）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 14 的下线本身失败：$(cat "$WORK/race_sd1.err")）"; exit 1; }
grep -q '官方来源清单' "$WORK/race_sd2.err" || { echo "db-test: 失败（并发场景 14 的新建不是被清单核对拒绝：$(cat "$WORK/race_sd2.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT count(*) FROM policy.source_document WHERE url LIKE 'https://jyj.race14.example/%'" </dev/null)" = "0" ] \
  || { echo "db-test: 失败（并发场景 14 后已下线的主机名下有新来源文件）"; exit 1; }
echo "ok - 并发：来源清单下线进行中新建的来源文件等下线提交后按已下线拒绝（守卫经属主函数对清单行 FOR SHARE）" >> "$OUT"
# 场景 15（0049，退回额度审查 S4）：同一笔因届别变化放弃的诊断预留，两个会话同时调退回。函数锁住预留行，后一个等前一个提交后
# 按幂等键看到已退过，返回 idempotent；整个过程只有一条退回流水、计数对账一致。合成学生 c2（学生账号 b1，10 号夹具，70 号开通了额度）
C2=c2000000-0000-4000-8000-0000000000c2; B1=b1000000-0000-4000-8000-0000000000b1; RK=b0b00000-0000-4000-8000-000000007999
AS_C2="SELECT set_config('app.account_id', '$B1', false); SET ROLE starbuddy_bff;"
# 80 号把计量时钟 t.clock 固定成了真实 now()：晚上跑会落进宵禁、预留被拒（2026-10-08 21:15 严格检查复现）。本场景把计量时钟
# 拨到当天北京时间 10:00，与墙钟无关
psql_run -tAc "UPDATE t.clock SET now = (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') + interval '10 hours') AT TIME ZONE 'Asia/Shanghai'" </dev/null >/dev/null \
  || { echo "db-test: 失败（并发场景 15 无法固定计量时钟）"; exit 1; }
# c2 到这里已是留存态（70 号的试用已结束），先以超级用户给它开一期合成的试点订阅并发放当期额度（只为本场景）
printf '%s\n' "WITH s AS (INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
    SELECT household_id, id, 'pro', platform.meter_current_config(), 'pilot_virtual', platform.meter_now(), platform.meter_now() + interval '30 days'
      FROM platform.student_profile WHERE id = '$C2' RETURNING id)
  SELECT platform.meter_grant_period(id) FROM s;
  $AS_C2 SELECT platform.meter_reserve('$C2', 'diagnosis_full', '$RK', repeat('7', 64), 1, NULL, 'diagnose')->>'outcome';
  SELECT platform.meter_settle(platform.meter_usage_id('$RK'), 1)->>'outcome'; RESET ROLE;
  INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version, usage_id, idempotency_key, start_grade)
  VALUES ('f7990000-0000-4000-8000-0000000000ff', '$C2', 'wenli', 'aya-diag-trace@0.1.0', '0.2.0', platform.meter_usage_id('$RK'), '$RK',
          CASE WHEN platform.current_grade((SELECT exam_cohort FROM platform.student_profile WHERE id = '$C2'), now()) = 7 THEN 8 ELSE 7 END);
  UPDATE platform.diagnosis_session SET status = 'abandoned', finished_at = now(), abandon_reason = 'grade_changed'
   WHERE id = 'f7990000-0000-4000-8000-0000000000ff';" | psql_run -tA >"$WORK/rf0.out" 2>"$WORK/rf0.err" \
  && grep -qx reserved "$WORK/rf0.out" && grep -qx settled "$WORK/rf0.out" \
  || { echo "db-test: 失败（并发场景 15 的夹具：$(cat "$WORK/rf0.out" "$WORK/rf0.err")）"; exit 1; }
printf '%s\n' "BEGIN; $AS_C2 SELECT platform.meter_refund_grade_changed(platform.meter_usage_id('$RK')); SELECT pg_sleep(1.5); COMMIT;" \
  | psql_run -tA >"$WORK/rf1.out" 2>"$WORK/rf1.err" &
sleep 0.5
printf '%s\n' "$AS_C2 SELECT platform.meter_refund_grade_changed(platform.meter_usage_id('$RK'));" | psql_run -tA >"$WORK/rf2.out" 2>"$WORK/rf2.err" \
  || { wait; echo "db-test: 失败（并发场景 15 的后一次退回出错：$(cat "$WORK/rf2.err")）"; exit 1; }
wait || { echo "db-test: 失败（并发场景 15 的先一次退回出错：$(cat "$WORK/rf1.err")）"; exit 1; }
grep -q '"idempotent": false' "$WORK/rf1.out" && grep -q '"outcome": "refunded"' "$WORK/rf2.out" && grep -q '"idempotent": true' "$WORK/rf2.out" \
  && [ "$(psql_run -tAc "SELECT count(*) FROM platform.usage_ledger WHERE kind = 'adjust' AND reason_code = 'grade_changed_refund' AND source_idempotency_key = '$RK'" </dev/null)" = "1" ] \
  && [ "$(psql_run -tAc "SELECT (SELECT count(*) FROM platform.entitlement_reconciliation) + (SELECT count(*) FROM platform.reservation_ledger_mismatch)" </dev/null)" = "0" ] \
  || { echo "db-test: 失败（并发场景 15：两次退回不是恰好一次生效、一次幂等：$(cat "$WORK/rf1.out" "$WORK/rf2.out")）"; exit 1; }
echo "ok - 并发：同一笔诊断预留的两次退回额度按预留行锁串行，只退一次，后一次幂等，计数对账一致" >> "$OUT"
# 场景 16（0052，PR3 已知风险 1）：写入方 kge 开着事务、经 policy.lock_active_source_host 对清单行持有共享锁并一直在执行（不空闲，
# 空闲上限管不到）；有权限的 staff（sf1）此时下线这个主机名。下线最多等 5 秒即以 55P03“稍后重试”失败（不挂到 kge 提交），
# 清单与操作记录都不变；kge 提交后再下线成功。去掉锁等待上限时下线一直等到 kge 提交后成功
# 持锁方拿到锁、进入 pg_sleep(12) 之后才开始计时（并行跑变异时机器繁忙，不用固定的 0.5 秒），上限 10 秒内必须失败
H16=jyj.race16.example
since_ms() { echo $(( ($(date +%s%N) - $1) / 1000000 )); }
holder_sleeping() {  # 等到另一个会话正在执行 pg_sleep(<$1，默认 12>)（此前的语句已取到锁），最多 10 秒
  local i s="${1:-12}"
  for i in $(seq 1 100); do
    [ "$(psql_run -tAc "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND query LIKE '%pg_sleep($s)%' AND pid <> pg_backend_pid()" </dev/null)" = "1" ] && return 0
    sleep 0.1
  done
  return 1
}
printf '%s\n' "$AS_STAFF SELECT policy.add_source_domain('$H16', '合成锁等待教育局');" | psql_run >/dev/null 2>"$WORK/race16.err" \
  || { echo "db-test: 失败（并发场景 16 的夹具：$(cat "$WORK/race16.err")）"; exit 1; }
printf '%s\n' "BEGIN; SET ROLE starbuddy_kge; SELECT policy.lock_active_source_host('$H16'); SELECT pg_sleep(12); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_lt1.err" &
holder_sleeping || { wait; echo "db-test: 失败（并发场景 16 的 kge 持锁事务没有开始：$(cat "$WORK/race_lt1.err")）"; exit 1; }
T16=$(date +%s%N)
if printf '%s\n' '\set VERBOSITY verbose' "$AS_STAFF SELECT policy.retire_source_domain('$H16');" | psql_run >/dev/null 2>"$WORK/race_lt2.err"; then
  wait; echo "db-test: 失败（kge 持有清单行共享锁时下线没有在锁等待上限内失败，而是等到 kge 提交后成功：$(since_ms "$T16") ms）"; exit 1
fi
E16=$(since_ms "$T16")
wait || { echo "db-test: 失败（并发场景 16 的 kge 持锁事务失败：$(cat "$WORK/race_lt1.err")）"; exit 1; }
grep -q '55P03' "$WORK/race_lt2.err" && grep -q '稍后重试' "$WORK/race_lt2.err" && [ "$E16" -ge 4500 ] && [ "$E16" -lt 10000 ] \
  || { echo "db-test: 失败（并发场景 16 的下线不是约 5 秒后以 55P03“稍后重试”失败：${E16} ms，$(cat "$WORK/race_lt2.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT (SELECT status FROM policy.source_domain WHERE host = '$H16') || ':' ||
                          (SELECT count(*) FROM platform.policy_source_domain_change WHERE host = '$H16' AND action = 'retire')" </dev/null)" = "active:0" ] \
  || { echo "db-test: 失败（并发场景 16 超时失败后清单或操作记录被改了）"; exit 1; }
printf '%s\n' "$AS_STAFF SELECT policy.retire_source_domain('$H16');" | psql_run >/dev/null 2>"$WORK/race_lt3.err" \
  || { echo "db-test: 失败（并发场景 16 kge 提交后下线失败：$(cat "$WORK/race_lt3.err")）"; exit 1; }
echo "ok - 并发：kge 持有清单行共享锁时下线约 5 秒（${E16} ms）后以 55P03“稍后重试”失败而不是挂起，库不变；kge 提交后下线成功" >> "$OUT"
# 场景 16b（0052）：一笔重新启用（新增函数对清单行 FOR UPDATE 并改状态）未提交时，另一笔重新启用同一主机名同样约 5 秒后 55P03 失败；
# 去掉新增函数的锁等待上限时后一笔一直等到前一笔提交，再按“已在清单里”失败（不是 55P03）
printf '%s\n' "BEGIN; $AS_STAFF SELECT policy.add_source_domain('$H16', '合成锁等待教育局（重新启用）'); SELECT pg_sleep(12); COMMIT;" \
  | psql_run >/dev/null 2>"$WORK/race_lt4.err" &
holder_sleeping || { wait; echo "db-test: 失败（并发场景 16b 的先一笔重新启用没有开始：$(cat "$WORK/race_lt4.err")）"; exit 1; }
T16=$(date +%s%N)
if printf '%s\n' '\set VERBOSITY verbose' "$AS_STAFF SELECT policy.add_source_domain('$H16', '合成锁等待教育局（第二笔）');" \
   | psql_run >/dev/null 2>"$WORK/race_lt5.err"; then
  wait; echo "db-test: 失败（另一笔重新启用未提交时新增居然成功了）"; exit 1
fi
E16=$(since_ms "$T16")
wait || { echo "db-test: 失败（并发场景 16b 的先一笔重新启用失败：$(cat "$WORK/race_lt4.err")）"; exit 1; }
grep -q '55P03' "$WORK/race_lt5.err" && grep -q '稍后重试' "$WORK/race_lt5.err" && [ "$E16" -ge 4500 ] && [ "$E16" -lt 10000 ] \
  || { echo "db-test: 失败（并发场景 16b 的新增不是约 5 秒后以 55P03“稍后重试”失败：${E16} ms，$(cat "$WORK/race_lt5.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT status || ':' || issuer FROM policy.source_domain WHERE host = '$H16'" </dev/null)" = "active:合成锁等待教育局（重新启用）" ] \
  || { echo "db-test: 失败（并发场景 16b 后清单行不是先一笔重新启用的结果）"; exit 1; }
echo "ok - 并发：另一笔重新启用未提交时新增约 5 秒（${E16} ms）后以 55P03“稍后重试”失败而不是挂起" >> "$OUT"
# 场景 17（0051，PR3 已知风险 2）：迁移执行者直接把合成学生 Z1（79za 建，有效绑定家长 gZ）的届别改成已毕业的一届后事务未提交；
# 家长此时重新开通已暂停的学科。选科函数对档案行 FOR SHARE，等改届别提交后按新届别核对，以 GRADE_OUT_OF_RANGE 拒绝；
# 去掉 FOR SHARE 时按改动前的届别（在读）放行。计量时钟 t.clock 沿用场景 15 固定的当天北京时间 10:00，与墙钟无关
Z1=f7a00000-0000-4000-8000-0000000000c1; GZ=f7a00000-0000-4000-8000-0000000000a1
AS_GZ="SELECT set_config('app.account_id', '$GZ', false); SET ROLE starbuddy_bff;"
Z1S="$(psql_run -tAc "SELECT e.subject FROM platform.subject_enrollment e WHERE e.student_id = '$Z1' AND e.status = 'paused'
                         AND EXISTS (SELECT 1 FROM platform.curriculum_version v WHERE v.subject = e.subject AND v.status = 'published')
                       ORDER BY e.subject::text LIMIT 1" </dev/null)"
# 已毕业的一届：按计量时钟当前年级为 10 的届别 = 现届别 − (10 − 当前年级)；Z1 当前不在 7–9 时为空，前提不成立
Z1OLD="$(psql_run -tAc "SELECT exam_cohort - (10 - platform.current_grade(exam_cohort, platform.meter_now())) FROM platform.student_profile WHERE id = '$Z1'" </dev/null)"
[ -n "$Z1S" ] && [ -n "$Z1OLD" ] && [ "$(psql_run -tAc "SELECT platform.current_grade($Z1OLD::smallint, platform.meter_now()) IS NULL" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（并发场景 17 的前提不成立：79za 的 Z1 没有已暂停的学科（$Z1S）或当前不在 7–9（$Z1OLD））"; exit 1; }
printf '%s\n' "BEGIN; UPDATE platform.student_profile SET exam_cohort = $Z1OLD, exam_cohort_source = 'staff_correction' WHERE id = '$Z1';
  SELECT pg_sleep(6); COMMIT;" | psql_run >/dev/null 2>"$WORK/race_en1.err" &
# 等改届别的事务已更新档案行、进入 pg_sleep(6) 后再开始选科（不靠固定的 0.5 秒；机器繁忙时也成立）
holder_sleeping 6 || { wait; echo "db-test: 失败（并发场景 17 的改届别事务没有开始：$(cat "$WORK/race_en1.err")）"; exit 1; }
if printf '%s\n' "$AS_GZ SELECT platform.enroll_subject('$Z1', '$Z1S');" | psql_run >/dev/null 2>"$WORK/race_en2.err"; then
  wait; echo "db-test: 失败（改届别进行中的选科没有等它提交，按改动前的届别放行了）"; exit 1
fi
wait || { echo "db-test: 失败（并发场景 17 的改届别失败：$(cat "$WORK/race_en1.err")）"; exit 1; }
grep -q 'GRADE_OUT_OF_RANGE' "$WORK/race_en2.err" || { echo "db-test: 失败（并发场景 17 的选科不是被年级核对拒绝：$(cat "$WORK/race_en2.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT status FROM platform.subject_enrollment WHERE student_id = '$Z1' AND subject = '$Z1S'" </dev/null)" = "paused" ] \
  || { echo "db-test: 失败（并发场景 17 后学科被重新开通了）"; exit 1; }
echo "ok - 并发：改届别进行中的选科等它提交后按新届别核对年级，已毕业时拒绝（选科函数对档案行 FOR SHARE）" >> "$OUT"

# 0041 的数据前提：库里已有已发布的考情数据（77、79v 经签字发布的）时，0041 up 以明确信息中止（自报工号的发布补不出签字记录）
if psql_run -1 < "$(ls "$ROOT"/db/migrations/0041_*.up.sql)" >/dev/null 2>"$WORK/up41.err"; then
  echo "db-test: 失败（库里已有已发布考情数据时 0041 up 仍然执行成功）"; exit 1
fi
grep -q '0041 中止：已有已发布或已撤下的考情数据' "$WORK/up41.err" || { echo "db-test: 失败（0041 up 不是被数据前提检查中止：$(cat "$WORK/up41.err")）"; exit 1; }
echo "ok - 已有已发布考情数据时 0041 up 以明确信息中止" >> "$OUT"
# 0041、0040 的回滚：签字人表有记录（只追加不能删）时，回滚必须以明确错误中止，且什么都不改
[ "$(psql_run -tAc "SELECT count(*) > 0 FROM platform.policy_signoff" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0041 回滚测试的前提不成立：库里没有考情签字记录）"; exit 1; }
for n in 0041 0040; do
  if psql_run -1 < "$(ls "$ROOT"/db/migrations/${n}_*.down.sql)" >/dev/null 2>"$WORK/down$n.err"; then
    echo "db-test: 失败（有考情签字记录时 $n 回滚仍然执行成功）"; exit 1
  fi
  grep -q "$n 回滚中止" "$WORK/down$n.err" || { echo "db-test: 失败（$n 回滚不是被签字记录检查中止：$(cat "$WORK/down$n.err")）"; exit 1; }
done
[ "$(psql_run -tAc "SELECT count(*) FROM pg_proc WHERE oid = 'policy.sign_record(text, uuid, text, text)'::regprocedure" </dev/null)" = "1" ] \
  || { echo "db-test: 失败（0041 回滚中止后签字函数不在了）"; exit 1; }
echo "ok - 有考情签字记录时 0041、0040 回滚以明确错误中止，库不变" >> "$OUT"
# 0048、0047、0045 的回滚：已有来源清单操作记录、控制线、届别更正记录（79y 与上面的并发场景写入）时，回滚以明确错误中止，且什么都不改
# （0046 只改策略与守卫，没有数据前提）
for n in 0048 0047 0045; do
  if psql_run -1 < "$(ls "$ROOT"/db/migrations/${n}_*.down.sql)" >/dev/null 2>"$WORK/down$n.err"; then
    echo "db-test: 失败（已有数据时 $n 回滚仍然执行成功）"; exit 1
  fi
  grep -q "$n 回滚中止" "$WORK/down$n.err" || { echo "db-test: 失败（$n 回滚不是被数据检查中止：$(cat "$WORK/down$n.err")）"; exit 1; }
done
[ "$(psql_run -tAc "SELECT to_regprocedure('policy.add_source_domain(text, text)') IS NOT NULL AND to_regclass('policy.control_line') IS NOT NULL
                      AND to_regprocedure('platform.correct_exam_cohort(uuid, smallint)') IS NOT NULL" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0045、0047、0048 回滚中止后对象不在了）"; exit 1; }
echo "ok - 已有来源清单操作记录、控制线、届别更正记录时 0048、0047、0045 回滚以明确错误中止，库不变" >> "$OUT"
# 0052、0051 在有数据时单独 down → up（PR4）：回滚没有数据前提，只换回函数。0052 回滚后新增、下线没有锁等待上限，取锁函数不设空闲上限；
# 0051 回滚后选科不核对年级（场景 17 后 Z1 已毕业：库函数放行到订阅判断）。重新执行后回到 0051、0052 的版本
U51="$(ls "$ROOT"/db/migrations/0051_*.up.sql)"; D51="$(ls "$ROOT"/db/migrations/0051_*.down.sql)"
U52="$(ls "$ROOT"/db/migrations/0052_*.up.sql)"; D52="$(ls "$ROOT"/db/migrations/0052_*.down.sql)"
pr4_state() {  # 新增、下线有 lock_timeout | 取锁函数设空闲上限 | 选科核对年级 | Z1（已毕业）重新开通已暂停学科的结果（事务回滚，不留改动）
  local a b
  a="$(psql_run -tAc "SELECT (SELECT bool_and(coalesce('lock_timeout=5s' = ANY (proconfig), false)) FROM pg_proc
                               WHERE oid IN ('policy.add_source_domain(text, text)'::regprocedure, 'policy.retire_source_domain(text)'::regprocedure))
                        || '|' || (SELECT position('idle_in_transaction_session_timeout' IN prosrc) > 0 FROM pg_proc WHERE oid = 'policy.lock_active_source_host(text)'::regprocedure)
                        || '|' || (SELECT position('GRADE_OUT_OF_RANGE' IN prosrc) > 0 FROM pg_proc WHERE oid = 'platform.enroll_subject(uuid, platform.subject)'::regprocedure)" </dev/null)"
  b="$(printf '%s\n' "BEGIN; $AS_GZ SELECT platform.enroll_subject('$Z1', '$Z1S')->>'outcome'; ROLLBACK;" | psql_run -tA -v VERBOSITY=terse 2>&1 | tail -n1 | sed 's/^.*ERROR: *//')"
  echo "$a|$b"
}
[ "$(pr4_state)" = "true|true|true|GRADE_OUT_OF_RANGE" ] \
  || { echo "db-test: 失败（0051、0052 回滚测试的前提不成立：$(pr4_state)）"; exit 1; }
psql_run -1 < "$D52" >/dev/null 2>"$WORK/down52.err" || { echo "db-test: 失败（有数据时 0052 回滚失败：$(cat "$WORK/down52.err")）"; exit 1; }
psql_run -1 < "$D51" >/dev/null 2>"$WORK/down51.err" || { echo "db-test: 失败（有数据时 0051 回滚失败：$(cat "$WORK/down51.err")）"; exit 1; }
[ "$(pr4_state)" = "false|false|false|no_subscription" ] \
  || { echo "db-test: 失败（0052、0051 回滚后没有回到 0050 的状态：$(pr4_state)）"; exit 1; }
echo "ok - 有数据时 0052、0051 单独回滚：新增、下线去掉锁等待上限，取锁函数不设空闲上限，选科不再核对年级" >> "$OUT"
# 0050、0049 在有数据时单独 down → up（PR3）：回滚没有数据前提。0050 回滚恢复 0048 及之前的来源清单函数与 url_host；
# 0049 回滚删掉已授予的 cohort_correction（回滚说明）、恢复导出清单，客服分支一律 42501（不恢复“任何 active staff 都能更正”，
# PR3 审查意见 3）。重新执行后函数与清单回到 0049、0050 的版本（cohort_correction 要由 DBA 重新授予）
U49="$(ls "$ROOT"/db/migrations/0049_*.up.sql)"; D49="$(ls "$ROOT"/db/migrations/0049_*.down.sql)"
U50="$(ls "$ROOT"/db/migrations/0050_*.up.sql)"; D50="$(ls "$ROOT"/db/migrations/0050_*.down.sql)"
[ "$(psql_run -tAc "SELECT count(*) > 0 FROM platform.staff_permission WHERE permission = 'cohort_correction'
                      AND staff_id = '5f000000-0000-4000-8000-00000000005f'" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0049 回滚测试的前提不成立：sf1 没有已授予的 cohort_correction）"; exit 1; }
psql_run -1 < "$D50" >/dev/null 2>"$WORK/down50.err" || { echo "db-test: 失败（有数据时 0050 回滚失败：$(cat "$WORK/down50.err")）"; exit 1; }
psql_run -1 < "$D49" >/dev/null 2>"$WORK/down49.err" || { echo "db-test: 失败（有数据时 0049 回滚失败：$(cat "$WORK/down49.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT (SELECT count(*) FROM platform.staff_permission WHERE permission = 'cohort_correction') = 0
                      AND to_regprocedure('policy.lock_active_source_host(text)') IS NULL
                      AND to_regprocedure('platform.meter_refund_grade_changed(uuid)') IS NULL
                      AND NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'platform.diagnosis_session'::regclass
                                       AND attname IN ('start_grade', 'abandon_reason') AND NOT attisdropped)
                      AND (SELECT export_topic IS NULL FROM platform.personal_table_registry WHERE relname = 'exam_cohort_change')
                      AND policy.url_host('https://a.example:443/x') = 'a.example'" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0050、0049 回滚后没有回到 0048 的状态）"; exit 1; }
# 回滚后客服分支：即使是原来有权限的 active staff（sf1，授予已随回滚删除）也一律 42501，对存在与不存在的学生结果相同
Y1NEXT="$(psql_run -tAc "SELECT exam_cohort + 1 FROM platform.student_profile WHERE id = '$Y1'" </dev/null)"
STAFF_AFTER_DOWN="$(printf '%s\n' "$AS_STAFF SELECT platform.correct_exam_cohort('$Y1', ${Y1NEXT}::smallint);" | psql_run 2>&1 >/dev/null || true)"
STAFF_AFTER_DOWN_X="$(printf '%s\n' "$AS_STAFF SELECT platform.correct_exam_cohort(gen_random_uuid(), 2028::smallint);" | psql_run 2>&1 >/dev/null || true)"
grep -q 'not allowed' <<< "$STAFF_AFTER_DOWN" && [ "$STAFF_AFTER_DOWN" = "$STAFF_AFTER_DOWN_X" ] \
  || { echo "db-test: 失败（0049 回滚后客服分支没有一律拒绝：$STAFF_AFTER_DOWN / $STAFF_AFTER_DOWN_X）"; exit 1; }
echo "ok - 有数据时 0050、0049 单独回滚：删掉 cohort_correction 授予、恢复 0048 及之前的函数与导出清单；客服分支一律 42501（不退回任何 active staff 可更正）" >> "$OUT"
psql_run -1 < "$U49" >/dev/null 2>"$WORK/up49.err" || { echo "db-test: 失败（0049 回滚后重新执行失败：$(cat "$WORK/up49.err")）"; exit 1; }
# 0050 的数据前提：清单里有不是规范形式的主机名（这里是不能往返解码的 xn-- 标签，0048 的规范化收得下）时以明确信息中止，库不变
psql_run </dev/null -c "INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('xn--zzzzzzzzzzzz.example', '合成', 'synthetic-dba')"
if psql_run -1 < "$U50" >/dev/null 2>"$WORK/up50.err"; then echo "db-test: 失败（清单里有不规范的主机名时 0050 仍然执行成功）"; exit 1; fi
grep -q '0050 中止' "$WORK/up50.err" && grep -q 'xn--zzzzzzzzzzzz.example' "$WORK/up50.err" \
  || { echo "db-test: 失败（0050 不是被主机名前提中止：$(cat "$WORK/up50.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT to_regprocedure('policy.idn_label_ok(text)') IS NULL" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0050 中止后库被改动了）"; exit 1; }
psql_run </dev/null -c "DELETE FROM policy.source_domain WHERE host = 'xn--zzzzzzzzzzzz.example'"
echo "ok - 清单里有不是规范形式的主机名时 0050 以明确信息中止（列出主机名），库不变" >> "$OUT"
psql_run -1 < "$U50" >/dev/null 2>"$WORK/up50.err" || { echo "db-test: 失败（0050 回滚后重新执行失败：$(cat "$WORK/up50.err")）"; exit 1; }
[ "$(psql_run -tAc "SELECT to_regprocedure('policy.lock_active_source_host(text)') IS NOT NULL
                      AND to_regprocedure('platform.meter_refund_grade_changed(uuid)') IS NOT NULL
                      AND (SELECT export_topic = 'cohort_changes' FROM platform.personal_table_registry WHERE relname = 'exam_cohort_change')
                      AND policy.url_host('https://a.example:443/x') IS NULL" </dev/null)" = "t" ] \
  || { echo "db-test: 失败（0049、0050 重新执行后状态不对）"; exit 1; }
echo "ok - 0049、0050 回滚后重新执行，函数与清单回到 0049、0050 的版本" >> "$OUT"
psql_run -1 < "$U51" >/dev/null 2>"$WORK/up51.err" || { echo "db-test: 失败（0051 回滚后重新执行失败：$(cat "$WORK/up51.err")）"; exit 1; }
psql_run -1 < "$U52" >/dev/null 2>"$WORK/up52.err" || { echo "db-test: 失败（0052 回滚后重新执行失败：$(cat "$WORK/up52.err")）"; exit 1; }
[ "$(pr4_state)" = "true|true|true|GRADE_OUT_OF_RANGE" ] \
  || { echo "db-test: 失败（0051、0052 重新执行后状态不对：$(pr4_state)）"; exit 1; }
echo "ok - 0051、0052 回滚后重新执行：锁等待上限、空闲上限与选科年级核对都回来了" >> "$OUT"

n=$(grep -c 'ok - ' "$OUT" || true)
echo "db-test: 通过（${n} 项断言）"
