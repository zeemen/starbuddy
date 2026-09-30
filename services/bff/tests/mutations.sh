#!/usr/bin/env bash
# BFF 集成测试的变异测试：逐条去掉计量函数里的加锁或并发判断，断言 Gate 0 集成测试能发现；
# 另有星图读模块、建档与行为事件接口的源码变异（见下方 SM），断言对应的集成测试能发现。
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
  $'连接角色核对放过 BYPASSRLS\tsrc/star-map/query.ts\tNOT r.rolsuper AND NOT r.rolbypassrls\tNOT r.rolsuper'
  $'未授权与不存在的响应不同\tsrc/http/feature-routes.ts\treturn map ? json(200, map) : problem(P.NOT_FOUND);\treturn map ? json(200, map) : problem(P.FORBIDDEN);'
  $'星图响应可以缓存\tsrc/http/server.ts\t\'cache-control\': \'no-store\',\t'
  $'日志记原始 student_id\tsrc/http/server.ts\trecord[\'student\'] = logHash(deps.keys, params[\'student_id\']);\trecord[\'student\'] = params[\'student_id\'];'
  # 行为事件与探针（冲刺 2-L）：日志不记学生 ID 原文；BFF 拒收未知键；无权回 403
  $'事件请求体放过未知键\tsrc/events/query.ts\treturn got.length === want.length && got.every((k, i) => k === want[i]);\treturn want.every((k) => got.includes(k));'
  $'事件无权当成功\tsrc/events/query.ts\tif (code === \'42501\') return { kind: \'forbidden\' };\tif (code === \'42501\') return { kind: \'ok\', value: null as T };'
  # 审查处理（契约审查 6、安全审查 B1）：重放回 200 并带响应头；54000 映射 429；幂等键冲突与“已关联别的作答”各有错误码
  $'事件重放也回 201\tsrc/http/feature-routes.ts\tjson(r.replayed ? 200 : 201,\tjson(201,'
  $'事件重放不标响应头\tsrc/http/feature-routes.ts\t{ \'idempotent-replayed\': r.replayed ? \'true\' : \'false\' }\t{ \'idempotent-replayed\': \'false\' }'
  $'事件写入上限不映射 429\tsrc/events/query.ts\tif (code === \'54000\') return { kind: \'too_many\' };\t'
  $'幂等键冲突复用 CONFLICT 文案\tsrc/http/feature-routes.ts\tP.PROBE_LINKED, () => OK\tP.CONFLICT, () => OK'
  # 计量（plugin-api-v1.0-step2）：预留过期后只查询重放接口，不发原请求；这几条跑计量测试
  $'计量：过期后仍发原请求\tsrc/metering/meter.ts\tif (r.state === \'expired\') return replayAfterExpiry(\t if (false) return replayAfterExpiry('
  $'计量：重放没有结果当成功\tsrc/metering/meter.ts\tif (out.kind === \'found\') {\tif (out.kind !== \'busy\') {'
  $'计量：路由 404 当作已封存\tsrc/metering/learning-client.ts\tif (status === 404 && problem.code === \'REPLAY_NOT_FOUND\')\tif (status === 404)'
  $'计量：重放 BUSY 当作已封存\tsrc/metering/learning-client.ts\tif (status === 409 && problem.code === \'BUSY\') return { kind: \'busy\' };\tif (status === 409) return { kind: \'not_found\', problem };'
  # 数学自适应诊断（SIE-02a）：只认学生本人、计量（开始预留、结束结算、放弃与确定失败释放、结果未知不释放、续做不另占）、
  # 视图不带题号、日志只记哈希；这几条跑诊断集成测试（学习服务与阿鸦适配器进程）
  $'诊断：开始不核对学生本人\tsrc/diagnosis/service.ts\tif (!(await isSelf(deps.pool, account, student))) return FORBIDDEN;\t'
  $'诊断：取当前题不核对会话归属\tsrc/diagnosis/service.ts\tif (!(await ownsSession(deps.pool, account, student, session))) return FORBIDDEN;\t'
  $'诊断：学习服务确定拒绝时不释放\tsrc/diagnosis/service.ts\t    if (holding) await releaseQuietly(deps, account, usage, \'upstream_rejected\');\t'
  $'诊断：结果未知就释放\tsrc/diagnosis/service.ts\t  if (r.kind === \'unknown\') return UNAVAILABLE;  // 不释放\t  if (r.kind === \'unknown\') { await releaseQuietly(deps, account, usage, \'unknown\'); return UNAVAILABLE; }  // 不释放'
  $'诊断：续做也另占一笔额度\tsrc/diagnosis/service.ts\t  if (open !== null) {\t  if (open === \'never\') {'
  # 审查处理（安全审查 B1、B2、S3）：第一道题判分即结算、答过题放弃不退、没答题放弃才退；宵禁挡续做 / 探针 / 作答并暂停会话；探针键派生
  $'诊断：第一道题判分后不结算（等会话结束）\tsrc/diagnosis/service.ts\t  await settleQuietly(deps, account, r.body.usage_id);\t  if (r.body.status === \'finished\') await settleQuietly(deps, account, r.body.usage_id);'
  $'诊断：没答题放弃不释放\tsrc/diagnosis/service.ts\t    else await releaseQuietly(deps, account, v.usage_id, \'abandoned\');\t'
  $'诊断：续做不查宵禁\tsrc/diagnosis/service.ts\tawait curfewGate(deps, account, student, open);  // 宵禁：续做\tnull;'
  $'诊断：取当前题不查宵禁\tsrc/diagnosis/service.ts\tawait curfewGate(deps, account, student, session);  // 宵禁：取题\tnull;'
  $'诊断：探针不查宵禁\tsrc/diagnosis/service.ts\tawait curfewGate(deps, account, student, session);  // 宵禁：探针\tnull;'
  $'诊断：宵禁不把会话置为暂停\tsrc/diagnosis/service.ts\t  if (session !== null) {\t  if (session === \'never\') {'
  $'诊断：宵禁不看计量只读函数的结果\tsrc/diagnosis/service.ts\treturn rows[0]!.s.in_curfew === true;\treturn false;'
  $'诊断：探针幂等键不按步骤派生\tsrc/diagnosis/service.ts\t[student, step.subject, step.item_id, input.prediction, input.asked_at, probeKey]\t[student, step.subject, step.item_id, input.prediction, input.asked_at, input.idempotency_key]'
  $'诊断：视图带出题号\tsrc/diagnosis/service.ts\t    seq: v.step.seq, kind: v.step.kind,\t    seq: v.step.seq, item_id: v.step.item_id, kind: v.step.kind,'
  $'诊断：日志记原始 student_id\tsrc/http/server.ts\trecord[\'student\'] = logHash(deps.keys, params[\'student_id\']);\trecord[\'student\'] = params[\'student_id\'];'
  # 统一壳 H2（specs/unified-shell-sprint4）：星图、建档、事件挂到路由表上，身份只来自会话；这几条跑星图、建档、事件的集成测试
  $'壳：身份改从 x-dev-account-id 读\tsrc/http/server.ts\tlet session: Session | null = null;\tlet session: Session | null = header(req, \'x-dev-account-id\') ? { token: \'\', accountId: header(req, \'x-dev-account-id\')!, kind: \'student\', origin: \'otp\' } : null;'
  $'壳：星图不需要会话\tsrc/http/feature-routes.ts\tread(\'/students/{student_id}/star-map\', \'session\', starMap)\tread(\'/students/{student_id}/star-map\', \'public\', starMap)'
  $'壳：建档与事件写入不开 CSRF\tsrc/http/feature-routes.ts\t({ method, path, auth, csrf: \'session\',\t({ method, path, auth, csrf: \'none\','
  $'壳：表外路径不回 404\tsrc/http/server.ts\t      res.writeHead(404, {\t      res.writeHead(200, {'
  # 统一壳 H1 认证与会话（specs/unified-shell-sprint4，第二轮 7.8 的 H1 部分）：这几条跑 tests/auth.integration.test.ts
  $'认证：身份改从请求头读\tsrc/http/server.ts\tsession = { token: presented, accountId: rows[0].account_id,\tsession = { token: presented, accountId: header(req, \'x-account-id\') ?? rows[0].account_id,'
  $'认证：set_config 第三个参数改为 false\tsrc/metering/db.ts\tset_config(\'app.account_id\', $1, true)\tset_config(\'app.account_id\', $1, false)'
  $'认证：限流按原因返回不同错误\tsrc/http/server.ts\tif (!rows[0]?.ok) throw new Stop(P.TOO_MANY);\tif (!rows[0]?.ok) throw new Stop(bucket.startsWith(\'otp_request\') ? P.TOO_MANY : P.FORBIDDEN);'
  $'认证：新路由绕过鉴权\tsrc/http/routes.ts\tauth: \'guardian\', csrf: \'session\', body: false, handler: openStudentMode\tauth: \'public\', csrf: \'session\', body: false, handler: openStudentMode'
  $'认证：学生会话可调家长路由\tsrc/http/server.ts\t(route.auth === \'guardian\' && session.kind !== \'guardian\') || \t'
  $'认证：CSRF 不核对会话令牌\tsrc/http/server.ts\tif (route.csrf === \'session\') {\tif (false) {'
  $'认证：CSRF 不核对 Origin\tsrc/http/server.ts\tif (origin === null || !safeEqual(origin, deps.shellOrigin)) throw new Stop(P.CSRF);\t'
  $'认证：无权打开学生模式回 403\tsrc/http/routes.ts\tif (pgCode(e) === \'42501\') return problem(P.NOT_FOUND);  // 未绑定\tif (pgCode(e) === \'42501\') return problem(P.FORBIDDEN);  // 未绑定'
  $'认证：日志记原始 student_id\tsrc/http/server.ts\trecord[\'student\'] = logHash(deps.keys, params[\'student_id\']);\trecord[\'student\'] = params[\'student_id\'];'
  $'认证：登录不作废本设备旧令牌\tsrc/http/routes.ts\tlife.absolute_seconds, life.idle_seconds, ctx.presentedToken]\tlife.absolute_seconds, life.idle_seconds, null]'
  $'认证：登出不废会话\tsrc/http/routes.ts\tawait ctx.q(\'SELECT platform.auth_session_revoke($1)\', [ctx.session!.token]);\t'
  $'认证：捕获桩收真实号码\tsrc/http/sms.ts\tif (!SYNTHETIC_PHONE.test(phone)) throw new Error(\'capture stub only accepts synthetic +999 numbers\');\t'
  $'认证：发验证码不核对号码格式\tsrc/http/routes.ts\tif (!onlyKeys(b, [\'phone\']) || typeof b[\'phone\'] !== \'string\' || !E164.test(b[\'phone\'])) return problem(P.INVALID);\tif (!onlyKeys(b, [\'phone\']) || typeof b[\'phone\'] !== \'string\') return problem(P.INVALID);'
)
# 副本保持仓库结构（测试按 ../../../ 找仓库根：adapters/aya 的登记脚本、db/seeds 的种子），adapters、db 软链接回仓库。
# 安全审查阻断 1：原来的副本不保持结构，测试的 before 在任何代码下都失败，变异全部“被发现”却不说明任何问题。
# 所以：先跑一次不变异的对照，必须通过；每条变异限时（挂起算漏检）；只有“至少一项测试通过、另有失败”才算被发现，
# 保证失败来自断言而不是环境没起来。
star_run() {  # $1 副本根目录，$2 变异名（“计量：”开头的跑计量测试）；输出 rc 与通过数
  local out rc pass tests="tests/star-map.test.ts tests/star-map.integration.test.ts tests/onboarding.integration.test.ts tests/events.integration.test.ts"
  [[ "${2:-}" == 计量：* ]] && tests="tests/learning-client.test.ts tests/gate0.integration.test.ts"
  [[ "${2:-}" == 诊断：* ]] && tests="tests/diagnosis.integration.test.ts"
  [[ "${2:-}" == 认证：* ]] && tests="tests/auth.integration.test.ts"
  out=$( cd "$1/services/bff" && timeout 240 bash "$ROOT/db/tests/pg-ephemeral.sh" -- \
      node --no-warnings --test --test-concurrency=1 --test-timeout=60000 $tests 2>&1 ); rc=$?
  pass=$(printf '%s\n' "$out" | sed -n 's/^# pass \([0-9]*\)$/\1/p' | tail -1)
  echo "$rc ${pass:-0}"
}
star_copy() {
  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-bffmut.XXXXXX")"
  mkdir -p "$work/services/bff"
  cp -r src tests openapi config package.json tsconfig.json "$work/services/bff/"
  ln -s "$PWD/node_modules" "$work/services/bff/node_modules"
  ln -s "$ROOT/adapters" "$work/adapters"; ln -s "$ROOT/db" "$work/db"
  ln -s "$ROOT/services/learning" "$work/services/learning"  # 诊断集成测试起学习服务进程（只读用，不改）
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
