#!/usr/bin/env bash
# 冲刺 4-K 道法切片的库上测试（specs/kge-daofa-slice-sprint4/spec.md 验收 1–5），只用本机一次性 Postgres（规则 9），全部合成数据。
#   1. 观点地图：两次生成逐字节相同；合成挡板（没 SET starbuddy.allow_synthetic 执行即失败、不写入）；以 starbuddy_kge 写入草稿版本，
#      发布前校验无阻断问题，投影全部未核对且 quote_text 为空，旧短编码进迁移表；
#   2. 采分点：合成 4 题导入 kge.scoring_point；重复执行不变；同一 rubric_version 改内容 → 整个事务失败（只新增不修改）；
#      原子层导出与内容包逐字段一致（rubric-check）；
#   3. 时政：一律 pending；库里的白名单再核一次（离线白名单多一个主机、库里没有 → 失败）；
#   4. 快照：daofa-snapshot.sql 在库里导出的快照与离线组装的快照逐项相同，覆盖报表两边逐字节相同；
#   5. 发布后只读角色（starbuddy_kge_read）：过期时政不可见、未来生效的不可见、有效期内的可见；未核对投影不可见。
# 退出码 3 = 本机没有 Postgres。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/db-lib.sh"
fail() { echo "kge-daofa-db: 失败：$*"; exit 1; }
daofa_db_start || { rc=$?; [ $rc -eq 3 ] && exit 3; fail "一次性库准备出错"; }

# ---------- 1. 观点地图 ----------
gen_map "$TMP/a" 0.0.1
gen_map "$TMP/b" 0.0.1
for f in import.sql import-report.json snapshot.json; do cmp -s "$TMP/a/$f" "$TMP/b/$f" || fail "两次生成的 $f 不是逐字节相同"; done
echo "ok - 同一输入两次生成的种子、报告、快照逐字节相同"
MAP="$TMP/a"; MV="$(json_field "$MAP/import-report.json" map_version_id)"
grep -q '^SET LOCAL ROLE starbuddy_kge;' "$MAP/import.sql" || fail "种子必须以 starbuddy_kge 写入"
if psql_run < "$MAP/import.sql" >/dev/null 2>"$TMP/guard.err"; then fail "没有允许合成数据时，合成样例的种子不应执行成功"; fi
grep -q '只能在一次性测试库执行' "$TMP/guard.err" || fail "挡板的报错应说明原因"
[ "$(q "SELECT count(*) FROM kge.map_version WHERE id = '$MV'")" = "0" ] || fail "挡板拦下后不应写入任何行"
echo "ok - 合成样例的种子带挡板：没 SET starbuddy.allow_synthetic = on 执行即失败，什么都不写"
run_syn "$MAP/import.sql" || fail "合成种子执行出错"
[ "$(q "SELECT status || '/' || subject FROM kge.map_version WHERE id = '$MV'")" = "draft/zhixing" ] || fail "应写入道法的草稿地图版本"
[ "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV' AND kind = 'viewpoint' AND level = 'node'")" = "$(json_field "$MAP/import-report.json" viewpoints)" ] \
  || fail "观点数与报告不一致"
