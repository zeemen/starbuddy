"""配置：全部来自环境变量，不写进仓库；只允许 dev / test（CLAUDE.md 规则 9，spec 待人确认 #3：只部署在 dev / staging）。

  REVIEW_CONSOLE_ENV                 dev | test
  REVIEW_CONSOLE_KGE_DATABASE_URL    读草稿、改草稿用；登录角色只继承 starbuddy_kge
  REVIEW_CONSOLE_REVIEW_DATABASE_URL 签字、发布、撤下用；登录角色只继承 starbuddy_review
两个连接串必须是两个不同的登录角色（spec 设计要点 3：提交方不能自己签字），启动时核对。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ALLOWED_ENVS = ("dev", "test")


@dataclass(frozen=True)
class ReviewSettings:
    env: str
    kge_database_url: str
    review_database_url: str

    @staticmethod
    def from_env(environ: dict[str, str] | None = None) -> "ReviewSettings":
        e = dict(os.environ if environ is None else environ)
        env = e.get("REVIEW_CONSOLE_ENV", "")
        if env not in ALLOWED_ENVS:
            raise RuntimeError(f"REVIEW_CONSOLE_ENV 必须是 {ALLOWED_ENVS} 之一；审核台 v0 不连接 pilot 或生产环境")
        kge, review = e.get("REVIEW_CONSOLE_KGE_DATABASE_URL", ""), e.get("REVIEW_CONSOLE_REVIEW_DATABASE_URL", "")
        if not kge or not review:
            raise RuntimeError("缺少 REVIEW_CONSOLE_KGE_DATABASE_URL 或 REVIEW_CONSOLE_REVIEW_DATABASE_URL")
        if kge == review:
            raise RuntimeError("提交与签字必须用两个不同的数据库登录角色")
        return ReviewSettings(env=env, kge_database_url=kge, review_database_url=review)
