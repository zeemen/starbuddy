"""LT-03 镜像流水线模板（deploy/ci）的静态校验：本机不构建、不推送，只核对脚本与模板的口径。
- 语法正确；镜像清单与构建脚本、Dockerfile 一致；
- 基础镜像只收 ACR 里按摘要锁定的镜像；只构建 staging 标记；不收 latest；
- SBOM、扫描（高危即失败、漏洞库从 ACR 拉）、签名与推送后验签（不上传公共透明日志）都在；不处理口令、不写死仓库地址；
  仓库地址按锚定正则核对；syft / trivy 不连上游；结束登出 ACR；
- 参数不对时在构建之前以退出码 2 拒绝（只跑参数核对分支，不调 docker）；
- 云效模板只手动触发、调用 pipeline.sh、取值全是占位。"""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest
import yaml

from conftest import DEPLOY, ROOT

CI = DEPLOY / "ci"
PIPE = CI / "pipeline.sh"


def code(path) -> str:
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))


def images():
    out = []
    for ln in (CI / "images.txt").read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.startswith("#"):
            name, file, base, extra = ln.split()
            out.append((name, file, base, extra))
    return out


@pytest.mark.skipif(shutil.which("bash") is None, reason="没有 bash")
def test_pipeline_syntax():
    r = subprocess.run(["bash", "-n", str(PIPE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_image_list_matches_build_script_and_dockerfiles():
    build = (DEPLOY / "scripts" / "build-images.sh").read_text(encoding="utf-8")
    for name, file, base, extra in images():
        assert (ROOT / file).is_file(), file
        assert base in ("PYTHON_IMAGE", "NODE_IMAGE")
        df = (ROOT / file).read_text(encoding="utf-8")
        assert f"ARG {base}" in df, f"{file} 不用 {base}"
        if name.startswith("adapter-"):
            assert extra == f"ADAPTER={name.split('-', 1)[1]}"
            assert 'deploy/docker/adapter.Dockerfile --build-arg NODE_IMAGE="$NODE_IMAGE" --build-arg ADAPTER="$a"' in build
        else:
            assert extra == "-"
            assert re.search(rf"build {re.escape(name)} {re.escape(file)} --build-arg {base}=", build), name
    assert len(images()) == 10


def test_pipeline_rules():
    c = code(PIPE)
    assert "deploy/ci/images.txt" in c
    assert "DIGEST='@sha256:[0-9a-f]{64}$'" in c and '[[ "$val" == "$ACR_REGISTRY/"* ]]' in c, "基础镜像只收 ACR 里按摘要锁定的"
    assert "-registry-vpc.cn-beijing.cr.aliyuncs.com/" in c, "只推华北 2 的 ACR 企业版 VPC 端点"
    assert "--build-arg STARBUDDY_IMAGE_ENV=staging" in c and '"$TAG" != "latest"' in c
    assert re.search(r"syft .*spdx-json", c)
    assert "trivy image --scanners vuln,secret --severity HIGH,CRITICAL --exit-code 1" in c
    assert '--db-repository "$TRIVY_DB_REPOSITORY"' in c and '--java-db-repository "$TRIVY_JAVA_DB_REPOSITORY"' in c
    cosign = [ln for ln in c.splitlines() if re.search(r"\bcosign (sign|attest)\b", ln)]
    assert cosign and all("--tlog-upload=false" in ln for ln in cosign), "签名信息不上传公共透明日志"
    assert "docker push" in c and "@sha256:" in c and "dist/image-digests.txt" in c
    # 安全审查 B3：推送签名后立即验签（只认本地公钥），验不过整条失败；没有不验签的模式
    verify = [ln for ln in c.splitlines() if re.search(r"\bcosign verify(-attestation)?\b", ln)]
    assert len(verify) == 2 and all('--key "$COSIGN_PUB_REF"' in ln and "--insecure-ignore-tlog=true" in ln for ln in verify)
    assert '|| die "验签失败（$name）"' in c and '|| die "SBOM 证明验签失败（$name）"' in c
    order = [c.index(s) for s in ("docker push", "cosign sign", "cosign verify --", 'echo "$name $ref" >> dist/image-digests.txt')]
    assert order == sorted(order), "推送 → 签名 → 验签 → 记录摘要"
    assert 'SIGN_MODE="${SIGN_MODE:-cosign}"' in c and "acr|cosign" not in c
    # 锚定的仓库正则、不连上游、结束登出
    assert "REGISTRY_RE='^" in c and "\\.cn-beijing\\.cr\\.aliyuncs\\.com/" in c and '[[ "$ACR_REGISTRY" =~ $REGISTRY_RE ]]' in c
    assert "SYFT_CHECK_FOR_APP_UPDATE=false" in c and "TRIVY_SKIP_VERSION_CHECK=true" in c and "TRIVY_DISABLE_TELEMETRY=true" in c
    assert "trap 'docker logout \"$REGISTRY_HOST\"" in c and "EXIT" in c
    # 不处理口令：不登录、不读口令变量；不写死任何真实仓库地址（只有 VPC 端点的后缀样式）
    assert not re.search(r"docker login|--password|PASSWORD|_TOKEN|ACCESS_KEY", c)
    assert not re.search(r"[a-z0-9]-registry-vpc\.cn-beijing", c), "不写死 ACR 实例名（只有 *-registry-vpc 的样式核对）"


@pytest.mark.skipif(shutil.which("bash") is None, reason="没有 bash")
@pytest.mark.parametrize("env,msg", [
    ({}, "缺 ACR_REGISTRY"),
    ({"ACR_REGISTRY": "registry.example.invalid/starbuddy"}, "VPC 端点"),
    # 锚定：前后夹带别的主机或路径都不收
    ({"ACR_REGISTRY": "evil.example.invalid/syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy"}, "VPC 端点"),
    ({"ACR_REGISTRY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com.evil.example.invalid/starbuddy"}, "VPC 端点"),
    ({"ACR_REGISTRY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy/extra"}, "VPC 端点"),
    ({"ACR_REGISTRY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy", "PYTHON_IMAGE": "python:3.11", "NODE_IMAGE": "node:22"}, "@sha256"),
    ({"ACR_REGISTRY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy",
      "PYTHON_IMAGE": "docker.example.invalid/python@sha256:" + "a" * 64, "NODE_IMAGE": "docker.example.invalid/node@sha256:" + "b" * 64},
     "来自 ACR"),
    ({"ACR_REGISTRY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy",
      "PYTHON_IMAGE": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy/starbuddy-base-python@sha256:" + "a" * 64,
      "NODE_IMAGE": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy/starbuddy-base-node@sha256:" + "b" * 64,
      "TRIVY_DB_REPOSITORY": "ghcr.example.invalid/trivy-db", "TRIVY_JAVA_DB_REPOSITORY": "ghcr.example.invalid/trivy-java-db"},
     "漏洞库"),
    ({"ACR_REGISTRY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy",
      "PYTHON_IMAGE": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy/starbuddy-base-python@sha256:" + "a" * 64,
      "NODE_IMAGE": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy/starbuddy-base-node@sha256:" + "b" * 64,
      "TRIVY_DB_REPOSITORY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/mirror/trivy-db",
      "TRIVY_JAVA_DB_REPOSITORY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/mirror/trivy-java-db", "SIGN_MODE": "acr"},
     "SIGN_MODE"),
    ({"ACR_REGISTRY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy",
      "PYTHON_IMAGE": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy/starbuddy-base-python@sha256:" + "a" * 64,
      "NODE_IMAGE": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/starbuddy/starbuddy-base-node@sha256:" + "b" * 64,
      "TRIVY_DB_REPOSITORY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/mirror/trivy-db",
      "TRIVY_JAVA_DB_REPOSITORY": "syn-registry-vpc.cn-beijing.cr.aliyuncs.com/mirror/trivy-java-db",
      "COSIGN_KEY_REF": "/nonexistent/cosign.key"},
     "COSIGN_KEY_REF"),
])
def test_pipeline_refuses_bad_params_before_building(env, msg):
    r = subprocess.run(["bash", str(PIPE)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env}, cwd=ROOT, timeout=30)
    assert r.returncode == 2 and msg in r.stdout, (r.returncode, r.stdout, r.stderr)
    assert not (ROOT / "dist").exists(), "参数核对不过时不产生任何产物"


def test_flow_template_manual_and_placeholders():
    doc = yaml.safe_load((CI / "flow.yaml").read_text(encoding="utf-8"))
    src = next(iter(doc["sources"].values()))
    assert src["triggerEvents"] == [], "只手动触发"
    for k, v in doc["variables"].items():
        if k != "SIGN_MODE":
            assert str(v).startswith("<由人填写"), k
    assert doc["variables"]["SIGN_MODE"] == "cosign"
    for k in ("COSIGN_KEY_REF", "COSIGN_PUB_REF"):
        assert str(doc["variables"][k]).startswith("<由人填写"), k
    steps = next(iter(next(iter(doc["stages"].values()))["jobs"].values()))["steps"]
    assert any(s.get("with", {}).get("run") == "bash deploy/ci/pipeline.sh" for s in steps.values())
    text = (CI / "flow.yaml").read_text(encoding="utf-8")
    assert not re.search(r"password|accessKey|secret", text, re.I)
