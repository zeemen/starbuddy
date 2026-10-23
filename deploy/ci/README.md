# 镜像构建流水线（LT-03 范围第 3 条）

正式镜像只在阿里云华北 2 的私有构建机上构建并推送到 ACR 企业版私有仓库（`deploy/iac/alicloud/acr.tf`）；本机不构建，只做静态校验
（`deploy/tests/test_ci.py`，随 `make deploy-check`）。本机冒烟仍用 `deploy/scripts/build-images.sh`（test 标记、不推送）。

| 文件 | 用途 |
|---|---|
| `images.txt` | 镜像清单（短名、Dockerfile、基础镜像变量、额外构建参数），与 `build-images.sh`、`acr.tf` 一致 |
| `pipeline.sh` | 构建 → 核对（uid 10001、健康检查）→ SBOM（syft，SPDX JSON）→ 扫描（trivy：高危 / 严重漏洞与密钥即失败）→ 推送 → 签名 → 验签 → 记录摘要 |
| `flow.yaml` | 云效 Flow 模板：私有构建集群、ACR 服务连接登录、只手动触发、产物归档（字段以云效文档为准，由人导入核对） |

## 1. 基础镜像
- 上游 `python:3.11-slim-bookworm`、`node:22-bookworm-slim`（Node ≥ 22.18）由人同步到 ACR 的 `starbuddy-base-python` / `starbuddy-base-node`
  （ACR 企业版的镜像同步或手动推送一次），记下摘要；流水线只收 `"$ACR_REGISTRY/starbuddy-base-*@sha256:<摘要>"`，不从第三方源拉取
  （LT-01 决定记录第 11 条）。升级基础镜像 = 同步新摘要 + 改流水线变量，经评审。

## 2. 扫描
- trivy 的漏洞库也从 ACR 拉（`TRIVY_DB_REPOSITORY` / `TRIVY_JAVA_DB_REPOSITORY`，由人定期同步），不从公网拉；
- 失败门槛：HIGH、CRITICAL 漏洞或镜像里有密钥；豁免只能经人审阅后写进 `.trivyignore`（每条带理由与到期日）。
- ACR 企业版自带的镜像安全扫描可同时开启（控制台），两者任一报高危即不发布。

## 3. SBOM
- 每个镜像一份 SPDX JSON（`dist/sbom/<镜像>.spdx.json`），随流水线制品归档，另以证明（attestation）附在镜像上并验签。

## 4. 签名与验签（安全审查 B3）
- **只有 cosign 一种模式**（`SIGN_MODE=cosign`，缺省）：每个镜像推送后用签名私钥签名并附 SBOM 证明，随即用验签公钥
  `cosign verify` / `cosign verify-attestation`，验不过就不记录摘要、整条流水线失败。签名与证明**不上传公共透明日志**
  （`--tlog-upload=false`；验签 `--insecure-ignore-tlog=true` 只认本地公钥），签名信息不出境。
- 原来的 `SIGN_MODE=acr`（ACR 企业版在推送时用 KMS 非对称密钥加签）已取消：流水线离线无法核实签名真的存在，之前“只核对已开启”的说法
  与脚本不符（脚本并没有核对）。ACR 企业版的加签可以另外开启，但发布与否只看 cosign 验签。IaC 的 ACR 规格缺省 Advanced（支持加签，待人核实）。
- 密钥：签名私钥文件运行时从凭据管家挂载（`COSIGN_KEY_REF`），口令由 CI 以 cosign 约定的环境变量提供，不写进仓库、不打印；
  验签公钥（`COSIGN_PUB_REF`）与 ACK 容器安全策略（验签组件）用同一把，staging 命名空间只允许带有效签名、来自本 ACR 实例的镜像运行（由人配置）。
  阿里云 KMS 不是 cosign 内置的 KMS 提供方，所以私钥以文件形式存在凭据管家里；改用 KMS 托管私钥（不出 KMS）的做法待人确认。
- 待人确认：cosign 只用本地公钥验签时是否仍会联网初始化 Sigstore 的 TUF 信任根（构建机出网只放行 ACR，联网会失败而不是放行）。

## 4a. 不连上游
- syft 关闭新版本检查（`SYFT_CHECK_FOR_APP_UPDATE=false`），trivy 关闭版本检查与遥测（`TRIVY_SKIP_VERSION_CHECK`、`TRIVY_DISABLE_TELEMETRY`）；
  漏洞库只从 ACR 拉。`ACR_REGISTRY` 按锚定正则核对（只收 `<实例名>-registry-vpc.cn-beijing.cr.aliyuncs.com/<命名空间>` 整串）；
  脚本结束时（含失败）`docker logout`，构建机上不留登录态。

## 5. 部署
- 流水线输出 `dist/image-digests.txt`（`<短名> <仓库>@sha256:<摘要>`）；由人把 `deploy/k8s/staging.yaml` 里的 `ACR_REGISTRY/starbuddy/starbuddy-<服务>@sha256:DIGEST`
  换成对应摘要（只按摘要部署，不用标签）。

## 6. 许可证
syft（Apache-2.0）、trivy（Apache-2.0）、cosign（Apache-2.0）只在构建机上作工具使用，不进镜像、不分发。
