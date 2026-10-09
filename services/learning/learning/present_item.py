"""从适配器取题面：插件 API 1.1.0 候选版的 present_item（PLT-12，specs/present-item-contract/spec.md）。

开关：LEARNING_PRESENT_ITEM_SOURCE=adapter|synthetic（缺省 synthetic）。adapter 只在候选契约开关 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1
时允许（配置层 Settings.from_env 与本模块构造各拦一道）；签字前不在任何环境启用。开关关闭时诊断与再练仍用合成题面，
行为与 PLT-12 之前逐字节一致（合成路径的代码一行未动，对拍测试见 tests/test_present_item.py）。

题面在返回 BFF 之前过两道白名单：
1. 适配器响应按契约 PresentItemResponse 校验，再用共享库 starbuddy_contract.present.project_presented_item 逐字段重建
   （答案类字段名、多余字段、超出受限富文本的内容一律整体拒绝）；
2. 本模块把重建后的题面转成诊断与再练的题面视图（ItemView），视图只有 format、stem（纯文字兜底）、options（键与纯文字）、
   rich（受限富文本：题干段落与选项片段），出口再由 clean_adapter_view 核一次键与形状。
题面不是关于学生的陈述（规则 11），不判分（规则 3），不计量（非消耗类，不带 usage_id）。请求用契约的 PresentItemContext：
不带学生 ID、不带幂等键，数据级别 L1（审查阻断 2）。题面与学生无关，按（学科, 题号, 内容版本）在进程内缓存，不同学生共用；
缓存条目 10 分钟过期（时钟可注入），过期后重新取。
只放行版权状态为 original、licensed、public 的题面；unverified 与 restricted 都按没有题面（审查建议 5）。
题库里没有可展示的题干（content_state = withheld）、版权未核或受限、诊断与再练不出的题型（简答）、或适配器没有这个方法
（停在 1.0 的学科：CONTRACT_VERSION_UNSUPPORTED、404 INVALID_REQUEST、任何 501）时返回 None：调用方回 501
PRESENTER_UNAVAILABLE，平台不自行补写题面。
取题面是网络调用，不放在数据库事务与 advisory 锁里：事务内只用 present_cached 查缓存，没命中抛 ViewNotCached，
调用方回滚后在事务外 present_view 预取、再重做（审查建议 7；learning/diagnosis/service.py）。
"""
from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import httpx

from starbuddy_contract.checks import ContractViolation
from starbuddy_contract.present import plain_text, project_presented_item

from .adapter_client import call_adapter
from .config import AdapterTarget
from .contract import UnsupportedContractVersion, plugin_contract
from .diagnosis.presenter import FORMATS, ItemView
from .errors import IngestError

CONTRACT = "1.1.0"
TIMEOUT = 5.0
CACHE_SIZE = 4096
CACHE_TTL_SEC = 600.0  # 10 分钟
FORMAT_OF = {"single_choice": "single", "multi_choice": "multi", "fill_blank": "fill"}
# 能对学生展示的版权状态（审查建议 5）：unverified 与 restricted 都不展示
SHOWABLE_LICENSES = frozenset({"original", "licensed", "public"})
VIEW_KEYS = frozenset({"format", "stem", "options", "rich"})
RICH_KEYS = frozenset({"stem", "options"})
# 适配器拒绝时视为“这道题没有题面”的上游编码（其余失败按暂不可用处理，可重试）。CONTRACT_VERSION_UNSUPPORTED：
# 停在 1.0 的学科（例如文砺）不认 1.1.0（审查建议 8）
NOT_PRESENTABLE = frozenset({"ITEM_NOT_FOUND", "CONTENT_VERSION_NOT_FOUND", "DISCIPLINE_NOT_SUPPORTED", "METHOD_NOT_IMPLEMENTED",
                             "CONTRACT_VERSION_UNSUPPORTED"})

ContentVersionOf = Callable[[str, str], Awaitable[dict | None]]


class ViewNotCached(Exception):
    """事务内查缓存没命中：调用方回滚、在事务外取题面后重做（不把网络调用放进事务与锁里）。"""

    def __init__(self, subject: str, item_id: str) -> None:
        super().__init__("item view is not cached")
        self.subject = subject
        self.item_id = item_id


