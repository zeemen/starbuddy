"""契约审查阻断 2：每个调用方 *_URL 的主机，都在被调方的允许 Host 列表里（否则 HostGuard 回 421，staging 起不来）。

- k8s：调用方经 Service 的 clusterIP 访问，Host 头是 clusterIP。带 Host 校验的三个服务（知识、洞察、审核台）各有一个
  staging 专用的“额外允许 Host”（KNOWLEDGE_EXTRA_HOST / INSIGHT_EXTRA_HOST / REVIEW_CONSOLE_EXTRA_HOST），必须恰好等于本服务
  Service 的 clusterIP；学习服务与 BFF 没有 Host 校验，只核对地址是被调方 Service 的 clusterIP 与端口。
- compose：调用方直接用被调方的固定内网地址，等于被调方的监听地址（*_HOST，HostGuard 收监听地址）与 ipv4_address。
只读仓库文件。"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

# 调用方变量 → （被调方 Deployment / compose 服务名，被调方的监听地址变量，被调方的额外允许 Host 变量；None = 没有 Host 校验）
CALLS = {
    "BFF_LEARNING_URL": ("learning", "LEARNING_HOST", None),
    "BFF_INSIGHT_URL": ("insight", "INSIGHT_HOST", "INSIGHT_EXTRA_HOST"),
    "LEARNING_KNOWLEDGE_URL": ("knowledge", "KNOWLEDGE_HOST", "KNOWLEDGE_EXTRA_HOST"),
    "INSIGHT_LEARNING_URL": ("learning", "LEARNING_HOST", None),
    "STARBUDDY_REVIEW_URL": ("review-console", "REVIEW_CONSOLE_HOST", "REVIEW_CONSOLE_EXTRA_HOST"),
    "STARBUDDY_BFF_URL": ("bff", "BFF_HOST", None),
}
GUARDED = {"knowledge": "KNOWLEDGE_EXTRA_HOST", "insight": "INSIGHT_EXTRA_HOST", "review-console": "REVIEW_CONSOLE_EXTRA_HOST"}


def _k8s(k8s_docs):
    deps = {d["metadata"]["name"]: d for d in k8s_docs if d["kind"] == "Deployment"}
    svcs = {d["metadata"]["name"]: d for d in k8s_docs if d["kind"] == "Service"}
    return deps, svcs


def _env(dep) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in dep["spec"]["template"]["spec"]["containers"]:
        for e in c.get("env", []):
            out[e["name"]] = e
    return out


def test_k8s_every_caller_url_is_allowed_by_callee(k8s_docs):
    deps, svcs = _k8s(k8s_docs)
    seen = set()
    for name, dep in deps.items():
        for var, e in _env(dep).items():
            if var not in CALLS:
                continue
            seen.add(var)
            callee, _listen, extra = CALLS[var]
            u = urlsplit(e["value"])
            svc = svcs[callee]["spec"]
            assert u.scheme == "http" and u.hostname == svc["clusterIP"], f"{name}.{var} 必须指向 {callee} 的 Service clusterIP"
            assert u.port == svc["ports"][0]["port"], f"{name}.{var} 端口与 {callee} 的 Service 不一致"
            if extra is not None:
                callee_env = _env(deps[callee])
                assert extra in callee_env, f"{callee} 缺少 {extra}：经 clusterIP 的调用会被 Host 校验拒绝（421）"
                assert callee_env[extra]["value"] == u.hostname, f"{callee}.{extra} 必须等于 {name}.{var} 的主机"
    assert seen == set(CALLS), f"清单里的调用在 k8s 里缺了：{set(CALLS) - seen}"


def test_k8s_extra_host_is_exactly_own_cluster_ip(k8s_docs):
    deps, svcs = _k8s(k8s_docs)
    for name, dep in deps.items():
        env = _env(dep)
        extras = [v for v in env if v.endswith("_EXTRA_HOST")]
        if name in GUARDED:
            assert extras == [GUARDED[name]], name
            ip = env[GUARDED[name]]["value"]
            assert ip == svcs[name]["spec"]["clusterIP"], f"{name}：额外允许 Host 只能是本服务的 clusterIP"
            assert ipaddress.IPv4Address(ip).is_private and ipaddress.IPv4Address(ip) in ipaddress.IPv4Network("10.0.0.0/8")
        else:
            assert extras == [], f"{name}：没有 Host 校验的服务不设额外允许 Host"


def test_compose_every_caller_url_is_callee_listen_address(compose_staging):
    services = compose_staging["services"]
    seen = set()
    for name, spec in services.items():
        for var, value in (spec.get("environment") or {}).items():
            if var not in CALLS:
                continue
            seen.add(var)
            callee, listen, _extra = CALLS[var]
            u = urlsplit(str(value))
            cenv = services[callee]["environment"]
            assert u.hostname == cenv[listen], f"compose {name}.{var} 必须等于 {callee}.{listen}（HostGuard 收监听地址）"
            assert u.hostname == services[callee]["networks"]["sbnet"]["ipv4_address"], f"compose {name}.{var}"
            assert not any(k.endswith("_EXTRA_HOST") for k in cenv), "compose 直连监听地址，不需要额外允许 Host"
    assert seen == set(CALLS), f"清单里的调用在 compose 里缺了：{set(CALLS) - seen}"
