# services/bff · TS BFF

模块：计量 `src/metering/`（2-E，`specs/metering-impl-sprint2/spec.md`）、星图读模块 `src/star-map/`（3-H，`specs/star-map-sprint3/spec.md`）、建档 `src/onboarding/`（SIE-01）、行为事件与探针 `src/events/`（2-L）、统一壳路由表与家庭登录 `src/http/`（4-H H1）。支付不在这里。

- 运行时：Node 22（原生运行 TS，与阿鸦适配器同一工具链）；数据库客户端 `pg`（MIT）。
- 计量流程：`meteredCall()`——以当前账号预留 → 用同一个 key 调学习服务 → 结算或释放；结果未知时同 key 退避重试。
  数据库侧的授权、加锁、幂等都在迁移 `0009` 的函数里，BFF 只是调用方（设计文档 `docs/design/metering-schema.md` 第 4、5 节）。
- 定时任务：`runExpiry()`、`runRenewal()`，连接池的登录角色只继承 `starbuddy_scheduler`。
- 规范化 JSON 与学习服务逐字节一致：共享向量 `packages/contracts/testdata/canonical-vectors.json`。
- 启动入口 `src/main.ts`（`npm start`；`node:http`，只监听 127.0.0.1）：只有统一壳路由表一个分发入口（`createShellServer`），表外一律 404。
  4-H H2 起星图、建档、行为事件与探针、诊断都挂在这张表上（`src/http/feature-routes.ts`），身份只来自会话，开发身份桩已删除。
- 星图：`GET /bff/v0/students/{id}/star-map`（auth = session）。
  - 接口定义在 `openapi/star-map.v0.yaml`；三态映射只在 `src/star-map/display-state.ts` 一处。
  - 授权完全交给 RLS；未授权与不存在返回同一个 404。
- 星图 v1（H5，`specs/star-map-v1-h5/spec.md`）：`GET /bff/v0/students/{id}/star-map/v1`，挂在开关 `starmap_v1` 上
  （`STARBUDDY_STARMAP=starmap_v1[,starmap_dashed]`，只在 dev / test），缺省全关、路由不注册、v0 不变。
  - 接口定义在 `openapi/star-map.v1.yaml`；读模块 `src/star-map/v1.ts`（判断先于展示），学习服务客户端 `src/star-map/nodes-client.ts`。
- 行为事件与“能做对吗”探针（冲刺 2-L，`specs/behavior-events-sprint2/spec.md`）：`src/events/`，同一个服务、同一套身份（auth = student）。
  - `POST /bff/v0/students/{id}/events`、`POST …/probes`、`POST …/probes/{probe_id}/attempt`；接口定义在 `openapi/events.v0.yaml`。
  - 只写不读，只回记录 ID；BFF 先校验请求体（未知键、自由文本一律 422），再调迁移 0025 的库函数（只认学生本人，家长、其他学生 403）。
  - 不消耗额度，不走计量预留；日志只记路由、状态码、耗时与学生 ID 的哈希，不记 payload。
- 数学自适应诊断（SIE-02a，`specs/sie-02a-math-diagnosis/spec.md`）：`src/diagnosis/`，同一个服务、同一套身份，只认学生本人。
  - `…/diagnosis/sessions`（开始 / 续做）、`…/sessions/{id}`（当前题）、`…/{id}/probe|answer|pause|abandon`；接口定义在 `openapi/diagnosis.v0.yaml`。
  - 开始预留 `diagnosis_full`、第一道题判分成功即结算、没答题就放弃或确定失败才释放、结果未知不释放；续做不另占额度。
    宵禁时段（计量只读函数 `platform.meter_guard_status` 的 `in_curfew`，迁移 0030，与 `meter_reserve` 同一口径、同一计量时钟；
    BFF 不自算宵禁）续做、取题、探针、作答回 402 `curfew` 并暂停会话。
    探针经 2-L 库函数写入（幂等键由会话与步骤派生）并在作答后关联。
  - 学习服务地址与令牌：`BFF_LEARNING_URL`、`BFF_LEARNING_TOKEN`（不设时诊断接口 503）。

- 统一壳 H4 读路由（`src/http/shell-routes.ts`、`src/shell/`；`config/shell.json`）：选科（目录读取、家长开通 / 暂停，只经 `enroll_subject` / `pause_subject`）、
  今日计划（经学习服务 `POST /internal/v1/plan`；推荐理由只取洞察服务 `insight.explain` 的 `RenderedStatement.text`，对外模板白名单为空时不显示）、
  额度与时长（`meter_guard_status` 与 `entitlement_balance`）、时长设置（`set_time_guard`）、连续使用提醒（进程内计时，见 lessons H4）。
  环境变量 `BFF_INSIGHT_URL` / `BFF_INSIGHT_TOKEN`（洞察服务）、`BFF_SHELL_CONFIG`（dev / test 换显示规则）。没有任何消耗类入口，也没有信任界面（冲刺 5）的路由。
