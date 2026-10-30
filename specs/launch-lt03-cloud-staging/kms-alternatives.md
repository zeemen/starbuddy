# 不买 KMS 实例时的加密替代方案（草案，待用户确认）

依据：LT-03 决定记录第 14 条（2026-10-29，现阶段不买 KMS 实例；阶段 B 之前由 Claude 起草替代方案、用户确认后再改 IaC）、lessons 待人确认 15。
状态：**草案**。本文只是提议，IaC、k8s 清单、规则与测试都还没改；用户逐项确认后另起提交（首行 `[NEEDS-HUMAN]`）。

资料来源与局限：aliyun/alicloud provider 1.294.0 文档、ack-secret-manager 仓库文档（README、docs/advanced_usage.md）。
阿里云帮助中心（help.aliyun.com）不在本会话的网络放行名单里，**计费与产品能力只能由人在控制台或帮助中心核实**，下文标“需人核实”的都是这一类。

## 背景
`alicloud_kms_key.main` 现在有 5 处使用，`alicloud_kms_key.secrets` 只给凭据管家用。KMS 3.0 的自建密钥（软件密钥）与凭据管家都要先买 KMS 实例（决定记录第 12 条：约 2,499 元/月，以购买页为准）。
staging 按决定存放内部家庭（含未成年人）的真实数据（`data-class = personal-minor`），所以每处降级都写明残余风险。

“服务密钥”：各云产品自动生成、由阿里云托管的密钥（RDS、ECS 云盘、日志服务等都有）。数据在磁盘上仍是加密的，但我们**不能**自己轮转、禁用、删除这把密钥，也不能用密钥策略单独限制谁能解密；能读写资源的身份就能读到明文。

## 逐项方案

| # | 用途（现在） | 建议替代 | 残余风险 | 需人核实 |
|---|---|---|---|---|
| 1 | RDS 云盘加密（`encryption_key = 主密钥`） | **服务密钥 `ServiceKey`**。provider 文档：PostgreSQL 创建时 `encryption_key` 只收自建密钥 ID，不收 `ServiceKey`；建好后才能改成 `ServiceKey`。所以分两步：阶段 B 建实例时不设加密 → 紧接着一次只改 `encryption_key = "ServiceKey"` 的 plan / apply（此时库里没有数据），**在第 3 节执行迁移之前**核对云盘加密已开启 | 有一段很短的未加密窗口（空库）；密钥不由我们控制 | 切换服务密钥是否重启实例、子账号需要哪个 RDS 动作（首次 plan / apply 时按报错补，补的动作过测试）；服务密钥是否免费 |
| 2 | OSS 业务桶（`sse_algorithm = KMS` + 主密钥） | **OSS 完全托管加密 `AES256`**，与状态桶相同 | 同上：能读桶的身份就能读明文；访问控制靠 RAM 与桶的私有 / 阻止公共访问（这部分不变） | 无（AES256 免费，状态桶已在用） |
| 3 | 节点系统盘（`system_disk_kms_key = 主密钥`） | **ECS 云盘加密，服务密钥**：保留 `system_disk_encrypted = true`，去掉 `system_disk_kms_key` | 同上 | 不指定密钥时是否用免费的 ECS 服务密钥 |
| 4 | ESSD 存储类（`kmsKeyId` 占位） | 保留 `encrypted: "true"`，去掉 `kmsKeyId`（用 ECS 服务密钥） | 同上 | CSI 在不给 `kmsKeyId` 时用服务密钥加密 |
| 5 | 日志服务（SLS） | **不用改**：`encrypt_conf` 已是 `encrypt_type = "default"`（服务密钥） | — | — |
| 6 | ACK Secret 落盘加密（`encryption_provider_key = 主密钥`） | 两个选项，见下 | 见下 | 见下 |
| 7 | 应用凭据（凭据管家 + ack-secret-manager，专用密钥 `secrets`） | **OOS 加密参数 + ack-secret-manager**（`provider: oos`，RRSA 认证方式与凭据管家相同）；退路：由人在跳板用 kubectl 直接建 Secret | 见下 | 见下 |

