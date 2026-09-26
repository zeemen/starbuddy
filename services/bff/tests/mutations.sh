#!/usr/bin/env bash
# BFF 集成测试的变异测试：逐条去掉计量函数里的加锁或并发判断，断言 Gate 0 集成测试能发现。
# 这几条只有在多连接并发下才会出错，所以放在这里而不是 db/tests/mutations.sh。
# 并发相关的失败依赖时序：每个变异最多跑 3 次，任何一次失败就算“被发现”。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
cd "$HERE/.."

fr() {  # 函数定义里的文本替换；找不到模式时让变异本身失败
  printf "DO \$\$ DECLARE d text := pg_get_functiondef('%s'::regprocedure); BEGIN IF position(%s IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, %s, %s); END \$\$;" "$1" "$2" "$2" "$3"
}
RS='platform.meter_reserve(uuid, text, text, text, numeric, text, text)'
declare -A M=(
  ["预留不锁额度桶"]="$(fr "$RS" "'ORDER BY en.id FOR UPDATE OF en;'" "'ORDER BY en.id;'")"
  ["预留加锁后不再查同 key"]="$(fr "$RS" "E'SELECT * INTO r FROM platform.usage_reservation WHERE idempotency_key = p_key;\n  IF FOUND THEN\n    RETURN platform.meter_same_key(r, p_student, p_resource, p_fingerprint, p_content_fingerprint, p_source_method);\n  END IF;\n\n  -- 24'" "'-- 24'")"
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
if [ $missed -gt 0 ]; then echo "bff-mutation: 失败，$missed/$total 个变异没有被发现"; exit 1; fi
echo "bff-mutation: 通过（$total 个变异全部被发现）"
