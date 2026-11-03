"""LT-02 真实模型供应商接入（specs/launch-lt02-model-provider）的部署侧核对：
- 路由表 staging 节登记的供应商地址 / Key 变量都在环境变量清单里、标为密钥、只在 staging；
- 开关矩阵 model_provider 仍 pending 时，staging 编排（compose、k8s）不设路由客户端令牌、供应商变量与适配器的路由地址
  （设了令牌而路由表占位未填，学习服务会拒绝启动）；
- 出网白名单：只有学习服务 Pod 带模型供应商主机名的注解；注解是占位，或与路由表 allowed_hosts 一致；
- 不论 pending 与否：供应商变量与路由令牌只经密钥注入（含注释掉、待解开的接线行）；
- 回环路由监听端口（LEARNING_ROUTE_PORT）在清单、k8s、compose、部署文档与 *_ROUTE_URL 里一致。"""
from __future__ import annotations

import re

import yaml

from conftest import ROOT, load_yaml

ROUTES = ROOT / "services" / "learning" / "config" / "model_routes.yaml"
ANNOTATION = "starbuddy/model-egress-hosts"
MODEL_VARS = re.compile(r"^(MODEL_[A-Z0-9_]+|LEARNING_ROUTE_CLIENT_[A-Z0-9]+_TOKEN|[A-Z]+_ROUTE_(URL|TOKEN))$")


def staging_vendors() -> dict:
    return (load_yaml(ROUTES)["environments"].get("staging") or {}).get("vendors") or {}


def pending(flags) -> bool:
    return any("model_provider" in (e.get("pending") or []) for e in [*flags["subjects"], *flags["features"]])


def test_route_vendor_vars_are_registered_as_staging_secrets(inventory):
    reg = {v["name"]: v for v in inventory["services"]["learning"]["vars"]}
    names = [n for v in staging_vendors().values() for n in (v.get("base_url_env"), v.get("api_key_env")) if n]
    assert names, "staging 节应登记了供应商变量名"
    for n in names:
        assert n in reg, f"{n} 没有登记在 env-inventory 的 learning 名下"
        assert reg[n]["secret"] is True and reg[n]["envs"] == "staging", n


def _containers(k8s_docs):
    for d in k8s_docs:
        if d["kind"] == "Deployment":
            for c in d["spec"]["template"]["spec"]["containers"]:
                yield d, c


def test_no_model_wiring_while_model_provider_pending(flags, k8s_docs, compose_staging):
    if not pending(flags):
        return  # 人解开 pending 之后由评审核对接线
    for d, c in _containers(k8s_docs):
        for e in c.get("env", []):
            assert not MODEL_VARS.match(e["name"]), f"k8s {d['metadata']['name']}/{c['name']}：{e['name']} 要等模型供应商开通后再设"
    for svc, spec in compose_staging["services"].items():
        env = spec.get("environment") or {}
        names = env if isinstance(env, dict) else [x.split("=", 1)[0] for x in env]
        for n in names:
            assert not MODEL_VARS.match(n), f"compose {svc}：{n} 要等模型供应商开通后再设"


CRED_VARS = re.compile(r"^(MODEL_[A-Z0-9_]+|LEARNING_ROUTE_CLIENT_[A-Z0-9]+_TOKEN|[A-Z]+_ROUTE_TOKEN)$")
K8S = ROOT / "deploy" / "k8s" / "staging.yaml"
COMPOSE = ROOT / "deploy" / "compose" / "staging.yaml"
README = ROOT / "deploy" / "config" / "README.md"
INVENTORY = ROOT / "deploy" / "config" / "env-inventory.yaml"


def _commented_k8s_env(text: str) -> list[dict]:
    """k8s 清单里注释掉的接线行（# - {name: …}）：解开注释后也必须合规，所以一并核对"""
    out = []
    for m in re.finditer(r"^\s*#\s*-\s*(\{name:.*\})\s*$", text, re.M):
        item = yaml.safe_load(m.group(1))
        if isinstance(item, dict) and "name" in item:
            out.append(item)
    return out


