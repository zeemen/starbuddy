#!/usr/bin/env bash
# BFF 集成测试的变异测试：逐条去掉计量函数里的加锁或并发判断，断言 Gate 0 集成测试能发现；
# 另有星图读模块的源码变异（见下方 SM），断言星图集成测试能发现。
# 这几条只有在多连接并发下才会出错，所以放在这里而不是 db/tests/mutations.sh。
# 并发相关的失败依赖时序：每个变异最多跑 3 次，任何一次失败就算“被发现”。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
cd "$HERE/.."

fr() {  # 函数定义里的文本替换；找不到模式时让变异本身失败
  printf "DO \$\$ DECLARE d text := pg_get_functiondef('%s'::regprocedure); BEGIN IF position(%s IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, %s, %s); END \$\$;" "$1" "$2" "$2" "$3"
}
RS='platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'  # 0015 起预留主体在 meter_reserve_impl
declare -A M=(
  ["预留不锁额度桶"]="$(fr "$RS" "'ORDER BY en.id FOR UPDATE OF en;'" "'ORDER BY en.id;'")"
  ["预留加锁后不再查同 key"]="$(fr "$RS" "E'SELECT * INTO r FROM platform.usage_reservation WHERE idempotency_key = p_key;\n  IF FOUND THEN\n    RETURN platform.meter_same_key(r, p_student, p_resource, p_fingerprint, p_content_fingerprint, p_source_method);\n  END IF;\n\n  IF rt.dedup_window IS NOT NULL THEN'" "'IF rt.dedup_window IS NOT NULL THEN'")"
  ["单日上限不按学生加锁"]="$(fr "$RS" "'PERFORM pg_advisory_xact_lock(hashtextextended(''platform.meter_daily:'''" "'PERFORM pg_advisory_xact_lock_shared(hashtextextended(''platform.meter_daily:'''")"
  ["单日上限拿锁后不重查同 key"]="$(fr "$RS" "E'-- 拿锁后重查同 key：同 key 的并发重试要拿到已有预留，而不是被上限拒绝（安全审查必须改 1）\n    SELECT * INTO r FROM platform.usage_reservation WHERE idempotency_key = p_key;\n    IF FOUND THEN'" "E'IF false THEN'")"
  ["自练不按学生加锁"]="$(fr 'platform.meter_practice(uuid, text)' "'PERFORM pg_advisory_xact_lock(hashtextextended'" "'PERFORM pg_advisory_xact_lock_shared(hashtextextended'")"
  ["试用领取不加锁"]="$(fr 'platform.meter_open_subscription(uuid, text)' "'PERFORM pg_advisory_xact_lock(hashtextextended(''platform.trial_claim'', 0));'" "''")"
  ["选科不按学生加锁"]="$(fr 'platform.enroll_subject(uuid, platform.subject)' "'PERFORM pg_advisory_xact_lock(hashtextextended'" "'PERFORM pg_advisory_xact_lock_shared(hashtextextended'")"
)

missed=0; total=0
for name in "${!M[@]}"; do
  total=$((total + 1)); caught=0
  for attempt in 1 2 3; do
    out=$(PGEPHEMERAL_EXTRA_SQL="${M[$name]}" bash "$ROOT/db/tests/pg-ephemeral.sh" -- \
          node --no-warnings --test --test-concurrency=1 tests/gate0.integration.test.ts 2>&1); rc=$?
    if [ $rc -eq 3 ]; then echo "bff-mutation: 跳过（本机没有 Postgres）"; exit 3; fi
    if echo "$out" | grep -q '附加 SQL 执行失败'; then echo "  变异本身写错：$name"; missed=$((missed + 1)); caught=2; break; fi
    if [ $rc -ne 0 ]; then caught=1; break; fi
  done
  if [ $caught -eq 0 ]; then echo "  漏检：$name"; missed=$((missed + 1)); fi
done

