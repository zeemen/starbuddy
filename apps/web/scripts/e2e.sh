#!/usr/bin/env bash
# 前端端到端测试的环境（spec 验收）：一次性 Postgres（db/tests/pg-ephemeral.sh）→ 阿鸦内容登记 → 星图合成种子 →
# BFF 星图服务（只监听 127.0.0.1，开发身份桩打开）→ Playwright（webServer 起 next start）。全部合成数据，不连共享库。
# 用法：bash apps/web/scripts/e2e.sh [playwright 参数…]        例如 --grep-invert @visual、--grep @visual
# 退出码：Playwright 的退出码；3 表示本机没有 Postgres。
set -uo pipefail
WEB="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$WEB/../.." && pwd)"

if [ "${1:-}" != "--inner" ]; then
  exec bash "$ROOT/db/tests/pg-ephemeral.sh" -- bash "$0" --inner "$@"
fi
shift
set -e
# 下面在 pg-ephemeral 里执行：PGHOST、PGDATABASE、PGUSER 已指向一次性实例
psql -X -q -v ON_ERROR_STOP=1 -c "CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff" >/dev/null
node --no-warnings "$ROOT/adapters/aya/scripts/register-content.ts" | psql -X -q -v ON_ERROR_STOP=1 >/dev/null
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/star-map-synthetic.sql" >/dev/null
AYA_COUNT="$(psql -X -At -c "SELECT count(*) FROM platform.skill_node WHERE subject = 'aya'")"
export AYA_COUNT

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
BFF_PORT="$(free_port)"
WEB_PORT="$(free_port)"
LOG="$(mktemp)"
( cd "$ROOT/services/bff" || exit 1
  exec env BFF_DATABASE_URL="postgresql://bff_app@/starbuddy_test?host=$PGHOST" PORT="$BFF_PORT" STARBUDDY_DEV_AUTH=1 \
    node --no-warnings src/star-map/main.ts ) > "$LOG" 2>&1 < /dev/null &
BFF_PID=$!
trap 'kill "$BFF_PID" 2>/dev/null || true; rm -f "$LOG"' EXIT
for _ in $(seq 1 80); do
  python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1', $BFF_PORT)) == 0 else 1)" && break
  sleep 0.25
done

export STARBUDDY_BFF_URL="http://127.0.0.1:$BFF_PORT" WEB_PORT STARBUDDY_DEV_AUTH=1 NEXT_TELEMETRY_DISABLED=1
cd "$WEB"
set +e
npx --no-install playwright test "$@"
rc=$?
if [ $rc -ne 0 ]; then echo "--- BFF 日志（最后 20 行）"; tail -20 "$LOG"; fi
exit $rc
