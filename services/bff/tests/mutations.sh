#!/usr/bin/env bash
# BFF 集成测试的变异测试：逐条去掉计量函数里的加锁或并发判断，断言 Gate 0 集成测试能发现；
# 另有星图读模块、建档与行为事件接口的源码变异（见下方 SM），断言对应的集成测试能发现；
# 冲刺 4-H H6 起每类第二轮第 7 节回归（授权矩阵、身份注入、连接池、会话、限流、安全头、计量入口）至少一条（“H6：”）。
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

# BFFMUT_FILTER=信任：只跑名字里含这段文字的变异（对照组照跑）；缺省全跑
FILTER="${BFFMUT_FILTER:-}"
missed=0; total=0
for name in "${!M[@]}"; do
  if [ -n "$FILTER" ] && [[ "$name" != *"$FILTER"* ]]; then continue; fi
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
  # 宵禁读取在错题再练 PR 3（审查 B1）抽成共享的 src/metering/curfew.ts，诊断与再练共用；这条仍跑诊断集成测试
  $'诊断：宵禁不看计量只读函数的结果\tsrc/metering/curfew.ts\treturn rows[0]!.s.in_curfew === true;\treturn false;'
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
  # 统一壳 H4 页面与读路由（第二轮 7.8 的 H4 部分）：这几条跑 tests/shell-pages.integration.test.ts
  $'页面：无判断也显示推荐理由\tsrc/http/shell-routes.ts\tlet rationale: Rationale = null;\tlet rationale: Rationale = { kind: \'content\', code: t.rationale_code };'
  $'页面：白名单检查去掉\tsrc/shell/statements.ts\tif (!cfg.externalTemplates.has(templateRef(r.template_key, r.template_version))) continue;\t'
  $'页面：学生会话可以选科\tsrc/http/shell-routes.ts\tauth: \'guardian\', csrf: \'session\', body: false, handler: subjectOp(\'enroll_subject\')\tauth: \'session\', csrf: \'session\', body: false, handler: subjectOp(\'enroll_subject\')'
  $'页面：学生端也给分来源明细\tsrc/http/shell-routes.ts\t(guardian ? b : {\t(true ? b : {'
  $'页面：宵禁临近不看配置阈值\tsrc/http/shell-routes.ts\tg.minutes_until_curfew <= cfg.curfewNearMinutes\tg.minutes_until_curfew <= 0'
  $'页面：今日计划不按优先级排序\tsrc/http/shell-routes.ts\tconst sorted = [...sec.tasks].sort((a, b) => b.priority - a.priority || KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));\tconst sorted = [...sec.tasks];'
  $'页面：理由不看编码的模板映射\tsrc/http/shell-routes.ts\t.filter((c) => allowed?.has(templateRef(c.template_key, c.template_version)))\t.filter(() => true)'
  $'页面：整页不设总时限\tsrc/shell/statements.ts\tawait Promise.race([Promise.all(\tawait Promise.all([Promise.all('
  $'页面：今日计划不限流\tsrc/http/shell-routes.ts\trateLimits: [{ bucket: \'today.session.minute\', dim: \'session\' }]\trateLimits: []'
  $'页面：默认宵禁不取单处配置\tsrc/http/shell-routes.ts\tdefault_curfew: { start: shellConfig(ctx.deps).meterDefaults.curfewStart,\tdefault_curfew: { start: \'22:30\','
  $'页面：选科口径不看周期\tsrc/http/shell-routes.ts\tAND $2::timestamptz >= s.period_start AND $2::timestamptz < s.period_end\tAND $2::timestamptz IS NOT NULL'
  $'认证：发验证码不核对号码格式\tsrc/http/routes.ts\tif (!onlyKeys(b, [\'phone\']) || typeof b[\'phone\'] !== \'string\' || !E164.test(b[\'phone\'])) return problem(P.INVALID);\tif (!onlyKeys(b, [\'phone\']) || typeof b[\'phone\'] !== \'string\') return problem(P.INVALID);'
  # SIE-03 PR③ 信任界面（异议、考试、预测与对账卡、导出、删除；开关与授权矩阵）：这几条跑 tests/trust-units.test.ts 与 tests/trust.integration.test.ts
  $'信任：开关关闭也注册路由\tsrc/http/routes.ts\t...TRUST_ROUTES.filter((r) => r.feature !== undefined && features.has(r.feature))\t...TRUST_ROUTES'
  $'信任：导出路由学生会话可用\tsrc/http/trust-routes.ts\t`${S}/exports`, \'guardian\', false, exportCreate\t`${S}/exports`, \'session\', false, exportCreate'
  $'信任：写路由不开 CSRF\tsrc/http/trust-routes.ts\t({ feature, method: \'POST\', path, auth, csrf: \'session\',\t({ feature, method: \'POST\', path, auth, csrf: \'none\','
  $'信任：家长可提“那次只是失误”\tsrc/http/trust-routes.ts\tif (kind === \'one_off_slip\' && s.kind !== \'student\') return problem(P.FORBIDDEN);\t'
  $'信任：异议不核对判断可见\tsrc/http/trust-routes.ts\tif (!(await visibleClaims(q, id, audience, [claimId], DISPUTABLE)).has(claimId)) return null;\t'
  $'信任：异议幂等键原样转发\tsrc/http/trust-routes.ts\tidempotencyKey: idem }\tidempotencyKey: key }'
  $'信任：异议结果转发 fact_ref\tsrc/trust/clients.ts\treturn { dispute_id: r[\'dispute_id\'], next_step: step as NextStep,\treturn { ...r, dispute_id: r[\'dispute_id\'], next_step: step as NextStep,'
  $'信任：考试录入无权回 403\tsrc/http/trust-routes.ts\tif (code === \'42501\') return problem(P.NOT_FOUND);\tif (code === \'42501\') return problem(P.FORBIDDEN);'
  $'信任：预测不核对判断可见\tsrc/http/trust-routes.ts\tconst view = p && shown.has(p.claim_id) ? p : null;\tconst view = p;'
  $'信任：对账句子不核对判断可见\tsrc/http/trust-routes.ts\tconst statements = card.statements.filter((s) => shown.get(s.claim_id) === templateRef(s.template_key, s.template_version) && s.display === \'solid\');\tconst statements = card.statements;'
  $'信任：对账卡保留没有 claim_id 的句子\tsrc/trust/clients.ts\tif (st) statements.push(st); else dropped += 1;\tstatements.push((st ?? s) as RenderedStatement);'
  $'信任：导出凭据可重复兑换\tsrc/trust/exports.ts\tthis.#drop(k);  // 一次性：兑换即删\t'
  $'信任：导出兑换不核对发起账号\tsrc/trust/exports.ts\t || e.accountId !== match.accountId) return null;\t) return null;'
  $'信任：导出兑换不再核绑定\tsrc/http/trust-routes.ts\tif (rows[0]?.linked !== true) {\tif (false) {'
  $'信任：删除确认不核对会话内凭据\tsrc/http/trust-routes.ts\tif (!safeEqual(b[\'confirm_token\'], confirmTokenFor(ctx, requestId))) return problem(P.CONFIRM_REQUIRED);\t'
  $'信任：删除凭据不绑定会话\tsrc/http/trust-routes.ts\t`${ctx.session!.token}:${requestId}`\t`${ctx.session!.accountId}:${requestId}`'
  # 审查处理（安全审查 M1–M3、H1、H2、L4b；契约审查 L1）
  $'信任：导出下载不查删除状态\tsrc/http/trust-routes.ts\t  if (await erasureBlocked(ctx.q, id)) {\t  if (false) {'
  $'信任：已确认删除仍可导出\tsrc/http/trust-routes.ts\tif (await erasureBlocked(q, id)) return \'blocked\' as const;\t'
  $'信任：删除确认不清暂存\tsrc/http/trust-routes.ts\t    exportStore(ctx.deps).purgeStudent(student(ctx));\t'
  $'信任：解除绑定兑换不清暂存\tsrc/http/trust-routes.ts\t    exportStore(ctx.deps).purgeStudent(id, account);\t'
  $'信任：导出下载日志不记账号摘要\tsrc/http/trust-routes.ts\t  ctx.note(\'account\', logHash(ctx.deps.keys, account));\t'
  $'信任：暂存不限 1 小时\tsrc/trust/exports.ts\texpiresAt: Math.min(entry.expiresAt, now + MAX_HOLD_MS)\texpiresAt: entry.expiresAt'
  $'信任：暂存总量不设上限\tsrc/trust/exports.ts\t || this.#total + size > this.maxTotalBytes) return false;\t) return false;'
  $'信任：删除确认不要求近期登录\tsrc/http/trust-routes.ts\tif (!recentLogins(ctx.deps).fresh(ctx.deps.keys, ctx.session!.token, nowMs(ctx.deps))) return problem(P.REAUTH_REQUIRED);\t'
  $'信任：对账卡不核对预测判断\tsrc/http/trust-routes.ts\tif (!pred || pred.prediction_id !== card.prediction_id || !shown.has(pred.claim_id)) {\tif (false) {'
  $'信任：异议结果不要求 fact_ref\tsrc/trust/clients.ts\tif (step === \'open_fact_for_correction\' && (typeof r[\'fact_ref\'] !== \'string\' || !FACT_REF.test(r[\'fact_ref\']))) return null;\t'
  $'信任：验证任务引用当题目展示\tsrc/trust/clients.ts\tconst NOT_ITEM_PREFIXES = [\'verify-task-\', \'stub-verify-\'] as const;\tconst NOT_ITEM_PREFIXES = [\'stub-verify-\'] as const;'
  $'信任：不核对统一信封\tsrc/trust/clients.ts\tconst value = envelopeOk(body) ? pickDispute(body.result) : null;\tconst value = pickDispute(body.result);'
  $'信任：异议列表不限流\tsrc/http/trust-routes.ts\tdisputeList, [{ bucket: \'trust_read.session.minute\', dim: \'session\' }]\tdisputeList, []'
  $'信任：近期登录不看时间\tsrc/trust/proof.ts\treturn t !== undefined && now - t < REAUTH_WINDOW_MS && now >= t;\treturn t !== undefined;'
  # 冲刺 5-H 跨科周报 v0（weekly，0036）：每句库内再核判断、白名单、学生版不收家长观察、形状再挑、按会话限流
  $'信任：周报不在库内再核判断\tsrc/http/trust-routes.ts\t    return c !== undefined && c.ref === ref && cfg.externalTemplates.has(ref) && clean(s.text);\t    return clean(s.text);'
  $'信任：周报不看对外模板白名单\tsrc/http/trust-routes.ts\t && c.ref === ref && cfg.externalTemplates.has(ref) && clean(s.text);\t && c.ref === ref && clean(s.text);'
  $'信任：周报学生版收家长观察\tsrc/http/trust-routes.ts\t        AND ($4 <> \'student\' OR c.source_type <> \'guardian\')`,\t`,'
  $'信任：周报不核对修订标记\tsrc/http/trust-routes.ts\tJOIN insight.claim c ON c.id = x.id AND c.updated_at = x.rev\tJOIN insight.claim c ON c.id = x.id'
  $'信任：周报不过禁用推断词表\tsrc/http/trust-routes.ts\t && cfg.externalTemplates.has(ref) && clean(s.text);\t && cfg.externalTemplates.has(ref);'
  $'信任：周报不按判断当前状态标初步判断\tsrc/http/trust-routes.ts\t...(c.status === \'hypothesis\' ? { label: \'preliminary\' } : {})\t...{}'
  $'信任：周报没有修订标记也收\tsrc/trust/weekly.ts\t      || typeof s[\'claim_revision\'] !== \'string\' || !REVISION.test(s[\'claim_revision\'])) {\t      ) {'
  $'信任：周报收已撤回的判断\tsrc/http/trust-routes.ts\tAND c.status IN (\'hypothesis\', \'established\') AND c.target_type = \'node\'\tAND c.target_type = \'node\''
  $'信任：周报句子不核对 claim_id\tsrc/trust/weekly.ts\t  if (typeof s[\'claim_id\'] !== \'string\' || !UUID.test(s[\'claim_id\']) || typeof s[\'template_key\']\t  if (typeof s[\'template_key\']'
  $'信任：周报学生版可带置信度\tsrc/trust/weekly.ts\t  if (audience === \'guardian\' ? typeof band !== \'string\' || !BANDS.includes(band) : band !== undefined) return null;\t'
  $'信任：周报客观栏不核来源\tsrc/trust/weekly.ts\t  if (o[\'source_type\'] !== \'objective\' || \t  if ('
  $'信任：周报只给家长\tsrc/http/trust-routes.ts\t`${S}/weekly-report`, \'session\', weeklyReport\t`${S}/weekly-report`, \'guardian\', weeklyReport'
  $'信任：周报不限流\tsrc/http/trust-routes.ts\tweeklyReport, [{ bucket: \'trust_read.session.minute\', dim: \'session\' }]\tweeklyReport, []'
  # KGE-04 第二步 PR 4 学情档案页（profile）：白名单、实线核对、初步判断标注、谁能读、限流
  $'信任：档案页不看对外模板白名单\tsrc/http/trust-routes.ts\t && pre.shown.get(r.claim_id) === ref && cfg.externalTemplates.has(ref);\t && pre.shown.get(r.claim_id) === ref;'
  $'信任：档案页收洞察服务给的虚线陈述\tsrc/shell/statements.ts\tst.display !== \'solid\' || (st.status !== \'hypothesis\' && st.status !== \'established\')\tfalse'
  $'信任：档案页不按判断当前状态标初步判断\tsrc/http/trust-routes.ts\t              ...(r.claim_status === \'hypothesis\' ? { label: \'preliminary\' } : {}) }];\t }];'
  $'信任：档案页只给家长\tsrc/http/trust-routes.ts\t`${S}/profile-pages/{subject}`, \'session\', profilePage\t`${S}/profile-pages/{subject}`, \'guardian\', profilePage'
  $'信任：档案页不限流\tsrc/http/trust-routes.ts\tprofilePage, [{ bucket: \'trust_read.session.minute\', dim: \'session\' }], true)\tprofilePage, [], true)'
  $'信任：档案页不核对学科码（英语也读）\tsrc/http/shell-routes.ts\tWHERE code = $1 AND status <> \'planned\' AND product IS NOT NULL`\tWHERE code = $1 AND product IS NOT NULL`'
  # SIE-02b 第二步 PR 2 首份报告（report）与单条解释（why）：先按档案授权、只转发 result、契约逐字段核对、库内再核、禁用推断、限流；
  # 这几条跑 tests/report-units.test.ts 与 tests/report.integration.test.ts（BFFMUT_FILTER=报告：）
  $'报告：转发信封外层字段\tsrc/http/trust-routes.ts\treturn json(200, { student_id: who.studentRef, report });\treturn json(200, { student_id: who.studentRef, report, ...out.envelope });'
  $'报告：不按档案授权就调洞察服务\tsrc/http/trust-routes.ts\tconst who = await examWho(ctx);\\n  if (!who) return problem(P.NOT_FOUND);\\n  const client = ctx.deps.trust?.read;\tconst who = { studentRef: student(ctx), audience: audienceOf(ctx), actorRef: ctx.session!.accountId };\\n  const client = ctx.deps.trust?.read;'
  $'报告：不在库内再核判断\tsrc/http/trust-routes.ts\t  if (bad > 0) {\t  if (false) {'
  $'报告：不过对外白名单\tsrc/http/trust-routes.ts\treturn !c || c.ref !== ref || c.status !== x.status || !cfg.externalTemplates.has(ref);\treturn !c || c.ref !== ref || c.status !== x.status;'
  $'报告：不核对判断状态（初步判断标注可能错）\tsrc/http/trust-routes.ts\treturn !c || c.ref !== ref || c.status !== x.status || !cfg.externalTemplates.has(ref);\treturn !c || c.ref !== ref || !cfg.externalTemplates.has(ref);'
  $'报告：库内再核不看可见范围\tsrc/http/trust-routes.ts\tAND $3 = ANY (c.visibility) AND c.expires_at > now()\\n        AND c.status::text = ANY ($4::text[]) AND ($5::boolean\tAND c.expires_at > now()\\n        AND c.status::text = ANY ($4::text[]) AND ($5::boolean'
  $'报告：收非知识节点的判断\tsrc/http/trust-routes.ts\tAND ($5::boolean IS FALSE OR c.target_type = \'node\')\tAND ($5::boolean IS FALSE OR true)'
  $'报告：不过禁用推断词表\tsrc/http/trust-routes.ts\tif (!inferenceFree(ctx, reportTexts(report))) {\tif (false) {'
  $'报告：要人工复核也照出\tsrc/http/trust-routes.ts\t  if (out.envelope.human_review_required) return none;\t'
  $'报告：不核对受众\tsrc/trust/report.ts\tformat !== \'sie-first-report/0.1\' || v[\'audience\'] !== audience ||\tformat !== \'sie-first-report/0.1\' ||'
  $'报告：claim_ids 不核对顺序\tsrc/trust/report.ts\t!ids.every((id, i) => id === order[i])\tfalse'
  $'报告：学生版放过置信度\tsrc/trust/report.ts\tif (audience === \'guardian\' ? typeof band !== \'string\' || !BANDS.includes(band) : band !== undefined) return null;\tif (audience === \'guardian\' && (typeof band !== \'string\' || !BANDS.includes(band))) return null;'
  $'报告：信封不合也当成功\tsrc/trust/report.ts\tif (!envelopeOk(env) || !(\'result\' in env)) return { kind: \'unavailable\' };\tif (!(\'result\' in env)) return { kind: \'unavailable\' };'
  $'报告：不限流\tsrc/http/trust-routes.ts\tfirstReport, [{ bucket: \'trust_read.session.minute\', dim: \'session\' }], true)\tfirstReport, [], true)'
  $'报告：解释不过对外白名单\tsrc/http/trust-routes.ts\tif (!st || !cfg.externalTemplates.has(st.ref)) return null;\tif (!st) return null;'
  $'报告：解释转发证据 ID\tsrc/trust/report.ts\tevidence.push({ occurred_at:\tevidence.push({ evidence_id: eid as string, occurred_at:'
  $'报告：解释不核对陈述状态\tsrc/http/trust-routes.ts\t || view.statement.status !== pre.st.status) {\t) {'
  $'报告：解释的洞察服务 404 改回 503\tsrc/http/trust-routes.ts\t  if (out.kind === \'not_found\') return problem(P.NOT_FOUND);\t  if (out.kind === \'not_found\') return problem(P.TRUST_UNAVAILABLE);'
  # 审查意见：解释按受众裁剪、节键与模板类别、策略版本记日志、响应体上限
  $'报告：解释不按受众裁剪\tsrc/http/trust-routes.ts\t...explainForAudience(view, audience) });\t...view });'
  $'报告：解释学生版放过顶层置信度\tsrc/trust/report.ts\t...(audience === \'guardian\' ? { confidence_band: v.confidence_band } : {}),\tconfidence_band: v.confidence_band,'
  $'报告：解释学生版放过陈述内置信度\tsrc/trust/report.ts\t...(audience === \'guardian\' ? { confidence_band: s.confidence_band } : {}) },\tconfidence_band: s.confidence_band },'
  $'报告：待验证解释带文字\tsrc/trust/report.ts\t...(solid ? { text: s.text } : {}),\ttext: s.text,'
  $'报告：不核对节键与模板类别\tsrc/http/trust-routes.ts\tif (sectionMismatches(report) > 0) {\tif (false) {'
  $'报告：类别按前缀而非整段比较\tsrc/trust/report.ts\treturn SECTION_KEYS.includes(last) ? last as ReportSection[\'key\'] : null;\treturn SECTION_KEYS.find((k) => last.startsWith(k)) as ReportSection[\'key\'] ?? null;'
  $'报告：不记策略版本\tsrc/http/trust-routes.ts\t  if (POLICY_VERSION.test(report.policy_version)) ctx.note(\'policy_version\', report.policy_version);\t'
  $'报告：响应体不设上限\tsrc/trust/report.ts\tconst max = opts.maxBytes ?? MAX_READ_BYTES;\tconst max = Number.MAX_SAFE_INTEGER;'
  $'报告：不看 content-length\tsrc/trust/report.ts\tif (len !== null && (!/^\\d{1,15}$/.test(len.trim()) || Number(len.trim()) > max)) {\tif (false) {'
  $'报告：流式超限不 cancel\tsrc/trust/report.ts\t      await reader.cancel().catch(() => undefined);\\n      return null;\t      return null;'
  # H5 星图 v1（specs/star-map-v1-h5，统一壳 7.2 节；验收“星图不核对 basis_id”“白名单检查去掉”等）：这几条跑 tests/star-map-v1*.test.ts
  $'星图v1：basis 不比对\tsrc/star-map/v1.ts\tnode.basis_id !== null && c.basis_id === node.basis_id\ttrue'
  $'星图v1：中性星泄露 state\tsrc/star-map/v1.ts\tif (s.display === \'neutral\') return { node_id: s.node_id, name, display: \'neutral\' };\tif (s.display === \'neutral\') return { node_id: s.node_id, name, display: \'neutral\', state: nodes.find((n) => n.node_id === s.node_id)!.state } as OutStar;'
  $'星图v1：跳过授权直接调用\tsrc/star-map/v1.ts\treturn rows[0]?.ok === true;\treturn true;'
  $'星图v1：用迁移判断画星\tsrc/star-map/v1.ts\tconst mastery = matching.filter((c) => rules.mastery.has(templateRef(c.template_key, c.template_version)));\tconst mastery = matching.filter((c) => rules.mastery.has(templateRef(c.template_key, c.template_version)) || rules.transfer.has(templateRef(c.template_key, c.template_version)));'
  $'星图v1：白名单不检查\tsrc/star-map/v1.ts\t\\n      && rules.external.has(templateRef(c.template_key, c.template_version))\t'
  $'星图v1：暂停学科也取\tsrc/star-map/v1.ts\tWHERE e.student_id = $1 AND e.status = \'active\' AND c.status\tWHERE e.student_id = $1 AND c.status'
  $'星图v1：虚线开关关也画虚线\tsrc/star-map/v1.ts\tconst pending = rules.dashed ? mastery.find(\tconst pending = true ? mastery.find('
  $'星图v1：开关关闭也注册 v1\tsrc/http/routes.ts\t...(starMap.has(\'starmap_v1\') ? STAR_MAP_V1_ROUTES : [])\t...STAR_MAP_V1_ROUTES'
  $'星图v1：学习服务响应多字段也收\tsrc/star-map/nodes-client.ts\tif (Object.keys(n).sort().join(\',\') !== NODE_KEYS || \tif ('
  $'星图v1：不核对学习服务的地图版本\tsrc/star-map/v1.ts\tif (o.mapVersion !== first.versions.get(e.product) || \tif ('
  $'星图v1：不核对节点集合\tsrc/star-map/v1.ts\t || ours.length !== theirs.length || ours.some((id, i) => id !== theirs[i])\t'
  $'星图v1：去掉禁用推断词表过滤\tsrc/star-map/v1.ts\tif (st && !clean(st.text)) {\tif (false) {'
  # 届别为真相源（specs/policy-decisions-v0.9.1 PR2）：年级读点按届别与请求时钟推算、家长届别更正入口的授权、请求体与频控；
  # 这几条跑 tests/cohort.integration.test.ts
  $'届别：/me 读存下的建档年级\tsrc/http/routes.ts\tplatform.current_grade(s.exam_cohort, $1::timestamptz) AS grade\tCASE WHEN $1::timestamptz IS NULL THEN NULL ELSE s.grade END AS grade'
  $'届别：/me 不用请求时钟\tsrc/http/routes.ts\tORDER BY l.created_at, l.student_id`, [asOf(ctx.deps)]);\tORDER BY l.created_at, l.student_id`, [new Date()]);'
  $'届别：选科目录读存下的建档年级\tsrc/http/shell-routes.ts\t\'SELECT platform.current_grade(s.exam_cohort, $2::timestamptz) AS grade FROM platform.student_profile s WHERE s.id = $1\'\t\'SELECT s.grade FROM platform.student_profile s WHERE s.id = $1 AND $2::timestamptz IS NOT NULL\''
  $'届别：更正收未知字段\tsrc/http/shell-routes.ts\tif (!b || Object.keys(b).join(\',\') !== \'exam_cohort\') return problem(P.INVALID);\tif (!b || !(\'exam_cohort\' in b)) return problem(P.INVALID);'
  $'届别：更正无权回 403\tsrc/http/shell-routes.ts\tif (code === \'42501\' || code === \'P0002\') return problem(P.NOT_FOUND);\tif (code === \'42501\' || code === \'P0002\') return problem(P.FORBIDDEN);'
  $'届别：更正不限流\tsrc/http/shell-routes.ts\trateLimits: [{ bucket: \'cohort_correction.link.day\', dim: \'link\' }, { bucket: \'cohort_correction.account.day\', dim: \'account\' }]\trateLimits: []'
  $'届别：更正没有按账号的总量上限\tsrc/http/shell-routes.ts\t, { bucket: \'cohort_correction.account.day\', dim: \'account\' }]\t]'
  $'届别：步长错误映射成 404\tsrc/http/shell-routes.ts\tif (code === \'22023\' || code === \'23514\') return problem(P.INVALID);\tif (code === \'22023\' || code === \'23514\') return problem(P.NOT_FOUND);'
  $'届别：步长错误不映射（500）\tsrc/http/shell-routes.ts\tif (code === \'22023\' || code === \'23514\') return problem(P.INVALID);\t'  $'届别：频控只按学生计数\tsrc/http/server.ts\t`${session!.accountId}:${params[\'student_id\'] ?? \'\'}`\t`${params[\'student_id\'] ?? \'\'}`'
  $'届别：学生 /me 读存下的建档年级\tsrc/http/routes.ts\tplatform.current_grade(p.exam_cohort, $1::timestamptz) AS grade\tCASE WHEN $1::timestamptz IS NULL THEN NULL ELSE p.grade END AS grade'
  # 届别 PR3（#12，迁移 0049）：会话中途届别被改时 409 透传 session_id 与 reason，因届别变化放弃即使答过题也退回额度（跑诊断集成测试）
  $'诊断：届别变化放弃不退额度\tsrc/diagnosis/service.ts\tif (v.status === \'abandoned\' && v.abandon_reason === \'grade_changed\') {\tif (false) {'
  $'诊断：409 不透传会话 ID\tsrc/diagnosis/service.ts\t{ reason: \'grade_changed\', session_id: expected }\t{ reason: \'grade_changed\' }'
  $'诊断：续做的 409 不认会话\tsrc/diagnosis/service.ts\tif (r.kind === \'failure\') return fromFailure(r, open);\tif (r.kind === \'failure\') return fromFailure(r, null);'
  $'诊断：退回走结算\tsrc/diagnosis/service.ts\t    await refundQuietly(deps, account, v.usage_id);\\n    return;\t    await settleQuietly(deps, account, v.usage_id);\\n    return;'
  # 届别 PR3（#10）：年级不在 7–9 时选科只能暂停、今日计划不排学科
  $'届别：毕业后也能新选\tsrc/http/shell-routes.ts\t    if (p.grade === null) return problem(P.GRADE_OUT_OF_RANGE);\t'
  $'届别：毕业后选科列表为空\tsrc/http/shell-routes.ts\t OR ($2::smallint IS NULL AND e.status IS NOT NULL)\t'
  $'届别：毕业后选科列表列出全部学科\tsrc/http/shell-routes.ts\t($2::smallint IS NULL AND e.status IS NOT NULL)\t$2::smallint IS NULL'
  $'届别：毕业后今日计划仍排学科\tsrc/http/shell-routes.ts\t    if (p.grade === null) return { subjects: [] as { code: string; product: PlanSubject }[], linkId: undefined };\t'
  $'届别：can_enroll 恒为真\tsrc/http/shell-routes.ts\tcan_enroll: p.grade !== null,\tcan_enroll: true,'
  $'届别：年级检查先于授权\tsrc/http/shell-routes.ts\t    if (!p) return problem(P.NOT_FOUND);\\n    if (p.grade === null) return problem(P.GRADE_OUT_OF_RANGE);\t    if (!p || p.grade === null) return problem(P.GRADE_OUT_OF_RANGE);'
  # 届别 PR4（迁移 0051）：库函数 enroll_subject 的年级拒绝（55000 GRADE_OUT_OF_RANGE）映射成与前置检查相同的 409
  $'届别：库层年级拒绝不映射（500）\tsrc/http/shell-routes.ts\t    if (fn === \'enroll_subject\' && isGradeOutOfRange(e)) return problem(P.GRADE_OUT_OF_RANGE);\t'
  $'届别：库层年级拒绝映射成 404\tsrc/http/shell-routes.ts\t    if (fn === \'enroll_subject\' && isGradeOutOfRange(e)) return problem(P.GRADE_OUT_OF_RANGE);\t    if (fn === \'enroll_subject\' && isGradeOutOfRange(e)) return problem(P.NOT_FOUND);'
  # 冲刺 4-H H6 第二轮第 7 节回归（每类至少一条）：这几条跑 tests/route-matrix.integration.test.ts
  # 7.1 授权矩阵与路由表覆盖
  $'H6：新路由不登记样例\tsrc/http/shell-routes.ts\t  read(`${S}/balance`, \'session\', balance),\t  read(`${S}/balance`, \'session\', balance), read(`${S}/balance-copy`, \'session\', balance),'
  $'H6：学生会话可改时长设置\tsrc/http/shell-routes.ts\tauth: \'guardian\', csrf: \'session\', body: true, handler: timeGuardPut\tauth: \'session\', csrf: \'session\', body: true, handler: timeGuardPut'
  $'H6：选科越权回 403\tsrc/http/shell-routes.ts\t    return json(200, Object.fromEntries(ENROLL_KEYS.filter((k) => k in r).map((k) => [k, r[k]])));\\n  } catch (e) {\\n    if (pgCode(e) === \'42501\') return problem(P.NOT_FOUND);\t    return json(200, Object.fromEntries(ENROLL_KEYS.filter((k) => k in r).map((k) => [k, r[k]])));\\n  } catch (e) {\\n    if (pgCode(e) === \'42501\') return problem(P.FORBIDDEN);'
  # 安全审查 M1：跨对象（自己孩子的路径带兄妹的导出任务与凭据）
  $'H6：导出兑换不核对学生\tsrc/trust/exports.ts\t || e.studentId !== match.studentId || \t || '
  # 契约审查 M3：只认本人路由的拒绝码按接口定义（x-sb-not-self）
  $'H6：事件与探针的“不是本人”改回 404\tsrc/http/feature-routes.ts\to.kind === \'forbidden\' ? problem(P.FORBIDDEN)\to.kind === \'forbidden\' ? problem(P.NOT_FOUND)'
  $'H6：方法不对回 404\tsrc/http/server.ts\tif (!found) throw new Stop(P.METHOD_NOT_ALLOWED);\tif (!found) throw new Stop(P.NOT_FOUND);'
  # 7.2 身份注入
  $'H6：身份改从查询参数读\tsrc/http/server.ts\tsession = { token: presented, accountId: rows[0].account_id,\tsession = { token: presented, accountId: url.searchParams.get(\'account_id\') ?? rows[0].account_id,'
  $'H6：路径参数可被查询参数覆盖\tsrc/http/server.ts\tparams[n] = found.m![i + 1]!;\tparams[n] = url.searchParams.get(n) ?? found.m![i + 1]!;'
  # 7.3 连接池串号
  $'H6：set_config 第三个参数改为 false\tsrc/metering/db.ts\tset_config(\'app.account_id\', $1, true)\tset_config(\'app.account_id\', $1, false)'
  # 7.4 会话
  $'H6：退出所有设备不废会话\tsrc/http/routes.ts\tawait ctx.q(\'SELECT platform.auth_session_revoke_all($1)\', [ctx.session!.token]);\t'
  $'H6：登出不废会话\tsrc/http/routes.ts\tawait ctx.q(\'SELECT platform.auth_session_revoke($1)\', [ctx.session!.token]);\t'
  # 7.5 限流
  $'H6：导出不挂限流桶\tsrc/http/trust-routes.ts\texportCreate, [{ bucket: \'export.account.hour\', dim: \'account\' }]\texportCreate, []'
  $'H6：按账号限流的键每次不同\tsrc/http/server.ts\tawait limit(r.bucket, \'account\', session!.accountId);\tawait limit(r.bucket, \'account\', randomUUID());'
  $'H6：限流按原因返回不同错误\tsrc/http/server.ts\tif (!rows[0]?.ok) throw new Stop(P.TOO_MANY);\tif (!rows[0]?.ok) throw new Stop(dim === \'ip\' ? P.TOO_MANY : P.FORBIDDEN);'
  # 7.6 安全头与缓存
  $'H6：响应可以缓存\tsrc/http/server.ts\t        \'cache-control\': \'no-store\',\t        \'cache-control\': \'private, max-age=60\','
  $'H6：响应带 CORS 放行头\tsrc/http/server.ts\t        \'x-content-type-options\': \'nosniff\',\t        \'x-content-type-options\': \'nosniff\', \'access-control-allow-origin\': \'*\','
  # 7.7 计量入口
  $'H6：诊断开始收前端带来的字段\tsrc/diagnosis/service.ts\tif (!exactKeys(b, [\'subject\', \'idempotency_key\']) || b[\'subject\']\tif (b[\'subject\']'
  $'H6：诊断失败不释放预留\tsrc/diagnosis/service.ts\t    if (holding) await releaseQuietly(deps, account, usage, \'upstream_rejected\');\t'
  # 冲刺 4-I 第二部分 语音分钟计量辅助（src/voice/minutes.ts）：取整、封顶、只扣 voice_realtime、失败回滚、未知不释放、同 key 重放、结算余量；
  # 这几条跑 tests/voice-minutes*.test.ts
  $'语音：向下取整\tsrc/voice/minutes.ts\treturn Math.ceil(durationMs / MS_PER_MINUTE);\treturn Math.floor(durationMs / MS_PER_MINUTE);'
  $'语音：四舍五入\tsrc/voice/minutes.ts\treturn Math.ceil(durationMs / MS_PER_MINUTE);\treturn Math.round(durationMs / MS_PER_MINUTE);'
  $'语音：超出预留不按预留封顶\tsrc/voice/minutes.ts\tconst charged = Math.min(billable, reservation.reservedMinutes);\tconst charged = billable;'
  $'语音：调用方可指定别的资源\tsrc/voice/minutes.ts\tif (input.resourceType !== undefined && input.resourceType !== VOICE_RESOURCE) {\tif (false) {'
  $'语音：预留改扣 ai_active_time\tsrc/voice/minutes.ts\t[input.studentId, VOICE_RESOURCE, input.idempotencyKey,\t[input.studentId, \'ai_active_time\', input.idempotencyKey,'
  $'语音：会话失败不释放\tsrc/voice/minutes.ts\t    await releaseVoiceMinutes(deps.pool, input.accountId, res, \'voice_session_failed\');\t'
  $'语音：结果未知就释放\tsrc/voice/minutes.ts\t    emit(\'session_result_unknown\');\t    emit(\'session_result_unknown\'); await releaseVoiceMinutes(deps.pool, input.accountId, res, \'unknown\');'
  $'语音：已结算的 key 重放再开会话\tsrc/voice/minutes.ts\tif (state === \'settled\') return\tif (false) return'
  $'语音：已释放的 key 重放再开会话\tsrc/voice/minutes.ts\tif (state === \'released\') return\tif (false) return'
  $'语音：会话时长不留结算余量\tsrc/voice/minutes.ts\tDate.parse(res.expiresAt) - now() - SETTLE_MARGIN_MS\tDate.parse(res.expiresAt) - now()'
  $'语音：结算不核对 usage_id 属于这个 key\tsrc/voice/minutes.ts\tif (checkKey(r.idempotencyKey) !== r.usageId) throw\tif (false) throw'
  $'语音：指纹不含预计分钟\tsrc/voice/minutes.ts\tresource: VOICE_RESOURCE, estimated_minutes: estimatedMinutes }\tresource: VOICE_RESOURCE }'
  # 审查处理（B1、5）：同 key 找回仍在预留中的那笔不开新会话；核对库返回的 usage_id；no_time_left 时释放
  $'语音：resumed 时照样开会话\tsrc/voice/minutes.ts\t  if (r.resumed) return { status: \'in_progress\', usageId: r.reservation.usageId };\t'
  $'语音：不核对库返回 usage_id\tsrc/voice/minutes.ts\tif (r[\'usage_id\'] !== usageId) throw\tif (false) throw'
  $'语音：no_time_left 时不释放\tsrc/voice/minutes.ts\t    await releaseVoiceMinutes(deps.pool, input.accountId, res, \'voice_no_time_left\');\t'
  # 错题再练（specs/practice-loop-stage1 PR 3）：只认学生本人、开关、计次（作答计一次、一张票据最多扣一次、本地可判的拒绝不计次）、票据绑定学生、
  # 宵禁、响应白名单、失败映射、重试；这几条跑 tests/practice-units.test.ts、tests/practice.integration.test.ts、tests/shell-openapi.test.ts
  $'再练：作答不核对学生本人\tsrc/practice/service.ts\t  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;\\n  // 本地能判的拒绝\t  // 本地能判的拒绝'
  $'再练：列表不核对学生本人\tsrc/practice/service.ts\t  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;\\n  const r = await withRetry(deps, (ms) => deps.learning.queue(\t  const r = await withRetry(deps, (ms) => deps.learning.queue('
  $'再练：取题不核对本人\tsrc/practice/service.ts\t  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;\\n  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：取题\t  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：取题'
  $'再练：开关关闭也注册路由\tsrc/http/routes.ts\t...(practice ? PRACTICE_ROUTES : [])\t...PRACTICE_ROUTES'
  $'再练：家长会话可读列表\tsrc/http/practice-routes.ts\t{ method: \'GET\', path: S, auth: \'student\'\t{ method: \'GET\', path: S, auth: \'session\''
  $'再练：不限流\tsrc/http/practice-routes.ts\tconst RL = [{ bucket: \'practice.session.minute\', dim: \'session\' }] as const;\tconst RL = [] as const;'
  $'再练：改挂别的限流桶\tsrc/http/practice-routes.ts\tconst RL = [{ bucket: \'practice.session.minute\', dim: \'session\' }] as const;\tconst RL = [{ bucket: \'trust_read.session.minute\', dim: \'session\' }] as const;'
  $'再练：不挡宵禁\tsrc/practice/service.ts\t  return c ? CURFEW : null;\t  return null;'
  $'再练：作答在计次之后才查宵禁\tsrc/practice/service.ts\t  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：作答（计次之前）\\n  if (blocked) return blocked;\\n  const usage = usageIdFor(key);\\n  const m = await countPractice(deps.pool, account, student, key);\t  const usage = usageIdFor(key);\\n  const m = await countPractice(deps.pool, account, student, key);\\n  const blocked = await curfewBlocked(deps, account, student);\\n  if (blocked) return blocked;'
  $'再练：一张票据可扣多次（用前端或随机的键）\tsrc/practice/service.ts\tconst key = t.idempotencyKey;\tconst key = crypto.randomUUID();'
  $'再练：同票据换作答不回 TICKET_USED\tsrc/practice/service.ts\t(code === \'TICKET_USED\' || code === \'IDEMPOTENCY_CONFLICT\')\t(code === \'TICKET_USED\')'
  $'再练：确定失败兜底回 422\tsrc/practice/service.ts\t  return PRACTICE_UNAVAILABLE;\\n}\\n\\nconst replayHeader\t  return INVALID;\\n}\\n\\nconst replayHeader'
  $'再练：取题收到其他 409 不回 503\tsrc/practice/service.ts\t  if (op !== \'answer\') return PRACTICE_UNAVAILABLE;\t'
  $'再练：确定性拒绝也重试\tsrc/practice/service.ts\tconst DETERMINISTIC = /^(?:501\\b|502 ADAPTER_RESULT_REJECTED$)/;\tconst DETERMINISTIC = /^$/;'
  $'再练：重试不设总时限\tsrc/practice/service.ts\t    if (left() - d <= 0) {\t    if (false) {'
  $'再练：判分结论不按契约枚举\tsrc/practice/service.ts\t!(VERDICTS as readonly unknown[]).includes(v[\'verdict\'])\ttypeof v[\'verdict\'] !== \'string\''
  $'再练：作答不计次\tsrc/practice/service.ts\tconst m = await countPractice(deps.pool, account, student, key);\tconst m = { outcome: \'allowed\', usage_id: usage } as PracticeCount;'
  $'再练：usage_id 不由幂等键派生\tsrc/practice/service.ts\tconst usage = usageIdFor(key);\tconst usage = key;'
  $'再练：额度拒绝不回 402\tsrc/practice/service.ts\t    case \'no_entitlement\': case \'daily_limit_reached\':\\n      return quota(m.outcome);\t    case \'no_entitlement\': case \'daily_limit_reached\':'
  $'再练：票据不绑定学生\tsrc/practice/ticket.ts\t`starbuddy-practice-ticket:v1:${student}`\t`starbuddy-practice-ticket:v1`'
  $'再练：换学科的票据也收\tsrc/practice/service.ts\tif (!t || t.subject !== subject) return TICKET_INVALID;\tif (!t) return TICKET_INVALID;'
  $'再练：作答形状不在计次前核对\tsrc/practice/service.ts\t  if (!answerFits(t.format, t.optionKeys, input.answer)) return INVALID_ANSWER;\t'
  $'再练：本地不判过期\tsrc/practice/service.ts\tif (deps.now() >= Date.parse(t.expiresAt) - EXPIRY_MARGIN_MS && \tif (false && '
  $'再练：过期不留余量\tsrc/practice/service.ts\tDate.parse(t.expiresAt) - EXPIRY_MARGIN_MS\tDate.parse(t.expiresAt)'
  $'再练：已计次的键过期后不放行\tsrc/practice/service.ts\t && !(await alreadyCounted(deps.pool, account, student, key))\t'
  $'再练：作答请求体放过未知键\tsrc/practice/service.ts\tif (!exactKeys(b, [\'ticket\', \'answer\'])) return null;\tif (![\'ticket\', \'answer\'].every((k) => k in b)) return null;'
  $'再练：取题下发学习服务票据\tsrc/practice/service.ts\titem: ready.item, ticket: sealed,\titem: ready.item, ticket: ready.ticket,'
  $'再练：取题转发题目引用\tsrc/practice/service.ts\treturn json(200, { status: \'ready\', entry_id: ready.entry_id,\treturn json(200, { status: \'ready\', item_ref: body[\'item_ref\'], entry_id: ready.entry_id,'
  $'再练：条目不过白名单\tsrc/practice/service.ts\tconst entries = body[\'entries\'].map(pickEntry);\tconst entries = body[\'entries\'].map((e) => e);'
  $'再练：作答结果不过白名单\tsrc/practice/service.ts\tconst out = pickAnswer(r.body);\tconst out = r.body as Record<string, unknown> | null;'
  $'再练：结果未知不重试\tsrc/practice/service.ts\t    if (out.kind !== \'unknown\' || isDeterministic(out)) break;\t    break;'
  $'再练：PRACTICE_NOT_DUE 不映射\tsrc/practice/service.ts\t  if (f.status === 409 && code === \'PRACTICE_NOT_DUE\') return op === \'queue\' ? PRACTICE_UNAVAILABLE : NOT_DUE;\t'
  $'再练：410 不映射\tsrc/practice/service.ts\t  if (f.status === 410) return TICKET_EXPIRED;\t'
  $'再练：重放不标响应头\tsrc/practice/service.ts\treturn json(200, out, replayHeader(r.replayed));\treturn json(200, out, replayHeader(false));'
  # PLT-12 受限富文本题面（审查建议 16）：BFF 第三道白名单 src/item-rich.ts 与诊断、再练两条路径；这几条跑 tests/item-rich.test.ts
  $'题面：rich 顶层放过多余字段\tsrc/item-rich.ts\tif (!isRecord(v) || !only(v, [\'stem\', \'options\'])\tif (!isRecord(v)'
  $'题面：文字允许双向控制符与零宽字符\tsrc/item-rich.ts\t\\u007F-\\u009F\\u200B-\\u200F\\u2028\\u2029\\u202A-\\u202E\\u2066-\\u2069\\uFEFF]{1,2000}$/u;\t\\u007F]{1,2000}$/u;'
  $'题面：图片允许公网地址\tsrc/item-rich.ts\texport const IMAGE_REF_RE = /^objstore:\\/\\/\texport const IMAGE_REF_RE = /^(?:objstore|https):\\/\\/'
  $'题面：公式不核对认得的命令\tsrc/item-rich.ts\t\\n      && latexCommandsKnown(v[\'latex\'])) {\t) {'
  $'题面：不限总字数\tsrc/item-rich.ts\treturn totalChars(stem, options) <= MAX_TOTAL_CHARS ? { stem, options } : null;\treturn { stem, options };'
  $'题面：诊断不过白名单直接转 rich\tsrc/diagnosis/service.ts\t: pickRich(v.step.item.rich, (v.step.item.options ?? []).map((o) => o.key));\t: (v.step.item.rich as never);'
  $'题面：再练 rich 不合格整道题不出\tsrc/practice/service.ts\t    rich = pickRich(item[\'rich\'], (opts ?? []).map((o) => o.key)) ?? undefined;\t    rich = pickRich(item[\'rich\'], (opts ?? []).map((o) => o.key)) ?? undefined;\\n    if (rich === undefined) return null;'
  $'题面：再练选项键只认大写字母\tsrc/practice/service.ts\tconst CHOICE_KEY = /^[A-Za-z0-9]{1,8}$/;\tconst CHOICE_KEY = /^[A-Z]$/;'
  # 复测与验证题（specs/retest-delivery-kge05）：只认学生本人、开关、计次（复用题库自练、一张票据最多扣一次、本地可判的拒绝不计次）、
  # 票据与再练不通用、宵禁、响应白名单、失败映射；这几条跑 tests/retest-units.test.ts、tests/retest.integration.test.ts、tests/shell-openapi.test.ts
  $'复测：作答不核对学生本人\tsrc/retest/service.ts\t  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;\\n  // 本地能判的拒绝\t  // 本地能判的拒绝'
  $'复测：列表不核对学生本人\tsrc/retest/service.ts\t  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;\\n  const r = await withRetry(deps, (ms) => deps.learning.queue(\t  const r = await withRetry(deps, (ms) => deps.learning.queue('
  $'复测：取题不核对本人\tsrc/retest/service.ts\t  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;\\n  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：取题\t  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：取题'
  $'复测：开关关闭也注册路由\tsrc/http/routes.ts\t...(retest ? RETEST_ROUTES : [])\t...RETEST_ROUTES'
  $'复测：家长会话可读列表\tsrc/http/retest-routes.ts\t{ method: \'GET\', path: S, auth: \'student\'\t{ method: \'GET\', path: S, auth: \'session\''
  $'复测：不限流\tsrc/http/retest-routes.ts\tconst RL = [{ bucket: \'practice.session.minute\', dim: \'session\' }] as const;\tconst RL = [] as const;'
  $'复测：取题不挡宵禁\tsrc/retest/service.ts\t  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：取题\\n  if (blocked) return blocked;\\n\t'
  $'复测：作答不挡宵禁\tsrc/retest/service.ts\t  if (blocked) {\\n    if (!counted) return blocked;\t  if (false) {\\n    if (!counted) return blocked;'
  $'复测：宵禁中没计次的票据也去找回\tsrc/retest/service.ts\t    if (!counted) return blocked;\\n\t'
  $'复测：不预检就计次\tsrc/retest/service.ts\t  if (!counted) {\\n    // 计次之前的只读预检\t  if (false) {\\n    // 计次之前的只读预检'
  $'复测：预检不通过也计次\tsrc/retest/service.ts\t    if (pre.kind === \'failure\') {\t    if (false) {'
  $'复测：取题路由改成家长\tsrc/http/retest-routes.ts\t{ method: \'POST\', path: `${S}/entries/{entry_id}/next`, auth: \'student\'\t{ method: \'POST\', path: `${S}/entries/{entry_id}/next`, auth: \'guardian\''
  $'复测：作答路由改成家长\tsrc/http/retest-routes.ts\t{ method: \'POST\', path: `${S}/answer`, auth: \'student\'\t{ method: \'POST\', path: `${S}/answer`, auth: \'guardian\''
  $'复测：作答结果不核对票据条目\tsrc/retest/service.ts\t  if (!out || (out[\'entry\'] as { entry_id: string }).entry_id !== t.entryId) return UPSTREAM_UNAVAILABLE;\t  if (!out) return UPSTREAM_UNAVAILABLE;'
  $'复测：一张票据可扣多次（用随机的键）\tsrc/retest/service.ts\tconst key = t.idempotencyKey;\tconst key = crypto.randomUUID();'
  $'复测：作答不计次\tsrc/retest/service.ts\tconst m = await countPractice(deps.pool, account, student, key);\tconst m = { outcome: \'allowed\', usage_id: usage } as const;'
  $'复测：usage_id 不由幂等键派生\tsrc/retest/service.ts\tconst usage = usageIdFor(key);\tconst usage = key;'
  $'复测：额度拒绝不回 402\tsrc/retest/service.ts\t    case \'no_entitlement\': case \'daily_limit_reached\':\\n      return quota(m.outcome);\t    case \'no_entitlement\': case \'daily_limit_reached\':'
  $'复测：票据与再练同一附加数据\tsrc/practice/ticket.ts\t`starbuddy-retest-ticket:v1:${student}`\t`starbuddy-practice-ticket:v1:${student}`'
  $'复测：换学科的票据也收\tsrc/retest/service.ts\tif (!t || t.subject !== subject) return TICKET_INVALID;\tif (!t) return TICKET_INVALID;'
  $'复测：作答形状不在计次前核对\tsrc/retest/service.ts\t  if (!answerFits(t.format, t.optionKeys, input.answer)) return INVALID_ANSWER;\t'
  $'复测：本地不判过期\tsrc/retest/service.ts\tif (deps.now() >= Date.parse(t.expiresAt) - EXPIRY_MARGIN_MS && \tif (false && '
  $'复测：作答请求体收任何前缀的票据\tsrc/retest/service.ts\t!ticket.startsWith(RETEST_SEALED_PREFIX)\t!ticket.startsWith(\'sb\')'
  $'复测：作答请求体放过未知键\tsrc/retest/service.ts\tif (!exactKeys(b, [\'ticket\', \'answer\'])) return null;\tif (![\'ticket\', \'answer\'].every((k) => k in b)) return null;'
  $'复测：取题下发学习服务票据\tsrc/retest/service.ts\titem: ready.item, ticket: sealed,\titem: ready.item, ticket: ready.ticket,'
  $'复测：条目不过白名单\tsrc/retest/service.ts\tconst entries = body[\'entries\'].map(pickRetestEntry);\tconst entries = body[\'entries\'].map((e) => e);'
  $'复测：作答结果不过白名单\tsrc/retest/service.ts\tconst out = pickRetestAnswer(r.body);\tconst out = r.body as Record<string, unknown> | null;'
  $'复测：不可用转出原因编码\tsrc/retest/service.ts\treturn json(200, { status: \'unavailable\' });\treturn json(200, body);'
  $'复测：RETEST_NOT_DUE 不映射\tsrc/retest/service.ts\t  if (f.status === 409 && code === \'RETEST_NOT_DUE\') return op === \'queue\' ? RETEST_UNAVAILABLE : NOT_DUE;\t'
  $'复测：同票据换作答不回 TICKET_USED\tsrc/retest/service.ts\t(code === \'TICKET_USED\' || code === \'IDEMPOTENCY_CONFLICT\')\t(code === \'TICKET_USED\')'
  $'复测：重放不标响应头\tsrc/retest/service.ts\treturn json(200, out, replayHeader(r.replayed));\treturn json(200, out, replayHeader(false));'
  $'复测：确定性拒绝当可重试\tsrc/retest/service.ts\t(isDeterministic(out) ? RETEST_UNAVAILABLE : UPSTREAM_UNAVAILABLE)\tUPSTREAM_UNAVAILABLE'
  # 今日计划跨科编排（PLT-11，specs/today-plan-orchestration-plt11）：字段白名单、只给学生本人、开关关时不调 /today、每页只排一次计划、
  # partial 不算未启用；这几条跑 tests/today-orch-units.test.ts、tests/today-orch.integration.test.ts
  $'编排：去掉字段白名单\tsrc/shell/today-orch.ts\titems.push({ subject: x[\'subject\'] as PlanSubject,\titems.push(x as unknown as OrderedItem); if (false) items.push({ subject: x[\'subject\'] as PlanSubject,'
  $'编排：家长会话也带跨科列表\tsrc/http/shell-routes.ts\tconst orchOn = deps.shell?.orch?.enabled === true && audience === \'student\';\tconst orchOn = deps.shell?.orch?.enabled === true;'
  $'编排：开关关时仍调 /today\tsrc/http/shell-routes.ts\tconst orchClient = orchOn ? deps.shell?.orch?.client : undefined;\tconst orchClient = deps.shell?.orch?.client;'
  $'编排：开关开时仍另调 /plan\tsrc/http/shell-routes.ts\tif (asked.length > 0 && orchClient) {\tif (false) {'
  $'编排：未启用的来源也算 partial\tsrc/shell/today-orch.ts\treturn st !== \'ok\' && st !== \'source_disabled\';\treturn st !== \'ok\';'
  # 家长诉求（PLT-14 第 1 步，specs/family-goals-monthly-plan）：学生会话在请求体、CSRF、限流之前 404；补充说明的禁用推断词表过滤；
  # 开关关时不注册；年级不在 7–9 与写入上限的映射。这几条跑 tests/family-goals-units.test.ts、tests/family-goals.integration.test.ts、
  # tests/shell-openapi.test.ts（BFFMUT_FILTER=诉求：）
  $'诉求：去掉学生 404 守卫\tsrc/http/server.ts\tif (guardianOnly404 && session!.kind !== \'guardian\') throw new Stop(P.NOT_FOUND);\t'
  $'诉求：关掉词表过滤\tsrc/family/goals.ts\tif (note !== null && forbiddenHits(note, loadLexicon()).length > 0) return { kind: \'forbidden_text\' };\t'
  $'诉求：不管开关一律注册\tsrc/http/routes.ts\t...(familyGoals ? FAMILY_GOALS_ROUTES : [])\t...FAMILY_GOALS_ROUTES'
  $'诉求：去掉年级专用问题码\tsrc/http/family-goals-routes.ts\tproblem(P.GOAL_GRADE_NOT_SUPPORTED);\tproblem(P.GRADE_OUT_OF_RANGE);'
  $'诉求：去掉写入上限映射\tsrc/family/goals.ts\tif (code === \'54000\') return { kind: \'too_many\' };\t'
  # 月度计划（PLT-14 第 3 步，specs/family-goals-monthly-plan/step3-spec.md）：家长端只给家长、先确认有效绑定再调生成、同月只生成一次、
  # 没有权益的统一回答、学生端只给本人、模板版本核对、两张词表、对外白名单、分差区间形状、开关关时不注册、生成客户端的错误映射。
  # 这几条跑 tests/monthly-plan-units.test.ts、tests/monthly-plan.integration.test.ts、tests/shell-openapi.test.ts（BFFMUT_FILTER=月度计划：）
  $'月度计划：家长端不标 guardianOnly404\tsrc/http/monthly-plan-routes.ts\tauth: \'session\', guardianOnly404: true,\tauth: \'session\','
  $'月度计划：不确认有效绑定就读视图、调生成\tsrc/http/monthly-plan-routes.ts\tif (!a || a.linked !== true || a.live !== true) return null;\tif (!a) return null;'
  $'月度计划：已有快照仍调生成\tsrc/http/monthly-plan-routes.ts\tif (first.view === null) {\tif (true) {'
  $'月度计划：没有权益当成不可用\tsrc/http/monthly-plan-routes.ts\tif (out === \'not_available\') entitled = false;\tif (false) entitled = false;'
  $'月度计划：不核对模板版本\tsrc/http/monthly-plan-routes.ts\t || v[\'template_version\'] !== rc.lib.version) {\t) {'
  $'月度计划：学生端不核对本人\tsrc/http/monthly-plan-routes.ts\tif (!a || a.self !== true) return null;\tif (!a) return null;'
  $'月度计划：成品句子不过名次概率承诺词表\tsrc/family/monthly-plan.ts\tforbiddenHits(text, forbidden).length === 0 && forbiddenHits(text, noPromise).length === 0;\tforbiddenHits(text, forbidden).length === 0;'
  $'月度计划：不核对对外白名单\tsrc/family/monthly-plan.ts\t || !ctx.external.has(templateRef(e[\'template_key\'], e[\'template_version\']))) {\t) {'
  $'月度计划：分差区间收多余字段\tsrc/family/monthly-plan.ts\tv[\'reached\'] === false && Object.keys(v).length === 3 && \tv[\'reached\'] === false && '
  $'月度计划：不管开关一律注册\tsrc/http/routes.ts\t...(monthlyPlan ? MONTHLY_PLAN_ROUTES : [])\t...MONTHLY_PLAN_ROUTES'
  $'月度计划：别的 404 也当没有权益\tsrc/family/monthly-plan.ts\tbody?.[\'code\'] === \'CONTENT_VERSION_NOT_FOUND\' ? \'not_available\' : \'unavailable\'\t\'not_available\''
  # 审查后补（PLT-14 第 3 步审查意见 4）：判断句 copy_key、本月按上海时区、注意事项只用 guardian 段模板、学生端完整时间戳、生成中途解绑 404；
  # 生成桶、负缓存、钉死文件、词表命中计数
  $'月度计划：判断句不核对 copy_key\tsrc/family/monthly-plan.ts\t      || e[\'copy_key\'] !== copyKey || !ctx.external.has(\t      || !ctx.external.has('
  $'月度计划：本月不按上海时区\tsrc/http/monthly-plan-routes.ts\t${ts} AT TIME ZONE \'Asia/Shanghai\'\t${ts} AT TIME ZONE \'UTC\''
  $'月度计划：注意事项可借别段模板\tsrc/family/monthly-plan.ts\tn[\'copy_key\'] !== `monthly_plan.guardian.${n[\'key\']}`\ttypeof n[\'copy_key\'] !== \'string\''
  $'月度计划：学生端时间戳只看前缀\tsrc/family/monthly-plan.ts\t!STAMP.test(raw[\'generated_at\'])) return null;\\n  const student_plan = renderStudentPlan(raw[\'student_plan\'], ctx, t);\\n  return\t!/^\\d{4}-\\d{2}-\\d{2}T/.test(raw[\'generated_at\'])) return null;\\n  const student_plan = renderStudentPlan(raw[\'student_plan\'], ctx, t);\\n  return'
  $'月度计划：生成中途解绑不回 404\tsrc/http/monthly-plan-routes.ts\tif (!again) return problem(P.NOT_FOUND);\tif (!again) return UNAVAILABLE;'
  $'月度计划：不计生成桶\tsrc/http/monthly-plan-routes.ts\tif (!(await generateAllowed(deps, account))) {\tif (false) {'
  $'月度计划：没有负缓存\tsrc/http/monthly-plan-routes.ts\tif (until !== undefined && until > nowMs(deps)) {\tif (false) {'
  $'月度计划：不核对钉死文件\tsrc/family/monthly-plan.ts\tneed(l[\'template_version\'] === lib.version && l[\'sha256\'] === lib.fingerprint, \'模板库与钉死文件不一致\');\t'
  $'月度计划：签字文案变量不核对\tsrc/family/monthly-plan.ts\t      need(slots.size === t.vars!.length\t      need(true || slots.size === t.vars!.length'
  $'月度计划：词表命中不另计\tsrc/family/monthly-plan.ts\t    t.lexicon_hits++;\t'
)
# 副本保持仓库结构（测试按 ../../../ 找仓库根：adapters/aya 的登记脚本、db/seeds 的种子），adapters、db 软链接回仓库。
# 安全审查阻断 1：原来的副本不保持结构，测试的 before 在任何代码下都失败，变异全部“被发现”却不说明任何问题。
# 所以：先跑一次不变异的对照，必须通过；每条变异限时（挂起算漏检）；只有“至少一项测试通过、另有失败”才算被发现，
# 保证失败来自断言而不是环境没起来。
# H6 变异的预期（安全审查 M2，参照 db/tests/mutations.sh 的 EXPECT）：第一条失败的测试名与断言消息必须是这里登记的，
# 证明是指定的断言发现了变异，而不是别的测试先失败。值 = “测试名片段|断言消息片段”。
declare -A EXPECT_H6=(
  ["H6：新路由不登记样例"]="H6 路由表覆盖|新增路由必须在 SAMPLES 里登记"
  ["H6：学生会话可改时长设置"]="H6 路由表覆盖|与接口定义的 x-sb-auth"
  ["H6：选科越权回 403"]="H6 授权矩阵|不存在 POST /students/{student_id}/enrollments/{subject}"
  ["H6：方法不对回 404"]="H6 表外|"
  ["H6：身份改从查询参数读"]="H6 身份注入|查询参数 + 请求头注入"
  ["H6：路径参数可被查询参数覆盖"]="H6 身份注入|"
  ["H6：set_config 第三个参数改为 false"]="H6 连接池串号|成功的请求之后：连接上没有残留账号"
  ["H6：退出所有设备不废会话"]="H6 会话：登出后重放|退出所有设备"
  ["H6：登出不废会话"]="H6 会话：登出后重放|登出后重放"
  ["H6：导出不挂限流桶"]="H6 路由表覆盖|挂了限流桶与登记豁免必须恰好其一"
  ["H6：按账号限流的键每次不同"]="H6 限流|"
  ["H6：限流按原因返回不同错误"]="H6 限流|POST /auth/otp/request otp_request.phone.minute："
  ["H6：响应可以缓存"]="H6 授权矩阵|no-store"
  ["H6：响应带 CORS 放行头"]="H6 授权矩阵|没有 CORS 头"
  ["H6：诊断开始收前端带来的字段"]="H6 计量入口（静态）|诊断开始拒收"
  ["H6：诊断失败不释放预留"]="H6 计量入口（集成）|学习服务确定失败时预留被释放"
  ["H6：导出兑换不核对学生"]="H6 跨对象|跨对象（兄妹 S2 的二级 ID）POST /students/{student_id}/exports/{export_job_id}/download"
  ["H6：事件与探针的“不是本人”改回 404"]="H6 授权矩阵|x-sb-not-self 403"
  ["H6：考试成绩不核对考试属于这个学生"]="H6 跨对象|跨对象（另一户 T 的二级 ID）POST /students/{student_id}/exams/{exam_id}/score"
)
h6_first() {  # $1 测试输出文件，$2 变异名：第一条 not ok 的测试名与它的错误块要含登记的片段
  local want="${EXPECT_H6[$2]:-}" first block
  if [ -z "$want" ]; then echo "  H6 变异没有登记预期断言：$2"; return 1; fi
  first="$(grep -m1 '^not ok' "$1" || true)"
  block="$(awk 'f && /^(ok |not ok |# Subtest)/ {exit} /^not ok/ {f=1} f' "$1")"
  if [[ "$first" != *"${want%%|*}"* ]] || [[ "$block" != *"${want#*|}"* ]]; then
    echo "  第一条失败不是登记的断言：$2（期望 ${want}；实际 ${first:0:80} / $(printf '%s' "$block" | grep -m1 'error:' | cut -c1-120)）"
    return 1
  fi
}
star_run() {  # $1 副本根目录，$2 变异名（“计量：”开头的跑计量测试）；输出 rc 与通过数
  local out rc pass tests="tests/star-map.test.ts tests/star-map.integration.test.ts tests/onboarding.integration.test.ts tests/events.integration.test.ts"
  [[ "${2:-}" == 计量：* ]] && tests="tests/learning-client.test.ts tests/gate0.integration.test.ts"
  [[ "${2:-}" == 诊断：* ]] && tests="tests/diagnosis.integration.test.ts"
  [[ "${2:-}" == 认证：* ]] && tests="tests/auth.integration.test.ts"
  [[ "${2:-}" == 页面：* ]] && tests="tests/shell-pages.integration.test.ts"
  [[ "${2:-}" == 信任：* ]] && tests="tests/trust-units.test.ts tests/trust.integration.test.ts"
  [[ "${2:-}" == H6：* ]] && tests="tests/route-matrix.integration.test.ts"
  [[ "${2:-}" == 星图v1：* ]] && tests="tests/star-map-v1.test.ts tests/star-map-v1.integration.test.ts"
  [[ "${2:-}" == 语音：* ]] && tests="tests/voice-minutes.test.ts tests/voice-minutes.integration.test.ts"
  [[ "${2:-}" == 报告：* ]] && tests="tests/report-units.test.ts tests/report.integration.test.ts"
  [[ "${2:-}" == 再练：* ]] && tests="tests/practice-units.test.ts tests/practice.integration.test.ts tests/shell-openapi.test.ts"
  [[ "${2:-}" == 复测：* ]] && tests="tests/retest-units.test.ts tests/retest.integration.test.ts tests/shell-openapi.test.ts"
  [[ "${2:-}" == 届别：* ]] && tests="tests/cohort.integration.test.ts"
  [[ "${2:-}" == 题面：* ]] && tests="tests/item-rich.test.ts"
  [[ "${2:-}" == 编排：* ]] && tests="tests/today-orch-units.test.ts tests/today-orch.integration.test.ts"
  [[ "${2:-}" == 诉求：* ]] && tests="tests/family-goals-units.test.ts tests/family-goals.integration.test.ts tests/shell-openapi.test.ts"
  [[ "${2:-}" == 月度计划：* ]] && tests="tests/monthly-plan-units.test.ts tests/monthly-plan.integration.test.ts tests/shell-openapi.test.ts"
  out=$( cd "$1/services/bff" && timeout 240 bash "$ROOT/db/tests/pg-ephemeral.sh" -- \
      node --no-warnings --test --test-concurrency=1 --test-timeout=60000 $tests 2>&1 ); rc=$?
  printf '%s\n' "$out" > "$1/run.out"  # H6 的“第一条失败必须是登记的断言”从这里读
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
  # 禁用推断词表（src/trust/lexicon.ts 按 ../../../../docs/design/sie/ 读）：没有它时带文字的路由 fail closed（503），
  # 家长诉求的词表变异会因此“被发现”却不说明任何问题，所以“诉求：”一组的副本带上 docs。
  # 其余组暂不带：带上之后“信任：周报收已撤回的判断”“报告：不核对受众”两条漏检——它们现在被“发现”只因为副本里没有词表（PLT-14 报告，待人处理）
  [[ "${1:-}" == 诉求：* ]] && ln -s "$ROOT/docs" "$work/docs"
  # 月度计划（PLT-14 第 3 步）：两张词表在 docs/design/sie/，模板库与洞察服务同一份文件（services/insight/templates/）；只读用，不改
  if [[ "${1:-}" == 月度计划：* ]]; then ln -s "$ROOT/docs" "$work/docs"; ln -s "$ROOT/services/insight" "$work/services/insight"; fi
  ln -s "$ROOT/services/learning" "$work/services/learning"  # 诊断集成测试起学习服务进程（只读用，不改）
  echo "$work"
}
work="$(star_copy)"; read -r rc pass <<< "$(star_run "$work")"; rm -rf "$work"
if [ "$rc" = "3" ]; then echo "bff-mutation: 跳过（本机没有 Postgres）"; exit 3; fi
if [ "$rc" != "0" ]; then echo "bff-mutation: 失败（星图变异的对照组没有通过，rc=$rc）"; exit 1; fi
for spec in "${SM[@]}"; do
  IFS=$'\t' read -r name file from to <<< "$spec"
  if [ -n "$FILTER" ] && [[ "$name" != *"$FILTER"* ]]; then continue; fi
  total=$((total + 1))
  work="$(star_copy "$name")"
  unique=0; [[ "$name" == H6：* ]] && unique=1
  if ! MUT_UNIQUE=$unique MUT_FILE="$work/services/bff/$file" MUT_FROM="$from" MUT_TO="$to" python3 - <<'PYM'
