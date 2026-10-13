#!/usr/bin/env bash
# KGE-07 第一步：语文切片导入器的库上测试（specs/kge07-chinese-slice/spec.md 验收），只用本机一次性 Postgres（规则 9），全部合成数据。
#   1. 同一输入两次生成的种子、报告、快照逐字节相同；合成挡板（没 SET starbuddy.allow_synthetic 执行即失败、不写入）；
#   2. 以 starbuddy_kge 写入语文（wenli）草稿地图版本：节点数与报告一致；层次递进 = prerequisite + weak、显式前置 = prerequisite + strong；
#      前置边不碰素材与方法；投影未核对且不带原文；发布前校验（kge.validate_map_version）零阻断；只导入草稿，不发布，只读角色看不到；
#   3. 库里的校验与离线校验同口径：往草稿里临时加一条“能力点 → 素材”的前置边，库里报 edge_kind_mismatch；加一条跳级弱边，库里报传递边（回滚）；
#   4. chinese-snapshot.sql 的库上快照与离线快照逐项相同、只读；覆盖报表（--subject wenli）两边逐字节相同。
# 退出码 3 = 本机没有 Postgres。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$(cd "$HERE/../.." && pwd)"
REPO="$(cd "$TOOL/../.." && pwd)"
# shellcheck source=/dev/null
source "$REPO/db/tests/pg-ephemeral.sh"
fail() { echo "kge-chinese-db: 失败：$*"; exit 1; }

rc=0; pg_ephemeral_start || rc=$?
[ $rc -eq 3 ] && exit 3
[ $rc -eq 0 ] || fail "一次性库启动出错"
TMP="$(mktemp -d)"; chmod 755 "$TMP"
trap 'rm -rf "$TMP"; pg_ephemeral_stop' EXIT
apply "$ROOT/db/bootstrap/roles.sql" || fail "角色 bootstrap 出错"
mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
apply "${UPS[@]}" || fail "迁移执行出错"

# 从标准输入读 SQL，只输出查询结果（-q 不打印命令标签）
sql() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v ON_ERROR_STOP=1; }
q() { echo "$1" | sql; }
run_syn() { { echo "SET starbuddy.allow_synthetic = on;"; cat "$1"; } | sql >/dev/null; }
chn() { (cd "$TOOL" && node --no-warnings src/chinese/main.ts "$@"); }
gen() { chn map --domain samples/chinese/reading.yaml --domain samples/chinese/accumulation.yaml --version "$2" --out "$1" >/dev/null; chmod -R a+r "$1"; }
json_field() { node -e 'console.log(require(process.argv[1])[process.argv[2]])' "$1" "$2"; }

# ---------- 1. 确定性与挡板 ----------
gen "$TMP/a" 0.0.1
gen "$TMP/b" 0.0.1
for f in import.sql import-report.json snapshot.json; do cmp -s "$TMP/a/$f" "$TMP/b/$f" || fail "两次生成的 $f 不是逐字节相同"; done
echo "ok - 同一输入两次生成的种子、报告、快照逐字节相同"
MAP="$TMP/a"; MV="$(json_field "$MAP/import-report.json" map_version_id)"; REP="$MAP/import-report.json"
grep -q '^SET LOCAL ROLE starbuddy_kge;' "$MAP/import.sql" || fail "种子必须以 starbuddy_kge 写入"
if sql < "$MAP/import.sql" >/dev/null 2>"$TMP/guard.err"; then fail "没有允许合成数据时，合成样例的种子不应执行成功"; fi
grep -q '只能在一次性测试库执行' "$TMP/guard.err" || fail "挡板的报错应说明原因"
[ "$(q "SELECT count(*) FROM kge.map_version WHERE id = '$MV'")" = "0" ] || fail "挡板拦下后不应写入任何行"
echo "ok - 合成样例的种子带挡板：没 SET starbuddy.allow_synthetic = on 执行即失败，什么都不写"

# ---------- 2. 草稿地图版本 ----------
run_syn "$MAP/import.sql" || fail "合成种子执行出错"
[ "$(q "SELECT status || '/' || subject FROM kge.map_version WHERE id = '$MV'")" = "draft/wenli" ] || fail "应写入语文的草稿地图版本"
for k in ability_kp:ability_kps material:materials method:methods; do
  [ "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV' AND level = 'node' AND kind = '${k%%:*}'")" = "$(json_field "$REP" "${k##*:}")" ] \
    || fail "${k%%:*} 节点数与报告不一致"
