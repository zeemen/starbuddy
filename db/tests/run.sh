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
  case "$(basename "$f")" in 0011_*|0012_*|0017_*|0018_*|0019_*|0024_*|0026_*|0028_*) continue ;; esac  # 0012、0017–0019、0024、0026、0028 依赖 0011 的 kge 表
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

n=$(grep -c 'ok - ' "$OUT" || true)
echo "db-test: 通过（${n} 项断言）"
