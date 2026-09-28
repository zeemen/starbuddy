"""按契约 YAML 校验（和阿鸦适配器加载同一份文件，不手抄结构）。

- 学科插件 API：packages/contracts/openapi/plugin-api.v0.9.yaml（默认）或 v1.0 候选版（请求与适配器响应）
- 学习服务内部 API：packages/contracts/openapi/learning-internal.v0.yaml（返回给 BFF 的摘要与错误）
迁移期两个插件契约版本并存（specs/plugin-api-v1.0/spec.md）：环境变量 STARBUDDY_CONTRACT_VERSION 选版本，
缺省 0.9.0。学习服务与阿鸦在第二步切换前一直用 0.9.0；一致性测试用它按 1.0.0 跑参考适配器。
错误只报位置与关键字，不带实例数据，避免把学生原文写进错误详情或日志。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

OPENAPI = Path(__file__).resolve().parents[2] / "openapi"
PLUGIN_CONTRACTS = {"0.9.0": OPENAPI / "plugin-api.v0.9.yaml", "1.0.0": OPENAPI / "plugin-api.v1.0.yaml"}
CONTRACT_VERSION = os.environ.get("STARBUDDY_CONTRACT_VERSION", "0.9.0")
if CONTRACT_VERSION not in PLUGIN_CONTRACTS:
    raise RuntimeError(f"不支持的契约版本 {CONTRACT_VERSION}（可选：{sorted(PLUGIN_CONTRACTS)}）")
# 候选版签字前只能在测试里用（安全审查建议 3）：还要显式设 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1，
# 防止服务进程因为全局环境变量误用候选版（学习服务的内部契约还停在 v0）
CANDIDATE_VERSIONS = {"1.0.0"}


def candidate_allowed() -> bool:
    return os.environ.get("STARBUDDY_ALLOW_CANDIDATE_CONTRACT") == "1"


class UnsupportedContractVersion(ValueError):
    """请求声明的契约版本不支持，或是签字前未开放的候选版。"""


if CONTRACT_VERSION in CANDIDATE_VERSIONS and not candidate_allowed():
    raise RuntimeError(f"契约 {CONTRACT_VERSION} 是候选版，签字前只能在测试里使用（需 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1）")
PLUGIN_CONTRACT = PLUGIN_CONTRACTS[CONTRACT_VERSION]
INTERNAL_CONTRACT = OPENAPI / "learning-internal.v0.yaml"


@dataclass(frozen=True)
class ContractDoc:
    uri: str
    version: str
    registry: Registry

    def validate(self, schema: str, value: object) -> list[str]:
        """返回错误列表（空表示通过）。每条只含实例路径与关键字。"""
        validator = Draft202012Validator(
            {"$ref": f"{self.uri}#/components/schemas/{schema}"},
            registry=self.registry,
            format_checker=FormatChecker(),
        )
        errors = []
        for err in validator.iter_errors(value):
            path = "/" + "/".join(str(p) for p in err.absolute_path)
            errors.append(f"{path} {err.validator}")
        return sorted(set(errors))


def _load(path: Path, uri: str) -> ContractDoc:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    resource = Resource.from_contents({"components": doc["components"]}, default_specification=DRAFT202012)
    registry = Registry().with_resource(uri, resource)
    # v1.0 用相对路径引用两份服务契约（衔接字段）：按文件 URI 登记同目录的契约，相对引用才能解析
    uri = path.resolve().as_uri() if uri.startswith("urn:") and _has_file_refs(doc) else uri
    if uri.startswith("file:"):
        for other in sorted(OPENAPI.glob("*.yaml")):
            contents = doc if other.resolve() == path.resolve() else yaml.safe_load(other.read_text(encoding="utf-8"))
            registry = registry.with_resource(other.resolve().as_uri(),
                                              Resource.from_contents(contents, default_specification=DRAFT202012))
    return ContractDoc(uri=uri, version=doc["info"]["version"], registry=registry)


def _has_file_refs(node) -> bool:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            return True
        return any(_has_file_refs(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_file_refs(v) for v in node)
    return False


def plugin_contract(version: str | None = None) -> ContractDoc:
    """按版本取插件契约；不给版本时取缺省版本（环境变量 STARBUDDY_CONTRACT_VERSION，缺省 0.9.0）。
    迁移期学习服务按请求的 context.contract_version 选版本（specs/plugin-api-v1.0-step2/spec.md）。"""
    v = version or CONTRACT_VERSION
    if v not in PLUGIN_CONTRACTS:
        raise UnsupportedContractVersion(f"contract version {v} is not supported")
    if v in CANDIDATE_VERSIONS and not candidate_allowed():
        raise UnsupportedContractVersion(f"contract version {v} is a candidate and not enabled here")
    return _plugin_contract_cached(v)


@lru_cache(maxsize=4)
def _plugin_contract_cached(version: str) -> ContractDoc:
    c = _load(PLUGIN_CONTRACTS[version], f"urn:starbuddy:plugin-api:{version}")
    if c.version != version:
        raise RuntimeError(f"契约文件版本 {c.version} 与登记的 {version} 不一致")
    return c


INTERNAL_CONTRACTS = {"0.9.0": INTERNAL_CONTRACT, "1.0.0": OPENAPI / "learning-internal.v1.yaml"}


@lru_cache(maxsize=None)
def internal_contract(version: str = "0.9.0") -> ContractDoc:
    """学习服务内部 API：0.9.0 对应 /internal/v0，1.0.0 对应 /internal/v1（信封请求，specs/evidence-attributes-sprint1）。"""
    return _load(INTERNAL_CONTRACTS[version], f"urn:starbuddy:learning-internal:{version}")
