# W13 收口：阻断级缺陷与技术债登记、待人确认汇总

- 依据：开发计划 v2.0 第五章 W13“阻断级缺陷清零与技术债登记”、第六章 Gate 0 验收清单（五条硬条件、五条软条件）；CLAUDE.md“必须停下来请人确认的变更”。
- 范围：`specs/*/lessons.md`、`specs/*/spec.md` 的“待人确认”，`docs/adr/` 01–15 的状态与第 5 节，`db/migrations/` 0010–0036 的文件头，`specs/readiness-check-2026-09-25.md`，`docs/compliance/vendors.md`，`docs/mapping/error-taxonomy.md`。
- 口径：
  - 只陈述仓库里能看到的事实并给出引用（`文件:行号`，行号是该文件自己的行号）；**不下 Gate 结论**，Gate 是否通过由人决定（见 `docs/gates/gate-0-evidence.md`，本文件不改它）。
  - 同一事项在多处登记的，合并为一条，引用列出全部出处；已在后续 PR 中关闭的事项不再列出（例如 N17“过期后无预留执行”已由 0016 重放封存关闭，`specs/plugin-api-v1.0-step2/lessons.md:25`）。
  - “建议处理人”只取五类：技术负责人、工程监督、教研、产品、合规。“默认建议”是来源里已写的默认做法，不是新决定。
  - 留出集只按规则 14 引用流程，不涉及内容。
- 截至：2026-10-01，仓库 HEAD `d0ba0f7`。本文件与 `docs/gates/gate-0-evidence.md` 同批提交。
- 已关闭、不再列出的另一例：`specs/learner-schema-v0.9/spec.md:86`（#11 选科影响权益）已由 0009 改为“选科只经函数写入”关闭（`specs/metering-impl-sprint2/lessons.md:22`）。

## 0. 总览

| 类别 | 条数 |
|---|---|
| 一、阻断级 | 24（其中 B02 已于 2026-10-01 关闭，未结 23） |
| 二、必停项待签字（七类合计；另有 2.8 节 ADR 与流程项 4 条单列） | 166 |
| 三、技术债（非阻断） | 52 |
| 四、环境与部署前置（八小节合计） | 67 |

所有 ADR（01–15）状态都是“草稿，待签字”（`docs/adr/README.md:9-18,26-30`）；迁移 0010–0036 是“草稿，只写文件，由人执行”（各文件第 2–3 行），0015、0016 除外：两者文件头没有这句，0016 的执行要求见 `specs/plugin-api-v1.0-step2/lessons.md:31`，0015 的草稿状态见 `specs/metering-v2/lessons.md:6`。

---

## 一、阻断级

判定口径：会让 Gate 0 某条硬条件的证据无法形成，或在 pilot（寒假封测）上线前必须解决的安全、合规、数据问题。Gate 0 硬条件编号沿用开发计划第六章：硬1 四科写入同一学生模型；硬2 家长隔离与扣次幂等；硬3 点点跨模型一致性结论；硬4 数学、道法知识切片过门；硬5 画像底线。“上线前”指寒假封测（pilot）前。

