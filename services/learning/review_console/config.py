"""配置：全部来自环境变量，不写进仓库；只允许 dev / test / staging（CLAUDE.md 规则 9，spec 待人确认 #3：只部署在 dev / staging）。

  REVIEW_CONSOLE_ENV                 dev | test | staging（staging：LT-01 决定记录第 5 条；REVIEW_CONSOLE_FEATURES 开发开关在 staging 仍拒绝）
  REVIEW_CONSOLE_HOST                监听地址，缺省 127.0.0.1；只有 staging 能设为内网 IPv4（RFC 1918）
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

封测周修订支持（KGE-06，specs/kge06-weekly-revision；可选，缺省全关）：
  REVIEW_CONSOLE_FEATURES            逗号分隔：tag_worklist（低置信打标候选工作单）、revision_preflight（修订发布预检）。
                                     只在 dev / test 生效（本服务本来就只允许这两个环境）；未知名字拒绝启动。关着时对应接口不注册（404）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from learning.netpolicy import SERVICE_ENVS, check_image_env, listen_host

from .pages import check_mr_url_template
from .spot_check import SpotCheckSettings

ALLOWED_ENVS = ("dev", "test")  # 开发开关（REVIEW_CONSOLE_FEATURES）与命令行导出仍只认这两个；服务本身另收 staging（SERVICE_ENVS）
FEATURES = ("tag_worklist", "revision_preflight")


def parse_features(raw: str, env: str) -> frozenset[str]:
    """REVIEW_CONSOLE_FEATURES → 打开的功能。缺省全关；只在 dev / test 能开；名字不认识拒绝启动（不静默忽略拼错的开关）。"""
    names = [x.strip() for x in raw.split(",") if x.strip()]
    if not names:
        return frozenset()
    if env not in ALLOWED_ENVS:
        raise RuntimeError("REVIEW_CONSOLE_FEATURES 只能在 dev / test 打开")
    unknown = sorted(set(names) - set(FEATURES))
    if unknown:
        raise RuntimeError(f"REVIEW_CONSOLE_FEATURES 有不认识的功能（只认 {', '.join(FEATURES)}）")
    return frozenset(names)


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
    features: frozenset[str] = frozenset()       # KGE-06：打开的功能（缺省全关）
    host: str = "127.0.0.1"                      # LT-01：监听地址（只有 staging 能改为内网 IPv4）

    @staticmethod
    def from_env(environ: dict[str, str] | None = None) -> "ReviewSettings":
        e = dict(os.environ if environ is None else environ)
        env = e.get("REVIEW_CONSOLE_ENV", "")
        if env not in SERVICE_ENVS:
            raise RuntimeError(f"REVIEW_CONSOLE_ENV 必须是 {SERVICE_ENVS} 之一；审核台 v0 不连接 pilot 或生产环境")
        check_image_env(env, e)
        host = listen_host(e.get("REVIEW_CONSOLE_HOST"), env, "REVIEW_CONSOLE_HOST")
        kge, review = e.get("REVIEW_CONSOLE_KGE_DATABASE_URL", ""), e.get("REVIEW_CONSOLE_REVIEW_DATABASE_URL", "")
        if not kge or not review:
            raise RuntimeError("缺少 REVIEW_CONSOLE_KGE_DATABASE_URL 或 REVIEW_CONSOLE_REVIEW_DATABASE_URL")
        if kge == review:
            raise RuntimeError("提交与签字必须用两个不同的数据库登录角色")
        return ReviewSettings(env=env, kge_database_url=kge, review_database_url=review, pages=PagesSettings.from_env(e),
                              spot_check=SpotCheckSettings.from_env(e),
                              features=parse_features(e.get("REVIEW_CONSOLE_FEATURES", ""), env), host=host)
