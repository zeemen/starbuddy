#!/usr/bin/env bash
# 在一次性的本地 Postgres 上验证迁移与 RLS（spec：specs/learner-schema-v0.9/spec.md）。
#   1. 全部 up → 全部 down → 全部 up，再把最新迁移单独 down → up（迁移可回滚，开发计划 冲刺 1-B）
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
# 最新迁移单独 down → up（只回滚最后一步时，前面的迁移不受影响）
apply_or_exit "${DOWNS[0]}"
apply_or_exit "${UPS[${#UPS[@]}-1]}"
echo "db-test: 最新迁移单独 down → up 通过（$(basename "${UPS[${#UPS[@]}-1]}")）"

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
  case "$(basename "$f")" in 0011_*|0012_*|0017_*|0018_*|0019_*|0024_*|0026_*|0028_*|0038_*|0039_*|0040_*|0041_*|0042_*|0043_*) continue ;; esac  # 0012、0017–0019、0024、0026、0028、0038、0043 依赖 0011 的 kge 表；0039、0040 依赖 0018（0039 还依赖 0012）；0041 依赖 0040；0042 依赖 0039
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

n=$(grep -c 'ok - ' "$OUT" || true)
echo "db-test: 通过（${n} 项断言）"
