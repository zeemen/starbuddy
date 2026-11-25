# 待人事项 · 按负责人（2026-11-10 汇总，2026-11-21 更新）

> **2026-11-19 更新**：并入 11-16 至 11-19 各任务的待人事项（内测前整改、再练复测列表、信任界面表格、数据库超时、CI 供应链固定），
> 划掉已经做完的（Actions 固定哈希、契约依赖哈希锁、整改待确认 1–2）。新增或变化的行在“出处”后标 **〔11-19〕**。

在 `blockers.md`（2026-10-26）的基础上，合进之后各任务（KGE-12、技术债第二、三批、PLT-23、PLT-24、PLT-25、T35）仍开着的待人事项，
并划掉已经完成的。工程侧没有不依赖下面这些就能推进的工作了（W13 技术债的现状见 `docs/gates/w13-debt-and-decisions.md` 的“2026-11-09 核对”）。

- **加粗** = 阻塞 12 月内部成员测试。“出处”指向细节所在的 lessons / spec。
- 已完成、不再列：`.gitignore` 补 `node_modules/`（T35）；云上阶段 A、B（网络、RDS、OSS、日志、ACR）已建；CI 首次真实运行已核验（T05）；
  WebKit、Firefox 数据权利用例已在 CI 跑通（T22）。

## 一、技术负责人（代码评审与合并）

| 优先 | 事项 | 出处 |
|---|---|---|
| ~~1~~ | ~~审阅并合并分支 `claude/busy-edison-5365e8`（PR zeemen/starbuddy#28）~~：**已合并（2026-11-21，`main` 上 `9cff366`）**；审定评论与审查材料见 PR 与 `pr28-review-notes.md`。合并后仍要人做的：0062、0063 副本演练后执行，staging 新增 `starbuddy-learning` 的 `session-token` | PR #28 **〔11-21〕** |
| ~~2~~ | ~~合并前在 Actions 手动跑一次 `scope=mutation`~~：**已跑（2026-11-11，run 42976770389，提交 `22b07d3`）；合并前审查 S1–S2 改了 0062 与 80 号测试后重跑（2026-11-16，run 43798172361，提交 `562d206`，23 个 job 通过、extra-browsers 未开）**。之后再有改迁移、RLS、鉴权、计量的提交进 PR，合并前要再跑 | `specs/tech-debt-batch3/lessons.md` 末节 |
| ~~3~~ | ~~合并进默认分支后 SessionStart 钩子才生效~~：**已生效（2026-11-21 核对：`main` 上 `.claude/settings.json` 登记了钩子，脚本本机执行退出码 0、按锁文件跳过未变依赖）** | `blockers.md` 第一节 **〔11-21〕** |
| ~~4~~ | ~~GitHub Actions 固定到提交哈希（防供应链）~~：**已做（2026-11-19，`5e67e9a`）**，12 处 `uses:` 固定到提交哈希并有测试钉住 | `specs/ci-supply-chain-pins/` **〔11-19〕** |
| ~~5~~ | ~~契约测试依赖是否补带哈希的锁文件~~：**已做（2026-11-19，`5e67e9a`）**，`packages/contracts/requirements-dev.lock`，CI 与安装脚本按哈希装 | 同上 **〔11-19〕** |
| ~~7~~ | ~~Actions 升级到 Node 24 原生的新大版本~~：**已做（2026-11-20）**，四个 Action 都升到 v7 并仍固定哈希，setup-node 显式关自动缓存 | `specs/ci-actions-node24/` **〔11-19〕** |
| 8 | 计量负责人知情：处理请求的数据库连接池加了语句 10 秒、事务闲置 15 秒超时，计量事务超时会整体回滚、走已有失败路径（已测：预留超时不留预留、同一幂等键重试只一笔） | `specs/db-call-timeouts/lessons.md` **〔11-19〕** |
| 6 | WeKnora 与知识库供应链中台的规格与 ADR（要能读到中台代码：推到 GitHub 或经 Remote Control 共享）；staging 节点规格够不够跑 WeKnora | `specs/launch-lt03-cloud-staging/lessons.md` 第 20 条 |

## 二、平台 / 运维负责人

