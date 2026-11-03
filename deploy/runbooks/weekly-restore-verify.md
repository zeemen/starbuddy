# 每周恢复演练：恢复到副本并校验行数与校验和（由人执行）

LT-05 范围第 6 条（specs/launch-lt05-observability）：在 `rds-restore-and-migrate-rehearsal.md` 第 1–3 节（恢复到副本、核对、mark-replica）的
基础上，**每周一次**把 staging 最近的备份恢复到新的副本实例，用 `deploy/runbooks/restore_verify.py` 只读校验副本上每张表的行数与内容校验和、
迁移账本与仓库一致，并与上周的报告比较。两个人：一人操作、一人复核（目标是副本、确认串里的服务端地址、报告结论），结果写进变更记录。

**只连副本**：校验脚本复用迁移执行器 `mark-replica` 的限定——连接串只从 `REPLICA_DSN` 读（主实例的 `PRIMARY_DSN` 一律拒绝），
必须 `--replica --replica-host-allow <副本内网主机名>`，连接串里每个主机都必须是显式放行的副本主机，连上后核对服务端地址，库里的标签
必须已是 `staging-replica`（主实例的标签是 `staging`，脚本拒绝），确认串带服务端地址、逐字一致。脚本每条查询各自包在只读事务里
（`BEGIN TRANSACTION READ ONLY`，并 `SET LOCAL row_security = off`：受行级安全约束的表直接报错，不会静默少算行），
报告里只有表名、行数、校验和与迁移版本，**不含任何行内容**（staging 存放内部家庭的真实数据，按个人信息从严）。

**校验角色**：必须看得到全部行——脚本在读任何业务表之前核对登录角色是超级用户或有 `BYPASSRLS`，否则所有开了 RLS 的表都必须归它所有
且没有 `FORCE ROW LEVEL SECURITY`，不满足就拒绝（退出码 2）。推荐由人在副本上建一个专用只读角色（`LOGIN BYPASSRLS`、只授各 schema 的
`USAGE` 与表的 `SELECT`、`ALTER ROLE … SET default_transaction_read_only = on`），只在副本上建、随副本释放，不在主实例上建。
校验范围：platform、insight、kge、policy、deploy_meta，以及存在时的各科 schema（aya、zhixing、wenli、echo）。提示与确认串写标准错误，
不给 `--out` 时标准输出只有报告本身。

## 0. 准备
- 变更记录：本周副本实例名 `<副本实例名>`（建议 `starbuddy-staging-restore-<日期>`）、恢复方式（最近一次数据备份，或按时间点 `<UTC 时间>`）；
- 跳板：`psql`（16）、Python 3、RDS CA 证书 `<CA 路径>`、`PGPASSFILE`（0600，只含副本的一行）；上周的报告文件 `<上周报告.json>`
  （放在跳板的加密目录里，不进仓库、不发聊天工具）。

## 1. 恢复到副本并标记
按 `rds-restore-and-migrate-rehearsal.md` 第 1 节恢复到新实例（同一 VPC、数据库交换机、不申请外网地址、只允许 SSL、云盘加密同主实例（服务密钥），恢复后核对已开启、没开不验证），
第 2 节核对 `ssl = t` 与服务端地址，然后标记副本（复核人核对确认串里的服务端地址是副本、不是主实例）：
```
export REPLICA_DSN='postgresql://<高权限账号>@<副本内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=<CA 路径>'
python3 deploy/migrate/migrate.py mark-replica --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm x
python3 deploy/migrate/migrate.py mark-replica --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm staging:mark-replica:staging-replica:<服务端地址>
```

## 2. 只读校验（行数、校验和、迁移账本）
```
python3 deploy/runbooks/restore_verify.py --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm x
# 第一次故意给错确认串：只打印“确认串：--confirm staging-replica:verify:<服务端地址>”，不读任何表
# 复核人核对 <服务端地址> 是本周副本的内网地址，再逐字给出，并与上周报告比较：
python3 deploy/runbooks/restore_verify.py --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm staging-replica:verify:<服务端地址> --baseline <上周报告.json> --out <本周报告.json>
```
- 退出码 0：通过；1：校验失败（迁移账本与仓库不一致、上周有的表没了、行数减少、迁移个数变少）；2：拒绝（不是副本、确认串不对等，未读数据）；
- 行数减少确属正常清理的表（例如过期会话、限流计数），由复核人确认后用 `--allow-decrease <schema.table>` 显式列出再跑一次，并在变更记录里写明；
- 行数不变而校验和变了只是提示（staging 有正常更新）；新出现的表是提示（新迁移）；
- 第一次没有上周报告时不给 `--baseline`，只做账本与计数，报告作为下周的基线。

## 3. 收尾
- 释放副本实例（控制台 → 释放），删除跳板上的 `PGPASSFILE`，`unset REPLICA_DSN`；本周报告保存在跳板的加密目录（下周的基线），保留 4 周；
- 变更记录写：副本实例名、恢复方式与时间点、mark-replica 与校验时核对的服务端地址、报告结论（表数、迁移版本、失败项与处理）、释放时间、操作人与复核人；
- 告警：恢复或校验失败按 `deploy/observability/alerts.yaml` 之外的人工流程上报（本演练不接自动告警），由值守负责人跟进。
