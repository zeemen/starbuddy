#!/usr/bin/env bash
# 构建全部生产镜像（LT-01 范围第 1、6 条）。由人执行，或 make deploy-check DEPLOY_BUILD=1。
#   PYTHON_IMAGE  Python 基础镜像，必须以摘要给出：<仓库>/python:3.11-slim-bookworm@sha256:<64 位十六进制>
#   NODE_IMAGE    Node 基础镜像，必须以摘要给出：<仓库>/node:22-bookworm-slim@sha256:<…>（Node ≥ 22.18）
#   STARBUDDY_REGISTRY  镜像名前缀（缺省 starbuddy-local，只在本机）；STARBUDDY_IMAGE_TAG 镜像版本（缺省 git 短提交号）
#   STARBUDDY_IMAGE_ENV  镜像固化的环境标记：staging（缺省）或 test（本机合成冒烟用；staging 镜像拒绝以 dev / test 运行）
#   ALLOW_TAG=1   只在本机冒烟时允许不带摘要的基础镜像（打印告警；推送到任何仓库前必须换成摘要）
# 正式来源是选定的境内云厂商镜像仓库（决定记录第 11 条，随 LT-03）；本脚本不推送、不登录任何仓库、不下载构建依赖之外的东西。
# 镜像扫描：本机装了 trivy 时对每个镜像做离线漏洞与密钥扫描（--skip-db-update，不联网更新库）；没装时打印“跳过”，不伪装为通过。
# 退出码：0 全部构建成功；1 构建失败；2 参数不对。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
DIGEST='@sha256:[0-9a-f]{64}$'
for v in PYTHON_IMAGE NODE_IMAGE; do
  val="${!v:-}"
  if [ -z "$val" ]; then echo "build-images: 缺 $v（基础镜像，以摘要给出）"; exit 2; fi
  if ! [[ "$val" =~ $DIGEST ]]; then
    if [ "${ALLOW_TAG:-0}" = "1" ]; then echo "build-images: 告警：$v 没有以摘要给出（只允许本机冒烟）"
    else echo "build-images: $v 必须以 @sha256:<摘要> 给出（决定记录第 11 条；本机冒烟可设 ALLOW_TAG=1）"; exit 2; fi
  fi
done
# 镜像固化的环境标记（安全审查 S10）：staging（缺省，供 staging 部署）或 test（只给本机合成冒烟 compose-smoke.sh）
IMAGE_ENV="${STARBUDDY_IMAGE_ENV:-staging}"
case "$IMAGE_ENV" in staging|test) ;; *) echo "build-images: STARBUDDY_IMAGE_ENV 只能是 staging 或 test"; exit 2;; esac
REG="${STARBUDDY_REGISTRY:-starbuddy-local}"
TAG="${STARBUDDY_IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"
export DOCKER_BUILDKIT=1
build() {  # $1 镜像短名 $2 Dockerfile，其余为额外参数
  local name="$1" file="$2"; shift 2
  echo "build-images: $REG/starbuddy-$name:$TAG ← $file"
  docker build --pull=false -f "$file" -t "$REG/starbuddy-$name:$TAG" --build-arg STARBUDDY_IMAGE_ENV="$IMAGE_ENV" "$@" . \
    || { echo "build-images: 失败（$name）"; exit 1; }
}
build learning services/learning/Dockerfile --build-arg PYTHON_IMAGE="$PYTHON_IMAGE"
build insight deploy/docker/insight.Dockerfile --build-arg PYTHON_IMAGE="$PYTHON_IMAGE"
build knowledge deploy/docker/knowledge.Dockerfile --build-arg PYTHON_IMAGE="$PYTHON_IMAGE"
build scheduler deploy/docker/scheduler.Dockerfile --build-arg PYTHON_IMAGE="$PYTHON_IMAGE"
build review-console deploy/docker/review-console.Dockerfile --build-arg PYTHON_IMAGE="$PYTHON_IMAGE"
build bff deploy/docker/bff.Dockerfile --build-arg NODE_IMAGE="$NODE_IMAGE"
for a in aya zhixing wenli; do
  build "adapter-$a" deploy/docker/adapter.Dockerfile --build-arg NODE_IMAGE="$NODE_IMAGE" --build-arg ADAPTER="$a"
done
build web deploy/docker/web.Dockerfile --build-arg NODE_IMAGE="$NODE_IMAGE"

# 构建后核对：每个镜像的运行用户是 10001、带健康检查
for n in learning insight knowledge scheduler review-console bff adapter-aya adapter-zhixing adapter-wenli web; do
  img="$REG/starbuddy-$n:$TAG"
  user="$(docker image inspect -f '{{.Config.User}}' "$img")"
  hc="$(docker image inspect -f '{{if .Config.Healthcheck}}yes{{end}}' "$img")"
  [ "${user%%:*}" = "10001" ] || { echo "build-images: $img 的运行用户不是 10001（$user）"; exit 1; }
  [ "$hc" = "yes" ] || { echo "build-images: $img 没有健康检查"; exit 1; }
done
if command -v trivy >/dev/null 2>&1; then
  for n in learning insight knowledge scheduler review-console bff adapter-aya adapter-zhixing adapter-wenli web; do
    trivy image --skip-db-update --scanners vuln,secret --exit-code 1 --severity HIGH,CRITICAL "$REG/starbuddy-$n:$TAG" \
      || { echo "build-images: 扫描发现高危问题（$n）"; exit 1; }
  done
else
  echo "build-images: 镜像扫描跳过（本机没有 trivy；不伪装为通过）"
fi
echo "build-images: 通过（10 个镜像，前缀 $REG，版本 $TAG；运行用户与健康检查已核对）"
