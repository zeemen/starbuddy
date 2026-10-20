"""镜像与编排的静态校验（LT-01 范围第 1、3、6 条；本机拉不到基础镜像时这是镜像规则的主要验证）：
Dockerfile：基础镜像只经无缺省值的 ARG 给出（由构建脚本强制 @sha256）；没有 ADD、没有远程下载；没有构建期密钥（ARG / ENV 不带
  TOKEN、SECRET、PASSWORD、KEY 一类的名字，构建命令不带 --secret 之外的凭据）；pip 只按带哈希的锁或 --no-deps 装本地包；npm 只用 npm ci；
  最后一个阶段以数字 uid 10001 运行；有 HEALTHCHECK；同名 .dockerignore 是白名单、排除 .env / 测试 / 夹具 / legacy，COPY 的来源都在白名单里。
编排：除一次性数据库外每个容器只读根文件系统、非 root、去掉全部 capability、禁止提权；不用 host 网络、不开特权；staging 的地址都在合成内网段；
  不出现真实域名（只允许 .invalid 等保留名）。
清单：每个容器非 root、只读根文件系统、禁止提权、去掉 capability、有资源请求与上限、有探针；镜像以摘要占位；密钥只经 secretKeyRef；
  不挂 ServiceAccount 令牌；有默认拒绝出网的 NetworkPolicy。"""
from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest

from conftest import DEPLOY, ROOT

DOCKERFILES = [ROOT / "services/learning/Dockerfile", *sorted((DEPLOY / "docker").glob("*.Dockerfile"))]
SECRETISH = re.compile(r"(TOKEN|SECRET|PASSW|PASSWD|PRIVATE|CREDENTIAL|HMAC|API_?KEY|_KEY\b)", re.I)
ADAPTERS = ("aya", "zhixing", "wenli")


def instructions(path: Path) -> list[tuple[str, str]]:
    """(指令, 参数)；续行合并、去注释"""
    out, buf = [], ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        buf += (" " if buf else "") + line.rstrip("\\").strip()
        if line.endswith("\\"):
            continue
        parts = buf.split(None, 1)
        out.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
        buf = ""
    return out


def stages(ins):
    cur = []
    allst = [cur]
    for op, arg in ins:
        if op == "FROM":
            cur = [(op, arg)]
            allst.append(cur)
        else:
            cur.append((op, arg))
    return [s for s in allst if s]


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.name)
def test_dockerfile_rules(path):
    ins = instructions(path)
    image_args = {a.split("=", 1)[0] for op, a in ins if op == "ARG" and a.split("=", 1)[0].endswith("_IMAGE")}
    for op, arg in ins:
        if op == "ARG" and arg.split("=", 1)[0].endswith("_IMAGE"):
            assert "=" not in arg, f"{path.name}：基础镜像 ARG 不能有缺省值（必须由构建脚本以摘要给出）"
        if op in ("ARG", "ENV"):
            for name in re.findall(r"([A-Z_][A-Z0-9_]*)=", arg) + ([arg.split("=")[0].strip()] if op == "ARG" else []):
                assert not SECRETISH.search(name), f"{path.name}：构建期不能有密钥类变量 {name}"
        assert op != "ADD", f"{path.name}：不用 ADD（远程下载与自动解压）"
        if op == "RUN":
            assert not re.search(r"\b(curl|wget)\b|https?://", arg), f"{path.name}：RUN 里不能下载远程内容"
            assert "--secret" not in arg or "type=secret" in arg
            if "pip install" in arg:
                for seg in arg.split("&&"):
                    if "pip install" in seg:
                        assert "--require-hashes" in seg or ("--no-deps" in seg and "./packages/" in seg), f"{path.name}：pip 只能按哈希锁或 --no-deps 装本地包"
            if re.search(r"\bnpm\b", arg):
                assert re.search(r"\bnpm ci\b", arg) and "--ignore-scripts" in arg, f"{path.name}：npm 只能 npm ci --ignore-scripts"
                assert "npm install" not in arg
    for op, arg in ins:
        if op == "FROM":
            base = arg.split()[0]
            m = re.fullmatch(r"\$\{([A-Z_]+)\}", base)
            assert m and m.group(1) in image_args, f"{path.name}：FROM 必须用无缺省值的 *_IMAGE ARG（得到 {base}）"
    final = stages(ins)[-1]
    users = [a for op, a in final if op == "USER"]
    assert users and users[-1].split(":")[0] == "10001", f"{path.name}：最后阶段必须以 uid 10001 运行"
    assert any(op == "HEALTHCHECK" for op, _ in final), f"{path.name}：缺 HEALTHCHECK"
    assert any(op == "ENV" and "STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}" in a for op, a in final), f"{path.name}：缺镜像环境标记（S10）"
    assert any(op == "RUN" and "STARBUDDY_IMAGE_ENV" in a and "staging|test" in a for op, a in final), f"{path.name}：镜像环境标记须核对取值"
    assert any(op in ("CMD", "ENTRYPOINT") for op, _ in final)


