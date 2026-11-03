#!/usr/bin/env bash
# make zhixing-rubric-check（冲刺 4-K，spec 设计要点 3）：内容包 adapters/zhixing/content/pack.json 与原子层 kge.scoring_point 逐字段一致。
#   - 给了 ZHIXING_RUBRIC_EXPORT=<原子层导出.json>（人在演练库上用 tools/kge-import/sql/export-scoring-points.sql 导出）时，直接比对该文件，不起库；
#   - 否则在本机一次性 Postgres 上走一遍“内容包 → kge.scoring_point → 原子层导出”，再与内容包比对（合成 4 题）。
# 不一致即失败（退出 1）；退出码 3 = 本机没有 Postgres。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${ZHIXING_RUBRIC_EXPORT:-}" ]; then
  REPO="$(cd "$HERE/../../../.." && pwd)"
  cd "$HERE/../.." && exec node --no-warnings src/daofa/main.ts rubric-check --pack "$REPO/adapters/zhixing/content/pack.json" --export "$ZHIXING_RUBRIC_EXPORT"
fi
source "$HERE/db-lib.sh"
fail() { echo "zhixing-rubric-check: 失败：$*"; exit 1; }
daofa_db_start || { rc=$?; [ $rc -eq 3 ] && exit 3; fail "一次性库准备出错"; }
gen_map "$TMP/m" 0.0.1
run_syn "$TMP/m/import.sql" || fail "合成地图种子执行出错"
daofa rubric --pack "$PACK" --map "$TMP/m" --out "$TMP/r" >/dev/null
run_syn "$TMP/r/scoring-points.sql" || fail "采分点种子执行出错"
cp "$TOOL/sql/export-scoring-points.sql" "$TMP/export.sql"; chmod 644 "$TMP/export.sql"
as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v ON_ERROR_STOP=1 -v subject=zhixing -f "$TMP/export.sql" > "$TMP/export.json" \
  || fail "原子层导出出错"
daofa rubric-check --pack "$PACK" --export "$TMP/export.json"
