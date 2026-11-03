"""判断 → 陈述文字：模板渲染规则的唯一实现（KGE-04 第二步 PR 4；第一步 spec 第 14 节“与洞察服务 RenderedStatement 同一套规则，不另写一套”）。

学情档案页（本服务）与洞察服务 RenderedStatement（services/insight/insight/render.py 经 shared.py 只读引用本模块）共用这一份。
纯标准库、不连库；改规则即同时改两边的对外文字，属模板与禁用推断相关的必停项。规则（模板渲染为主，不调模型）：
- 结论文字 = 已审核模板的 text_template，只把 {变量} 换成判断的 target_id（也可写 node_id）与 value 里的编码；
- 变量名只收 [a-z_]{1,32}，值只收编码（整数、两位小数的有限浮点数、形如 [A-Za-z0-9][A-Za-z0-9_.:-]{0,63} 的字符串；布尔不收）；
- 缺变量、值不是编码、渲染后还有花括号、去空白后为空或超过 300 字（RenderedStatement.text 的 maxLength）都不渲染（返回 None）；
- 渲染结果过禁用推断词表，命中即不渲染。

依赖方向只能是洞察服务读学习服务的纯函数模块（services/insight/insight/shared.py），学习服务不依赖洞察服务的代码。
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Mapping, Sequence

VAR = re.compile(r"\{([a-z_]{1,32})\}")
CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}$")
MAX_TEXT = 300

Forbidden = Callable[[str], Sequence[str]]


def _code(v: Any) -> str | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.2f}" if math.isfinite(v) else None
    if isinstance(v, str) and CODE.fullmatch(v):
        return v
    return None


def render_text(text_template: str | None, target_id: str, value: Mapping[str, Any], forbidden: Forbidden) -> str | None:
    if not text_template:
        return None
    variables = {"target_id": target_id, "node_id": target_id, **{k: v for k, v in value.items() if isinstance(k, str)}}
    missing = False

    def sub(m: re.Match[str]) -> str:
        nonlocal missing
        code = _code(variables.get(m.group(1)))
        if code is None:
            missing = True
            return ""
        return code

    text = VAR.sub(sub, text_template)
    if missing or "{" in text or "}" in text:
        return None
    text = text.strip()
    if not text or len(text) > MAX_TEXT or forbidden(text):
        return None
    return text
