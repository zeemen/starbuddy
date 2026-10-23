# RDS 快照恢复到副本 + 迁移演练（由人执行）

目的：(1) 证明备份能恢复（数据备份 + 日志备份 = 按时间点恢复）；(2) 在 staging 数据的副本上先演练要执行的迁移（up → 校验 → down → 校验 → up），
再在 staging 主实例上执行。演练对象永远是**新恢复出来的副本实例**，不是主实例。

**主 / 副连接串分开放**（LT-03 决定记录（续）第 5 条）：副本只用环境变量 `REPLICA_DSN`（命令一律 `--dsn-env REPLICA_DSN`），主实例只用
`PRIMARY_DSN`（`--dsn-env PRIMARY_DSN`）；同一个会话里不要同时设两个，演练结束先 `unset REPLICA_DSN` 再设 `PRIMARY_DSN`。
副本上的改库命令（mark-replica、rehearse、up、down）迁移执行器都要求 `--replica --replica-host-allow <副本内网主机名>`，少了就拒绝。

## 0. 准备（操作人 + 复核人）
- 变更记录：要执行的迁移清单（版本号）、本次演练的副本实例名 `<副本实例名>`（建议 `starbuddy-staging-replica-<日期>`）、恢复时间点 `<UTC 时间>`；
- 确认 IaC 的备份策略在生效（控制台“备份恢复”：每日数据备份、日志备份开启、保留 ≥ 7 天）；
- 在跳板上准备：`psql`、RDS CA 证书文件 `<CA 路径>`、`PGPASSFILE`（0600，只含副本的一行，演练后删除）。

## 1. 恢复到副本（控制台或阿里云 CLI）
1. RDS 控制台 → staging 实例 → 备份恢复 → **恢复到新实例**（克隆实例）：
   - 恢复方式：按时间点（PITR，`<UTC 时间>`）或按备份集（最近一次数据备份）；
   - 地域华北 2、同一 VPC、**数据库交换机**、规格可小一档；网络类型只有专有网络；**不申请外网地址**；
   - 白名单只填 VPC 网段（与主实例相同，见 `deploy/iac/alicloud/variables.tf` 的 `vpc_cidr`）；只允许 SSL 连接（与主实例同一 hostssl 设置）；
     云盘加密用同一把 KMS 主密钥；
   - 标签：`environment=staging`、`purpose=restore-rehearsal`、`expires=<日期>`。
2. 记下副本的内网主机名 `<副本内网主机名>`（只写进本次变更记录，不进仓库）。

## 2. 核对恢复结果（只看计数与版本，不导出学生数据）
```
export REPLICA_DSN='postgresql://<高权限账号>@<副本内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=<CA 路径>'
psql "$REPLICA_DSN" -X -c "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"          # ssl 必须为 t
psql "$REPLICA_DSN" -X -c "SELECT host(inet_server_addr())"                                           # 必须是副本的 VPC 内网地址
psql "$REPLICA_DSN" -X -c "SELECT label FROM deploy_meta.target; SELECT max(version), count(*) FROM deploy_meta.schema_migrations"
```
- 迁移版本与主实例一致（与变更记录比对）；按时间点恢复的，抽查几张业务表的行数与恢复时间点前后的写入（只看 `count(*)` 与最大时间戳）。
- 结果写进变更记录：恢复耗时、版本、`ssl = t`。这一步本身就是**每月一次的备份可恢复性演练**（不做迁移时到此为止，跳到第 5 步删副本）。

## 3. 标记副本（迁移执行器 mark-replica；复核人确认目标是副本）
副本是 staging 的快照，`deploy_meta.target` 里记录的标签是 `staging`；迁移执行器只在标签以 `-replica` 结尾时允许演练（防止连错库）。
`mark-replica` 只做一件事：把**副本**上的 `staging` 改为 `staging-replica`。它要求连接串里的主机都在 `--replica-host-allow` 里，连上后核对
服务端地址（RFC 1918 内网地址、且是放行主机解析出的地址），库里的标签必须正是 `staging`，并打印带服务端地址的确认串：
```
python3 deploy/migrate/migrate.py mark-replica --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm x
# 第一次故意给错确认串：只打印“确认串：--confirm staging:mark-replica:staging-replica:<服务端地址>”，不改库
# 复核人核对 <服务端地址> 与控制台里副本实例的内网地址一致（不是主实例），再逐字给出：
python3 deploy/migrate/migrate.py mark-replica --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm staging:mark-replica:staging-replica:<服务端地址>
```
主实例上永远不执行 mark-replica（主实例只用 `PRIMARY_DSN`，命令里也不会出现 `--replica`）。

