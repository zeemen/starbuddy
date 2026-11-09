#!/usr/bin/env bash
# KGE-01 骨架导入的库上验证（specs/kge-01-skeleton-import/spec.md 验收第 1–4 条），只用本机一次性 Postgres（规则 9）。
# 第一段不需要 legacy（CI 没有 legacy 副本）：执行仓库里的种子，发布前校验的 orphan / missing_boundary 与报告里的清单逐条一致、
#   不能发布；旧编码全部进迁移表，每个节点都挂到能力视图；种子以 starbuddy_kge 写入。
# 第二段需要 legacy/aya（本机只读副本）：重新生成必须与种子逐字节一致；合成补齐（含易错点）后校验全绿并能发布。
#   没有 legacy 时打印“跳过”，严格模式也放行（契约审查阻断 1）。
# 退出码 3 = 本机没有 Postgres。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/.."
REPO="$(cd "$TOOL/../.." && pwd)"
AYA="${KGE_IMPORT_AYA:-$REPO/legacy/aya}"  # 测试“没有 legacy 副本”的路径时可指向不存在的目录
SEED="$REPO/db/seeds/kge/aya-fun-0.1.0"
LEGACY_ID='^[A-Z]{1,2}-[A-Z]{2,4}-[0-9A-Z]{2,6}$'
# shellcheck source=/dev/null
source "$REPO/db/tests/pg-ephemeral.sh"
pg_ephemeral_start || exit $?
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"; pg_ephemeral_stop' EXIT
apply "$ROOT/db/bootstrap/roles.sql"
mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
apply "${UPS[@]}"
fail() { echo "kge-import-db: 失败：$*"; exit 1; }
q() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -At -v ON_ERROR_STOP=1 -c "$1"; }
# 0019 起发布要先由 active staff 签字，再以 starbuddy_review 身份发布（合成 staff 账号）
STAFF=5f000000-0000-4000-8000-0000000000aa; STAFF2=5f000000-0000-4000-8000-0000000000ab
as_staff() { echo "SELECT set_config('app.account_id', '$1', false); SET ROLE starbuddy_review;"; }
# 首次发布算结构性变更：两名 staff 签字（0019），再发布
sign_publish() {
  q "$(as_staff "$STAFF2") SELECT kge.sign_review('map_version', '$1', 'approved', 'high', 'kge01')" >/dev/null
  q "$(as_staff "$STAFF") SELECT kge.sign_review('map_version', '$1', 'approved', 'high', 'kge01'); SELECT kge.publish_map_version('$1')"
}
report() { node -e 'const r=require(process.argv[1]);const v=r[process.argv[2]];console.log(Array.isArray(v)?v.join("\n"):v)' "$1" "$2"; }

# 能力视图：登记阿鸦 fun 专题的 skill_node（与 tools/content-register/aya.ts 同一套 local_id；只收合法编码）
{
  echo "INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version) VALUES ('e0000000-0000-4000-8000-0000000000f1', 'nanjing', 2027, 9, 'suke-2024', 'aya', '0.9.9');"
  node -e 'const p=require(process.argv[1]);console.log(p.nodes.map(n=>n.id).filter(i=>i.includes("-FUN-")).join("\n"))' "$REPO/adapters/aya/content/pack.json" \
    | grep -E "$LEGACY_ID" \
    | while read -r id; do echo "INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('aya', '$id', 'e0000000-0000-4000-8000-0000000000f1', '$id');"; done
  echo "INSERT INTO platform.account (id, kind) VALUES ('$STAFF', 'staff'), ('$STAFF2', 'staff');"
} | psql_run -1

