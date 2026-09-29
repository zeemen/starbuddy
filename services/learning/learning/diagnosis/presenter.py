"""题面提供者（spec 设计要点 4，待人确认 #2）。

契约里还没有 present_item（插件 API 新增只读方法要另起 [NEEDS-HUMAN] 契约 PR，签字后由适配器从内容库返回）。
本任务只提供测试环境的合成题面实现：题面是占位文字，题号借用阿鸦已登记的题目（只作验证器判分的槽位）。

返回给 BFF 的题面只有：题型、题面文字、选项（键与文字）。不含答案、解析、节点、选题理由（规则 3、11）。
合成夹具本身不带答案；合成学生与验证器的答案来自阿鸦内容包（验证器自己的数据），不经过本模块。
"""
from __future__ import annotations

from typing import Protocol

from .source import load_fixture, require_test_env

FORMATS = ("single", "multi", "fill")


class ItemView(dict):
    """{format, stem, options?}：只有这几个键（present 出口处再核一次）。"""


class ItemPresenter(Protocol):
    def present(self, subject: str, item_id: str) -> ItemView | None: ...


ALLOWED_KEYS = frozenset({"format", "stem", "options"})
ALLOWED_OPTION_KEYS = frozenset({"key", "text"})


def _clean(view: dict) -> ItemView:
    """出口白名单：多一个键就拒绝（防止以后给夹具或适配器加了答案、解析字段后被原样带出去）。"""
    if set(view) - ALLOWED_KEYS or view.get("format") not in FORMATS or not isinstance(view.get("stem"), str):
        raise ValueError("item view has fields outside the whitelist")
    out = ItemView(format=view["format"], stem=view["stem"])
    if view["format"] in ("single", "multi"):
        opts = view.get("options") or []
        if not opts or any(set(o) - ALLOWED_OPTION_KEYS for o in opts):
            raise ValueError("options have fields outside the whitelist")
        out["options"] = [{"key": o["key"], "text": o["text"]} for o in opts]
    return out


class SyntheticPresenter:
    """测试环境的合成题面。非 test 环境构造即失败（配置层 Settings.from_env 另有一道）。"""

    def __init__(self, env: str, doc: dict | None = None) -> None:
        require_test_env(env, "合成题面提供者")
        doc = doc or load_fixture()
        self.subject = doc["subject"]
        self._items = {it["item_id"]: it for it in doc["items"]}

    def present(self, subject: str, item_id: str) -> ItemView | None:
        it = self._items.get(item_id)
        if subject != self.subject or it is None:
            return None
        view = {"format": it["format"], "stem": it["stem"]}
        if "options" in it:
            view["options"] = it["options"]
        return _clean(view)