import os, sys
p, a, b = os.environ['MUT_FILE'], os.environ['MUT_FROM'], os.environ['MUT_TO']
a, b = a.replace('\\n', '\n'), b.replace('\\n', '\n')  # 原文与替换里的 \n 表示换行（多行锚点）
s = open(p, encoding='utf-8').read()
if s.count(a) != 1 and os.environ.get('MUT_UNIQUE') == '1': sys.exit(1)  # H6：锚点必须唯一
if a not in s: sys.exit(1)
open(p, 'w', encoding='utf-8').write(s.replace(a, b, 1))
PYM
  then echo "  变异本身写错：$name"; missed=$((missed + 1)); rm -rf "$work"; continue; fi
  read -r rc pass <<< "$(star_run "$work" "$name")"
  first_ok=1; [[ "$name" == H6：* ]] && [ "$rc" != "0" ] && { h6_first "$work/run.out" "$name" || first_ok=0; }
  rm -rf "$work"
  if [ "$rc" = "0" ]; then echo "  漏检：$name"; missed=$((missed + 1))
  elif [ "$rc" = "124" ]; then echo "  超时（测试挂起，不算发现）：$name"; missed=$((missed + 1))
  elif [ "${pass:-0}" -lt 1 ]; then echo "  测试没有跑起来（不算发现）：$name"; missed=$((missed + 1))
  elif [ $first_ok -eq 0 ]; then missed=$((missed + 1))
  fi
