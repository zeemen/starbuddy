"""契约加载与校验：来自共享库 starbuddy_contract（packages/contracts/python）。"""
import os
from functools import lru_cache

from starbuddy_contract.contract import (CONTRACT_VERSION, OPENAPI, ContractDoc, UnsupportedContractVersion, _load,
                                         internal_contract, plugin_contract)

__all__ = ["CONTRACT_VERSION", "ContractDoc", "UnsupportedContractVersion", "internal_contract", "internal_v1_enabled",
           "knowledge_contract", "plugin_contract"]

INTERNAL_V1_ENV = "LEARNING_INTERNAL_V1"


def internal_v1_enabled() -> bool:
    """learning-internal v1 未签字（插件 API v1.0 签字不连带它，specs/contract-v1.0-signoff 审查阻断）：
    只在显式设 LEARNING_INTERNAL_V1=1 时开放签字前由插件 1.0.0 候选开关挡住的那些行为——
    /internal/v1/grade、/internal/v1/diagnose 写入，/internal/v1/plan 调适配器，诊断、再练（及依赖再练的复测）模块启动。
    缺省关，只在测试与演练里开。每次调用时读环境变量（与候选版开关同一做法），不缓存。"""
    return os.environ.get(INTERNAL_V1_ENV) == "1"


@lru_cache(maxsize=1)
def knowledge_contract() -> ContractDoc:
    """知识服务契约（packages/contracts/openapi/knowledge-service.v0.9.yaml）：诊断选题校验 node.path / item.search 的响应。"""
    return _load(OPENAPI / "knowledge-service.v0.9.yaml", "urn:starbuddy:knowledge-service:0.9.0")
