#!/bin/sh
# 本机合成全栈（deploy/compose/local-test.yaml）的一次性库初始化：Postgres 官方镜像首次初始化时执行 /docker-entrypoint-initdb.d/。
# 只用于本机一次性容器（数据卷不持久化，down -v 即删）；不连接任何共享或生产库（规则 9）。
# 角色 bootstrap → 全部 up 迁移（每个文件单事务，与 db/tests/pg-ephemeral.sh 相同）→ 本机登录角色（local-roles.sql，合成名、trust 认证）。
# 正式环境的迁移走 deploy/migrate/migrate.py（显式确认、版本表、副本演练），不走这里。
set -eu
apply() {
  PGOPTIONS='-c client_min_messages=warning' psql -X -q -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$1"
}
apply /repo/db/bootstrap/roles.sql
for f in $(ls /repo/db/migrations/*.up.sql | sort); do apply "$f"; done
apply /repo/deploy/compose/local-init/local-roles.sql
echo "local-init: 迁移与本机登录角色就绪"