[ "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV' AND id LIKE 'MORAL.LAW.%' AND level = 'topic'")" = "2" ] || fail "法律领域应有 2 个大概念"
[ "$(q "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV' AND id LIKE 'MORAL.LAW.%' AND kind = 'viewpoint'")" = "6" ] || fail "法律领域应有 6 个观点"
[ "$(q "SELECT string_agg(DISTINCT type, ',' ORDER BY type) FROM kge.knowledge_edge WHERE map_version_id = '$MV'")" = "prerequisite,related" ] || fail "边只能是关联与显式前置"
[ "$(q "SELECT count(*) FROM kge.knowledge_edge WHERE map_version_id = '$MV' AND type = 'prerequisite' AND evidence_ref NOT LIKE 'explicit:%'")" = "0" ] \
  || fail "前置边必须带显式标注的依据"
[ "$(q "SELECT count(*) FROM kge.textbook_projection WHERE map_version_id = '$MV' AND (quote_text IS NOT NULL OR quote_hash IS NOT NULL OR mapping_state <> 'unverified' OR verified_by IS NOT NULL)")" = "0" ] \
  || fail "投影必须未核对、不带原句"
[ "$(q "SELECT count(*) FROM kge.id_migration WHERE map_version_id = '$MV' AND rule = 'manual'")" = "2" ] || fail "大概念旧短编码应进迁移表"
errs="$(q "SELECT coalesce(string_agg(check_name || ':' || object_id, ' '), '') FROM kge.validate_map_version('$MV') WHERE severity = 'error'")"
[ -z "$errs" ] || fail "合成地图不应有阻断问题：$errs"
echo "ok - 草稿地图版本：2 个领域、法律领域 2 个大概念 6 个观点，边只有关联与显式前置，投影未核对且无原句，发布前校验无阻断问题"

# ---------- 2. 采分点 ----------
daofa rubric --pack "$PACK" --map "$MAP" --out "$TMP/r" >/dev/null
run_syn "$TMP/r/scoring-points.sql" || fail "采分点种子执行出错"
n="$(q "SELECT count(*) FROM kge.scoring_point WHERE map_version_id = '$MV' AND subject = 'zhixing'")"
want="$(node -e 'const p=require(process.argv[1]);console.log(p.items.reduce((s,i)=>s+i.points.length,0))' "$PACK")"
[ "$n" = "$want" ] || fail "采分点应写入 $want 个，实际 $n 个"
run_syn "$TMP/r/scoring-points.sql" || fail "同一采分点种子重复执行应不报错（只新增，已有的相同）"
[ "$(q "SELECT count(*) FROM kge.scoring_point WHERE map_version_id = '$MV'")" = "$n" ] || fail "重复执行不应多写"
node -e 'const fs=require("fs");const p=JSON.parse(fs.readFileSync(process.argv[1]));p.items[0].points[0].keyword_family.core.push("（合成变异）");fs.writeFileSync(process.argv[2],JSON.stringify(p))' \
  "$PACK" "$TMP/pack-mut.json"
daofa rubric --pack "$TMP/pack-mut.json" --map "$MAP" --out "$TMP/rm" >/dev/null
if run_syn "$TMP/rm/scoring-points.sql" 2>"$TMP/rm.err"; then fail "同一 rubric_version 改内容不应执行成功"; fi
grep -q '只能新增不能修改' "$TMP/rm.err" || fail "改内容被拒的报错应说明“只能新增不能修改”"
echo "ok - 采分点：合成 4 题 $n 个采分点写入 kge.scoring_point；重复执行不变；同一 rubric_version 改内容整个事务失败"
cp "$TOOL/sql/export-scoring-points.sql" "$TMP/export.sql"; chmod 644 "$TMP/export.sql"
as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v ON_ERROR_STOP=1 -v subject=zhixing -f "$TMP/export.sql" > "$TMP/export.json" \
  || fail "原子层导出出错"
daofa rubric-check --pack "$PACK" --export "$TMP/export.json" >/dev/null || fail "内容包与原子层导出应一致"
if daofa rubric-check --pack "$TMP/pack-mut.json" --export "$TMP/export.json" >/dev/null 2>&1; then fail "改过的内容包应被一致性检查发现"; fi
echo "ok - 原子层导出（export-scoring-points.sql）与内容包逐字段一致；改过的内容包被发现"

# ---------- 3. 时政 ----------
TODAY="$(date -u +%F)"; d() { date -u -d "$TODAY $1 day" +%F; }
cat > "$TMP/affairs.yaml" <<YAML
format: kge-daofa-current-affairs/1
synthetic: true
notice: 合成、非真实时政：库上测试用，日期相对今天生成
subject: zhixing
entries:
  - key: syn-valid
    source: https://news.synthetic-source.example/syn/valid
    node_ids: [MORAL.LAW.SYN_BC_A.VP_A1]
    summary: （合成占位）有效期内的条目
    valid_from: $(d -1)
    valid_until: $(d +60)
  - key: syn-expired
    source: https://gov.synthetic-source.example/syn/expired
    node_ids: [MORAL.LAW.SYN_BC_B.VP_B2]
    summary: （合成占位）今天到期的条目
    valid_from: $(d -60)
    valid_until: $TODAY
  - key: syn-future
    source: https://news.synthetic-source.example/syn/future
    node_ids: [MORAL.SYN.LAW.MINOR_ONLINE_PROTECTION]
    summary: （合成占位）明天才生效的条目
    valid_from: $(d +1)
    valid_until: $(d +30)
YAML
daofa affairs --entries "$TMP/affairs.yaml" --source-domains "$SAMPLES/source-domains.txt" --map "$MAP" --out "$TMP/ca" >/dev/null
run_syn "$TMP/ca/current-affairs.sql" || fail "时政种子执行出错"
[ "$(q "SELECT count(*) || '/' || count(*) FILTER (WHERE review_status = 'pending') FROM kge.current_affair WHERE map_version_id = '$MV'")" = "3/3" ] \
  || fail "时政应一律以 pending 写入"
{ cat "$SAMPLES/source-domains.txt"; echo "rogue.synthetic-source.example"; } > "$TMP/wide.txt"
sed 's#https://news.synthetic-source.example/syn/valid#https://rogue.synthetic-source.example/syn/x#; s#key: syn-valid#key: syn-rogue#' "$TMP/affairs.yaml" > "$TMP/rogue.yaml"
daofa affairs --entries "$TMP/rogue.yaml" --source-domains "$TMP/wide.txt" --map "$MAP" --out "$TMP/cr" >/dev/null
if run_syn "$TMP/cr/current-affairs.sql" 2>"$TMP/cr.err"; then fail "库里白名单没有的主机不应写入"; fi
grep -q 'policy.source_domain' "$TMP/cr.err" || fail "库里白名单核对的报错应说明原因"
echo "ok - 时政：3 条以 pending 写入；离线清单放行但库里白名单（policy.source_domain）没有的主机，执行即失败"

# ---------- 4. 库上快照 = 离线快照；覆盖报表两边逐字节相同 ----------
daofa snapshot --map "$MAP" --pack "$PACK" --entries "$TMP/affairs.yaml" --source-domains "$SAMPLES/source-domains.txt" --out "$TMP/offline.json"
cp "$REPO/tools/kge-coverage/sql/daofa-snapshot.sql" "$TMP/snap.sql"; chmod 644 "$TMP/snap.sql"
as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v ON_ERROR_STOP=1 -v map_version_id="$MV" -f "$TMP/snap.sql" > "$TMP/db.json" \
  || fail "daofa-snapshot.sql 执行出错"
node -e '
const fs=require("fs");const norm=(x)=>Array.isArray(x)?x.map(norm):x&&typeof x==="object"?Object.fromEntries(Object.keys(x).sort().map(k=>[k,norm(x[k])])):x;
const a=JSON.stringify(norm(JSON.parse(fs.readFileSync(process.argv[1]))));const b=JSON.stringify(norm(JSON.parse(fs.readFileSync(process.argv[2]))));
if(a!==b){console.error("库上快照与离线快照不同");process.exit(1)}' "$TMP/offline.json" "$TMP/db.json" || fail "库上快照与离线快照不一致"
if as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -q -At -v map_version_id=00000000-0000-4000-8000-000000000000 -f "$TMP/snap.sql" >/dev/null 2>&1; then
  fail "不存在的地图版本应报错"
fi
grep -Eiq '^[^-]*\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE|GRANT)\b' "$TMP/snap.sql" && fail "daofa-snapshot.sql 不应含写语句"
(cd "$REPO/tools/kge-coverage" && node --no-warnings src/main.ts --subject zhixing --snapshot "$TMP/offline.json" --out "$TMP/cov-offline" >/dev/null \
  && node --no-warnings src/main.ts --subject zhixing --snapshot "$TMP/db.json" --out "$TMP/cov-db" >/dev/null) || fail "道法覆盖报表生成出错"
for f in "$TMP"/cov-offline/*; do cmp -s "$f" "$TMP/cov-db/$(basename "$f")" || fail "覆盖报表 $(basename "$f") 两边不一致"; done
grep -q '读本内容：已排除' "$TMP"/cov-db/*.md || fail "覆盖报表应单列“读本内容：已排除”"
echo "ok - daofa-snapshot.sql 的库上快照与离线快照逐项相同、只读；覆盖报表两边逐字节相同，单列读本已排除"

# ---------- 5. 发布后只读角色的可见性 ----------
sign_publish "$MV" || fail "合成地图版本应能签字发布"
for key in syn-valid syn-expired syn-future; do
  id="$(q "SELECT id FROM kge.current_affair WHERE map_version_id = '$MV' AND source LIKE '%/syn/${key#syn-}'")"
  [ -n "$id" ] || fail "找不到时政 $key"
  q "$(as_staff "$STAFF") SELECT kge.sign_review('current_affair', '$id', 'approved', 'low', 'daofa4k')" >/dev/null
  q "SET ROLE starbuddy_kge; UPDATE kge.current_affair SET review_status = 'published' WHERE id = '$id'" >/dev/null
done
# psql -c 执行多条语句时会先打印 SET 的命令标签：只取最后一行（查询结果）
reader() { q "SET ROLE starbuddy_kge_read; $1" | tail -n 1; }
[ "$(reader "SELECT string_agg(source, ',' ORDER BY source) FROM kge.current_affair")" = "https://news.synthetic-source.example/syn/valid" ] \
  || fail "只读角色应只看到有效期内的时政（过期、未来生效的都不可见）"
[ "$(reader "SELECT count(*) FROM kge.textbook_projection")" = "0" ] || fail "未核对的投影对只读角色不可见"
[ "$(reader "SELECT count(*) FROM kge.scoring_point WHERE map_version_id = '$MV'")" = "$n" ] || fail "已发布版本的采分点对只读角色可见"
[ "$(reader "SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = '$MV' AND kind = 'viewpoint'")" = "$(json_field "$MAP/import-report.json" viewpoints)" ] \
  || fail "已发布版本的观点对只读角色可见"
echo "ok - 发布后：只读角色只看到有效期内的时政（今天到期、明天生效的都看不到），看不到未核对投影，看得到观点与采分点"
echo "kge-daofa-db: 通过"
