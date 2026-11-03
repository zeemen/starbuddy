"""洞察服务契约的加载与严格校验（packages/contracts/openapi/insight-service.v0.9.yaml）。

工具清单从契约的 paths 生成（路径 /v0/<工具名>、operationId、请求与 200 响应的 schema），不在代码里另抄一份。
契约引用插件 API 与知识服务契约（Problem、Subject、ModelVersion、NodeId、EvidenceLevel 等），三份一起进 referencing 注册表。
错误只报实例路径与关键字，不带实例数据（请求里有学生与账号 ID）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[4]
OPENAPI = Path(os.environ.get("INSIGHT_CONTRACT_DIR") or ROOT / "packages" / "contracts" / "openapi")
CONTRACT_FILE = "insight-service.v0.9.yaml"
REFERENCED = ("plugin-api.v0.9.yaml", "knowledge-service.v0.9.yaml")
CONTRACT_VERSION = "0.9.0"
IMPLEMENTED = ("insight.claims", "insight.context", "insight.explain", "insight.record", "insight.dispute", "insight.predict",
               "insight.reconcile", "insight.export", "insight.erase", "insight.report")
# SIE-03 PR② 起九个工具都有实现（insight.record 仍在校验后回 501；export / erase 缺省不接线回 501）；
# SIE-02b 第二步追加第十个 insight.report（首份画像报告）
NOT_IMPLEMENTED: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    operation_id: str
    path: str
    request_schema: str
    response_schema: str


class Contract:
    def __init__(self, openapi_dir: Path = OPENAPI) -> None:
        self.path = (openapi_dir / CONTRACT_FILE).resolve()
        self.doc = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.uri = self.path.as_uri()
        resources = [(self.uri, Resource.from_contents(self.doc, default_specification=DRAFT202012))]
        self.docs = {CONTRACT_FILE: self.doc}
        for name in REFERENCED:
            p = (openapi_dir / name).resolve()
            d = yaml.safe_load(p.read_text(encoding="utf-8"))
            self.docs[name] = d
            resources.append((p.as_uri(), Resource.from_contents(d, default_specification=DRAFT202012)))
        self.plugin_uri = (openapi_dir / REFERENCED[0]).resolve().as_uri()
        self.version = str(self.doc["info"]["version"])
        if self.version != CONTRACT_VERSION:
            raise RuntimeError(f"洞察服务契约版本 {self.version} 与实现的 {CONTRACT_VERSION} 不一致")
        self.registry = Registry().with_resources(resources)
        self._validators: dict[str, Draft202012Validator] = {}
        self.tools = self._tools()

    def _tools(self) -> dict[str, ToolSpec]:
        out: dict[str, ToolSpec] = {}
        for path, item in sorted(self.doc["paths"].items()):
            op = item["post"]
            name = path.removeprefix("/v0/")
            req = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
            resp = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
            if op["operationId"] != name.replace(".", "_"):
                raise RuntimeError(f"契约里 {path} 的 operationId 与工具名不对应")
            out[name] = ToolSpec(name, op["operationId"], path, req, resp)
        if set(out) != set(IMPLEMENTED) | set(NOT_IMPLEMENTED):
            raise RuntimeError("契约的工具清单与实现不一致")
        return out

    def validator(self, schema: str, uri: str | None = None) -> Draft202012Validator:
        key = f"{uri or self.uri}#{schema}"
        v = self._validators.get(key)
        if v is None:
            v = Draft202012Validator({"$ref": f"{uri or self.uri}#/components/schemas/{schema}"}, registry=self.registry,
                                     format_checker=FormatChecker())
            self._validators[key] = v
        return v

    def validate(self, schema: str, value: Any) -> list[str]:
        """返回错误列表（空表示通过）。每条只含实例路径与关键字"""
        return sorted({"/" + "/".join(str(p) for p in e.absolute_path) + " " + str(e.validator)
                       for e in self.validator(schema).iter_errors(value)})

    def problem_errors(self, value: Any) -> list[str]:
        return sorted({"/" + "/".join(str(p) for p in e.absolute_path) + " " + str(e.validator)
                       for e in self.validator("Problem", self.plugin_uri).iter_errors(value)})


@lru_cache(maxsize=1)
def contract() -> Contract:
    return Contract()