done
[ "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV' AND id NOT LIKE 'CHN.%'")" = "0" ] || fail "节点必须在 CHN 命名空间"
[ "$(q "SELECT count(*) FROM kge.knowledge_edge WHERE map_version_id = '$MV' AND type = 'prerequisite' AND strength = 'weak' AND evidence_ref LIKE 'chinese:progression:%'")" \
  = "$(json_field "$REP" progression_edges)" ] || fail "层次递进应写成 prerequisite + weak"
[ "$(q "SELECT count(*) FROM kge.knowledge_edge WHERE map_version_id = '$MV' AND type = 'prerequisite' AND strength = 'strong' AND evidence_ref LIKE 'explicit:%'")" \
  = "$(json_field "$REP" explicit_prerequisites)" ] || fail "显式前置应写成 prerequisite + strong，并带依据"
[ "$(q "SELECT count(*) FROM kge.knowledge_edge WHERE map_version_id = '$MV' AND type = 'prerequisite' AND (strength = 'weak') <> (evidence_ref LIKE 'chinese:progression:%')")" = "0" ] \
  || fail "弱前置边只用于层次递进"
[ "$(q "SELECT count(*) FROM kge.knowledge_edge e JOIN kge.knowledge_node a ON a.map_version_id = e.map_version_id AND a.id = e.from_id
        JOIN kge.knowledge_node b ON b.map_version_id = e.to_map_version_id AND b.id = e.to_id
        WHERE e.map_version_id = '$MV' AND e.type = 'prerequisite' AND (a.kind <> 'ability_kp' OR b.kind <> 'ability_kp')")" = "0" ] \
  || fail "前置边（强弱）两端都必须是能力点，素材与方法不进前置图"
[ "$(q "SELECT string_agg(DISTINCT type, ',' ORDER BY type) FROM kge.knowledge_edge WHERE map_version_id = '$MV'")" = "confusable,prerequisite,related,uses_method" ] \
  || fail "边只用已有的四种类型"
[ "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV' AND kind IN ('material', 'method') AND cardinality(cognitive_levels) > 0")" = "0" ] \
  || fail "素材与方法不带认知层次"
[ "$(q "SELECT count(*) FROM kge.textbook_projection WHERE map_version_id = '$MV'")" = "$(json_field "$REP" projections)" ] || fail "投影数与报告不一致"
[ "$(q "SELECT count(*) FROM kge.textbook_projection WHERE map_version_id = '$MV' AND (quote_text IS NOT NULL OR quote_hash IS NOT NULL OR mapping_state <> 'unverified' OR verified_by IS NOT NULL)")" = "0" ] \
  || fail "投影必须未核对、不带原文"
errs="$(q "SELECT coalesce(string_agg(check_name || ':' || object_id, ' '), '') FROM kge.validate_map_version('$MV') WHERE severity = 'error'")"
[ -z "$errs" ] || fail "合成语文骨架不应有阻断问题：$errs"
[ "$(q "SELECT count(*) FROM kge.validate_map_version('$MV') WHERE severity = 'warning' AND check_name <> 'few_items'")" = "0" ] \
  || fail "除了“题目不足”（本步不导题）不应有其他警告"