done

# H6 的库函数变异（安全审查 M1：跨对象）：改测试库里的函数，跑 tests/route-matrix.integration.test.ts，同样要求第一条失败是登记的断言
declare -A HM=(
  ["H6：考试成绩不核对考试属于这个学生"]="$(fr 'insight.submit_exam_score(uuid, uuid, numeric)' "'WHERE e.id = p_plan AND e.student_id = p_student AND'" "'WHERE e.id = p_plan AND'")"
)
for name in "${!HM[@]}"; do
  if [ -n "$FILTER" ] && [[ "$name" != *"$FILTER"* ]]; then continue; fi
  total=$((total + 1))
  out=$(PGEPHEMERAL_EXTRA_SQL="${HM[$name]}" timeout 240 bash "$ROOT/db/tests/pg-ephemeral.sh" -- \
        node --no-warnings --test --test-concurrency=1 --test-timeout=60000 tests/route-matrix.integration.test.ts 2>&1); rc=$?
  tmp="$(mktemp "${TMPDIR:-/tmp}/starbuddy-h6mut.XXXXXX")"; printf '%s\n' "$out" > "$tmp"
  if echo "$out" | grep -q '附加 SQL 执行失败'; then echo "  变异本身写错：$name"; missed=$((missed + 1))
  elif [ "$rc" = "0" ]; then echo "  漏检：$name"; missed=$((missed + 1))
  elif ! h6_first "$tmp" "$name"; then missed=$((missed + 1))
  fi
  rm -f "$tmp"
done
if [ $missed -gt 0 ]; then echo "bff-mutation: 失败，$missed/$total 个变异没有被发现"; exit 1; fi
echo "bff-mutation: 通过（$total 个变异全部被发现）"
