#!/usr/bin/env bash
# 冲刺 4-K 页码抽样核验的回填演示（spec 设计要点 2、验收第 2 条），只在本机一次性 Postgres 上执行（规则 9），全部合成数据。
# 只用已有的写入方与签字路径（不新增库函数、不改审核台）：
#   知识服务角色（starbuddy_kge，审核台后端）在草稿地图版本上写 mapping_state / verified_by / verified_at，
#   再由 active staff 以 starbuddy_review 对整个地图版本签字（kge.sign_review，0019；摘要含投影，签字后再改要重签）。
# 演示内容：
#   1. 固定种子抽样可复现（两次抽样的核验单逐字节相同），核验单不含任何原文；
#   2. 通过率低于阈值（合成回填 1 条 fail）：判定该版次不能标 verified，生成器拒绝把它标 verified；
#      只把不通过的样本标 disputed，这条 SQL 能执行；签字摘要随之变化（签字后又改过要重签）；
#   3. 通过率达标（合成回填全部 pass）：生成的“标 verified”SQL 在现有表上执行失败——0011 的约束 projection_verified_by
#      要求 verified 的投影 quote_text 非空，而原句在法务确认前不入库（decisions #5）。这是已知限制，记为待人确认 #5 的迁移草稿需求
#      （specs/kge-daofa-slice-sprint4/lessons.md），本任务不改迁移。
# 退出码 3 = 本机没有 Postgres。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/db-lib.sh"
fail() { echo "kge-daofa-page-demo: 失败：$*"; exit 1; }
daofa_db_start || { rc=$?; [ $rc -eq 3 ] && exit 3; fail "一次性库准备出错"; }

gen_map "$TMP/m" 0.0.2
MAP="$TMP/m"; MV="$(json_field "$MAP/import-report.json" map_version_id)"
run_syn "$MAP/import.sql" || fail "合成种子执行出错"

# ---------- 1. 抽样 ----------
daofa page-sample --map "$MAP" --out "$TMP/c1.csv" >/dev/null
daofa page-sample --map "$MAP" --out "$TMP/c2.csv" >/dev/null
cmp -s "$TMP/c1.csv" "$TMP/c2.csv" || fail "同一种子两次抽样应逐字节相同"
head -1 "$TMP/c1.csv" | grep -q '^sample_id,projection_id,edition,edition_year,volume,chapter,page_from,page_to,node_id,result,checker,checked_on,note$' \
  || fail "核验单表头不对"
grep -qiE 'quote|原句|原文' "$TMP/c1.csv" && fail "核验单不能含原文"
rows="$(($(wc -l < "$TMP/c1.csv") - 1))"
echo "ok - 固定种子抽样可复现：$rows 条样本（每版次 20%、至少 20 条，不足 20 条全抽），核验单不含原文"

# 合成回填：第 1 条 fail，其余 pass（核验人、日期都是合成的）
fill() {  # $1 = 核验单，$2 = 输出，$3 = fail 的行号（0 = 全部 pass）
  awk -F, -v OFS=, -v bad="$3" 'NR == 1 { print; next }
       { $10 = (NR - 1 == bad) ? "fail" : "pass"; $11 = "syn-checker-01"; $12 = "2026-09-30"; $13 = (NR - 1 == bad) ? "（合成）页码差 1 页" : ""; print }' "$1" > "$2"
}
sign() { q "$(as_staff "$STAFF") SELECT kge.sign_review('map_version', '$MV', 'approved', 'high', 'daofa4k')" >/dev/null; }
latest_digest() { q "SELECT digest FROM kge.content_review WHERE object_type = 'map_version' AND object_id = '$MV' ORDER BY decided_at DESC, id DESC LIMIT 1"; }

# ---------- 2. 低于阈值 ----------
fill "$TMP/c1.csv" "$TMP/b-fail.csv" 1
set +e
daofa page-backfill --map "$MAP" --checklist "$TMP/c1.csv" --backfill "$TMP/b-fail.csv" --approver syn-approver-01 --approved-on 2026-09-30 --out "$TMP/o-fail" >"$TMP/o-fail.log" 2>&1
rc=$?
set -e
[ $rc -eq 1 ] || fail "有版次低于阈值时 page-backfill 应退出 1（实际 $rc）"
grep -q '不能标 verified' "$TMP/o-fail.log" || fail "应说明该版次不能标 verified"
grep -q "mapping_state = 'verified'" "$TMP/o-fail/page-backfill.sql" && fail "低于阈值的版次不能出现标 verified 的语句"
node -e 'const d=require(process.argv[1]);if(d.decisions.some(x=>x.can_verify))process.exit(1)' "$TMP/o-fail/page-decision.json" || fail "判定应为不能标 verified"
sign; before="$(latest_digest)"
psql_run < "$TMP/o-fail/page-backfill.sql" >/dev/null || fail "只标 disputed 的回填 SQL 应能以 starbuddy_kge 执行"
[ "$(q "SELECT count(*) FROM kge.textbook_projection WHERE map_version_id = '$MV' AND mapping_state = 'disputed'")" = "1" ] || fail "不通过的样本应标 disputed"
[ "$(q "SELECT count(*) FROM kge.textbook_projection WHERE map_version_id = '$MV' AND mapping_state = 'verified'")" = "0" ] || fail "低于阈值时不能有 verified"
[ "$(q "SELECT kge.map_version_digest('$MV')")" != "$before" ] || fail "回填后版本摘要应变化（签字后又改过要重签）"
sign; [ "$(latest_digest)" = "$(q "SELECT kge.map_version_digest('$MV')")" ] || fail "重签后签字摘要应等于当前摘要"
echo "ok - 通过率低于阈值（$((rows - 1))/$rows）：判定不能标 verified、生成器不出标 verified 的语句；不通过的样本以 starbuddy_kge 标 disputed，staff 重新签字"

# ---------- 3. 达标：现有表上写不进 verified（已知限制） ----------
q "SET ROLE starbuddy_kge; UPDATE kge.textbook_projection SET mapping_state = 'unverified' WHERE map_version_id = '$MV'" >/dev/null
fill "$TMP/c1.csv" "$TMP/b-pass.csv" 0
daofa page-backfill --map "$MAP" --checklist "$TMP/c1.csv" --backfill "$TMP/b-pass.csv" --approver syn-approver-01 --approved-on 2026-09-30 --out "$TMP/o-pass" >/dev/null \
  || fail "全部通过时 page-backfill 应退出 0"
grep -q "mapping_state = 'verified'" "$TMP/o-pass/page-backfill.sql" || fail "达标的版次应生成标 verified 的语句"
if psql_run < "$TMP/o-pass/page-backfill.sql" >/dev/null 2>"$TMP/o-pass.err"; then
  echo "kge-daofa-page-demo: 注意：标 verified 执行成功——0011 的 projection_verified_by 约束已放宽，请更新 lessons 待人确认 #5"
else
  grep -q 'projection_verified_by' "$TMP/o-pass.err" || fail "标 verified 失败的原因应是 projection_verified_by 约束，实际：$(head -2 "$TMP/o-pass.err")"
  [ "$(q "SELECT count(*) FROM kge.textbook_projection WHERE map_version_id = '$MV' AND mapping_state <> 'unverified'")" = "0" ] || fail "失败的回填应整体回滚"
  echo "ok - 通过率达标（$rows/$rows）：生成标 verified 的 SQL；现有表上因 projection_verified_by（verified 必须有 quote_text）整体回滚——待人确认 #5 的迁移草稿需求"
fi
echo "kge-daofa-page-demo: 通过"
