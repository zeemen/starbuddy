"""知识服务契约的加载与严格校验（packages/contracts/openapi/knowledge-service.v0.9.yaml）。

工具清单从契约的 paths 生成（路径 /v0/<工具名>、operationId、请求与 200 响应的 schema），不在代码里另抄一份；
HTTP 与 MCP 共用这里的校验。错误只报实例路径与关键字，不带实例数据（node.resolve 的 text 可能来自学生）。
MCP 的 inputSchema 由同一份 schema 打包成自包含的 JSON Schema（$defs），与契约校验结果一致（有测试）。
"""
from __future__ import annotations

import copy
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
OPENAPI = Path(os.environ.get("KNOWLEDGE_CONTRACT_DIR") or ROOT / "packages" / "contracts" / "openapi")
CONTRACT_FILE = "knowledge-service.v0.9.yaml"
PLUGIN_FILE = "plugin-api.v0.9.yaml"
CONTRACT_VERSION = "0.9.0"
PUBLIC_TOOLS = ("node.resolve", "node.get", "node.path", "page.get", "item.search", "item.similar", "rubric.get")
PERSONAL_TOOLS = ("evidence.write", "mastery.get", "mastery.explain")


@dataclass(frozen=True)
class ToolSpec:
    name: str          # node.get
    operation_id: str  # node_get（MCP 工具名）
    path: str          # /v0/node.get
    summary: str
    request_schema: str
    response_schema: str


class Contract:
    def __init__(self, openapi_dir: Path = OPENAPI) -> None:
        self.path = (openapi_dir / CONTRACT_FILE).resolve()
        self.doc = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        plugin_path = (openapi_dir / PLUGIN_FILE).resolve()
        self.plugin_doc = yaml.safe_load(plugin_path.read_text(encoding="utf-8"))
        self.uri = self.path.as_uri()
        self.plugin_uri = plugin_path.as_uri()
        self.version = str(self.doc["info"]["version"])
        if self.version != CONTRACT_VERSION:
            raise RuntimeError(f"知识服务契约版本 {self.version} 与实现的 {CONTRACT_VERSION} 不一致")
        self.registry = Registry().with_resources([
            (self.uri, Resource.from_contents(self.doc, default_specification=DRAFT202012)),
            (self.plugin_uri, Resource.from_contents(self.plugin_doc, default_specification=DRAFT202012)),
        ])
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
            out[name] = ToolSpec(name, op["operationId"], path, str(op.get("summary", "")), req, resp)
        if set(out) != set(PUBLIC_TOOLS) | set(PERSONAL_TOOLS):
            raise RuntimeError("契约的工具清单与实现不一致")
        return out

    def by_operation_id(self, operation_id: str) -> ToolSpec | None:
        return next((t for t in self.tools.values() if t.operation_id == operation_id), None)

    def validator(self, schema: str) -> Draft202012Validator:
        v = self._validators.get(schema)
        if v is None:
            v = Draft202012Validator({"$ref": f"{self.uri}#/components/schemas/{schema}"}, registry=self.registry,
                                     format_checker=FormatChecker())
            self._validators[schema] = v
        return v

    def validate(self, schema: str, value: Any) -> list[str]:
        """返回错误列表（空表示通过）。每条只含实例路径与关键字"""
        errors = set()
        for err in self.validator(schema).iter_errors(value):
            errors.add("/" + "/".join(str(p) for p in err.absolute_path) + " " + str(err.validator))
        return sorted(errors)

    def problem_errors(self, value: Any) -> list[str]:
        v = Draft202012Validator({"$ref": f"{self.plugin_uri}#/components/schemas/Problem"}, registry=self.registry,
                                 format_checker=FormatChecker())
        return sorted({"/" + "/".join(str(p) for p in e.absolute_path) + " " + str(e.validator) for e in v.iter_errors(value)})

    # ---------- MCP：自包含的 JSON Schema ----------
    def bundle(self, schema: str) -> dict[str, Any]:
        """把 components/schemas 下的一个 schema 及其传递引用打包成 {"$defs": ...}，引用改写为 #/$defs/<名>"""
        defs: dict[str, Any] = {}

        def key_for(ref: str, base: str) -> tuple[str, str, dict[str, Any]]:
            target, _, frag = ref.partition("#")
            doc_key = base if not target else ("plugin" if target.endswith(PLUGIN_FILE) else "knowledge")
            name = frag.rsplit("/", 1)[-1]
            doc = self.plugin_doc if doc_key == "plugin" else self.doc
            return doc_key, (f"plugin-api.{name}" if doc_key == "plugin" else name), doc["components"]["schemas"][name]

        def rewrite(node: Any, base: str) -> Any:
            if isinstance(node, dict):
                out = {}
                for k, v in node.items():
                    if k == "$ref" and isinstance(v, str):
                        doc_key, name, target = key_for(v, base)
                        if name not in defs:
                            defs[name] = {}
                            defs[name] = rewrite(copy.deepcopy(target), doc_key)
                        out[k] = f"#/$defs/{name}"
                    elif k in ("examples", "example"):
                        continue
                    else:
                        out[k] = rewrite(v, base)
                return out
            if isinstance(node, list):
                return [rewrite(v, base) for v in node]
            return node

        root = rewrite({"$ref": f"#/components/schemas/{schema}"}, "knowledge")
        return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "$ref": root["$ref"], "$defs": defs}


@lru_cache(maxsize=1)
def contract() -> Contract:
    return Contract()