def _commented_compose_env(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2).strip()) for m in re.finditer(r"^\s*#\s+([A-Z][A-Z0-9_]+):\s*(\S+)", text, re.M)]


def test_vendor_and_route_credentials_only_injected_from_secrets(k8s_docs, compose_staging):
    """安全审查建议 3：不论 model_provider 是否 pending，MODEL_*、路由客户端令牌与 *_ROUTE_TOKEN 只经 secretKeyRef / ${…:?} 注入，
    不写字面值（含注释掉、待解开的接线行）"""
    entries = [e for _d, c in _containers(k8s_docs) for e in c.get("env", [])] + _commented_k8s_env(K8S.read_text(encoding="utf-8"))
    checked = 0
    for e in entries:
        if CRED_VARS.match(e["name"]):
            checked += 1
            assert "value" not in e and "secretKeyRef" in (e.get("valueFrom") or {}), f"k8s：{e['name']} 必须经 secretKeyRef"
    compose_pairs = [(n, str(v)) for spec in compose_staging["services"].values()
                     for n, v in ((spec.get("environment") or {}).items() if isinstance(spec.get("environment"), dict) else [])]
    compose_pairs += _commented_compose_env(COMPOSE.read_text(encoding="utf-8"))
    for name, value in compose_pairs:
        if CRED_VARS.match(name):
            checked += 1
            assert re.fullmatch(r"\$\{[A-Z0-9_]+:\?[^}]*\}", value), f"compose：{name} 必须写成 ${{VAR:?…}} 从运行环境注入"
    assert checked >= 8, "注释里的接线行也要被核对到（MODEL_* 四个、两科令牌）"


def _route_port_values() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for p in (K8S, COMPOSE, README, INVENTORY):
        text = p.read_text(encoding="utf-8")
        found[p.name] = set(re.findall(r"_ROUTE_URL[^\n]{0,40}?http://127\.0\.0\.1:(\d+)", text))
    return found


def test_loopback_route_port_is_consistent(inventory, k8s_docs, compose_staging):
    """决定记录（续）第 5 条：学习服务回环路由监听的端口在清单缺省、k8s、compose 与各处 *_ROUTE_URL 里一致，且不与其他端口冲突"""
    reg = {v["name"]: v for v in inventory["services"]["learning"]["vars"]}
    port = reg["LEARNING_ROUTE_PORT"]["default"]
    assert reg["LEARNING_ROUTE_PORT"]["envs"] == "staging"
    k8s_learning = next(d for d in k8s_docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "learning")
    containers = {c["name"]: {e["name"]: e.get("value") for e in c.get("env", [])} for c in k8s_learning["spec"]["template"]["spec"]["containers"]}
    assert containers["learning"]["LEARNING_ROUTE_PORT"] == port
    assert str(compose_staging["services"]["learning"]["environment"]["LEARNING_ROUTE_PORT"]) == port
    used = {containers["learning"]["PORT"], *(env["PORT"] for name, env in containers.items() if name != "learning")}
    assert port not in used, "回环路由端口不能与学习服务或适配器的端口相同"
    found = _route_port_values()
    for name, ports in found.items():
        assert ports == {port}, f"{name} 里的 *_ROUTE_URL 端口 {sorted(ports)} 与 LEARNING_ROUTE_PORT={port} 不一致（或没有写明）"


def test_model_egress_annotation_only_on_learning_and_matches_routes(k8s_docs):
    seen = {}
    for d in k8s_docs:
        if d["kind"] != "Deployment":
            continue
        ann = (d["spec"]["template"]["metadata"].get("annotations") or {}).get(ANNOTATION)
        if ann is not None:
            seen[d["metadata"]["name"]] = ann
    assert set(seen) == {"learning"}, "只有学习服务（模型路由）可以出网到模型供应商"
    value = seen["learning"]
    hosts = sorted({h for v in staging_vendors().values() for h in (v.get("allowed_hosts") or [])})
    if value.startswith("<") and value.endswith(">"):
        return  # 仍是占位：由人填写
    assert sorted(x.strip() for x in re.split(r"[,，、\s]+", value) if x.strip()) == hosts, "注解与路由表 allowed_hosts 不一致"
