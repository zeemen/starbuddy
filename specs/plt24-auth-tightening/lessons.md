# PLT-24 · 鉴权收紧 · 实现记录

2026-11-09 用户确认（“按建议，默认值都同意，开始实现”），spec 第六节待人确认 1–5 全部按默认：两枚令牌、请求体注入保持 422、
`contact_support` 保持可区分、学生会话最长 12 小时 / 空闲 1 小时、T49 与 T47 一个提交。**属于服务间鉴权（安全关键代码）**：
提交首行 `[NEEDS-HUMAN]`，请安全负责人过目。不改 packages/contracts、迁移、RLS、计量与判分逻辑。

## 交付

### A. T49：BFF → 学习服务两枚令牌
- **学习服务**（`learning/config.py`、`learning/app.py`）：新变量 `LEARNING_SESSION_TOKEN`（≥ 32 字符、参与“令牌各不相同”核对、不进 repr）；
  staging 缺失拒绝启动。诊断、错题再练、复测、今日编排四组接口改为只认会话令牌；写入令牌（`LEARNING_SERVICE_TOKEN`）只开契约 v1 的
  写入、重放、今日计划、星图（`/internal/v1/star-map/nodes` 原本就只认它，不变）。被拒响应与没带令牌逐字节相同。
- **BFF**（`src/http/config.ts` 的 `learningTokens`、`src/main.ts`）：新变量 `BFF_LEARNING_SESSION_TOKEN`。配了 `BFF_LEARNING_URL` 时两枚都必须有、
  各 ≥ 32、互不相同，否则拒绝启动（消息不带取值）；洞察服务令牌与两枚都不同。诊断、再练、复测、今日编排客户端用会话令牌，今日计划、星图用写入令牌。
- **接口定义**：`services/learning/openapi/` 四份服务自有 yaml 的安全方案改为 `bffSessionToken`；签字契约 learning-internal v1 不动。
- **启动脚本与部署**：`apps/web/scripts/e2e.sh`（真实学习服务与各 BFF；`fake-learning-practice.ts` 同真实服务分两枚核对）、
  BFF 诊断集成测试、`tools/e2e-cohort` / `tools/loadtest` 栈、`db/pentest/up.sh`（secrets.env 三枚）与 `selftest.sh`（新增三条混用核对）；
  compose（staging、local-test）、`deploy/k8s/staging.yaml`（`starbuddy-learning` 的新键 `session-token`，学习服务与 BFF 共用）、
  `env-inventory.yaml`、`deploy-check.sh` / `compose-smoke.sh` 的变量清单、首次部署手册的 Secret 键表。
  凭据同步清单（`kms.tf` 的 `app_secrets`）按 Secret 名登记，新键在同一个 `starbuddy-learning` 参数里，**名单不变**；
  人在 OOS 加密参数 `starbuddy-staging/starbuddy-learning` 里加 `session-token` 一项（随机生成，与 `service-token` 不同）。
- **ADR 06** 增补“服务间令牌按能力分组”（只登记事实）；攻击面清单 F3 扩到两枚令牌的混用并补引用。

### B. T47
1. 请求体注入 `account_id`：保持 422。`specs/household-pentest-round2/spec.md` 第 149 行与验收清单改为“请求体注入一律拒绝（422），
   请求头、查询参数注入一律忽略”；攻击面 N3 写明口径。只改文档。
2. `contact_support`：保持可区分（409）。攻击面 N1 登记理由（只有持有该号码、输对验证码的人看得到，不对外泄露同号账号数）并补引用。只改文档。
3. 学生会话：`services/bff/config/auth.json` 的 `session.student` 改为 `absolute_seconds: 43200`、`idle_seconds: 3600`；家长会话不变。
   库函数 `auth_issue_student_session`（0029）的上下界本来就收这组值，不改迁移。

## 测试与变异
- 学习服务：`tests/test_token_matrix.py`——路由 × 令牌矩阵（枚举 `app.routes`，**新增路由不登记即失败**；每条 BFF 路由：本组令牌不是 401，
  另一枚、编排层、洞察服务、路由客户端、变形令牌与没带令牌逐字节相同；编排层 / 洞察服务 / 路由客户端的接口两枚 BFF 令牌都打不开；
  会话令牌未配置时四组接口全部 401）、配置核对（读取、repr、过短、与其他令牌相同）；`test_staging_env.py` 加“staging 缺会话令牌拒绝启动”。
  原有会话类测试改用会话令牌、契约写入与重放带写入令牌。全量 3964 项通过。
