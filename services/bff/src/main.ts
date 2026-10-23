// BFF 的启动入口（specs/unified-shell-sprint4/spec.md 第 3、4 节，H2）：一个 node:http 服务，只有统一壳路由表一个分发入口
// （src/http/server.ts 的 createShellServer），表外路径一律 404。身份只来自会话（x-sb-session），开发身份桩已删除。
// 缺省只监听 127.0.0.1（staging 可经 BFF_HOST 显式配置内网 IPv4，部署拓扑随 ADR 10）；不连接任何共享或生产库（规则 9）。
// 运行环境与各变量的口径同 deploy/config/README.md 与 deploy/config/env-inventory.yaml（LT-01，以那两份为准）：
//   STARBUDDY_ENV     dev / test / staging / pilot / production（必填；短信捕获桩只在 dev / test；staging 只能用白名单固定码；
//                     pilot、生产没有短信供应商，拒绝启动）
//   STARBUDDY_IMAGE_ENV    镜像固化的环境标记（构建时写入）：设了就必须与 STARBUDDY_ENV 一致，否则拒绝启动
//   LT-01（specs/launch-lt01-deploy-packaging 决定记录第 1、2、6、10 条）：
//   BFF_HOST               监听地址，缺省 127.0.0.1；只有 staging 能设为内网 IPv4（RFC 1918），其余环境设了别的值拒绝启动
//   BFF_STAGING_OTP_FILE / BFF_STAGING_OTP_AUDIT_FILE  只在 staging、且 staging 必填：白名单固定码条目（运行时只读挂载，不进仓库）与
//                          审计文件（可写卷；哈希链、单实例锁、权限核对见 src/http/staging-otp.ts）；dev / test、pilot、生产设了拒绝启动
//   BFF_CONSENT_VERSIONS   只在 staging：由人登记的同意文本版本（逗号分隔的 同意类型=文本版本）；不设 = 记同意一律 422；
//                          合成版本（synthetic-*，不区分大小写）staging 拒绝
//   staging 下 BFF_LEARNING_URL / BFF_INSIGHT_URL 另收内网 IPv4 的 http。下面各开关标“dev / test”的，LT-01 起 staging 也可打开：
//   STARBUDDY_TRUST_UI（不含 profile）、STARBUDDY_STARMAP（只 starmap_v1）、STARBUDDY_PRACTICE、STARBUDDY_RETEST_UI、STARBUDDY_TODAY_ORCH
//   （开关矩阵 deploy/flags/staging.yaml）；pilot、生产设了仍拒绝启动。STARBUDDY_FAMILY_GOALS、STARBUDDY_MONTHLY_PLAN 仍只在 dev / test。
//   BFF_DATABASE_URL  连接串，登录角色只继承 starbuddy_bff（例如 postgresql://bff_app@/starbuddy_dev?host=/tmp/x）；
//                     staging 必须带 sslmode=verify-full&sslrootcert=/run/rds-ca/ca.pem（文件存在），否则建池之前拒绝（LT-03）
//   PORT              默认 8787
//   BFF_SHELL_ORIGIN  壳的来源（改变状态的请求 Origin 必须等于它）；dev / test 缺省 http://127.0.0.1:3000
//   BFF_LOG_HMAC_KEY、BFF_AUTH_SECRET  十六进制、至少 32 字节；dev / test 不设时随机生成，其余环境不设拒绝启动
//   BFF_TRUST_CLIENT_IP=1  客户端 IP 取 apps/web 转交的 x-sb-client-ip（BFF 只对 apps/web 开放、且网关覆盖 X-Forwarded-For 时才能打开；
//                          LT-01 编排里缺省 0，打开前核对网关是发布检查项）
//   BFF_SMS_CAPTURE_FILE   dev / test：捕获的验证码另写到这个文件（端到端测试读）
//   BFF_AUTH_CONFIG        dev / test：换一份阈值配置（端到端一次要登录很多次，放宽限流）；其余环境设了就拒绝启动，
//                          一律用 config/auth.json
//   BFF_LEARNING_URL / BFF_LEARNING_TOKEN  学习服务地址（https 或本机回环）与服务间令牌（≥ 32 字符）；不设时诊断接口 503、
//                          今日计划每一科“今天暂无安排”
//   BFF_INSIGHT_URL / BFF_INSIGHT_TOKEN    洞察服务地址（https 或本机回环）与 caller=bff 的令牌（≥ 32 字符）；不设时壳内不显示任何判断陈述
//   BFF_SHELL_CONFIG       dev / test：换一份壳的显示规则（config/shell.json 的格式）；其余环境设了就拒绝启动
//   STARBUDDY_TRUST_UI     dev / test：打开信任界面的 BFF 路由，逗号分隔（完整清单见 src/trust/features.ts）：
//                          dispute / reconcile / data_rights（SIE-03 PR③）、weekly（周报，冲刺 5-H）、profile（学情档案页，KGE-04 第二步 PR 4）、
//                          report（首份报告）、why（单条解释）（SIE-02b 第二步 PR 2）；
//                          缺省全关、路由不注册；其余环境设了就拒绝启动。异议经 BFF_INSIGHT_URL 调 insight.dispute；
//                          预测与对账卡的读取没有契约通道（缺省 503，lessons PR③ 待人确认）；
//                          profile、why 经 insight.explain，report 经 insight.report（同一个 BFF_INSIGHT_URL；不配时 report / why 503）
//   STARBUDDY_STARMAP      dev / test：H5 星图 v1 的开关（starmap_v1[,starmap_dashed]；src/star-map/features.ts）；缺省全关、
//                          v1 路由不注册、v0 不变；其余环境设了就拒绝启动。星态经 BFF_LEARNING_URL 调学习服务 star-map/nodes（同一个服务间令牌），
//                          不配学习服务时 v1 的星全部是中性底星
//   STARBUDDY_PRACTICE     dev / test：错题再练的开关（只认 practice；src/practice/features.ts）；缺省关、三条路由不注册；
//                          其余环境设了就拒绝启动。经 BFF_LEARNING_URL 调学习服务 practice 内部接口（同一个服务间令牌），
//                          不配学习服务时打开开关也一律 503。作答按题库自练计次（platform.meter_practice）
//   STARBUDDY_RETEST_UI    dev / test：复测与验证题的开关（只认 retest；src/retest/features.ts，KGE-05）；缺省关、三条路由不注册；
//                          其余环境设了就拒绝启动。经 BFF_LEARNING_URL 调学习服务 retest 内部接口（同一个服务间令牌），
//                          不配学习服务时打开开关也一律 503。作答默认沿用题库自练计次（platform.meter_practice，待人确认）
//   STARBUDDY_TODAY_ORCH   dev / test：今日计划跨科编排的开关（只认 today_orch；src/shell/today-orch.ts，PLT-11）；缺省关、today 响应不变；
//                          其余环境设了就拒绝启动。打开时学生本人的 today 另带 ordered，经 BFF_LEARNING_URL 调学习服务 /internal/v1/today
//                          （同一个服务间令牌、同一个计划时限）；不配学习服务时 ordered 为 null
//   STARBUDDY_FAMILY_GOALS dev / test：家长诉求的开关（只认 family_goals；src/family/features.ts，PLT-14 第 1 步）；缺省关、两条路由不注册；
//                          其余环境设了就拒绝启动。只给家长：学生会话在请求体、CSRF、限流之前一律 404；不计量
//   STARBUDDY_MONTHLY_PLAN dev / test：月度计划的开关（只认 monthly_plan；src/family/monthly-plan.ts，PLT-14 第 3 步）；缺省关、两条路由不注册；
//                          其余环境设了就拒绝启动；打开时启动即读模板库（services/insight/templates/monthly_plan.v0.yaml）与两张词表，
//                          读不到拒绝启动。家长端本月没有快照时经 BFF_INSIGHT_URL 调洞察服务生成接口（同一个 caller=bff 令牌）；
//                          不配洞察服务时家长端本月没有快照就 503。不计量
//   BFF_TRUST_SECRET       信任界面专用密钥（删除确认凭据、异议幂等键）：十六进制 ≥ 32 字节，规则同 BFF_AUTH_SECRET
//   STARBUDDY_SUBJECT_GATE 分学科并发闸门的开关（只认 subject_gate；src/gate/subject-gate.ts，PLT-16 第一步）；缺省关：不读配置、不建闸门，
//                          响应与访问日志逐字节不变。打开时启动即读 config/concurrency.json（读不到或不合格拒绝启动），诊断开始 / 取题 / 探针 / 作答、
//                          再练与复测的取题与作答在计量之前按学科（另按学生）取名额，超限回 503 SUBJECT_BUSY（带 retry-after）；
//                          闸门状态在进程内：dev / test 以外须在配置里声明 single_instance: true 才建闸门，否则不建并告警；建了也告警
//   BFF_CONCURRENCY_CONFIG dev / test：换一份闸门配置（config/concurrency.json 的格式）；其余环境设了就拒绝启动
// 诊断的宵禁判断用数据库的计量时钟（platform.meter_guard_status，迁移 0030），BFF 没有自己的时钟开关。
import pg from 'pg';
import { httpDiagnosisLearning } from './diagnosis/learning-client.ts';
import { checkImageEnv, dbTlsOk, isLocalEnv, listenHost, loadAuthConfig, loadKeys, parseRunEnv, RDS_CA_PATH, serviceUrlOk } from './http/config.ts';
import { parseConsentVersions, type ShellDeps } from './http/routes.ts';
import { createShellServer } from './http/server.ts';
import { createSmsSender } from './http/sms.ts';
import { StagingOtp } from './http/staging-otp.ts';
import { loadShellConfig } from './shell/config.ts';
import { httpInsightClient } from './shell/insight-client.ts';
import { httpPlanClient } from './shell/plan-client.ts';
import { httpTodayOrchClient, parseTodayOrchFeature } from './shell/today-orch.ts';
import { httpDisputeClient } from './trust/clients.ts';
import { parseTrustFeatures } from './trust/features.ts';
import { httpInsightReadClient } from './trust/report.ts';
import { assertBffRole } from './star-map/query.ts';
import { parseStarMapFeatures } from './star-map/features.ts';
import { httpStarMapClient } from './star-map/nodes-client.ts';
import { parsePracticeFeature } from './practice/features.ts';
import { httpPracticeLearning } from './practice/learning-client.ts';
import { parseRetestFeature } from './retest/features.ts';
import { httpRetestLearning } from './retest/learning-client.ts';
import { parseFamilyGoalsFeature } from './family/features.ts';
import { cleanWith, httpMonthlyPlanClient, loadTemplates, parseMonthlyPlanFeature } from './family/monthly-plan.ts';
import { lexicon, noPromiseLexicon } from './trust/lexicon.ts';
import { createSubjectGate, decideSubjectGate, loadConcurrencyConfig, parseSubjectGateFeature } from './gate/subject-gate.ts';

