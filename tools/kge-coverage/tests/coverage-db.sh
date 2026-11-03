#!/usr/bin/env bash
# KGE-02 覆盖矩阵的库上核对（spec 验收“覆盖矩阵报表在二次函数主题上生成，数字与库里一致”）。只用本机一次性 Postgres（规则 9）。
# 步骤：全部 up 迁移 → 仓库里的二次函数种子（db/seeds/kge/aya-fun-0.1.0）→ 合成打标（tests/fixtures/db-tagging-report.json，
#   题目 ID 全部合成）写成 platform.item + kge.item_tag → 以只读事务执行 sql/coverage.sql → 与报表计算的结果逐字节比对。
# 退出码 3 = 本机没有 Postgres（由调用方决定是否算失败）。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/.."
REPO="$(cd "$TOOL/../.." && pwd)"
SEED="$REPO/db/seeds/kge/aya-fun-0.1.0"
FIXTURE="$HERE/fixtures/db-tagging-report.json"
LEGACY_ID='^[A-Z]{1,2}-[A-Z]{2,4}-[0-9A-Z]{2,6}$'
# shellcheck source=/dev/null
source "$REPO/db/tests/pg-ephemeral.sh"
pg_ephemeral_start || exit $?
TMP="$(mktemp -d)"; chmod 755 "$TMP"; trap 'rm -rf "$TMP"; pg_ephemeral_stop' EXIT
fail() { echo "kge-coverage-db: 失败：$*"; exit 1; }
q() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -At -v ON_ERROR_STOP=1 -c "$1"; }

# psql 出错的退出码也是 3，与“没有 Postgres”混淆：以下失败一律转成 1
apply "$ROOT/db/bootstrap/roles.sql" || fail "角色 bootstrap 执行出错"
mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
apply "${UPS[@]}" || fail "迁移执行出错"

# 种子要求能力视图已登记（与 tools/kge-inventory/tests/import-db.sh 相同的做法）
{
  echo "INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version) VALUES ('e0000000-0000-4000-8000-0000000000f1', 'nanjing', 2027, 9, 'suke-2024', 'aya', '0.9.9');"
  node -e 'const p=require(process.argv[1]);console.log(p.nodes.map(n=>n.id).filter(i=>i.includes("-FUN-")).join("\n"))' "$REPO/adapters/aya/content/pack.json" \
    | grep -E "$LEGACY_ID" \
    | while read -r id; do echo "INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('aya', '$id', 'e0000000-0000-4000-8000-0000000000f1', '$id');"; done
} | psql_run -1 || fail "能力视图登记出错"
psql_run < "$SEED/import.sql" || fail "种子执行出错"
MV="$(node -e 'console.log(require(process.argv[1]).map_version_id)' "$SEED/import-report.json")"

HAS0024="$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'kge' AND table_name = 'item_tag' AND column_name = 'tag_source'")"
cd "$TOOL"
node --no-warnings tests/db-fixture.ts sql "$SEED" "$FIXTURE" "$HAS0024" | psql_run -1 || fail "合成打标写入出错"
node --no-warnings tests/db-fixture.ts expected "$SEED" "$FIXTURE" "$HAS0024" > "$TMP/expected.txt"

# 报表与 SQL 都从同一份种子与标签出发
node --no-warnings src/main.ts --map "$SEED" --tagging "$FIXTURE" --scope item_tag --out "$TMP/report" >/dev/null
[ -s "$TMP/report/coverage-0.1.0.md" ] || fail "报表没有生成"

cp "$TOOL/sql/coverage.sql" "$TMP/coverage.sql"; chmod 644 "$TMP/coverage.sql"
as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -v ON_ERROR_STOP=1 -v map_version_id="$MV" -f "$TMP/coverage.sql" > "$TMP/actual.txt" \
  || fail "coverage.sql 执行出错"
if ! cmp -s "$TMP/expected.txt" "$TMP/actual.txt"; then
  diff "$TMP/expected.txt" "$TMP/actual.txt" | head -40
  fail "coverage.sql 的输出与报表计算不一致"
fi
sed -n '/^## matrix$/,/^## summary$/p' "$TMP/actual.txt" | sed '1d;$d' > "$TMP/matrix.csv"
cmp -s "$TMP/matrix.csv" "$TMP/report/coverage-0.1.0.csv" || fail "matrix 段与报表 coverage-0.1.0.csv 不是逐字节相同"
echo "ok - 二次函数主题：coverage.sql 在库里算出的矩阵、空节点、节点数分布与报表逐字节一致（0024 列：$([ "$HAS0024" = 1 ] && echo 有 || echo 无)）"

# 只读：coverage.sql 在 READ ONLY 事务里执行，写入会被拒绝；不存在的地图版本、缺参数都报错
before="$(q "SELECT count(*) FROM kge.item_tag")"
if as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -v ON_ERROR_STOP=1 -v map_version_id=00000000-0000-4000-8000-000000000000 -f "$TMP/coverage.sql" >/dev/null 2>&1; then
  fail "不存在的地图版本应报错"
fi
if as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -f "$TMP/coverage.sql" >/dev/null 2>&1; then
  fail "缺 map_version_id 应报错"
fi
grep -Eiq '^[^-]*\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE|GRANT)\b' "$TMP/coverage.sql" && fail "coverage.sql 不应含写语句"
[ "$(q "SELECT count(*) FROM kge.item_tag")" = "$before" ] || fail "coverage.sql 改动了库"
echo "ok - coverage.sql 只读；缺参数或地图版本不存在时报错"
echo "kge-coverage-db: 通过"
