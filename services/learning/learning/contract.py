"""契约加载与校验：来自共享库 starbuddy_contract（packages/contracts/python）。

learning-internal v1 已于 2026-10-20 签字（1.1.0，specs/launch-lt04-learning-internal-v1）：签字前挡住 /internal/v1 写入、plan、
诊断与再练模块的开关 LEARNING_INTERNAL_V1 随之删除；写入改由服务身份限定 + 计量核对 + 服务端推导用途与等级把关（learning/meter.py）。
"""
from functools import lru_cache

from starbuddy_contract.contract import (CONTRACT_VERSION, OPENAPI, ContractDoc, UnsupportedContractVersion, _load,
                                         internal_contract, plugin_contract)

__all__ = ["CONTRACT_VERSION", "ContractDoc", "UnsupportedContractVersion", "internal_contract", "knowledge_contract",
           "plugin_contract"]


@lru_cache(maxsize=1)
def knowledge_contract() -> ContractDoc:
    """知识服务契约（packages/contracts/openapi/knowledge-service.v0.9.yaml）：诊断选题校验 node.path / item.search 的响应。"""
    return _load(OPENAPI / "knowledge-service.v0.9.yaml", "urn:starbuddy:knowledge-service:0.9.0")