# ---------- 第一段：仓库里的种子 ----------
grep -q '^SET LOCAL ROLE starbuddy_kge;' "$SEED/import.sql" || fail "种子必须以 starbuddy_kge 写入"
grep -q '^SET LOCAL standard_conforming_strings = on;' "$SEED/import.sql" || fail "种子必须固定 standard_conforming_strings"
psql_run < "$SEED/import.sql"  # 文件自带 BEGIN / COMMIT
mv1="$(report "$SEED/import-report.json" map_version_id)"
[ "$(q "SELECT count(*) FROM kge.map_version WHERE id = '$mv1'")" = "1" ] || fail "地图版本没有写入"
checks="$(q "SELECT DISTINCT check_name FROM kge.validate_map_version('$mv1') WHERE severity = 'error' ORDER BY 1" | tr '\n' ' ')"
[ "$checks" = "missing_boundary orphan " ] || fail "0.1.0 的阻断问题应只有 missing_boundary 与 orphan，实际：$checks"
for kind in orphan missing_boundary; do
  got="$(q "SELECT object_id FROM kge.validate_map_version('$mv1') WHERE check_name = '$kind' ORDER BY 1")"
  want="$(report "$SEED/import-report.json" "expected_${kind/orphan/orphans}" | sort)"
  [ "$got" = "$want" ] || fail "$kind 与报告清单不一致：库里 $(wc -l <<<"$got") 条，报告 $(wc -l <<<"$want") 条"
done
err="$(sign_publish "$mv1" 2>&1 >/dev/null || true)"
grep -q '阻断问题' <<<"$err" || fail "有阻断问题时应被发布前校验拒绝，实际：$err"
[ "$(q "SELECT count(*) FROM kge.knowledge_node n WHERE map_version_id = '$mv1' AND level = 'node' AND NOT EXISTS (
       SELECT 1 FROM kge.id_migration m WHERE m.map_version_id = n.map_version_id AND m.new_id = n.id AND m.rule = 'rename')")" = "0" ] \
  || fail "每个导入节点都要有迁移表记录"
[ "$(q "SELECT count(*) FROM kge.knowledge_node n WHERE map_version_id = '$mv1' AND level = 'node' AND NOT EXISTS (
       SELECT 1 FROM kge.skill_link s WHERE s.map_version_id = n.map_version_id AND s.node_id = n.id AND s.weight = 1)")" = "0" ] \
  || fail "每个导入节点都要挂到能力视图（权重 1）"
echo "ok - 种子 0.1.0：只剩 orphan 与 missing_boundary，逐条与报告一致，不能发布；迁移表与能力视图齐全"

# ---------- 第二段：需要 legacy 的部分 ----------
if [ ! -d "$AYA/content/chapters/fun" ]; then
  echo "kge-import-db: 第二段跳过（没有 legacy/aya 只读副本：重新生成比对与合成补齐发布只在有副本的机器上跑）"
  echo "kge-import-db: 通过（第一段）"
  exit 0
fi
cd "$TOOL"
node --no-warnings src/import/main.ts --aya "$AYA" --out "$TMP/v1" >/dev/null
for f in import.sql worklist.md import-report.json; do
  cmp -s "$TMP/v1/$f" "$SEED/$f" || fail "$f 与仓库里的种子不一致（重新生成：make kge-import）"
done
echo "ok - 重新生成与仓库种子逐字节一致"
node --no-warnings tests/import-complete-overrides.ts "$AYA" fun > "$TMP/complete.json"
node --no-warnings src/import/main.ts --aya "$AYA" --version 0.2.0 --overrides "$TMP/complete.json" --out "$TMP/v2" >/dev/null
psql_run < "$TMP/v2/import.sql"
mv2="$(report "$TMP/v2/import-report.json" map_version_id)"
[ "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$mv2' AND kind = 'pitfall' AND error_type IS NOT NULL")" = "2" ] \
  || fail "合成补齐应带 2 个易错点"
left="$(q "SELECT count(*) FROM kge.validate_map_version('$mv2') WHERE severity = 'error'")"
[ "$left" = "0" ] || fail "合成补齐后仍有 $left 个阻断问题：$(q "SELECT check_name || ':' || object_id FROM kge.validate_map_version('$mv2') WHERE severity = 'error' LIMIT 5" | tr '\n' ' ')"
sign_publish "$mv2" >/dev/null || fail "补齐后签字应能发布"
[ "$(q "SELECT status FROM kge.map_version WHERE id = '$mv2'")" = "published" ] || fail "发布后状态应为 published"
echo "ok - 0.2.0（合成补齐，含易错点）：发布前校验全绿，已发布"
echo "kge-import-db: 通过"
