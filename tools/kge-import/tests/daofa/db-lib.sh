#!/usr/bin/env bash
# 冲刺 4-K 道法切片库上测试的公共部分（被 source）：起一次性 Postgres（只监听 Unix socket，结束即删，规则 9）、执行全部迁移，
# 登记合成内容包（adapters/zhixing/scripts/register-content.ts）、两个合成 staff 账号、合成的时政来源主机（.example 保留域名），
# 并提供生成与执行合成样例的函数。全部合成数据（规则 8）。退出码 3 = 本机没有 Postgres。
HERE_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$(cd "$HERE_LIB/../.." && pwd)"
REPO="$(cd "$TOOL/../.." && pwd)"
SAMPLES="$TOOL/samples/daofa"
PACK="$REPO/adapters/zhixing/content/pack.json"
STAFF=5f000000-0000-4000-8000-0000000004a1; STAFF2=5f000000-0000-4000-8000-0000000004a2
# shellcheck source=/dev/null
source "$REPO/db/tests/pg-ephemeral.sh"

daofa_db_start() {  # 成功 0；没有 Postgres 返回 3；其余失败返回 1（psql 出错的 3 不能与“没有 Postgres”混淆）
  pg_ephemeral_start || return $?
  TMP="$(mktemp -d)"; chmod 755 "$TMP"
  trap 'rm -rf "$TMP"; pg_ephemeral_stop' EXIT
  apply "$ROOT/db/bootstrap/roles.sql" || return 1
  mapfile -t UPS < <(ls "$ROOT"/db/migrations/*.up.sql | sort)
  apply "${UPS[@]}" || return 1
  (cd "$REPO/adapters/zhixing" && node --no-warnings scripts/register-content.ts) | psql_run >/dev/null || return 1
  psql_run -1 >/dev/null <<SQL || return 1
INSERT INTO platform.account (id, kind) VALUES ('$STAFF', 'staff'), ('$STAFF2', 'staff');
INSERT INTO policy.source_domain (host, issuer, added_by) VALUES
  ('news.synthetic-source.example', '（合成）虚构来源甲', 'syn-staff-01'),
  ('gov.synthetic-source.example', '（合成）虚构来源乙', 'syn-staff-01');
SQL
}

q() { as_pg "$PGBIN/psql" -h "$WORK" -U postgres -d starbuddy_test -X -At -v ON_ERROR_STOP=1 -c "$1"; }
# 合成样例的种子带挡板：会话里先允许合成数据再执行
run_syn() { { echo "SET starbuddy.allow_synthetic = on;"; cat "$1"; } | psql_run >/dev/null; }
daofa() { (cd "$TOOL" && node --no-warnings src/daofa/main.ts "$@"); }
as_staff() { echo "SELECT set_config('app.account_id', '$1', false); SET ROLE starbuddy_review;"; }
# 首次发布算结构性变更：两名 staff 对当前内容签字（0019），再发布
sign_publish() {
  q "$(as_staff "$STAFF2") SELECT kge.sign_review('map_version', '$1', 'approved', 'high', 'daofa4k')" >/dev/null
  q "$(as_staff "$STAFF") SELECT kge.sign_review('map_version', '$1', 'approved', 'high', 'daofa4k'); SELECT kge.publish_map_version('$1')" >/dev/null
}
json_field() { node -e 'console.log(require(process.argv[1])[process.argv[2]])' "$1" "$2"; }
# 生成合成地图（法律领域样例 + 内容包挂接节点）到目录 $1，版本号 $2
gen_map() { daofa map --domain "$SAMPLES/law.yaml" --domain "$SAMPLES/pack-nodes.yaml" --version "$2" --out "$1" >/dev/null; chmod -R a+r "$1"; }