def not_presentable(e: IngestError) -> bool:
    """适配器的这次拒绝是不是“这道题没有题面”（而不是暂时不可用）：已知编码、404 INVALID_REQUEST（未知路由）、任何 501。"""
    if e.upstream_code in NOT_PRESENTABLE or e.upstream_status == 501:
        return True
    return e.upstream_status == 404 and e.upstream_code == "INVALID_REQUEST"


def require_candidate() -> None:
    """present_item 只在 1.1.0 候选版里有：候选开关没开时拒绝启用（启动即失败，不等到第一个请求）。"""
    try:
        plugin_contract(CONTRACT)
    except UnsupportedContractVersion:
        raise RuntimeError("LEARNING_PRESENT_ITEM_SOURCE=adapter 需要候选契约 1.1.0：签字前只在测试里开"
                           "（STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1）") from None


def adapter_view(projected: dict) -> ItemView | None:
    """契约题面（已过共享库白名单）→ 诊断与再练的题面视图。不能展示的返回 None。"""
    fmt = FORMAT_OF.get(projected["item_type"])
    if fmt is None or projected["content_state"] != "complete" or projected["license_state"] not in SHOWABLE_LICENSES:
        return None
    stem = projected["stem"]
    view = ItemView(format=fmt, stem="\n".join(plain_text(p["runs"]) for p in stem))
    rich: dict = {"stem": [{"type": "paragraph", "runs": [dict(r) for r in p["runs"]]} for p in stem]}
    if fmt in ("single", "multi"):
        opts = projected.get("options") or []
        view["options"] = [{"key": o["key"], "text": plain_text(o["content"])} for o in opts]
        rich["options"] = [{"key": o["key"], "content": [dict(r) for r in o["content"]]} for o in opts]
    view["rich"] = rich
    return clean_adapter_view(view)


def clean_adapter_view(view: dict) -> ItemView:
    """适配器题面视图的出口白名单（第二道）：键与形状不对就拒绝（不是静默丢弃）。"""
    if set(view) - VIEW_KEYS or not {"format", "stem", "rich"} <= set(view) or view["format"] not in FORMATS:
        raise ValueError("adapter item view has fields outside the whitelist")
    if not isinstance(view["stem"], str) or not view["stem"]:
        raise ValueError("adapter item view has no stem")
    rich = view["rich"]
    if not isinstance(rich, dict) or set(rich) - RICH_KEYS or "stem" not in rich:
        raise ValueError("adapter item view has a malformed rich stem")
    # 富文本部分再按契约白名单重建一次（与适配器响应同一份共享库代码），保证没有多余字段混进来
    probe = {"item_ref": "aya:x", "item_version": "x@1", "item_type": "fill_blank", "content_state": "complete",
             "answer_format": {"kind": "expression", "max_length": 1}, "license_state": "original", "stem": rich["stem"]}
    if view["format"] in ("single", "multi"):
        probe.update(item_type="single_choice" if view["format"] == "single" else "multi_choice", options=rich.get("options"),
                     answer_format={"kind": "choice", "min_choices": 1, "max_choices": 1})
        keys = [o.get("key") for o in view.get("options") or []]
        if keys != [o.get("key") for o in rich.get("options") or []] or any(set(o) != {"key", "text"} for o in view["options"]):
            raise ValueError("adapter item view options do not match")
    elif "options" in view or "options" in rich:
        raise ValueError("fill item view has options")
    try:
        cleaned = project_presented_item(probe)
    except ContractViolation:
        raise ValueError("adapter item view rich text is outside the whitelist") from None
    out = ItemView(format=view["format"], stem=view["stem"])
    if "options" in view:
        out["options"] = [{"key": o["key"], "text": o["text"]} for o in view["options"]]
    out["rich"] = {"stem": cleaned["stem"], **({"options": cleaned["options"]} if "options" in cleaned else {})}
    return out


