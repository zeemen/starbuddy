"""配置：全部来自环境变量，不写进仓库。

只允许开发与测试环境（CLAUDE.md 规则 9：不接触生产）。pilot 与生产的部署配置由人决定。
  LEARNING_ENV                   dev | test
  LEARNING_DATABASE_URL          asyncpg 连接串；登录角色只继承 starbuddy_learning
  LEARNING_SERVICE_TOKEN         BFF → 学习服务的服务间令牌（≥ 32 字符）
  LEARNING_ADAPTER_<SUBJECT>_URL    例如 LEARNING_ADAPTER_AYA_URL=http://127.0.0.1:8787
  LEARNING_ADAPTER_<SUBJECT>_TOKEN  学习服务 → 该科适配器的服务间令牌
  LEARNING_ROUTE_CLIENT_<SUBJECT>_TOKEN  该科适配器 → 学习服务内部模型路由接口的令牌（≥ 32 字符，令牌绑定学科；不设即该科不能调模型）
  自适应诊断（specs/sie-02a-math-diagnosis）：
  LEARNING_DIAGNOSIS_PRESENTER   不设 = 诊断接口回 501；synthetic = 合成题面（只允许 LEARNING_ENV=test，契约 present_item 签字前不接真实题库）
  LEARNING_DIAGNOSIS_SOURCE      fixture（缺省，合成夹具，只允许 test）| knowledge（经知识服务 node.path / item.search）
  LEARNING_KNOWLEDGE_URL / LEARNING_KNOWLEDGE_TOKEN   知识服务地址（https 或本机回环）与 caller=learning 的令牌（≥ 32 字符）
  LEARNING_DIAGNOSIS_TARGETS     knowledge 模式下的目标节点：JSON {"年级": [节点 ID…]}；LEARNING_DIAGNOSIS_MAP_VERSION 地图版本号
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

SUBJECTS = ("wenli", "aya", "zhixing", "echo")
ALLOWED_ENVS = ("dev", "test")


@dataclass(frozen=True)
class AdapterTarget:
    base_url: str
    credential: str


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    service_credential: str
    adapters: dict[str, AdapterTarget] = field(default_factory=dict)
    # 学科 → 路由客户端令牌（specs/adapter-zhixing-sprint2 设计要点 5）
    route_clients: dict[str, str] = field(default_factory=dict)
    # pending 租约（spec 待人确认 #5）：必须长于适配器调用的总预算（2 × 超时 + 1）再加核对与落库的余量
    # （安全审查 S6）。diagnose 总预算 61 秒，所以租约 90 秒。
    lease_seconds: float = 90.0
    grade_timeout: float = 10.0
    diagnose_timeout: float = 30.0
    # 自适应诊断（SIE-02a）
    diagnosis_presenter: str | None = None
    diagnosis_source: str = "fixture"
    knowledge: AdapterTarget | None = None
    diagnosis_targets: dict[int, list[str]] = field(default_factory=dict)
    diagnosis_map_version: str | None = None

    @staticmethod
    def from_env(environ: dict[str, str] | None = None) -> "Settings":
        e = dict(os.environ if environ is None else environ)
        env = e.get("LEARNING_ENV", "")
        if env not in ALLOWED_ENVS:
            raise RuntimeError(f"LEARNING_ENV 必须是 {ALLOWED_ENVS} 之一；本服务在冲刺 1 不连接 pilot 或生产环境")
        credential = e.get("LEARNING_SERVICE_TOKEN", "")
        if len(credential) < 32:
            raise RuntimeError("LEARNING_SERVICE_TOKEN 缺失或少于 32 个字符")
        database_url = e.get("LEARNING_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("缺少 LEARNING_DATABASE_URL")
        adapters = {}
        for subject in SUBJECTS:
            url = e.get(f"LEARNING_ADAPTER_{subject.upper()}_URL")
            if url:
                cred = e.get(f"LEARNING_ADAPTER_{subject.upper()}_TOKEN", "")
                if len(cred) < 32:
                    raise RuntimeError(f"LEARNING_ADAPTER_{subject.upper()}_TOKEN 缺失或少于 32 个字符")
                if not (url.startswith("https://") or re.match(r"http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)", url)):
                    raise RuntimeError(f"LEARNING_ADAPTER_{subject.upper()}_URL 必须是 https，或本机回环地址")
                adapters[subject] = AdapterTarget(url.rstrip("/"), cred)
        route_clients = {}
        for subject in SUBJECTS:
            tok = e.get(f"LEARNING_ROUTE_CLIENT_{subject.upper()}_TOKEN")
            if tok is not None:
                if len(tok) < 32:
                    raise RuntimeError(f"LEARNING_ROUTE_CLIENT_{subject.upper()}_TOKEN 少于 32 个字符")
                route_clients[subject] = tok
        presenter = e.get("LEARNING_DIAGNOSIS_PRESENTER") or None
        if presenter not in (None, "synthetic"):
            raise RuntimeError("LEARNING_DIAGNOSIS_PRESENTER 只能不设或为 synthetic")
        if presenter == "synthetic" and env != "test":
            raise RuntimeError("合成题面只允许 LEARNING_ENV=test（契约 present_item 签字前不接真实题库）")
        source = e.get("LEARNING_DIAGNOSIS_SOURCE") or "fixture"
        if source not in ("fixture", "knowledge"):
            raise RuntimeError("LEARNING_DIAGNOSIS_SOURCE 只能是 fixture 或 knowledge")
        if presenter and source == "fixture" and env != "test":
            raise RuntimeError("合成诊断夹具只允许 LEARNING_ENV=test")
        knowledge = None
        targets: dict[int, list[str]] = {}
        map_version = None
        if presenter and source == "knowledge":
            url, tok = e.get("LEARNING_KNOWLEDGE_URL", ""), e.get("LEARNING_KNOWLEDGE_TOKEN", "")
            if len(tok) < 32:
                raise RuntimeError("LEARNING_KNOWLEDGE_TOKEN 缺失或少于 32 个字符")
            if not (url.startswith("https://") or re.match(r"http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)", url)):
                raise RuntimeError("LEARNING_KNOWLEDGE_URL 必须是 https，或本机回环地址")
            knowledge = AdapterTarget(url.rstrip("/"), tok)
            try:
                raw = json.loads(e.get("LEARNING_DIAGNOSIS_TARGETS", ""))
                targets = {int(g): [str(n) for n in v] for g, v in raw.items()}
            except (ValueError, AttributeError, TypeError):
                raise RuntimeError("LEARNING_DIAGNOSIS_TARGETS 必须是 JSON {\"年级\": [节点 ID…]}") from None
            map_version = e.get("LEARNING_DIAGNOSIS_MAP_VERSION", "")
            if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,4}", map_version):
                raise RuntimeError("LEARNING_DIAGNOSIS_MAP_VERSION 必须是 主.次.修订")
        tokens = [credential, *(a.credential for a in adapters.values()), *route_clients.values(),
                  *([knowledge.credential] if knowledge else [])]
        if len(set(tokens)) != len(tokens):
            raise RuntimeError("服务间令牌、适配器令牌、路由客户端令牌、知识服务令牌必须各不相同")
        return Settings(env=env, database_url=database_url, service_credential=credential, adapters=adapters,
                        route_clients=route_clients, diagnosis_presenter=presenter, diagnosis_source=source, knowledge=knowledge,
                        diagnosis_targets=targets, diagnosis_map_version=map_version)
