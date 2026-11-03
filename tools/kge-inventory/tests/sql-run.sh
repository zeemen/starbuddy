#!/usr/bin/env bash
# 在本机一次性 Postgres 上运行库模式 SQL（spec 验收）：
#   1. 空库：每一项都是 absent，没有 columns 行；
#   2. 按 legacy 迁移的列建出几张内容表（合成数据），核对计数、分组与 column absent。
# 不连接任何远程库（CLAUDE.md 规则 9）。退出码 3 = 本机没有 Postgres。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL="$HERE/../db/readonly-inventory.sql"
# shellcheck source=/dev/null
source "$HERE/../../../db/tests/pg-ephemeral.sh"
pg_ephemeral_start || exit $?
# 按 spec 的运行方式：-X -At -F '|'，不加 -q（BEGIN / SET / ROLLBACK 这类命令标记由解析时忽略）
run() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -At -F '|' -f - < "$SQL" | grep '|' ; }

empty="$(run)"
if grep -v '|absent$' <<<"$empty" | grep -q .; then
  echo "sql-run: 空库上应全部是 absent，实际："; echo "$empty"; exit 1
fi
[ "$(wc -l <<<"$empty")" -eq 25 ] || { echo "sql-run: 空库应输出 25 行，实际 $(wc -l <<<"$empty")"; exit 1; }

psql_run <<'SQL' >/dev/null
CREATE TABLE knowledge_nodes (id text PRIMARY KEY, kind text NOT NULL);
INSERT INTO knowledge_nodes VALUES ('a','K'),('b','K'),('c','S');
CREATE TABLE textbook_catalog (id int PRIMARY KEY, grade text NOT NULL, title text NOT NULL, must_recite boolean NOT NULL DEFAULT false);
INSERT INTO textbook_catalog VALUES (1,'七年级','篇甲',true),(2,'七年级','篇乙',false),(3,'八年级','篇丙',true);
CREATE TABLE daofa_item (id int PRIMARY KEY, title text NOT NULL, status text);
INSERT INTO daofa_item VALUES (1,'题一','approved'),(2,'题二',NULL);
SQL

got="$(run)"
expect=$(cat <<'OUT'
columns|daofa_item|id,title,status
columns|knowledge_nodes|id,kind
columns|textbook_catalog|id,grade,title,must_recite
group|classical_text.status|absent
group|daofa_item.status|{ "(null)" : 1, "approved" : 1 }
group|doc_chunks.source|absent
group|item_bank.domain|absent
group|item_bank.review_status|absent
group|item_bank.source|absent
group|knowledge_nodes.kind|{ "K" : 2, "S" : 1 }
group|knowledge_point.domain|absent
group|knowledge_point.level|absent
group|knowledge_point.status|absent
group|textbook_catalog.grade|{ "七年级" : 2, "八年级" : 1 }
notnull|daofa_item.rubric|column absent
notnull|item_bank.textbook_anchor|absent
notnull|knowledge_point.catalog_ref|absent
rows|classical_text|absent
rows|daofa_item|2
rows|doc_chunks|absent
rows|item_bank|absent
rows|knowledge_nodes|3
rows|knowledge_point|absent
rows|questions|absent
rows|recitation_corpus|absent
rows|textbook_catalog|3
true|recitation_corpus.must_recite|absent
true|textbook_catalog.must_recite|2
OUT
)
if [ "$got" != "$expect" ]; then
  echo "sql-run: 输出与预期不一致"; diff <(echo "$expect") <(echo "$got") || true; exit 1
fi
# 只读：SQL 运行后数据没有变化
[ "$(psql_run -At -c 'SELECT count(*) FROM knowledge_nodes')" = "3" ]
echo "sql-run: 空库与合成内容表两种情况均通过"