class AdapterItemSource:
    """经适配器 present_item 取题面，带进程内缓存（题面与学生无关；同一内容版本下逐字节不变，契约 present_item 说明）。
    缓存键是（学科, 题号, 内容版本），条目 ttl 秒后过期（clock 可注入，测试用）。"""

    def __init__(self, client: httpx.AsyncClient, adapters: dict[str, AdapterTarget], content_version_of: ContentVersionOf,
                 *, timeout: float = TIMEOUT, cache_size: int = CACHE_SIZE, ttl: float = CACHE_TTL_SEC,
                 clock: Callable[[], float] = time.monotonic) -> None:
        require_candidate()
        self.client = client
        self.adapters = dict(adapters)
        self.content_version_of = content_version_of
        self.timeout = timeout
        self.cache_size = cache_size
        self.ttl = ttl
        self.clock = clock
        self._cache: OrderedDict[tuple[str, str, str], tuple[float, ItemView | None]] = OrderedDict()
        self.calls = 0  # 调适配器的次数（测试用）

    @staticmethod
    def _copy(view: ItemView | None) -> ItemView | None:
        return None if view is None else ItemView(json.loads(json.dumps(view)))

    async def _key(self, subject: str, item_id: str) -> tuple[tuple[str, str, str] | None, dict | None]:
        if subject not in self.adapters:
            return None, None
        cv = await self.content_version_of(subject, item_id)
        if cv is None:
            return None, None
        return (subject, item_id, json.dumps(cv, sort_keys=True)), cv

    def _lookup(self, key: tuple[str, str, str]) -> tuple[bool, ItemView | None]:
        hit = self._cache.get(key)
        if hit is None:
            return False, None
        if self.clock() >= hit[0]:
            del self._cache[key]  # 过期：重新取
            return False, None
        self._cache.move_to_end(key)
        return True, self._copy(hit[1])

    async def cached(self, subject: str, item_id: str) -> ItemView | None:
        """只查缓存（事务内用，不发网络请求）。没有这一科或内容版本 → None；缓存没命中 → ViewNotCached。"""
        key, _ = await self._key(subject, item_id)
        if key is None:
            return None
        found, view = self._lookup(key)
        if not found:
            raise ViewNotCached(subject, item_id)
        return view

    async def fetch(self, subject: str, item_id: str) -> ItemView | None:
        key, cv = await self._key(subject, item_id)
        if key is None or cv is None:
            return None
        found, view = self._lookup(key)
        if found:
            return view
        view = await self._call(self.adapters[subject], subject, item_id, cv)
        self._cache[key] = (self.clock() + self.ttl, view)
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return self._copy(view)

    async def _call(self, target: AdapterTarget, subject: str, item_id: str, cv: dict) -> ItemView | None:
        contract = plugin_contract(CONTRACT)
        item_ref = f"{subject}:{item_id}"
        # PresentItemContext：不带学生 ID、幂等键与计量 ID；不关联学生，L1
        req = {"context": {"request_id": str(uuid.uuid4()), "contract_version": CONTRACT, "subject": subject, "data_class": "L1"},
               "item_ref": item_ref, "content_version": dict(cv)}
        if contract.validate("PresentItemRequest", req):
            return None  # 题号或内容版本不合契约（例如登记的内容版本不全）：这道题没有题面
        self.calls += 1
        try:
            data = await call_adapter(self.client, target, "present_item", req, self.timeout)
        except IngestError as e:
            if not_presentable(e):
                return None
            raise IngestError(503, "ITEM_VIEW_UNAVAILABLE", "item view is temporarily unavailable", retryable=True) from None
        if contract.validate("PresentItemResponse", data) or data.get("contract_version") != CONTRACT:
            raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter item view does not match the contract")
        try:
            projected = project_presented_item(data.get("result"))
        except ContractViolation:
            raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter item view is outside the whitelist") from None
        if projected["item_ref"] != item_ref:
            raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter returned another item")
        try:
            return adapter_view(projected)
        except ValueError:
            raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter item view is outside the whitelist") from None


async def present_view(presenter, subject: str, item_id: str) -> ItemView | None:
    """诊断与再练取题面的唯一入口（在事务外调用）：适配器来源（有 fetch）走 present_item；否则是合成题面（同步，原样调用，行为不变）。"""
    fetch = getattr(presenter, "fetch", None)
    if fetch is not None:
        return await fetch(subject, item_id)
    return presenter.present(subject, item_id)


async def present_cached(presenter, subject: str, item_id: str) -> ItemView | None:
    """事务内取题面：适配器来源只查缓存（没命中抛 ViewNotCached，不发网络请求）；合成题面原样调用。"""
    cached = getattr(presenter, "cached", None)
    if cached is not None:
        return await cached(subject, item_id)
    return presenter.present(subject, item_id)
