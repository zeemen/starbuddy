#!/usr/bin/env bash
# 前端端到端测试的环境（spec 验收）：一次性 Postgres（db/tests/pg-ephemeral.sh）→ 阿鸦内容登记 → 星图合成种子 →
# BFF（只监听 127.0.0.1；身份只来自真实会话，端到端经短信捕获桩的收件箱读验证码登录）→ 测试构建（含开发专用页面，输出到单独的 .next-e2e，不碰正式的 .next）
# → Playwright（webServer 起 next start，同样读 .next-e2e）。
# 全部合成数据，不连共享库。
# 用法：bash apps/web/scripts/e2e.sh [playwright 参数…]        例如 --grep-invert @visual、--grep @visual
# 退出码：Playwright 的退出码；3 表示本机没有 Postgres，或缺少 services/learning/.venv、services/insight/.venv
# （两个 venv 在起任何进程之前就核对，缺了不起一次性 Postgres、不起任何服务）。
set -uo pipefail
WEB="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$WEB/../.." && pwd)"
# 学习服务与审核台后端（同 make learning-test）、洞察服务（月度计划生成与单条解释，同 make insight-test）的 venv
PY="$ROOT/services/learning/.venv/bin/python"
IPY="$ROOT/services/insight/.venv/bin/python"
if [ ! -x "$PY" ]; then echo "e2e: 缺少 services/learning/.venv（学习服务、审核台后端）"; exit 3; fi
if [ ! -x "$IPY" ]; then echo "e2e: 缺少 services/insight/.venv（洞察服务：月度计划生成与单条解释）"; exit 3; fi

if [ "${1:-}" != "--inner" ]; then
  exec bash "$ROOT/db/tests/pg-ephemeral.sh" -- bash "$0" --inner "$@"