### 第 6 项：ACK Secret 落盘加密
- **6a（建议，现阶段）**：`disable_encryption = true`，不做 Secret 的信封加密。Secret 仍在 ACK 托管的 etcd 里（由阿里云托管、不对我们开放）。
  补偿：只有部署者能读 `starbuddy-staging` 命名空间的 Secret（RBAC，原设计已有）；应用的 ServiceAccount 不挂令牌；Secret 的权威来源在集群外（第 7 项），集群内只是同步副本。
  以后买了 KMS 实例：ACK Pro 支持在已有集群上开启 Secret 落盘加密，届时只改这一项。
  残余风险：etcd 快照与备份里的 Secret 只经 base64，没有我们控制的密钥保护。
- 6b：用 KMS 3.0 的免费“默认密钥”做 `encryption_provider_key`。**需人核实**：默认密钥是否真的免费、ACK 是否接受它、provider 能否在没有实例时创建或引用它。核实可行再选 6b。

### 第 7 项：应用凭据
- **7a（建议）**：OOS 加密参数。由人在 OOS 控制台创建 11 个加密参数（名字与 `kms.tf` 的 `app_secrets` 一一对应，取值不进 Terraform 与仓库），集群里 ack-secret-manager 经 RRSA 读取并同步成同名 Secret。
  改动：`kms.tf` 的 `secret_reader` 策略从 `kms:GetSecretValue`（11 个凭据 ARN）+ 专用密钥 `Decrypt` 改为 `oos:GetSecretParameter`（11 个参数 ARN）+ 解密 OOS 服务密钥所需的最小动作；这一组 RAM 资源仍在**阶段 R 由人用管理员身份 apply**。k8s 的同步规则把 `kms` 换成 `oos`。
  **需人核实**：OOS 加密参数在没有 KMS 实例时能否创建、加密用哪把密钥、是否收费；ack-secret-manager 的 README 对 OOS 列出的权限里有 `kms:GetSecretValue`，读 OOS 加密参数到底需要哪些 KMS 动作，按首次同步的报错逐个补。
  残余风险：OOS 加密参数的密钥由阿里云托管，没有专用密钥这层隔离；能读参数的身份就能读明文（`secret_reader` 仍只列 11 个参数名）。
- 7b（退路）：不建同步，由人在跳板用 kubectl 从本地文件建 Secret（文件 0600、用后删除）。最简单，但轮换靠人，且需要部署者手里有明文。

## 需要连带改的地方（确认后一次改完）
- IaC：`rds.tf`（加密分两步，变量控制）、`oss.tf`（AES256）、`ack.tf`（`disable_encryption`、去掉 `system_disk_kms_key`）、`kms.tf`（两把自建密钥与别名移除或按变量不建；`secret_reader` 改 OOS）、`outputs.tf`（两个 KMS 输出）。
- `check_iac.py` 规则 R06 / R07 / R09 改成“必须加密，可以是服务密钥或 AES256”，不再要求自建密钥；变异测试同步（例如“RDS 云盘不加密”“OSS 不加密”仍须被发现）。
- k8s：`staging.yaml` 存储类去掉 `kmsKeyId`；同步规则改 `oos`；`test_static.py` 同步。
- 手册：阶段 K 改写；阶段 B 增加“RDS 改服务密钥”一步与验收；第 3 节迁移前核对 RDS 云盘加密；第 4.1 节凭据同步改 OOS；RAM 策略草案按需补 RDS 加密动作。
- RAM 子账号：OOS 相关写权限不给（参数由人建）；只按首次 plan 报错补 RDS 加密动作。

## 另一种选择：真实数据等 KMS
如果不接受上述残余风险，也可以先按本方案搭好 staging，但**只放合成数据**，等买了 KMS 实例、切回自建密钥之后再导入内部家庭的真实数据。代价是内部测试推迟。

## 请用户确认
1. 第 1–5 项按建议改（服务密钥 / AES256）。
2. 第 6 项选 6a（暂不加密 Secret）还是 6b（先核实默认密钥）。
3. 第 7 项选 7a（OOS 加密参数）还是 7b（kubectl 手建）。
4. 真实数据是否等 KMS 实例（上一节）。
5. 上表“需人核实”各项由谁在控制台或帮助中心核实。
