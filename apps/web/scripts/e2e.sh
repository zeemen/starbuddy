#!/usr/bin/env bash
# 前端端到端测试的环境（spec 验收）：一次性 Postgres（db/tests/pg-ephemeral.sh）→ 阿鸦内容登记 → 星图合成种子 →
# BFF 星图服务（只监听 127.0.0.1，开发身份桩打开）→ 测试构建（含开发专用页面，输出到单独的 .next-e2e，不碰正式的 .next）
# → Playwright（webServer 起 next start，同样读 .next-e2e）。
# 全部合成数据，不连共享库。
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

# 审核台后端（services/learning/review_console）：两个登录角色分别只继承 kge、review；合成 staff 与语文合成地图；
# 身份来自 staff 登录（PR 4，合成凭据在种子里）。需要 services/learning/.venv（同 make learning-test）。
PY="$ROOT/services/learning/.venv/bin/python"
if [ ! -x "$PY" ]; then echo "e2e: 缺少 services/learning/.venv（审核台后端）"; exit 3; fi
psql -X -q -v ON_ERROR_STOP=1 -c "CREATE ROLE review_kge_app LOGIN IN ROLE starbuddy_kge" \
  -c "CREATE ROLE review_sign_app LOGIN IN ROLE starbuddy_review" >/dev/null
PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -q -v ON_ERROR_STOP=1 -f "$ROOT/db/seeds/dev/review-synthetic.sql" >/dev/null
REVIEW_PORT="$(free_port)"
RLOG="$(mktemp)"
( cd "$ROOT/services/learning" || exit 1
  exec env REVIEW_CONSOLE_ENV=test \
    REVIEW_CONSOLE_KGE_DATABASE_URL="postgresql://review_kge_app@/starbuddy_test?host=$PGHOST" \
    REVIEW_CONSOLE_REVIEW_DATABASE_URL="postgresql://review_sign_app@/starbuddy_test?host=$PGHOST" \
    "$PY" -m review_console serve --port "$REVIEW_PORT" ) > "$RLOG" 2>&1 < /dev/null &
REVIEW_PID=$!
# 测试构建会改写 next-env.d.ts（指向 .next-e2e/types）：先备份，结束时还原，并删掉测试构建目录
ENVDTS="$(mktemp)"
if [ -f "$WEB/next-env.d.ts" ]; then cp "$WEB/next-env.d.ts" "$ENVDTS"; else rm -f "$ENVDTS"; fi
cleanup() {
  kill "$BFF_PID" "$REVIEW_PID" 2>/dev/null || true
  rm -f "$LOG" "$RLOG"
  if [ -f "$ENVDTS" ]; then cp "$ENVDTS" "$WEB/next-env.d.ts"; rm -f "$ENVDTS"; else rm -f "$WEB/next-env.d.ts"; fi
  rm -rf "${WEB:?}/.next-e2e"
}
trap cleanup EXIT
for _ in $(seq 1 80); do
  python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1', $REVIEW_PORT)) == 0 else 1)" && break
  sleep 0.25
done

# STARBUDDY_NEXT_DIST_DIR：构建与 next start（playwright.config.ts 的 webServer）都读 .next-e2e
export STARBUDDY_BFF_URL="http://127.0.0.1:$BFF_PORT" STARBUDDY_REVIEW_URL="http://127.0.0.1:$REVIEW_PORT" WEB_PORT STARBUDDY_DEV_AUTH=1 STARBUDDY_ENV=test NEXT_TELEMETRY_DISABLED=1 \
  STARBUDDY_NEXT_DIST_DIR=.next-e2e
cd "$WEB"
# 端到端用测试构建（冲刺 2-L）：开发专用页面（page.dev.tsx，如探针演示页）只在 STARBUDDY_DEV_AUTH=1 且 STARBUDDY_ENV=dev/test
# 构建时才是页面（next.config.ts）。测试构建输出到 .next-e2e、类型检查用 tsconfig.e2e.json，正式的 .next 保持生产构建，
# tests/build-output.test.ts 的检查不再跳过；tsconfig.json 不被改写（next-env.d.ts 结束时还原）。
BLOG="$(mktemp)"
npx --no-install next build > "$BLOG" 2>&1 || { tail -30 "$BLOG"; rm -f "$BLOG"; exit 1; }
rm -f "$BLOG"
set +e
npx --no-install playwright test "$@"
rc=$?
if [ $rc -ne 0 ]; then echo "--- BFF 日志（最后 20 行）"; tail -20 "$LOG"; echo "--- 审核台后端日志（最后 20 行）"; tail -20 "$RLOG"; fi
exit $rc