[ "$(q "SELECT status FROM kge.map_version WHERE id = '$MV'")" = "draft" ] || fail "只导入草稿，不发布"
[ "$(q "SET ROLE starbuddy_kge_read; SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV'")" = "0" ] || fail "草稿版本不应对只读角色可见"
echo "ok - 语文草稿地图版本：能力点、素材、方法数与报告一致；层次递进 = 弱前置、显式前置 = 强前置，前置边不碰素材与方法；投影未核对无原文；"
echo "     发布前校验零阻断（只有本步不导题带来的 few_items 警告）；只导入草稿，只读角色看不到"

# ---------- 3. 库里的校验与离线校验同口径 ----------
probe() {  # $1 插入的边（from, to, strength）；输出这条边带来的阻断检查名（事务回滚，不留痕）
  { echo "BEGIN; SET LOCAL ROLE starbuddy_kge;"
    echo "INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type, strength) VALUES ('$MV', $1);"
    echo "SELECT coalesce(string_agg(DISTINCT check_name, ',' ORDER BY check_name), '') FROM kge.validate_map_version('$MV') WHERE severity = 'error';"
    echo "ROLLBACK;"; } | sql
}
[ "$(probe "'CHN.SYN_READ.SYN_CLASSICAL.WORD_INFER_L1', '$MV', 'CHN.SYN_ACC.SYN_WORD.WORD_A', 'prerequisite', 'weak'")" = "edge_kind_mismatch" ] \
  || fail "库里应把“能力点 → 素材”的前置边报为 edge_kind_mismatch（与离线“素材不进前置图”同口径）"
[ "$(probe "'CHN.SYN_READ.SYN_NARR.INFER_CHAR_L1', '$MV', 'CHN.SYN_READ.SYN_NARR.INFER_CHAR_L3', 'prerequisite', 'weak'")" = "transitive_edge" ] \
  || fail "库里的传递边检查不分强弱（与离线口径一致）"
[ "$(q "SELECT count(*) FROM kge.knowledge_edge WHERE map_version_id = '$MV'")" = "$(node -e 'const r=require(process.argv[1]);console.log(r.progression_edges+r.explicit_prerequisites+r.related_edges+r.confusable_edges+r.uses_method_edges)' "$REP")" ] \
  || fail "探针应回滚，不留边"
echo "ok - 库里的发布前校验与离线校验同口径：素材进前置图报 edge_kind_mismatch，跳级弱边报 transitive_edge（探针回滚）"

# ---------- 4. 库上快照 = 离线快照；覆盖报表两边逐字节相同 ----------
cp "$REPO/tools/kge-coverage/sql/chinese-snapshot.sql" "$TMP/snap.sql"; chmod 644 "$TMP/snap.sql"
as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v ON_ERROR_STOP=1 -v map_version_id="$MV" -f "$TMP/snap.sql" > "$TMP/db.json" \
  || fail "chinese-snapshot.sql 执行出错"
node -e '
const fs=require("fs");const norm=(x)=>Array.isArray(x)?x.map(norm):x&&typeof x==="object"?Object.fromEntries(Object.keys(x).sort().map(k=>[k,norm(x[k])])):x;
const a=JSON.stringify(norm(JSON.parse(fs.readFileSync(process.argv[1]))));const b=JSON.stringify(norm(JSON.parse(fs.readFileSync(process.argv[2]))));
if(a!==b){console.error("库上快照与离线快照不同");process.exit(1)}' "$MAP/snapshot.json" "$TMP/db.json" || fail "库上快照与离线快照不一致"
if as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v map_version_id=00000000-0000-4000-8000-000000000000 -f "$TMP/snap.sql" >/dev/null 2>&1; then
  fail "不存在的地图版本应报错"
fi
if grep -Eiq '^[^-]*\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE|GRANT)\b' "$TMP/snap.sql"; then fail "chinese-snapshot.sql 不应含写语句"; fi
(cd "$REPO/tools/kge-coverage" && node --no-warnings src/main.ts --subject wenli --snapshot "$MAP/snapshot.json" --out "$TMP/cov-offline" >/dev/null \
  && node --no-warnings src/main.ts --subject wenli --snapshot "$TMP/db.json" --out "$TMP/cov-db" >/dev/null) || fail "语文覆盖报表生成出错"
for f in "$TMP"/cov-offline/*; do cmp -s "$f" "$TMP/cov-db/$(basename "$f")" || fail "覆盖报表 $(basename "$f") 两边不一致"; done
grep -q '不计入必修覆盖：CHN.SYN_ACC.SYN_LOCAL.LOCAL_A（考情拓展）' "$TMP"/cov-db/*.md || fail "在地文化拓展素材应单列，不计入必修覆盖"
# 停用的边不导出（草稿里把一条关联边标为停用，再导出一次）
q "UPDATE kge.knowledge_edge SET status = 'deprecated' WHERE map_version_id = '$MV' AND from_id = 'CHN.SYN_ACC.SYN_LOCAL.LOCAL_A' AND type = 'related'" >/dev/null
as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v ON_ERROR_STOP=1 -v map_version_id="$MV" -f "$TMP/snap.sql" > "$TMP/db2.json" \
  || fail "chinese-snapshot.sql 第二次执行出错"
[ "$(node -e 'const f=require("fs");const n=(p)=>JSON.parse(f.readFileSync(p)).edges.length;console.log(n(process.argv[1])-n(process.argv[2]))' "$TMP/db.json" "$TMP/db2.json")" = "1" ] \
  || fail "停用的边不应导出"
echo "ok - chinese-snapshot.sql 的库上快照与离线快照逐项相同、只读；语文覆盖报表两边逐字节相同，拓展素材单列"
echo "kge-chinese-db: 通过"