- 变异：学习服务 `scripts/staging_mutations.py` 新增 10 条（四组认错令牌、会话接口也收写入令牌、写入也收会话令牌、长度 / staging 必填 /
  互不相同 / 读入配置），99 + 9 条全部被发现；BFF `tests/mutations.sh` 新增“令牌：”9 条（两枚相同、长度、缺会话令牌退回写入令牌、
  四个会话类客户端与计划客户端拿错令牌、洞察令牌可与会话令牌相同）与“H6：学生会话用家长时长”1 条，全部被发现（H6 那条按登记的断言命中）。
- BFF：`tests/learning-tokens.test.ts`（`learningTokens` 单元 + `src/main.ts` 静态核对：每个学习服务客户端拿哪一枚，新增客户端不登记即失败）；
  `route-matrix.integration.test.ts` 新增“PLT-24 学生会话”：注入时钟，空闲差一分钟仍有效、满 1 小时 + 1 秒每条路由 401；一直在用（每 50 分钟）
  满 12 小时每条路由 401；同一时间线上另一台设备的家长会话始终有效。`auth.integration.test.ts` 的配置断言补学生时长。
- `deploy-check`（318 项 + iac / observability / migrate-rehearse / compose 结构）、`attack-surface-check`（74 条、539 条引用）通过。
- 提交 f006316 之后补记（本机）：学习服务变异组 practice（35 + 25 + 39）、retest（24 + 34）、today（24 + 46）、diagnosis（52）、
  internal-v1（39）、mastery（77 + 54）全部被发现；`pentest-env-test`（含三条新的令牌混用核对）、`loadtest-test`、`e2e-cohort`、
  洞察服务 `test_kge04_db.py` 通过。
- 端到端（`e2e.sh --grep-invert @visual`，本机要 `PW_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`，
  否则锁定的 Playwright 找不到 headless_shell、全部失败）：178 项中 174 通过、1 跳过；星图“手机宽度与 200% 缩放”一例在与变异组同时跑时
  `page.goto` 超时，单独重跑通过；glyphs DPR 2 两例失败是本机 Chromium 1243 的已知环境差异（tech-debt-batch2 记录：旧代码同样失败），与本任务无关。
- 端到端的学习服务桩（`apps/web/scripts/fake-learning-practice.ts`）在补跑时修正：诊断接口也按会话令牌核对（与真实服务四组一致）。

## 与 spec 的偏差
1. **学习服务未配会话令牌时**：spec 写“四组接口回 503（与没有配置学习服务一致）”。实现是 **401**（与没带令牌同一个响应）：
   只在 dev / test 会出现（staging 缺失即拒绝启动），401 不向调用方透露“这一组没配置”，也不用改四个接口模块的错误分支；
   BFF 侧配了学习服务地址而缺会话令牌直接拒绝启动，所以真实部署里到不了这一步。需要 503 时只改 `app.py` 的 `session_ok` 一处与四个模块的拒绝分支。
2. **BFF 缺会话令牌**：spec 写“两枚相同即拒绝启动”，缺失“按约定拒绝启动或 503”。实现是配了 `BFF_LEARNING_URL` 时缺任何一枚、过短或相同都拒绝启动
   （所有环境）；不配地址时照旧（诊断 503、今日计划“暂无安排”）。
3. **打开学生模式会作废打开它的家长会话**（现有行为，0029）：会话测试里用另一台设备上的家长会话核对“家长会话照旧”。

## 经验
- 学习服务的路由清单从 `app.routes` 枚举再与登记表比较，比在测试里手写路由列表可靠：漏登记的新路由直接让第一条测试失败。
- 测试里 `pytest.raises(match=...)` 的中文片段要逐字对：报错是“各不相同”，写成“不同”匹配不上（“各不相同”里没有连续的“不同”）。
- H6 变异的预期断言要对准**第一条**失败的断言：“学生会话用家长时长”先挂在“学生空闲 1 小时”的 401 扫描上，而不是“空闲不到 1 小时”那条（那条在变异下仍通过）。

## 待人确认
1. **偏差 1**：会话令牌未配置时 401（实现）还是 503（spec 原文）？建议保持 401（只在 dev / test 出现，staging 拒绝启动）。
2. **staging 首次部署**：OOS 加密参数 `starbuddy-learning` 里新增 `session-token`（随机 ≥ 32 字符、与 `service-token` 不同），由人在密钥管理里生成；
   部署前用首次部署手册的 Secret 键表核对（只看名字与字节数）。
3. 安全负责人过目本提交（服务间鉴权）。
