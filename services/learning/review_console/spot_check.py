"""低风险合并窗口的事后抽检（spec 第 2 节“程序体检通过后进每日合并窗口……事后按比例抽检”、冲刺 4 验收“事后抽检按固定种子抽样，结果记审核记录”）。

纯函数：输入是托管平台合并记录导出（pages.MergeExport）与一天（北京时间），输出当天低风险合并里被抽中的页面改动。
- 候选：当天（按 merged_at 换算北京时间）合并进主分支、风险为 low 的合并请求里的每个页面（页面 ID + 头提交；签字与对账按头提交记）；
- 抽样：固定种子 + 日期 + 页面 + 合并提交（merge_commit）取 SHA-256，按哈希排序取前 ceil(比例 × 候选数) 个；同样的输入与配置，
  结果逐字节相同，不依赖 Python 随机数实现，事后能复核“当天该抽哪几页”。哈希用合并提交而不用头提交：头提交在提交方手里，
  提交方能反复改头提交、按公开的算法挑一个抽不中的再提；合并提交在合并之后才确定（契约审查）；
- 种子必须由配置给出，仓库里没有缺省种子（公开的缺省种子等于让提交方预先算出哪些页会被抽中）；
- 抽检结果由审核人在审核台签字记录：kge.sign_page_review(页面, 头提交, 结论, 'low', 'spot_check')（迁移 0038，审核记录只追加）。
  对账（pages.reconcile）把依据为 spot_check 的记录与事前签字分开：不计入批准人比对；抽检驳回则扣页（spot_check_rejected）。

配置位（spec 第 2 节：抽检比例、合并窗口时间由教研负责人定，ADR 12 待人确认 #3；这里只留配置，默认值待定）：
  REVIEW_CONSOLE_SPOT_CHECK_SEED    固定种子（必填才启用抽检；1–64 个字母、数字、下划线、连字符；由人在部署配置里设，不进仓库）
  REVIEW_CONSOLE_SPOT_CHECK_RATIO   抽检比例（0–1 的小数；默认 0.1，待教研负责人定；为 0 时照常运行但接口返回告警 ratio_zero）
  REVIEW_CONSOLE_MERGE_WINDOW       每日合并窗口时刻（北京时间 HH:MM；默认 02:00，待教研负责人定；合并窗口本身在 KGE-03 编译器里执行，
                                    这里只用于界面说明）
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from dataclasses import dataclass

from .pages import MergeExport

SHANGHAI = dt.timezone(dt.timedelta(hours=8))
DEFAULT_RATIO = 0.1  # 待教研负责人定（ADR 12 待人确认 #3）
DEFAULT_WINDOW = "02:00"  # 待教研负责人定（ADR 12 待人确认 #3）
SPOT_CHECK_BASIS = "spot_check"
_SEED = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WINDOW = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


@dataclass(frozen=True)
class SpotCheckSettings:
    seed: str
    ratio: float = DEFAULT_RATIO
    window: str = DEFAULT_WINDOW

    def __post_init__(self) -> None:
        if not isinstance(self.seed, str) or not _SEED.fullmatch(self.seed):
            raise RuntimeError("REVIEW_CONSOLE_SPOT_CHECK_SEED 只能是 1–64 个字母、数字、下划线、连字符")
        if not isinstance(self.ratio, (int, float)) or math.isnan(self.ratio) or not (0.0 <= self.ratio <= 1.0):
            raise RuntimeError("REVIEW_CONSOLE_SPOT_CHECK_RATIO 必须是 0–1 的小数")
        if not _WINDOW.fullmatch(self.window):
            raise RuntimeError("REVIEW_CONSOLE_MERGE_WINDOW 必须是北京时间 HH:MM")

    @staticmethod
    def from_env(e: dict[str, str]) -> "SpotCheckSettings | None":
        """没设种子时返回 None（抽检未配置：接口报错，不用任何缺省种子）；比例、窗口写错照样启动失败。"""
        raw = e.get("REVIEW_CONSOLE_SPOT_CHECK_RATIO", "")
        try:
            ratio = float(raw) if raw else DEFAULT_RATIO
        except ValueError:
            raise RuntimeError("REVIEW_CONSOLE_SPOT_CHECK_RATIO 必须是 0–1 的小数") from None
        if math.isnan(ratio) or not (0.0 <= ratio <= 1.0):
            raise RuntimeError("REVIEW_CONSOLE_SPOT_CHECK_RATIO 必须是 0–1 的小数")
        window = e.get("REVIEW_CONSOLE_MERGE_WINDOW", "") or DEFAULT_WINDOW
        if not _WINDOW.fullmatch(window):
            raise RuntimeError("REVIEW_CONSOLE_MERGE_WINDOW 必须是北京时间 HH:MM")
        seed = e.get("REVIEW_CONSOLE_SPOT_CHECK_SEED", "")
        if not seed:
            return None
        return SpotCheckSettings(seed, ratio, window)


@dataclass(frozen=True)
class Candidate:
    page_id: str
    mr_id: str
    head_commit: str
    merge_commit: str


def local_day(t: dt.datetime) -> dt.date:
    return t.astimezone(SHANGHAI).date()


def candidates(export: MergeExport, day: dt.date) -> list[Candidate]:
    """当天（北京时间）合并的低风险页面改动，按页面、合并请求排序。"""
    out = [Candidate(p, m.mr_id, m.head_commit, m.merge_commit) for m in export.merge_requests
           if m.state == "merged" and m.risk == "low" and m.merged_at is not None and m.merge_commit is not None
           and local_day(m.merged_at) == day for p in m.pages]
    return sorted(set(out), key=lambda c: (c.page_id, int(c.mr_id), c.head_commit))


def sample_size(n: int, ratio: float) -> int:
    return 0 if n == 0 or ratio <= 0 else min(n, math.ceil(n * ratio - 1e-9))


def _rank(seed: str, day: dt.date, c: Candidate) -> str:
    return hashlib.sha256(f"{seed}|{day.isoformat()}|{c.page_id}|{c.merge_commit}".encode("utf-8")).hexdigest()


def sample(export: MergeExport, day: dt.date, settings: SpotCheckSettings) -> tuple[list[Candidate], list[Candidate]]:
    """（全部候选，抽中的）；抽中的按页面 ID 排序。"""
    pool = candidates(export, day)
    k = sample_size(len(pool), settings.ratio)
    picked = sorted(pool, key=lambda c: (_rank(settings.seed, day, c), c.page_id))[:k]
    return pool, sorted(picked, key=lambda c: (c.page_id, int(c.mr_id)))