| 截止 | 事项 | 出处 |
|---|---|---|
| **定时间** | **恢复集群（阶段 C）**：测试阶段暂缓（决定记录第 18 条）；恢复时重新 plan、确认后 apply，之后是阶段 R、D 与第 4 节部署 | `specs/launch-lt03-cloud-staging/spec.md` 决定 18 |
| **W4** | **迁移 0010–0063 副本演练后执行**：0058 先于学习服务；0059 执行后处理判据对所有环境生效、三处内测开关一起打开；**0062 演练时一并评估“专用分区属主角色”，演练通过前不执行**；演练中确认 RDS 能否建 BYPASSRLS 角色、装 pgcrypto（不能则必停） | LT-04 1、LT-06 1、LT-03 3；`specs/plt23-data-maintenance/lessons.md` 末节 |
| **部署前** | **staging 密钥新增 `starbuddy-learning` 的 `session-token`**（随机 ≥ 32 字符、与 `service-token` 不同；按首次部署手册的 Secret 键表只核对名字与字节数） | `specs/plt24-auth-tightening/lessons.md` 末节 |
| 部署前 | 由人生成签字时间表 `schedule.signed.yaml`：partition-ensure 每日 03:10、retention-purge 03:40（批量 1000、上限 10000），model-call-purge 04:10（随教研 / 产品签字） | PLT-23 lessons 末节；`specs/plt-model-call-log/lessons.md` 第 7 条 |
| 部署前 | 知识服务镜像白名单加 `tools/weknora-probe`（打开 WeKnora 开关之前） | `specs/kge-12-weknora-integration/lessons.md` 第 7 条 |
| W4 | RDS 参数名、OSS 网关终端节点、NAT 边界防火墙、安全组类型以控制台核实；RDS 日志相关参数（七项是否可改、auto_explain、`log_min_error_statement`） | LT-03 3–4；`specs/plt-db-log-redaction/lessons.md` 第 1、4、5 条 |
| W4 | 取值：日志留存 180 天、OSS 生命周期、RDS 备份保留 14 天；SLS 里 HMAC 截断的留存与密钥轮换 | LT-03 6、LT-05 3 |
| W4 | provider 1.294.0 无 GPG 公钥、只靠锁文件哈希是否接受；`.terraform.lock.hcl` 另起 PR | LT-03 第 13 条 |
| W5 | ARMS、SLS 开通，采集组件命名空间与标签、日志 project；证书拨测从 VPC 内网发起 | LT-05 2 |
| W5 | 镜像签名：ACR 加签规格、cosign 私钥存放、验签是否联网初始化 TUF 根 | LT-03 5 |
| — | 空的旧分区由人在分区维护时处理（续建与留存清理都不删分区） | PLT-23 lessons 末节第 3 条 |
| W4 | RDS 参数组给各应用登录角色设更宽的全局兜底：`statement_timeout` 30 秒、`idle_in_transaction_session_timeout` 60 秒（调度器与迁移执行者的角色不设，它们有长查询） | `specs/db-call-timeouts/lessons.md` 待人确认第 1 条 **〔11-19〕** |

## 三、合规 / 法务（含模型供应商）

| 截止 | 事项 | 出处 |
|---|---|---|
| **W3–W4** | **百炼、方舟开通，数据处理协议，应用登记 / 准入依据，预算上限；按控制台填快照名、`allowed_hosts`、vendors.md 主机名，白名单批准** | LT-02 1 |
| **W6 前** | **监护人同意书、隐私政策、用途授权文本**（须覆盖学生提交反馈、退出测试后数据保留至测试结束）**；同意书版本登记** | launch-test-2026、LT-06 决定 2–3 |
| **W6 前** | **三份文本写同一组保留期**：写入幂等 30 天、验证码过期后 1 天、会话失效后 30 天、限流计数 2 天、行为事件 180 天、模型调用记录 180 天 | PLT-23 lessons 末节第 2 条；`specs/plt-model-call-log/` |
| W6 前 | ARMS、SLS 作为数据处理方的协议、地域、留存 | LT-05 2 |
| W6 前 | 学生本人单独确认同意（需新迁移或契约变更）；反馈是否进数据导出包；同意与退出记录留存期限；联系不上家长的删除路径 | LT-06 2、4、8 |

## 四、教研

| 截止 | 事项 | 出处 |
|---|---|---|
| **W4** | **签字：掌握配置与阈值、报告一致性词表、调度时间表**（签字前学习、洞察、调度器不部署） | LT-03 决定 9 |
| W6 | 内容终审；诊断会话续做是否加上限 | launch-test-2026、LT-04 2 |
| W6 | WeKnora 检索口径联调过目（条目缺出版社、版本、学期时按“不适用”处理是否合适）；出版社对照表（版本编码 → 全称）由谁提供、放哪里 | KGE-12 lessons 第 2、5 条 |
| 1 月 | 申诉期间原来那条 A 级证据是否临时降级（与掌握口径负责人一起定，另起任务） | `specs/plt25-map-version-check/lessons.md` 待人确认 |
| 1 月 | 阿鸦 74 道提示含答案的题改题；一致性夹具独立复核；错因映射表签字 | W13 T14、T02、T52 |