# 星图（冲刺 3-H）的源码变异：把 services/bff 复制到临时目录（保持仓库结构），改一处源码，跑星图测试，断言能发现。
# 每条是“名字<TAB>文件<TAB>原文<TAB>替换为”（替换为可以为空，表示删掉原文）；原文找不到时算变异本身写错。
SM=(
  $'星图不按 is_current 过滤\tsrc/star-map/query.ts\t AND m.is_current'
  $'星图把 understand 当半亮\tsrc/star-map/display-state.ts\tunderstand: \'unlit\'\tunderstand: \'half\''
  $'星图不标保持层\tsrc/star-map/display-state.ts\tretained: state === \'retain\'\tretained: false'
  $'星图也显示已暂停的学科\tsrc/star-map/query.ts\t AND e.status = \'active\'\t'
  $'开发身份桩不看监听地址\tsrc/star-map/server.ts\tif (!addr || !LOOPBACK.has(addr.address)) return null;\t'
  $'开发身份桩不看开关\tsrc/star-map/server.ts\tif (!devAuth) return null;\t'
  $'开发身份桩不看转发头\tsrc/star-map/server.ts\tif (FORWARDED_HEADERS.some((h) => req.headers[h] !== undefined)) return null;\t'
  $'连接角色核对放过 BYPASSRLS\tsrc/star-map/query.ts\tNOT r.rolsuper AND NOT r.rolbypassrls\tNOT r.rolsuper'
  $'未授权与不存在的响应不同\tsrc/star-map/server.ts\tif (!map) return send(404, NOT_FOUND);\tif (!map) return send(403, UNAUTHENTICATED);'
  $'星图响应可以缓存\tsrc/star-map/server.ts\t\'cache-control\': \'no-store\',\t'
  $'日志记原始 student_id\tsrc/star-map/server.ts\trecord[\'student\'] = hashId(studentId);\trecord[\'student\'] = studentId;'
  # 计量（plugin-api-v1.0-step2）：预留过期后只查询重放接口，不发原请求；这几条跑计量测试
  $'计量：过期后仍发原请求\tsrc/metering/meter.ts\tif (r.state === \'expired\') return replayAfterExpiry(\t if (false) return replayAfterExpiry('
  $'计量：重放没有结果当成功\tsrc/metering/meter.ts\tif (out.kind === \'found\') {\tif (out.kind !== \'busy\') {'
  $'计量：路由 404 当作已封存\tsrc/metering/learning-client.ts\tif (status === 404 && problem.code === \'REPLAY_NOT_FOUND\')\tif (status === 404)'
  $'计量：重放 BUSY 当作已封存\tsrc/metering/learning-client.ts\tif (status === 409 && problem.code === \'BUSY\') return { kind: \'busy\' };\tif (status === 409) return { kind: \'not_found\', problem };'
)
# 副本保持仓库结构（测试按 ../../../ 找仓库根：adapters/aya 的登记脚本、db/seeds 的种子），adapters、db 软链接回仓库。
# 安全审查阻断 1：原来的副本不保持结构，测试的 before 在任何代码下都失败，变异全部“被发现”却不说明任何问题。
# 所以：先跑一次不变异的对照，必须通过；每条变异限时（挂起算漏检）；只有“至少一项测试通过、另有失败”才算被发现，
# 保证失败来自断言而不是环境没起来。
star_run() {  # $1 副本根目录，$2 变异名（“计量：”开头的跑计量测试）；输出 rc 与通过数
  local out rc pass tests="tests/star-map.test.ts tests/star-map.integration.test.ts"
  [[ "${2:-}" == 计量：* ]] && tests="tests/learning-client.test.ts tests/gate0.integration.test.ts"
  out=$( cd "$1/services/bff" && timeout 240 bash "$ROOT/db/tests/pg-ephemeral.sh" -- \
      node --no-warnings --test --test-concurrency=1 --test-timeout=60000 $tests 2>&1 ); rc=$?
  pass=$(printf '%s\n' "$out" | sed -n 's/^# pass \([0-9]*\)$/\1/p' | tail -1)
  echo "$rc ${pass:-0}"
}
star_copy() {
  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-bffmut.XXXXXX")"
  mkdir -p "$work/services/bff"
  cp -r src tests openapi package.json tsconfig.json "$work/services/bff/"
  ln -s "$PWD/node_modules" "$work/services/bff/node_modules"
  ln -s "$ROOT/adapters" "$work/adapters"; ln -s "$ROOT/db" "$work/db"
  echo "$work"
}
work="$(star_copy)"; read -r rc pass <<< "$(star_run "$work")"; rm -rf "$work"
if [ "$rc" = "3" ]; then echo "bff-mutation: 跳过（本机没有 Postgres）"; exit 3; fi
if [ "$rc" != "0" ]; then echo "bff-mutation: 失败（星图变异的对照组没有通过，rc=$rc）"; exit 1; fi
for spec in "${SM[@]}"; do
  IFS=$'\t' read -r name file from to <<< "$spec"
  total=$((total + 1))
  work="$(star_copy)"
  if ! MUT_FILE="$work/services/bff/$file" MUT_FROM="$from" MUT_TO="$to" python3 - <<'PYM'
import os, sys
p, a, b = os.environ['MUT_FILE'], os.environ['MUT_FROM'], os.environ['MUT_TO']
s = open(p, encoding='utf-8').read()
if a not in s: sys.exit(1)
open(p, 'w', encoding='utf-8').write(s.replace(a, b, 1))
PYM
  then echo "  变异本身写错：$name"; missed=$((missed + 1)); rm -rf "$work"; continue; fi
  read -r rc pass <<< "$(star_run "$work" "$name")"
  rm -rf "$work"
  if [ "$rc" = "0" ]; then echo "  漏检：$name"; missed=$((missed + 1))
  elif [ "$rc" = "124" ]; then echo "  超时（测试挂起，不算发现）：$name"; missed=$((missed + 1))
  elif [ "${pass:-0}" -lt 1 ]; then echo "  测试没有跑起来（不算发现）：$name"; missed=$((missed + 1))
  fi
done
if [ $missed -gt 0 ]; then echo "bff-mutation: 失败，$missed/$total 个变异没有被发现"; exit 1; fi
echo "bff-mutation: 通过（$total 个变异全部被发现）"
