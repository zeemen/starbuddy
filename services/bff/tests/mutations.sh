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
  work="$(star_copy)"
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
