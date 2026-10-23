#!/usr/bin/env bash
# staging 镜像流水线（LT-03 范围第 3 条）：构建 → 核对 → SBOM → 漏洞与密钥扫描 → 推送到 ACR 企业版私有仓库 → 签名 → 验签 → 记录摘要。
# 在选定境内云厂商的 CI（云效 Flow 私有构建机，deploy/ci/flow.yaml）里由流水线调用；**本机不运行**（本机只做静态校验 deploy/tests/test_ci.py）。
# 输入（全部由 CI 的变量与服务连接提供，仓库里没有任何取值；登录镜像仓库由 CI 的服务连接完成，本脚本不处理任何口令）：
#   ACR_REGISTRY   私有仓库前缀：<ACR 实例名>-registry-vpc.cn-beijing.cr.aliyuncs.com/<命名空间>（VPC 端点，构建机在同一 VPC；整串按锚定的正则核对）
#   PYTHON_IMAGE / NODE_IMAGE  基础镜像：必须是 "$ACR_REGISTRY/starbuddy-base-python@sha256:<64 位>" 这种写法——
#                  只从 ACR 里同步好的镜像按摘要拉取（决定记录第 11 条：不擅自换第三方镜像源）
#   IMAGE_TAG      镜像版本（缺省 git 短提交号）
#   TRIVY_DB_REPOSITORY / TRIVY_JAVA_DB_REPOSITORY  漏洞库的 ACR 镜像地址（必须在 ACR_REGISTRY 的主机之下；不从公网拉漏洞库）
#   COSIGN_KEY_REF 签名私钥文件（CI 运行时从凭据管家挂载；不写进仓库、不打印；口令由 CI 以 cosign 约定的环境变量提供）
#   COSIGN_PUB_REF 验签公钥文件（与 ACK 验签策略用同一把；推送签名后立即用它验签，验不过即失败）
# 签名只有 cosign 一种（安全审查 B3）：原来的 acr 模式靠 ACR 加签规则在推送时签名，本脚本离线无法核实签名真的存在，
# 所以取消；ACR 企业版加签可以另外开启（控制台），但流水线的通过与否只看这里的 cosign verify。
# 签名与证明都不上传公共透明日志（--tlog-upload=false；验签用 --insecure-ignore-tlog=true 只认本地公钥），签名信息不出境。
# cosign 的 TUF 根（公共 Sigstore 信任根）在只用本地公钥时是否仍会联网初始化：待人确认（构建机出网只放行 ACR，联网会失败而不是放行）。
# syft / trivy：关闭版本检查与遥测（不连上游）。结束时（含失败）登出 ACR。
# 产物：dist/sbom/<镜像>.spdx.json、dist/scan/<镜像>.txt、dist/image-digests.txt（镜像短名 + 仓库@sha256 摘要，供人填写 deploy/k8s）。
# 退出码：0 成功；1 任一步失败（构建、核对、高危漏洞、推送、签名、验签）；2 参数不对。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
DIGEST='@sha256:[0-9a-f]{64}$'
REGISTRY_RE='^[a-z0-9][a-z0-9-]{0,62}-registry-vpc\.cn-beijing\.cr\.aliyuncs\.com/[a-z0-9][a-z0-9_-]{0,63}$'
die() { echo "pipeline: $1"; exit "${2:-1}"; }

[ -n "${ACR_REGISTRY:-}" ] || die "缺 ACR_REGISTRY（私有仓库前缀，VPC 端点）" 2
[[ "$ACR_REGISTRY" =~ $REGISTRY_RE ]] || die "ACR_REGISTRY 必须是华北 2 的 ACR 企业版 VPC 端点（<实例名>-registry-vpc.cn-beijing.cr.aliyuncs.com/<命名空间>）" 2
REGISTRY_HOST="${ACR_REGISTRY%%/*}"
for v in PYTHON_IMAGE NODE_IMAGE; do
  val="${!v:-}"
  [[ "$val" =~ $DIGEST ]] || die "$v 必须以 @sha256:<摘要> 给出" 2
  [[ "$val" == "$ACR_REGISTRY/"* ]] || die "$v 必须来自 ACR 里同步的基础镜像（$ACR_REGISTRY/…）" 2
done
for v in TRIVY_DB_REPOSITORY TRIVY_JAVA_DB_REPOSITORY; do
  val="${!v:-}"
  [[ "$val" == "$REGISTRY_HOST/"* ]] || die "$v 必须是 ACR 里的漏洞库镜像（不从公网拉）" 2