- 信任界面接口（SIE-03 PR③，`src/http/trust-routes.ts`、`src/trust/`；接口定义 `openapi/trust.v0.yaml`）：异议（→ `insight.dispute`，不转发 `fact_ref`）、
  考试安排与成绩（`insight.submit_exam_plan` / `submit_exam_score`）、预测与对账卡读取（句子只来自可见判断，契约没有 BFF 读取通道，缺省 503）、
  家长导出（直连 0035 `export_student_data`，BFF 暂存、一次性兑换）与删除（申请、会话内二次确认、撤回、状态与回执）。每块挂在功能开关上
  （`STARBUDDY_TRUST_UI=dispute,reconcile,data_rights`，只在 dev / test），缺省全关、路由不注册；见 `specs/sie-03-dispute-reconcile/lessons.md` PR③。
  冲刺 5-H 加周报读取（开关 `weekly`，`GET …/weekly-report`）：直读 0036 `insight.weekly_report`（按受众 RLS），每句在库里再核判断并过对外模板白名单，
  纯函数在 `src/trust/weekly.ts`；见 `specs/trust-ui-sprint5/lessons.md` 周报小节。
  SIE-02b 第二步 PR 2 加首份报告（开关 `report`，`GET …/first-report` → `insight.report`）与单条解释（开关 `why`，
  `GET …/claims/{claim_id}/explain` → `insight.explain`）：先按档案授权（学生本人 / 有效绑定家长），只转发洞察服务的 `result`
  （信封外层的置信度、证据 ID、版本不给前端，证据链也不带证据 ID），按契约逐字段核对、过禁用推断词表、每个判断在库里再核，
  报告另核节键与句子模板类别对应，任何一处不合整份不出；解释按受众裁剪（学生无置信度分档、待验证无文字）；洞察服务响应体约 1 MB 上限；
  客户端与核对在 `src/trust/report.ts`，测试 `tests/report-units.test.ts`、`tests/report.integration.test.ts`，
  变异组 `BFFMUT_FILTER=报告：`。
- 统一壳路由表与家庭登录（冲刺 4-H H1，`specs/unified-shell-sprint4/spec.md` 第 2、3、16、17 节）：`src/http/`，接口定义 `openapi/shell.v0.yaml`。
  - 路由表 `src/http/routes.ts`：每条路由声明方法、路径模板、auth（public / session / guardian / student）、CSRF、限流；分发只查这张表。
    表外一律 404（H2 起没有旧路由）；路由表与 `openapi/*.v0.yaml` 一一对应（`tests/shell-openapi.test.ts`）。
  - 中间件链：请求 ID → 匹配 → 请求体 → 按 IP 限流 → 解析会话（`x-sb-session` → `platform.auth_session_resolve`）→ 按 auth 拦截
    → CSRF（Origin 等于 `BFF_SHELL_ORIGIN`；有会话的另核对与会话绑定的 `x-csrf-token`）→ 按号码 / 账号 / 会话 /（账号、学生）限流 → `withAccount` → handler。
  - 登录、会话、限流全部在迁移 0029 的库函数里（表不给任何应用角色策略）；验证码由 BFF 生成、经 `SmsSender` 发出，库里只存 HMAC。
  - 短信：只有捕获桩 `CaptureSmsSender`（dev / test，只收 +999 合成号码）；staging、pilot、生产没有供应商，启动即失败。
  - 年级与届别（`specs/policy-decisions-v0.9.1` PR2，迁移 0045）：`/me` 的 `children[].grade` 与选科目录一律读
    `platform.current_grade(届别, 请求时钟)`（`deps.now`，测试注入），不读存下的建档年级；不在 7–9 时为 null。家长届别更正
    `POST /students/{id}/exam-cohort` 只经 `platform.correct_exam_cohort`，频控按（家长账号、学生）成对（限流维度 `link`，
    桶 `cohort_correction.link.day`，每天 3 次），另加按账号的总量桶 `cohort_correction.account.day`（每天 10 次）。测试 `tests/cohort.integration.test.ts`，变异组 `BFFMUT_FILTER=届别：`。
    年级为 null 时（PR3 #10）：选科列表只列已开通、已暂停的学科、`can_enroll` 为假，只能暂停，新选回 409 `GRADE_OUT_OF_RANGE`；今日计划不排学科。
    学生本人的 `/me` 也带 `grade`（同样由届别推算）。诊断会话中途届别被改：学习服务的 409 `SESSION_INCONSISTENT`（reason = grade_changed）
    连同已核对归属的 `session_id` 透传给前端；“重新开始”放弃这个会话时调 `platform.meter_refund_grade_changed` 退回额度（迁移 0049）。
  - 配置：阈值 `config/auth.json`；`STARBUDDY_ENV`（必填）、`BFF_SHELL_ORIGIN`、`BFF_LOG_HMAC_KEY`、`BFF_AUTH_SECRET`（十六进制 ≥ 32 字节，
    dev / test 缺省随机）、`BFF_TRUST_CLIENT_IP`、`BFF_SMS_CAPTURE_FILE`（dev / test）、`BFF_AUTH_CONFIG`（只在 dev / test，端到端放宽限流）。
    见 `src/main.ts` 开头的注释。
  - 部署要求：`platform.auth_otp_login` 的参数有手机号原文，BFF 的登录角色要关掉语句与参数日志（同 0020：`log_statement = 'none'`、
    `log_parameter_max_length = 0`、`log_parameter_max_length_on_error = 0`）。

## 测试
```bash
cd services/bff && npm ci
make bff-test       # 类型检查 + 单测 + Gate 0 六类场景（一次性 Postgres，多连接）
make bff-mutation   # 去掉加锁或并发判断，断言集成测试能发现
```
