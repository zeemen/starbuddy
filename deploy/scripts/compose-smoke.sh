#!/usr/bin/env bash
# 本机合成全栈冒烟（LT-01 范围第 6 条）：用 build-images.sh 构建的镜像，按 deploy/compose/local-test.yaml 起全栈（test 模式、合成数据、
# 一次性数据库、不发布端口），按依赖顺序等全部健康后走一遍：
#   合成家长（+999 号码，db/seeds/dev/auth-synthetic.sql）登录 → /me → 今日计划 → 星图 v1 → 前端首页可达 → 调度器 --dry-run 核对通过。
# 令牌每次随机生成（只在本进程环境里，不落盘、不打印）；结束时 down -v 删除一切。不连接任何共享或生产库（规则 9）。
#   POSTGRES_IMAGE  Postgres 16 镜像（以摘要给出）；STARBUDDY_REGISTRY / STARBUDDY_IMAGE_TAG 同 build-images.sh
# 退出码：0 通过；1 失败；2 参数不对；3 本机没有 docker 守护进程。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/compose/local-test.yaml")
docker info >/dev/null 2>&1 || { echo "compose-smoke: 跳过（本机没有 docker 守护进程）"; exit 3; }
[ -n "${POSTGRES_IMAGE:-}" ] || { echo "compose-smoke: 缺 POSTGRES_IMAGE"; exit 2; }
export STARBUDDY_REGISTRY="${STARBUDDY_REGISTRY:-starbuddy-local}"
export STARBUDDY_IMAGE_TAG="${STARBUDDY_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)}"
gen() { python3 -c 'import secrets; print("syn-" + secrets.token_hex(24))'; }
for v in LEARNING_SERVICE_TOKEN LEARNING_SESSION_TOKEN AYA_ADAPTER_SERVICE_TOKEN ZHIXING_ADAPTER_SERVICE_TOKEN WENLI_ADAPTER_SERVICE_TOKEN KNOWLEDGE_TOKEN_LEARNING \
         LEARNING_INSIGHT_CLIENT_TOKEN LEARNING_PRACTICE_TICKET_SECRET INSIGHT_TOKEN_BFF; do
  export "$v=$(gen)"
done
cleanup() { "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
fail() { echo "compose-smoke: 失败（$1）"; "${COMPOSE[@]}" ps; "${COMPOSE[@]}" logs --tail 30 2>&1 | tail -120; exit 1; }

"${COMPOSE[@]}" up -d --wait --wait-timeout 300 db knowledge learning adapter-aya adapter-zhixing adapter-wenli insight review-console bff web \
  || fail "全栈没有在 300 秒内全部健康"
"${COMPOSE[@]}" exec -T -e PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' db \
  psql -X -q -v ON_ERROR_STOP=1 -U postgres -d starbuddy_local -f /repo/db/seeds/dev/auth-synthetic.sql >/dev/null || fail "合成种子"

# 在学习服务容器里（同一网络命名空间）经回环走查 BFF；验证码从 BFF 的捕获桩文件读（只在 test 模式存在）
"${COMPOSE[@]}" exec -T learning python -I - <<'PY' || fail "走查"
import json, urllib.request
B = "http://127.0.0.1:8787/bff/v0"
def call(method, path, body=None, headers=None):
    h = {"origin": "http://127.0.0.1:3000", **(headers or {})}
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        h["content-type"] = "application/json"
    req = urllib.request.Request(B + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
st, _ = call("POST", "/auth/otp/request", {"phone": "+99900000101"})
assert st == 202, st
print("otp-requested")
PY
code="$("${COMPOSE[@]}" exec -T bff node -e "const l=require('node:fs').readFileSync('/var/lib/starbuddy/audit/sms-capture.jsonl','utf8').trim().split('\n').map(JSON.parse).filter(m=>m.phone==='+99900000101');process.stdout.write(l[l.length-1].code)")" \
  || fail "读不到捕获的验证码"
"${COMPOSE[@]}" exec -T -e SMOKE_CODE="$code" learning python -I - <<'PY' || fail "走查"
import json, os, urllib.request
B = "http://127.0.0.1:8787/bff/v0"
def call(method, path, body=None, sess=None):
    h = {"origin": "http://127.0.0.1:3000"}
    if sess:
        h["x-sb-session"] = sess["session_token"]
        h["x-csrf-token"] = sess["csrf_token"]
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        h["content-type"] = "application/json"
    req = urllib.request.Request(B + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
st, sess = call("POST", "/auth/otp/verify", {"phone": "+99900000101", "code": os.environ["SMOKE_CODE"]})
assert st == 200 and sess["account_kind"] == "guardian", st
st, me = call("GET", "/me", sess=sess)
assert st == 200 and me["account_kind"] == "guardian", st
print("me", len(me["children"]))
for child in me["children"][:1]:
    sid = child["student_id"]
    for path in (f"/students/{sid}/today", f"/students/{sid}/star-map/v1"):
        st, _ = call("GET", path, sess=sess)
        assert st in (200, 404), (path, st)
        print(path.split("/")[-1], st)
st, _ = call("POST", "/auth/logout", {}, sess=sess)
print("logout", st)
PY
"${COMPOSE[@]}" exec -T web node -e "fetch('http://127.0.0.1:3000/login').then(r=>{if(r.status!==200)process.exit(1)}).catch(()=>process.exit(1))" \
  || fail "前端 /login 不可达"
"${COMPOSE[@]}" run --rm -T scheduler >/dev/null || fail "调度器 --dry-run 核对不通过"
echo "compose-smoke: 通过（全栈健康、合成家长登录走查、前端可达、调度器核对）"