// 配置不全、环境不认识、非 dev / test 却没有短信供应商：一律拒绝启动（在连数据库之前）
let shellDeps: Omit<ShellDeps, 'pool'>;
let listenAddr = '127.0.0.1';
try {
  const env = parseRunEnv(process.env['STARBUDDY_ENV']);
  checkImageEnv(env, process.env['STARBUDDY_IMAGE_ENV']);  // LT-01 安全审查 S10：镜像固化的环境标记
  listenAddr = listenHost(process.env['BFF_HOST'], env);  // LT-01：只有 staging 能显式配置内网 IPv4
  const starMapFeatures = parseStarMapFeatures(env, process.env['STARBUDDY_STARMAP']);  // 非 dev / test 设了就拒绝启动
  const practiceOn = parsePracticeFeature(env, process.env['STARBUDDY_PRACTICE']);     // 同上
  const retestOn = parseRetestFeature(env, process.env['STARBUDDY_RETEST_UI']);        // 同上
  const orchOn = parseTodayOrchFeature(env, process.env['STARBUDDY_TODAY_ORCH']);      // 同上
  const familyGoalsOn = parseFamilyGoalsFeature(env, process.env['STARBUDDY_FAMILY_GOALS']);  // 同上
  const monthlyPlanOn = parseMonthlyPlanFeature(env, process.env['STARBUDDY_MONTHLY_PLAN']);  // 同上
  if (monthlyPlanOn) {
    try {
      loadTemplates(env, cleanWith(lexicon(), noPromiseLexicon()));  // 模板库与两张词表启动即读一次（fail closed）
    } catch (e) {
      throw new Error(`STARBUDDY_MONTHLY_PLAN 打开但月度计划模板库或词表不可用：${(e as Error).message}`);
    }
  }
  const configPath = process.env['BFF_AUTH_CONFIG'];
  if (configPath !== undefined && !isLocalEnv(env)) throw new Error('BFF_AUTH_CONFIG 只在 dev / test 可用');
  const keys = loadKeys(env);
  const shellOrigin = process.env['BFF_SHELL_ORIGIN'] ?? (isLocalEnv(env) ? 'http://127.0.0.1:3000' : '');
  if (!/^https?:\/\/[^/]+$/.test(shellOrigin) || (!isLocalEnv(env) && !shellOrigin.startsWith('https://'))) {
    throw new Error('BFF_SHELL_ORIGIN 必须是壳的来源（scheme://host[:port]，dev / test 以外必须是 https）');
  }
  const shellConfigPath = process.env['BFF_SHELL_CONFIG'];
  if (shellConfigPath !== undefined && !isLocalEnv(env)) throw new Error('BFF_SHELL_CONFIG 只在 dev / test 可用');
  const gateOn = parseSubjectGateFeature(env, process.env['STARBUDDY_SUBJECT_GATE']);
  const concurrencyPath = process.env['BFF_CONCURRENCY_CONFIG'];
  if (concurrencyPath !== undefined && !isLocalEnv(env)) throw new Error('BFF_CONCURRENCY_CONFIG 只在 dev / test 可用');
  // staging 白名单固定码 OTP（LT-01 决定记录第 6 条）：createSmsSender 已核对只在 staging、两个文件都配了
  const sms = createSmsSender(env);
  const stagingOtp = env === 'staging'
    ? new StagingOtp({ env, entriesFile: process.env['BFF_STAGING_OTP_FILE'] ?? '', auditFile: process.env['BFF_STAGING_OTP_AUDIT_FILE'] ?? '', keys })
    : undefined;
  const consentVersions = parseConsentVersions(env, process.env['BFF_CONSENT_VERSIONS']);
  shellDeps = { config: configPath === undefined ? loadAuthConfig() : loadAuthConfig(configPath), keys, env, sms, shellOrigin,
                ...(stagingOtp ? { stagingOtp } : {}), ...(consentVersions ? { consentVersions } : {}),
                trustClientIpHeader: process.env['BFF_TRUST_CLIENT_IP'] === '1',
                shell: { config: shellConfigPath === undefined ? loadShellConfig() : loadShellConfig(shellConfigPath), orch: { enabled: orchOn } },
                trust: { features: parseTrustFeatures(env, process.env['STARBUDDY_TRUST_UI']) },
                starMap: { features: starMapFeatures }, practice: { enabled: practiceOn }, retest: { enabled: retestOn },
                familyGoals: { enabled: familyGoalsOn }, monthlyPlan: { enabled: monthlyPlanOn } };
  // 分学科并发闸门（PLT-16）：开关关时不读配置、不建闸门（与开关上线前逐字节一致）
  if (gateOn) {
    const gateConfig = concurrencyPath === undefined ? loadConcurrencyConfig() : loadConcurrencyConfig(concurrencyPath);
    // 闸门的占用与队列在进程内：多实例部署时每个实例各算各的（spec 待人确认 #3）。dev / test 以外没声明 single_instance 就不建并告警，
    // 建了也告警（同 rest.singleInstance 先例）
    const decision = decideSubjectGate(env, gateConfig);
    if (decision.build) shellDeps.subjectGate = createSubjectGate(gateConfig);
    console.warn(JSON.stringify(decision.warning));
  }
  // 连续使用提醒的计时在进程内（src/shell/rest.ts）：dev / test 以外没声明单实例部署就不启用，并在启动时告警（pilot 前须完成会话表加列迁移）
  if (!isLocalEnv(env) && !shellDeps.shell!.config!.rest.singleInstance) {
    console.warn(JSON.stringify({ event: 'rest_reminder_disabled', reason: 'in_process_timer_requires_single_instance' }));
  }
} catch (e) {
  console.error(`bff: ${(e as Error).message}`);
  process.exit(2);
}

