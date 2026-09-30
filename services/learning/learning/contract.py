"""契约加载与校验：来自共享库 starbuddy_contract（packages/contracts/python）。"""
from functools import lru_cache

from starbuddy_contract.contract import (CONTRACT_VERSION, OPENAPI, ContractDoc, UnsupportedContractVersion, _load,
                                         internal_contract, plugin_contract)

__all__ = ["CONTRACT_VERSION", "ContractDoc", "UnsupportedContractVersion", "internal_contract", "knowledge_contract", "plugin_contract"]


@lru_cache(maxsize=1)
def knowledge_contract() -> ContractDoc:
    """知识服务契约（packages/contracts/openapi/knowledge-service.v0.9.yaml）：诊断选题校验 node.path / item.search 的响应。"""
    return _load(OPENAPI / "knowledge-service.v0.9.yaml", "urn:starbuddy:knowledge-service:0.9.0")
