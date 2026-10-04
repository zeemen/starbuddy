"""配置：全部来自环境变量，不写进仓库；只允许 dev / test（CLAUDE.md 规则 9，spec 待人确认 #3：只部署在 dev / staging）。

  REVIEW_CONSOLE_ENV                 dev | test
  REVIEW_CONSOLE_KGE_DATABASE_URL    读草稿、改草稿用；登录角色只继承 starbuddy_kge
  REVIEW_CONSOLE_REVIEW_DATABASE_URL 签字、发布、撤下用；登录角色只继承 starbuddy_review
两个连接串必须是两个不同的登录角色（spec 设计要点 3：提交方不能自己签字），启动时核对。

编译页面入口（冲刺 3，可选；三项要么都设、要么都不设，不设时页面入口显示“未配置”）：
  REVIEW_CONSOLE_PAGES_EXPORT        托管平台合并记录导出文件（kge-pages-merge-export/1，由人或同步任务定期写入；本服务不连托管平台）
  REVIEW_CONSOLE_PAGES_STAFF_MAP     托管平台用户名 → staff 账号对应表（kge-pages-staff-map/1，由人维护，不含学生数据）
  REVIEW_CONSOLE_PAGES_MR_URL        合并请求网页地址模板，https，含且仅含一个 {mr_id}

低风险事后抽检（冲刺 4，可选；比例与窗口的默认值待教研负责人定，见 spot_check.py）：
  REVIEW_CONSOLE_SPOT_CHECK_SEED（必须由人配置，仓库里没有缺省种子；不设则抽检接口回 503 SPOT_CHECK_UNCONFIGURED）
  REVIEW_CONSOLE_SPOT_CHECK_RATIO / REVIEW_CONSOLE_MERGE_WINDOW
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .pages import check_mr_url_template
from .spot_check import SpotCheckSettings

ALLOWED_ENVS = ("dev", "test")


@dataclass(frozen=True)
class PagesSettings:
    export_path: str
    staff_map_path: str
    mr_url_template: str

    @staticmethod
    def from_env(e: dict[str, str]) -> "PagesSettings | None":
        vals = [e.get(k, "") for k in ("REVIEW_CONSOLE_PAGES_EXPORT", "REVIEW_CONSOLE_PAGES_STAFF_MAP", "REVIEW_CONSOLE_PAGES_MR_URL")]
        if not any(vals):
            return None
        if not all(vals):
            raise RuntimeError("编译页面入口的三项配置（导出文件、staff 对应表、合并请求地址模板）要么都设、要么都不设")
        return PagesSettings(vals[0], vals[1], check_mr_url_template(vals[2]))


@dataclass(frozen=True)
class ReviewSettings:
    env: str
    kge_database_url: str
    review_database_url: str
    pages: PagesSettings | None = None
    spot_check: SpotCheckSettings | None = None  # 没设种子时为空：抽检接口报错，不用缺省种子

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
        return ReviewSettings(env=env, kge_database_url=kge, review_database_url=review, pages=PagesSettings.from_env(e),
                              spot_check=SpotCheckSettings.from_env(e))
