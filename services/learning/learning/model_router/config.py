"""路由表加载与启动校验（spec 设计要点 2）。任何一条不满足就抛 ConfigError，服务不启动。

路由表按环境分节；仓库里只提交 dev、test 两节（假供应商），staging、pilot、prod 由人按白名单流程填写（必停项）。
供应商的地址与 Key 只写环境变量名，值不进仓库、不进日志。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .types import ConfigError, DataClass, Residency, Task

ENVIRONMENTS = ("dev", "test", "staging", "pilot", "prod")
# 生产与试点：不出境（用户决定 2026-09-28，含 L0、L1）、只用白名单里已批准的真实供应商
NO_EGRESS_ENVS = frozenset({"pilot", "prod"})
PROTOCOLS = frozenset({"openai_compat", "fake"})
WHITELIST = frozenset({"approved", "pending", "test_only"})
# 锁定快照：日期或版本号；不接受 latest 之类会漂移的别名（ADR 04 增补第 4 条）
_VERSION = re.compile(r"^(\d{4}-\d{2}-\d{2}|v?\d+(\.\d+){0,3})$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_FLOATING = re.compile(r"(latest|preview|default|auto)", re.IGNORECASE)
NO_BACKUP_TASKS = frozenset({Task.T7})  # 向量模型选定即锁版本，不做主备切换（增补第 5 条）


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    org: str  # 厂商
    platform: str  # 提供服务的平台（同一模型可能经不同平台提供）
    residency: Residency
    protocol: str
    whitelist: str
    base_url_env: str
    api_key_env: str | None


@dataclass(frozen=True)
class Target:
    vendor: str
    model: str
    version: str
    api_name: str  # 发给供应商的模型名（锁定快照）
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def model_version(self) -> str:
        """契约的 model:<供应商>/<模型>@<版本> 格式"""
        return f"model:{self.vendor}/{self.model}@{self.version}"


@dataclass(frozen=True)
class Route:
    task: Task
    data_classes: frozenset[DataClass]
    primary: Target
    backup: Target | None
    timeout_seconds: float
    max_retries: int

    @property
    def targets(self) -> tuple[Target, ...]:
        return (self.primary,) if self.backup is None else (self.primary, self.backup)


@dataclass(frozen=True)
class RouterConfig:
    env: str
    vendors: dict[str, Vendor]
    routes: dict[Task, Route]
    failure_threshold: int
    cooldown_seconds: float


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def _target(raw: Any, where: str) -> Target:
    _need(isinstance(raw, dict), f"{where}: 必须是映射")
    extra = set(raw) - {"vendor", "model", "version", "api_name", "params"}
    _need(not extra, f"{where}: 未知键 {sorted(extra)}")
    model, version = str(raw.get("model", "")), str(raw.get("version", ""))
    api_name = str(raw.get("api_name", model))
    _need(bool(_NAME.match(model)) and bool(_NAME.match(api_name)), f"{where}: 模型名不合格式")
    _need(bool(_VERSION.match(version)), f"{where}: 必须锁定快照版本（日期或版本号）")
    _need(not _FLOATING.search(model) and not _FLOATING.search(api_name), f"{where}: 不能用 latest 之类会漂移的别名")
    params = raw.get("params", {}) or {}
    _need(isinstance(params, dict) and set(params) <= {"temperature", "top_p", "max_tokens", "seed"}, f"{where}: params 只收推理参数")
    return Target(vendor=str(raw.get("vendor", "")), model=model, version=version, api_name=api_name, params=dict(params))


def parse(doc: Any, env: str) -> RouterConfig:
    """解析并校验一个环境的路由表"""
    _need(env in ENVIRONMENTS, f"未知环境 {env!r}")
    _need(isinstance(doc, dict) and doc.get("version") == 1, "路由表版本必须是 1")
    defaults = doc.get("defaults") or {}
    timeouts = defaults.get("timeout_seconds") or {}
    breaker = defaults.get("breaker") or {}
    max_retries_default = int(defaults.get("max_retries", 2))
    envs = doc.get("environments") or {}
    _need(isinstance(envs, dict) and env in envs, f"路由表没有 {env} 一节")
    section = envs[env] or {}
    raw_vendors = section.get("vendors") or {}
    raw_routes = section.get("routes") or {}
    _need(isinstance(raw_vendors, dict) and isinstance(raw_routes, dict), f"{env}: vendors 与 routes 必须是映射")

    vendors: dict[str, Vendor] = {}
    for vid, v in raw_vendors.items():
        where = f"{env}.vendors.{vid}"
        _need(isinstance(v, dict), f"{where}: 必须是映射")
        try:
            residency = Residency(v.get("residency"))
        except ValueError:
            raise ConfigError(f"{where}: residency 只能是 domestic 或 foreign") from None
        protocol, whitelist = v.get("protocol"), v.get("whitelist")
        _need(protocol in PROTOCOLS, f"{where}: 未知协议")
        _need(whitelist in WHITELIST, f"{where}: whitelist 只能是 approved、pending、test_only")
        base_url_env, api_key_env = v.get("base_url_env"), v.get("api_key_env")
        _need(isinstance(base_url_env, str) and bool(_ENV_NAME.match(base_url_env)), f"{where}: base_url_env 必须是环境变量名（不写地址本身）")
        _need(api_key_env is None or (isinstance(api_key_env, str) and bool(_ENV_NAME.match(api_key_env))),
              f"{where}: api_key_env 必须是环境变量名（不写 Key 本身）")
        if env in NO_EGRESS_ENVS:
            _need(residency is Residency.DOMESTIC, f"{where}: {env} 环境不能出现境外供应商（数据不出境）")
            _need(whitelist == "approved", f"{where}: {env} 环境只能用白名单里已批准的供应商")
            _need(protocol != "fake", f"{where}: {env} 环境不能用假供应商")
        else:
            _need(whitelist != "approved" or protocol != "fake", f"{where}: 假供应商不能标为 approved")
        vendors[str(vid)] = Vendor(vendor_id=str(vid), org=str(v.get("org", vid)), platform=str(v.get("platform", vid)),
                                   residency=residency, protocol=str(protocol), whitelist=str(whitelist),
                                   base_url_env=base_url_env, api_key_env=api_key_env)

    routes: dict[Task, Route] = {}
    for tname, r in raw_routes.items():
        where = f"{env}.routes.{tname}"
        try:
            task = Task(tname)
        except ValueError:
            raise ConfigError(f"{where}: 未知任务类型") from None
        _need(isinstance(r, dict), f"{where}: 必须是映射")
        extra = set(r) - {"data_classes", "primary", "backup", "timeout_seconds", "max_retries", "dual_run"}
        _need(not extra, f"{where}: 未知键 {sorted(extra)}")
        _need(not r.get("dual_run", False), f"{where}: dual_run（主备双跑对比）本任务未实现，只能是 false（spec 待人确认 #6）")
        try:
            classes = frozenset(DataClass(c) for c in (r.get("data_classes") or []))
        except ValueError:
            raise ConfigError(f"{where}: data_classes 只收 L0–L3") from None
        _need(bool(classes), f"{where}: 必须声明允许的数据级别")
        primary = _target(r.get("primary"), f"{where}.primary")
        backup = _target(r["backup"], f"{where}.backup") if r.get("backup") is not None else None
        _need(not (task in NO_BACKUP_TASKS and backup is not None), f"{where}: 检索的向量模型不做主备切换")
        for t in (primary, backup):
            if t is None:
                continue
            _need(t.vendor in vendors, f"{where}: 供应商 {t.vendor!r} 未在本环境登记")
            if any(c.rank >= 2 for c in classes):
                _need(vendors[t.vendor].residency is Residency.DOMESTIC, f"{where}: 可达 L2、L3 的任务只能配境内供应商")
        if backup is not None:
            a, b = vendors[primary.vendor], vendors[backup.vendor]
            _need(not (a.org == b.org and a.platform == b.platform), f"{where}: 主备必须分属不同厂商或不同平台")
        timeout = float(r.get("timeout_seconds", timeouts.get(task.value, timeouts.get("default", 30))))
        retries = int(r.get("max_retries", max_retries_default))
        _need(0 < timeout <= 600 and 0 <= retries <= 5, f"{where}: 超时或重试次数超出范围")
        routes[task] = Route(task=task, data_classes=classes, primary=primary, backup=backup, timeout_seconds=timeout, max_retries=retries)

    threshold = int(breaker.get("failure_threshold", 5))
    cooldown = float(breaker.get("cooldown_seconds", 30))
    _need(1 <= threshold <= 100 and 0 < cooldown <= 3600, "熔断参数超出范围")
    return RouterConfig(env=env, vendors=vendors, routes=routes, failure_threshold=threshold, cooldown_seconds=cooldown)


def load(path: str | Path, env: str) -> RouterConfig:
    with open(path, encoding="utf-8") as f:
        return parse(yaml.safe_load(f), env)


DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "model_routes.yaml"
