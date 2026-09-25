"""配置：全部来自环境变量，不写进仓库。

只允许开发与测试环境（CLAUDE.md 规则 9：不接触生产）。pilot 与生产的部署配置由人决定。
  LEARNING_ENV                   dev | test
  LEARNING_DATABASE_URL          asyncpg 连接串；登录角色只继承 starbuddy_learning
  LEARNING_SERVICE_TOKEN         BFF → 学习服务的服务间令牌（≥ 32 字符）
  LEARNING_ADAPTER_<SUBJECT>_URL    例如 LEARNING_ADAPTER_AYA_URL=http://127.0.0.1:8787
  LEARNING_ADAPTER_<SUBJECT>_TOKEN  学习服务 → 该科适配器的服务间令牌
"""
from __future__ import annotations

import os
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
    # pending 租约（spec 待人确认 #5）：比适配器超时加一次重试更长
    lease_seconds: float = 60.0
    grade_timeout: float = 10.0
    diagnose_timeout: float = 30.0

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
                adapters[subject] = AdapterTarget(url.rstrip("/"), e.get(f"LEARNING_ADAPTER_{subject.upper()}_TOKEN", ""))
        return Settings(env=env, database_url=database_url, service_credential=credential, adapters=adapters)
