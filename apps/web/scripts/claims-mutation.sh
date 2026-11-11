#!/usr/bin/env bash
# 陈述—判断一致性爬取（T21，specs/tech-debt-batch2 第一节 A；e2e/claim-binding.ts）的变异测试：
# 逐条把 BFF 或前端改坏（每类断言至少一条），只跑对应那一套的 T21 用例，断言它失败。
# 每条变异都要重新构建端到端前端（scripts/e2e.sh 每次都构建），所以单条要几分钟；只进 make 的变异组（CI 手动触发时跑）。
# 改动在工作区里原地进行，结束（含中断）时一律还原；还原前后核对文件内容与改之前逐字节相同。
# 退出码：0 全部被发现；1 有漏检或变异本身写错；3 端到端环境不全（同 scripts/e2e.sh）。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
PY="${PYTHON:-python3}"

# 名字 | 文件（相对仓库根目录） | 原文 | 替换 | Playwright 项目
M=(
  $'BFF 周报不再核判断状态（转为待验证的句子以实心结论露出；推翻、撤回由库的 RLS 另挡）\tservices/bff/src/http/trust-routes.ts\tAND c.status IN (\'hypothesis\', \'established\') AND c.target_type = \'node\'\tAND c.target_type = \'node\'\ttrust-on'
  $'周报句子不带 data-claim-id\tapps/web/src/trust/WeeklyPage.tsx\t<span data-claim-id={s.claim_id}>{s.text}</span>\t<span>{s.text}</span>\ttrust-on'
  $'周报句子抄进说明属性（不带判断 ID）\tapps/web/src/trust/WeeklyPage.tsx\t<span data-claim-id={s.claim_id}>{s.text}</span>\t<span data-claim-id={s.claim_id}>{s.text}</span><span aria-label={s.text} />\ttrust-on'
  # “为什么”页（specs/why-page-e2e）：e2e/trust-on.spec.ts 的“为什么”页两条与 T21 爬取
  $'“为什么”页结论不带 data-claim-id\tapps/web/src/trust/WhyPage.tsx\t<span data-claim-id={st.claim_id}>{st.text}</span>\t<span>{st.text}</span>\ttrust-on'
  $'“为什么”页读取不可用时仍显示异议入口\tapps/web/src/trust/WhyPage.tsx\t{view && trustEnabled(\'dispute\')\n        ? <DisputePanel studentId={studentId} claimId={view.claim_id}\t{trustEnabled(\'dispute\')\n        ? <DisputePanel studentId={studentId} claimId={view?.claim_id ?? claimId}\ttrust-on'
  $'“为什么”页把待验证画成实心结论（前端不看画法）\tapps/web/src/trust/WhyPage.tsx\t{st.display === \'solid\' && st.text !== null\t{st.status !== \'refuted\'\ttrust-on'
  $'家长多出只给学生的异议路\tapps/web/src/trust/view.ts\taudience === \'student\' ? [...DISPUTE_KINDS] : DISPUTE_KINDS.filter((k) => k !== \'one_off_slip\')\t[...DISPUTE_KINDS]\ttrust-on'
  $'星图把待验证的判断画成实心星\tservices/bff/src/star-map/v1.ts\tconst solid = mastery.find((c) => SOLID.has(c.status));\tconst solid = mastery.find((c) => SOLID.has(c.status) || c.status === \'to_verify\');\tstarmap-on'
)

run() {  # $1 项目；输出 e2e.sh 的退出码
  ( cd "$ROOT" && bash apps/web/scripts/e2e.sh --grep-invert @visual --reporter=dot --project "$1" --grep T21 >/dev/null 2>&1 ); echo $?
}

BACKUP="$(mktemp -d)"
restore() {
  for f in "$BACKUP"/*.orig; do
    [ -e "$f" ] || continue
    dst="$(cat "${f%.orig}.path")"
    cp "$f" "$ROOT/$dst"
  done
  rm -rf "$BACKUP"
}
trap restore EXIT

for project in trust-on starmap-on; do
  rc=$(run "$project")
  if [ "$rc" = "3" ]; then echo "claims-mutation: 端到端环境不全（同 scripts/e2e.sh），跳过"; exit 3; fi
  if [ "$rc" != "0" ]; then echo "claims-mutation: 对照（未变异，$project）没有通过（退出码 $rc），变异结果不可信"; exit 1; fi
done

missed=0; total=0; i=0
for entry in "${M[@]}"; do
  IFS=$'\t' read -r name file orig repl project <<<"$entry"
  total=$((total + 1)); i=$((i + 1))
  cp "$ROOT/$file" "$BACKUP/$i.orig"; printf '%s' "$file" > "$BACKUP/$i.path"
  if ! MUT_FILE="$ROOT/$file" MUT_ORIG="$orig" MUT_REPL="$repl" "$PY" - <<'PY'
import os, sys
p, o, r = os.environ["MUT_FILE"], os.environ["MUT_ORIG"], os.environ["MUT_REPL"]
s = open(p, encoding="utf-8").read()
if s.count(o) != 1:
    sys.exit(1)
open(p, "w", encoding="utf-8").write(s.replace(o, r))
PY
  then echo "  变异本身写错（原文找不到或不唯一）：$name"; missed=$((missed + 1)); continue; fi
  rc=$(run "$project")
  cp "$BACKUP/$i.orig" "$ROOT/$file"
  cmp -s "$BACKUP/$i.orig" "$ROOT/$file" || { echo "claims-mutation: 还原失败：$file"; exit 1; }
  case "$rc" in
    1) : ;;
    0) echo "  漏检：$name"; missed=$((missed + 1)) ;;
    *) echo "  变异本身写错（端到端退出码 $rc）：$name"; missed=$((missed + 1)) ;;
  esac
done

if [ "$missed" -gt 0 ]; then echo "claims-mutation: $missed / $total 条没有被发现"; exit 1; fi
echo "claims-mutation: $total 条变异全部被 T21 爬取发现"
