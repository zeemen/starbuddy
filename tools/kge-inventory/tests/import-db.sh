#!/usr/bin/env bash
# KGE-01 骨架导入的库上验证（specs/kge-01-skeleton-import/spec.md 验收第 1–4 条），只用本机一次性 Postgres（规则 9）：
#   1. 重新生成阿鸦 fun 的导入文件，必须与仓库里 db/seeds/kge/aya-fun-0.1.0 逐字节一致（确定性、种子没过期）；
#   2. 执行 import.sql（以 starbuddy_kge 写入），发布前校验只剩 orphan 与 missing_boundary，条数与工作单一致，且不能发布；
#   3. 用合成的补齐覆盖文件生成 0.2.0，校验全绿并能发布；旧编码全部进迁移表，每个节点都有能力视图挂接。
# 退出码 3 = 本机没有 Postgres。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/.."
REPO="$(cd "$TOOL/../.." && pwd)"
AYA="$REPO/legacy/aya"
SEED="$REPO/db/seeds/kge/aya-fun-0.1.0"
# shellcheck source=/dev/null
source "$REPO/db/tests/pg-ephemeral.sh"
pg_ephemeral_start || exit $?
apply "$ROOT/db/bootstrap/roles.sql"
mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
apply "${UPS[@]}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"; pg_ephemeral_stop 2>/dev/null || true' EXIT
fail() { echo "kge-import-db: 失败：$*"; exit 1; }
q() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -At -v ON_ERROR_STOP=1 -c "$1"; }

cd "$TOOL"
node --no-warnings src/import/main.ts --aya "$AYA" --out "$TMP/v1" >/dev/null
for f in import.sql worklist.md import-report.json; do
  cmp -s "$TMP/v1/$f" "$SEED/$f" || fail "$f 与仓库里的种子不一致（重新生成：make kge-import）"
done

# 能力视图：登记阿鸦 fun 专题的 skill_node（与 adapters/aya/scripts/register-content.ts 同一套 local_id）
ids="$(node -e 'const p=require(process.argv[1]);console.log(p.nodes.filter(n=>n.id.includes("-FUN-")).map(n=>n.id).join(" "))' "$REPO/adapters/aya/content/pack.json")"
{
  echo "INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version) VALUES ('e0000000-0000-4000-8000-0000000000f1', 'nanjing', 2027, 9, 'suke-2024', 'aya', '0.9.9');"
  for id in $ids; do
    echo "INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('aya', '$id', 'e0000000-0000-4000-8000-0000000000f1', '$id');"
  done
} | psql_run -1

psql_run < "$SEED/import.sql"  # 文件自带 BEGIN / COMMIT
mv1="$(node -e 'console.log(require(process.argv[1]).map_version_id)' "$SEED/import-report.json")"
got="$(q "SELECT check_name || '=' || n FROM (SELECT check_name, count(*) AS n FROM kge.validate_map_version('$mv1') WHERE severity = 'error' GROUP BY check_name) x ORDER BY 1" | tr '\n' ' ')"
want_orphan="$(grep -c '孤点' "$SEED/worklist.md" || true)"
want_boundary="$(grep -cE '\| 缺(包含|识别特征)' "$SEED/worklist.md" || true)"
[ "$got" = "missing_boundary=$want_boundary orphan=$want_orphan " ] || fail "0.1.0 的阻断问题应只有 missing_boundary=$want_boundary、orphan=$want_orphan，实际：$got"
if q "SET ROLE starbuddy_kge; SELECT kge.publish_map_version('$mv1')" >/dev/null 2>&1; then fail "有阻断问题时不应能发布"; fi
[ "$(q "SELECT count(*) FROM kge.id_migration WHERE map_version_id = '$mv1'")" = "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$mv1' AND level = 'node'")" ] \
  || fail "每个导入节点都要有迁移表记录"
[ "$(q "SELECT count(*) FROM kge.knowledge_node n WHERE map_version_id = '$mv1' AND level = 'node' AND NOT EXISTS (SELECT 1 FROM kge.skill_link s WHERE s.map_version_id = n.map_version_id AND s.node_id = n.id)")" = "0" ] \
  || fail "每个导入节点都要挂到能力视图"
echo "ok - 0.1.0 草稿：只剩 orphan $want_orphan 条、missing_boundary $want_boundary 条，与工作单一致，不能发布"

node --no-warnings tests/import-complete-overrides.ts "$AYA" fun > "$TMP/complete.json"
node --no-warnings src/import/main.ts --aya "$AYA" --version 0.2.0 --overrides "$TMP/complete.json" --out "$TMP/v2" >/dev/null
psql_run < "$TMP/v2/import.sql"
mv2="$(node -e 'console.log(require(process.argv[1]).map_version_id)' "$TMP/v2/import-report.json")"
left="$(q "SELECT count(*) FROM kge.validate_map_version('$mv2') WHERE severity = 'error'")"
[ "$left" = "0" ] || fail "合成补齐后仍有 $left 个阻断问题：$(q "SELECT check_name || ':' || object_id FROM kge.validate_map_version('$mv2') WHERE severity = 'error' LIMIT 5" | tr '\n' ' ')"
q "SET ROLE starbuddy_kge; SELECT kge.publish_map_version('$mv2')" >/dev/null || fail "补齐后应能发布"
[ "$(q "SELECT status FROM kge.map_version WHERE id = '$mv2'")" = "published" ] || fail "发布后状态应为 published"
echo "ok - 0.2.0（合成补齐）：发布前校验全绿，已发布"
echo "kge-import-db: 通过"
