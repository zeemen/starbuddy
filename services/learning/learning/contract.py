"""按契约 YAML 校验（和阿鸦适配器加载同一份文件，不手抄结构）。

- 学科插件 API：packages/contracts/openapi/plugin-api.v0.9.yaml（请求与适配器响应）
- 学习服务内部 API：packages/contracts/openapi/learning-internal.v0.yaml（返回给 BFF 的摘要与错误）
错误只报位置与关键字，不带实例数据，避免把学生原文写进错误详情或日志。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_CONTRACT = ROOT / "packages/contracts/openapi/plugin-api.v0.9.yaml"
INTERNAL_CONTRACT = ROOT / "packages/contracts/openapi/learning-internal.v0.yaml"
CONTRACT_VERSION = "0.9.0"


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
    return ContractDoc(uri=uri, version=doc["info"]["version"], registry=Registry().with_resource(uri, resource))


@lru_cache(maxsize=1)
def plugin_contract() -> ContractDoc:
    c = _load(PLUGIN_CONTRACT, "urn:starbuddy:plugin-api")
    if c.version != CONTRACT_VERSION:
        raise RuntimeError(f"契约版本 {c.version} 与学习服务实现的 {CONTRACT_VERSION} 不一致")
    return c


@lru_cache(maxsize=1)
def internal_contract() -> ContractDoc:
    return _load(INTERNAL_CONTRACT, "urn:starbuddy:learning-internal")