def ignore_rules(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.name)
def test_dockerignore_whitelist_covers_copies(path):
    ign = path.with_name(path.name + ".dockerignore")
    assert ign.is_file(), f"{path.name} 缺同名 .dockerignore"
    rules = ignore_rules(ign)
    assert rules[0] == "*", f"{ign.name}：必须以 * 开头（白名单）"
    for must in ("**/.env*",):
        assert must in rules, f"{ign.name}：缺 {must}"
    assert "legacy" in rules and ("**/tests" in rules or "**/fixtures" in rules), f"{ign.name}：须再次排除 legacy 与测试 / 夹具"
    allow = [r[1:] for r in rules if r.startswith("!")]
    assert not any(a.startswith(("legacy", ".env")) or "/.env" in a or a.endswith("/tests") for a in allow)
    for op, arg in instructions(path):
        if op != "COPY" or "--from=" in arg:
            continue
        srcs = [s for s in arg.split() if not s.startswith("--")][:-1]
        for s in srcs:
            cands = [s.replace("${ADAPTER}", a) for a in ADAPTERS] if "${ADAPTER}" in s else [s]
            for c in cands:
                c = c.rstrip("/")
                assert any(c == a or c.startswith(a + "/") for a in allow), f"{path.name}：COPY 来源 {c} 不在白名单里"


def test_build_script_enforces_digest():
    text = (DEPLOY / "scripts/build-images.sh").read_text(encoding="utf-8")
    assert "@sha256:" in text and "ALLOW_TAG" in text and '--build-arg STARBUDDY_IMAGE_ENV="$IMAGE_ENV"' in text
    for df in DOCKERFILES:
        assert str(df.relative_to(ROOT)) in text, f"构建脚本没有覆盖 {df.relative_to(ROOT)}"


def _all_strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _all_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _all_strings(v)
    elif isinstance(o, str):
        yield o


DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|cn|net|org|io|cloud|xyz|top|aliyuncs\.com|myqcloud\.com)\b", re.I)


