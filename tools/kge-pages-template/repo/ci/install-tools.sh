#!/usr/bin/env bash
# 在无凭据的合并请求任务里安装检查工具：StarBuddy services/knowledge 的固定版本预构建只读制品。
#   用法：bash <main 检出>/ci/install-tools.sh <目标目录>      之后 KNOWLEDGE_DIR=<目标目录>/services/knowledge
# - 制品地址与 sha256 只从 main 检出里的 ci/kge-tools.pin 读（平台负责人维护，CODEOWNERS 审核）；本脚本不 source 该文件，只取两行值；
# - 下载不带任何凭据（制品在内网匿名只读可取；需要鉴权的仓库请改用预构建镜像，凭据配在运行器上，不进任务）；只接受 https；
# - 先核对 sha256 再解包；依赖按制品里的 requirements.lock 安装，pip 一律 --require-hashes（哈希不符即失败）。
# 退出码：0 完成；2 参数、固定版本文件或校验失败。
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="${1:-}"
py="${PYTHON:-python3}"
if [ -z "$dest" ]; then echo "install-tools: 需要目标目录" >&2; exit 2; fi
pin="$here/kge-tools.pin"
if [ ! -f "$pin" ] || [ -L "$pin" ]; then echo "install-tools: 缺少 ci/kge-tools.pin" >&2; exit 2; fi
url="$(sed -n 's/^KGE_TOOLS_URL=//p' "$pin" | head -n 1)"
sha="$(sed -n 's/^KGE_TOOLS_SHA256=//p' "$pin" | head -n 1)"
case "$url" in
  https://*) ;;
  *) echo "install-tools: KGE_TOOLS_URL 必须是 https 地址" >&2; exit 2 ;;
esac
if ! [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || [ "$sha" = "0000000000000000000000000000000000000000000000000000000000000000" ]; then
  echo "install-tools: KGE_TOOLS_SHA256 不是有效的 sha256（占位值必须替换）" >&2; exit 2
fi
mkdir -p "$dest"
archive="$dest/kge-tools.tar.gz"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 --output "$archive" "$url"
if ! echo "$sha  $archive" | sha256sum --check --quiet -; then
  echo "install-tools: 制品的 sha256 与 ci/kge-tools.pin 不符" >&2; exit 2
fi
tar -xzf "$archive" -C "$dest" --no-same-owner --no-same-permissions
lock="$dest/services/knowledge/requirements.lock"
if [ ! -f "$lock" ]; then echo "install-tools: 制品里没有 services/knowledge/requirements.lock" >&2; exit 2; fi
"$py" -m pip install --quiet --no-input --require-hashes -r "$lock"
echo "install-tools: 已安装到 $dest/services/knowledge"
