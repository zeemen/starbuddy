"""环境变量清单（deploy/config/env-inventory.yaml，LT-01 范围第 2 条）：名字真实存在；编排文件只用登记过的名字；密钥只经注入；
staging 必填的都在 staging 编排里；只在 dev / test 的开发开关不进 staging 编排。"""
from __future__ import annotations

import re

from names import name_exists, read_sources

COMPOSE_TO_INVENTORY = {"learning": "learning", "insight": "insight", "knowledge": "knowledge", "scheduler": "scheduler",
                        "review-console": "review_console", "bff": "bff", "web": "web", "adapter-aya": "adapter-aya",
                        "adapter-zhixing": "adapter-zhixing", "adapter-wenli": "adapter-wenli"}
# 调度器把各身份的变量转给子进程：它的编排环境也可以用洞察服务、学习服务、审核台登记的名字
SCHEDULER_PASSTHROUGH = ("insight", "learning", "review_console")
# 镜像与编排自己的变量（不归服务源码）：前端的监听地址与审核台端口由容器启动命令使用
CONTAINER_VARS = {"review_console": {"REVIEW_CONSOLE_PORT"}}


def registered(inventory, svc):
    return {v["name"]: v for v in inventory["services"][svc]["vars"]}


def matches(name, reg):
    if name in reg:
        return reg[name]
    for k, v in reg.items():
        if "<" in k:
            prefix, rest = k.split("<", 1)
            suffix = rest.split(">", 1)[1]
            if name.startswith(prefix) and name.endswith(suffix) and re.fullmatch(r"[A-Z0-9]+", name[len(prefix): len(name) - len(suffix) or None]):
                return v
    return None


def test_inventory_names_exist(inventory):
    assert inventory["schema"] == "starbuddy-env-inventory/1"
    for svc, spec in inventory["services"].items():
        text = read_sources(spec["sources"])
        names = [v["name"] for v in spec["vars"]]
        assert len(names) == len(set(names)), f"{svc} 有重复登记"
        for v in spec["vars"]:
            assert set(v) <= {"name", "purpose", "secret", "default", "envs", "required_in_staging"}, v
            assert v["envs"] in ("all", "local", "staging"), v
            assert isinstance(v["secret"], bool) and v["purpose"], v
            if v["name"] == "PORT" and svc.startswith("adapter-"):
                assert "process.env.PORT" in text, svc
                continue
            assert name_exists(v["name"], text), f"{svc}：{v['name']} 不在 {spec['sources']}"


def _env_of(service_spec):
    env = service_spec.get("environment") or {}
    return env if isinstance(env, dict) else dict(x.split("=", 1) for x in env)


def _lookup(inventory, svc, name):
    if name in CONTAINER_VARS.get(COMPOSE_TO_INVENTORY[svc], set()):
        return {"secret": False, "envs": "all"}
    hit = matches(name, registered(inventory, COMPOSE_TO_INVENTORY[svc]))
    if hit is None and svc == "scheduler":
        for other in SCHEDULER_PASSTHROUGH:
            hit = hit or matches(name, registered(inventory, other))
    return hit


def test_compose_uses_only_registered_names_and_injects_secrets(inventory, compose_staging, compose_local):
    for label, doc in (("staging", compose_staging), ("local-test", compose_local)):
        for svc, spec in doc["services"].items():
            if svc == "db":
                continue
            for name, value in _env_of(spec).items():
                hit = _lookup(inventory, svc, name)
                assert hit is not None, f"{label} {svc}：{name} 没有登记"
                value = str(value)
                if hit["secret"]:
                    literal_ok = label == "local-test" and value.startswith("postgresql://") and "@127.0.0.1:" in value \
                        and not re.match(r"postgresql://[^@/]*:[^@/]*@", value)
                    assert value.startswith("${") or literal_ok, f"{label} {svc}：密钥 {name} 必须由运行环境注入"
                if label == "staging":
                    assert hit["envs"] != "local", f"staging {svc}：{name} 只在 dev / test"


def test_staging_required_present(inventory, compose_staging):
    for svc, spec in compose_staging["services"].items():
        inv = COMPOSE_TO_INVENTORY[svc]
        env = _env_of(spec)
        for v in inventory["services"][inv]["vars"]:
            if not v.get("required_in_staging"):
                continue
            if "<" in v["name"]:
                assert any(matches(k, {v["name"]: v}) for k in env), f"staging {svc} 缺 {v['name']}"
            else:
                assert v["name"] in env, f"staging {svc} 缺 {v['name']}"