const url = process.env['BFF_DATABASE_URL'];
if (!url) {
  console.error('bff: 需要 BFF_DATABASE_URL');
  process.exit(2);
}
// LT-03 决定记录（续）第 6 条：staging 的连库串必须 verify-full + 固定 CA（文件存在），建池之前拒绝；消息不带连接串
if (!dbTlsOk(shellDeps.env, url)) {
  console.error(`bff: staging 的 BFF_DATABASE_URL 必须带 sslmode=verify-full&sslrootcert=${RDS_CA_PATH}（且该 CA 文件存在）`);
  process.exit(2);
}
const port = Number(process.env['PORT'] ?? 8787);
const pool = new pg.Pool({ connectionString: url, max: 10 });
try {
  await assertBffRole(pool);  // 连接角色配错会让 RLS 整体失效：拒绝启动
} catch (e) {
  console.error(`bff: ${(e as Error).message}`);
  await pool.end();
  process.exit(2);
}
const learningUrl = process.env['BFF_LEARNING_URL'];
const learningCredential = process.env['BFF_LEARNING_TOKEN'] ?? '';
if (learningUrl) {
  if (learningCredential.length < 32 || !serviceUrlOk(learningUrl, shellDeps.env)) {
    console.error('bff: BFF_LEARNING_URL 必须是 https 或本机回环地址（staging 另收内网 IPv4），服务间令牌至少 32 个字符');
    await pool.end();
    process.exit(2);
  }
  shellDeps.diagnosis = { learning: httpDiagnosisLearning({ baseUrl: learningUrl, token: learningCredential }) };
  shellDeps.shell!.plan = httpPlanClient({ baseUrl: learningUrl, token: learningCredential, timeoutMs: shellDeps.shell!.config!.plan.timeoutMs });
  if (shellDeps.shell!.orch!.enabled) {
    shellDeps.shell!.orch!.client = httpTodayOrchClient({ baseUrl: learningUrl, token: learningCredential, timeoutMs: shellDeps.shell!.config!.plan.timeoutMs });
  }
  if (shellDeps.starMap!.features!.has('starmap_v1')) {
    shellDeps.starMap!.nodes = httpStarMapClient({ baseUrl: learningUrl, token: learningCredential, timeoutMs: shellDeps.shell!.config!.starMap.timeoutMs });
  }
  if (shellDeps.practice!.enabled) shellDeps.practice!.learning = httpPracticeLearning({ baseUrl: learningUrl, token: learningCredential });
  if (shellDeps.retest!.enabled) shellDeps.retest!.learning = httpRetestLearning({ baseUrl: learningUrl, token: learningCredential });
}
const insightUrl = process.env['BFF_INSIGHT_URL'];
const insightCredential = process.env['BFF_INSIGHT_TOKEN'] ?? '';
if (insightUrl) {
  if (insightCredential.length < 32 || insightCredential === learningCredential
      || !serviceUrlOk(insightUrl, shellDeps.env)) {
    console.error('bff: BFF_INSIGHT_URL 必须是 https 或本机回环地址（staging 另收内网 IPv4），令牌至少 32 个字符且与学习服务令牌不同');
    await pool.end();
    process.exit(2);
  }
  shellDeps.shell!.insight = httpInsightClient({ baseUrl: insightUrl, token: insightCredential });
  shellDeps.trust!.dispute = httpDisputeClient({ baseUrl: insightUrl, token: insightCredential });
  shellDeps.trust!.read = httpInsightReadClient({ baseUrl: insightUrl, token: insightCredential });  // 首份报告与单条解释（开关 report / why）
  // 月度计划生成接口（开关 monthly_plan；只在家长打开页面且本月没有快照时调）
  if (shellDeps.monthlyPlan!.enabled) shellDeps.monthlyPlan!.client = httpMonthlyPlanClient({ baseUrl: insightUrl, token: insightCredential });
}
const server = createShellServer({ ...shellDeps, pool });
server.listen(port, listenAddr, () => {
  console.log(JSON.stringify({ event: 'listening', port }));
});
const stop = () => server.close(() => void pool.end());
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