## 4. 迁移演练（up → 校验 → down → 校验 → up）
```
python3 deploy/migrate/migrate.py plan --dsn-env REPLICA_DSN --target-label staging-replica
# 核对清单（版本、名字、sha256 前 12 位）与变更记录一致，复制打印出的副本演练确认串
python3 deploy/migrate/migrate.py rehearse --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm staging-replica:rehearse:<个数>:<最后版本>
```
- `--replica-host-allow` 只写这一次的副本主机名（安全审查 S9：演练缺省只连 Unix socket 与回环）；
- 演练内容：在副本上执行全部待执行迁移 → 校验 → 逐个 down 回到演练前的基线 → 校验 → 再 up → 校验；任一步失败即停，记录失败的版本与报错首行，
  **不在主实例上执行**，回到开发修迁移（迁移内容变更属必停项）；
- 单独演练“回滚上一版发布的迁移”（发布后要 down 时，见 `rollback.md` 第 2 节）：
  ```
  python3 deploy/migrate/migrate.py down --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --to <回滚到的版本> --confirm staging-replica:down:<个数>:<回滚到的版本>
  python3 deploy/migrate/migrate.py up --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm staging-replica:up:<个数>:<最后版本>
  ```
  down 会丢数据的迁移（删列、删表）要在变更记录里写明丢什么，由人决定能不能接受。
- 演练通过后，在 staging 主实例上执行（须批准，复核人在场；先 `unset REPLICA_DSN`）：
  ```
  export PRIMARY_DSN='postgresql://<高权限账号>@<主实例内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=<CA 路径>'
  python3 deploy/migrate/migrate.py plan --dsn-env PRIMARY_DSN --target-label staging
  python3 deploy/migrate/migrate.py up --dsn-env PRIMARY_DSN --target-label staging --confirm staging:up:<个数>:<最后版本>
  ```
  执行后学习服务应能启动、`/readyz` 回到 200（缺 0058 或学习服务角色没有执行权时学习服务拒绝启动、`/readyz` 也会因执行权核对不过而 503，
  `deploy/config/README.md` 第 5.1、9 节）。

## 5. 收尾
- 删除副本实例（控制台 → 释放），删除跳板上的 `PGPASSFILE` 并 `unset REPLICA_DSN PRIMARY_DSN`；副本的备份不保留；
- 变更记录写：副本实例名、恢复时间点、版本核对、标记副本时核对的服务端地址、演练结果（每一步通过 / 失败）、主实例执行结果、释放时间。

## 6. 演练时一并核对（部署层）
- k8s 探针：各服务 Pod 的就绪探针 `/readyz`、存活探针 `/healthz` 生效（kubelet 来自本节点，不受 NetworkPolicy 限制）；适配器的 exec TCP
  探针生效；
- NetworkPolicy：从一个不带 `starbuddy/db-client` 标签的 Pod 连 RDS 5432 应失败，带标签的成功；从 Pod 访问公网任意地址应失败。
- 服务端强制 TLS：从 VPC 内（带 `starbuddy/db-client` 标签的调试 Pod）用 `sslmode=disable` 连副本应被拒（pg_hba 只有 hostssl）；
  用 `sslmode=verify-full&sslrootcert=/run/rds-ca/ca.pem` 连副本的内网地址成功。
- 服务间 Host：前端经 clusterIP 调审核台、BFF 经 clusterIP 调洞察服务、学习服务经 clusterIP 调知识服务都不回 421（`*_EXTRA_HOST` 生效）。
- 出网：节点上访问 100.64.0.0/10 里登记之外的地址应失败；云防火墙 NAT 边界的访问控制已生效（`nat_firewall_confirmed`）。
