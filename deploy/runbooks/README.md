# staging 演练与回滚手册（LT-03 范围第 5 条）

全部**由人执行**（Claude 不连任何云或真实环境）。仓库里只有步骤、命令模板与核对口径；`<…>` 都是由人填写的占位，不写真实地址、实例 ID、
账号与凭据。每次执行都要两个人：一人操作、一人复核关键输出（确认串、目标实例、恢复时间点），并在变更记录里留痕（时间、操作人、复核人、结果），
记录里不写学生数据。staging 存放内部家庭（含未成年人）的真实数据（LT-01 决定记录 2026-10-20），按个人信息从严：副本与导出同样只在 VPC 内、
同样加密、用完即删。

| 手册 | 什么时候用 |
|---|---|
| [staging-first-deploy.md](staging-first-deploy.md) | staging 第一次全量部署与上线前走查：LT-01 至 LT-05 串成一条顺序（Terraform 分阶段、镜像、副本演练与迁移、k8s 按依赖部署、开关解除、走查、回滚） |
| [rds-restore-and-migrate-rehearsal.md](rds-restore-and-migrate-rehearsal.md) | 每次执行迁移之前（含 0058）；每月一次备份可恢复性演练 |
| [rollback.md](rollback.md) | 发布后出问题：应用回滚、迁移回滚（down）、按时间点恢复（PITR） |
| [weekly-restore-verify.md](weekly-restore-verify.md) | 每周一次：恢复到副本、mark-replica，用 `restore_verify.py` 只读校验行数与校验和、迁移账本，并与上周报告比较（LT-05） |

共同前提：
- 工具在 VPC 内的运维跳板（或集群里的一次性运维 Pod，镜像来自 ACR、按摘要）上运行：`psql`（16）、`kubectl`、阿里云 CLI；
  跳板经 VPN 访问，不开公网入口；运维凭据是 RAM 用户扮演运维角色取得的临时凭据，不落盘、不进命令历史（用 `HISTCONTROL=ignorespace` 或在
  会话结束后清理）。
- 连库一律 `sslmode=verify-full`，CA 证书用 RDS 控制台下载的云盘 CA（与集群 ConfigMap `starbuddy-rds-ca` 同一份），见 `deploy/config/README.md` 第 8 节。
- 主实例与副本的连接串分放两个环境变量：`PRIMARY_DSN`、`REPLICA_DSN`（迁移执行器用 `--dsn-env` 选；副本标签的改库命令必须带
  `--replica --replica-host-allow`，副本改标签只用 `mark-replica` 子命令，不手写 SQL）。
- 迁移执行器 `deploy/migrate/migrate.py` 的规则见 `deploy/config/README.md` 第 5 节：连接串只从环境变量读（不带口令，口令走 `PGPASSFILE`），
  执行前打印清单与确认串，`--confirm` 必须逐字一致。**真实环境执行迁移须人批准**（CLAUDE.md 必停项）。