| 编号 | 事项 | 来源 | 影响的 Gate 条件 | 建议处理人 | 默认建议 |
|---|---|---|---|---|---|
| B01 | **插件 API v1.0 仍是候选版**：61 项评审未签字；1.0.0 只能在 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1` 下使用（测试开关），学习服务 v1 写入、诊断会话、今日计划 `/internal/v1/plan` 都依赖它；三科能力声明默认仍报 0.9.0，联调时临时打开候选开关 | `specs/plugin-api-v1.0/lessons.md:3,23,48`；`specs/e2e-integration-sprint5/lessons.md:64-66,89`；`specs/sie-02a-math-diagnosis/lessons.md:30`；`specs/unified-shell-sprint4/lessons.md:172-173`；`specs/contract-conformance-sprint1/lessons.md:50`；`specs/adapter-aya-methods-sprint4/lessons.md:35,90`（覆盖率报告改取 1.0.0 那一遍，已取代 `specs/plugin-api-v1.0-step2/lessons.md:40` 的“1.0.0 那条不写报告”） | 硬1（证据在候选版下取得）；软·契约覆盖率（阿鸦报告取 1.0.0 那一遍）；上线前（候选开关不得在 pilot 打开） | 技术负责人 | 召开评审会逐项签字；签字后收口默认版本与候选开关，并按 e2e lessons 第 66 行重生 Gate 0 样例 |
| B02 | **【已关闭（2026-10-01 用户决定：Gate 0 “四科”改为“三科”，英语搁置不计）】** 英语搁置后的 Gate 0 口径未定：硬1 写“四科”，证据按三科出；软条件“开口成章 ≥ 4/6”不准备证据 | `specs/adapter-echo-sprint3/lessons.md:27,32,50`；`specs/plan-v2-alignment/spec.md:61-62`；`specs/e2e-integration-sprint5/lessons.md:76-77` | 硬1；软·契约覆盖率 | 产品 | 证据按三科出，“四科→三科”是否改写条款由 Gate 0 评审定 |
| B03 | **硬1 证据的结构性缺口**：道法没有诊断入口（适配器只支持 process，学习服务不接受）；复测由桩调度；节点状态一律 `evidence_only`（掌握未写库）；星图截图未做；真实内测的只读核对登录需要不受 RLS 的角色（RLS 变更）；合成证据包与真实内测汇总取哪一份未定 | `specs/e2e-integration-sprint5/lessons.md:35-36,39-45,59-62,71-73,76` | 硬1 | 技术负责人 | 道法入口交契约评审；复测与状态随 B04；截图在 4-H 壳页面与星图 v1 后补；核对角色按 RUNBOOK 第 1 节二选一另起必停 PR |
| B04 | **KGE-04 第二步未实现，掌握数值未签字**：`aya.draft.json` 数值全空、签字栏“未签字”，签字前最高到“稳定”；`mastery_estimate` 节点键、`retest_task`、`profile_page` 三张表的迁移未写；“我其实会”的验证任务是接口桩 | `specs/mastery-estimate-sprint4/lessons.md:49,57-68`；`specs/sie-03-dispute-reconcile/lessons.md:85-86,182,280`；`specs/e2e-integration-sprint5/lessons.md:35-36` | 硬1（节点状态、复测）；硬5（删除覆盖缺档案页）；星亮 | 教研（数值签字）、技术负责人（迁移与接入） | 数学教研填写并签字配置；第二步迁移从下一个空编号起，先副本演练 |
| B05 | **家长隔离渗透测试两轮都没有执行**：第一轮只有仓库侧交付物；执行方、时间窗、授权人未指定；第二轮 spec 仍是草稿；`coverage.md` 是空模板 | `specs/household-pentest-sprint3/lessons.md:4,6,85`；`specs/household-pentest-round2/spec.md:3,29,229-246`；`docs/security/pentest/coverage.md:1-5` | 硬2 | 技术负责人、合规 | 第二轮按其待人确认 #1 默认（ADR 10 未签时第 3 层在本地扩展环境执行，R 类登记为 pilot 前补测）；Gate 0 只认两轮都完成的版本（同 spec #9） |
| B06 | **扣次相关的已接受风险与规则待签字**：①模型路由接口不核对计量预留（拿到学科令牌可不经计量调用模型），也不按 (usage_id, idempotency_key) 去重，按默认写成“已接受风险（待人签字）”；②转人工复核的批改按成功扣 1 次；③诊断预留有效期 10 分钟而诊断最长 35 分钟，10 分钟内未交题会过期不扣；④联调回放不经 BFF，计量零新增是“没有经过 BFF 的零” | `specs/adapter-zhixing-sprint2/lessons.md:75-76`；`specs/model-router-sprint1/lessons.md:37`；`specs/load-cost-baseline-sprint5/lessons.md:60-62`；`specs/sie-02a-math-diagnosis/lessons.md:89-92`；`specs/e2e-integration-sprint5/lessons.md:46-48` | 硬2（扣次幂等的口径） | 产品、技术负责人 | ①签字接受或在计量 v2 加预留核对；②③维持现状待定；④幂等仍以 db-test 70/78 与 bff-test 为证据，BFF 学习写入路由稳定后回放改经 BFF |
| B07 | **点点跨模型一致性没有真实运行**：首轮、二轮都只用假供应商证明流程；前置全部缺失——ADR 04 / 08 未签、`vendors.md` 没有任何供应商、仓库里没有人设 Prompt（`docs/personas/prompts/` 不存在，登记表 `prompts` 全空）、后处理 `safe_reply` 是占位；ADR 08 #3 通过标准是否沿用未定；留出集须由教研负责人重新封存（仓库只留条数与校验和，构建方不接触，规则 14） | `specs/diandian-consistency-r1-sprint2/lessons.md:21`；`specs/diandian-consistency-r2-sprint3/lessons.md:4,51-53,64`；`specs/persona-governance-sprint3/lessons.md:9`；`docs/compliance/vendors.md:3,9`；`docs/adr/08-english-model-and-voice-chain.md:103`；`specs/diandian-cases-v0-sprint1/lessons.md:42-43,51-53`；`specs/diandian-consistency-r2-sprint3/lessons.md:55-56` | 硬3 | 产品（人设负责人）、合规、技术负责人 | 先签 ADR 04 增补与供应商登记，再审定人设 Prompt v0 与替代句，教研负责人完成留出集重新封存，由执行人在有凭据的环境运行，只提交不含原文的报告 |
| B08 | **数学、道法切片过门所需的人工产出均未到位**：编纂规范 v1 第 10 节空白待签发；页面仓库未建；`evals/kge-tagging/gold`、`evals/daofa-grading/gold`、`evals/kge-compile/gold`、`evals/sie-shadow/gold` 目录都不存在；留出集盲测、≥100 题双人试标注（kappa）、≥50 题道法判分标注、核心节点清单与门槛签字、影子评审周数都待教研；阿鸦 P 类映射、14 个孤点、20 个节点边界待定；两库库模式盘点未运行 | `specs/kge-03-compile-layer/lessons.md:163-175`；`specs/kge-daofa-slice-sprint4/lessons.md:81-95`；`specs/kge-m2-gate-sprint5/lessons.md:44-49`；`specs/kge-01-skeleton-import/lessons.md:40-42`；`specs/kge-00-asset-inventory/lessons.md:67-72` | 硬4（全部六项指标） | 教研 | 按 KGE-03 lessons“真实 M1 交接清单”与 4-K“真实内容交接清单”逐项执行；P 类映射建议先复核（KGE-01 lessons:35） |
| B09 | **节点—课标承接关系没有表**：库上数据时 M1 报表的“课标承接率”恒为“不可计算：承接关系未入库”，页面“课标要求”段为“（暂无）” | `specs/kge-03-compile-layer/lessons.md:39,101,151,170`；`specs/kge-schema-v0.9/lessons.md:133` | 硬4（课标承接 100%） | 技术负责人、教研 | 定表 / 列 / `aligned_to` 边三选一，出迁移草稿（必停） |
| B10 | **道法教材投影在现 schema 上写不成 verified**：0011 要求 verified 时原句非空，而原句按决定不入库；0026 只放行 verified，所以只读角色看不到任何道法投影（开卷定位） | `specs/kge-daofa-slice-sprint4/lessons.md:51-56,67-73` | 硬4（道法切片） | 合规（法务）、教研、技术负责人 | 默认不改，等人二选一：法务确认原句口径后入库，或另起迁移分开“页码已核对”与“原句已核对” |
| B11 | **道法观点节点挂不了页面**：首期页面只有 `kp` 类型，`viewpoint` 节点的页面被体检判为孤立页、快照导出拒绝 | `specs/kge-m2-gate-sprint5/lessons.md:19-21,44-45` | 硬4（核心节点页面发布率 100%） | 教研、产品 | 三选一：导入时 kind 用 kp；页面层加 viewpoint 类型；道法 M2 暂不要求页面。默认不动、如实报 |
| B12 | **审核台同一名 staff 可以自己改草稿、自己签字、自己发布**（安全审查按阻断请人决定）；草稿修改不记修改人 | `specs/release-gate-sprint4/lessons.md:65-66,151` | 硬4（高风险页与地图版本的两人审核口径）；上线前 | 工程监督、教研 | v0 走查只有一名教研时暂时接受，结构性变更仍须两人；另一选择是 platform 侧记录草稿修改人、`sign_review` 拒绝“最近修改人 = 签字人”（迁移） |
| B13 | **禁用推断词表未签字，且有已知漏拦**：词表 `status: draft`，签字前不能用作发布阻断；“粗心 / 马虎 / 粗心大意”单独出现不命中；缺“名次与录取概率”类；英文缺 anxiety、learning style 类词条 | `specs/sie-00-field-dictionary/lessons.md:88`；`docs/design/sie/forbidden-inference.v0.yaml:2,11`；`specs/sie-03-dispute-reconcile/lessons.md:328-330`；`specs/plan-v2-alignment/spec.md:173-174`；`specs/service-openapi-v0.9/lessons.md:155` | 硬5（禁用推断 0） | 产品、教研、合规 | 产品负责人、教研与外部合规顾问签字；签字时一并补三类缺口 |
| B14 | **判断模板未登记、对外模板白名单为空**：洞察服务只有 `syn.*` 合成占位模板；`config/shell.json` 的 `external_claim_templates` 为空，壳内、周报都不显示任何判断陈述；判断阈值、置信度分档、有效期未签；首份报告、周报、对账卡、信任界面文案全部是 `{{TODO_COPY}}` 占位 | `specs/sie-02b-claims-first-report/lessons.md:66-69`；`specs/unified-shell-sprint4/lessons.md:212-213,234-235`；`specs/trust-ui-sprint5/lessons.md:70-71,145-146,152`；`docs/adr/13-claim-ledger-single-writer.md:80-81`；`specs/sie-00-field-dictionary/lessons.md:89` | 硬5（对学生家长可见的陈述 100% 引用判断——真实陈述的证据形态）；上线前 | 产品、教研 | 人写人审后登记模板，再由人改白名单（必停） |
| B15 | **洞察服务对 BFF 代表身份的授权再核对没有接线**：`ActorVerifier` 缺省一律不通过，真实环境里 `insight.explain`、`insight.dispute` 都是 404；导出 / 删除接线二选一未定（洞察服务工具回 501，BFF 直连 0035） | `specs/sie-02b-claims-first-report/lessons.md:37-39,61-62`；`specs/sie-03-dispute-reconcile/lessons.md:118-119,167-170,220`；`specs/unified-shell-sprint4/lessons.md:227` | 硬5（“为什么”与异议的可用性）；上线前 | 技术负责人 | 迁移草稿新增属主 authz、只返回真假的核对函数（只授洞察服务），或确认“信任 BFF 令牌”并改契约描述（两者都必停） |
| B16 | **删除覆盖的已知缺口**：档案页无表；staff 四表只靠函数保证不含学生账号；`trial_claim` 豁免理由与实际不符（删除后 `student_id` 仍在）；`auth_otp_challenge`、`auth_rate_counter` 的手机号摘要不在清单；库外存储（BFF 导出包暂存、对象存储、备份、日志）不在覆盖范围；计量流水只追加、与未成年人删除权的处理未定；14 周岁以上学生本人的导出与删除入口未做 | `specs/sie-03-dispute-reconcile/lessons.md:51-52,63,243,280,319-334`；`specs/metering-schema-sprint1/lessons.md:90` | 硬5（删除覆盖全表） | 合规、技术负责人 | 逐条登记；staff 表加守卫触发器、`trial_claim` 置空或改理由、两张认证表是否入清单由合规定（均为迁移） |
| B17 | **已绑定家长在库层可读学生的全部证据**，含诊断作答与过程原文（0004 `evidence_read`）；诊断不对家长出结论，但库层可读 | `specs/sie-02a-math-diagnosis/lessons.md:109-111`；`specs/learner-schema-v0.9/spec.md:84` | 硬5；上线前（画像可见范围） | 技术负责人、产品 | 二选一：证据加来源标记、家长策略排除诊断与过程原文；或过程原文不进 `evidence_event`（迁移与 RLS，必停） |
| B18 | **“C 级识别内容不生成结论”的约束覆盖不全**：学习服务侧已有测试，洞察服务侧约束测试未补；`skill_observation` 无等级标记、`insight.claim.evidence_ids` 不按等级过滤；画布 / 语音 A 级只凭 `answer_evidence_ref` 且引用无法核实；申诉期间原 A 级证据不降级 | `specs/kge-m2-gate-sprint5/lessons.md:15-18,47`；`specs/evidence-attributes-sprint1/lessons.md:46-47,51-52,54` | 硬5（C 级不生成结论） | 技术负责人 | 由 SIE 任务包补洞察服务侧测试；输入通道服务上线前画布、语音是否一律封顶 B 由人定 |
| B19 | **生产拦截与密钥卫生**：`.claude/hooks/guard.py:14` 生产标识仍是占位符（只拦 `--prod`）；文砺仓库根目录 `starbuddy-secrets-backup` 内容未核查；Supabase 连接器指向哪个项目未确认；GitHub 分支保护未核验 | `specs/readiness-check-2026-09-25.md:27-28,31,38-44`；`specs/contract-conformance-sprint1/lessons.md:8` | 上线前（安全） | 工程监督 | 由人填写生产标识；若为真实密钥则移出仓库并轮换；确认连接器只指向开发项目；开启主干 PR 审查 |
| B20 | **数据驻留与境内托管未定**：ADR 09（选项 C/A）、ADR 10（选项 B/A）未签；云厂商、ICP 备案主体、等保级别未定；`vendors.md` 没有任何供应商 | `docs/adr/09-data-residency.md:171-182`；`docs/adr/10-domestic-hosting-selection.md:98-103`；`docs/compliance/vendors.md:3,9` | 软·境内试点环境；硬2（第二轮渗透的 staging 依赖 ADR 10）；上线前 | 产品、技术负责人、合规 | 按 ADR 评审顺序先签 09，再签 04 / 10 |
| B21 | **短信供应商缺失**：staging、pilot、生产没有短信供应商时 BFF 启动即失败；删除二次确认的短信复核现有接口不支持 | `specs/unified-shell-sprint4/lessons.md:28`；`specs/sprint4-v2-rewrite/lessons.md:14`；`specs/sie-03-dispute-reconcile/lessons.md:221-224`；`specs/household-pentest-round2/spec.md:236-238` | 上线前 | 产品、合规 | 新增第三方走必停确认；staging 默认用捕获桩 |
| B22 | **进程内状态在多实例或重启下不一致，pilot 前须落库或换存储**：连续使用提醒计时（来源写明“pilot 前必须完成”）；导出包暂存在 BFF 进程内（“pilot 前须换成对象存储”）；删除确认的登录时刻记在进程内；洞察服务异议幂等是进程内缓存 | `specs/unified-shell-sprint4/lessons.md:224-226,263-264`；`specs/sie-03-dispute-reconcile/lessons.md:165-166,229-230,255-256` | 上线前 | 技术负责人 | `auth_session` 加列与两个函数；对象存储（新增存储服务，必停）；会话表记签发时间或新函数；持久幂等表（迁移） |
| B23 | **同意与监护**：同意文本版本缺省 `synthetic-0`，pilot / 生产拒收；同意表单不按年龄区分；任一家长撤回即关闭学生模式的口径；监护关系真实性只能线下核对；用途分项授权 `purpose_grant` 未建；试点同意书与用途授权是 W13 寒假封测包的组成部分 | `specs/unified-shell-sprint4/lessons.md:45,220,235,282`；`specs/e2e-integration-sprint5/lessons.md:74-75`；`specs/learner-schema-v0.9/spec.md:91`；`specs/behavior-events-sprint2/lessons.md:44`；`specs/plan-v2-alignment/spec.md:79-80` | 上线前（寒假封测包） | 合规 | 合规轨出同意书模板与版本号；`purpose_grant` 在合规给出用途分项方案前不建表 |
| B24 | **迁移 0010–0036 全部是草稿，未在任何共享环境执行**；其中有上线顺序硬约束（0016 先于学习服务、0030 与 BFF 同时上线等）。特别是 0031 执行前，攻击面 C3 的手机号枚举（`lookup_accounts_by_phone`）与免验证码注册（`signup_guardian`）仍对 BFF 开放，Gate 0 与 pilot 环境须确认 0031 已执行 | 各迁移文件第 2–3 行；`specs/learner-schema-v0.9/spec.md:93`；`specs/unified-shell-sprint4/lessons.md:43,110`；`specs/household-pentest-sprint3/spec.md:100`；`specs/plugin-api-v1.0-step2/lessons.md:31-32`；`specs/unified-shell-sprint4/lessons.md:87,151` | 上线前；硬1–硬5 的真实环境复现 | 技术负责人 | 按第四节 4.1 的顺序在副本演练（含 down），再由人执行 |

---

## 二、必停项待签字清单

每条一行，按 CLAUDE.md 必停类别分组；同一事项只列一次，放在主要类别下。

### 2.1 迁移与 RLS、权限（46）
- 迁移 0010–0036 逐个副本演练与执行（清单与顺序见 4.1）—— 各迁移文件头
- ADR 02 增补：个人层表留在 `platform.*`，`insight.*` 放个人事实层（含不接受时移回 platform 的替代方案）—— `specs/plan-v2-alignment/spec.md:72-73`；`specs/sie-01-onboarding/lessons.md:19-20,36`（家长自由文本观察留在 `platform.guardian_report`，不在建档重复存放）；`specs/e2e-integration-sprint5/lessons.md:67`（私有副本扫描的个人层白名单随 ADR 调整）
- ADR 13 #1：学科服务读判断不按学生过滤，或加会话级学生过滤 —— `docs/adr/13-claim-ledger-single-writer.md:77`；`specs/sie-00-field-dictionary/lessons.md:95`
- ADR 13 #6：渗透环境是否加只继承 `starbuddy_insight` 的登录角色 —— `docs/adr/13-claim-ledger-single-writer.md:82`
- 授权与策略快照目录 `db/tests/expected/`、`db/bootstrap/roles.sql` 是否纳入必停项或 CODEOWNERS —— `specs/household-pentest-sprint3/lessons.md:83,87`
- 快照变更按 RLS 变更对待（每个动授权的 PR 附快照 diff）—— `specs/household-pentest-sprint3/lessons.md:83`
- B5 学生自签邀请 / 同号多家长账号绕过“签发人不能兑换自己的邀请” —— `specs/household-pentest-sprint3/lessons.md:95`；`specs/learner-schema-v0.9/spec.md:90`
- 注册与建档的数量上限（每位家长的学生数）—— `specs/learner-schema-v0.9/spec.md:92`
- 学生能否解除家庭创建者的绑定、被解除的创建者仍能读家庭记录 —— `specs/learner-schema-v0.9/spec.md:82,94`
- 联调只读核对登录（BYPASSRLS 或定义者只读视图）—— `specs/e2e-integration-sprint5/lessons.md:71-73`
- 0028 补授 `platform.attempt.response_evidence_id`（列级）给洞察服务 —— `specs/sie-02b-claims-first-report/lessons.md:63`；`specs/sie-03-dispute-reconcile/lessons.md:121,174`
- 0028 追加 `kge.item_tag` 列级只读给洞察服务 —— `specs/sie-02b-claims-first-report/lessons.md:64-65`
- 0010 增加原因码（如 `basis_downgraded`），替代借用 `fact_corrected` —— `specs/sie-02b-claims-first-report/lessons.md:58,71`
- 0032 增加“验证低于所述”的结案流转 —— `specs/sie-03-dispute-reconcile/lessons.md:135-137`
- `link_id` 传入 `insight.raise_dispute`；授删除清单读权限；持久幂等表 —— `specs/sie-03-dispute-reconcile/lessons.md:119,131-132,174`
- 删除后家长侧写入（家长观察、考试安排）是否统一检查 `erased_at` —— `specs/sie-03-dispute-reconcile/lessons.md:53`
- 导出兑换库内留痕函数（如 `platform.redeem_export`）—— `specs/sie-03-dispute-reconcile/lessons.md:251-253`
- 删除确认的会话签发时间落库（会话表加列或新函数）—— `specs/sie-03-dispute-reconcile/lessons.md:255-256`
- 只核对不登录的短信复核函数（如 `auth_otp_step_up`）—— `specs/sie-03-dispute-reconcile/lessons.md:223-224`
- 审核台读过程原文的定义者函数（如 `platform.review_work_text`，附审计与个人信息遮蔽）—— `specs/sie-02a-math-diagnosis/lessons.md:99-103`
- 家长对原始证据（含诊断与过程原文）的读权限收窄（见 B17）—— `specs/sie-02a-math-diagnosis/lessons.md:109-111`；`specs/learner-schema-v0.9/spec.md:84`
- `auth_session` 加连续使用计时两列与 `auth_session_touch` / `auth_session_ack_rest` —— `specs/unified-shell-sprint4/lessons.md:224-226`
- 会话、验证码挑战、限流计数、过期 staff 会话的定时清理函数（只授调度角色）—— `specs/unified-shell-sprint4/lessons.md:42`；`specs/release-gate-sprint4/lessons.md:153`
- `meter_guard_status` 返回默认宵禁与留存态自练次数；新增只读的选科预判函数 —— `specs/unified-shell-sprint4/lessons.md:265-267,270-271`
- 0035 清单修正：staff 四表守卫、`trial_claim`、两张认证表 —— `specs/sie-03-dispute-reconcile/lessons.md:319-327`
- 0036 复核任务学科取首句判断学科，或放开 `review_task.subject` 可空 —— `specs/trust-ui-sprint5/lessons.md:127-128`
- 节点—课标承接表（见 B09）—— `specs/kge-03-compile-layer/lessons.md:39`
- 道法投影 verified 口径、页码核对签字函数 `kge.sign_projection_check`、时政摘要列 —— `specs/kge-daofa-slice-sprint4/lessons.md:67-74`
- `kge.item_tag` 加题型列 —— `specs/mastery-estimate-sprint4/lessons.md:50`
- `rubric.get` 查历史地图版本需放开 0026 对非发布版本 `scoring_point` 的 RLS —— `specs/kge-03-compile-layer/lessons.md:257-258`
- `starbuddy_kge_read` 是否加 `platform.item(admission)` 列权限 —— `specs/kge-03-compile-layer/lessons.md:99`
- `kge.has_personal_key` 词表扩到与契约检查同范围并逐字比对 —— `specs/service-openapi-v0.9/lessons.md:148`
- kge 证据包与自由文本的内容边界（顶层键白名单、最小人数、禁 uuid、去标识易错实例）—— `specs/kge-schema-v0.9/lessons.md:114-118`
- 已发布内容版本是否只能引用已发布地图版本；投影是否单独版本化 —— `specs/kge-schema-v0.9/lessons.md:119-132`
- 语文“层次递进边”用 prerequisite+weak 还是新增边类型 —— `specs/kge-schema-v0.9/lessons.md:135`
- `item_tag` 地图版本与题目内容版本一致性；题目主考点两处存放（`platform.item.skill_local_id`）—— `specs/kge-schema-v0.9/lessons.md:124-129`
- `starbuddy_review` 继承复核队列读权限，是否另建只签字的组角色 —— `specs/release-gate-sprint4/lessons.md:69`
- 只返回已签人数的函数；其他审核对象风险下限；已发布版本上新增题目族与采分点是否走审核 —— `specs/release-gate-sprint4/lessons.md:34,39,106`
- 撤下是否需两人、staff 是否细分角色、发布人能否是签字人 —— `specs/release-gate-sprint4/lessons.md:35,38`
- 复核队列：停用领取人的任务放回函数；抽检加盐；学生指针改 HMAC 与密钥管理 —— `specs/review-queue-sprint2/lessons.md:42,73-76`
- 复核结案与新判分绑定 / 改判放行是否要求 `regrade_required`（同属判分口径）—— `specs/review-queue-sprint2/lessons.md:40`；`specs/learner-schema-v0.9/spec.md:95`
- 同一判断多轮异议：唯一约束或由 `insight.dispute` 建多条 —— `specs/review-queue-sprint2/lessons.md:41`
- `platform.curriculum_version` 是否加“适用年级范围”或按年级拆三个课程版本 —— `specs/adapter-aya-sprint1/lessons.md:13-17`
- 行为事件与探针的保留期 —— `specs/behavior-events-sprint2/lessons.md:44`
- 政策库：直接发布路径是否收口到定义者函数；`source_domain` 日常维护归属；BFF 读往年已撤下方案；届别真相源与更正渠道；控制线粒度 —— `specs/policy-schema-v0.9/lessons.md:61-66`
- 计量流水分区建分区时顺手开 RLS（改 0004 共享函数）—— `specs/metering-schema-sprint1/lessons.md:89`

### 2.2 契约（packages/contracts）（33）
- 插件 API v1.0 评审会 61 项签字；迁移期 W6 是否合适；v1.0 引用服务契约 v0.9 的引用链 —— `specs/plugin-api-v1.0/lessons.md:48,53-54,51`
- v0 契约补 `IDEMPOTENCY_KEY_SEALED`；重放接口改 POST —— `specs/plugin-api-v1.0-step2/lessons.md:35-36,41`
- 迁移期 5xx 语义（v0 BFF 仍按旧语义回滚，可能少计）—— `specs/plugin-api-v1.0/lessons.md:50`
- `learner_context` 按方法限定字段（个人层推理只用境内已备案模型）—— `specs/plugin-api-v1.0/lessons.md:49`
- 作图语义操作表由谁定 —— `specs/plugin-api-v1.0/lessons.md:55`
- `TeachRequest` 加“作答已提交”信号（放开 4–5 级提示）；“第 5 级”内容 —— `specs/adapter-aya-methods-sprint4/lessons.md:84-89`
- `TeachResult.withheld_reason_code` 或 `ReviewReason` 新值 —— `specs/adapter-aya-methods-sprint4/lessons.md:111-113`
- “待复核、暂不给分”的单列表示 —— `specs/adapter-aya-sprint1/lessons.md:18-20`
- Problem 编码 `UNAUTHENTICATED` / `FORBIDDEN`；拍照模式用 `DISCIPLINE_NOT_SUPPORTED` 还是 `INVALID_REQUEST` —— `specs/adapter-aya-sprint1/lessons.md:21-22,40-41`；`specs/kge-03-compile-layer/lessons.md:263`
- 模型判分的专门复核原因码；忙碌码；L3 用 `DATA_CLASS_VIOLATION` —— `specs/adapter-zhixing-sprint2/lessons.md:32-33,64-65`
- `DiagnoseRequest` 携带既往逐点得分 —— `specs/adapter-zhixing-sprint2/lessons.md:30-31`
- `AttemptInput.evidence_level`（或学习服务转发 ingest 等级）—— `specs/adapter-wenli-sprint2/lessons.md:39-41,53-55`
- `subject_category` pattern 放宽或加 `kge_error_code` —— `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:55`
- 文砺 report 量表逐维统计（或平台报告引擎直读 `grade_dimension`）—— `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:56-57`
- `skill_summary.trend` 改可选或加 `unknown` —— `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:63`
- `present_item` 只读题面接口；诊断内部接口并入 `learning-internal.v1.yaml`；`IngestContext.purpose` 加 `diagnosis`；`item.search` 翻页 —— `specs/sie-02a-math-diagnosis/lessons.md:84-85,104-106,112-113,126`
- grade 摘要加只读溯源字段；道法 process 诊断入口；学生年级与内容版本不一致是否拒绝 —— `specs/e2e-integration-sprint5/lessons.md:54-56,61-63`
- `POST /internal/v1/plan` 形状 —— `specs/unified-shell-sprint4/lessons.md:231`
- 知识服务：`PageGetResponse.atom_refs` 补类型；SourceVersion 新增 `pages`；`page_id` 映射写入描述；旧版页面的 `source_version` —— `specs/kge-03-compile-layer/lessons.md:262-265,272`
- 知识服务 MCP outputSchema（oneOf）—— `specs/kge-03-compile-layer/lessons.md:104`
- 洞察服务：`insight.record` 写入路径或移出 v1.0；首份报告取用接口 —— `specs/sie-02b-claims-first-report/lessons.md:59-60,70`
- 洞察服务：预测与对账卡给 BFF 的只读工具（如 `insight.exam_view`）—— `specs/sie-03-dispute-reconcile/lessons.md:225-228`
- 洞察服务：预测区间 `low` / `high` 与 `actual_score` 上限 150，而 `full_score` 允许 1–300，满分超过 150 时回 400 —— `packages/contracts/openapi/insight-service.v0.9.yaml:566,587-588,616`；`specs/sie-03-dispute-reconcile/lessons.md:159`；`specs/service-openapi-v0.9/lessons.md:141`
- 按学生查节点的读接口（节点查询目前靠只读 SQL）；复测调度契约（现为桩）—— `specs/e2e-integration-sprint5/lessons.md:57-60`
- 服务契约：`insight.context` 是否按任务列字段白名单；采分点在契约与迁移之间的对应（`subject_dimension`、`keyword_family`、`partial_credit_rule`）及 `partial_credit_rule` 写法 —— `specs/service-openapi-v0.9/lessons.md:140,149-152`；`specs/kge-03-compile-layer/lessons.md:268`
- 洞察服务：dispute 请求带事实 / 作答引用；`fact_ref` 标“不得转发”或改不透明句柄；异议撤回工具；冲突 / 证据不足错误码；export / erase 的确认码与异步形态；`insight.reconcile` 接受成绩行或考试安排 ID、学生 / 家长版对账卡没有读取工具（差异 4）；`insight.dispute` 契约允许编排层而库要求提出人（差异 9）—— `specs/sie-03-dispute-reconcile/lessons.md:157-173`
- 洞察服务：周报工具（`insight.weekly` 或扩展 `insight.claims`）—— `specs/trust-ui-sprint5/lessons.md:163`
- 洞察服务：“为什么”的 BFF 读取路由与响应形状 —— `specs/trust-ui-sprint5/lessons.md:40-50`
- 服务契约：服务特有错误码；不计次前提与父级 usage 引用；供应商枚举是否进契约；`mastery.*` 只给编排层 —— `specs/service-openapi-v0.9/lessons.md:139,142-147`
- `insight-service` 放宽 `full_score` 未升版本号 —— `specs/plugin-api-v1.0/lessons.md:38`
- 洞察服务 `BehaviorEvent` 加 payload 与探针事件 —— `specs/behavior-events-sprint2/lessons.md:47`
- 审核台接口 `review-console.v0.yaml` 是否进契约 —— `specs/review-queue-sprint2/lessons.md:52`；`specs/release-gate-sprint4/lessons.md:72`
- 结构字段 `referral` / `handoff` / `root_causes` 的正式字段名；英语 3-D 登记事项（L3 示例、`transcript.v0`、开放题 verdict）是否仍评审 —— `specs/diandian-cases-v0-sprint1/lessons.md:38`；`specs/adapter-echo-sprint3/lessons.md:33-39`

### 2.3 判分与掌握计算（含知识与打标口径）（20）
- KGE-04 数值签字（`aya.draft.json`）；结构性下限；偏差 3–8（证据口径、旧证据、题型组、原因编码）—— `specs/mastery-estimate-sprint4/lessons.md:38-45,49,54`
- 星亮看哪个认知层次；只因“内容未准入”而复核的结果是否计入；复测是否扣额度 —— `specs/sprint4-v2-rewrite/lessons.md:11`
- B 级证据权重经验值 —— `docs/adr/15-input-channels-and-answer-evidence.md:75`
- 过程题原文证据是否计入掌握（只计 grade_result 与作答引用）—— `specs/sie-02a-math-diagnosis/lessons.md:107-108`
- 诊断选题参数（确认题、题数、追溯深度）；数学过程 Rubric —— `specs/sie-02a-math-diagnosis/lessons.md:86-88,96`
- SIE 判断阈值（`establish.*`）与中圈指标窗口、映射、偏差阈值 —— `specs/sie-02b-claims-first-report/lessons.md:66-67`
- 文砺：字数规则、置信度常数、注入防护、B/C 级拍照作文加 `ocr_low_confidence`、证据最短长度、plan 掌握映射 —— `specs/adapter-wenli-sprint2/lessons.md:42-45,56-61,64`
- 知行：置信度常数、错因映射 disputed 项、rubric_version 是否升版、`scoring_points` 比对 node_ids 与 partial_credit、注入词表误伤 —— `specs/adapter-zhixing-sprint2/lessons.md:34-35,66-70`
- 知行 plan 常数（领域权重全为合成）；未测节点排诊断还是不排；文砺“同题型”按体裁 —— `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:58-64`
- 阿鸦 plan 参数照搬 Q1 推导值；plan / report 编码白名单 —— `specs/adapter-aya-methods-sprint4/lessons.md:102-105`
- 模型返回名与配置不一致时判分任务是否拒收或转人工；主备不一致转人工口径 —— `specs/model-router-sprint1/lessons.md:33`；`docs/adr/04-model-routing-by-data-class.md:125`
- 批改主备全失效时“先收下、排队、补判”是否要做 —— `specs/load-cost-baseline-sprint5/lessons.md:48-51`
- 总分与维度分之和是否在契约写明计分规则 —— `specs/learning-ingest-sprint1/lessons.md:31-32`
- 验证器 / Rubric 注册表（平台对照核实 `rule_ref`、`rubric_version`）：现在 `judged_by = verifier` 配任意 `rule_ref` 都按非主观、高置信落库，影响规则 3 的可核实性 —— `specs/learning-ingest-sprint1/lessons.md:28-30`；`specs/plugin-api-v1.0-step2/lessons.md:42-43`
- 错因跨科大类映射表四科教研签字；错因大类数目（6 还是 7）对齐 —— `docs/mapping/error-taxonomy.md:3,101-112`；`specs/kge-schema-v0.9/lessons.md:134`
- KGE-01：P 类映射；孤点前置；20 个节点边界；能力子节点 C 的登记方式；未导入 21 个旧编码进 `id_migration` —— `specs/kge-01-skeleton-import/lessons.md:40-46`
- KGE-02：A/B 级口径（`--rules-only-a`）；退到主题节点；多个主测节点；六段未定的 8 题；六段映射表；自然语言前置；小题节点上限 —— `specs/kge-02-atoms-tagging/lessons.md:56-62`
- KGE 覆盖门槛口径、报表是否含 B 级、`cognitive_level`、无处可标率分母 —— `specs/kge-02-atoms-tagging/lessons.md:76-80`
- KGE-03：句长上限、群体统计门槛、截断哈希位数；中风险是否改抽检；页面只列强前置 —— `specs/kge-03-compile-layer/lessons.md:40,43,100`
- 4-K：时政上限与提醒、时政白名单与 `policy.source_domain` 是否共表 —— `specs/kge-daofa-slice-sprint4/lessons.md:77-79`

### 2.4 判断模板、禁用推断词表、画像可见范围（22）
- 禁用推断词表签字，并补“粗心 / 马虎”“名次与录取概率”、英文缺项（见 B13）—— `specs/sie-00-field-dictionary/lessons.md:88`；`specs/sie-03-dispute-reconcile/lessons.md:328-330`
- 判断模板（知识掌握、知识迁移、中圈指标、考试、对账）文案与编码映射；判断默认有效期 —— `specs/sie-02b-claims-first-report/lessons.md:68-69`；`docs/adr/13-claim-ledger-single-writer.md:80-81`
- 对外模板白名单 `external_claim_templates` —— `specs/unified-shell-sprint4/lessons.md:234`；`specs/trust-ui-sprint5/lessons.md:145-146`
- 推荐理由编码分类与编码—模板映射 `rationale_templates` —— `specs/unified-shell-sprint4/lessons.md:232-233,272-273`
- 家长孩子页今日计划（节点名、任务类型）是否越过规则 11 —— `specs/unified-shell-sprint4/lessons.md:281`
- `inner.g2.exam_prediction` 与 `inner.g3.error_attribution` 对学生、家长的可见范围 —— `specs/sie-00-field-dictionary/lessons.md:90-92`
- 对账判断对学生、家长可见（`reconcile_visibility` / `card_audience`）；高于区间不出卡 —— `specs/sie-03-dispute-reconcile/lessons.md:145-148`；`specs/trust-ui-sprint5/lessons.md:73`
- 字段默认可见范围四处（异议、家校沟通自由文本、补习情况、上传原件）—— `specs/sie-00-field-dictionary/lessons.md:97`
- 待验证判断对前端可见（虚线不是陈述）；学生端看待验证的“为什么”、预测与对账卡 —— `docs/adr/13-claim-ledger-single-writer.md:78`；`specs/trust-ui-sprint5/lessons.md:72`
- 能力视图是否对家长显示（与字段字典“只给 agent”冲突）—— `docs/adr/11-star-map-unified-definition.md:71`；`specs/sprint4-v2-rewrite/lessons.md:14`
- 审核台详情显示判断的全部可见范围（含只对 agent 可见）—— `specs/review-queue-sprint2/lessons.md:71-72`
- 周报：句子随判断修订消失是否可接受；置信度分档沿用快照；周报不进导出包；抽检是否看客观栏；“下一步”等三段需新模板 —— `specs/trust-ui-sprint5/lessons.md:131-135,140,157,162`
- 周报标题、衔接短语、排除词表；首份报告提示语与报告检查词表 —— `specs/trust-ui-sprint5/lessons.md:152`；`specs/sie-02b-claims-first-report/lessons.md:68`
- 档案页一致性检查的程度词、结论词、衔接短语、人设词表正式版 —— `specs/mastery-estimate-sprint4/lessons.md:53`
- 外显编号（知识点编号、依据版本、回执表名）是否改为名称与按范围合并 —— `specs/trust-ui-sprint5/lessons.md:79-81`；`specs/sie-03-dispute-reconcile/lessons.md:242`
- 家长导出范围（不含学生自述、异议、行为事件等）—— `specs/sie-03-dispute-reconcile/lessons.md:54`
- 同一孩子的两位家长互看考试记录（含排名区间）—— `specs/sie-01-onboarding/lessons.md:26`
- 建档自由文本是否过禁用推断词表“只标记” —— `specs/sie-01-onboarding/lessons.md:21`
- 去向引擎陈述走判断账本；家庭目标与“我的方向”可见范围（阶段 1 去向层 spec）—— `specs/plan-v2-alignment/spec.md:177-183,195-204`
- 情感依赖识别只在会话内处置、不入判断账本 —— `specs/plan-v2-alignment/spec.md:372-375`
- 学习服务可读全部家长自述（报告外层字段），是否按科或按用途限制 —— `specs/learner-schema-v0.9/spec.md:85`
- 公共层自由文本是否发布前过禁用推断词表 —— `specs/service-openapi-v0.9/lessons.md:138`

### 2.5 人设（Prompt、语料、禁语表、转介话术）（14）
- 禁语词表 A–E 迁移与 F:math、F:chinese、G、platform 种子逐条核对，`reviewed` 改 true（`manifest.yaml` 仍为 false）—— `specs/persona-governance-sprint3/lessons.md:104`；`packages/personas/forbidden/manifest.yaml:6`
- 登记表判定依据与合同对照表草稿 —— `specs/persona-governance-sprint3/lessons.md:105`
- 规格冲突 (a)–(e)：转介对象、F 类重名、“砺石”、阿基与 X 博士无规格、考官型披露句超长 —— `specs/persona-governance-sprint3/lessons.md:93-100,109`
- `platform` 类名、G 类收 §5.1/§2.1 三条、近似规则阈值与 `reveal_level` —— `specs/persona-governance-sprint3/lessons.md:106-107`
- 人设 Prompt v0（仓库内尚无）与回复格式、用例上下文注入方式、Prompt 版本号 —— `specs/diandian-consistency-r1-sprint2/lessons.md:14-17`
- 后处理替代句 `safe_reply`；`disclosure` 是否为跨科换句、与登记表 `disclosure.text` 统一 —— `specs/diandian-consistency-r2-sprint3/lessons.md:51-53`
- 情感信号规则 `signals.yaml`（`reviewed: false`）—— `specs/diandian-consistency-r2-sprint3/lessons.md:54-57`
- 危机情形是否升级（人工与监护人通知）及流程话术 —— `specs/diandian-cases-v0-sprint1/lessons.md:47`；`docs/adr/04-model-routing-by-data-class.md:127`
- 星图句式边界、交还对象、已知误报 —— `specs/diandian-cases-v0-sprint1/lessons.md:37,39-40,46,48`
- 测试夹具里的禁语探针短句不算人设语料 —— `specs/diandian-consistency-r1-sprint2/lessons.md:29-30`
- 留出集由教研负责人重新封存（构建方已接触 `holdout.txt` 与 `referral.yaml`），之后同步 `HOLDOUT_FROZEN_COUNT` —— `specs/diandian-cases-v0-sprint1/lessons.md:42-43,51-53`；`specs/diandian-consistency-r2-sprint3/lessons.md:55-56`；`specs/diandian-consistency-r1-sprint2/lessons.md:39-40`
- 面向学生、家长的文案人设评审：探针、诊断、登录与壳、信任界面、周报、SIE-03 配置文案、道法学生读本入口说明 —— `specs/behavior-events-sprint2/lessons.md:43`；`specs/sie-02a-math-diagnosis/lessons.md:94`；`specs/unified-shell-sprint4/lessons.md:52,156,255`；`specs/trust-ui-sprint5/lessons.md:70`；`specs/sie-03-dispute-reconcile/lessons.md:181`；`specs/kge-daofa-slice-sprint4/lessons.md:75`
- 判分 Prompt（`zhixing-m3-grade@0.2.0`、`wenli-essay-grade@0.1.0`）与后处理规则 —— `specs/adapter-zhixing-sprint2/lessons.md:37,70`；`specs/adapter-wenli-sprint2/lessons.md:45`
- KGE 编译 Prompt 是否另设回归 —— `specs/kge-03-compile-layer/lessons.md:266-267`

### 2.6 第三方服务、模型供应商与数据出境（17）
- ADR 04 增补：九类任务首批主备供应商；OCR/ASR 不适用备案的合规判断 —— `docs/adr/04-model-routing-by-data-class.md:122,126`
- 开发环境用合成数据调用境外模型的例外写进 ADR 04 或 09 —— `docs/adr/04-model-routing-by-data-class.md:123`；`specs/adr-11-15/lessons.md:15-16`
- staging 是否允许境外供应商处理合成数据（默认不允许）；dev 是否只允许已批准供应商 —— `specs/model-router-sprint1/lessons.md:35`；`specs/diandian-consistency-r1-sprint2/lessons.md:27`
- KGE V1.1“离线构建可用 Claude”与“一律不出境”的冲突；T8 全量重编的真实供应商 —— `specs/kge-02-atoms-tagging/lessons.md:82-84`；`specs/kge-03-compile-layer/lessons.md:260`
- 路由接口按学科设 (task, data_class) 白名单 —— `specs/adapter-wenli-sprint2/lessons.md:62-63`
- 供应商登记表 `vendors.md` 与路由表的 CI 自动对照 —— `specs/model-router-sprint1/lessons.md:36`
- 短信供应商（staging 用捕获桩或沙箱）—— `specs/household-pentest-round2/spec.md:236-238`
- 第二轮外部渗透测试机构（合同、保密、云厂商报备）—— `specs/household-pentest-round2/spec.md:233-235`
- 对象存储（导出包；将来的上传原件）—— `specs/sie-03-dispute-reconcile/lessons.md:229-230`
- 输入通道第三方识别（手写组件采购、OCR/ASR 候选）与样本集采集、个人信息保护影响评估 —— `docs/adr/15-input-channels-and-answer-evidence.md:77,80-81`；`specs/plan-v2-alignment/spec.md:163-164`
- 语音盲测执行环境、口音标签、删除时限、供应商留存门槛（随英语搁置）—— `specs/voice-blind-test-sprint4/lessons.md:36-43`
- CI 在 GitHub 托管 runner（境外）上运行；Actions 固定到提交哈希 —— `specs/contract-conformance-sprint1/lessons.md:46,59`
- CI 从 Playwright CDN 下载浏览器（境外、无完整性校验）—— `specs/star-map-sprint3/lessons.md:123,135`
- 依赖许可证与来源：certifi（MPL-2.0）、axe-core（MPL-2.0，测试）、caniuse-lite（CC-BY-4.0）、sharp 占位包、jieba 0.42.1、mcp 2.2.0 及传递依赖 —— `specs/learning-ingest-sprint1/lessons.md:18`；`specs/star-map-sprint3/lessons.md:70,128-130`；`specs/kge-03-compile-layer/lessons.md:261`
- 向量检索（pgvector、本地嵌入与重排模型）—— `specs/kge-03-compile-layer/lessons.md:161`
- 支付渠道与预收费监管（取得书面法律意见前不接）—— `specs/plan-v2-alignment/spec.md:330-337`
- 手机 APP 与原生 SDK（推送、支付、热更新）—— `specs/plan-v2-alignment/spec.md:342-355`

### 2.7 计量与权益（含预留、结算、回滚、幂等与扣次规则）（14）
- 路由接口不核对计量预留的“已接受风险”签字（见 B06）—— `specs/adapter-zhixing-sprint2/lessons.md:75-76`
- 转人工复核的批改是否扣次 —— `specs/load-cost-baseline-sprint5/lessons.md:60-62`
- 诊断预留有效期（调长或按段计量）、首周基线是否免计 —— `specs/sie-02a-math-diagnosis/lessons.md:89-92`
- 2-E 与 3-B、3-H spec 的具名确认人补签 —— `specs/metering-impl-sprint2/lessons.md:4,64`；`specs/household-pentest-sprint3/lessons.md:4`；`specs/star-map-sprint3/lessons.md:4`
- 试点名单维护与试点结束后转换；家长能否限制选科 —— `specs/metering-impl-sprint2/lessons.md:65-68`
- BFF 超时与学习服务同 key 等待时长不匹配；多额度桶合计够、单桶不够 —— `specs/metering-impl-sprint2/lessons.md:71-77`
- 筛查是否计量与付费档次数；过期时间与单次上限草案值；24 小时复看按学生去重；过期后迟到成功不扣费 —— `specs/metering-schema-sprint1/lessons.md:67-85`
- 对账视图巡检角色；`resource_type`、`plan_config` 写入走审批函数 —— `specs/metering-schema-sprint1/lessons.md:87-88`
- 计量 v2：核心包理解、学科改 live、宽限期额度、存储口径、初筛与错题额度 —— `specs/metering-v2/lessons.md:46-50`
- 计量 v2：试用领取记录删除与防刷保留、HMAC 密钥轮换、多位家长时间设置取最严、深度报告发起人、旧配置是否豁免宵禁 —— `specs/metering-v2/lessons.md:51-55`
- 试用 14 天转留存态、试用额度是否折算（规格 V1.2 对齐）—— `specs/plan-v2-alignment/spec.md:262-270,277-286`
- “每天 5 次自练”两处写死（0015 与 0030）是否抽成共用函数 —— `specs/unified-shell-sprint4/lessons.md:85-86`
- 可重试错误属于“结果未知”写进 ADR 06；洞察服务调用不逐次计量的前提 —— `specs/learning-ingest-sprint1/lessons.md:33-34`；`specs/service-openapi-v0.9/lessons.md:143-146`
- 评测调用不走平台计量预留；语音分钟计量（4-I 第二部分未开始）—— `specs/diandian-consistency-r1-sprint2/lessons.md:28`；`specs/voice-blind-test-sprint4/lessons.md:45-47`

### 2.8 不在七类之内、但须签字的 ADR 与流程项（未计入上面条数，单列备查）
- ADR 01–15 全部待签字；评审顺序见 `docs/adr/README.md:9-18,26-30`；各 ADR 第 5 节待人确认：01（`:93-97`）、02（`:82-86`）、03（`:77-80`）、04（`:86-90`、增补 `:121-127`）、05（`:83-86`）、06（`:82-87`）、07（`:79-83`）、08（`:99-104`）、09（`:171-182`）、10（`:98-103`）、11（`:70-74`）、12（`:62-66`）、13（`:76-82`）、14（`:64-69`）、15（`:73-81`）。
- ADR 14 #5：数据保存与删除策略、监护人同意书模板、路由主备切换、页面仓库分支保护四类是否纳入人审点 —— `docs/adr/14-task-packages-and-human-review.md:69`
- ADR 签字后另开 PR 的四件事 —— `specs/adr-11-15/lessons.md:24-28`
- `make contract-test` 默认是否改为 `CONTRACT_STRICT=1` —— `specs/sie-02a-math-diagnosis/lessons.md:124-125`

---

## 三、技术债（非阻断）

时机按开发计划第七章阶段 1 月度节点：1 月（寒假封测；软条件须在阶段 1 首月关闭）、2 月（语文切片、OpenMAIC 讲评原型）、3 月（四科内测约 1,000 户）、4 月（一模）、5 月（增长工程、2 倍峰值压测）、6 月（Gate 1 证据包）。

| 编号 | 事项 | 来源 | 影响 | 建议时机 |
|---|---|---|---|---|
| T01 | 发布闸门“四科各一个版本”口径；阿鸦格式 3 的第二部分（准入决定文件、平台来源编号列、`change_admission`）未做 | `specs/release-gate-sprint4/lessons.md:203`；`specs/release-gate-sprint4/spec.md:335` | 软·发布闸门版本记录 | 1 月 |
| T02 | 一致性夹具由实现会话起草，需独立会话或该科负责人复核；阿鸦夹具教研抽查，六类边界只覆盖三类 | `specs/adapter-aya-methods-sprint4/lessons.md:98`；`specs/adapter-wenli-sprint2/lessons.md:31`；`specs/adapter-zhixing-sprint2/lessons.md:21`；`specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:52`；`specs/contract-conformance-sprint1/lessons.md:39-45` | 软·契约覆盖率的可信度 | 1 月 |
| T03 | 平台侧核对与泄露检查只覆盖 grade、diagnose | `specs/contract-conformance-sprint1/lessons.md:61`；`specs/adapter-aya-methods-sprint4/lessons.md:91-92` | 软·契约覆盖率 | 1 月 |
| T04 | Makefile 非严格模式“跳过变绿”、跳过分支 `exit 0` 只结束一行（处理口径见 2.8 “`make contract-test` 默认严格模式”一条） | `specs/adapter-zhixing-sprint2/lessons.md:27,78`；`specs/voice-blind-test-sprint4/lessons.md:32-33`；`specs/contract-conformance-sprint1/lessons.md:83`；`specs/household-pentest-sprint3/lessons.md:89` | 本地检查结果可信度（CI 严格模式不受影响） | 1 月 |
| T05 | CI 首次真实运行未核验（Ubuntu 24.04 上的 postgresql-16、非 root 一次性库） | `specs/contract-conformance-sprint1/lessons.md:47` | CI 可信度 | 1 月 |
| T06 | 依赖锁文件与哈希校验（契约共享库、PyYAML 等未锁版本） | `specs/contract-conformance-sprint1/lessons.md:51-54` | 供应链 | 1 月 |
| T07 | 学习服务交付方式（无 Dockerfile；契约 YAML 只在可编辑安装下可用） | `specs/contract-conformance-sprint1/lessons.md:55-58` | 部署 | 1 月 |
| T08 | 人设家族级回归在 CI 跑两遍；前端单测仍读旧禁语词表位置（镜像待删） | `specs/persona-governance-sprint3/lessons.md:86,111-117` | 软·人设回归 CI；CI 时长 | 1 月 |
| T09 | 时序敏感测试（知行幂等“缓存满全是进行中”、诊断长流程 15 秒轮询、审核台水合） | `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:41-42,65`；`specs/unified-shell-sprint4/lessons.md:248`；`specs/review-queue-sprint2/lessons.md:85-86` | 偶发失败 | 1 月 |
| T10 | 联调样例须在干净工作区重生；KGE-04 第二步合入后去掉复测桩并重生 | `specs/e2e-integration-sprint5/lessons.md:33-36,82-83` | Gate 0 证据包可复现 | 1 月 |
| T11 | 三科的 `register-content.ts` 都生成写 `platform.*` 的 SQL（`adapters/aya/scripts/register-content.ts:34,38,45`、`adapters/wenli/scripts/register-content.ts:32,36,43`、`adapters/zhixing/scripts/register-content.ts:32,36`），与规则 1 字面冲突；lessons 只登记了阿鸦，知行与文砺是本次核对发现 | `specs/kge-schema-v0.9/lessons.md:137`；`specs/plugin-api-v1.0/lessons.md:52`；`specs/plugin-api-v1.0-step2/lessons.md:44` | 规则 1 | 1 月 |
| T12 | BFF 写入切到 `/internal/v1`；v1 信封 `channel`、`purpose` 由编排层填写，BFF 不透传前端字段 | `specs/plugin-api-v1.0-step2/lessons.md:33`；`specs/evidence-attributes-sprint1/lessons.md:45,49-50` | 证据等级可信度 | 1 月（v1.0 签字后） |
| T13 | v0 申诉不写能力观察（行为变更待确认）；地图版本两个来源不交叉核对 | `specs/evidence-attributes-sprint1/lessons.md:48,55` | 掌握口径 | 1 月 |
| T14 | 阿鸦 74 道提示含答案交教研改题；3 道答案词在题面内；legacy 两处典型错误答案等价 | `specs/adapter-aya-methods-sprint4/lessons.md:74-81`；`specs/adapter-aya-sprint1/lessons.md:64-69` | 内容质量 | 1 月 |
| T15 | 阿鸦 rejected / 授权 denied 题在 plan、teach 仍可能被用；非严格 C9 带标记的提示仍返回原文；C9 词表来源 | `specs/release-gate-sprint4/lessons.md:212-222` | 内容安全 | 1 月 |
| T16 | 阿鸦 `skill_links` 为空，plan 用不上前置；`latex` 作答 422 | `specs/adapter-aya-methods-sprint4/lessons.md:99-101`；`specs/plugin-api-v1.0-step2/lessons.md:37-38` | 功能缺口 | 2 月 |
| T17 | 渗透测试回归后续：超大适配器响应断言（F5）、部署后角色继承巡检（H2 / 0001 角色断言）、函数越权矩阵扩到复核与调度角色及 insight schema、写矩阵、按行主键比较 | `specs/household-pentest-sprint3/lessons.md:78,81,90-94`；`specs/sie-03-dispute-reconcile/lessons.md:67-68`；`specs/learner-schema-v0.9/spec.md:96` | 硬2 回归深度 | 1 月 |
| T18 | 流水与证据分区只建到 2027-12，续建依赖调度任务；渗透夹具 2028 年起失败 | `specs/metering-schema-sprint1/lessons.md:89`；`specs/household-pentest-sprint3/lessons.md:88` | 2028 年起写入失败 | 6 月前 |
| T19 | 双会话并发测试缺失（政策库发布锁、复核领取） | `specs/policy-schema-v0.9/lessons.md:57`；`specs/review-queue-sprint2/lessons.md:77` | 并发正确性 | 3 月 |
| T20 | 79p 对 `insight.confidence_probe` 谓词改坏漏检；0035 头注释“33 张”应为 32 张 | `specs/sie-03-dispute-reconcile/lessons.md:333,343` | 测试与文档准确性 | 1 月 |
| T21 | 前端运行时 DOM 文本核对与全站陈述—判断爬取（需假洞察服务） | `specs/sie-03-dispute-reconcile/lessons.md:341-342`；`specs/unified-shell-sprint4/lessons.md:239-240` | 硬5 证据深度 | 1 月 |
| T22 | WebKit、Firefox 上数据权利用例未跑 | `specs/trust-ui-sprint5/lessons.md:82-84` | 兼容性 | 1 月 |
| T23 | 视觉基线未签字、视觉回归不进 CI；半亮观感、对比度色值、959 颗星一屏放不下、学科显示名 | `specs/star-map-sprint3/lessons.md:117-127`；`specs/unified-shell-sprint4/lessons.md:149-150` | 视觉一致性 | 3 月（四科统一壳正式版） |
| T24 | 星图数据源从 `skill_node` 换成知识节点，加能力视图侧栏与待验证虚线（H5 / 星图 v1） | `specs/adr-11-15/lessons.md:26`；`specs/trust-ui-sprint5/lessons.md:51-52` | 规则 11 过渡条款 | 1–2 月 |
| T25 | 信任界面缺口：考试安排没有列表与节点选择；“数据有误”的原始事实选择与更正、异议撤回未做；结果 `verify_by` 不显示 | `specs/trust-ui-sprint5/lessons.md:53-57`；`specs/sie-03-dispute-reconcile/lessons.md:244-245` | 功能缺口 | 3 月 |
| T26 | BFF 接口口径：无权一律 403 与建档“一律 404”不一致；探针关联由学习服务自动回填；探针关联窗口 2 小时；词表检查脚本归位 | `specs/behavior-events-sprint2/lessons.md:45-49`；`specs/sie-02a-math-diagnosis/lessons.md:98` | 一致性 | 3 月 |
| T27 | SIE：中圈指标判断多数时间处于待验证；保持率分母用实际复测数；指标与周报调度未接；调度进程归属；`confirm_data` 由谁调 | `specs/sie-02b-claims-first-report/lessons.md:44,71,77`；`specs/sie-03-dispute-reconcile/lessons.md:179-180`；`specs/trust-ui-sprint5/lessons.md:142-143` | 中圈指标、对账节奏 | 3 月（中圈指标静默采集） |
| T28 | 异议的“粗心”同节点练习未做；“我其实会”在验证任务落地前停在待验证 | `specs/sie-03-dispute-reconcile/lessons.md:73,141` | 异议闭环 | 1 月（随 KGE-04 第二步） |
| T29 | 对账：偏差节点只来自调用方；调整只会是待验证或无；预测题型系数与地区权重缺省 | `specs/sie-03-dispute-reconcile/lessons.md:143-148` | 对账精度 | 3 月（月考首次对账） |
| T30 | 知识服务：快照 manifest HMAC 签名未实现；换快照须重启；检索参数未用金标调过；一致性指标口径；正确率判定 | `specs/kge-03-compile-layer/lessons.md:86,155-160,259` | 检索与快照完整性 | 2 月 |
| T31 | 补页面信号的存放与保留期、摘要密钥管理 | `specs/kge-03-compile-layer/lessons.md:158` | 运维 | 2 月 |
| T32 | KGE 导入：一次构建多专题；无网络环境运行导入器；打标器升版后旧候选过期函数；并发接受同题主考点 | `specs/kge-01-skeleton-import/lessons.md:47-49`；`specs/kge-00-asset-inventory/lessons.md:61`；`specs/kge-02-atoms-tagging/lessons.md:105-106` | 扩面 | 4 月（地图数据校准次版本） |
| T33 | M1 报告没有快照摘要，M2 只核对版本号；留出集执行人枚举只有教研负责人 | `specs/kge-m2-gate-sprint5/lessons.md:50-52` | 报表可核对性 | 1 月 |
| T34 | 道法：采分点只能挂观点（约定非约束）；判分 → CSV 转写脚本；道法打标并入 item_tag | `specs/kge-daofa-slice-sprint4/lessons.md:59-62` | 道法评测 | 1 月 |
| T35 | 审核台：撤下依据编码清单、逐条确认高风险差异、名称改动分不出错别字、地区投影风险级别、前端类型手写 | `specs/release-gate-sprint4/lessons.md:67-68,103-107` | 审核效率 | 1 月 |
| T36 | 复核队列：抽检调度频率；审核台看作答原文的单条授权 | `specs/review-queue-sprint2/lessons.md:43-44` | 复核 | 1 月 |
| T37 | 熔断状态在进程内；失败尝试用量未记录、超时换备选可能双重计费；调用记录 usage 哈希未加盐 | `specs/model-router-sprint1/lessons.md:31-32,37`；`specs/load-cost-baseline-sprint5/lessons.md:63` | 多实例与成本 | 5 月（增长工程） |
| T38 | 性能通过线未定；200 并发下连接池无数据；预发压测接入 | `specs/load-cost-baseline-sprint5/lessons.md:39-40,54,59` | 容量 | 5 月（2 倍峰值压测） |
| T39 | 单位经济首版：多科额度分摊口径、使用分布、价格表为示例价 | `specs/load-cost-baseline-sprint5/lessons.md:55-58` | 软·单位经济首版 | 1 月 |
| T40 | 讲解（teach）三科未实现，降级断言待补；诊断暂停可恢复的模型失效断言 | `specs/load-cost-baseline-sprint5/lessons.md:52-53` | 降级覆盖 | 2 月（讲评课原型） |
| T41 | 幂等记录、封存记录、行为事件的保留期与清理任务 | `specs/learning-ingest-sprint1/lessons.md:19`；`specs/plugin-api-v1.0-step2/lessons.md:34` | 存储增长、合规 | 3 月 |
| T42 | 同一证据被不同请求引用时推断挂在第一条证据上的设计确认；证据 ID 非全局唯一 | `specs/learning-ingest-sprint1/lessons.md:35-36`；`specs/learner-schema-v0.9/spec.md:87` | 数据一致性 | 3 月 |
| T43 | 政策库：审计表（随 PLT-01）；学科码改外键；考情数据服务接口；近 5 届回填排期 | `specs/policy-schema-v0.9/lessons.md:63,70-73` | 去向层前置 | 4 月 |
| T44 | 计量 v2 偏差：宽限期额度、存储按周期累计、设备指纹未记录、升级无支付单号 | `specs/metering-v2/lessons.md:18-21,43` | 权益完整性 | 6 月（支付与退款） |
| T45 | 试用到期当天显示、今日计划超时阈值、`insight.explain` 限频阈值 | `specs/unified-shell-sprint4/lessons.md:236-238` | 体验 | 1 月 |
| T46 | CSP `img-src data:` 可收紧；BFF 地址白名单只认 RFC 1918 IPv4 字面量；客户端 IP 头须由反向代理覆盖 | `specs/unified-shell-sprint4/lessons.md:47,148,152` | 安全加固 | 1 月 |
| T47 | 请求体注入 `account_id` 一律 422 与验收字面不同；`contact_support` 是否可区分；学生会话时长 | `specs/unified-shell-sprint4/lessons.md:44,46,49` | 鉴权口径 | 1 月 |
| T48 | 学习服务直读 `kge.*` 与“知识服务是 kge 读口”的分工待 ADR | `specs/unified-shell-sprint4/lessons.md:277-278` | 架构边界 | 2 月 |
| T49 | 服务间令牌过宽（诊断接口与写入、重放共用一枚） | `specs/sie-02a-math-diagnosis/lessons.md:113-114` | 最小权限 | 1 月 |
| T50 | 点点评测：两组数字同一次调用的口径、修订签字后需重跑、留出集汇总须带入、自助法次数统一 | `specs/diandian-consistency-r2-sprint3/lessons.md:58-65,43`；`specs/diandian-consistency-r1-sprint2/lessons.md:45` | 硬3 报告口径 | 1 月 |
| T51 | 点点用例覆盖缺口（双层架构、风格串味、情境角色边界、出场协议若干、记忆两类）；回复入库口径统一 | `specs/diandian-cases-v0-sprint1/lessons.md:44-45,49` | 人设回归覆盖 | 2 月 |
| T52 | 错因映射签字后各科适配器按表填写 `cross_subject_category`；ADR 14 签字后模板补两节 | `docs/mapping/error-taxonomy.md:110-112`；`specs/adr-11-15/lessons.md:25` | 跨科错因 | 1 月 |

### 2026-11-09 核对（上表写于 10 月初，之后未逐行更新；本节以文件与提交为准，上表不改行，便于对照）

“已完成”指本仓库内的工作已做完；“人”指要教研、法务、产品、签字或真实集群；“停”指剩余部分碰 CLAUDE.md 必停清单。

| 编号 | 现状 | 剩余 / 卡在 | 依据 |
|---|---|---|---|
| T04、T06–T09、T19、T20 | 已完成 | — | `specs/tech-debt-batch1/` |
| T05 | 已完成（主体） | 变异组（scope=mutation，约 500 计费分钟）拆分后尚未在 CI 跑过，由人决定何时触发；Actions 固定提交哈希（另一条） | CI run 42564677313；`specs/contract-conformance-sprint1/lessons.md` 第 47 行附注 |
| T12 | 已完成 | 迁移 0058 由人执行 | `specs/tech-debt-batch2/spec.md` |
| T18、T41 | 已完成（迁移草稿 0062、0063） | 副本演练后由人执行；调度时间表随签字 | `specs/plt23-data-maintenance/` |
| T21、T46 | 已完成 | — | `specs/tech-debt-batch2/` |
| T47、T49 | 已完成 | staging 新增 `session-token`；安全负责人过目 | `specs/plt24-auth-tightening/` |
| T10 | 已完成 | — | `tools/e2e-cohort/sample/run_metadata.json`（`worktree_dirty: false`） |
| T11 | 已完成 | — | 登记脚本迁到 `tools/content-register/`；`tools/content-register/tests/placement.test.ts` |
| T13 | 已完成 | 地图版本交叉核对已加；v0 申诉一条因 v0 下线关闭；申诉期间原证据是否临时降级另起〔人：掌握口径〕 | `specs/plt25-map-version-check/` |
| T35 | 部分完成 | 前端请求体与后端模型自动核对已做（`specs/review-console-types-t35`）；响应类型仍手写；撤下依据清单、逐条确认高风险差异等〔人〕 | `apps/web/tests/review-requests.test.ts` |
| T17 | 部分完成 | 函数越权矩阵已扩到全部应用 schema；部署后角色继承巡检（H2）〔人：真实集群〕 | `db/tests/80_attack_surface.sql` 第六节；`specs/tech-debt-batch3/` |
| T01 | 部分完成 | 第二部分（0039、0042、准入导出与走查）已做，发布门禁拒收非严格或带标记的 C9 检查；各科真实签字的版本记录〔人〕 | `specs/release-gate-sprint4/lessons.md`；`specs/tech-debt-batch3/` |
| T15 | 部分完成 | 发布门禁部分已做；C9 词表来源〔停〕；plan、teach 跳过 rejected 题〔人：方法行为变更〕 | 同上 |
| T22 | 已完成 | CI 手动开关 `extra_browsers`；2026-11-09 首跑 WebKit、Firefox 数据权利用例 4 项全部通过 | run 42637392388 的 extra-browsers job |
| T26 | 部分完成 | 词表检查并入 `sie-dictionary-check`、删去多余构建；403 / 404 口径〔人〕；探针关联窗口在 0025〔停〕 | `Makefile` |
| T34 | 部分完成 | 判分转写脚本已写；采分点挂观点的约束〔停〕；道法打标并入 item_tag〔人〕 | `evals/daofa-grading/transcribe.py` |
| T52 | 部分完成 | 模板已补“输入契约”“禁止修改的范围”；错因映射签字〔人〕 | `specs/_template/spec.md` |
| T24、T27、T30、T33、T38 | 部分完成 | 均卡在签字或人（ADR 11 / 13、契约 1.1.0、指标频率、留出集执行人、通过线） | 各自来源 lessons |
| T02、T03、T14、T16、T23、T25、T28、T29、T31、T32、T36、T37、T39、T40、T42–T45、T48、T50、T51 | 未做 | 均卡在人或必停 | `specs/tech-debt-batch3/spec.md` 第二节 |

---

## 四、环境与部署前置

### 4.1 迁移副本演练顺序（0010 → 0036）（27）

除 0015、0016 外，文件头都写“草稿，只写文件，由人（先在副本演练再）执行”；0016 见 `specs/plugin-api-v1.0-step2/lessons.md:31`，0015 见 `specs/metering-v2/lessons.md:6`。每步按单事务（`psql -1`），演练含 up → down → up。顺序按编号即满足依赖；下表只列依赖与演练注意。

| 迁移 | 依赖 | 演练注意 | 来源 |
|---|---|---|---|
| 0010 insight 判断账本 | bootstrap `starbuddy_insight` | ADR 13 同时签字 | 文件 `:2,12` |
| 0011 kge 知识地图 | bootstrap `starbuddy_kge`；库编码 UTF8 | 角色断言（kge 不能登录、authz 无成员） | 文件 `:2`；`specs/kge-schema-v0.9/lessons.md:72-75` |
| 0012 课程版本引用地图版本 | 0011 | 与 0011 同评审 | 文件 `:2` |
| 0013 policy 考情库 | 0011（复用 `starbuddy_kge`） | `source_domain` 由人登记 | 文件 `:2,11` |
| 0014 学生届别 | — | 数据迁移：先回填再改必填，副本演练 | 文件 `:2` |
| 0015 计量 v2 | 0008、0009 | 回填试用领取；`v12-pilot@1` 由人发布；down 在新资源已用时删不掉 | `specs/metering-v2/lessons.md:24,42` |
| 0016 重放封存 | 0007 | **必须先于本版本学习服务上线**（启动时检查封存表） | `specs/plugin-api-v1.0-step2/lessons.md:31-32` |
| 0017 证据补列 | 0011 | `source_type` 整表单事务回填，长时间持锁；分区表 BEFORE ROW 触发器需 PG13+ | `specs/evidence-attributes-sprint1/lessons.md:43,53` |
| 0018 签字人表 | 0011 | — | 文件 `:2` |
| 0019 kge 签字与发布 | 0011、0018 | 依赖 authz 无成员 | `specs/release-gate-sprint4/lessons.md:33` |
| 0020 staff 登录 | 0002、0009 | 人工建凭据前关语句与参数日志 | `specs/release-gate-sprint4/lessons.md:129` |
| 0021 建档事实（platform） | 0002 | — | 文件 `:2` |
| 0022 建档事实（insight） | 0002、0010、0021；bootstrap `starbuddy_report` | — | 文件 `:2`；`specs/sie-01-onboarding/lessons.md:4-5` |
| 0023 统一复核队列 | 0006、0009、0010、0020 | 先确认没有 `in_progress` 却无领取人的旧任务；枚举值回滚删不掉 | `specs/review-queue-sprint2/lessons.md:35-36,78` |
| 0024 kge 打标 | 0011、0019、0023 | 摘要输入变化，已有签字需重签 | 文件 `:2,8` |
| 0025 行为事件 | 0002、0004、0010、0022 | 保留期未定、不自动删除 | 文件 `:2,7` |
| 0026 知识服务只读角色 | bootstrap `starbuddy_kge_read` | — | 文件 `:2,12` |
| 0027 诊断会话 | — | 新枚举值同事务按 text 比较；回滚约束 | 文件 `:2,6,9` |
| 0028 洞察服务只读授权 | 0010 | — | 文件 `:2` |
| 0029 认证与会话 | 0002、0009 | 不在共享环境执行；登录角色关参数日志 | 文件 `:2-3`；`specs/unified-shell-sprint4/lessons.md:48` |
| 0030 计量只读函数 | 0002、0009、0015 | **须与 BFF 同时上线**，否则诊断入口 500 | `specs/unified-shell-sprint4/lessons.md:87` |
| 0031 收回旧登录入口 | 0002 | 先确认无其他调用方（运维脚本） | `specs/unified-shell-sprint4/lessons.md:151` |
| 0032 异议 | 0002、0004、0006/0023、0010、0022 | — | 文件 `:4` |
| 0033 触发规则动作记录 | 0002、0010 | — | 文件 `:2` |
| 0034 考试预测与对账卡 | 0002、0009、0010、0022 | down 在已有考试安排时失败（有意） | `specs/sie-03-dispute-reconcile/lessons.md:45` |
| 0035 个人层清单、删除与导出 | 0002…0034；bootstrap `starbuddy_eraser` | down 会删回执与导出任务记录，回滚前留档 | `specs/sie-03-dispute-reconcile/lessons.md:45-46` |
| 0036 周报 | 0002、0006/0023/0027、0010、0032、0035 | 79q 已补周报行 | 文件 `:4-5`；`specs/trust-ui-sprint5/lessons.md:150` |

另：KGE 种子 `db/seeds/kge/aya-fun-0.1.0/` 依赖 0011（`specs/kge-01-skeleton-import/lessons.md:44`）；`db/seeds/dev/*` 只进开发与一次性测试库（库名与开关双重保护，`specs/star-map-sprint3/lessons.md:131`、`specs/unified-shell-sprint4/lessons.md:276`）。

### 4.2 数据库角色 bootstrap（6）
- 每个环境先执行 `db/bootstrap/roles.sql`；新增角色 `starbuddy_insight`、`starbuddy_kge`、`starbuddy_kge_read`、`starbuddy_report`、`starbuddy_eraser`（NOLOGIN、BYPASSRLS，SIE-03 新增，范围外改动请确认）—— `db/bootstrap/roles.sql:30-45`；`specs/sie-03-dispute-reconcile/lessons.md:31-32,46`
- 登录角色由人创建，各只继承一个应用角色：BFF（非超级用户、无 BYPASSRLS、非表属主）、知识服务（只继承 `starbuddy_kge_read`）、洞察服务（只继承 `starbuddy_insight`）、审核台（签字连接与登录连接分开）—— `specs/star-map-sprint3/lessons.md:105`；`specs/kge-03-compile-layer/lessons.md:102`；`specs/sie-02b-claims-first-report/lessons.md:11-12`；`specs/release-gate-sprint4/lessons.md:130`
- 联调只读核对角色（BYPASSRLS 或定义者只读视图，能 `SET ROLE starbuddy_bff`）—— `specs/e2e-integration-sprint5/lessons.md:71-73`
- 首批 staff 凭据由谁建、TOTP 密钥交付（默认当面）—— `specs/release-gate-sprint4/lessons.md:147`
- 部署后的角色继承巡检（0001 / 0009 / 80 只在迁移或测试时检查）—— `specs/household-pentest-sprint3/lessons.md:81`；`specs/learner-schema-v0.9/spec.md:96`
- KGE-00 库模式盘点需专用只读角色与只读副本（`row_security = off`）—— `specs/kge-00-asset-inventory/lessons.md:51-52,67`

### 4.3 新密钥与服务间令牌（不进仓库，staging 以上必配）（11）
- `BFF_AUTH_SECRET`（会话、CSRF、日志 HMAC）—— `specs/unified-shell-sprint4/lessons.md:26-27`
- `BFF_TRUST_SECRET`（删除确认凭据、异议幂等键派生；staging 以上不设或太短拒绝启动）—— `specs/sie-03-dispute-reconcile/lessons.md:259-261`
- `BFF_INSIGHT_URL` / `BFF_INSIGHT_TOKEN` —— `specs/unified-shell-sprint4/lessons.md:187`
- 知识服务三枚令牌 `KNOWLEDGE_TOKEN_BFF` / `_ORCHESTRATOR` / `_LEARNING` —— `specs/kge-03-compile-layer/lessons.md:64`
- 适配器路由令牌与服务令牌两枚且必须不同（知行、文砺）；适配器令牌 ≥ 32 字符、非回环必须 https —— `specs/adapter-zhixing-sprint2/lessons.md:59`；`specs/adapter-wenli-sprint2/lessons.md:4-5`；`specs/learning-ingest-sprint1/lessons.md:37`
- 建议的诊断接口单独令牌 `LEARNING_DIAGNOSIS_TOKEN` —— `specs/sie-02a-math-diagnosis/lessons.md:113-114`
- 计量 `meter_secret`（迁移时库内随机生成；轮换与外移待 ADR 09/10）—— `specs/metering-v2/lessons.md:9,52`
- 复核队列学生指针密钥（若改 HMAC）—— `specs/review-queue-sprint2/lessons.md:73`
- 补页面信号摘要密钥（每实例随机或部署级固定）—— `specs/kge-03-compile-layer/lessons.md:158`
- 页面仓库编译器推送凭据、CI 检查工具制品地址与 sha256（`ci/kge-tools.pin`）—— `specs/kge-03-compile-layer/lessons.md:167,270-271`
- 模型供应商凭据只来自执行环境变量（运行器不持有 Key）—— `specs/diandian-consistency-r1-sprint2/lessons.md:6`

### 4.4 随部署携带的文件与配置（8）
- BFF 制品须带 `docs/design/sie/forbidden-inference.v0.yaml` 与 `docs/design/sie/sie_lexicon.py`（周报逐句词表检查；读不到即一句不显示）—— `specs/trust-ui-sprint5/lessons.md:158-159`
- 洞察服务制品须带 `services/learning`（`sys.path` 复用一致性检查）—— `specs/sie-02b-claims-first-report/lessons.md:54-55`
- 学习服务须带 `packages/contracts` 的契约 YAML（启动加载，缺失即失败）—— `specs/contract-conformance-sprint1/lessons.md:55-58`；`specs/evidence-attributes-sprint1/lessons.md:27`
- `services/bff/config/auth.json`、`config/shell.json`（白名单为空）；`BFF_AUTH_CONFIG`、`BFF_SHELL_CONFIG` 只在 dev/test —— `specs/unified-shell-sprint4/lessons.md:187,212-213`；`specs/unified-shell-sprint4/lessons.md:142`
- 洞察服务 `INSIGHT_SIE03` 非 test 环境必须指向已签字配置，否则拒绝启动；周报配置未签字时 test 以外不生成 —— `specs/sie-03-dispute-reconcile/lessons.md:123`；`specs/trust-ui-sprint5/lessons.md:143`
- 知识服务页面快照目录 `KNOWLEDGE_PAGE_SNAPSHOT`（换快照须重启）、`KNOWLEDGE_CACHE_DIR` —— `specs/kge-03-compile-layer/lessons.md:86,102,231-232`
- 掌握配置：未签字或 `synthetic` 的配置只允许 `LEARNING_ENV=test` 加载 —— `specs/mastery-estimate-sprint4/lessons.md:24`
- 同意文本版本 `STARBUDDY_CONSENT_VERSION`（缺省 `synthetic-0`，pilot / 生产拒收）—— `specs/unified-shell-sprint4/lessons.md:220`

### 4.5 运维配置（9）
- `STARBUDDY_ENV` 必须设置；`STARBUDDY_TRUST_UI`、开发页开关只在 dev/test —— `specs/unified-shell-sprint4/lessons.md:32`；`specs/sie-03-dispute-reconcile/lessons.md:195`
- Postgres 服务端日志：约束报错 DETAIL 可能含学生作答；BFF 登录角色与签字登录角色关语句与参数日志（参数含手机号原文）—— `specs/learning-ingest-sprint1/lessons.md:38-39`；`specs/unified-shell-sprint4/lessons.md:48`；`specs/release-gate-sprint4/lessons.md:129`
- 学习服务 uvicorn 关访问日志（重放 GET 的查询串含 key 与 `student_ref`），不经会记 URL 的代理 —— `specs/plugin-api-v1.0-step2/lessons.md:35-36`
- 反向代理：按来源 IP 限流 `/review` 下所有 POST；覆盖（而非追加）客户端 IP 头 —— `specs/release-gate-sprint4/lessons.md:138,148-150`；`specs/unified-shell-sprint4/lessons.md:47`
- BFF 地址白名单（staging 以上只认 RFC 1918 IPv4 字面量）—— `specs/unified-shell-sprint4/lessons.md:109,148`
- HSTS 只在 staging 以上；所有脚本设 `NEXT_TELEMETRY_DISABLED=1`；`apps/web` 只能用 `npm ci` —— `specs/unified-shell-sprint4/lessons.md:116,141`；`specs/star-map-sprint3/lessons.md:71,137`
- CI 必须 `CONTRACT_STRICT=1`；本地默认值是否改严格见 2.8 —— `specs/adapter-aya-sprint1/lessons.md:42`
- 休息提醒在未声明单实例部署时停用（`rest_reminder.single_instance`）—— `specs/unified-shell-sprint4/lessons.md:263-264`
- 页面仓库：托管平台、主分支保护、CODEOWNERS、CI 样例中的 SHA 与镜像 digest 占位须替换 —— `specs/kge-03-compile-layer/lessons.md:188-196,270-271`；`docs/adr/12-compiled-pages-git-pilot.md:63-66`

### 4.6 调度任务（尚未接调度，须定执行身份与频率）（4）
- 计量：过期回收、续期 `meter_renew_due`、分区续建 `ensure_monthly_partitions` —— `specs/metering-schema-sprint1/lessons.md:89`；`specs/metering-impl-sprint2/lessons.md:49`
- 复核抽检（按学科、按天、5%）与周报抽样 —— `specs/review-queue-sprint2/lessons.md:43`；`specs/trust-ui-sprint5/lessons.md:142-143`
- 洞察服务：预测冻结（考试当天零点后）、删除执行（冷静期满后 24 小时内）、`jobs.sweep` / `sync_mastery` / `recompute_metrics` / `sweep_disputes`、每周一周报 —— `specs/sie-03-dispute-reconcile/lessons.md:96,179`；`specs/sie-02b-claims-first-report/lessons.md:76-77`
- 学习服务掌握对账 `mastery-sweep`（第二步）；会话与验证码清理（待迁移）—— `specs/mastery-estimate-sprint4/lessons.md:65`；`specs/unified-shell-sprint4/lessons.md:42`

### 4.7 由人维护的数据（1）
- 试点名单 `platform.pilot_household`、学科上线状态（beta → live）、`policy.source_domain` 主机登记、额度配置发布、内容登记 SQL（pilot 与生产由人执行）—— `specs/metering-impl-sprint2/lessons.md:11,67`；`specs/metering-v2/lessons.md:23,47`；`specs/policy-schema-v0.9/lessons.md:37,65`；`specs/adapter-aya-sprint1/lessons.md:8`

### 4.8 Gate 0 证据重生（1）
- 0035 定稿合入、v1.0 签字收口候选开关、KGE-04 第二步合入时，分别按 `make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample` 在干净工作区重生，并在 PR 说明差异（见 T10、B01）。

---

## 附：本次遍历的来源
- 有 lessons 的 spec 目录 50 个（`specs/*/lessons.md`）；没有 lessons、只读了 spec 的：`adr-01-10`、`contracts-plugin-api-v0.9`、`error-taxonomy-v0.9`、`household-pentest-round2`、`learner-schema-v0.9`、`plan-v2-alignment`，以及 `inventory-*` 四科盘点（已于 2026-09-25 确认，未逐条复核）。
- ADR：`docs/adr/01`–`15` 状态与第 5 节；ADR 04 增补的待人确认。
- 迁移：`db/migrations/0010`–`0036` 的 up 文件头。
- 其他：`specs/readiness-check-2026-09-25.md`、`docs/compliance/vendors.md`、`docs/mapping/error-taxonomy.md`、`packages/personas/forbidden/manifest.yaml`、`docs/design/sie/forbidden-inference.v0.yaml`、`.claude/skills/gate-check/SKILL.md`。
