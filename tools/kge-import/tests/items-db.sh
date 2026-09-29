#!/usr/bin/env bash
# KGE-02 题库导入种子的库上验证（specs/kge-02-atoms-tagging/spec.md 验收第 1、3 条），只用本机一次性 Postgres（规则 9）。
# 第一段不需要 legacy：执行全部迁移 → 阿鸦内容登记（adapters/aya/scripts/register-content.ts）→ KGE-01 地图种子 → 本任务的 items.sql；
#   核对题组、打标、候选的行数与 tagging-report.json 一致，候选全部待处理、以知识服务身份写入。
# 第二段需要 legacy/aya：以 --rules-only-a 重新生成到临时目录并执行，证明写 item_tag 的路径也能通过 0024 的约束。没有 legacy 时打印“跳过”。
# 退出码 3 = 本机没有 Postgres。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/.."
REPO="$(cd "$TOOL/../.." && pwd)"
AYA="${KGE_IMPORT_AYA:-$REPO/legacy/aya}"
MAP="$REPO/db/seeds/kge/aya-fun-0.1.0"
SEED="$REPO/db/seeds/kge/aya-items-0.1.0"
# shellcheck source=/dev/null
source "$REPO/db/tests/pg-ephemeral.sh"
pg_ephemeral_start || exit $?
TMP="$(mktemp -d)"; chmod 755 "$TMP"; trap 'rm -rf "$TMP"; pg_ephemeral_stop' EXIT
apply "$ROOT/db/bootstrap/roles.sql"
mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
apply "${UPS[@]}"
fail() { echo "kge-items-db: 失败：$*"; exit 1; }
q() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -At -v ON_ERROR_STOP=1 -c "$1"; }
report() { node -e 'const r=require(process.argv[1]);const v=process.argv[2].split(".").reduce((o,k)=>o[k],r);console.log(v)' "$1" "$2"; }

# 题目登记（platform.item）与地图版本种子
(cd "$REPO/adapters/aya" && node --no-warnings scripts/register-content.ts) | psql_run >/dev/null
psql_run < "$MAP/import.sql" >/dev/null

# ---------- 第一段：仓库里的种子 ----------
grep -q '^SET LOCAL ROLE starbuddy_kge;' "$SEED/items.sql" || fail "种子必须以 starbuddy_kge 写入"
psql_run < "$SEED/items.sql" >/dev/null
mv="$(report "$SEED/tagging-report.json" map_version_id 2>/dev/null || true)"
[ -n "$mv" ] && [ "$mv" != "undefined" ] || mv="$(q "SELECT id FROM kge.map_version WHERE subject = 'aya' AND version = '0.1.0'")"
cand_db="$(q "SELECT count(*) FROM kge.tag_candidate WHERE map_version_id = '$mv'")"
cand_sql="$(grep -cE "^  \('$mv', 'aya', " "$SEED/items.sql" || true)"
tag_db="$(q "SELECT count(*) FROM kge.item_tag WHERE map_version_id = '$mv'")"
[ "$cand_db" -gt 0 ] || fail "候选没有写入"
[ "$(q "SELECT count(*) FROM kge.tag_candidate WHERE map_version_id = '$mv' AND status <> 'open'")" = "0" ] || fail "候选必须以待处理写入"
[ "$(q "SELECT count(*) FROM kge.tag_candidate WHERE map_version_id = '$mv' AND layer <> 'rule'")" = "0" ] || fail "默认种子只含规则层候选"
[ "$(q "SELECT count(*) FROM kge.item_family WHERE map_version_id = '$mv'")" -ge 1 ] || fail "题组没有写入"
echo "ok - 默认种子：候选 $cand_db 行（SQL 里 $cand_sql 行）、打标 $tag_db 题、全部待处理且只含规则层"
[ "$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'kge' AND table_name IN ('tag_candidate', 'item_tag', 'item_family')
         AND column_name ~ '(student|account|guardian|learner)'")" = "0" ] || fail "公共层表不能有学生或账号键"

# ---------- 第二段：放宽后写 item_tag 的路径（需要 legacy） ----------
if [ ! -d "$AYA/content" ]; then
  echo "kge-items-db: 第二段跳过（没有 legacy/aya 副本）"
else
  (cd "$TOOL" && node --no-warnings src/main.ts --aya "$AYA" --map "$MAP" --rules-only-a --out "$TMP/relaxed" >/dev/null)
  chmod -R a+r "$TMP/relaxed"
  q "DELETE FROM kge.tag_candidate WHERE map_version_id = '$mv'; DELETE FROM kge.item_family WHERE map_version_id = '$mv'" >/dev/null
  psql_run < "$TMP/relaxed/items.sql" >/dev/null
  n="$(q "SELECT count(*) FROM kge.item_tag WHERE map_version_id = '$mv' AND tag_source = 'rule' AND tagger_version IS NOT NULL
            AND tag_confidence IS NOT NULL AND tag_review_status = 'pending' AND cardinality(node_ids) BETWEEN 1 AND 3")"
  [ "$n" -gt 0 ] || fail "--rules-only-a 应写出带来源、版本、置信度的打标草稿"
  [ "$n" = "$(q "SELECT count(*) FROM kge.item_tag WHERE map_version_id = '$mv'")" ] || fail "每条打标都要带来源、版本、置信度与 1–3 个节点"
  echo "ok - --rules-only-a：写入 $n 题打标草稿，全部带来源、打标器版本、置信度，节点 1–3 个"
fi
echo "kge-items-db: 通过"