## 五、安全负责人

| 截止 | 事项 | 出处 |
|---|---|---|
| **合并前** | **过目 PLT-24（BFF → 学习服务拆成写入令牌与会话令牌、学生会话 12 小时 / 空闲 1 小时）** | `specs/plt24-auth-tightening/` |
| 合并前 | 复核 79p 与 80 号测试里新增的删除例外（模型调用记录留存、PLT-23 的五个保留期清理函数、0062 的唯一属主例外） | `specs/plt-model-call-log/lessons.md` 第 3 条；PLT-23 lessons“经验” |
| W5 | SW 缓存范围复核；健康 / 指标端点攻击面登记范围；RDS 错误日志可能含 CHECK 失败整行的访问与留存 | LT-07 8、LT-03 7、LT-06 10 |
| W5 | 白名单当前条目文件长期存放跳板加密盘是否可接受；作废前已签发验证码 ≤ 5 分钟的处置 | LT-06 5–6 |
| — | 0061 的四个计量函数只改了错误文本，是否仍需计量负责人单独签字 | `specs/plt-db-raise-redaction/lessons.md` 第 2 条 |
| 合并前 | 知情：日志字段白名单（安全审查 B）新增 7 个字段：闸门的 `gate_result`（原名 `gate`，11-21 改名）、`gate_subject`、`gate_queue_ms`、`gate_reason`，以及 `caller`、`node_id`（公共节点编码）、`cognitive_level`；取值全是固定枚举或公共编码，不含学生、账号与内容 | `specs/gate-log-fields/lessons.md`、`specs/log-field-audit/lessons.md` **〔11-21〕** |

## 六、产品 / 设计 / 运营

| 截止 | 事项 | 出处 |
|---|---|---|
| **W6** | **人写人审全部占位文案：家长端与 PWA（`apps/web/src/pwa/copy.ts`、离线页）、内测反馈与退出页（`TODO_COPY`）、学生端诊断入口与返回（`apps/web/src/diagnosis/copy.ts` 的 `DIAGNOSIS_ENTRY_COPY`）、做题等待时的过程状态（`apps/web/src/shell/copy.ts` 的 `PROCESS_STATUS`）、讲解白板指着元素提问的菜单与提示（同文件的 `STAGE_ASK`）；应用名** | LT-07 1、LT-06；`specs/diagnosis-student-entry/`、`specs/process-status/`、`specs/stage-point-to-ask/` **〔11-25〕** |
| W6 | 图标与主题色；16 张移动视觉基线签字；包体积阈值与 3G 首屏目标签字 | LT-07 2–4 |
| W6 | 视觉负责人在锁定环境里更新“错题再练 列表”桌面基线（102、103 两行改显示知识点名称；本机 Chromium 版本不同，判断不了） | `specs/practice-retest-list-polish/lessons.md` 待人确认第 1 条 **〔11-19〕** |
| ~~诊断上线前~~ | ~~诊断探针提交失败时的处理~~：**已定并实现（2026-11-22）**——可重试的（闸门被拒、限流、断连）留在原处、冷却后再给一次机会，其余照旧跳过；请 SIE 负责人知情 | `specs/diagnosis-probe-retry/` **〔11-22〕** |
| 1 月 | 再练、复测列表现在显示知识地图里的节点名（面向教研的命名）；编译层将来若提供学生可读的名称，再换来源 | 同上第 2 条 **〔11-19〕** |
| W6 | 告警阈值与 SLO 通过线签字（含新增的 WeKnora 退回 SQL、模型调用记录写库两类占位告警）；告警接收人、值守排班；内测手册默认值 | LT-05 1、LT-06 9；KGE-12 第 6 条 |
| W7 | WebKit 真机与真实微信内核对添加到主屏幕、离线回退、清缓存 | LT-07 5 |
| 1 月 | 试用到期当天显示、今日计划超时阈值、`insight.explain` 限频阈值 | W13 T45 |

## 七、可选 / 后续（不阻塞 12 月）
信任界面“为什么”证据表、考试对账卡里的知识点仍显示编号（换名称要动洞察服务契约，必停）**〔11-19〕**；
`blockers.md` 第六节各项；~~审核台响应类型的核对~~（已做，`specs/review-console-responses/`）**〔11-20〕**；考试预测与对账卡的端到端（要先补契约 `insight.predict` / `insight.reconcile` 给 BFF，必停；
“为什么”页那一半已于 2026-11-11 补齐，`specs/why-page-e2e/`）。