fi
shift
set -e
# 下面在 pg-ephemeral 里执行：PGHOST、PGDATABASE、PGUSER 已指向一次性实例
psql -X -q -v ON_ERROR_STOP=1 -c "CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff" >/dev/null
# 计量时钟（只在这个一次性测试库里，做法同 services/bff/tests/harness.ts）：把北京时间挪到当天白天，
# 否则夜里跑端到端会撞上 22:00–06:00 的宵禁（数学诊断开始时要预留额度，SIE-02a）。时间照常往前走。
# 偏移量在建环境时算一次、写成常量（计量时钟 = now() + 偏移，从当天北京时间 10:00 起单调连续走，12 小时内不进宵禁）。
# 原来按“当天 10 点 + 当前小时内的分秒”算，每逢整点倒退约 59 分钟：种子在整点前开通的订阅与额度（period_start = 计量时钟）
# 在整点后“还没开始”，诊断开始预留得到 no_entitlement → 402 → 演示页“现在还不能开始”（specs/diagnosis-e2e-flake）。
psql -X -q -v ON_ERROR_STOP=1 -c "DO \$do\$ BEGIN EXECUTE format(\$f\$CREATE OR REPLACE FUNCTION platform.meter_now() RETURNS timestamptz
  LANGUAGE sql STABLE SET search_path = pg_catalog AS \$b\$ SELECT now() + %L::interval \$b\$\$f\$,
  ((date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') + interval '10 hours') AT TIME ZONE 'Asia/Shanghai') - now()); END \$do\$" >/dev/null
node --no-warnings "$ROOT/adapters/aya/scripts/register-content.ts" | psql -X -q -v ON_ERROR_STOP=1 >/dev/null
# 4-G 迁移 0039 起 BFF 只看已发布内容版本的能力视图：在这个一次性测试库里把登记的阿鸦内容版本以合成 staff 经签字函数发布
# （种子不发布真实登记的内容；发布函数见 db/seeds/dev/syn-publish-content.sql）
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/syn-publish-content.sql" \
  -c "SELECT pg_temp.syn_publish_content(v.id) FROM platform.curriculum_version v
       WHERE v.status = 'draft' AND v.id IN (SELECT n.curriculum_version_id FROM platform.skill_node n WHERE n.subject = 'aya')" >/dev/null
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/syn-publish-content.sql" \
  -f "$ROOT/db/seeds/dev/star-map-synthetic.sql" >/dev/null
# 数学诊断演示（SIE-02a）：九年级合成学生 D1 与家长 gD，试点会员（完整诊断额度）
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/diagnosis-synthetic.sql" >/dev/null
# 家庭登录与学生模式（4-H H1）：gS 的合成号码 +99900000101、S1 的合成同意
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/auth-synthetic.sql" >/dev/null
# 统一壳 H4（今日、科目、额度与时长、选科、时长设置）：家庭 HG（家长 gH +99900000106；孩子试用、留存、试点各一名）。
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/syn-publish-content.sql" \
  -f "$ROOT/db/seeds/dev/shell-synthetic.sql" >/dev/null
# 跨科周报 v0（冲刺 5-H）：GT 本周的学生版成长手记与家长版周报（引用两条合成判断，模板 syn.weekly.node_mastery@1）
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/weekly-synthetic.sql" >/dev/null
AYA_COUNT="$(psql -X -At -c "SELECT count(*) FROM platform.skill_node WHERE subject = 'aya'")"
export AYA_COUNT

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
BFF_PORT="$(free_port)"
WEB_PORT="$(free_port)"
LOG="$(mktemp)"
# 短信捕获桩的收件箱（4-H H1）：BFF 把捕获的验证码追加到这个文件，端到端测试从这里读码登录；只有本用户可读，结束时删除
SMS_INBOX="$(mktemp)"
chmod 600 "$SMS_INBOX"
export SB_SMS_INBOX="$SMS_INBOX"
# 数学诊断（SIE-02a）：阿鸦适配器（验证器）+ 学习服务（合成题面只在 LEARNING_ENV=test）。服务间凭据每次随机生成，不落仓库。
# 需要 services/learning/.venv（同 make learning-test）。
# 学习服务的 venv 已在文件开头（起任何进程之前）核对：$PY
psql -X -q -v ON_ERROR_STOP=1 -c "CREATE ROLE learning_app LOGIN IN ROLE starbuddy_learning" >/dev/null
AYA_PORT="$(free_port)"
LEARNING_PORT="$(free_port)"
AYA_CRED="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
SVC_CRED="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
LLOG="$(mktemp)"
# 服务间凭据只经环境变量传：在子 shell 里 export 再 exec，不出现在任何进程的命令行（argv）里（安全审查 S8）。
# 诊断作答走 learning-internal v1 写入路径：内部 v1 未签字，学习服务开 LEARNING_INTERNAL_V1=1（只在这个一次性测试环境）。
# 插件契约 1.0.0 已签字（specs/contract-v1.0-signoff）：学习服务与阿鸦适配器都不再需要候选版开关。
( cd "$ROOT/adapters/aya" || exit 1
  export AYA_ADAPTER_SERVICE_TOKEN="$AYA_CRED" PORT="$AYA_PORT"
  exec node --no-warnings src/main.ts ) > /dev/null 2>&1 < /dev/null &
AYA_PID=$!
( cd "$ROOT/services/learning" || exit 1
  # 同 env -i：先清掉子 shell 继承的全部导出变量，只导出学习服务要的几项
  keep_path="$PATH"
  db_url="postgresql://learning_app@/starbuddy_test?host=$PGHOST"
  while read -r v; do unset "$v" 2>/dev/null || true; done < <(compgen -e)
  export PATH="$keep_path" LEARNING_ENV=test PORT="$LEARNING_PORT" LEARNING_DIAGNOSIS_PRESENTER=synthetic \
    LEARNING_INTERNAL_V1=1 LEARNING_DATABASE_URL="$db_url" LEARNING_SERVICE_TOKEN="$SVC_CRED" \
    LEARNING_ADAPTER_AYA_URL="http://127.0.0.1:$AYA_PORT" LEARNING_ADAPTER_AYA_TOKEN="$AYA_CRED"
  exec "$PY" -m learning ) > "$LLOG" 2>&1 < /dev/null &
LEARNING_PID=$!
trap 'kill "$AYA_PID" "$LEARNING_PID" 2>/dev/null || true; rm -f "$LLOG" "$SMS_INBOX"' EXIT
for _ in $(seq 1 80); do
  python3 -c "import socket,sys; ok=all(socket.socket().connect_ex(('127.0.0.1', p)) == 0 for p in ($LEARNING_PORT, $AYA_PORT)); sys.exit(0 if ok else 1)" && break
  sleep 0.25
done
# BFF（4-H H2：只有统一壳路由表一个入口，身份只来自真实会话；诊断的宵禁判断用上面挪到白天的计量时钟，
# 经 platform.meter_guard_status，BFF 没有自己的时钟开关）
# 限流阈值放宽的一份配置（端到端一次要让同一个合成家长登录很多次；每号每分钟 1 次的原值会让后面的用例 429）。
# 只在这个一次性环境、只在 STARBUDDY_ENV=test 时 BFF 才认（BFF_AUTH_CONFIG，services/bff/src/main.ts）；
# 限流本身由 services/bff/tests/auth.integration.test.ts 按原配置测。
AUTH_CFG="$(mktemp --suffix=.json)"
python3 - "$ROOT/services/bff/config/auth.json" "$AUTH_CFG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
for v in c['rate_limits'].values():
    v['limit'] = min(v['limit'] * 1000, 1000000)
json.dump(c, open(sys.argv[2], 'w'))
PY
( cd "$ROOT/services/bff" || exit 1
  unset STARBUDDY_STARMAP STARBUDDY_PRACTICE STARBUDDY_RETEST_UI STARBUDDY_TODAY_ORCH STARBUDDY_FAMILY_GOALS STARBUDDY_MONTHLY_PLAN  # 星图、错题再练、复测、跨科编排开关全关（v0）：不从调用方环境继承
  export BFF_DATABASE_URL="postgresql://bff_app@/starbuddy_test?host=$PGHOST" PORT="$BFF_PORT" BFF_AUTH_CONFIG="$AUTH_CFG" \
    BFF_LEARNING_URL="http://127.0.0.1:$LEARNING_PORT" BFF_LEARNING_TOKEN="$SVC_CRED" \
    STARBUDDY_ENV=test BFF_SHELL_ORIGIN="http://127.0.0.1:$WEB_PORT" BFF_SMS_CAPTURE_FILE="$SMS_INBOX"
  exec node --no-warnings src/main.ts ) > "$LOG" 2>&1 < /dev/null &
BFF_PID=$!
# 信任界面开关打开的第二套（冲刺 5-H，e2e/trust-on.spec.ts）：同一个一次性库上另起一个 BFF（STARBUDDY_TRUST_UI 打开 dispute / reconcile /
# weekly / data_rights，只在 STARBUDDY_ENV=test 生效）与一个前端（playwright.config.ts 的第二个 webServer，STARBUDDY_TRUST_UI 另含 why）。
# 原来这一套开关全关，4-H“信任界面预留”验收与 e2e/trust-off.spec.ts 在它上面跑。没有洞察服务：异议提交 503，预测与对账卡读取 503。
TRUST_BFF_PORT="$(free_port)"
WEB_TRUST_PORT="$(free_port)"
TLOG="$(mktemp)"
# 周报页要显示判断句：第二个 BFF 用一份把周报合成模板列进对外白名单的显示规则（BFF_SHELL_CONFIG 只在 STARBUDDY_ENV=test 认；画像可见范围
# 的正式白名单仍是 config/shell.json 的空表，必停项）
TRUST_SHELL_CFG="$(mktemp --suffix=.json)"
python3 - "$ROOT/services/bff/config/shell.json" "$TRUST_SHELL_CFG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
c['external_claim_templates'] = ['syn.weekly.node_mastery@1']
json.dump(c, open(sys.argv[2], 'w'))
PY
( cd "$ROOT/services/bff" || exit 1
  unset STARBUDDY_STARMAP STARBUDDY_PRACTICE STARBUDDY_RETEST_UI STARBUDDY_TODAY_ORCH STARBUDDY_FAMILY_GOALS STARBUDDY_MONTHLY_PLAN  # 星图、错题再练、复测、跨科编排开关全关（v0）：不从调用方环境继承
  export BFF_DATABASE_URL="postgresql://bff_app@/starbuddy_test?host=$PGHOST" PORT="$TRUST_BFF_PORT" BFF_AUTH_CONFIG="$AUTH_CFG" \
    BFF_LEARNING_URL="http://127.0.0.1:$LEARNING_PORT" BFF_LEARNING_TOKEN="$SVC_CRED" \
    STARBUDDY_ENV=test BFF_SHELL_ORIGIN="http://127.0.0.1:$WEB_TRUST_PORT" BFF_SMS_CAPTURE_FILE="$SMS_INBOX" \
    STARBUDDY_TRUST_UI=dispute,reconcile,weekly,data_rights BFF_SHELL_CONFIG="$TRUST_SHELL_CFG"
  exec node --no-warnings src/main.ts ) > "$TLOG" 2>&1 < /dev/null &
TRUST_BFF_PID=$!
trap 'kill "$BFF_PID" "$TRUST_BFF_PID" "$AYA_PID" "$LEARNING_PID" 2>/dev/null || true; rm -f "$LOG" "$TLOG" "$LLOG" "$SMS_INBOX" "$AUTH_CFG" "$TRUST_SHELL_CFG"' EXIT
# H5 星图 v1 开关打开的第三套（e2e/star-map-v1.spec.ts、e2e/visual-v1.spec.ts，playwright.config.ts 的 starmap-on 项目）：
#   - 第二个学习服务：同一个一次性库，另带合成数学掌握配置（config/mastery/synthetic-test.json，只在 LEARNING_ENV=test 认），
#     给星图接口 star-map/nodes 用；第一个学习服务不带掌握配置（诊断等端到端的行为不变）；
#   - 第三个 BFF：STARBUDDY_STARMAP=starmap_v1,starmap_dashed（只在 STARBUDDY_ENV=test 生效），显示规则把合成星图模板列进对外白名单与
#     “知识掌握”“知识迁移”两张表（BFF_SHELL_CONFIG 只在 test 认；正式的 config/shell.json 仍是空表，必停项）；没有洞察服务，星数说明不出现。
#   合成数据见 db/seeds/dev/star-map-v1-synthetic.sql（在审核台种子之后执行）。
SM_LEARNING_PORT="$(free_port)"
SM_BFF_PORT="$(free_port)"
WEB_STARMAP_PORT="$(free_port)"
SMLLOG="$(mktemp)"
SMLOG="$(mktemp)"
( cd "$ROOT/services/learning" || exit 1
  keep_path="$PATH"
  db_url="postgresql://learning_app@/starbuddy_test?host=$PGHOST"
  while read -r v; do unset "$v" 2>/dev/null || true; done < <(compgen -e)
  export PATH="$keep_path" LEARNING_ENV=test PORT="$SM_LEARNING_PORT" LEARNING_DIAGNOSIS_PRESENTER=synthetic \
    LEARNING_INTERNAL_V1=1 LEARNING_DATABASE_URL="$db_url" LEARNING_SERVICE_TOKEN="$SVC_CRED" \
    LEARNING_ADAPTER_AYA_URL="http://127.0.0.1:$AYA_PORT" LEARNING_ADAPTER_AYA_TOKEN="$AYA_CRED" \
    LEARNING_MASTERY_CONFIG_AYA=config/mastery/synthetic-test.json
  exec "$PY" -m learning ) > "$SMLLOG" 2>&1 < /dev/null &
SM_LEARNING_PID=$!
SM_SHELL_CFG="$(mktemp --suffix=.json)"
python3 - "$ROOT/services/bff/config/shell.json" "$SM_SHELL_CFG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
c['external_claim_templates'] = ['syn.starmap.node_mastery@1', 'syn.starmap.node_transfer@1']
c['star_map']['mastery_templates'] = ['syn.starmap.node_mastery@1']
c['star_map']['transfer_templates'] = ['syn.starmap.node_transfer@1']
json.dump(c, open(sys.argv[2], 'w'))
PY
( cd "$ROOT/services/bff" || exit 1
  unset STARBUDDY_PRACTICE STARBUDDY_RETEST_UI STARBUDDY_TODAY_ORCH STARBUDDY_FAMILY_GOALS STARBUDDY_MONTHLY_PLAN  # 错题再练、复测、跨科编排开关关：不从调用方环境继承
  export BFF_DATABASE_URL="postgresql://bff_app@/starbuddy_test?host=$PGHOST" PORT="$SM_BFF_PORT" BFF_AUTH_CONFIG="$AUTH_CFG" \
    BFF_LEARNING_URL="http://127.0.0.1:$SM_LEARNING_PORT" BFF_LEARNING_TOKEN="$SVC_CRED" \
    STARBUDDY_ENV=test BFF_SHELL_ORIGIN="http://127.0.0.1:$WEB_STARMAP_PORT" BFF_SMS_CAPTURE_FILE="$SMS_INBOX" \
    STARBUDDY_STARMAP=starmap_v1,starmap_dashed BFF_SHELL_CONFIG="$SM_SHELL_CFG"
  exec node --no-warnings src/main.ts ) > "$SMLOG" 2>&1 < /dev/null &
SM_BFF_PID=$!
trap 'kill "$BFF_PID" "$TRUST_BFF_PID" "$SM_BFF_PID" "$SM_LEARNING_PID" "$AYA_PID" "$LEARNING_PID" 2>/dev/null || true; rm -f "$LOG" "$TLOG" "$SMLOG" "$LLOG" "$SMLLOG" "$SMS_INBOX" "$AUTH_CFG" "$TRUST_SHELL_CFG" "$SM_SHELL_CFG"' EXIT
# SIE-02b 第二步 PR 2 首份报告开发演示页（e2e/first-report.spec.ts，playwright.config.ts 的 report-on 项目）：
#   - 合成家庭 HR 与 R1 的四条合成判断（db/seeds/dev/first-report-synthetic.sql）；
#   - 合成洞察服务桩（scripts/fake-insight.ts，只实现 insight.report / insight.explain，按契约信封返回 R1 的合成报告与解释；令牌每次随机生成，
#     只经环境变量传）。真实洞察服务的报告生成与 0044 授权再核对由 services/insight 的测试覆盖（lessons 待人确认：端到端接真实洞察服务）；
#   - 第四个 BFF：STARBUDDY_TRUST_UI=report,why（只在 STARBUDDY_ENV=test 生效），BFF_INSIGHT_URL 指向桩，显示规则把两个合成报告模板列进
#     对外白名单（BFF_SHELL_CONFIG 只在 test 认；正式的 config/shell.json 仍是空表，必停项）。
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/first-report-synthetic.sql" >/dev/null
FI_PORT="$(free_port)"
RP_BFF_PORT="$(free_port)"
WEB_REPORT_PORT="$(free_port)"
FI_CRED="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
FILOG="$(mktemp)"
RPLOG="$(mktemp)"
( export FAKE_INSIGHT_PORT="$FI_PORT" FAKE_INSIGHT_TOKEN="$FI_CRED"
  exec node --no-warnings "$WEB/scripts/fake-insight.ts" ) > "$FILOG" 2>&1 < /dev/null &
FI_PID=$!
RP_SHELL_CFG="$(mktemp --suffix=.json)"
python3 - "$ROOT/services/bff/config/shell.json" "$RP_SHELL_CFG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
c['external_claim_templates'] = ['syn.report.node_mastery@1', 'syn.report.node_transfer@1']
json.dump(c, open(sys.argv[2], 'w'))
PY
( cd "$ROOT/services/bff" || exit 1
  unset STARBUDDY_STARMAP STARBUDDY_PRACTICE STARBUDDY_RETEST_UI STARBUDDY_TODAY_ORCH STARBUDDY_FAMILY_GOALS STARBUDDY_MONTHLY_PLAN  # 星图、错题再练、复测、跨科编排开关全关（v0）：不从调用方环境继承
  export BFF_DATABASE_URL="postgresql://bff_app@/starbuddy_test?host=$PGHOST" PORT="$RP_BFF_PORT" BFF_AUTH_CONFIG="$AUTH_CFG" \
    STARBUDDY_ENV=test BFF_SHELL_ORIGIN="http://127.0.0.1:$WEB_REPORT_PORT" BFF_SMS_CAPTURE_FILE="$SMS_INBOX" \
    STARBUDDY_TRUST_UI=report,why BFF_SHELL_CONFIG="$RP_SHELL_CFG" BFF_INSIGHT_URL="http://127.0.0.1:$FI_PORT" BFF_INSIGHT_TOKEN="$FI_CRED"
  exec node --no-warnings src/main.ts ) > "$RPLOG" 2>&1 < /dev/null &
RP_BFF_PID=$!
trap 'kill "$BFF_PID" "$TRUST_BFF_PID" "$SM_BFF_PID" "$SM_LEARNING_PID" "$RP_BFF_PID" "$FI_PID" "$AYA_PID" "$LEARNING_PID" 2>/dev/null || true; rm -f "$LOG" "$TLOG" "$SMLOG" "$RPLOG" "$FILOG" "$LLOG" "$SMLLOG" "$SMS_INBOX" "$AUTH_CFG" "$TRUST_SHELL_CFG" "$SM_SHELL_CFG" "$RP_SHELL_CFG"' EXIT
# 错题再练学生端页面（specs/practice-loop-stage1 PR 4；e2e/practice.spec.ts，playwright.config.ts 的 practice-on 项目）：
#   - 合成家庭 HP 与 P1、P2（宵禁）、P3（没有权益）（db/seeds/dev/practice-synthetic.sql，在统一壳种子之后执行）；
#   - 合成学习服务桩（scripts/fake-learning-practice.ts，只监听 127.0.0.1，只实现 practice 的三个内部接口，按固定剧本返回合成条目、题面与判分；
#     令牌每次随机生成，只经环境变量传）。真实学习服务的错题判定、选题、验票与 grade 落库由 services/learning 的测试覆盖；
#   - 第五个 BFF：STARBUDDY_PRACTICE=practice（只在 STARBUDDY_ENV=test 生效），BFF_LEARNING_URL 指向桩（今日计划、诊断等其余学习服务调用都得到 501，
#     按“暂无安排”处理）；作答照常经 platform.meter_practice 在这个一次性库里计次。另四套 BFF 不开 practice（开关关时三条路由 404，
#     e2e/practice-off.spec.ts 在 trust-off 项目里核对，并按桩的请求记录核对这期间桩收到 0 个请求）。
#   - 复测与验证题（specs/retest-delivery-kge05；e2e/retest.spec.ts）也在这一套：第五个 BFF 另开 STARBUDDY_RETEST_UI=retest，桩另实现
#     retest 的三个内部接口；另四套 BFF 不开（e2e/retest-off.spec.ts 在 trust-off 项目里核对路由 404、桩收到 0 个请求）。
#   - 今日计划跨科编排（PLT-11；e2e/today-orch.spec.ts）也在这一套：第五个 BFF 另开 STARBUDDY_TODAY_ORCH=today_orch，桩另实现 /internal/v1/today；
#     另四套 BFF 不开（e2e/today-orch-off.spec.ts 在 trust-off 项目里核对今日页没有跨科列表、BFF 响应没有 ordered）。
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/practice-synthetic.sql" >/dev/null
FL_PORT="$(free_port)"
PR_BFF_PORT="$(free_port)"
WEB_PRACTICE_PORT="$(free_port)"
FL_CRED="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
FLLOG="$(mktemp)"
PRLOG="$(mktemp)"
# 桩的请求记录（每个请求一行 {"method","path"}，不含请求体与令牌）：端到端从这里读行数，核对开关关时桩收到 0 个请求；只有本用户可读，结束时删除
FL_REQLOG="$(mktemp)"
chmod 600 "$FL_REQLOG"
export SB_FAKE_LEARNING_LOG="$FL_REQLOG"
( export FAKE_LEARNING_PORT="$FL_PORT" FAKE_LEARNING_TOKEN="$FL_CRED" FAKE_LEARNING_REQUEST_LOG="$FL_REQLOG"
  exec node --no-warnings "$WEB/scripts/fake-learning-practice.ts" ) > "$FLLOG" 2>&1 < /dev/null &
FL_PID=$!
( cd "$ROOT/services/bff" || exit 1
  unset STARBUDDY_STARMAP STARBUDDY_TRUST_UI STARBUDDY_FAMILY_GOALS STARBUDDY_MONTHLY_PLAN  # 星图、信任界面、家长诉求开关全关：不从调用方环境继承
  export BFF_DATABASE_URL="postgresql://bff_app@/starbuddy_test?host=$PGHOST" PORT="$PR_BFF_PORT" BFF_AUTH_CONFIG="$AUTH_CFG" \
    BFF_LEARNING_URL="http://127.0.0.1:$FL_PORT" BFF_LEARNING_TOKEN="$FL_CRED" \
    STARBUDDY_ENV=test BFF_SHELL_ORIGIN="http://127.0.0.1:$WEB_PRACTICE_PORT" BFF_SMS_CAPTURE_FILE="$SMS_INBOX" \
    STARBUDDY_PRACTICE=practice STARBUDDY_RETEST_UI=retest STARBUDDY_TODAY_ORCH=today_orch
  exec node --no-warnings src/main.ts ) > "$PRLOG" 2>&1 < /dev/null &
PR_BFF_PID=$!
# 家长诉求（PLT-14 第 1 步；e2e/family-goals.spec.ts，playwright.config.ts 的 family-on 项目）：
#   - 合成家庭 HG2（家长 gG +99900000110；初三 G9、初二 G8）与两所合成的已发布学校（db/seeds/dev/family-goals-synthetic.sql）；
#   - 第六个 BFF：STARBUDDY_FAMILY_GOALS=family_goals（只在 STARBUDDY_ENV=test 生效），学习服务同第一套；另五套 BFF 与前端都不开
#     （e2e/family-goals-off.spec.ts 在 trust-off 项目里核对入口不出现、页面 404、BFF 路由不注册）。
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/family-goals-synthetic.sql" >/dev/null
# 月度计划（PLT-14 第 3 步；e2e/monthly-plan.spec.ts，同在 family-on 项目）：
#   - 真实洞察服务（services/insight，INSIGHT_ENV=test，合成判断策略、月度计划配置与模板库；登录角色只继承 starbuddy_insight），
#     只配 BFF 一个调用方令牌（每次随机生成，只经环境变量传）：家长打开“月度计划”页时第六个 BFF 经它生成快照（0054 的守卫照常核对），
#     “为什么”经它的 insight.explain；需要 services/insight/.venv（同 make insight-test）；
#   - 合成家庭 HM（家长 gM +99900000111；M1 专业档有判断与掌握数据、M2 基础档没有月度计划权益）：db/seeds/dev/monthly-plan-synthetic.sql，
#     引用星图 v1 合成地图的节点，在 star-map-v1 种子之后执行（见下方审核台一段）；
#   - 第六个 BFF 另开 STARBUDDY_MONTHLY_PLAN=monthly_plan 与 STARBUDDY_TRUST_UI=why，显示规则把三个合成判断模板列进对外白名单
#     （BFF_SHELL_CONFIG 只在 test 认；正式的 config/shell.json 仍是空表，必停项）。另五套 BFF 与前端都不开月度计划
#     （e2e/monthly-plan-off.spec.ts 在 trust-off 项目里核对入口不出现、页面 404、BFF 路由不注册）。
# 洞察服务的 venv 已在文件开头（起任何进程之前）核对：$IPY
psql -X -q -v ON_ERROR_STOP=1 -c "CREATE ROLE insight_app LOGIN IN ROLE starbuddy_insight" >/dev/null
IN_PORT="$(free_port)"
IN_CRED="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
INLOG="$(mktemp)"
( cd "$ROOT/services/insight" || exit 1
  # 同学习服务：先清掉子 shell 继承的全部导出变量，只导出洞察服务要的几项
  keep_path="$PATH"
  db_url="postgresql://insight_app@/starbuddy_test?host=$PGHOST"
  while read -r v; do unset "$v" 2>/dev/null || true; done < <(compgen -e)
  export PATH="$keep_path" INSIGHT_ENV=test INSIGHT_HOST=127.0.0.1 INSIGHT_PORT="$IN_PORT" INSIGHT_DATABASE_URL="$db_url" \
    INSIGHT_TOKEN_BFF="$IN_CRED" PYTHONDONTWRITEBYTECODE=1
  exec "$IPY" -m insight.service ) > "$INLOG" 2>&1 < /dev/null &
IN_PID=$!
FG_SHELL_CFG="$(mktemp --suffix=.json)"
python3 -c 'import json, sys
c = json.load(open(sys.argv[1]))
c["external_claim_templates"] = ["syn.math.node_mastery@1", "syn.math.node_transfer@1", "syn.exam.reconcile@1"]
json.dump(c, open(sys.argv[2], "w"))' "$ROOT/services/bff/config/shell.json" "$FG_SHELL_CFG"
FG_BFF_PORT="$(free_port)"
WEB_FAMILY_PORT="$(free_port)"
FGLOG="$(mktemp)"
( cd "$ROOT/services/bff" || exit 1
  unset STARBUDDY_STARMAP STARBUDDY_TRUST_UI STARBUDDY_PRACTICE STARBUDDY_RETEST_UI STARBUDDY_TODAY_ORCH STARBUDDY_FAMILY_GOALS STARBUDDY_MONTHLY_PLAN  # 其余开关全关：不从调用方环境继承
  export BFF_DATABASE_URL="postgresql://bff_app@/starbuddy_test?host=$PGHOST" PORT="$FG_BFF_PORT" BFF_AUTH_CONFIG="$AUTH_CFG" \
    BFF_LEARNING_URL="http://127.0.0.1:$LEARNING_PORT" BFF_LEARNING_TOKEN="$SVC_CRED" \
    STARBUDDY_ENV=test BFF_SHELL_ORIGIN="http://127.0.0.1:$WEB_FAMILY_PORT" BFF_SMS_CAPTURE_FILE="$SMS_INBOX" \
    STARBUDDY_FAMILY_GOALS=family_goals STARBUDDY_MONTHLY_PLAN=monthly_plan STARBUDDY_TRUST_UI=why BFF_SHELL_CONFIG="$FG_SHELL_CFG" \
    BFF_INSIGHT_URL="http://127.0.0.1:$IN_PORT" BFF_INSIGHT_TOKEN="$IN_CRED"
  exec node --no-warnings src/main.ts ) > "$FGLOG" 2>&1 < /dev/null &
FG_BFF_PID=$!
trap 'kill "$BFF_PID" "$TRUST_BFF_PID" "$SM_BFF_PID" "$SM_LEARNING_PID" "$RP_BFF_PID" "$FI_PID" "$PR_BFF_PID" "$FL_PID" "$FG_BFF_PID" "$IN_PID" "$AYA_PID" "$LEARNING_PID" 2>/dev/null || true; rm -f "$LOG" "$TLOG" "$SMLOG" "$RPLOG" "$FILOG" "$PRLOG" "$FLLOG" "$FL_REQLOG" "$FGLOG" "$INLOG" "$LLOG" "$SMLLOG" "$SMS_INBOX" "$AUTH_CFG" "$TRUST_SHELL_CFG" "$SM_SHELL_CFG" "$RP_SHELL_CFG" "$FG_SHELL_CFG"' EXIT
for _ in $(seq 1 80); do
  python3 -c "import socket,sys; ok=all(socket.socket().connect_ex(('127.0.0.1', p)) == 0 for p in ($BFF_PORT, $TRUST_BFF_PORT, $SM_BFF_PORT, $SM_LEARNING_PORT, $RP_BFF_PORT, $FI_PORT, $PR_BFF_PORT, $FL_PORT, $FG_BFF_PORT, $IN_PORT)); sys.exit(0 if ok else 1)" && break
  sleep 0.25
done

# 审核台后端（services/learning/review_console）：两个登录角色分别只继承 kge、review；合成 staff 与语文合成地图；
# 身份来自 staff 登录（PR 4，合成凭据在种子里）。需要 services/learning/.venv（同 make learning-test）。
psql -X -q -v ON_ERROR_STOP=1 -c "CREATE ROLE review_kge_app LOGIN IN ROLE starbuddy_kge" \
  -c "CREATE ROLE review_sign_app LOGIN IN ROLE starbuddy_review" >/dev/null
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/review-synthetic.sql" >/dev/null
# 编译页面入口（G 流冲刺 3）：合成页面审核记录；审核台后端配上同一组合成的托管平台合并记录与 staff 对应表（对账通过的那一份）
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/review-pages-synthetic.sql" >/dev/null
PAGES_FIX="$ROOT/services/learning/tests/fixtures/pages-reconcile"
# H5 星图 v1：合成数学地图（已发布）、家庭 HV（家长 gV +99900000107，孩子 V1、V2）、V1 的节点键掌握行与判断（语文地图由上面的审核台种子发布）
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/star-map-v1-synthetic.sql" >/dev/null
# 月度计划（PLT-14 第 3 步）：合成家庭 HM 的掌握行与判断引用上面这份合成数学地图的节点，所以在它之后执行
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/monthly-plan-synthetic.sql" >/dev/null
REVIEW_PORT="$(free_port)"
RLOG="$(mktemp)"
( cd "$ROOT/services/learning" || exit 1
  exec env REVIEW_CONSOLE_ENV=test \
    REVIEW_CONSOLE_KGE_DATABASE_URL="postgresql://review_kge_app@/starbuddy_test?host=$PGHOST" \
    REVIEW_CONSOLE_REVIEW_DATABASE_URL="postgresql://review_sign_app@/starbuddy_test?host=$PGHOST" \
    REVIEW_CONSOLE_PAGES_EXPORT="$PAGES_FIX/export.json" REVIEW_CONSOLE_PAGES_STAFF_MAP="$PAGES_FIX/staff-map.json" \
    REVIEW_CONSOLE_PAGES_MR_URL='https://git.example.cn/synthetic/kge-pages/-/merge_requests/{mr_id}' \
    REVIEW_CONSOLE_FEATURES=tag_worklist,revision_preflight \
    "$PY" -m review_console serve --port "$REVIEW_PORT" ) > "$RLOG" 2>&1 < /dev/null &
# ↑ KGE-06：审核台后端打开低置信工作单与修订预检（只有 practice-on 那一套前端打开同名开关，e2e/review-kge06.spec.ts；
#   其余前端关着，trust-off 里的 review-kge06-off.spec.ts 核对页面 404）
REVIEW_PID=$!
# 测试构建会改写 next-env.d.ts（指向 .next-e2e/types）：先备份，结束时还原，并删掉测试构建目录
ENVDTS="$(mktemp)"
if [ -f "$WEB/next-env.d.ts" ]; then cp "$WEB/next-env.d.ts" "$ENVDTS"; else rm -f "$ENVDTS"; fi
cleanup() {
  kill "$BFF_PID" "$TRUST_BFF_PID" "$SM_BFF_PID" "$SM_LEARNING_PID" "$RP_BFF_PID" "$FI_PID" "$PR_BFF_PID" "$FL_PID" "$FG_BFF_PID" "$IN_PID" "$REVIEW_PID" "$AYA_PID" "$LEARNING_PID" 2>/dev/null || true
  rm -f "$LOG" "$TLOG" "$SMLOG" "$RPLOG" "$FILOG" "$PRLOG" "$FLLOG" "$FL_REQLOG" "$FGLOG" "$INLOG" "$RLOG" "$LLOG" "$SMLLOG" "$SMS_INBOX" "$AUTH_CFG" "$TRUST_SHELL_CFG" "$SM_SHELL_CFG" "$RP_SHELL_CFG" "$FG_SHELL_CFG"
  if [ -f "$ENVDTS" ]; then cp "$ENVDTS" "$WEB/next-env.d.ts"; rm -f "$ENVDTS"; else rm -f "$WEB/next-env.d.ts"; fi
  rm -rf "${WEB:?}/.next-e2e"
}
trap cleanup EXIT
for _ in $(seq 1 80); do
  python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1', $REVIEW_PORT)) == 0 else 1)" && break
  sleep 0.25
done

# STARBUDDY_NEXT_DIST_DIR：构建与 next start（playwright.config.ts 的 webServer）都读 .next-e2e
export STARBUDDY_BFF_URL="http://127.0.0.1:$BFF_PORT" STARBUDDY_REVIEW_URL="http://127.0.0.1:$REVIEW_PORT" WEB_PORT STARBUDDY_DEV_PAGES=1 STARBUDDY_ENV=test NEXT_TELEMETRY_DISABLED=1 \
  STARBUDDY_NEXT_DIST_DIR=.next-e2e STARBUDDY_TRUST_BFF_URL="http://127.0.0.1:$TRUST_BFF_PORT" WEB_TRUST_PORT \
  STARBUDDY_STARMAP_BFF_URL="http://127.0.0.1:$SM_BFF_PORT" WEB_STARMAP_PORT \
  STARBUDDY_REPORT_BFF_URL="http://127.0.0.1:$RP_BFF_PORT" WEB_REPORT_PORT \
  STARBUDDY_PRACTICE_BFF_URL="http://127.0.0.1:$PR_BFF_PORT" WEB_PRACTICE_PORT \
  STARBUDDY_FAMILY_BFF_URL="http://127.0.0.1:$FG_BFF_PORT" WEB_FAMILY_PORT
cd "$WEB"
# 端到端用测试构建（冲刺 2-L）：开发专用页面（page.dev.tsx：探针、诊断演示页与符号样张页）只在 STARBUDDY_DEV_PAGES=1 且
# STARBUDDY_ENV=dev/test 构建时才是页面（next.config.ts，构建期常量）。测试构建输出到 .next-e2e、类型检查用 tsconfig.e2e.json，正式的 .next 保持生产构建，
# tests/build-output.test.ts 的检查不再跳过；tsconfig.json 不被改写（next-env.d.ts 结束时还原）。
BLOG="$(mktemp)"
npx --no-install next build > "$BLOG" 2>&1 || { tail -30 "$BLOG"; rm -f "$BLOG"; exit 1; }
rm -f "$BLOG"
set +e
npx --no-install playwright test "$@"
rc=$?
if [ $rc -ne 0 ]; then echo "--- BFF 日志（最后 20 行）"; tail -20 "$LOG"; echo "--- 信任界面 BFF 日志（最后 20 行）"; tail -20 "$TLOG"; echo "--- 审核台后端日志（最后 20 行）"; tail -20 "$RLOG"; echo "--- 学习服务日志（最后 20 行）"; tail -20 "$LLOG"
  echo "--- 星图 v1 BFF 日志（最后 20 行）"; tail -20 "$SMLOG"; echo "--- 星图 v1 学习服务日志（最后 20 行）"; tail -20 "$SMLLOG"
  echo "--- 首份报告 BFF 日志（最后 20 行）"; tail -20 "$RPLOG"; echo "--- 合成洞察服务桩日志（最后 20 行）"; tail -20 "$FILOG"
  echo "--- 错题再练 BFF 日志（最后 20 行）"; tail -20 "$PRLOG"; echo "--- 合成学习服务桩日志（最后 20 行）"; tail -20 "$FLLOG"
  echo "--- 家长诉求 BFF 日志（最后 20 行）"; tail -20 "$FGLOG"; echo "--- 洞察服务日志（最后 20 行）"; tail -20 "$INLOG"; fi
exit $rc