@pytest.mark.parametrize("name", ["staging", "local-test"])
def test_compose_hardening(name, compose_staging, compose_local):
    doc = compose_staging if name == "staging" else compose_local
    for svc, spec in doc["services"].items():
        assert not spec.get("privileged") and spec.get("network_mode") != "host", svc
        if svc == "db":
            continue
        assert spec.get("read_only") is True, f"{svc}：只读根文件系统"
        assert str(spec.get("user", "")).split(":")[0] == "10001", f"{svc}：非 root"
        assert spec.get("cap_drop") == ["ALL"] and "no-new-privileges:true" in spec.get("security_opt", []), svc
        img = spec["image"]
        assert img.startswith("${STARBUDDY_REGISTRY:?") and ":${STARBUDDY_IMAGE_TAG:?" in img, f"{svc}：镜像必须由运行者显式给出"
    for s in _all_strings(doc):
        assert not DOMAIN.search(s), f"{name}：出现了真实域名样式的取值 {s[:60]}"
    if name == "staging":
        assert doc["services"]["bff"]["environment"]["BFF_TRUST_CLIENT_IP"] == "0", "S5：网关核对之前不信任转交的客户端 IP"
        net = ipaddress.ip_network(doc["networks"]["sbnet"]["ipam"]["config"][0]["subnet"])
        assert net.is_private
        for svc, spec in doc["services"].items():
            if "networks" in spec:
                ip = ipaddress.ip_address(spec["networks"]["sbnet"]["ipv4_address"])
                assert ip in net, svc
            else:
                assert spec.get("network_mode") == "service:learning" and svc.startswith("adapter-"), f"{svc}：只有适配器与学习服务共用网络命名空间"
            assert "ports" not in spec or all(str(p).startswith("127.0.0.1:") for p in spec["ports"]), f"{svc}：只发布到本机回环"
    else:
        assert doc["networks"]["sblocal"].get("internal") is True
        for svc, spec in doc["services"].items():
            assert "ports" not in spec, f"local-test {svc}：不发布端口"
            if svc != "db":
                assert spec["network_mode"] == "service:db"


def test_k8s_rules(k8s_docs):
    deployments = [d for d in k8s_docs if d["kind"] == "Deployment"]
    assert {d["metadata"]["name"] for d in deployments} == {"learning", "knowledge", "insight", "review-console", "bff", "web", "scheduler"}
    for d in deployments:
        pod = d["spec"]["template"]["spec"]
        assert pod.get("automountServiceAccountToken") is False, d["metadata"]["name"]
        sc = pod["securityContext"]
        assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 10001 and sc["seccompProfile"]["type"] == "RuntimeDefault"
        for c in pod["containers"]:
            where = f"{d['metadata']['name']}/{c['name']}"
            csc = c["securityContext"]
            assert csc["readOnlyRootFilesystem"] is True and csc["allowPrivilegeEscalation"] is False and csc["capabilities"]["drop"] == ["ALL"], where
            assert c["resources"]["requests"] and c["resources"]["limits"], where
            assert c.get("readinessProbe") or c.get("livenessProbe"), where
            assert re.fullmatch(r"REGISTRY/starbuddy-[a-z-]+@sha256:DIGEST", c["image"]), where
            for e in c.get("env", []):
                if SECRETISH.search(e["name"]) or e["name"].endswith("DATABASE_URL"):
                    assert "valueFrom" in e and "secretKeyRef" in e["valueFrom"], f"{where}：{e['name']} 必须经 secretKeyRef"
    bff = next(d for d in deployments if d["metadata"]["name"] == "bff")
    benv = {e["name"]: e.get("value") for e in bff["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert benv["BFF_TRUST_CLIENT_IP"] == "0", "S5：网关核对之前不信任转交的客户端 IP"
    services = {d["metadata"]["name"]: d for d in k8s_docs if d["kind"] == "Service"}
    for name, svc in services.items():
        if "clusterIP" in svc["spec"]:
            assert ipaddress.ip_address(svc["spec"]["clusterIP"]).is_private, name
    pols = [d for d in k8s_docs if d["kind"] == "NetworkPolicy"]
    assert any("Egress" in p["spec"]["policyTypes"] and p["spec"]["podSelector"] == {} for p in pols), "缺默认拒绝出网的 NetworkPolicy"
    assert not any(d["kind"] == "Secret" for d in k8s_docs), "仓库里不提交 Secret"
    for s in _all_strings(k8s_docs):
        if re.fullmatch(r"[a-z.]*k8s\.io/v1", s):  # Kubernetes API 组名，不是部署地址
            continue
        assert not DOMAIN.search(s), f"清单里出现了真实域名样式的取值 {s[:60]}"
