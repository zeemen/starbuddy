#!/usr/bin/env bash
# 阿鸦适配器端到端测试（spec 验收前两条）：在一次性的本地 Postgres 上
#   1. 执行角色 bootstrap 与全部迁移（db/bootstrap/roles.sql、db/migrations/*.up.sql）；
#   2. 写入一个合成学生（CLAUDE.md 规则 8）；
#   3. 执行内容登记 SQL 两次（第二次验证只增不改）；
#   4. 运行 scenario.ts：适配器 grade / diagnose → 模拟学习服务以 starbuddy_learning 身份落库 → 查询断言。
# 只连本机临时实例（Unix socket，不监听 TCP），结束即删除；不连接任何共享或生产库（规则 9）。
# 退出码：0 通过；1 失败；3 缺依赖（本机没有 Postgres，或没有安装 node_modules），由 Makefile 决定是否算失败。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ADAPTER="$(cd "$HERE/../.." && pwd)"
ROOT="$(cd "$ADAPTER/../.." && pwd)"

if [ ! -d "$ADAPTER/node_modules/ajv" ]; then
  echo "adapter-aya-e2e: 跳过（没有安装依赖；在 adapters/aya 下运行 npm ci）"
  exit 3
fi
PGBIN="${PGBIN:-$( [ -x /usr/lib/postgresql/16/bin/initdb ] && echo /usr/lib/postgresql/16/bin \
                   || ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)}"
if [ -z "$PGBIN" ] && command -v pg_config >/dev/null 2>&1; then PGBIN="$(pg_config --bindir)"; fi
if [ -z "$PGBIN" ] || [ ! -x "$PGBIN/initdb" ]; then
  echo "adapter-aya-e2e: 跳过（本机没有 Postgres 服务端；需要 postgresql-16）"
  exit 3
fi

as_pg() { if [ "$(id -u)" = "0" ]; then runuser -u postgres -- "$@"; else "$@"; fi; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-aya-e2e.XXXXXX")"
if [ "$(id -u)" = "0" ]; then chown postgres "$WORK"; fi
chmod 700 "$WORK"
cleanup() {
  as_pg "$PGBIN/pg_ctl" -D "$WORK/data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

as_pg "$PGBIN/initdb" -D "$WORK/data" -A trust -U postgres -E UTF8 --no-sync >/dev/null
as_pg "$PGBIN/pg_ctl" -D "$WORK/data" -w -l "$WORK/pg.log" \
  -o "-k $WORK -c listen_addresses='' -c fsync=off -c timezone=UTC" start >/dev/null
as_pg "$PGBIN/createdb" -h "$WORK" -U postgres aya_e2e

psql_run() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d aya_e2e -X -q -v ON_ERROR_STOP=1 "$@"; }
fail() { echo "adapter-aya-e2e: 失败（$1）"; exit 1; }

PGOPTIONS='-c client_min_messages=warning' psql_run -1 < "$ROOT/db/bootstrap/roles.sql" || fail "roles.sql"
for f in $(ls "$ROOT"/db/migrations/*.up.sql | sort); do
  PGOPTIONS='-c client_min_messages=warning' psql_run -1 < "$f" || fail "执行 $(basename "$f") 出错"
done

# 断言函数与合成学生（以超级用户准备；正式的建档路径由 db/tests 覆盖）
psql_run -1 <<'SQL' || fail "准备合成数据"
CREATE SCHEMA e2e;
GRANT USAGE ON SCHEMA e2e TO PUBLIC;
CREATE FUNCTION e2e.ok(cond boolean, msg text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL: %', msg; END IF;
  RAISE NOTICE 'ok - %', msg;
END $$;
GRANT EXECUTE ON FUNCTION e2e.ok(boolean, text) TO PUBLIC;
INSERT INTO platform.account (id, kind) VALUES
  ('a0000000-0000-4000-8000-00000000000a', 'guardian'),
  ('a1000000-0000-4000-8000-0000000000a1', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('d1000000-0000-4000-8000-0000000000d1', 'a0000000-0000-4000-8000-00000000000a');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('c1000000-0000-4000-8000-0000000000c1', 'a1000000-0000-4000-8000-0000000000a1', 'd1000000-0000-4000-8000-0000000000d1',
   9, 'nanjing', 'suke-2024', '2012-03-01');
SQL

cd "$ADAPTER"
node --no-warnings scripts/register-content.ts > "$WORK/register.sql"
psql_run < "$WORK/register.sql" || fail "内容登记"
psql_run < "$WORK/register.sql" || fail "内容登记重复执行"
counts=$(psql_run -tA -c "SELECT (SELECT count(*) FROM platform.curriculum_version WHERE subject = 'aya') || '/' ||
                                  (SELECT count(*) FROM platform.skill_node WHERE subject = 'aya') || '/' ||
                                  (SELECT count(*) FROM platform.item WHERE subject = 'aya') || '/' ||
                                  (SELECT count(*) FROM platform.item WHERE subject = 'aya' AND admission = 'source_unverified')" </dev/null)
[ "$counts" = "1/959/721/721" ] || fail "登记数量不对：$counts（期望 课程版本/能力点/题目/未准入 = 1/959/721/721）"
echo "ok - 内容登记：1 个课程版本、959 个能力点、721 道题（全部 source_unverified），重复执行不重复登记" > "$WORK/out"

node --no-warnings tests/e2e/scenario.ts > "$WORK/scenario.sql" || fail "场景脚本出错"
if ! psql_run < "$WORK/scenario.sql" >> "$WORK/out" 2>&1; then
  grep -E 'FAIL|ERROR' "$WORK/out" | head -20
  fail "落库或断言失败"
fi
grep -E 'ok - ' "$WORK/out" | sed -E 's/^.*(ok - )/  \1/'
n=$(grep -c 'ok - ' "$WORK/out" || true)
echo "adapter-aya-e2e: 通过（${n} 项断言）"