done
SIGN_MODE="${SIGN_MODE:-cosign}"
[ "$SIGN_MODE" = "cosign" ] || die "SIGN_MODE 只能是 cosign（acr 模式离线无法验签，已取消）" 2
[ -f "${COSIGN_KEY_REF:-}" ] || die "COSIGN_KEY_REF 必须指向挂载的签名私钥文件" 2
[ -f "${COSIGN_PUB_REF:-}" ] || die "COSIGN_PUB_REF 必须指向验签公钥文件" 2
for tool in docker syft trivy cosign; do command -v "$tool" >/dev/null 2>&1 || die "构建机缺 $tool（流水线不跳过 SBOM、扫描与签名）"; done
TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"
[[ "$TAG" =~ ^[0-9a-z][0-9a-z.-]{3,40}$ ]] && [ "$TAG" != "latest" ] || die "IMAGE_TAG 不合法（不收 latest）" 2

# 不连上游：关闭 syft 的新版本检查、trivy 的版本检查与遥测
export SYFT_CHECK_FOR_APP_UPDATE=false
export TRIVY_SKIP_VERSION_CHECK=true TRIVY_DISABLE_TELEMETRY=true
# 结束时（成功或失败）登出 ACR：构建机上不留登录态
trap 'docker logout "$REGISTRY_HOST" >/dev/null 2>&1 || true' EXIT

mkdir -p dist/sbom dist/scan
: > dist/image-digests.txt
export DOCKER_BUILDKIT=1
while read -r name file base extra; do
  case "$name" in ''|'#'*) continue;; esac
  img="$ACR_REGISTRY/starbuddy-$name:$TAG"
  args=(--build-arg "$base=${!base}" --build-arg STARBUDDY_IMAGE_ENV=staging)
  [ "$extra" = "-" ] || args+=(--build-arg "$extra")
  echo "pipeline: 构建 $img ← $file"
  docker build -f "$file" -t "$img" "${args[@]}" . || die "构建失败（$name）"
  # 核对：运行用户 10001、带健康检查（与 deploy/scripts/build-images.sh 同一口径）
  user="$(docker image inspect -f '{{.Config.User}}' "$img")"
  [ "${user%%:*}" = "10001" ] || die "$name 的运行用户不是 10001"
  [ -n "$(docker image inspect -f '{{if .Config.Healthcheck}}yes{{end}}' "$img")" ] || die "$name 没有健康检查"
  # SBOM（SPDX JSON）与扫描：高危、严重漏洞或镜像里有密钥即失败；豁免只能经人审阅后写进 .trivyignore（带理由与到期日）
  syft "docker:$img" -o "spdx-json=dist/sbom/$name.spdx.json" >/dev/null || die "SBOM 生成失败（$name）"
  trivy image --scanners vuln,secret --severity HIGH,CRITICAL --exit-code 1 --no-progress \
    --db-repository "$TRIVY_DB_REPOSITORY" --java-db-repository "$TRIVY_JAVA_DB_REPOSITORY" \
    --output "dist/scan/$name.txt" "$img" || die "扫描发现高危问题或密钥（$name，见 dist/scan/$name.txt）"
  docker push "$img" >/dev/null || die "推送失败（$name）"
  ref="$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$img" | grep -m1 "^$ACR_REGISTRY/starbuddy-$name@sha256:")" \
    || die "取不到推送后的摘要（$name）"
  [[ "$ref" =~ $DIGEST ]] || die "推送后的摘要格式不对（$name）"
  # 签名与 SBOM 证明（存进同一个 ACR 仓库），随后用公钥验签：验不过就不记录摘要、整条流水线失败
  cosign sign --yes --tlog-upload=false --key "$COSIGN_KEY_REF" "$ref" >/dev/null || die "签名失败（$name）"
  cosign attest --yes --tlog-upload=false --key "$COSIGN_KEY_REF" --type spdxjson --predicate "dist/sbom/$name.spdx.json" "$ref" >/dev/null \
    || die "SBOM 证明失败（$name）"
  cosign verify --insecure-ignore-tlog=true --key "$COSIGN_PUB_REF" "$ref" >/dev/null || die "验签失败（$name）"
  cosign verify-attestation --insecure-ignore-tlog=true --key "$COSIGN_PUB_REF" --type spdxjson "$ref" >/dev/null \
    || die "SBOM 证明验签失败（$name）"
  echo "$name $ref" >> dist/image-digests.txt
done < deploy/ci/images.txt
n="$(wc -l < dist/image-digests.txt)"
[ "$n" -eq 10 ] || die "应有 10 个镜像，实际 $n 个"
echo "pipeline: 通过（$n 个镜像，均已签名并验签；摘要见 dist/image-digests.txt，由人填进 deploy/k8s/staging.yaml）"
