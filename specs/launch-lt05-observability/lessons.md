# LT-05 可观测性：指标、日志、告警与备份恢复演练 · 实现记录

2026-10-23 由 Claude 推进（specs/launch-test-2026）。提交首行 `[NEEDS-HUMAN]`：新增云服务（阿里云 ARMS 托管 Prometheus、SLS）、新依赖（Python `prometheus-client==0.26.0`）、计量代码中加计数（BFF reserve / settle / release、学习服务写入前核对）、NetworkPolicy（指标端口只放采集组件）。仓库内无真实地址、凭据、告警接收人。不改契约、迁移、适配器代码。

## 交付
- **指标**：学习、洞察、知识、调度器、BFF、审核台后端各起**单独指标端口**（9787–9793，`*_METRICS_PORT`），只认 `GET /metrics`；业务端口、回环路由监听、调度器健康监听上的 `/metrics` 一律 404。标签白名单 8 个（service、route 模板、method、status_class、subject、task、provider、outcome），subject / task / provider / outcome 为枚举集合（枚举外记 other），写入前再拒 UUID、手机号、邮箱、长数字、原始路径。核心指标：请求数与时延、连接池、就绪、模型路由调用 / 供应商尝试 / 熔断 / 护栏剩余 / 估算成本、计量预留 / 结算 / 释放 / 409 / 结果未知、写入前核对放行与 409、复核积压（30 秒缓存、失败负缓存、最早等待按小时取整）、调度任务成败与最近成功。
- **BFF 指标**：自写 Prometheus 文本格式 0.0.4（计数器、直方图、仪表、转义），**不引入 prom-client**（已弃用且带 3 个传递依赖）；package.json / lockfile 与改动前一致。
- **计量计数**：BFF 在 `withAccount` 提交之后计数；所有计数（BFF、学习服务 `meter_checked`、`MetricsSink.write`、调度器 `record`、请求中间件）异常隔离；测试核对“计数在 COMMIT 之后”与“计数抛异常时计量结果逐项不变”，配套变异。
- **日志**：一行一个 JSON（ts、level、service、logger、route、request_id）；结构化字段**白名单**（信封字段 + 登记元数据键，键名先驼峰归一，白名单外整项丢弃），正文中手机号（允许空格 / 连字符）、邮箱、身份证、令牌样式串、连接串、UUID（request_id 字段为 UUID 时保留）替换；Python 与 BFF 逐项一致（有对照测试）；BFF 的 `student` / `account` 只收 16 位十六进制 HMAC 截断；每服务静态扫描禁止字段。
- **deploy/observability/**：`alerts.yaml`（阈值均为占位缺省、待人签字）、4 个 Grafana 仪表盘、`metrics-inventory.yaml`（各服务测试导出实际注册指标逐项比对）、`check_observability.py`（O01–O06，接入 deploy-check）；`make obs-promtool` 单列（严格模式要求 promtool，未接 check）。
- **IaC / k8s**：ack.tf 加 arms-prometheus 组件；SLS app 日志库只建白名单字段索引、不建全文索引，留存 180–365 天（R12 核对上下限）；check_iac 新增 R20（必须装采集组件、IaC 不建告警联系人）、R21（字段索引只收白名单，与 obs.py 同源）。`deploy/k8s/observability.yaml`：PodMonitor（`port: metrics`，只保留 `starbuddy_*`）与 AliyunLogConfig（只采六个后端主容器，`KeepSourceIfParseError: false`，日志库由 IaC 预建）；NetworkPolicy `metrics-from-collector` 只放行 9787–9793。
- **恢复演练**：`deploy/runbooks/restore_verify.py` + `weekly-restore-verify.md`：只收 REPLICA_DSN、主机显式放行、核对服务端地址与 staging-replica 标签、确认串；读业务表前核对角色（超级用户 / BYPASSRLS / 属主且无 FORCE），每条查询 `BEGIN TRANSACTION READ ONLY` + `SET LOCAL row_security = off`；校验行数与行序无关校验和（含存在的各科 schema），报告只含表名、行数、md5、服务端地址，0600、不覆盖；提示写标准错误。
- **同步**：env-inventory 登记 6 个指标端口变量；health.v0.yaml、config README 第 10 节、攻击面清单 P4、vendors.md“其他数据处理方”（ARMS、SLS 待开通）。

## 依赖与许可证
- Python `prometheus-client==0.26.0`：许可证实为 **Apache-2.0 AND BSD-2-Clause**（BSD 部分来自包内 decorator.py），无运行依赖；锁定 sha256，装入 learning / insight / knowledge / scheduler 的 .venv。
- Node：未引入任何依赖（prom-client 已卸载）。

## 测试
子代理逐目标（严格模式）：deploy-check（263）、staging-mutation（87 + 9）、learning-test 3854、insight / knowledge / scheduler 测试与变异、review-console-mutation、model-router-mutation、internal-v1-mutation、bff-test（含计量幂等）、bff-mutation（全量 448 漏 1 条，补用例后复跑可观测组 34 条全发现）、web-test、attack-surface-check、contract-test 通过。严格 `make check` 见提交说明。

## 审查意见的处理
- 安全审查阻断：SLS 采集未脱敏原文 → 只采六个后端主容器、解析失败行丢弃；脱敏黑名单可绕过 → 字段白名单 + 驼峰归一 + UUID / 分隔手机号；prom-client 弃用与传递依赖 → 用户决定自写文本格式。建议全部采纳（单独指标端口为用户决定；标签枚举、中间件异常隔离、复核积压负缓存与粗化、计量计数移到提交后、SLS 留存上限、拨测内网发起、restore_verify 只读与 RLS 核对）。
- 契约审查：无代码阻断；“严格 make check 本机过不了”是因审查环境缺契约依赖，严格检查环境已装齐。建议全部采纳（计数异常隔离、session 键对齐、告警描述口径、注释、变异目标不得在软链接目录）。

## 教训
- 日志脱敏必须是字段白名单，黑名单总会漏（驼峰键、泛化键、带分隔符的号码、正文 UUID）。
- 日志采集要按容器白名单，`KeepSourceIfParseError` 会让未脱敏的崩溃输出与第三方进程日志原样留存。
- 免鉴权的观测端点要单独端口、单独放行；同端口时命名空间内任意 Pod（含最靠近负载均衡的前端）都能读到经营与计量数据。
- 观测代码进入计量等关键路径时，必须在事务提交之后、且异常隔离，并用测试证明“计数失败不改结果”。
- 新依赖要看传递依赖与维护状态（弃用），而不只看直接许可证。
- 变异副本若缺依赖目录（BFF 副本未链接 deploy/），“变异被发现”可能只是读不到文件导致的失败，结论不可信；变异目标须断言不在软链接回主仓的目录下。

## 待人确认
1. **阈值与值守**：所有告警阈值与 SLO 通过线签字；告警接收人、通知渠道、值守排班在 ARMS 控制台配置。
2. **开通与登记**：ARMS、SLS 开通；数据处理协议、地域、留存由合规负责人填写（vendors.md 待开通）；ARMS 采集组件命名空间与 Pod 标签、AliyunLogConfig 的 project 与字段名、采集侧指标（kube-state-metrics、kubelet、拨测）以控制台为准；证书拨测须从 VPC 内网发起。
3. **学生 / 账号 HMAC 截断**在 SLS 留存 180–365 天：密钥轮换周期，以及轮换后旧哈希能否关联。
4. 复核积压“最早等待”按小时取整，告警阈值 48 小时，粒度是否合适。
5. SLS 解析失败行直接丢弃（会丢掉非 JSON 的进程级崩溃输出），是否改为单独短留存日志库。
6. 适配器与前端容器日志不进 SLS，是否另行采集（须先脱敏）。
7. 命名空间内互访仍放行全部端口，指标端口在命名空间内同样可达（与健康端点相同）。
8. 语音分钟预留、再练 / 复测计次未计入指标（语音模块“只导入计量模块”约束）。
9. 多实例部署时指标按实例计。
10. 恢复演练：行数减少一律失败（除非 `--allow-decrease` 列出）；报告保留 4 周，放跳板加密目录；推荐专用只读 BYPASSRLS 角色。
