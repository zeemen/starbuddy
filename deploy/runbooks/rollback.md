# 回滚步骤（由人执行）

原则：先止损（摘流 / 回滚应用），再判断数据；能前向修复就不回滚迁移；回滚迁移必须先在副本上演练（`rds-restore-and-migrate-rehearsal.md`）；
按时间点恢复（PITR）是最后手段（会丢恢复点之后的写入）。每一步两人复核、留变更记录（不写学生数据）。

## 1. 应用回滚（镜像回到上一版摘要；不动数据库）
1. 从上一次流水线的 `dist/image-digests.txt`（流水线制品）取上一版各服务的 `<仓库>@sha256:<摘要>`；
2. 只回滚出问题的服务，按依赖倒序（前端 → BFF → 洞察 / 审核台 → 学习服务 + 适配器 → 知识服务；调度器单独）：
   ```
   kubectl -n starbuddy-staging set image deployment/<服务> <容器>=<仓库>@sha256:<上一版摘要>
   kubectl -n starbuddy-staging rollout status deployment/<服务> --timeout=300s
   ```
   或 `kubectl -n starbuddy-staging rollout undo deployment/<服务>`（只在上一个 ReplicaSet 就是要回到的版本时）；
3. 核对：每个 Pod 就绪（`/readyz` 200）、`kubectl get events` 无反复重启；前端经内网负载均衡能登录（白名单固定码账号）。
- BFF 与调度器是 `Recreate`（单实例：白名单 OTP 审计锁、调度互斥）：回滚期间短暂不可用属预期；BFF 新旧实例不能同时在（审计锁最多等 60 秒）。
- 上一版镜像要求的迁移版本不能高于库里当前版本（新版只加列、加表时旧版照常；删改了旧版依赖的对象就不能只回滚应用，见第 2 节）。

## 2. 迁移回滚（down；先在副本上演练）
适用：本次发布的迁移导致问题、且前向修复来不及。
1. 按 `rds-restore-and-migrate-rehearsal.md` 第 1–4 步恢复一个副本（`REPLICA_DSN`、mark-replica），在副本上演练 `down --to <回滚到的版本>` 与之后的应用回滚；
   记录 down 会丢的数据（删列、删表）并由人决定是否接受；
2. 停写：把 BFF 与调度器缩到 0（`kubectl -n starbuddy-staging scale deployment/bff deployment/scheduler --replicas=0`），确认没有在途写入；
3. 在主实例上执行（须批准，复核人核对确认串）：
   ```
   python3 deploy/migrate/migrate.py down --dsn-env PRIMARY_DSN --target-label staging --to <回滚到的版本> --confirm staging:down:<个数>:<回滚到的版本>
   ```
4. 回滚应用到与该迁移版本匹配的镜像（第 1 节），再把 BFF、调度器恢复到 1；核对 `/readyz`；
5. 学习服务启动前置（例如 0058）被 down 掉时，学习服务按设计拒绝启动、`/readyz` 不会就绪——这种情况不要 down 0058，改为应用回滚 + 前向修复。

## 3. 按时间点恢复（PITR；最后手段）
适用：数据被错误改写或删除，且无法用迁移或应用修复。
1. 停写（同第 2 节第 2 步），记下事故开始前的时间点 `<UTC 时间>`；
2. RDS 控制台 → 恢复到新实例（按时间点 `<UTC 时间>`；同 VPC、数据库交换机、不申请外网地址、SSL、KMS 加密、白名单只 VPC 网段）；
3. 在新实例上核对（同演练手册第 2 步：`ssl = t`、迁移版本、关键表计数）；
4. 切换：在 OOS 加密参数里把各服务连接串的主机名换成新实例的内网主机名（只改主机名，`sslmode=verify-full` 与 `sslrootcert` 不变；
   新实例证书由同一 CA 签发——若不是，先更新 ConfigMap `starbuddy-rds-ca`）；等 ack-secret-manager 同步后依次重启
   知识服务 → 学习服务 → 洞察 / 审核台 → BFF → 调度器，核对 `/readyz`；
5. 恢复点之后丢失的写入（作答、证据、判断、同意记录、OTP 审计不受影响——审计在 BFF 的卷上）由人评估并通知受影响的家庭（内部成员）；
6. 原实例保留到复盘结束（不释放、只读），之后按数据保留期限处理。

## 4. IaC 层（基础设施回退）
- 基础设施变更只经 `deploy/iac/alicloud` 的 plan / apply（由人）；回退 = 回到上一版 IaC 提交再 plan / apply，计划由第二人审阅；
- RDS、OSS、KMS 主密钥、ACK 集群都开了删除保护：任何 plan 里出现这些资源的销毁（`destroy` / `replace`）一律停下，不 apply。
