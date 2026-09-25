"""平台侧核对：规则本身在共享库 starbuddy_contract.checks（与一致性测试共用，冲刺 1-A）。
这里只把 ContractViolation 转成学习服务的错误：整体拒绝、一行不写（502 ADAPTER_RESULT_REJECTED）。
"""
from __future__ import annotations

from starbuddy_contract import checks as _shared
from starbuddy_contract.checks import (  # noqa: F401  （供 service.py、store.py 使用）
    ADMISSION_RANK,
    RESPONSE_KINDS,
    ContractViolation,
    Verified,
    collect_refs,
    local_id,
    parse_time,
)

from .errors import rejected


def verify(method: str, request: dict, response: dict, **kw) -> Verified:
    try:
        return _shared.verify(method, request, response, **kw)
    except ContractViolation as e:
        raise rejected(str(e)) from None
